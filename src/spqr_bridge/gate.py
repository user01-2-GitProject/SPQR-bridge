"""v2 principal gate: authentication re-check, ownership, per-job scope, usage stops and loop caps.

Sits in front of one Supervisor. Every call re-reads the operator registry (fail closed) and
re-checks the caller's credential is still listed. See DESIGN-V2.md, Amendment A.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .registry import AuthContext, Registry, RegistryError, load_registry, principal_valid
from .supervisor import BridgeError, Replay, Supervisor

FAILURE_CAP = 3        # Decision 6 band 3–5: Jimmy's standing "stuck after 3 tries, stop"
DEFAULT_ROUNDS = 5     # Decision 6 band 5–10: grants may raise to 10
LINEAGE_WINDOW = 3600  # seconds; no-grant lineage = principal + agent + workspace per hour
FAILURES = ("failed", "timed_out")
_TASK_ID = re.compile(r"^t_[0-9a-f]{16}$")
_AGENT_ID = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def _auth_error() -> BridgeError:
    return BridgeError("unauthenticated", "authentication required")


def _grant_error() -> BridgeError:
    return BridgeError("grant_denied", "no usable grant with that id for this principal")


def _within(ws: Path, roots: list[Path]) -> bool:
    return any(ws == r or ws.is_relative_to(r) for r in roots)


class PrincipalGate:
    def __init__(self, sup: Supervisor, registry_path: str | Path, auth: AuthContext):
        self.sup, self.store, self.path, self.auth = sup, sup.store, Path(registry_path), auth
        self.pid = auth.principal_id
        self.protected = self.path.resolve()  # no job workspace may contain the registry
        sup.classify_usage = True

    # ---- authority -------------------------------------------------------
    def _registry(self) -> tuple[Registry, Any]:
        try:
            reg = load_registry(self.path)
        except RegistryError:
            raise _auth_error() from None
        p = principal_valid(reg, self.pid, self.auth.credential_digest, self.auth.transport)
        if p is None:
            raise _auth_error()
        return reg, p

    def _scope(self, reg: Registry, p, grant_id: str | None) -> dict:
        if grant_id is None:  # silence is never a broad grant
            return {"grant_id": None, "access": "read_only", "agents": list(p.agents),
                    "roots": [str(r) for r in p.roots], "max_rounds": DEFAULT_ROUNDS}
        g = reg.grants.get(grant_id) if isinstance(grant_id, str) else None
        now = datetime.now(timezone.utc)
        if g is None or g.principal != self.pid or not (g.issued_at <= now < g.expires_at):
            raise _grant_error()
        agents = [a for a in (g.agents if g.agents is not None else p.agents) if a in p.agents]
        roots = [r for r in (g.roots if g.roots is not None else p.roots) if _within(r, p.roots)]
        access = "write" if g.access == "write" and p.allow_write else "read_only"
        return {"grant_id": grant_id, "access": access, "agents": agents, "roots": [str(r) for r in roots],
                "max_rounds": g.max_rounds}

    def _audit(self, tool: str, outcome: str, task_id=None, agent_id=None, grant_id=None) -> None:
        ok = lambda v, rx: v if isinstance(v, str) and rx.match(v) else None  # noqa: E731
        try:
            self.store.audit(self.pid, tool, outcome, ok(task_id, _TASK_ID),
                             agent_id if agent_id in self.sup.cfg.agents else None, ok(grant_id, _AGENT_ID))
        except Exception as e:  # audit storage failure stops new execution
            self.sup.failed = f"audit write failed ({type(e).__name__}); restart the bridge"

    # ---- tools ---------------------------------------------------------------
    async def start(self, agent_id: str, task: str, context: str | None, workspace: str,
                    access: str = "read_only", timeout_seconds: int | None = None,
                    idempotency_key: str | None = None, grant_id: str | None = None) -> dict:
        try:
            view = await self._start(agent_id, task, context, workspace, access, timeout_seconds,
                                     idempotency_key, grant_id)
        except BridgeError as e:
            self._audit("start_task", e.code, agent_id=agent_id, grant_id=grant_id)
            raise
        self._audit("start_task", "replayed" if view.get("replayed") else "admitted", view["task_id"],
                    agent_id, grant_id)
        return view

    def _allowed(self, scope: dict, agent_id: str, access: str, ws: str | None) -> None:
        if agent_id not in scope["agents"]:
            raise BridgeError("agent_denied", "agent not within this job's scope")
        if access == "write" and scope["access"] != "write":
            raise BridgeError("access_denied", "write access not granted for this job")
        if ws is not None:
            w = Path(ws).resolve()
            if not _within(w, [Path(r) for r in scope["roots"]]) or _within(self.protected, [w]):
                raise BridgeError("workspace_denied", "workspace not within this job's scope")

    async def _start(self, agent_id, task, context, workspace, access, timeout_seconds, idempotency_key,
                     grant_id) -> dict:
        reg, p = self._registry()
        scope = self._scope(reg, p, grant_id)
        # checked before pinning too, so probing outside scope cannot tell which paths exist
        self._allowed(scope, agent_id, access, workspace if isinstance(workspace, str) and
                      workspace.startswith("/") else None)
        if idempotency_key is not None and not (isinstance(idempotency_key, str) and 1 <= len(idempotency_key) <= 128):
            raise BridgeError("invalid_idempotency_key", "key must be 1..128 chars")
        cleared = reg.usage_cleared.get(agent_id)
        cleared_ts = cleared.timestamp() if cleared else 0.0

        pinned: dict[str, str] = {}

        def admit(req_hash: str, ws: str) -> dict:
            self._allowed(scope, agent_id, access, ws)
            pinned["ws"] = ws
            if idempotency_key is not None:
                prior = self.store.receipt(self.pid, idempotency_key)
                if prior:
                    if prior["request_hash"] != req_hash:
                        raise BridgeError("idempotency_conflict", "key already used for a different request")
                    raise Replay(self._replay(prior["task_id"]))
            if self.store.usage_stopped(agent_id, cleared_ts):
                raise BridgeError("usage_stopped", f"{agent_id!r} stopped on usage; waiting for Jimmy to clear it")
            self._check_loop(scope, agent_id, ws)
            return {"principal_id": self.pid, "grant_id": scope["grant_id"], "receipt_key": idempotency_key,
                    "scope_json": json.dumps(scope, sort_keys=True)}

        def prelaunch() -> None:  # same checks as admission, against the registry as it is now
            reg2, p2 = self._registry()
            self._allowed(self._scope(reg2, p2, grant_id), agent_id, access, pinned["ws"])

        try:
            row = await self.sup.start(agent_id, task, context, workspace, access, timeout_seconds,
                                       None, admit=admit, prelaunch=prelaunch)
        except Replay as r:
            return r.view
        return self._owned_view(row["task_id"])

    def _check_loop(self, scope: dict, agent_id: str, ws: str) -> None:
        """Decision 6 caps over one lineage (one query feeds all three checks)."""
        jobs = self.store.lineage(self.pid, scope["grant_id"], agent_id, ws, time.time() - LINEAGE_WINDOW)
        if len(jobs) >= scope["max_rounds"]:
            raise BridgeError("round_cap_reached", f"{scope['max_rounds']} rounds used; ask Jimmy for a new grant")
        finished = [j for j in jobs if j["state"] not in ("queued", "running")]
        streak = 0
        for j in finished:
            if j["state"] != "succeeded" and j["state"] not in FAILURES:
                continue  # cancelled/interrupted are neither progress nor failure
            if j["state"] == "succeeded":
                break
            streak += 1
        if streak >= FAILURE_CAP:
            raise BridgeError("retry_cap_reached", f"{FAILURE_CAP} failures in a row; stopping for Jimmy")
        finished = [j for j in finished if j["state"] == "succeeded" or j["state"] in FAILURES]
        if len(finished) >= 2:
            a, b = finished[0], finished[1]
            if (a["state"], a["result_text"], a["error"]) == (b["state"], b["result_text"], b["error"]):
                raise BridgeError("no_progress", "the last two rounds produced the same result; stopping")

    def _replay(self, task_id: str) -> dict:
        row = self.store.get(task_id)
        if row is None:  # pruned: never re-executed
            return {"task_id": task_id, "state": "result_expired", "terminal": True, "replayed": True,
                    "principal_id": self.pid}
        return dict(self._owned_view(task_id), replayed=True)

    def _owned_row(self, task_id: str) -> dict:
        row = self.store.get(task_id) if isinstance(task_id, str) and _TASK_ID.match(task_id) else None
        if row is None or row.get("principal_id") != self.pid:
            raise BridgeError("unknown_task", "no such task_id")  # foreign and missing look the same
        return row

    def _owned_view(self, task_id: str) -> dict:
        row = self._owned_row(task_id)
        return dict(self.sup._view(row), principal_id=row["principal_id"], grant_id=row["grant_id"])

    async def _audited(self, tool: str, task_id, coro_fn) -> dict:
        try:
            out = await coro_fn()
        except BridgeError as e:
            self._audit(tool, e.code, task_id)
            raise
        self._audit(tool, "ok", task_id)
        return out

    async def wait(self, task_id: str, seconds: float) -> dict:
        async def run():
            self._registry()
            self._owned_row(task_id)
            await self.sup.wait(task_id, seconds)
            self._registry()  # revalidate before returning protected output after a wait
            return self._owned_view(task_id)
        return await self._audited("get_task", task_id, run)

    async def cancel(self, task_id: str) -> dict:
        async def run():
            self._registry()
            self._owned_row(task_id)
            await self.sup.cancel(task_id)
            return self._owned_view(task_id)
        return await self._audited("cancel_task", task_id, run)

    def list_agents(self) -> dict:
        _, p = self._registry()
        agents = []
        for aid in p.agents:
            a = self.sup.cfg.agents.get(aid)
            if a is None:
                continue
            agents.append({"id": aid, "adapter": a.adapter, "enabled": a.enabled,
                           "access_modes": ["read_only"],  # without a grant
                           "write_possible_with_grant": a.allow_write and p.allow_write,
                           "description": a.description})
        self._audit("list_agents", "ok")
        return {"agents": agents, "max_timeout_seconds": self.sup.s.max_timeout_seconds,
                "note": "write and wider scope need a grant from Jimmy (pass grant_id)"}
