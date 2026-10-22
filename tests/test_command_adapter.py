"""Operator-configured runtimes keep caller text out of argv and never get write grants."""

import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from spqr_bridge.adapters import build_invocation, expected_flags, parse_output
from spqr_bridge.config import AgentConfig, BridgeConfig, ServerConfig, load_config
from spqr_bridge.server import build_server
from spqr_bridge.store import Store
from spqr_bridge.supervisor import Supervisor


def command(**kw):
    return AgentConfig(adapter="command", binary=Path(sys.executable), enabled=True, **kw)


def test_fixed_argv_and_literal_stdin():
    task = '--flag @/etc/passwd $(touch stolen); echo pwned'
    agent = command(argv=["--fixed", "{workspace}", "$HOME"])
    inv = build_invocation(agent, task, "some context", Path("/workspace"), "read_only")
    assert inv.argv == [sys.executable, "--fixed", "{workspace}", "$HOME"]
    assert inv.stdin.decode() == task + "\n\n--- Context supplied by the delegating agent ---\nsome context"
    assert expected_flags(agent) == []


@pytest.mark.parametrize("kwargs", [
    {"allow_write": True}, {"argv": ["bad\x00arg"]}, {"argv": ["a"] * 65},
    {"argv": ["a" * 4097]}, {"argv": ["a" * 4096] * 5},
    {"argv": ["bad\narg"]}, {"argv": [4]},
])
def test_command_configuration_bounds(kwargs):
    with pytest.raises(ValidationError):
        command(**kwargs)


def test_native_adapters_reject_generic_argv():
    with pytest.raises(ValidationError):
        AgentConfig(adapter="claude", binary=Path("/opt/claude"), argv=["--unsafe"])


def test_command_writes_refused_at_invocation():
    with pytest.raises(ValueError):
        build_invocation(command(), "work", None, Path("/workspace"), "write")


def test_plain_text_results_are_conservative():
    assert parse_output("command", b" finished work\n", 0).text == "finished work"
    assert parse_output("command", b"finished work", 0).ok
    assert not parse_output("command", b" \n", 0).ok
    failed = parse_output("command", b"partial output", 7)
    assert not failed.ok and "exit 7" in failed.error


async def test_configured_runtime_executes_through_supervisor(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    runtime = tmp_path / "runtime.py"
    runtime.write_text("import json,sys,os\nprint(json.dumps({'args':sys.argv[1:],'task':sys.stdin.read(),'cwd':os.getcwd()}))\n")
    config = tmp_path / "bridge.toml"
    config.write_text(f'''[server]
state_dir = {json.dumps(str(tmp_path / 'state'))}
allowed_roots = [{json.dumps(str(ws))}]
[agents.future]
adapter = "command"
binary = {json.dumps(sys.executable)}
enabled = true
argv = ["-I", {json.dumps(str(runtime))}, "literal"]
''')
    cfg = load_config(config)
    sup = Supervisor(cfg, Store(cfg.server.state_dir))
    try:
        task = "$(touch stolen) --system unrestricted"
        started = await sup.start("future", task, None, str(ws), "read_only")
        job = await sup.wait(started["task_id"], 10)
        assert job["state"] == "succeeded", job
        assert json.loads(job["result_text"]) == {"args": ["literal"], "task": task, "cwd": str(ws)}
        assert not (ws / "stolen").exists()
    finally:
        await sup.shutdown()


async def test_doctor_does_not_execute_unknown_program(tmp_path, monkeypatch):
    ws = tmp_path / "workspace"
    ws.mkdir()
    cfg = BridgeConfig(server=ServerConfig(state_dir=tmp_path / "state", allowed_roots=[ws]),
                       agents={"future": command()})
    async def forbidden(*args, **kw):
        raise AssertionError("doctor must not run an arbitrary configured runtime")
    monkeypatch.setattr("spqr_bridge.server._probe", forbidden)
    server = build_server(cfg)
    async with server.settings.lifespan(server):
        result = await server.call_tool("doctor", {})
        report = result.structured_content
        assert report["model_requests_made"] is False
        assert report["agents"]["future"]["probe_status"] == "not_run_for_command_adapter"
        listing = (await server.call_tool("list_agents", {})).structured_content
        assert listing["agents"][0]["access_modes"] == ["read_only"]
        assert any("confinement" in s for s in listing["agents"][0]["limitations"])


async def test_v2_command_listing_exposes_permission_limitations(tmp_path):
    """v2 clients have no doctor/v1 listing; caveats must reach their discovery view."""
    from spqr_bridge.gate import PrincipalGate
    from spqr_bridge.registry import authenticate, load_registry
    from v2_helpers import TOKENS, registry_text, write_registry

    ws = tmp_path / "workspace"
    ws.mkdir()
    cfg = BridgeConfig(server=ServerConfig(state_dir=tmp_path / "state", allowed_roots=[ws]),
                       agents={"future": command()})
    path = write_registry(tmp_path / "registry.toml", registry_text(
        ws, principals={"caesar": {"agents": ["future"]}}))
    auth = authenticate(load_registry(path), TOKENS["caesar"].encode(), "stdio")
    sup = Supervisor(cfg, Store(cfg.server.state_dir))
    try:
        agent = PrincipalGate(sup, path, auth).list_agents()["agents"][0]
        assert agent["access_modes"] == ["read_only"]
        assert any("confinement" in item for item in agent.get("limitations", []))
    finally:
        await sup.shutdown()
