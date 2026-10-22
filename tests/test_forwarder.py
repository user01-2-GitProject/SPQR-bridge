"""Thin forwarder contract; all agents and daemon replies are fake."""
import asyncio
import json
import os
import subprocess
from pathlib import Path

import pytest

from spqr_bridge.forward import ForwardError, Forwarder

TOKEN = b"test-token-" + b"a" * 43


@pytest.fixture
async def fake_daemon(tmp_path):
    socket = tmp_path / "bridge.sock"
    requests = []

    async def handle(reader, writer):
        request = json.loads(await reader.readline())
        requests.append(request)
        if request["token"] != TOKEN.decode():
            response = {"ok": False, "error": {"code": "unauthenticated", "message": "authentication required"}}
        elif request["tool"] in {"hello", "check"}:
            response = {"ok": True, "result": {"principal_id": "caesar"}}
        else:
            response = {"ok": True, "result": {"agents": [{"id": "fixture"}]}}
        writer.write(json.dumps(response).encode() + b"\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_unix_server(handle, path=str(socket))
    socket.chmod(0o600)
    try:
        yield socket, requests
    finally:
        server.close()
        await server.wait_closed()


async def test_forwarder_authenticates_then_binds_principal(fake_daemon):
    socket, requests = fake_daemon
    f = Forwarder(socket, TOKEN)
    await f.authenticate()
    assert f.principal_id == "caesar"
    assert await f.call("list_agents", {}) == {"agents": [{"id": "fixture"}]}
    assert requests[0]["tool"] == "hello"
    assert requests[1]["principal_id"] == "caesar"
    assert requests[1]["arguments"] == {}


async def test_forwarder_bad_credential_is_generic(fake_daemon):
    socket, _ = fake_daemon
    f = Forwarder(socket, b"wrong-secret-" + b"b" * 43)
    with pytest.raises(ForwardError, match="unauthenticated") as err:
        await f.authenticate()
    assert "wrong-secret" not in str(err.value)


async def test_forwarder_refuses_calls_before_authentication(fake_daemon):
    socket, requests = fake_daemon
    f = Forwarder(socket, TOKEN)
    with pytest.raises(ForwardError, match="unauthenticated"):
        await f.call("list_agents", {})
    assert not requests


async def test_forwarder_rejects_public_socket(fake_daemon):
    socket, requests = fake_daemon
    socket.chmod(0o660)
    with pytest.raises(ForwardError, match="unavailable"):
        await Forwarder(socket, TOKEN).authenticate()
    assert not requests


async def test_forwarder_rejects_symlink_socket(fake_daemon):
    socket, requests = fake_daemon
    link = socket.parent / "link.sock"
    link.symlink_to(socket)
    with pytest.raises(ForwardError, match="unavailable"):
        await Forwarder(link, TOKEN).authenticate()
    assert not requests


async def test_forwarder_rejects_oversize_request(fake_daemon):
    socket, requests = fake_daemon
    f = Forwarder(socket, TOKEN)
    await f.authenticate()
    with pytest.raises(ForwardError, match="request_too_large"):
        await f.call("start_task", {"task": "x" * 262144})
    assert len(requests) == 1


@pytest.mark.parametrize("name", ["", "../x", "Upper", "a b", "a\nx", "x" * 33])
def test_connect_wrapper_rejects_bad_slot(name, tmp_path):
    wrapper = Path(__file__).resolve().parents[1] / "scripts" / "spqr-connect"
    p = subprocess.run([str(wrapper), name], env={**os.environ, "SPQR_CONFIG_DIR": str(tmp_path)},
                       capture_output=True, timeout=10)
    assert p.returncode == 2 and b"slot" in p.stderr


async def test_actual_stdio_mcp_discovery_and_call(fake_daemon, tmp_path):
    import sys
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters

    socket, requests = fake_daemon
    token = tmp_path / "caesar.token"
    token.write_bytes(TOKEN)
    token.chmod(0o600)
    params = StdioServerParameters(command=sys.executable,
                                  args=["-m", "spqr_bridge.forward", "--socket", str(socket),
                                        "--token-file", str(token)])
    async with Client(params, read_timeout_seconds=20) as client:
        tools = await client.list_tools()
        assert {tool.name for tool in tools.tools} == {
            "list_agents", "start_task", "get_task", "cancel_task"}
        start = next(tool for tool in tools.tools if tool.name == "start_task")
        assert "grant_id" in start.input_schema["properties"]
        assert not {"token", "principal_id"} & set(start.input_schema["properties"])
        result = await client.call_tool("list_agents", {})
        assert not result.is_error and "fixture" in result.content[0].text
    assert requests[0]["tool"] == "hello"
    assert requests[-1]["principal_id"] == "caesar"


def test_forwarder_depth_refusal_precedes_token_read(tmp_path):
    import sys
    result = subprocess.run([sys.executable, "-m", "spqr_bridge.forward", "--socket", str(tmp_path / "sock"),
                             "--token-file", str(tmp_path / "does-not-exist")],
                            env={**os.environ, "SPQR_BRIDGE_DEPTH": "1"}, capture_output=True, timeout=10)
    assert result.returncode == 2 and b"recursion_denied" in result.stderr
    assert result.stdout == b""


async def test_two_actual_clients_share_owner_and_keep_private_jobs(env_setup):
    import sys
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters
    from spqr_bridge.config import load_config
    from spqr_bridge.daemon import SharedOwner
    from v2_helpers import TOKENS, registry_text, write_registry

    cfg_text, workspace, _, tmp = env_setup
    cfg = tmp / "bridge.toml"
    cfg.write_text(cfg_text())
    reg = write_registry(tmp / "registry.toml", registry_text(workspace))
    ipc = tmp / "ipc"
    ipc.mkdir(mode=0o700)
    sock = ipc / "bridge.sock"

    def client(pid):
        token = tmp / f"{pid}.token"
        token.write_text(TOKENS[pid])
        token.chmod(0o600)
        return Client(StdioServerParameters(command=sys.executable,
                      args=["-m", "spqr_bridge.forward", "--socket", str(sock), "--token-file", str(token)]),
                      read_timeout_seconds=20)

    def payload(result):
        structured = result.structured_content
        if structured is not None:
            return structured.get("result", structured) if set(structured) == {"result"} else structured
        return json.loads(result.content[0].text)

    async with SharedOwner(load_config(cfg), reg, sock) as owner:
        async with client("caesar") as caesar, client("iggy") as iggy:
            assert "start_task" in {t.name for t in (await caesar.list_tools()).tools}
            assert "start_task" in {t.name for t in (await iggy.list_tools()).tools}
            starts = await asyncio.gather(
                caesar.call_tool("start_task", {"agent_id": "claude", "task": "hello from caesar",
                                                "workspace": str(workspace)}),
                iggy.call_tool("start_task", {"agent_id": "claude", "task": "hello from iggy",
                                              "workspace": str(workspace)}))
            first, second = [payload(result) for result in starts]
            foreign = await iggy.call_tool("get_task", {"task_id": first["task_id"]})
            assert foreign.is_error and "unknown_task" in foreign.content[0].text
            done = payload(await caesar.call_tool("get_task", {"task_id": first["task_id"], "wait_seconds": 5}))
            assert done["state"] == "succeeded" and done["principal_id"] == "caesar"
            assert second["task_id"] != first["task_id"]
        # Neither MCP client's exit closes the Store or the job owner.
        assert owner.server.is_serving()
        async with client("iggy") as reconnected:
            done = payload(await reconnected.call_tool("get_task", {"task_id": second["task_id"],
                                                                     "wait_seconds": 5}))
            assert done["state"] == "succeeded" and done["principal_id"] == "iggy"


async def test_existing_mcp_discovery_fails_after_revocation(env_setup):
    import sys
    from mcp import Client, MCPError
    from mcp.client.stdio import StdioServerParameters
    from spqr_bridge.config import load_config
    from spqr_bridge.daemon import SharedOwner
    from v2_helpers import TOKENS, registry_text, write_registry

    cfg_text, workspace, _, tmp = env_setup
    cfg = tmp / "bridge.toml"
    cfg.write_text(cfg_text())
    reg = write_registry(tmp / "registry.toml", registry_text(workspace))
    token = tmp / "caesar.token"
    token.write_text(TOKENS["caesar"])
    token.chmod(0o600)
    ipc = tmp / "ipc"
    ipc.mkdir(mode=0o700)
    sock = ipc / "bridge.sock"
    params = StdioServerParameters(command=sys.executable,
                                  args=["-m", "spqr_bridge.forward", "--socket", str(sock),
                                        "--token-file", str(token)])
    async with SharedOwner(load_config(cfg), reg, sock):
        async with Client(params, read_timeout_seconds=20) as client:
            assert len((await client.list_tools()).tools) == 4
            write_registry(reg, registry_text(workspace, principals={"caesar": {"enabled": False}}))
            with pytest.raises(MCPError, match="authentication"):
                await client.list_tools()


async def test_identity_never_changes_after_bootstrap(fake_daemon):
    socket, requests = fake_daemon
    f = Forwarder(socket, TOKEN)
    await f.authenticate()
    await f.authenticate()
    assert f.principal_id == "caesar"
    assert requests[-1]["tool"] == "check"
    assert requests[-1]["principal_id"] == "caesar"


async def test_reassigned_credential_cannot_switch_bound_identity(env_setup):
    from spqr_bridge.config import load_config
    from spqr_bridge.daemon import SharedOwner
    from v2_helpers import TOKENS, digest, registry_text, write_registry

    cfg_text, workspace, _, tmp = env_setup
    cfg = tmp / "bridge.toml"
    cfg.write_text(cfg_text())
    reg = write_registry(tmp / "registry.toml", registry_text(workspace))
    ipc = tmp / "ipc"
    ipc.mkdir(mode=0o700)
    async with SharedOwner(load_config(cfg), reg, ipc / "bridge.sock"):
        f = Forwarder(ipc / "bridge.sock", TOKENS["caesar"].encode())
        await f.authenticate()
        replacement = registry_text(workspace, principals={
            "caesar": {"token_sha256": [digest("new-token-" + "c" * 43)]},
            "iggy": {"token_sha256": [digest(TOKENS["caesar"])]}})
        write_registry(reg, replacement)
        with pytest.raises(ForwardError, match="unauthenticated"):
            await f.authenticate()
        assert f.principal_id == "caesar"
