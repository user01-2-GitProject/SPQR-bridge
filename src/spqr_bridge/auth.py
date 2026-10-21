"""v2 stdio credential bootstrap: read one bounded credential from an inherited anonymous pipe."""

from __future__ import annotations

import os
import select
import stat
import time

MAX_CREDENTIAL_BYTES = 4096
DEADLINE_SECONDS = 2.0


class CredentialError(Exception):
    """Safe code only; never carries credential bytes."""


def read_credential_fd(fd: int, max_bytes: int = MAX_CREDENTIAL_BYTES,
                       deadline: float = DEADLINE_SECONDS) -> bytes:
    """Read the whole credential (until EOF) within `deadline` seconds, then close the fd.

    Only a pipe is accepted (never a regular disk file), and never stdin/stdout/stderr. Linux cannot
    reliably tell an anonymous pipe from an inherited named FIFO; the trusted launcher only passes
    an anonymous one.
    """
    if not isinstance(fd, int) or fd < 3:
        raise CredentialError("bad_fd")
    try:
        st = os.fstat(fd)
    except OSError:
        raise CredentialError("bad_fd") from None
    try:
        if not stat.S_ISFIFO(st.st_mode):
            raise CredentialError("not_a_pipe")
        os.set_inheritable(fd, False)  # no worker may inherit it
        buf = bytearray()
        end = time.monotonic() + deadline
        while True:
            left = end - time.monotonic()
            if left <= 0 or not select.select([fd], [], [], left)[0]:
                raise CredentialError("timeout")
            chunk = os.read(fd, max_bytes + 1 - len(buf))
            if not chunk:
                break
            buf += chunk
            if len(buf) > max_bytes:
                raise CredentialError("too_large")
        token = bytes(buf).strip()
        if not token:
            raise CredentialError("empty")
        return token
    finally:
        os.close(fd)
