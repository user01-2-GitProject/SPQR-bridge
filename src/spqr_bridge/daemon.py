"""Single job owner with bounded authenticated local IPC; no network listener.

Production starts require a verified systemd user service with control-group cleanup.
The public SharedOwner context is also used by fake-agent tests in isolated state.
Remote HTTP and hostile-worker confinement remain separate rollout gates.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import signal
import socket
import stat
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .config import BridgeConfig, load_config
from .envpolicy import current_depth
from .gate import PrincipalGate
from .registry import RegistryError, authenticate, load_registry
from .store import Store
from .supervisor import BridgeError, Supervisor
from .v2 import _log_auth_failure, prepare_config

MAX_FRAME = 262144
MAX_RESPONSE = 4 * 1024 * 1024
MAX_CONNECTIONS = 32


class _Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Start(_Arguments):
    agent_id: str
    task: str
    workspace: str
    context: str | None = None
    access: Literal["read_only", "write"] = "read_only"
    timeout_seconds: int | None = Field(default=None, ge=1)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    grant_id: str | None = Field(default=None, max_length=32)


class Get(_Arguments):
    task_id: str
    wait_seconds: float = Field(default=0, ge=0, le=10)


class Cancel(_Arguments):
    task_id: str


def validate_service(unit: str) -> None:
    """Verify actual service-manager and kernel identity, never an argv/env claim."""
    if not re.fullmatch(r"spqr-bridge[-a-z0-9]*\.service", unit):
        raise RuntimeError("a spqr-bridge systemd user service is required")
    result = subprocess.run(
        ["systemctl", "--user", "show", unit, "--property=MainPID,ControlGroup,KillMode,SendSIGKILL"],
        capture_output=True, text=True, timeout=5, check=True)
    props = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    groups = Path("/proc/self/cgroup").read_text().splitlines()
    group = next((line[3:] for line in groups if line.startswith("0::")), None)
    if (props.get("MainPID") != str(os.getpid()) or not group or
            props.get("ControlGroup") != group or props.get("KillMode") != "control-group" or
            props.get("SendSIGKILL") != "yes"):
        raise RuntimeError("service identity or control-group cleanup policy is not verified")
    # A valid service identity alone does not prove a previous generation is empty.
    # At admission only this process may exist anywhere in the unit's cgroup subtree.
    root = Path("/sys/fs/cgroup") / group.lstrip("/")
    pids = set()
    for path in [root / "cgroup.procs", *root.glob("**/cgroup.procs")]:
        pids.update(int(pid) for pid in path.read_text().split())
    if pids != {os.getpid()}:
        raise RuntimeError("service contains other processes; previous generation cleanup is not established")


def _error(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


class SharedOwner:
    def __init__(self, cfg: BridgeConfig, registry: Path, socket_path: Path):
        self.cfg = prepare_config(cfg)
        self.registry = Path(registry).resolve()
        self.socket = Path(socket_path)
        self.sup: Supervisor | None = None
        self.server = None
        self.connections: set[asyncio.Task] = set()
        self.socket_identity = None
        self.rate_tokens = 64.0
        self.rate_at: float | None = None

    def take_request(self, now: float | None = None) -> bool:
        """Global bounded bucket before registry reads: burst 64, sustained 32/sec."""
        now = time.monotonic() if now is None else now
        if self.rate_at is not None:
            self.rate_tokens = min(64.0, self.rate_tokens + max(0, now - self.rate_at) * 32)
        self.rate_at = now
        if self.rate_tokens < 1:
            return False
        self.rate_tokens -= 1
        return True

    async def __aenter__(self):
        load_registry(self.registry)  # fail before recovery or socket creation
        parent = self.socket.parent
        st = parent.lstat()
        if (not self.socket.is_absolute() or not stat.S_ISDIR(st.st_mode) or
                st.st_uid != os.getuid() or st.st_mode & 0o077):
            raise RuntimeError("socket directory must be absolute, owned by this user and mode 0700")
        protected = [self.registry, parent.resolve(), self.cfg.server.state_dir.resolve()]
        if any(p.is_relative_to(r) for r in self.cfg.server.allowed_roots for p in protected):
            raise RuntimeError("registry, socket and daemon state must be outside all worker roots")
        # Take the state lock first: a live owner holds it for its whole lifetime, so
        # holding it proves a leftover socket is stale (a crash). Only a real socket is
        # removed; any other file or symlink at the path is refused.
        self.sup = Supervisor(self.cfg, Store(self.cfg.server.state_dir))
        try:
            st = self.socket.lstat()
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISSOCK(st.st_mode) or st.st_uid != os.getuid():
                await self.__aexit__(None, None, None)
                raise RuntimeError("socket path is occupied by something else; inspect it")
            self.socket.unlink()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            # Bind directly: asyncio's path= convenience removes pre-existing sockets.
            # bind() instead fails atomically if a competing owner won the path.
            listener.bind(str(self.socket))
            listener.setblocking(False)
            st = self.socket.lstat()
            self.socket_identity = (st.st_dev, st.st_ino)
            self.server = await asyncio.start_unix_server(self._handle, sock=listener,
                                                          limit=MAX_FRAME, start_serving=False)
            self.socket.chmod(0o600)
            await self.server.start_serving()
        except BaseException:
            listener.close()
            await self.__aexit__(None, None, None)
            raise
        return self

    async def __aexit__(self, *_):
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        for task in list(self.connections):
            task.cancel()
        if self.connections:
            await asyncio.gather(*list(self.connections), return_exceptions=True)
        if self.sup is not None:
            await self.sup.shutdown()
        if self.socket_identity is not None:
            try:
                st = self.socket.lstat()
                if (st.st_dev, st.st_ino) == self.socket_identity:
                    self.socket.unlink()
            except FileNotFoundError:
                pass

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        task = asyncio.current_task()
        accepted = len(self.connections) < MAX_CONNECTIONS
        if accepted:
            self.connections.add(task)
        try:
            if not accepted:
                response = _error("busy", "too many bridge connections")
            elif not self.take_request():
                response = _error("rate_limited", "bridge request rate exceeded; wait before retrying")
            else:
                # The private socket is local, and the peer must have the owner's kernel UID.
                peer = writer.get_extra_info("socket").getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                _, uid, _ = struct.unpack("3i", peer)
                if uid != os.getuid():
                    response = _error("unauthenticated", "authentication required")
                else:
                    async with asyncio.timeout(15):
                        line = await reader.readline()
                        if not line.endswith(b"\n") or len(line) > MAX_FRAME:
                            raise ValueError("frame")
                        response = await self.dispatch(json.loads(line))
            wire = json.dumps(response, ensure_ascii=True).encode() + b"\n"
            if len(wire) > MAX_RESPONSE:
                wire = json.dumps(_error("response_too_large", "result exceeds the IPC frame limit")).encode() + b"\n"
            writer.write(wire)
            async with asyncio.timeout(5):
                await writer.drain()
        except TimeoutError:
            writer.transport.abort()
        except (ValueError, UnicodeError):
            writer.write(json.dumps(_error("invalid_request", "invalid or oversized request")).encode() + b"\n")
            try:
                async with asyncio.timeout(5):
                    await writer.drain()
            except (ConnectionError, OSError, TimeoutError):
                writer.transport.abort()
        except (ConnectionError, OSError):
            pass  # disconnect does not shut down the Supervisor or admitted jobs
        except Exception:
            # Never leak raw validation/protocol exceptions containing credentials or task text.
            writer.write(json.dumps(_error("internal_error", "bridge request failed")).encode() + b"\n")
        finally:
            writer.close()
            try:
                async with asyncio.timeout(1):
                    await writer.wait_closed()
            except (ConnectionError, OSError, TimeoutError):
                writer.transport.abort()
            if accepted:
                self.connections.discard(task)

    async def dispatch(self, request: object) -> dict:
        if (not isinstance(request, dict) or set(request) - {"token", "tool", "arguments", "principal_id"}
                or not isinstance(request.get("token"), str) or len(request["token"]) > 4096):
            return _error("invalid_request", "invalid request")
        try:
            auth = authenticate(load_registry(self.registry), request["token"].encode(), "stdio")
        except RegistryError:
            auth = None
        tool = request.get("tool")
        if auth is None or (tool != "hello" and request.get("principal_id") != auth.principal_id):
            _log_auth_failure(self.cfg.server.state_dir, "bad_credential")
            return _error("unauthenticated", "authentication required")
        gate = PrincipalGate(self.sup, self.registry, auth)
        args = request.get("arguments")
        if not isinstance(args, dict):
            return _error("invalid_arguments", "arguments must be an object")
        try:
            if tool == "hello":
                _Arguments.model_validate(args)
                gate._audit("hello", "ok")
                result = {"principal_id": auth.principal_id}
            elif tool == "check":
                _Arguments.model_validate(args)
                result = {"principal_id": auth.principal_id}
            elif tool == "list_agents":
                _Arguments.model_validate(args)
                result = gate.list_agents()
            elif tool == "start_task":
                result = await gate.start(**Start.model_validate(args).model_dump())
            elif tool == "get_task":
                arg = Get.model_validate(args)
                result = await gate.wait(arg.task_id, arg.wait_seconds)
            elif tool == "cancel_task":
                result = await gate.cancel(Cancel.model_validate(args).task_id)
            else:
                return _error("unknown_tool", "unknown bridge tool")
            return {"ok": True, "result": result}
        except ValidationError:
            return _error("invalid_arguments", "invalid tool arguments")
        except BridgeError as error:
            return _error(error.code, str(error))


async def serve(cfg, registry, socket_path):
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    async with SharedOwner(cfg, registry, socket_path):
        await stop.wait()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--config", required=True)
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--socket", required=True, type=Path)
    parser.add_argument("--service-unit", required=True)
    args = parser.parse_args(argv)
    try:
        if current_depth():
            raise RuntimeError("bridge-launched workers cannot start a daemon")
        validate_service(args.service_unit)
        asyncio.run(serve(load_config(args.config), args.registry, args.socket))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        print("spqr-bridge-daemon: startup failed; check service identity, config and private paths", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
