"""v2 client slot: one agent-agnostic launcher command and a slot-issuing helper."""

import hashlib
import os
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from v2_helpers import registry_text, write_registry

REPO = Path(__file__).resolve().parents[1]
WRAPPER = REPO / "scripts" / "spqr-v2"


def issue(*args, cwd=None):
    return subprocess.run([sys.executable, "-m", "spqr_bridge.slot", *args], capture_output=True, text=True,
                          timeout=20, cwd=cwd)


def test_slot_new_writes_owner_only_token_and_matching_principal(tmp_path):
    r = issue("new", "caesar", "--dir", str(tmp_path))
    assert r.returncode == 0, r.stderr
    tok = tmp_path / "caesar.token"
    assert stat.S_IMODE(tok.stat().st_mode) == 0o600
    token = tok.read_bytes().strip()
    assert len(token) >= 40 and token.decode() not in r.stdout   # the token itself is never printed
    block = tomllib.loads(r.stdout)["principals"]["caesar"]
    assert block["token_sha256"] == [hashlib.sha256(token).hexdigest()]
    assert block["transports"] == ["stdio"] and block["enabled"] is True and block["allow_write"] is False


def test_slot_new_never_overwrites(tmp_path):
    (tmp_path / "caesar.token").write_text("old")
    r = issue("new", "caesar", "--dir", str(tmp_path))
    assert r.returncode != 0 and (tmp_path / "caesar.token").read_text() == "old"


@pytest.mark.parametrize("name", ["../x", "Caesar", "a b", "", "x" * 40])
def test_slot_new_rejects_bad_names(tmp_path, name):
    r = issue("new", name, "--dir", str(tmp_path))
    assert r.returncode != 0 and not any(tmp_path.iterdir())


@pytest.mark.parametrize("name", ["../x", "Caesar", ""])
def test_wrapper_rejects_bad_slot(tmp_path, name):
    r = subprocess.run([str(WRAPPER), name], capture_output=True, text=True, timeout=20,
                       env={**os.environ, "SPQR_CONFIG_DIR": str(tmp_path)})
    assert r.returncode != 0 and "slot" in r.stderr


def test_wrapper_reaches_launcher(tmp_path):
    r = subprocess.run([str(WRAPPER), "caesar"], capture_output=True, text=True, timeout=20,
                       env={**os.environ, "SPQR_CONFIG_DIR": str(tmp_path)})
    assert r.returncode == 2 and "cannot open token file" in r.stderr


async def test_any_mcp_client_can_take_the_slot(env_setup, tmp_path):
    """Issue a slot, paste its block into the registry, start through the one wrapper command."""
    cfg_text, ws, _state, tmp = env_setup
    conf = tmp_path / "conf"
    conf.mkdir()
    cfg = tmp / "bridge.toml"
    cfg.write_text(cfg_text())
    r = issue("new", "caesar", "--dir", str(conf))
    assert r.returncode == 0, r.stderr
    digest = tomllib.loads(r.stdout)["principals"]["caesar"]["token_sha256"][0]
    write_registry(conf / "registry.toml", registry_text(ws).replace(
        hashlib.sha256(b"caesar-token-" + b"a" * 43).hexdigest(), digest))
    params = StdioServerParameters(command=str(WRAPPER), args=["caesar"],
                                   env={**os.environ, "SPQR_CONFIG_DIR": str(conf), "SPQR_BRIDGE_CONFIG": str(cfg)})
    async with Client(params, read_timeout_seconds=30) as c:
        tools = await c.list_tools()
        assert "start_task" in {t.name for t in tools.tools}
