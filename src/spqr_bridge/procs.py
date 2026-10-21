"""Owned process-group lifecycle (Linux/POSIX), shared by agent jobs and doctor probes.

`run_group` runs spawn → supervise → group cleanup inside an inner task that nobody cancels.
A stop request, a timeout, an output cap or cancellation of the awaiting caller all become a
stop signal: the native spawn is allowed to settle (so a child created while asyncio is still
connecting pipes is never orphaned), the process group gets SIGTERM then SIGKILL, the leader is
reaped, and only then does the call return (or re-raise CancelledError).
"""

from __future__ import annotations

import asyncio
import os
import signal
from dataclasses import dataclass


@dataclass(frozen=True)
class ProcSpec:
    argv: list[str]
    env: dict[str, str]
    stdin: bytes | None = None          # None -> /dev/null
    cwd: str | None = None
    pass_fds: tuple[int, ...] = ()
    timeout: float = 60.0
    grace: float = 3.0
    out_cap: int = 2_000_000
    err_tail: int = 4096


@dataclass
class ProcResult:
    outcome: str                        # exited | output_limit | timeout | stopped | spawn_failed
    returncode: int | None = None
    stdout: bytes = b""
    stderr: bytes = b""
    out_truncated: bool = False
    group_gone: bool = True
    error: str | None = None


class _Buf:
    def __init__(self, cap: int, tail: bool = False):
        self.cap, self.tail = cap, tail
        self.data = bytearray()
        self.truncated = False


async def _drain(stream: asyncio.StreamReader, buf: _Buf, cap_hit: asyncio.Event | None) -> None:
    """Read until EOF. Bytes beyond the cap are discarded (tail mode keeps the last `cap`)."""
    while chunk := await stream.read(65536):
        if buf.tail:
            buf.data += chunk
            if len(buf.data) > buf.cap:
                del buf.data[: len(buf.data) - buf.cap]
                buf.truncated = True
            continue
        room = buf.cap - len(buf.data)
        if room > 0:
            buf.data += chunk[:room]
        if len(chunk) > room:
            buf.truncated = True
            if cap_hit:
                cap_hit.set()


async def _feed(proc: asyncio.subprocess.Process, data: bytes) -> None:
    try:
        proc.stdin.write(data)
        await proc.stdin.drain()
        proc.stdin.close()
    except (BrokenPipeError, ConnectionResetError):
        pass


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


async def kill_group(pgid: int, grace: float) -> bool:
    """SIGTERM the process group, wait `grace`, then SIGKILL and wait briefly. True if the group is gone."""
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 5.0)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return True
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait
        while loop.time() < deadline:
            if not _group_alive(pgid):
                return True
            await asyncio.sleep(0.05)
    return not _group_alive(pgid)


async def run_group(spec: ProcSpec, stop: asyncio.Event | None = None) -> ProcResult:
    """Run `spec` as a new session/process group and always clean the group up before returning."""
    stop = stop if stop is not None else asyncio.Event()
    inner = asyncio.create_task(_lifecycle(spec, stop))
    caller_cancelled = False
    while not inner.done():
        try:
            await asyncio.shield(inner)
        except asyncio.CancelledError:
            if inner.done():
                break
            caller_cancelled = True     # keep owning the child: stop it, then finish cleanup
            stop.set()
    if caller_cancelled:
        if not inner.cancelled():
            inner.exception()           # consume; the caller's cancellation takes precedence
        raise asyncio.CancelledError
    return inner.result()


async def _lifecycle(spec: ProcSpec, stop: asyncio.Event) -> ProcResult:
    try:
        # Never cancelled: if a stop arrives mid-spawn we still collect the Process and kill its group.
        proc = await asyncio.create_subprocess_exec(
            *spec.argv, stdin=asyncio.subprocess.PIPE if spec.stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=spec.cwd, env=spec.env,
            pass_fds=spec.pass_fds, start_new_session=True)
    except OSError as e:
        return ProcResult("spawn_failed", error=e.strerror or type(e).__name__)
    loop = asyncio.get_running_loop()
    out, err = _Buf(spec.out_cap), _Buf(spec.err_tail, tail=True)
    cap_hit = asyncio.Event()
    helpers: list[asyncio.Task] = []
    readers: list[asyncio.Task] = []
    outcome, gone = "stopped", False
    try:
        if spec.stdin is not None:
            helpers.append(asyncio.create_task(_feed(proc, spec.stdin)))
        readers = [asyncio.create_task(_drain(proc.stdout, out, cap_hit)),
                   asyncio.create_task(_drain(proc.stderr, err, None))]
        waiter = asyncio.create_task(proc.wait())
        capw, stopw = asyncio.create_task(cap_hit.wait()), asyncio.create_task(stop.wait())
        helpers += [*readers, waiter, capw, stopw]
        deadline = loop.time() + spec.timeout
        open_readers = set(readers)
        outcome = "timeout"
        while (remaining := deadline - loop.time()) > 0:
            done, _ = await asyncio.wait({waiter, capw, stopw, *open_readers}, timeout=remaining,
                                         return_when=asyncio.FIRST_COMPLETED)
            for r in done & open_readers:
                r.result()              # a pipeline error propagates after cleanup below
                open_readers.discard(r)
            if waiter in done:
                outcome = "exited"
            elif capw in done:
                outcome = "output_limit"
            elif stopw in done:
                outcome = "stopped"
            else:
                continue
            break
    finally:
        # Always signal the group: also reaps descendants left behind after a clean exit.
        gone = await kill_group(proc.pid, spec.grace)
        await proc.wait()
        if readers:
            await asyncio.wait(readers, timeout=2)
        for h in helpers:
            if not h.done():
                h.cancel()
        await asyncio.gather(*helpers, return_exceptions=True)
        if proc.stdin is not None and not proc.stdin.is_closing():
            proc.stdin.close()
    for r in readers:
        if not r.cancelled() and r.exception() is not None:
            raise r.exception()
    return ProcResult(outcome, proc.returncode, bytes(out.data), bytes(err.data), out.truncated, gone)
