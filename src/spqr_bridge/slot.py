"""Issue a v2 client slot: a fresh token file plus the registry block that admits it.

    python -m spqr_bridge.slot new NAME [--dir ~/.config/spqr] [--agents claude,codex] [--roots /abs,...]

Writes DIR/NAME.token (mode 0600, never overwritten) and prints a [principals.NAME] block to paste
into DIR/registry.toml. The token is never printed. Any MCP client that runs `scripts/spqr-v2 NAME`
holds the slot; see docs/CLIENTS.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sys
from pathlib import Path

_ID = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")  # same rule as registry ids


def _csv(v: str) -> list[str]:
    return [x.strip() for x in v.split(",") if x.strip()]


def new_slot(name: str, directory: Path, agents: list[str], roots: list[str]) -> str:
    if not _ID.match(name):
        raise ValueError("slot name must match [a-z][a-z0-9_-]{0,31}")
    for r in roots:
        if not os.path.isabs(r):
            raise ValueError(f"root must be absolute: {r}")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32).encode()
    fd = os.open(directory / f"{name}.token", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, token)
    finally:
        os.close(fd)
    return "\n".join([
        f"[principals.{name}]",
        "enabled = true",
        f"token_sha256 = {json.dumps([hashlib.sha256(token).hexdigest()])}",
        'transports = ["stdio"]',
        f"agents = {json.dumps(agents)}    # ceiling: agents this slot may task",
        f"roots = {json.dumps(roots)}    # ceiling: workspaces, inside allowed_roots",
        "allow_write = false    # ceiling: may a grant ever give write?",
        "",
    ])


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="spqr-bridge-slot", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter, allow_abbrev=False)
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new", help="create NAME.token and print its registry block")
    n.add_argument("name")
    n.add_argument("--dir", type=Path, default=Path.home() / ".config" / "spqr")
    n.add_argument("--agents", type=_csv, default=[])
    n.add_argument("--roots", type=_csv, default=[])
    a = ap.parse_args(argv)
    try:
        sys.stdout.write(new_slot(a.name, a.dir, a.agents, a.roots))
    except (ValueError, OSError) as e:
        print(f"spqr-bridge-slot: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
