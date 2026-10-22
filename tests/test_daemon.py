"""Shared owner behavior, using fake agents and temporary state only."""
import asyncio
import json

import pytest

from spqr_bridge.config import load_config
from spqr_bridge.daemon import SharedOwner, validate_service
from v2_helpers import TOKENS, registry_text, write_registry


@pytest.fixture
async def owner(env_setup):
    cfg_text, ws, _, tmp = env_setup
    cfg = tmp / "bridge.toml"
    cfg.write_text(cfg_text())
    reg = write_registry(tmp / "registry.toml", registry_text(ws))
    control = tmp / "control"
    control.mkdir(mode=0o700)
    async with SharedOwner(load_config(cfg), reg, control / "bridge.sock") as instance:
        yield instance, ws, reg


async def call(owner, who="caesar", tool="hello", arguments=None, **extra):
    reader, writer = await asyncio.open_unix_connection(str(owner.socket))
    try:
        request = dict(token=TOKENS[who], tool=tool, arguments=arguments or {})
        request.update(extra)
        writer.write(json.dumps(request).encode() + b"\n")
        await writer.drain()
        return json.loads(await reader.readline())
    finally:
        writer.close()
        await writer.wait_closed()


async def test_two_principals_share_owner_and_keep_jobs_private(owner):
    daemon, ws, _ = owner
    greetings = await asyncio.gather(call(daemon), call(daemon, "iggy"))
    assert [r["result"]["principal_id"] for r in greetings] == ["caesar", "iggy"]
    started = await call(daemon, tool="start_task", arguments={
        "agent_id": "claude", "task": "say hello", "workspace": str(ws),
        "idempotency_key": "shared-owner-test"}, principal_id="caesar")
    assert started["ok"], started
    tid = started["result"]["task_id"]
    # Each request disconnects; the owner and the admitted job remain alive.
    result = await call(daemon, tool="get_task", arguments={"task_id": tid, "wait_seconds": 10},
                        principal_id="caesar")
    assert result["result"]["state"] == "succeeded"
    stolen = await call(daemon, "iggy", "get_task", {"task_id": tid}, principal_id="iggy")
    assert stolen["error"]["code"] == "unknown_task"
    replay = await call(daemon, tool="start_task", arguments={
        "agent_id": "claude", "task": "say hello", "workspace": str(ws),
        "idempotency_key": "shared-owner-test"}, principal_id="caesar")
    assert replay["result"]["task_id"] == tid and replay["result"]["replayed"]


async def test_bad_credentials_binding_and_registry_revocation(owner):
    daemon, ws, reg = owner
    bad = await call(daemon, token="not-a-credential")
    assert bad["error"]["code"] == "unauthenticated"
    switched = await call(daemon, "iggy", "list_agents", principal_id="caesar")
    assert switched["error"]["code"] == "unauthenticated"
    missing = await call(daemon, tool="list_agents")
    assert missing["error"]["code"] == "unauthenticated"
    reg.write_text(registry_text(ws, principals={"caesar": {"enabled": False}}))
    revoked = await call(daemon, tool="list_agents", principal_id="caesar")
    assert revoked["error"]["code"] == "unauthenticated"


async def test_arguments_and_protocol_are_bounded(owner):
    daemon, _, _ = owner
    bad = await call(daemon, tool="get_task", arguments={"task_id": "x", "wait_seconds": 11},
                     principal_id="caesar")
    assert bad["error"]["code"] == "invalid_arguments"
    unknown = await call(daemon, tool="shell", principal_id="caesar")
    assert unknown["error"]["code"] == "unknown_tool"
    reader, writer = await asyncio.open_unix_connection(str(daemon.socket))
    writer.write(b"x" * 262145 + b"\n")
    await writer.drain()
    assert json.loads(await reader.readline())["error"]["code"] == "invalid_request"
    writer.close()
    await writer.wait_closed()


async def test_competing_owner_cannot_replace_socket_or_store(owner):
    daemon, _, reg = owner
    inode = daemon.socket.stat().st_ino
    with pytest.raises((RuntimeError, OSError)):
        async with SharedOwner(daemon.cfg, reg, daemon.socket):
            pass
    assert daemon.socket.stat().st_ino == inode
    assert (await call(daemon))["ok"]


def test_manual_production_start_is_refused_before_state_open():
    with pytest.raises(RuntimeError):
        validate_service("not-a-unit")


async def test_request_rate_is_bounded_and_recovers(owner):
    daemon, _, _ = owner
    assert sum(daemon.take_request(100) for _ in range(100)) == 64
    assert not daemon.take_request(100)
    assert sum(daemon.take_request(101) for _ in range(100)) == 32
