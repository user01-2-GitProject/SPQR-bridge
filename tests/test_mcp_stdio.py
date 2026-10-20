"""Slice 5: real MCP stdio server process; initialise, discover and call all five tools.

Agents are the FAKE fixture executables from conftest — no live models are invoked.
"""

import json
import sys

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from spqr_bridge.store import StateLockError, Store

TOOLS = {"list_agents", "start_task", "get_task", "cancel_task", "doctor"}


def payload(res):
    if res.structured_content is not None:
        sc = res.structured_content
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    return json.loads(res.content[0].text)


async def test_mcp_stdio_all_tools(env_setup):
    cfg_text, ws, state, tmp = env_setup
    cfg = tmp / "bridge.toml"
    cfg.write_text(cfg_text())
    params = StdioServerParameters(command=sys.executable, args=["-m", "spqr_bridge.server", "--config", str(cfg)])
    async with Client(params, read_timeout_seconds=30) as c:
        tools = await c.list_tools()
        assert {t.name for t in tools.tools} == TOOLS
        start_schema = next(t for t in tools.tools if t.name == "start_task").input_schema
        props = set(start_schema["properties"])
        assert props == {"agent_id", "task", "context", "workspace", "access", "timeout_seconds", "idempotency_key"}

        # only one live server may own a state dir
        with pytest.raises(StateLockError):
            Store(state)

        agents = payload(await c.call_tool("list_agents", {}))
        by_id = {a["id"]: a for a in agents["agents"]}
        assert by_id["claude"]["readiness"]["binary_present"] is True
        assert by_id["ghost"]["readiness"]["binary_present"] is False
        assert by_id["omp"]["enabled"] is False

        doc = payload(await c.call_tool("doctor", {}))
        assert doc["model_requests_made"] is False
        assert doc["agents"]["claude"]["binary_found"] is True
        assert "not checked" in doc["agents"]["claude"]["provider_ready"]
        # help-text omission is informational only (claude hides --max-turns yet accepts it)
        claude_doc = doc["agents"]["claude"]
        assert "required_flags_missing" not in claude_doc
        assert "--max-budget-usd" in claude_doc["flags_not_advertised"]  # fixture config sets a budget
        assert "not proof" in claude_doc["flags_note"]

        st = payload(await c.call_tool("start_task", {"agent_id": "claude", "task": "hi", "workspace": str(ws)}))
        tid = st["task_id"]
        for _ in range(5):
            g = payload(await c.call_tool("get_task", {"task_id": tid, "wait_seconds": 5}))
            if g["terminal"]:
                break
        assert g["state"] == "succeeded" and json.loads(g["result_text"])["fixture"] == "fake_agent"

        h = payload(await c.call_tool("start_task", {"agent_id": "claude", "task": f"FAKE_MODE=hang {tmp / 'p'}",
                                                     "workspace": str(ws)}))
        cx = payload(await c.call_tool("cancel_task", {"task_id": h["task_id"]}))
        assert cx["state"] == "cancelled"

        bad = await c.call_tool("start_task", {"agent_id": "nope", "task": "x", "workspace": str(ws)})
        assert bad.is_error and "unknown_agent" in bad.content[0].text
        bad_wait = await c.call_tool("get_task", {"task_id": tid, "wait_seconds": 999})
        assert bad_wait.is_error
