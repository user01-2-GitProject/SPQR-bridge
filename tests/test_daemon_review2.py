"""Independent review round 1 (daemon cutover) regressions, written before the fixes."""
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from spqr_bridge.config import load_config
from spqr_bridge.daemon import SharedOwner
from v2_helpers import TOKENS, registry_text, write_registry

REPO = Path(__file__).resolve().parents[1]
TOKEN = "test-token-" + "a" * 43


def _owner_args(env_setup, state_name="state"):
    cfg_text, ws, _, tmp = env_setup
    cfg = tmp / f"{state_name}.toml"
    cfg.write_text(cfg_text().replace(f'state_dir = "{tmp / "state"}"', f'state_dir = "{tmp / state_name}"'))
    ctl = tmp / "ctl"
    ctl.mkdir(mode=0o700, exist_ok=True)
    return load_config(cfg), write_registry(tmp / "registry.toml", registry_text(ws)), ctl / "bridge.sock"


async def test_idle_client_does_not_delay_shutdown(env_setup):
    cfg, reg, sock = _owner_args(env_setup)
    owner = SharedOwner(cfg, reg, sock)
    await owner.__aenter__()
    _, writer = await asyncio.open_unix_connection(str(sock))  # connects, never sends
    await asyncio.sleep(0.1)
    started = time.monotonic()
    await owner.__aexit__(None, None, None)
    assert time.monotonic() - started < 3
    writer.close()


async def test_second_owner_with_other_state_cannot_steal_live_socket(env_setup):
    cfg, reg, sock = _owner_args(env_setup)
    other_cfg, _, _ = _owner_args(env_setup, "state2")
    async with SharedOwner(cfg, reg, sock):
        inode = sock.stat().st_ino
        with pytest.raises(RuntimeError):
            async with SharedOwner(other_cfg, reg, sock):
                pass
        assert sock.stat().st_ino == inode


@pytest.fixture
async def flaky_daemon(tmp_path):
    """Fake owner: once a tool result was served, the next check is rate limited."""
    sock = tmp_path / "bridge.sock"
    served = {"tool": False}

    async def handle(reader, writer):
        req = json.loads(await reader.readline())
        if req["tool"] == "hello":
            resp = {"ok": True, "result": {"principal_id": "caesar"}}
        elif req["tool"] == "check" and served["tool"]:
            served["tool"] = False
            resp = {"ok": False, "error": {"code": "rate_limited", "message": "slow down"}}
        elif req["tool"] == "check":
            resp = {"ok": True, "result": {"principal_id": "caesar"}}
        else:
            served["tool"] = True
            resp = {"ok": True, "result": {"task_id": "t-admitted"}}
        writer.write(json.dumps(resp).encode() + b"\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_unix_server(handle, path=str(sock))
    sock.chmod(0o600)
    yield sock
    server.close()


async def test_transient_postcheck_failure_keeps_produced_result(flaky_daemon, tmp_path):
    token = tmp_path / "t.token"
    token.write_text(TOKEN)
    token.chmod(0o600)
    params = StdioServerParameters(command=sys.executable, args=[
        "-m", "spqr_bridge.forward", "--socket", str(flaky_daemon), "--token-file", str(token)])
    async with Client(params, read_timeout_seconds=20) as c:
        r = await c.call_tool("start_task", {"agent_id": "x", "task": "t", "workspace": "/w"})
        assert not r.is_error and "t-admitted" in r.content[0].text


def _install(tmp_path, repo_copy=None):
    env = {**os.environ, "XDG_CONFIG_HOME": str(tmp_path / "xdg"), "SPQR_CONFIG_DIR": str(tmp_path / "spqr")}
    return subprocess.run([str(REPO / "scripts" / "spqr-daemon"), "install"], capture_output=True,
                          text=True, timeout=20, env=env)


def test_unit_does_not_restart_on_startup_refusal(tmp_path):
    assert _install(tmp_path).returncode == 0
    unit = (tmp_path / "xdg" / "systemd" / "user" / "spqr-bridge.service").read_text()
    assert "RestartPreventExitStatus=2" in unit
    assert "Restart=no" not in (REPO / "deployment" / "spqr-bridge.service.example").read_text()


def test_installer_never_writes_through_dangling_symlink(tmp_path):
    units = tmp_path / "xdg" / "systemd" / "user"
    units.mkdir(parents=True)
    (units / "spqr-bridge.service").symlink_to(tmp_path / "target")
    assert _install(tmp_path).returncode != 0
    assert not (tmp_path / "target").exists()


def test_installer_rejects_dollar_in_paths(tmp_path):
    env = {**os.environ, "XDG_CONFIG_HOME": str(tmp_path / "xdg"), "SPQR_CONFIG_DIR": str(tmp_path / "a$b")}
    r = subprocess.run([str(REPO / "scripts" / "spqr-daemon"), "install"], capture_output=True,
                       text=True, timeout=20, env=env)
    assert r.returncode != 0


def test_standalone_fallback_is_announced(tmp_path):
    r = subprocess.run([str(REPO / "scripts" / "spqr-v2"), "caesar"], capture_output=True, text=True,
                       timeout=20, env={**os.environ, "SPQR_CONFIG_DIR": str(tmp_path)})
    assert "standalone" in r.stderr
