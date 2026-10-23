"""GATED LIVE SMOKE — invokes the real installed Claude Code CLI through the bridge (costs money).

Skipped unless SPQR_LIVE_SMOKE=1. Uses the machine-local registry (SPQR_BRIDGE_CONFIG or
config/bridge.local.toml). Proves a real agent loop ran by asking Claude to read a fresh
random nonce from a file it could not otherwise know.
Run: scripts/live_smoke.sh
"""

import json
import os
import re
import secrets
import shutil
import tempfile
import tomllib
import sys
import time
from pathlib import Path

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

PROJECT = Path(__file__).resolve().parent.parent
pytestmark = [pytest.mark.live,
              pytest.mark.skipif(os.environ.get("SPQR_LIVE_SMOKE") != "1", reason="live smoke not requested")]


def payload(res):
    sc = res.structured_content
    if sc is not None:
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    return json.loads(res.content[0].text)


async def test_live_claude_reads_nonce_via_mcp():
    cfg = os.environ.get("SPQR_BRIDGE_CONFIG", str(PROJECT / "config" / "bridge.local.toml"))
    cfg_text = Path(cfg).read_text()
    # The task must run inside an allowed root, and the bridge daemon may hold the configured
    # state_dir lock, so run this throwaway server against a temp workspace and temp state dir.
    root = Path(tomllib.loads(cfg_text)["server"]["allowed_roots"][0])
    tmp = Path(tempfile.mkdtemp(prefix="spqr-live-smoke-"))
    fixture_dir = Path(tempfile.mkdtemp(prefix="live-smoke-", dir=root))
    smoke_cfg = tmp / "bridge.toml"
    smoke_cfg.write_text(re.sub(r'(?m)^state_dir\s*=.*$', f'state_dir = "{tmp / "state"}"', cfg_text))
    cfg = str(smoke_cfg)
    nonce = "SPQR-" + secrets.token_hex(8)
    (fixture_dir / "nonce.txt").write_text(nonce + "\n")
    task = ("Use your Read tool to read the file nonce.txt in the current working "
            "directory. Reply with only the exact single line it contains, nothing else.")
    try:
        params = StdioServerParameters(command=sys.executable, args=["-m", "spqr_bridge.server", "--config", cfg])
        async with Client(params, read_timeout_seconds=60) as c:
            st = payload(await c.call_tool("start_task", {"agent_id": "claude", "task": task,
                                                          "workspace": str(fixture_dir), "timeout_seconds": 240}))
            deadline = time.monotonic() + 260
            while time.monotonic() < deadline:
                g = payload(await c.call_tool("get_task", {"task_id": st["task_id"], "wait_seconds": 10}))
                if g["terminal"]:
                    break
            print("\nLIVE RESULT:", json.dumps({k: g[k] for k in ("state", "result_text", "error", "return_code",
                                                                  "duration_seconds", "session_id", "usage")}))
        assert g["state"] == "succeeded", g
        assert nonce in g["result_text"]
    finally:
        shutil.rmtree(fixture_dir, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)
