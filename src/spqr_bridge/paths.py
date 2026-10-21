"""Workspace path policy. Restricts the launch cwd only — this is NOT an OS sandbox.

Linux only: an admitted workspace is pinned as an O_DIRECTORY fd whose canonical path is read back
from /proc/self/fd and whose dev/ino is recorded. The agent is launched with cwd
`/proc/self/fd/<fd>` (fd passed to the child), so a rename/symlink swap of the *path* after
admission cannot redirect the launch to a different directory object. This does not stop a
same-user process from moving the directory itself (or an ancestor) out of the allowed root after
launch; enforcement of what the agent may touch remains the backend CLI's own.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class PathPolicyError(ValueError):
    pass


def _check_policy(real: Path, allowed_roots: list[Path], exclude: list[Path]) -> Path:
    for ex in exclude:  # e.g. the bridge state dir
        e = Path(ex).resolve()
        if real == e or real.is_relative_to(e):
            raise PathPolicyError("workspace is inside a protected bridge directory")
    for root in allowed_roots:
        r = root.resolve()
        # Path.is_relative_to compares components, so /root-evil is not inside /root.
        if real == r or real.is_relative_to(r):
            return real
    raise PathPolicyError("workspace is outside the allowed roots")


def resolve_workspace(raw: str, allowed_roots: list[Path], exclude: list[Path] = ()) -> Path:
    if not raw or "\x00" in raw:
        raise PathPolicyError("workspace path is empty or invalid")
    p = Path(raw)
    if not p.is_absolute():
        raise PathPolicyError("workspace path must be absolute")
    try:
        real = p.resolve(strict=True)  # follows symlinks; escapes are caught below
    except (OSError, RuntimeError):
        raise PathPolicyError("workspace path does not exist") from None
    if not real.is_dir():
        raise PathPolicyError("workspace path is not a directory")
    return _check_policy(real, allowed_roots, list(exclude))


@dataclass
class PinnedDir:
    """An admitted workspace held open by fd. `close()` is idempotent."""

    fd: int
    path: Path
    dev: int
    ino: int

    @property
    def launch_cwd(self) -> str:
        return f"/proc/self/fd/{self.fd}"

    def close(self) -> None:
        if self.fd >= 0:
            fd, self.fd = self.fd, -1
            os.close(fd)


def check_pinned(pinned: PinnedDir, allowed_roots: list[Path], exclude: list[Path] = ()) -> None:
    """Reject if the pinned object moved, or if the admitted path now names a different object."""
    try:
        actual = Path(os.readlink(f"/proc/self/fd/{pinned.fd}"))
        st = os.stat(pinned.path)
    except OSError:
        raise PathPolicyError("workspace_changed: admitted directory is gone") from None
    if actual != pinned.path or (st.st_dev, st.st_ino) != (pinned.dev, pinned.ino):
        raise PathPolicyError("workspace_changed: admitted directory was moved or replaced")
    _check_policy(actual, allowed_roots, list(exclude))


def pin_workspace(raw: str, allowed_roots: list[Path], exclude: list[Path] = ()) -> PinnedDir:
    real = resolve_workspace(raw, allowed_roots, exclude)
    try:
        fd = os.open(real, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError:
        raise PathPolicyError("workspace could not be opened as a directory") from None
    try:
        st = os.fstat(fd)
        pinned = PinnedDir(fd, real, st.st_dev, st.st_ino)
        check_pinned(pinned, allowed_roots, exclude)  # the opened object is the one that was checked
    except BaseException:
        os.close(fd)
        raise
    return pinned
