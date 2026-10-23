"""HTTP door behavior, using fake agents and temporary state only.

The door is bound to loopback here because a test host has no Tailscale
address to bind; admission policy (which addresses may be bound at all) is
tested separately against validate_http_bind as a pure function.
"""
import asyncio
import json

import pytest

from spqr_bridge.config import load_config
from spqr_bridge.daemon import SharedOwner
from spqr_bridge.forward import ForwardError
from spqr_bridge.httpdoor import HttpDoor, validate_http_bind
from spqr_bridge.httpforward import HttpForwarder
from v2_helpers import TOKENS, registry_text, write_registry

# caesar may use the door; iggy's credential stays stdio-only.
PRINCIPALS = {"caesar": {"transports": ["stdio", "http"]}}


@pytest.fixture
async def door(env_setup):
    cfg_text, ws, _, tmp = env_setup
    cfg = tmp / "bridge.toml"
    cfg.write_text(cfg_text())
    reg = write_registry(tmp / "registry.toml", registry_text(ws, principals=PRINCIPALS))
    control = tmp / "control"
    control.mkdir(mode=0o700)
    async with SharedOwner(load_config(cfg), reg, control / "bridge.sock") as owner:
        instance = HttpDoor(owner, "127.0.0.1", 0)
        await instance.start()
        port = instance.server.sockets[0].getsockname()[1]
        yield owner, instance, port, ws
        await instance.close()


def post(port, obj=None, token=None, host=None, extra=(), method="POST", target="/",
         raw_body=None):
    body = raw_body if raw_body is not None else json.dumps(obj or {}).encode()
    lines = [f"{method} {target} HTTP/1.1", f"Host: {host or f'127.0.0.1:{port}'}"]
    if token is not None:
        lines.append(f"Authorization: Bearer {token}")
    lines += list(extra)
    lines += [f"Content-Length: {len(body)}", "Connection: close", "", ""]
    return ("\r\n".join(lines)).encode() + body


async def http(port, raw):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(raw)
        await writer.drain()
        head = await reader.readuntil(b"\r\n\r\n")
        status = int(head.split(b" ", 2)[1])
        length = int([line for line in head.split(b"\r\n")
                      if line.lower().startswith(b"content-length")][0].split(b":")[1])
        return status, json.loads(await reader.readexactly(length))
    finally:
        writer.close()
        await writer.wait_closed()


# -- admission policy ------------------------------------------------------


def test_bind_policy_fails_closed():
    assigned = {"100.101.2.3", "fd7a:115c:a1e0::7", "192.168.1.5"}
    for bad in ("0.0.0.0", "::", "127.0.0.1", "::1", "192.168.1.5", "8.8.8.8",
                "2001:4860:4860::8888", "not-an-ip", "bridge.local"):
        with pytest.raises(RuntimeError):
            validate_http_bind(bad, assigned)
    # Tailscale-shaped but not assigned to this host: still refused.
    with pytest.raises(RuntimeError):
        validate_http_bind("100.64.0.9", assigned)
    with pytest.raises(RuntimeError):
        validate_http_bind("fd7a:115c:a1e0::9", assigned)
    assert validate_http_bind("100.101.2.3", assigned) == "100.101.2.3"
    assert validate_http_bind("fd7a:115c:a1e0::7", assigned) == "fd7a:115c:a1e0::7"


# -- authentication and routing --------------------------------------------


async def test_hello_and_list_agents_over_http(door):
    _, _, port, _ = door
    status, body = await http(port, post(port, {"tool": "hello", "arguments": {}},
                                         token=TOKENS["caesar"]))
    assert status == 200 and body["result"]["principal_id"] == "caesar"
    status, body = await http(port, post(port, {"tool": "list_agents", "arguments": {},
                                                "principal_id": "caesar"},
                                         token=TOKENS["caesar"]))
    assert status == 200
    assert {a["id"] for a in body["result"]["agents"]} >= {"claude", "codex"}


async def test_credentials_are_scoped_to_the_door(door):
    _, _, port, _ = door
    # iggy's credential is valid — on stdio. The door must refuse it.
    status, body = await http(port, post(port, {"tool": "hello", "arguments": {}},
                                         token=TOKENS["iggy"]))
    assert status == 401 and body["error"]["code"] == "unauthenticated"
    status, _ = await http(port, post(port, {"tool": "hello", "arguments": {}}))
    assert status == 401  # no credential at all
    status, _ = await http(port, post(port, {"tool": "hello", "arguments": {}},
                                      token="n" * 50))
    assert status == 401  # wrong credential
    # A credential presented for another principal is refused by the dispatcher.
    status, body = await http(port, post(port, {"tool": "list_agents", "arguments": {},
                                                "principal_id": "iggy"},
                                         token=TOKENS["caesar"]))
    assert status == 401 and body["error"]["code"] == "unauthenticated"


async def test_token_in_body_is_refused(door):
    _, _, port, _ = door
    status, body = await http(port, post(
        port, {"token": TOKENS["caesar"], "tool": "hello", "arguments": {}},
        token=TOKENS["caesar"]))
    assert status == 400 and body["error"]["code"] == "invalid_request"


async def test_everything_requires_auth_before_routing(door):
    _, _, port, _ = door
    status, _ = await http(port, post(port, method="GET"))
    assert status == 401  # even discovery-shaped requests authenticate first
    status, body = await http(port, post(port, method="GET", token=TOKENS["caesar"]))
    assert status == 405
    status, body = await http(port, post(port, {"tool": "hello", "arguments": {}},
                                         token=TOKENS["caesar"], target="/agents"))
    assert status == 404
    status, body = await http(port, post(port, {"tool": "hello", "arguments": {}},
                                         token=TOKENS["caesar"], target="/?probe=1"))
    assert status == 400  # query strings are refused outright


async def test_host_origin_and_duplicate_headers(door):
    _, _, port, _ = door
    status, _ = await http(port, post(port, {"tool": "hello", "arguments": {}},
                                      token=TOKENS["caesar"], host="evil.example:1"))
    assert status == 421
    dup = post(port, {"tool": "hello", "arguments": {}}, token=TOKENS["caesar"],
               extra=[f"Authorization: Bearer {TOKENS['iggy']}"])
    status, _ = await http(port, dup)
    assert status == 401  # duplicate Authorization headers are refused, not picked from
    status, _ = await http(port, post(
        port, {"tool": "hello", "arguments": {}}, token=TOKENS["caesar"],
        extra=["Origin: http://evil.example:1"]))
    assert status == 403


async def test_body_bounds(door):
    _, _, port, _ = door
    head = post(port, token=TOKENS["caesar"],
                extra=["Transfer-Encoding: chunked"], raw_body=b"")
    head = head.replace(b"Content-Length: 0\r\n", b"")
    status, body = await http(port, head)
    assert status == 400
    big = (f"POST / HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
           f"Authorization: Bearer {TOKENS['caesar']}\r\n"
           f"Content-Length: 262145\r\nConnection: close\r\n\r\n").encode()
    status, body = await http(port, big)
    assert status == 413


# -- the client half ---------------------------------------------------------


async def test_http_forwarder_round_trip_and_task(door):
    _, _, port, ws = door
    fwd = HttpForwarder(f"http://127.0.0.1:{port}", TOKENS["caesar"].encode())
    await fwd.authenticate()
    assert fwd.principal_id == "caesar"
    agents = await fwd.call("list_agents", {})
    assert {a["id"] for a in agents["agents"]} >= {"claude", "codex"}
    started = await fwd.call("start_task", {"agent_id": "claude", "task": "say hello",
                                            "workspace": str(ws),
                                            "idempotency_key": "http-door-test"})
    result = await fwd.call("get_task", {"task_id": started["task_id"], "wait_seconds": 10})
    assert result["state"] == "succeeded"


async def test_http_forwarder_rejects_stdio_only_credential(door):
    _, _, port, _ = door
    fwd = HttpForwarder(f"http://127.0.0.1:{port}", TOKENS["iggy"].encode())
    with pytest.raises(ForwardError):
        await fwd.authenticate()
