"""Minimal child environment and secret scrubbing."""

from __future__ import annotations

import os
import re
from pathlib import Path

DEPTH_VAR = "SPQR_BRIDGE_DEPTH"
MAX_DEPTH = 1  # a bridge-launched agent may not itself start bridge tasks

BASE_ALLOW = (
    "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TERM",
    "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR",
)


def current_depth() -> int:
    try:
        return int(os.environ.get(DEPTH_VAR, "0"))
    except ValueError:
        return MAX_DEPTH


def build_env(tmpdir: Path, passthrough: list[str]) -> dict[str, str]:
    env = {k: os.environ[k] for k in BASE_ALLOW if k in os.environ}
    for name in passthrough:  # operator-configured NAMES only
        if name in os.environ:
            env[name] = os.environ[name]
    env["TMPDIR"] = str(tmpdir)
    env[DEPTH_VAR] = str(current_depth() + 1)
    return env


_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password)[\"']?\s*[:=]\s*[\"']?)[^\s\"',]{6,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
]


def scrub(text: str) -> str:
    """Best-effort redaction of secret-looking strings. Not a guarantee."""
    for pat in _SECRET_PATTERNS:
        if pat.groups:
            text = pat.sub(lambda m: m.group(1) + "[REDACTED]", text)
        else:
            text = pat.sub("[REDACTED]", text)
    return text
