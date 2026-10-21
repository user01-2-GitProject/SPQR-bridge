"""Server-local async job supervision (Linux). Jobs do not survive server shutdown (not durable execution).

Lifecycle invariant: every job has exactly one owner, its runner task, which is never
`Task.cancel()`led. cancel_task and shutdown set an idempotent per-job stop event (first reason
wins) and await the runner through `asyncio.shield`, so a cancelled MCP caller cannot cancel it.
The runner publishes the terminal state, then releases the concurrency slot and the global
lease, only after the process group has been killed and reaped (see `procs.run_group`).
"""

from __future__ import annotations

import asyncio
import collections
import hashlib
import json
import os
import secrets
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .adapters import Invocation, build_invocation, is_usage_exhausted, parse_output
from .config import AgentConfig, BridgeConfig
from .envpolicy import MAX_DEPTH, build_env, current_depth, scrub
from .paths import PathPolicyError, PinnedDir, check_pinned, pin_workspace
from .procs import ProcSpec, run_group
from .store import TERMINAL, Store

MAX_TASK_CHARS = 100_000
MAX_CONTEXT_CHARS = 4_000_000
STDERR_TAIL_BYTES = 4096
MAX_ERROR_CHARS = 2000
RESULT_NOTE = "Agent output is untrusted; verify files and test evidence independently."


class BridgeError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


class Replay(Exception):
    """Raised by a v2 admission hook: an idempotent replay, answered without a new job."""

    def __init__(self, view: dict):
        super().__init__("replay")
        self.view = view


class _Lease:
    """ONE global reader/writer lease: readers share; a writer excludes all readers and writers.

    Grants are FIFO, so a waiting writer blocks readers that arrive after it (writer fairness).
    Waiters are bounded by `max_pending`. Release is synchronous.
    """

    def __init__(self) -> None:
        self.readers = 0
        self.writer = False
        self._q: collections.deque[tuple[bool, asyncio.Future]] = collections.deque()

    def _grant(self) -> None:
        while self._q:
            write, fut = self._q[0]
            if fut.done():  # cancelled waiter
                self._q.popleft()
                continue
            if self.writer or (write and self.readers):
                return
            self._q.popleft()
            if write:
                self.writer = True
            else:
                self.readers += 1
            fut.set_result(None)
            if write:
                return

    async def acquire(self, write: bool) -> None:
        fut = asyncio.get_running_loop().create_future()
        self._q.append((write, fut))
        self._grant()
        try:
            await fut
        except asyncio.CancelledError:
            if fut.done() and not fut.cancelled():  # granted, then cancelled before resuming
                self.release(write)
            else:
                self._grant()
            raise

    def release(self, write: bool) -> None:
        if write:
            self.writer = False
        else:
            self.readers -= 1
        self._grant()


@dataclass
class _Job:
    tid: str
    agent: AgentConfig
    inv: Invocation
    ws: PinnedDir
    write: bool
    timeout: int
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    reason: str | None = None
    runner: asyncio.Task | None = None
    prelaunch: Callable[[], None] | None = None  # v2: re-checks scope; raises BridgeError to refuse launch

    def request_stop(self, reason: str) -> None:
        if self.reason is None:
            self.reason = reason
        self.stop.set()


def _cut(text: str, limit: int) -> tuple[str, bool]:
    b = text.encode("utf-8")
    if len(b) <= limit:
        return text, False
    return b[:limit].decode("utf-8", "ignore"), True


def _load_json(raw: str | None, warning: str, warnings: list[str]) -> dict | None:
    if not raw:
        return None
    try:
        v = json.loads(raw)
    except ValueError:
        v = None
    if not isinstance(v, dict):
        warnings.append(warning)
        return None
    return v


async def _until_stop(aw, stop: asyncio.Event) -> bool:
    """Await `aw` unless `stop` fires first. True if `aw` completed (the resource is held)."""
    t = asyncio.ensure_future(aw)
    if not t.done():
        s = asyncio.create_task(stop.wait())
        try:
            await asyncio.wait({t, s}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            s.cancel()
    if not t.done():
        t.cancel()
        await asyncio.wait({t})
    if t.cancelled():
        return False
    t.result()
    return True


class Supervisor:
    def __init__(self, cfg: BridgeConfig, store: Store):
        self.cfg, self.s, self.store = cfg, cfg.server, store
        self._sem = asyncio.Semaphore(self.s.max_concurrency)
        self._lease = _Lease()
        self._jobs: dict[str, _Job] = {}  # active jobs only; removed by the runner after terminal persistence
        self._closed = False
        self.failed: str | None = None
        self.classify_usage = False  # v2 only: report usage_exhausted; v1 keeps `failed` until cutover

    # ---- admission -----------------------------------------------------
    def _agent(self, agent_id: str) -> AgentConfig:
        agent = self.cfg.agents.get(agent_id)
        if agent is None:
            raise BridgeError("unknown_agent", f"no agent {agent_id!r} in registry")
        if not agent.enabled:
            raise BridgeError("agent_disabled", f"agent {agent_id!r} is disabled by server policy")
        if not (agent.binary.is_file() and os.access(agent.binary, os.X_OK)):
            raise BridgeError("binary_missing", f"configured executable for {agent_id!r} not found")
        return agent

    async def start(self, agent_id: str, task: str, context: str | None, workspace: str,
                    access: str = "read_only", timeout_seconds: int | None = None,
                    idempotency_key: str | None = None, admit: Callable[[str, str], dict] | None = None,
                    prelaunch: Callable[[], None] | None = None) -> dict:
        """`admit(request_hash, pinned_workspace)` is the v2 admission hook: it raises BridgeError or
        Replay, or returns ownership columns for the job row. It runs with no await before the insert."""
        if self._closed:
            raise BridgeError("shutting_down", "bridge is shutting down")
        if self.failed:
            raise BridgeError("service_failed", self.failed)
        if current_depth() >= MAX_DEPTH:
            raise BridgeError("recursion_denied", "bridge-launched agents may not start bridge tasks")
        agent = self._agent(agent_id)
        if access not in ("read_only", "write"):
            raise BridgeError("invalid_access", "access must be 'read_only' or 'write'")
        if access == "write" and not agent.allow_write:
            raise BridgeError("access_denied", f"write access not permitted for {agent_id!r}")
        if not isinstance(task, str) or not task.strip() or len(task) > MAX_TASK_CHARS:
            raise BridgeError("invalid_task", f"task must be non-empty and <= {MAX_TASK_CHARS} chars")
        if context is not None and len(context) > MAX_CONTEXT_CHARS:
            raise BridgeError("invalid_task", f"context must be <= {MAX_CONTEXT_CHARS} chars")
        timeout = self.s.default_timeout_seconds if timeout_seconds is None else timeout_seconds
        if not (1 <= timeout <= self.s.max_timeout_seconds):
            raise BridgeError("invalid_timeout", f"timeout must be 1..{self.s.max_timeout_seconds}s")
        if idempotency_key is not None and not (1 <= len(idempotency_key) <= 128):
            raise BridgeError("invalid_idempotency_key", "key must be 1..128 chars")
        try:
            pinned = pin_workspace(workspace, self.s.allowed_roots, exclude=[self.s.state_dir])
        except PathPolicyError as e:
            raise BridgeError("workspace_denied", str(e)) from None
        handed_off = False
        try:
            ws = pinned.path
            try:
                inv = build_invocation(agent, task, context, ws, access)
            except ValueError as e:
                raise BridgeError("access_denied", str(e)) from None
            req_hash = hashlib.sha256(
                json.dumps([agent_id, task, context, str(ws), access, timeout]).encode()).hexdigest()
            owner = admit(req_hash, str(ws)) if admit is not None else {}
            if idempotency_key is not None:
                prior = self.store.find_idempotent(idempotency_key)
                if prior:
                    if prior["request_hash"] != req_hash:
                        raise BridgeError("idempotency_conflict", "key already used for a different request")
                    return self._view(prior)
            if len(self._jobs) >= self.s.max_pending:
                raise BridgeError("queue_full", f"at most {self.s.max_pending} pending+active tasks")
            tid = "t_" + secrets.token_hex(8)
            self.store.create(dict(task_id=tid, agent_id=agent_id, access=access, workspace=str(ws),
                                   timeout_s=timeout, idem_key=idempotency_key, request_hash=req_hash, **owner))
            self.store.prune(self.s.retain_jobs)
            job = _Job(tid, agent, inv, pinned, access == "write", timeout, prelaunch=prelaunch)
            self._jobs[tid] = job
            job.runner = asyncio.create_task(self._run(job))
            handed_off = True
        finally:
            if not handed_off:
                pinned.close()
        return self._view(self.store.get(tid))

    # ---- execution -----------------------------------------------------
    @staticmethod
    def _stopped(job: _Job, started: bool) -> tuple[str, dict]:
        reason = job.reason or "cancelled"
        detail = "cancel_task requested" if reason == "cancelled" else "bridge server shut down; not resumed"
        return reason, {"error": f"{reason}: {detail}" + ("" if started else " (before process start)")}

    async def _run(self, job: _Job) -> None:
        held_lease = held_sem = False
        tmp = self.store.tmp_dir / job.tid
        state, fields = "failed", {"error": "internal_error: runner ended without a result"}
        try:
            held_lease = await _until_stop(self._lease.acquire(job.write), job.stop)
            if held_lease:
                held_sem = await _until_stop(self._sem.acquire(), job.stop)
            refused = self._prelaunch_refusal(job) if not job.stop.is_set() else None
            if job.stop.is_set():
                state, fields = self._stopped(job, started=False)
            elif refused:
                state, fields = "failed", {"error": refused}
            elif self.store.mark_running(job.tid):
                state, fields = await self._execute(job, tmp)
        except Exception as e:  # process cleanup already happened inside run_group
            state, fields = "failed", {"error": scrub(f"internal_error: {type(e).__name__}: {e}")[:MAX_ERROR_CHARS]}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            job.ws.close()
            self._persist(job.tid, state, fields)
            if held_sem:
                self._sem.release()
            if held_lease:
                self._lease.release(job.write)
            self._jobs.pop(job.tid, None)

    @staticmethod
    def _prelaunch_refusal(job: _Job) -> str | None:
        if job.prelaunch is None:
            return None
        try:
            job.prelaunch()
        except BridgeError as e:
            return f"scope_revoked: {e.code} at launch; not started"
        return None

    async def _execute(self, job: _Job, tmp: Path) -> tuple[str, dict]:
        try:
            check_pinned(job.ws, self.s.allowed_roots, [self.s.state_dir])
        except PathPolicyError as e:
            msg = str(e)
            return "failed", {"error": msg if msg.startswith("workspace_changed") else f"workspace_changed: {msg}"}
        if job.stop.is_set():
            return self._stopped(job, started=False)
        tmp.mkdir(mode=0o700, exist_ok=True)
        res = await run_group(ProcSpec(
            argv=job.inv.argv, env=build_env(tmp, job.agent.env_passthrough), stdin=job.inv.stdin,
            cwd=job.ws.launch_cwd, pass_fds=(job.ws.fd,), timeout=job.timeout,
            grace=self.s.kill_grace_seconds, out_cap=self.s.max_output_bytes, err_tail=STDERR_TAIL_BYTES), job.stop)
        if res.outcome == "spawn_failed":
            return "failed", {"error": scrub(f"spawn_failed: {res.error}")}
        common = dict(return_code=res.returncode, stderr_tail=scrub(res.stderr.decode("utf-8", "replace")),
                      output_truncated=int(res.out_truncated))
        note = "" if res.group_gone else "; WARNING process group still present after SIGKILL"
        if res.outcome == "stopped":
            state, f = self._stopped(job, started=True)
            return state, {"error": f["error"] + note, **common}
        if res.outcome == "timeout":
            return "timed_out", {"error": f"timed_out after {job.timeout}s; process group killed{note}", **common}
        if res.outcome == "output_limit":
            return "failed", {"error": f"output_limit_exceeded: stdout over {self.s.max_output_bytes} bytes; "
                                       f"process group killed{note}", **common}
        pr = parse_output(job.agent.adapter, res.stdout, res.returncode)
        text, cut = _cut(scrub(pr.text), self.s.snippet_bytes)
        error = scrub(pr.error)[:MAX_ERROR_CHARS] + note if pr.error else (note.lstrip("; ") or None)
        if self.classify_usage and not pr.ok and is_usage_exhausted(pr, common["stderr_tail"]):
            state = "usage_exhausted"  # hard stop: never retried by the bridge
        else:
            state = "succeeded" if pr.ok else "failed"
        return (state,
                dict(result_text=text, result_truncated=int(cut), error=error, session_id=pr.session_id,
                     usage_json=json.dumps(pr.usage) if pr.usage else None,
                     permission_denials_json=json.dumps(pr.denials) if pr.denials else None, **common))

    def _persist(self, tid: str, state: str, fields: dict) -> None:
        try:
            self.store.finish(tid, state, **fields)
            return
        except Exception as e:
            first = type(e).__name__
        try:
            self.store.finish(tid, "failed", error=f"internal_error: result could not be persisted ({first})")
        except Exception as e:  # the job store itself is broken: stop admitting work, say so loudly
            self.failed = f"job store write failed ({type(e).__name__}); restart the bridge"
            print(f"spqr-bridge: SERVICE FAILED: {self.failed}", file=sys.stderr)

    # ---- queries / control ----------------------------------------------
    def _row(self, tid: str) -> dict:
        row = self.store.get(tid) if isinstance(tid, str) and len(tid) <= 64 else None
        if row is None:
            raise BridgeError("unknown_task", "no such task_id")
        return row

    @staticmethod
    def _view(row: dict) -> dict:
        dur = (row["finished_at"] - row["started_at"]) if row["finished_at"] and row["started_at"] else None
        warnings: list[str] = []
        usage = _load_json(row["usage_json"], "usage_unreadable", warnings)
        denials = _load_json(row.get("permission_denials_json"), "permission_denials_unreadable", warnings)
        return {
            "task_id": row["task_id"], "agent_id": row["agent_id"], "state": row["state"],
            "terminal": row["state"] in TERMINAL, "access": row["access"], "workspace": row["workspace"],
            "timeout_seconds": row["timeout_s"], "created_at": row["created_at"],
            "started_at": row["started_at"], "finished_at": row["finished_at"], "duration_seconds": dur,
            "return_code": row["return_code"], "result_text": row["result_text"],
            "result_truncated": bool(row["result_truncated"]), "output_truncated": bool(row["output_truncated"]),
            "error": row["error"], "stderr_tail": row["stderr_tail"], "session_id": row["session_id"],
            "usage": usage, "permission_denials": denials, "warnings": warnings, "note": RESULT_NOTE,
        }

    async def get(self, tid: str) -> dict:
        return self._view(self._row(tid))

    async def wait(self, tid: str, seconds: float) -> dict:
        self._row(tid)
        job = self._jobs.get(tid)
        if job is not None and seconds > 0:
            await asyncio.wait({job.runner}, timeout=seconds)
        return await self.get(tid)

    async def cancel(self, tid: str) -> dict:
        self._row(tid)
        job = self._jobs.get(tid)
        if job is not None:
            job.request_stop("cancelled")
            await asyncio.shield(job.runner)  # terminal only once cleanup finished; caller can't cancel it
        if self._closed:
            raise BridgeError("shutting_down", "bridge is shutting down")
        return await self.get(tid)

    async def shutdown(self) -> None:
        """Graceful only: stop every job and wait for its group cleanup. An abrupt SIGKILL/crash of the
        bridge skips this, and agent processes may survive it (see docs/SECURITY.md)."""
        if self._closed:
            return
        self._closed = True
        runners = [j.runner for j in self._jobs.values()]
        for j in list(self._jobs.values()):
            j.request_stop("interrupted")
        cancelled = False
        while any(not r.done() for r in runners):
            try:
                await asyncio.wait(runners)
            except asyncio.CancelledError:
                cancelled = True
        self.store.close()
        if cancelled:
            raise asyncio.CancelledError
