"""GATED LIVE SMOKE — invokes the real installed Claude Code CLI through the bridge (costs money).

Skipped unless SPQR_LIVE_SMOKE=1. Uses the machine-local registry (SPQR_BRIDGE_CONFIG or
config/bridge.local.toml). Proves a real agent loop ran by asking Claude to read a fresh
random nonce from a file it could not otherwise know.
Run: scripts/live_smoke.sh
"""

import json
import os
import secrets
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
    fixture_dir = PROJECT / "tests" / "live_fixture"
    fixture_dir.mkdir(exist_ok=True)
    nonce = "SPQR-" + secrets.token_hex(8)
    (fixture_dir / "nonce.txt").write_text(nonce + "\n")
    task = ("Use your Read tool to read the file tests/live_fixture/nonce.txt in the current working "
            "directory. Reply with only the exact single line it contains, nothing else.")
    params = StdioServerParameters(command=sys.executable, args=["-m", "spqr_bridge.server", "--config", cfg])
    async with Client(params, read_timeout_seconds=60) as c:
        st = payload(await c.call_tool("start_task", {"agent_id": "claude", "task": task,
                                                      "workspace": str(PROJECT), "timeout_seconds": 240}))
        deadline = time.monotonic() + 260
        while time.monotonic() < deadline:
            g = payload(await c.call_tool("get_task", {"task_id": st["task_id"], "wait_seconds": 10}))
            if g["terminal"]:
                break
        print("\nLIVE RESULT:", json.dumps({k: g[k] for k in ("state", "result_text", "error", "return_code",
                                                              "duration_seconds", "session_id", "usage")}))
    assert g["state"] == "succeeded", g
    assert nonce in g["result_text"]
