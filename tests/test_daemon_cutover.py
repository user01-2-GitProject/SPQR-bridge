"""Daemon cutover: the one slot command finds the shared owner, stale sockets recover, unit installs."""
import asyncio
import json
import os
import socket
import subprocess
from pathlib import Path

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from spqr_bridge.config import load_config
from spqr_bridge.daemon import SharedOwner
from v2_helpers import TOKENS, registry_text, write_registry

REPO = Path(__file__).resolve().parents[1]
WRAPPER = REPO / "scripts" / "spqr-v2"
INSTALLER = REPO / "scripts" / "spqr-daemon"


def _setup(env_setup):
    cfg_text, ws, _, tmp = env_setup
    cfg = tmp / "bridge.toml"
    cfg.write_text(cfg_text())
    conf = tmp / "conf"
    conf.mkdir(mode=0o700)
    reg = write_registry(conf / "registry.toml", registry_text(ws))
    for pid, token in TOKENS.items():
        (conf / f"{pid}.token").write_text(token)
        (conf / f"{pid}.token").chmod(0o600)
    return cfg, conf, reg, ws


def _payload(result):
    s = result.structured_content
    if s is not None:
        return s.get("result", s) if set(s) == {"result"} else s
    return json.loads(result.content[0].text)


def _stale_socket(path: Path) -> None:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(path))
    s.close()  # the path stays behind, as after a daemon crash


async def test_slot_command_uses_shared_owner_when_socket_exists(env_setup):
    cfg, conf, reg, ws = _setup(env_setup)
    env = {**os.environ, "SPQR_CONFIG_DIR": str(conf), "SPQR_BRIDGE_CONFIG": str(cfg)}
    async with SharedOwner(load_config(cfg), reg, conf / "bridge.sock"):
        # Two slots at once through the same command: only possible through the shared owner.
        a = Client(StdioServerParameters(command=str(WRAPPER), args=["caesar"], env=env), read_timeout_seconds=20)
        b = Client(StdioServerParameters(command=str(WRAPPER), args=["iggy"], env=env), read_timeout_seconds=20)
        async with a as caesar, b as iggy:
            listed = _payload(await caesar.call_tool("list_agents", {}))
            assert "claude" in {x["id"] for x in listed["agents"]}
            assert not (await iggy.call_tool("list_agents", {})).is_error


async def test_slot_command_still_standalone_without_socket(env_setup):
    cfg, conf, _, _ = _setup(env_setup)
    env = {**os.environ, "SPQR_CONFIG_DIR": str(conf), "SPQR_BRIDGE_CONFIG": str(cfg)}
    async with Client(StdioServerParameters(command=str(WRAPPER), args=["caesar"], env=env),
                      read_timeout_seconds=20) as c:
        assert "start_task" in {t.name for t in (await c.list_tools()).tools}


async def test_stale_socket_is_recovered_once_state_lock_is_held(env_setup):
    cfg, conf, reg, _ = _setup(env_setup)
    sock = conf / "bridge.sock"
    _stale_socket(sock)
    async with SharedOwner(load_config(cfg), reg, sock) as owner:
        reader, writer = await asyncio.open_unix_connection(str(sock))
        writer.write(json.dumps({"token": TOKENS["caesar"], "tool": "hello", "arguments": {}}).encode() + b"\n")
        await writer.drain()
        assert json.loads(await reader.readline())["ok"]
        writer.close()
        await writer.wait_closed()
        assert owner.server.is_serving()


@pytest.mark.parametrize("kind", ["file", "symlink"])
async def test_non_socket_at_socket_path_is_never_removed(env_setup, kind):
    cfg, conf, reg, _ = _setup(env_setup)
    sock = conf / "bridge.sock"
    if kind == "file":
        sock.write_text("not a socket")
    else:
        sock.symlink_to(conf / "elsewhere")
    with pytest.raises(RuntimeError):
        async with SharedOwner(load_config(cfg), reg, sock):
            pass
    assert sock.is_symlink() or sock.read_text() == "not a socket"


async def test_live_owner_socket_survives_competing_start(env_setup):
    cfg, conf, reg, _ = _setup(env_setup)
    sock = conf / "bridge.sock"
    async with SharedOwner(load_config(cfg), reg, sock):
        inode = sock.stat().st_ino
        with pytest.raises((RuntimeError, OSError)):
            async with SharedOwner(load_config(cfg), reg, sock):
                pass
        assert sock.stat().st_ino == inode


def test_installer_writes_user_unit_once(tmp_path):
    xdg = tmp_path / "xdg"
    env = {**os.environ, "XDG_CONFIG_HOME": str(xdg), "SPQR_CONFIG_DIR": str(tmp_path / "spqr")}
    r = subprocess.run([str(INSTALLER), "install"], capture_output=True, text=True, timeout=20, env=env)
    assert r.returncode == 0, r.stderr
    unit = (xdg / "systemd" / "user" / "spqr-bridge.service").read_text()
    # Paths are quoted: the real repo path contains a space and an apostrophe.
    assert f'"{REPO}/.venv/bin/python" -m spqr_bridge.daemon' in unit
    assert "--service-unit spqr-bridge.service" in unit
    assert f'--socket "{tmp_path}/spqr/bridge.sock"' in unit
    assert "KillMode=control-group" in unit and "SendSIGKILL=yes" in unit
    assert "systemctl --user" in r.stdout
    again = subprocess.run([str(INSTALLER), "install"], capture_output=True, text=True, timeout=20, env=env)
    assert again.returncode != 0 and "exists" in again.stderr
    assert (xdg / "systemd" / "user" / "spqr-bridge.service").read_text() == unit


def test_unit_executable_path_has_no_special_characters(tmp_path):
    # systemd refuses an ExecStart program path with spaces or quotes even when quoted, and the
    # real repo path has both; a plain launcher runs the venv python given as its argument.
    env = {**os.environ, "XDG_CONFIG_HOME": str(tmp_path / "xdg"), "SPQR_CONFIG_DIR": str(tmp_path / "spqr")}
    assert subprocess.run([str(INSTALLER), "install"], capture_output=True, timeout=20, env=env).returncode == 0
    unit = (tmp_path / "xdg" / "systemd" / "user" / "spqr-bridge.service").read_text()
    exec_line = next(line for line in unit.splitlines() if line.startswith("ExecStart="))
    assert exec_line.startswith('ExecStart=/usr/bin/env "')
