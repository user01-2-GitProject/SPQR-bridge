"""Credential launcher for the v2 stdio server.

    python -m spqr_bridge.launch --token-file F -- --config C --registry R

Reads the token from an owner-only file, hands it to the server on an anonymous pipe and
execs `python -m spqr_bridge.v2 ... --credential-fd N`. The token never appears in argv,
environment or MCP payloads.
"""

from __future__ import annotations

import os
import stat
import sys

MAX_TOKEN_BYTES = 4096


def _fail(msg: str) -> None:
    print(f"spqr-bridge-launch: {msg}", file=sys.stderr)
    sys.exit(2)


def read_token_file(path: str) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        _fail("cannot open token file")
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
            _fail("token file must be a regular file owned by this user with mode 0600")
        data = os.read(fd, MAX_TOKEN_BYTES + 1)
    finally:
        os.close(fd)
    if not data.strip() or len(data) > MAX_TOKEN_BYTES:
        _fail("token file is empty or too large")
    return data.strip()


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 3 or argv[0] != "--token-file" or argv[2] != "--":
        _fail("usage: --token-file FILE -- <server args>")
    server_args = argv[3:]
    if any(a == "--credential-fd" or a.startswith("--credential-fd=") for a in server_args):
        _fail("--credential-fd is set by the launcher")
    token = read_token_file(argv[1])
    r, w = os.pipe()
    os.write(w, token)  # far below pipe capacity, so this never blocks
    os.close(w)
    os.set_inheritable(r, True)
    os.execv(sys.executable, [sys.executable, "-m", "spqr_bridge.v2", *server_args, "--credential-fd", str(r)])


if __name__ == "__main__":
    main()
