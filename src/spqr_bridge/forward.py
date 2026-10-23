"""Thin stdio MCP client of the shared local owner; never owns jobs or a Store.

The token is read from an owner-only file, used only on the private Unix socket,
and bound to the principal returned at bootstrap. No network listener is opened.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import socket
import stat
import struct
import sys
from typing import Annotated, Any, Literal

from mcp import MCPError
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import __version__
from .envpolicy import MAX_DEPTH, current_depth
from .launch import read_token_file

MAX_LINE_BYTES = 262144
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
REQUEST_TIMEOUT = 15
_ID = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")
_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
TOOLS = frozenset({"list_agents", "start_task", "get_task", "cancel_task"})
INSTRUCTIONS = (
    "Delegate bounded work through the shared SPQR owner. Without grant_id work is read-only. "
    "Poll get_task with wait_seconds up to 10. Usage exhaustion and loop caps are final stops: "
    "report to Jimmy. Remember to read and write in the Library, within the task's permissions. "
    "Verify returned work independently."
)


class ForwardError(ValueError):
    """Bounded diagnostic safe to return to an MCP caller."""


class Forwarder:
    def __init__(self, path: Path, token: bytes):
        self.path = Path(path)
        try:
            self._token = token.decode("ascii")
        except UnicodeDecodeError:
            raise ForwardError("unauthenticated: authentication required") from None
        if not self._token or len(token) > 4096:
            raise ForwardError("unauthenticated: authentication required")
        self.principal_id: str | None = None

    def _socket_check(self) -> None:
        try:
            st = self.path.lstat()
            if not stat.S_ISSOCK(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
                raise OSError
        except OSError:
            raise ForwardError("unavailable: private bridge socket unavailable") from None

    async def _request(self, tool: str, arguments: dict) -> dict:
        request = {"token": self._token, "tool": tool, "arguments": arguments}
        if tool != "hello":
            request["principal_id"] = self.principal_id
        wire = json.dumps(request, ensure_ascii=True, allow_nan=False).encode() + b"\n"
        if len(wire) > MAX_LINE_BYTES:
            raise ForwardError("request_too_large: bridge request exceeds limit")
        self._socket_check()
        writer = None
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                reader, writer = await asyncio.open_unix_connection(str(self.path), limit=MAX_RESPONSE_BYTES)
                # Check the connected peer as well as the pathname before sending a credential.
                peer = writer.get_extra_info("socket")
                _, uid, _ = struct.unpack("3i", peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if uid != os.getuid():
                    raise ForwardError("unavailable: unexpected bridge owner")
                writer.write(wire)
                await writer.drain()
                line = await reader.readline()
                if len(line) > MAX_RESPONSE_BYTES or not line.endswith(b"\n"):
                    raise ForwardError("protocol_error: invalid bridge response")
                response = json.loads(line)
        except (OSError, TimeoutError, ValueError, UnicodeDecodeError) as exc:
            if isinstance(exc, ForwardError):
                raise
            raise ForwardError("unavailable: bridge request failed") from None
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass  # The owner may have disappeared; the original diagnostic wins.
        if not isinstance(response, dict):
            raise ForwardError("protocol_error: invalid bridge response")
        if response.get("ok") is True and isinstance(response.get("result"), dict):
            return response["result"]
        error = response.get("error")
        if response.get("ok") is False and isinstance(error, dict):
            code, message = error.get("code"), error.get("message")
            if isinstance(code, str) and _CODE.fullmatch(code) and isinstance(message, str):
                # Never repeat a credential even if a broken owner accidentally echoes one.
                raise ForwardError(f"{code}: {message.replace(self._token, '[REDACTED]')[:500]}")
        raise ForwardError("protocol_error: invalid bridge response")

    async def authenticate(self) -> None:
        if self.principal_id is not None:
            await self.check()
            return
        result = await self._request("hello", {})
        pid = result.get("principal_id")
        if not isinstance(pid, str) or not _ID.fullmatch(pid):
            raise ForwardError("protocol_error: invalid bridge identity")
        self.principal_id = pid

    async def check(self) -> None:
        """Revalidate the original identity; never re-bootstrap into a different principal."""
        if self.principal_id is None:
            raise ForwardError("unauthenticated: authentication required")
        result = await self._request("check", {})
        if result.get("principal_id") != self.principal_id:
            raise ForwardError("unauthenticated: authentication required")

    async def call(self, tool: str, arguments: dict) -> dict:
        if self.principal_id is None:
            raise ForwardError("unauthenticated: authentication required")
        if tool not in TOOLS:
            raise ForwardError("unknown_tool: unsupported bridge tool")
        return await self._request(tool, arguments)


def build_forward_server(forwarder: Forwarder) -> MCPServer:
    async def authenticate_method(ctx, call_next):
        # SDK middleware wraps initialize, discovery, unknown methods and notifications,
        # before validation or handler lookup. Check again before releasing any result.
        try:
            await forwarder.check()
        except ForwardError:
            raise MCPError(code=-32001, message="authentication required") from None
        result = await call_next(ctx)
        if ctx.request_id is not None:
            try:
                await forwarder.check()
            except ForwardError as exc:
                # Withhold the result only on a real credential failure. A transient error
                # (rate limit, restart) must not hide work already admitted, e.g. a task_id.
                if str(exc).startswith("unauthenticated"):
                    raise MCPError(code=-32001, message="authentication required") from None
        return result

    mcp = MCPServer("spqr-bridge", version=__version__, instructions=INSTRUCTIONS,
                    middleware=[authenticate_method])

    async def call(tool: str, args: dict) -> dict[str, Any]:
        try:
            return await forwarder.call(tool, args)
        except ForwardError as exc:
            raise ToolError(str(exc)) from None

    @mcp.tool()
    async def list_agents() -> dict[str, Any]:
        """List the agents this principal may task. Makes no model calls."""
        return await call("list_agents", {})

    @mcp.tool()
    async def start_task(
        agent_id: str,
        task: Annotated[str, Field(description="Bounded task instructions, passed on stdin.")],
        workspace: Annotated[str, Field(description="Absolute directory within this job's scope.")],
        context: str | None = None,
        access: Literal["read_only", "write"] = "read_only",
        timeout_seconds: Annotated[int | None, Field(ge=1)] = None,
        idempotency_key: Annotated[str | None, Field(max_length=128)] = None,
        grant_id: Annotated[str | None, Field(max_length=32)] = None,
    ) -> dict[str, Any]:
        """Queue bounded work; Jimmy must issue a grant for write access."""
        return await call("start_task", {"agent_id": agent_id, "task": task, "workspace": workspace,
                                        "context": context, "access": access, "timeout_seconds": timeout_seconds,
                                        "idempotency_key": idempotency_key, "grant_id": grant_id})

    @mcp.tool()
    async def get_task(task_id: str, wait_seconds: Annotated[float, Field(ge=0, le=10)] = 0) -> dict[str, Any]:
        """Get one of your tasks, optionally waiting up to ten seconds."""
        return await call("get_task", {"task_id": task_id, "wait_seconds": wait_seconds})

    @mcp.tool()
    async def cancel_task(task_id: str) -> dict[str, Any]:
        """Cancel one of your queued or running tasks; returns after group cleanup."""
        return await call("cancel_task", {"task_id": task_id})

    return mcp


async def _run(path: Path, token: bytes) -> None:
    f = Forwarder(path, token)
    await f.authenticate()  # No MCP initialize/discovery until the owner accepted the credential.
    await build_forward_server(f).run_stdio_async()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--token-file", required=True)
    args = parser.parse_args(argv)
    if current_depth() >= MAX_DEPTH:
        print("spqr-connect: recursion_denied: bridge worker cannot open a bridge", file=sys.stderr)
        raise SystemExit(2)
    token = read_token_file(args.token_file)
    try:
        asyncio.run(_run(args.socket, token))
    except ForwardError as exc:
        print(f"spqr-connect: {exc}", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
