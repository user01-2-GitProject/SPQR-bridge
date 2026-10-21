"""Backend adapters: fixed native argv per CLI plus output parsing.

Callers never supply argv, env, model or provider. The prompt always travels on stdin,
so task text beginning with "-" or "@" is never interpreted as a flag or file reference.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from .config import AgentConfig
from .envpolicy import scrub

CLAUDE_READ_TOOLS = "Read,Grep,Glob"
CLAUDE_WRITE_TOOLS = "Read,Grep,Glob,Edit,Write"
EMPTY_MCP = '{"mcpServers":{}}'

USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens",
              "cached_input_tokens", "reasoning_output_tokens", "total_cost_usd", "num_turns", "duration_ms",
              "duration_api_ms")
CLAUDE_TOP_METRICS = ("total_cost_usd", "num_turns", "duration_ms", "duration_api_ms")
MAX_DENIALS = 20
DENIAL_EXCERPT_CHARS = 200

# Flags each adapter relies on. `doctor` reports which ones the installed help text does not
# advertise; that is informational only (Claude hides --max-turns from --help yet accepts it).
REQUIRED_FLAGS = {
    "claude": ["--restricted", "--strict-mcp-config", "--mcp-config", "--tools", "--allowedTools",
               "--permission-mode", "--permission-prompts", "--no-session-persistence",
               "--max-turns", "--output-format"],
    "codex": ["--json", "--ephemeral", "--ignore-user-config", "--sandbox", "--skip-git-repo-check"],
    "omp": ["--mode"],
}
def expected_flags(agent: AgentConfig) -> list[str]:
    flags = list(REQUIRED_FLAGS[agent.adapter])
    if agent.adapter == "claude" and agent.max_budget_usd is not None:
        flags.append("--max-budget-usd")
    return flags


HELP_ARGV = {"claude": ["--help"], "codex": ["exec", "--help"], "omp": ["--help"]}


@dataclass(frozen=True)
class Invocation:
    argv: list[str]
    stdin: bytes


@dataclass(frozen=True)
class ParseResult:
    ok: bool
    text: str
    error: str | None
    session_id: str | None = None
    usage: dict = field(default_factory=dict)
    denials: dict | None = None


def compose_prompt(task: str, context: str | None) -> bytes:
    prompt = task
    if context:
        prompt += "\n\n--- Context supplied by the delegating agent ---\n" + context
    return prompt.encode("utf-8")


def build_invocation(agent: AgentConfig, task: str, context: str | None, workspace: Path,
                     access: str) -> Invocation:
    if access not in ("read_only", "write"):
        raise ValueError("access must be read_only or write")
    if access == "write" and not agent.allow_write:
        raise ValueError("write access is not permitted for this agent by server policy")
    b = str(agent.binary)
    stdin = compose_prompt(task, context)
    if agent.adapter == "claude":
        tools = CLAUDE_WRITE_TOOLS if access == "write" else CLAUDE_READ_TOOLS
        argv = [b, "-p", "--output-format", "json", "--restricted",
                "--strict-mcp-config", "--mcp-config", EMPTY_MCP,
                "--tools", tools, "--allowedTools", tools,
                "--permission-mode", "acceptEdits" if access == "write" else "dontAsk",
                "--permission-prompts", "none",
                "--no-session-persistence", "--max-turns", str(agent.max_turns)]
        if agent.max_budget_usd is not None:
            argv += ["--max-budget-usd", f"{agent.max_budget_usd:g}"]
    elif agent.adapter == "codex":
        argv = [b, "exec", "--json", "--ephemeral", "--ignore-user-config", "--skip-git-repo-check",
                "--sandbox", "workspace-write" if access == "write" else "read-only",
                "-C", str(workspace), "-"]
    elif agent.adapter == "omp":
        if access == "write":
            raise ValueError("omp has no enforced file restriction; write is unsupported")
        argv = [b, "-p", "--mode", "json"]
    else:  # pragma: no cover - config validation prevents this
        raise ValueError("unknown adapter")
    return Invocation(argv=argv, stdin=stdin)


def _json_lines(out: bytes) -> list[dict]:
    objs = []
    for line in out.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if isinstance(o, dict):
            objs.append(o)
    return objs


def normalize_usage(raw: object, extra: dict | None = None) -> dict:
    """Whitelisted finite numeric metrics only, so the stored JSON is always small and valid."""
    src = dict(raw) if isinstance(raw, dict) else {}
    kept = {k: v for k, v in {**src, **(extra or {})}.items()
            if k in USAGE_KEYS and isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)}
    omitted = sum(1 for k in src if k not in kept)
    if omitted:
        kept["omitted_fields"] = omitted
    return kept


def _denials(raw: object) -> dict | None:
    """Bounded, scrubbed summary of Claude's permission_denials (visible even when the run succeeded)."""
    if not isinstance(raw, list) or not raw:
        return None
    items = []
    for d in raw[:MAX_DENIALS]:
        if isinstance(d, dict):
            name = d.get("tool_name")
            excerpt = json.dumps(d.get("tool_input"), default=str)[:DENIAL_EXCERPT_CHARS]
            items.append({"tool_name": name[:64] if isinstance(name, str) else "?", "input_excerpt": scrub(excerpt)})
    return {"count": len(raw), "items": items, "truncated": len(raw) > len(items)}


def _sid(v: object) -> tuple[str | None, bool]:
    if v is None:
        return None, True
    return (v[:128], True) if isinstance(v, str) else (None, False)


def _parse_claude(out: bytes) -> ParseResult:
    try:
        o = json.loads(out.decode("utf-8", "replace"))
    except ValueError:
        objs = _json_lines(out)  # stream-json style fallback: use the final result object
        o = next((x for x in reversed(objs) if x.get("type") == "result"), None)
    if not isinstance(o, dict) or o.get("type") != "result":
        return ParseResult(False, "", "malformed_output: no Claude result envelope")
    usage = normalize_usage(o.get("usage"), {k: o[k] for k in CLAUDE_TOP_METRICS if k in o})
    denials = _denials(o.get("permission_denials"))
    sid, sid_ok = _sid(o.get("session_id"))
    text = o.get("result") if isinstance(o.get("result"), str) else ""
    if not sid_ok:
        return ParseResult(False, "", "malformed_output: session_id is not a string", None, usage, denials)
    subtype = o.get("subtype")
    if o.get("is_error") is not False or subtype != "success":
        detail = subtype[:64] if isinstance(subtype, str) else "is_error"
        return ParseResult(False, text, f"backend_error: {detail}", sid, usage, denials)
    if not text.strip():
        return ParseResult(False, "", "malformed_output: empty or missing result text", sid, usage, denials)
    return ParseResult(True, text, None, sid, usage, denials)


def _parse_codex(out: bytes) -> ParseResult:
    objs = _json_lines(out)
    if not objs:
        return ParseResult(False, "", "malformed_output: no JSON events")
    sid, text, usage, err, completed, sid_ok = None, "", {}, None, False, True
    for o in objs:
        t = o.get("type")
        if t == "thread.started":
            sid, sid_ok = _sid(o.get("thread_id"))
        elif t == "item.completed" and isinstance(o.get("item"), dict):
            item = o["item"]
            if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                text = item["text"]
        elif t == "turn.completed":
            completed = True
            usage = normalize_usage(o.get("usage"))
        elif t in ("turn.failed", "error"):
            e = o.get("error")
            err = (e.get("message") if isinstance(e, dict) else None) or o.get("message") or t
    if err:  # any failure event wins over a completed turn
        return ParseResult(False, text, f"backend_error: {str(err)[:500]}", sid, usage)
    if not sid_ok:
        return ParseResult(False, "", "malformed_output: thread_id is not a string", None, usage)
    if not completed:
        return ParseResult(False, "", "malformed_output: no turn.completed event", sid, usage)
    if not text:
        return ParseResult(False, "", "malformed_output: no agent message", sid, usage)
    return ParseResult(True, text, None, sid, usage)


def _parse_omp(out: bytes) -> ParseResult:
    # UNVERIFIED best effort: OMP's JSON schema was never confirmed and OMP is disabled by default.
    # Unlike Claude/Codex this does not prove the backend completed its turn.
    objs = _json_lines(out)
    for o in reversed(objs):
        for k in ("result", "text", "content"):
            if isinstance(o.get(k), str) and o[k]:
                return ParseResult(True, o[k], None, o.get("session_id"), {})
    raw = out.decode("utf-8", "replace").strip()
    if raw:
        return ParseResult(True, raw, None)
    return ParseResult(False, "", "malformed_output: empty output")


def parse_output(adapter: str, out: bytes, returncode: int) -> ParseResult:
    r = {"claude": _parse_claude, "codex": _parse_codex, "omp": _parse_omp}[adapter](out)
    if returncode != 0:
        detail = r.error or "backend reported no error detail"
        return ParseResult(False, r.text, f"exit {returncode}: {detail}", r.session_id, r.usage, r.denials)
    return r
