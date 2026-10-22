"""Independent review regressions: revocation, valid large replies, stale containment."""

import os
import asyncio
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from spqr_bridge.config import AgentConfig, BridgeConfig, ServerConfig
from spqr_bridge.daemon import SharedOwner, validate_service
from spqr_bridge.forward import Forwarder
from v2_helpers import TOKENS, registry_text, write_registry


def settings(tmp_path, **server):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    control = tmp_path / "control"
    control.mkdir(mode=0o700)
    registry = write_registry(tmp_path / "registry.toml", registry_text(workspace))
    cfg = BridgeConfig(server=ServerConfig(state_dir=tmp_path / "state", allowed_roots=[workspace], **server),
                       agents={"claude": AgentConfig(adapter="command", binary=Path(sys.executable),
                                                     argv=["-c", "print(chr(34) * 200000)"], enabled=True)})
    return cfg, workspace, registry, control / "bridge.sock"


async def test_revoked_forwarder_cannot_discover_tools(tmp_path):
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters

    cfg, workspace, registry, socket = settings(tmp_path)
    async with SharedOwner(cfg, registry, socket):
        token = tmp_path / "caesar.token"
        token.write_text(TOKENS["caesar"])
        token.chmod(0o600)
        params = StdioServerParameters(command=sys.executable,
                                      args=["-m", "spqr_bridge.forward", "--socket", str(socket),
                                            "--token-file", str(token)])
        async with Client(params, read_timeout_seconds=20) as client:
            assert len((await client.list_tools()).tools) == 4
            registry.write_text(registry_text(workspace, principals={"caesar": {"enabled": False}}))
            with pytest.raises(Exception, match="authentication|unauthenticated"):
                await client.list_tools()


async def test_valid_maximum_snippet_is_readable_through_forwarder(tmp_path):
    cfg, workspace, registry, socket = settings(tmp_path, snippet_bytes=200000)
    async with SharedOwner(cfg, registry, socket):
        forwarder = Forwarder(socket, TOKENS["caesar"].encode())
        await forwarder.authenticate()
        job = await forwarder.call("start_task", {"agent_id": "claude", "task": "fixture only",
                                                  "workspace": str(workspace)})
        result = await forwarder.call("get_task", {"task_id": job["task_id"], "wait_seconds": 10})
        assert result["state"] == "succeeded"
        assert result["result_text"] == '"' * 200000


def test_service_with_leftover_workers_cannot_open_state(monkeypatch):
    group = "/user.slice/spqr-review.service"
    original_read = Path.read_text
    def read(path, *args, **kwargs):
        if str(path) == "/proc/self/cgroup":
            return "0::" + group + "\n"
        if str(path).startswith("/sys/fs/cgroup") and path.name == "cgroup.procs":
            return f"{os.getpid()}\n999999\n"
        return original_read(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(
        a, 0, stdout=f"MainPID={os.getpid()}\nControlGroup={group}\nKillMode=control-group\nSendSIGKILL=yes\n"))
    with pytest.raises(RuntimeError):
        validate_service("spqr-bridge-review.service")


async def test_write_timeout_never_reenters_unbounded_drain(tmp_path):
    cfg, _, registry, socket = settings(tmp_path)
    owner = SharedOwner(cfg, registry, socket)
    async def response(_):
        return {"ok": True, "result": {"text": "x" * 1000000}}
    owner.dispatch = response
    reader = asyncio.StreamReader()
    reader.feed_data(b'{}\n')
    class Peer:
        def getsockopt(self, *args):
            return struct.pack("3i", os.getpid(), os.getuid(), os.getgid())
    class Transport:
        def abort(self):
            pass
    class BlockedWriter:
        transport = Transport()
        drains = 0
        def get_extra_info(self, _):
            return Peer()
        def write(self, _):
            pass
        async def drain(self):
            self.drains += 1
            if self.drains > 1:
                raise AssertionError("timeout handler reentered unbounded drain")
            raise TimeoutError("simulated full write buffer")
        def close(self):
            pass
        async def wait_closed(self):
            pass
    writer = BlockedWriter()
    await owner._handle(reader, writer)
    assert writer.drains == 1


async def test_nonreading_peer_releases_connection_slot_after_write_deadline(tmp_path):
    cfg, _, registry, socket = settings(tmp_path)
    async with SharedOwner(cfg, registry, socket) as owner:
        async def response(_):
            return {"ok": True, "result": {"text": "x" * 3000000}}
        owner.dispatch = response
        # A tiny receive buffer pauses the reader transport immediately. Never consume
        # the response: the owner's large reply must fill the Unix socket send buffer.
        reader, writer = await asyncio.open_unix_connection(str(socket), limit=1024)
        try:
            writer.write(b'{}\n')
            await writer.drain()
            async with asyncio.timeout(2):
                while not owner.connections:
                    await asyncio.sleep(0.01)
            assert len(owner.connections) == 1
            async with asyncio.timeout(7):
                while owner.connections:
                    await asyncio.sleep(0.05)
            assert len(reader._buffer) > 0  # a real response reached the stalled peer
        finally:
            writer.transport.abort()
