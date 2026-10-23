"""HTTP client of the shared owner: the forwarder's network transport.

Same stdio MCP face as forward.py, but the envelope travels over the bridge's
HTTP door (Tailscale) instead of the private Unix socket. The credential rides
only in the Authorization: Bearer header — never in the request body — and is
bound to the principal returned at bootstrap, exactly as on the socket. Run as:
python -m spqr_bridge.httpforward --url http://100.x.y.z:PORT --token-file F
"""
from __future__ import annotations

import argparse
import asyncio
import json
import ssl
import sys
from pathlib import Path
from urllib.parse import urlsplit

from .envpolicy import MAX_DEPTH, current_depth
from .forward import (MAX_LINE_BYTES, MAX_RESPONSE_BYTES, REQUEST_TIMEOUT, _CODE,
                      ForwardError, Forwarder, build_forward_server)
from .launch import read_token_file

_MAX_HEADERS = 64


class HttpForwarder(Forwarder):
    """A Forwarder whose private channel is the HTTP door, not the socket."""

    def __init__(self, url: str, token: bytes):
        parts = urlsplit(url)
        if (parts.scheme not in ("http", "https") or not parts.hostname
                or parts.path not in ("", "/") or parts.query or parts.fragment
                or parts.username or parts.password):
            raise ForwardError("unavailable: invalid bridge URL")
        super().__init__(Path("/dev/null"), token)  # path unused; token checks inherited
        self._host = parts.hostname
        self._tls = parts.scheme == "https"
        self._port = parts.port or (443 if self._tls else 80)
        host_part = f"[{self._host}]" if ":" in self._host else self._host
        self._authority = f"{host_part}:{self._port}"

    async def _request(self, tool: str, arguments: dict) -> dict:
        request = {"tool": tool, "arguments": arguments}
        if tool != "hello":
            request["principal_id"] = self.principal_id
        wire = json.dumps(request, ensure_ascii=True, allow_nan=False).encode()
        if len(wire) > MAX_LINE_BYTES:
            raise ForwardError("request_too_large: bridge request exceeds limit")
        head = (f"POST / HTTP/1.1\r\nHost: {self._authority}\r\n"
                f"Authorization: Bearer {self._token}\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(wire)}\r\nConnection: close\r\n\r\n").encode()
        writer = None
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                context = ssl.create_default_context() if self._tls else None
                reader, writer = await asyncio.open_connection(
                    self._host, self._port, ssl=context, limit=MAX_RESPONSE_BYTES)
                writer.write(head + wire)
                await writer.drain()
                response = await self._read_response(reader)
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

    async def _read_response(self, reader: asyncio.StreamReader) -> dict:
        status_line = await reader.readline()
        parts = status_line.split(b" ", 2)
        if len(parts) < 2 or not parts[1].isdigit():
            raise ForwardError("protocol_error: invalid bridge response")
        length = None
        for _ in range(_MAX_HEADERS):
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            name, sep, value = line.partition(b":")
            if sep and name.strip().lower() == b"content-length":
                try:
                    length = int(value.strip())
                except ValueError:
                    raise ForwardError("protocol_error: invalid bridge response") from None
        if length is None or length < 0 or length > MAX_RESPONSE_BYTES:
            raise ForwardError("protocol_error: invalid bridge response")
        body = await reader.readexactly(length)
        return json.loads(body)


async def _run(url: str, token: bytes) -> None:
    f = HttpForwarder(url, token)
    await f.authenticate()  # No MCP initialize/discovery until the door accepted the credential.
    await build_forward_server(f).run_stdio_async()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--url", required=True)
    parser.add_argument("--token-file", required=True)
    args = parser.parse_args(argv)
    if current_depth() >= MAX_DEPTH:
        print("spqr-connect: recursion_denied: bridge worker cannot open a bridge", file=sys.stderr)
        raise SystemExit(2)
    cred = read_token_file(args.token_file)
    try:
        asyncio.run(_run(args.url, cred))
    except ForwardError as exc:
        print(f"spqr-connect: {exc}", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
