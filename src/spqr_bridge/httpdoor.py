"""The bridge's HTTP door: the shared owner's network ingress (DESIGN-V2 transport).

Speaks the same request envelope as the local IPC ingress — {tool, arguments,
principal_id} — with one difference that is the point of the door: the credential
travels only in the Authorization: Bearer header, never in the body, a URL or a
query string. A body that carries a token is refused. Every request, whatever
its method or path, is authenticated before routing: there is no anonymous
discovery or health endpoint. Forwarded headers are never read, let alone
trusted; the peer is whoever the tailnet says it is, and the bearer token is
what authorizes them.

Admission policy lives with the owner, not here: the daemon only starts this
door when --http-bind/--http-port are configured, and validate_http_bind
refuses anything that is not an address inside the Tailscale ranges
(100.64.0.0/10, fd7a:115c:a1e0::/48) verifiably assigned to this host. Wildcard,
loopback, link-local and public binds fail closed, as does an address probe
that cannot be completed. The registry separately decides which principals may
use the http transport at all; a stdio-only credential fails here even though
the same token works on the socket.

Confidentiality on the wire is the WireGuard tunnel's (plain HTTP), unless a
future operator decision puts TLS in front; this module deliberately makes no
TLS claim of its own.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import subprocess
from typing import Iterable
from urllib.parse import urlsplit

from .registry import RegistryError, authenticate, load_registry
from .v2 import _log_auth_failure

MAX_HEADER_BYTES = 16384
MAX_BODY_BYTES = 262144  # matches the IPC ingress frame bound in daemon.py
MAX_RESPONSE_BYTES = 4 * 1024 * 1024  # matches the IPC response bound
MAX_CONNECTIONS = 32  # matches the IPC connection bound
MAX_HEADERS = 64
REQUEST_TIMEOUT = 15  # matches the IPC per-request bound

TAILNET_V4 = ipaddress.ip_network("100.64.0.0/10")
TAILNET_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")

_REASONS = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
            404: "Not Found", 405: "Method Not Allowed", 411: "Length Required",
            413: "Content Too Large", 421: "Misdirected Request",
            429: "Too Many Requests", 500: "Internal Server Error",
            503: "Service Unavailable"}
_STATUS = {"unauthenticated": 401, "invalid_request": 400, "invalid_arguments": 400,
           "unknown_tool": 404, "rate_limited": 429, "busy": 503,
           "response_too_large": 500}


def _error(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


def validate_http_bind(bind: str, assigned: Iterable[str]) -> str:
    """Return the normalized bind address or raise; the door fails closed."""
    try:
        ip = ipaddress.ip_address(bind)
    except ValueError:
        raise RuntimeError("http bind is not an IP address") from None
    net = TAILNET_V4 if ip.version == 4 else TAILNET_V6
    if ip not in net:
        raise RuntimeError("http bind must be an address in the Tailscale range")
    if str(ip) not in set(assigned):
        raise RuntimeError("http bind is not assigned to this host")
    return str(ip)


def local_addresses() -> set[str]:
    """Every address assigned to this host, probed — never assumed."""
    result = subprocess.run(["ip", "-j", "addr", "show"], capture_output=True,
                            text=True, timeout=5, check=True)
    addrs = set()
    for iface in json.loads(result.stdout):
        for info in iface.get("addr_info", []):
            if info.get("local"):
                addrs.add(info["local"])
    return addrs


class HttpDoor:
    """Mechanism only: one authenticated POST of the bridge envelope per connection."""

    def __init__(self, owner, host: str, port: int, names: Iterable[str] = ()):
        self.owner = owner
        self.host = host
        self.port = port
        self._names = tuple(names)
        self.authorities = self._authorities(port)
        self.server: asyncio.Server | None = None
        self.connections: set[asyncio.Task] = set()

    def _authorities(self, port: int) -> set[str]:
        host_part = f"[{self.host}]" if ":" in self.host and not self.host.startswith("[") else self.host
        return {f"{host_part}:{port}".lower()} | {f"{n}:{port}".lower() for n in self._names}

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, self.host, self.port)
        # An ephemeral port (tests) becomes concrete only once bound.
        self.authorities = self._authorities(self.server.sockets[0].getsockname()[1])

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
        for task in list(self.connections):
            task.cancel()
        if self.connections:
            await asyncio.gather(*list(self.connections), return_exceptions=True)
        if self.server is not None:
            await self.server.wait_closed()

    # -- request handling -------------------------------------------------

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        task = asyncio.current_task()
        accepted = len(self.connections) < MAX_CONNECTIONS
        if accepted:
            self.connections.add(task)
        try:
            if not accepted:
                status, response = 503, _error("busy", "too many bridge connections")
            else:
                status, response = await self._serve(reader)
            wire = json.dumps(response, ensure_ascii=True).encode() + b"\n"
            if len(wire) > MAX_RESPONSE_BYTES:
                status = 500
                wire = json.dumps(_error("response_too_large",
                                         "result exceeds the HTTP frame limit")).encode() + b"\n"
            head = (f"HTTP/1.1 {status} {_REASONS[status]}\r\n"
                    f"Content-Type: application/json\r\n"
                    f"Content-Length: {len(wire)}\r\nConnection: close\r\n\r\n").encode()
            writer.write(head + wire)
            async with asyncio.timeout(5):
                await writer.drain()
        except TimeoutError:
            writer.transport.abort()
        except (ConnectionError, OSError):
            pass
        except Exception:
            # Never leak protocol internals or credentials into a response.
            try:
                body = json.dumps(_error("internal_error", "bridge request failed")).encode() + b"\n"
                writer.write((f"HTTP/1.1 500 Internal Server Error\r\n"
                              f"Content-Type: application/json\r\n"
                              f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode() + body)
                async with asyncio.timeout(5):
                    await writer.drain()
            except (ConnectionError, OSError, TimeoutError):
                writer.transport.abort()
        finally:
            writer.close()
            try:
                async with asyncio.timeout(1):
                    await writer.wait_closed()
            except (ConnectionError, OSError, TimeoutError):
                writer.transport.abort()
            if accepted:
                self.connections.discard(task)

    async def _serve(self, reader: asyncio.StreamReader) -> tuple[int, dict]:
        async with asyncio.timeout(REQUEST_TIMEOUT):
            head = await self._read_head(reader)
            if isinstance(head[0], int):  # an error (status, envelope), not a request
                return head
            method, target, headers, prefix = head
            # Host first: a rebound name is refused before any credential work.
            host = self._single(headers, "host")
            if host is None or host.lower() not in self.authorities:
                return 421, _error("invalid_request", "invalid host")
            origin = self._single(headers, "origin")
            if origin is not None:
                authority = urlsplit(origin).netloc
                if not authority or authority.lower() not in self.authorities:
                    return 403, _error("invalid_request", "invalid origin")
            if not self.owner.take_request():
                return 429, _error("rate_limited", "bridge request rate exceeded; wait before retrying")
            token = self._bearer(headers)
            auth = None
            if token is not None:
                try:
                    auth = authenticate(load_registry(self.owner.registry),
                                        token.encode(), "http")
                except RegistryError:
                    auth = None
            if auth is None:
                _log_auth_failure(self.owner.cfg.server.state_dir, "bad_credential")
                return 401, _error("unauthenticated", "authentication required")
            if "?" in target or "#" in target:
                return 400, _error("invalid_request", "invalid target")
            if target != "/":
                return 404, _error("invalid_request", "unknown path")
            if method != "POST":
                return 405, _error("invalid_request", "method not allowed")
            body = await self._read_body(reader, headers, prefix)
            if isinstance(body, tuple):
                return body
            try:
                request = json.loads(body)
            except (ValueError, UnicodeError):
                return 400, _error("invalid_request", "invalid request")
            if not isinstance(request, dict) or "token" in request:
                return 400, _error("invalid_request",
                                   "credentials travel in the Authorization header only")
            response = await self.owner.dispatch({**request, "token": token}, transport="http")
            if response.get("ok"):
                return 200, response
            code = (response.get("error") or {}).get("code", "")
            return _STATUS.get(code, 200), response

    async def _read_head(self, reader: asyncio.StreamReader):
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = await reader.read(4096)
            if not chunk:
                raise ConnectionError("peer closed during headers")
            buf += chunk
            if len(buf) > MAX_HEADER_BYTES:
                return 400, _error("invalid_request", "headers too large")
        raw, _, prefix = buf.partition(b"\r\n\r\n")
        lines = raw.decode("iso-8859-1").split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) != 3 or parts[2] not in ("HTTP/1.0", "HTTP/1.1") or not parts[0] or not parts[1]:
            return 400, _error("invalid_request", "invalid request line")
        headers: list[tuple[str, str]] = []
        for line in lines[1:]:
            name, sep, value = line.partition(":")
            if not sep or not name.strip():
                return 400, _error("invalid_request", "invalid header")
            headers.append((name.strip().lower(), value.strip()))
            if len(headers) > MAX_HEADERS:
                return 400, _error("invalid_request", "too many headers")
        return parts[0], parts[1], headers, prefix

    async def _read_body(self, reader: asyncio.StreamReader, headers, prefix: bytes):
        if self._all(headers, "transfer-encoding"):
            return 400, _error("invalid_request", "transfer encoding not accepted")
        lengths = self._all(headers, "content-length")
        if len(lengths) != 1:
            return 411, _error("invalid_request", "exactly one content-length is required")
        try:
            length = int(lengths[0])
        except ValueError:
            return 400, _error("invalid_request", "invalid content-length")
        if length < 0:
            return 400, _error("invalid_request", "invalid content-length")
        if length > MAX_BODY_BYTES:
            return 413, _error("invalid_request", "request too large")
        if len(prefix) > length:
            return 400, _error("invalid_request", "pipelined data is not accepted")
        rest = await reader.readexactly(length - len(prefix)) if len(prefix) < length else b""
        return prefix + rest

    # -- header helpers ----------------------------------------------------

    @staticmethod
    def _all(headers: list[tuple[str, str]], name: str) -> list[str]:
        return [v for n, v in headers if n == name]

    def _single(self, headers, name: str):
        """The header's value only when it appears exactly once, else None."""
        values = self._all(headers, name)
        if len(values) != 1:
            return None
        return values[0]

    def _bearer(self, headers) -> str | None:
        values = self._all(headers, "authorization")
        if len(values) != 1:
            return None
        parts = values[0].split(" ")
        if len(parts) != 2 or parts[0] != "Bearer":
            return None
        cred = parts[1]
        if not cred or len(cred) > 4096:
            return None
        return cred
