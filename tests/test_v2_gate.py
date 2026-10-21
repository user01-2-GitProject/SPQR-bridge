"""v2 Phase 1: principal gate over the Supervisor (ownership, per-job grants, usage stops, loop caps).

Agents are the FAKE fixture executables from conftest — no live models are invoked.
"""

import asyncio
import json

import pytest

from spqr_bridge.config import load_config
from spqr_bridge.gate import PrincipalGate
from spqr_bridge.registry import authenticate, load_registry
from spqr_bridge.store import Store
from spqr_bridge.supervisor import BridgeError, Supervisor
from spqr_bridge.v2 import prepare_config
from v2_helpers import TOKENS, drained_agent, registry_text, soon, write_registry


@pytest.fixture
async def v2(env_setup):
    cfg_text, ws, state, tmp = env_setup
    other = tmp / "other"  # inside no principal's roots but inside allowed_roots
    (ws / "sub").mkdir()
    cfgp = tmp / "bridge.toml"
    cfgp.write_text(cfg_text() + drained_agent(tmp / "bin"))
    cfg = prepare_config(load_config(cfgp))
    sup = Supervisor(cfg, Store(cfg.server.state_dir))
    regp = tmp / "registry.toml"

    class Env:
        pass
    e = Env()
    e.ws, e.tmp, e.sup, e.regp, e.cfg = ws, tmp, sup, regp, cfg

    def registry(**kw):
        write_registry(regp, registry_text(ws, **kw))
    e.registry = registry
    registry()

    def gate(pid):
        ctx = authenticate(load_registry(regp), TOKENS[pid].encode(), "stdio")
        assert ctx is not None
        return PrincipalGate(sup, regp, ctx)
    e.gate = gate
    yield e
    await sup.shutdown()


async def done(g, tid):
    return await g.wait(tid, 20)


def code(exc_info):
    return exc_info.value.code


# ---- ownership / idempotency --------------------------------------------------

async def test_foreign_task_looks_missing(v2):
    a, b = v2.gate("caesar"), v2.gate("iggy")
    r = await a.start("claude", "hello", None, str(v2.ws))
    j = await done(a, r["task_id"])
    assert j["state"] == "succeeded"
    for op in (lambda: b.wait(r["task_id"], 0), lambda: b.cancel(r["task_id"])):
        with pytest.raises(BridgeError) as e:
            await op()
        assert code(e) == "unknown_task"
    with pytest.raises(BridgeError) as e2:
        await b.wait("t_0000000000000000", 0)
    assert str(e.value) == str(e2.value)


async def test_legacy_v1_rows_invisible(v2):
    r = await v2.sup.start("claude", "legacy", None, str(v2.ws))
    await v2.sup.wait(r["task_id"], 20)
    with pytest.raises(BridgeError) as e:
        await v2.gate("caesar").wait(r["task_id"], 0)
    assert code(e) == "unknown_task"


async def test_idempotency_is_per_principal(v2):
    a, b = v2.gate("caesar"), v2.gate("iggy")
    ra = await a.start("claude", "same", None, str(v2.ws), idempotency_key="k1")
    rb = await b.start("claude", "same", None, str(v2.ws), idempotency_key="k1")
    assert ra["task_id"] != rb["task_id"]
    again = await a.start("claude", "same", None, str(v2.ws), idempotency_key="k1")
    assert again["task_id"] == ra["task_id"]
    with pytest.raises(BridgeError) as e:
        await a.start("claude", "different", None, str(v2.ws), idempotency_key="k1")
    assert code(e) == "idempotency_conflict"


async def test_replay_after_prune_does_not_execute(v2):
    a = v2.gate("caesar")
    r = await a.start("claude", "once", None, str(v2.ws), idempotency_key="k2")
    await done(a, r["task_id"])
    v2.sup.store.db.execute("DELETE FROM jobs WHERE task_id=?", (r["task_id"],))
    n = v2.sup.store.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    again = await a.start("claude", "once", None, str(v2.ws), idempotency_key="k2")
    assert again["task_id"] == r["task_id"] and again["state"] == "result_expired"
    assert v2.sup.store.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == n


# ---- scope: silence is minimum, grants scope one job ---------------------------

async def test_no_grant_is_read_only(v2):
    a = v2.gate("caesar")
    with pytest.raises(BridgeError) as e:
        await a.start("claude", "edit", None, str(v2.ws), access="write")
    assert code(e) == "access_denied"
    r = await a.start("claude", "read", None, str(v2.ws))
    assert r["access"] == "read_only"


async def test_write_grant_allows_write(v2):
    v2.registry(grants={"g1": {"principal": "caesar", "access": "write", "agents": ["claude"]}})
    r = await v2.gate("caesar").start("claude", "edit", None, str(v2.ws), access="write", grant_id="g1")
    j = await done(v2.gate("caesar"), r["task_id"])
    assert j["state"] == "succeeded" and j["access"] == "write" and j["grant_id"] == "g1"


@pytest.mark.parametrize("grants,principal", [
    ({"g1": {"principal": "iggy", "access": "write"}}, "caesar"),        # someone else's grant
    ({"g1": {"principal": "caesar", "access": "write", "issued_at": soon(-3),
             "expires_at": soon(-1)}}, "caesar"),                         # expired
    ({}, "caesar"),                                                       # missing
])
async def test_unusable_grant_generic_error(v2, grants, principal):
    v2.registry(grants=grants)
    with pytest.raises(BridgeError) as e:
        await v2.gate(principal).start("claude", "x", None, str(v2.ws), access="write", grant_id="g1")
    assert code(e) == "grant_denied" and "iggy" not in str(e.value)


async def test_grant_narrows_agents_and_roots(v2):
    v2.registry(grants={"g1": {"principal": "caesar", "access": "read_only", "agents": ["codex"],
                               "roots": [str(v2.ws / "sub")]}})
    a = v2.gate("caesar")
    with pytest.raises(BridgeError) as e:
        await a.start("claude", "x", None, str(v2.ws / "sub"), grant_id="g1")
    assert code(e) == "agent_denied"
    with pytest.raises(BridgeError) as e:
        await a.start("codex", "x", None, str(v2.ws), grant_id="g1")
    assert code(e) == "workspace_denied"
    r = await a.start("codex", "x", None, str(v2.ws / "sub"), grant_id="g1")
    assert (await done(a, r["task_id"]))["state"] == "succeeded"


async def test_grant_cannot_exceed_principal_ceiling(v2):
    v2.registry(principals={"caesar": {"agents": ["codex"], "allow_write": False}},
                grants={"g1": {"principal": "caesar", "access": "write", "agents": ["claude", "codex"]}})
    a = v2.gate("caesar")
    with pytest.raises(BridgeError) as e:
        await a.start("claude", "x", None, str(v2.ws), grant_id="g1")
    assert code(e) == "agent_denied"
    with pytest.raises(BridgeError) as e:
        await a.start("codex", "x", None, str(v2.ws), access="write", grant_id="g1")
    assert code(e) == "access_denied"


async def test_grant_cannot_enable_disabled_agent(v2):
    v2.registry(principals={"caesar": {"agents": ["off"]}},
                grants={"g1": {"principal": "caesar", "access": "read_only", "agents": ["off"]}})
    with pytest.raises(BridgeError) as e:
        await v2.gate("caesar").start("off", "x", None, str(v2.ws), grant_id="g1")
    assert code(e) == "agent_disabled"


async def test_grant_rechecked_before_launch(v2):
    v2.registry(grants={"g1": {"principal": "caesar", "access": "write"}})
    a = v2.gate("caesar")
    # occupy the writer lease so the next write job stays queued
    hold = await a.start("claude", f"FAKE_MODE=hang {v2.tmp / 'p'}", None, str(v2.ws), access="write",
                         grant_id="g1")
    queued = await a.start("claude", "later", None, str(v2.ws), access="write", grant_id="g1")
    v2.registry(grants={})  # Jimmy withdraws the grant while the job waits in the queue
    await a.cancel(hold["task_id"])
    j = await done(v2.gate("caesar"), queued["task_id"])
    assert j["state"] == "failed" and "scope_revoked" in j["error"] and j["started_at"] is None


async def test_scope_snapshot_recorded(v2):
    v2.registry(grants={"g1": {"principal": "caesar", "access": "read_only", "agents": ["claude"]}})
    a = v2.gate("caesar")
    r = await a.start("claude", "x", None, str(v2.ws), grant_id="g1")
    row = v2.sup.store.get(r["task_id"])
    scope = json.loads(row["scope_json"])
    assert row["principal_id"] == "caesar" and scope["agents"] == ["claude"] and scope["access"] == "read_only"


# ---- v2 has no money layer ----------------------------------------------------

async def test_no_budget_flag_in_v2(v2):
    a = v2.gate("caesar")
    r = await a.start("claude", "x", None, str(v2.ws))
    j = await done(a, r["task_id"])
    assert "--max-budget-usd" not in json.loads(j["result_text"])["argv"]


# ---- usage stops (Decision 5) ---------------------------------------------------

async def test_usage_exhaustion_is_hard_stop_for_everyone(v2):
    a, b = v2.gate("caesar"), v2.gate("iggy")
    r = await a.start("drained", "work", None, str(v2.ws))
    j = await done(a, r["task_id"])
    assert j["state"] == "usage_exhausted" and j["terminal"]
    for g, task in ((a, "work"), (a, "other work"), (b, "work")):
        with pytest.raises(BridgeError) as e:
            await g.start("drained", task, None, str(v2.ws))
        assert code(e) == "usage_stopped"
    # other agents keep working
    assert (await done(a, (await a.start("claude", "x", None, str(v2.ws)))["task_id"]))["state"] == "succeeded"


async def test_usage_stop_cleared_by_jimmy(v2):
    a = v2.gate("caesar")
    await done(a, (await a.start("drained", "work", None, str(v2.ws)))["task_id"])
    await asyncio.sleep(2.1)
    v2.registry(cleared={"drained": soon(-1 / 3600)})  # one second ago, after the stop
    r = await a.start("drained", "work again", None, str(v2.ws))
    assert (await done(a, r["task_id"]))["state"] == "usage_exhausted"


async def test_successful_answer_mentioning_usage_is_not_a_stop(v2):
    a = v2.gate("caesar")
    r = await a.start("claude", "Claude AI usage limit reached", None, str(v2.ws))
    assert (await done(a, r["task_id"]))["state"] == "succeeded"
    await a.start("claude", "next", None, str(v2.ws))


# ---- loop guardrails (Decision 6) -----------------------------------------------

async def test_failure_cap_three_even_if_task_text_changes(v2):
    a = v2.gate("caesar")
    for i in range(3):
        j = await done(a, (await a.start("claude", f"FAKE_MODE=fail try{i}", None, str(v2.ws)))["task_id"])
        assert j["state"] == "failed"
    with pytest.raises(BridgeError) as e:
        await a.start("claude", "FAKE_MODE=fail a new approach", None, str(v2.ws))
    assert code(e) == "retry_cap_reached"
    # another principal's lineage is separate
    await v2.gate("iggy").start("claude", "x", None, str(v2.ws))


async def test_success_resets_failure_cap(v2):
    v2.registry(grants={"g1": {"principal": "caesar", "access": "read_only", "max_rounds": 10}})
    a = v2.gate("caesar")
    for task in ("FAKE_MODE=fail 1", "FAKE_MODE=fail 2", "ok", "FAKE_MODE=fail 3", "FAKE_MODE=fail 4"):
        await done(a, (await a.start("claude", task, None, str(v2.ws), grant_id="g1"))["task_id"])
    await a.start("claude", "still allowed", None, str(v2.ws), grant_id="g1")


async def test_round_cap_default_five_without_grant(v2):
    a = v2.gate("caesar")
    for i in range(5):
        await done(a, (await a.start("claude", f"round {i}", None, str(v2.ws)))["task_id"])
    with pytest.raises(BridgeError) as e:
        await a.start("claude", "round 6", None, str(v2.ws))
    assert code(e) == "round_cap_reached"
    await a.start("codex", "different agent is a different lineage", None, str(v2.ws))


async def test_grant_max_rounds(v2):
    v2.registry(grants={"g1": {"principal": "caesar", "access": "read_only", "max_rounds": 6}})
    a = v2.gate("caesar")
    for i in range(6):
        await done(a, (await a.start("claude", f"round {i}", None, str(v2.ws), grant_id="g1"))["task_id"])
    with pytest.raises(BridgeError) as e:
        await a.start("claude", "round 7", None, str(v2.ws), grant_id="g1")
    assert code(e) == "round_cap_reached"


async def test_replays_do_not_consume_rounds(v2):
    a = v2.gate("caesar")
    r = await a.start("claude", "same", None, str(v2.ws), idempotency_key="k")
    await done(a, r["task_id"])
    for _ in range(6):
        await a.start("claude", "same", None, str(v2.ws), idempotency_key="k")
    for i in range(4):
        await a.start("claude", f"fresh {i}", None, str(v2.ws))


async def test_no_progress_tripwire(v2):
    v2.registry(grants={"g1": {"principal": "caesar", "access": "read_only", "max_rounds": 10}})
    a = v2.gate("caesar")
    for i in range(2):  # different tasks, identical outcome
        await done(a, (await a.start("claude", f"FAKE_MODE=semantic variant{i}", None, str(v2.ws),
                                     grant_id="g1"))["task_id"])
    with pytest.raises(BridgeError) as e:
        await a.start("claude", "FAKE_MODE=semantic variant2", None, str(v2.ws), grant_id="g1")
    assert code(e) == "no_progress"


async def test_concurrent_starts_cannot_overrun_round_cap(v2):
    v2.registry(grants={"g1": {"principal": "caesar", "access": "read_only", "max_rounds": 5}})
    a = v2.gate("caesar")
    res = await asyncio.gather(*[a.start("claude", f"FAKE_MODE=slow c{i}", None, str(v2.ws), grant_id="g1")
                                 for i in range(8)], return_exceptions=True)
    ok = [r for r in res if isinstance(r, dict)]
    refused = [r for r in res if isinstance(r, BridgeError)]
    assert len(ok) == 5 and {r.code for r in refused} == {"round_cap_reached"}


# ---- revocation / registry reload ------------------------------------------------

async def test_revoked_principal_cannot_read_results(v2):
    a = v2.gate("caesar")
    r = await a.start("claude", "x", None, str(v2.ws))
    await done(a, r["task_id"])
    v2.registry(principals={"caesar": {"enabled": False}})
    for op in (lambda: a.wait(r["task_id"], 0), lambda: a.start("claude", "y", None, str(v2.ws)),
               lambda: a.list_agents()):
        with pytest.raises(BridgeError) as e:
            res = op()
            if asyncio.iscoroutine(res):
                await res
        assert code(e) == "unauthenticated"


async def test_rotated_out_credential_loses_session(v2):
    a = v2.gate("caesar")
    v2.registry(principals={"caesar": {"token_sha256": ["0" * 64]}})
    with pytest.raises(BridgeError) as e:
        await a.start("claude", "x", None, str(v2.ws))
    assert code(e) == "unauthenticated"


async def test_broken_registry_fails_closed(v2):
    a = v2.gate("caesar")
    write_registry(v2.regp, "garbage [[[")
    with pytest.raises(BridgeError) as e:
        await a.start("claude", "x", None, str(v2.ws))
    assert code(e) == "unauthenticated"


async def test_list_agents_scoped_to_ceiling(v2):
    v2.registry(principals={"caesar": {"agents": ["codex"]}})
    out = v2.gate("caesar").list_agents()
    assert [x["id"] for x in out["agents"]] == ["codex"]
    assert "state_dir" not in json.dumps(out)


# ---- audit -----------------------------------------------------------------------

async def test_audit_rows_allowlisted(v2):
    a = v2.gate("caesar")
    r = await a.start("claude", "SECRET-TASK-TEXT", None, str(v2.ws))
    await done(a, r["task_id"])
    with pytest.raises(BridgeError):
        await a.start("claude", "x", None, str(v2.ws), access="write")
    rows = [dict(x) for x in v2.sup.store.db.execute("SELECT * FROM audit_events ORDER BY seq")]
    assert {r["outcome"] for r in rows} >= {"admitted", "access_denied"}
    assert all(r["principal_id"] == "caesar" for r in rows)
    assert "SECRET-TASK-TEXT" not in json.dumps(rows) and str(v2.ws) not in json.dumps(rows)
