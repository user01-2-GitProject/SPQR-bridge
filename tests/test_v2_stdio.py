"""v2 Phase 1: real stdio server started through the credential launcher (fake agents only)."""

import json
import subprocess
import sys

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from v2_helpers import TOKENS, registry_text, write_registry

V2_TOOLS = {"list_agents", "start_task", "get_task", "cancel_task"}


def payload(res):
    if res.structured_content is not None:
        sc = res.structured_content
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    return json.loads(res.content[0].text)


@pytest.fixture
def files(env_setup):
    cfg_text, ws, state, tmp = env_setup
    cfg = tmp / "bridge.toml"
    cfg.write_text(cfg_text())
    reg = write_registry(tmp / "registry.toml", registry_text(ws))
    tok = tmp / "caesar.token"
    tok.write_text(TOKENS["caesar"])
    tok.chmod(0o600)
    return cfg, reg, tok, ws, state


def launch_args(cfg, reg, tok):
    return ["-m", "spqr_bridge.launch", "--token-file", str(tok), "--", "--config", str(cfg), "--registry", str(reg)]


async def test_v2_stdio_authenticated_session(files):
    cfg, reg, tok, ws, _ = files
    params = StdioServerParameters(command=sys.executable, args=launch_args(cfg, reg, tok))
    async with Client(params, read_timeout_seconds=30) as c:
        tools = await c.list_tools()
        assert {t.name for t in tools.tools} == V2_TOOLS
        props = set(next(t for t in tools.tools if t.name == "start_task").input_schema["properties"])
        assert "grant_id" in props and not props & {"principal", "principal_id", "token"}
        st = payload(await c.call_tool("start_task", {"agent_id": "claude", "task": "hi", "workspace": str(ws)}))
        for _ in range(5):
            g = payload(await c.call_tool("get_task", {"task_id": st["task_id"], "wait_seconds": 5}))
            if g["terminal"]:
                break
        assert g["state"] == "succeeded" and g["principal_id"] == "caesar"
        bad = await c.call_tool("start_task", {"agent_id": "claude", "task": "x", "workspace": str(ws),
                                               "access": "write"})
        assert bad.is_error and "access_denied" in bad.content[0].text


def run_v2(args, **kw):
    return subprocess.run([sys.executable, *args], input=b"", capture_output=True, timeout=20, **kw)


def test_wrong_token_no_service(files):
    cfg, reg, tok, _, state = files
    tok.write_text("wrong-" + "z" * 43)
    r = run_v2(launch_args(cfg, reg, tok))
    assert r.returncode != 0 and b"authentication failed" in r.stderr
    assert b"wrong-" not in r.stderr and r.stdout == b""
    log = (state / "auth-failures.log").read_text()
    assert "bad_credential" in log and "wrong-" not in log
    assert not (state / "jobs.sqlite3").exists()  # store never opened for an unauthenticated caller


def test_token_file_must_be_owner_only(files):
    cfg, reg, tok, _, _ = files
    tok.chmod(0o640)
    r = run_v2(launch_args(cfg, reg, tok))
    assert r.returncode != 0 and r.stdout == b""


def test_no_anonymous_mode(files):
    cfg, reg, tok, _, _ = files
    r = run_v2(["-m", "spqr_bridge.v2", "--config", str(cfg), "--registry", str(reg)])
    assert r.returncode != 0 and r.stdout == b""


def test_principal_flag_not_accepted(files):
    cfg, reg, tok, _, _ = files
    r = run_v2(launch_args(cfg, reg, tok) + ["--principal", "iggy"])
    assert r.returncode != 0 and r.stdout == b""


def test_regular_file_as_credential_fd_rejected(files):
    cfg, reg, tok, _, _ = files
    with open(tok, "rb") as f:
        r = run_v2(["-m", "spqr_bridge.v2", "--config", str(cfg), "--registry", str(reg),
                    "--credential-fd", str(f.fileno())], pass_fds=(f.fileno(),))
    assert r.returncode != 0 and b"authentication failed" in r.stderr


def test_auth_failure_log_is_bounded(files):
    cfg, reg, tok, _, state = files
    state.mkdir(mode=0o700, exist_ok=True)
    log = state / "auth-failures.log"
    log.write_text("x" * 1_100_000)
    tok.write_text("wrong-" + "z" * 43)
    run_v2(launch_args(cfg, reg, tok))
    assert log.stat().st_size <= 1_100_000


def test_registry_inside_allowed_root_refused(files, tmp_path):
    cfg, reg, tok, ws, _ = files
    inside = write_registry(ws / "registry.toml", reg.read_text())
    r = run_v2(["-m", "spqr_bridge.launch", "--token-file", str(tok), "--", "--config", str(cfg),
                "--registry", str(inside)])
    assert r.returncode != 0 and r.stdout == b"" and b"registry" in r.stderr
