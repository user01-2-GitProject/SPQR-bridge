"""Slice 2: native argv generation and backend output parsing (no processes)."""

import json
from pathlib import Path

import pytest

from spqr_bridge.adapters import ParseResult, build_invocation, parse_output
from spqr_bridge.config import AgentConfig
from spqr_bridge.envpolicy import scrub

WS = Path("/w/s")
INJECT = "--dangerously-skip-permissions @/etc/passwd $(rm -rf ~) ; echo pwned"


def agent(adapter, **kw):
    return AgentConfig(adapter=adapter, binary=Path(f"/opt/{adapter}"), enabled=True, **kw)


def test_claude_read_only_argv():
    inv = build_invocation(agent("claude", max_budget_usd=0.2, max_turns=3), "do x", None, WS, "read_only")
    a = inv.argv
    assert a[0] == "/opt/claude"
    assert a[a.index("--output-format") + 1] == "json"
    assert "-p" in a and "--restricted" in a and "--strict-mcp-config" in a
    assert a[a.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    assert a[a.index("--tools") + 1] == "Read,Grep,Glob"
    assert a[a.index("--allowedTools") + 1] == "Read,Grep,Glob"
    assert a[a.index("--permission-mode") + 1] == "dontAsk"
    assert a[a.index("--permission-prompts") + 1] == "none"
    assert a[a.index("--max-turns") + 1] == "3"
    assert a[a.index("--max-budget-usd") + 1] == "0.2"
    assert "--no-session-persistence" in a
    assert not any("dangerously" in x or "bypass" in x.lower() for x in a)
    assert "Bash" not in ",".join(a)


def test_claude_write_argv_scoped_tools_no_bash():
    a = build_invocation(agent("claude", allow_write=True), "x", None, WS, "write").argv
    assert a[a.index("--tools") + 1] == "Read,Grep,Glob,Edit,Write"
    assert a[a.index("--permission-mode") + 1] == "acceptEdits"
    assert "Bash" not in ",".join(a)


def test_codex_argv_sandbox_modes():
    ro = build_invocation(agent("codex"), "x", None, WS, "read_only").argv
    assert ro[:2] == ["/opt/codex", "exec"]
    assert ro[ro.index("--sandbox") + 1] == "read-only"
    for f in ("--json", "--ephemeral", "--ignore-user-config", "--skip-git-repo-check"):
        assert f in ro
    assert ro[ro.index("-C") + 1] == str(WS)
    assert ro[-1] == "-"  # prompt from stdin
    rw = build_invocation(agent("codex", allow_write=True), "x", None, WS, "write").argv
    assert rw[rw.index("--sandbox") + 1] == "workspace-write"
    assert not any("danger" in x for x in ro + rw)


def test_omp_argv_read_only_only():
    inv = build_invocation(agent("omp"), "x", None, WS, "read_only")
    assert inv.argv[0] == "/opt/omp"
    with pytest.raises(ValueError):
        build_invocation(agent("omp"), "x", None, WS, "write")


@pytest.mark.parametrize("adapter", ["claude", "codex", "omp"])
def test_prompt_injection_stays_in_stdin(adapter):
    inv = build_invocation(agent(adapter), INJECT, "ctx --flag", WS, "read_only")
    assert all(INJECT not in x and "pwned" not in x for x in inv.argv)
    assert INJECT in inv.stdin.decode()
    assert "ctx --flag" in inv.stdin.decode()


def test_write_refused_when_policy_disallows():
    with pytest.raises(ValueError):
        build_invocation(agent("claude"), "x", None, WS, "write")


# --- parsing ---------------------------------------------------------------


def test_parse_claude_success():
    out = b'{"type":"result","subtype":"success","is_error":false,"result":"hello","session_id":"s1","total_cost_usd":0.01,"usage":{"input_tokens":3}}'
    r = parse_output("claude", out, 0)
    assert r == ParseResult(ok=True, text="hello", error=None, session_id="s1", usage={"input_tokens": 3, "total_cost_usd": 0.01})


def test_parse_claude_semantic_error():
    out = b'{"type":"result","subtype":"error_max_turns","is_error":true,"result":"","session_id":"s1"}'
    r = parse_output("claude", out, 0)
    assert not r.ok and "error_max_turns" in r.error


def test_parse_claude_error_reports_reason_not_success():
    out = b'{"type":"result","subtype":"success","is_error":true,"result":"Failed to authenticate: OAuth session expired","session_id":"s1"}'
    r = parse_output("claude", out, 1)
    assert not r.ok and r.error.endswith("backend_error: Failed to authenticate: OAuth session expired")


def test_parse_omp_event_stream_returns_final_answer():
    lines = [
        {"type": "session", "version": 3, "id": "sess-1", "cwd": "/w"},
        {"type": "message_end", "message": {"role": "user", "content": [{"type": "text", "text": "say pong"}]}},
        {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "pong"}},
        {"type": "agent_end", "messages": [
            {"role": "user", "content": [{"type": "text", "text": "say pong"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "pong", "textSignature": "x"}]}]},
    ]
    out = "\n".join(json.dumps(x) for x in lines).encode()
    r = parse_output("omp", out, 0)
    assert r.ok and r.text == "pong" and r.session_id == "sess-1"


def test_parse_omp_agent_end_without_answer_fails():
    out = json.dumps({"type": "agent_end", "messages": [{"role": "user", "content": []}]}).encode()
    r = parse_output("omp", out, 0)
    assert not r.ok and "agent_end" in r.error


@pytest.mark.parametrize("out", [b"", b"not json", b"[1,2]"])
def test_parse_claude_malformed(out):
    r = parse_output("claude", out, 0)
    assert not r.ok and r.error.startswith("malformed_output")


def test_parse_codex_jsonl():
    out = b"\n".join([
        b'{"type":"thread.started","thread_id":"t9"}',
        b"garbage line",
        b'{"type":"item.completed","item":{"type":"agent_message","text":"first"}}',
        b'{"type":"item.completed","item":{"type":"agent_message","text":"final answer"}}',
        b'{"type":"turn.completed","usage":{"input_tokens":5}}',
    ])
    r = parse_output("codex", out, 0)
    assert r.ok and r.text == "final answer" and r.session_id == "t9" and r.usage == {"input_tokens": 5}


def test_parse_codex_failure_event():
    out = b'{"type":"thread.started","thread_id":"t"}\n{"type":"turn.failed","error":{"message":"quota"}}'
    r = parse_output("codex", out, 0)
    assert not r.ok and "quota" in r.error


def test_parse_nonzero_exit_is_failure():
    out = b'{"type":"result","subtype":"success","is_error":false,"result":"hi"}'
    r = parse_output("claude", out, 2)
    assert not r.ok and "exit" in r.error


def test_scrub_secrets():
    s = scrub("auth failed for sk-ant-abcdefghijklmnop with Bearer abcdefghijk token=supersecretvalue")
    assert "abcdefghijklmnop" not in s and "supersecretvalue" not in s and "[REDACTED]" in s
