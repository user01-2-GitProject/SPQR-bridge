"""Test-only helpers for the v2 principal bridge: fixture registry, tokens, extra fake agents."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

TOKENS = {"caesar": "caesar-token-" + "a" * 43, "iggy": "iggy-token-" + "b" * 43}


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def soon(hours: float = 1) -> str:
    return iso(datetime.now(timezone.utc) + timedelta(hours=hours))


def registry_text(ws: Path, principals: dict | None = None, grants: dict | None = None,
                  cleared: dict | None = None) -> str:
    """Two principals by default, both with ceiling agents claude+codex, root ws, write allowed."""
    base = {"enabled": True, "transports": ["stdio"], "agents": ["claude", "codex", "drained", "nostdin"],
            "roots": [str(ws)], "allow_write": True}
    ps = {pid: dict(base, token_sha256=[digest(t)]) for pid, t in TOKENS.items()}
    for pid, over in (principals or {}).items():
        ps[pid] = dict(ps.get(pid, base), **over)
    out = []
    for pid, p in ps.items():
        out.append(f"[principals.{pid}]")
        out += [f"{k} = {json.dumps(v)}" for k, v in p.items()]
        out.append("")
    for gid, g in (grants or {}).items():
        out.append(f"[grants.{gid}]")
        g = dict({"issued_at": soon(-1 / 60), "expires_at": soon()}, **g)
        out += [f"{k} = {json.dumps(v)}" for k, v in g.items()]
        out.append("")
    if cleared:
        out.append("[usage_cleared]")
        out += [f"{k} = {json.dumps(v)}" for k, v in cleared.items()]
    return "\n".join(out) + "\n"


def drained_agent(bins: Path) -> str:
    """A fake Claude that reports a usage-limit error envelope and exits 1, plus its config block."""
    import sys
    script = bins / "drained.py"
    script.write_text(
        "import json, sys\nsys.stdin.read()\n"
        "print(json.dumps({'type': 'result', 'subtype': 'error_during_execution', 'is_error': True,"
        " 'result': 'Claude AI usage limit reached|1792600000', 'session_id': 's'}))\nsys.exit(1)\n")
    wrapper = bins / "drained"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" -I "{script}" "$@"\n')
    wrapper.chmod(0o700)
    return f'\n[agents.drained]\nadapter = "claude"\nbinary = "{wrapper}"\nenabled = true\n'


def write_registry(path: Path, text: str) -> Path:
    """Operator registries must be owner-only."""
    path.write_text(text)
    path.chmod(0o600)
    return path
