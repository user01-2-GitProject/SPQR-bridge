"""Hardening regressions from the release-blocking review (Linux only).

Every process here is the labelled FAKE agent fixture (tests/fixtures/fake_agent.py); no live models.
Each test reproduces a reviewed defect behaviourally and was run RED before the fix (docs/HARDENING.md).
"""

import asyncio
import json
import os
import signal

import pytest

import spqr_bridge.supervisor as supmod
from conftest import make_wrapper, pid_alive
from spqr_bridge import server as srv
from spqr_bridge.adapters import parse_output
from spqr_bridge.config import load_config
from spqr_bridge.envpolicy import build_env
from spqr_bridge.store import Store
from spqr_bridge.supervisor import Supervisor

try:
    import spqr_bridge.procs as procs
except ImportError:  # pre-hardening code had no shared process helper
    procs = None


@pytest.fixture
def reap():
    """Track fixture PIDs and SIGKILL any survivors at teardown so a RED run leaks nothing."""
    tracked: list[int] = []

    def add(pids):
        tracked.extend(pids)
        return pids

    yield add
    for p in tracked:
        try:
            os.kill(p, signal.SIGKILL)
        except ProcessLookupError:
            pass


@pytest.fixture
async def make_sup(env_setup):
    cfg_text, ws, state, tmp = env_setup
    stub = make_wrapper(tmp / "bin" / "stubborn", f'--fixture-stubborn "{tmp / "spids"}"')
    sups = []

    def make(**over):
        p = tmp / "bridge.toml"
        p.write_text(cfg_text(**over) + f'\n[agents.stubborn]\nadapter = "claude"\nbinary = "{stub}"\n'
                     "enabled = true\n")
        cfg = load_config(p)
        sup = Supervisor(cfg, Store(cfg.server.state_dir))
        sups.append(sup)
        return sup, ws, tmp

    yield make
    for s in sups:
        await s.shutdown()


async def wait_pids(p, secs=10):
    for _ in range(int(secs * 20)):
        if p.exists() and len(p.read_text().split()) == 2:
            return [int(x) for x in p.read_text().split()]
        await asyncio.sleep(0.05)
    raise AssertionError(f"{p} never written")


def alive(pids):
    return [p for p in pids if pid_alive(p)]


def gate_loop(monkeypatch, name):
    """Block the event loop's pipe connection step, i.e. after the native fork/exec succeeded."""
    loop = asyncio.get_running_loop()
    gate, orig = asyncio.Event(), getattr(loop, name)

    async def gated(*a, **k):
        await gate.wait()
        return await orig(*a, **k)

    monkeypatch.setattr(loop, name, gated)
    return gate


# ---- 1. queued path substitution ------------------------------------------------------------

async def test_queued_workspace_substitution_rejected(make_sup, reap):
    sup, ws, tmp = make_sup(max_concurrency=1)
    proj, outside = ws / "project", tmp / "outside"
    proj.mkdir()
    outside.mkdir()
    a = await sup.start("claude", f"FAKE_MODE=hang {tmp / 'p'}", None, str(ws), "read_only")
    reap(await wait_pids(tmp / "p"))
    b = await sup.start("claude", "hello", None, str(proj), "read_only")
    assert (await sup.get(b["task_id"]))["state"] == "queued"
    proj.rename(ws / "moved")
    proj.symlink_to(outside)
    await sup.cancel(a["task_id"])
    j = await sup.wait(b["task_id"], 10)
    assert j["state"] == "failed" and "workspace_changed" in (j["error"] or ""), j


async def test_swap_between_final_check_and_spawn_uses_pinned_dir(make_sup, monkeypatch):
    sup, ws, tmp = make_sup()
    proj, outside = ws / "project", tmp / "outside"
    proj.mkdir()
    outside.mkdir()
    ino = proj.stat().st_ino
    orig = asyncio.create_subprocess_exec

    async def swap_then_spawn(*a, **k):  # runs after every bridge-side check, right before fork/exec
        if not proj.is_symlink():
            proj.rename(ws / "moved")
            proj.symlink_to(outside)
        return await orig(*a, **k)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", swap_then_spawn)
    r = await sup.start("claude", "hello", None, str(proj), "read_only")
    j = await sup.wait(r["task_id"], 20)
    assert j["state"] == "succeeded", j
    cwd = json.loads(j["result_text"])["cwd"]
    assert os.stat(cwd).st_ino == ino and not cwd.startswith(str(outside)), cwd


# ---- 2. repeated / overlapping cancellation ------------------------------------------------

async def test_concurrent_cancel_terminal_only_after_group_dead(make_sup, reap):
    sup, ws, tmp = make_sup()
    r = await sup.start("stubborn", "x", None, str(ws), "read_only")
    pids = reap(await wait_pids(tmp / "spids"))
    first = asyncio.create_task(sup.cancel(r["task_id"]))
    await asyncio.sleep(0.3)  # second request lands while the first is mid group-kill (SIGTERM ignored)
    second = await asyncio.wait_for(sup.cancel(r["task_id"]), 15)
    assert alive(pids) == [], "cancel returned while the SIGTERM-ignoring group was still alive"
    assert second["state"] == (await asyncio.wait_for(first, 15))["state"] == "cancelled"
    assert alive(pids) == []


async def test_cancelled_requester_and_replacement_writer(make_sup, reap):
    sup, ws, tmp = make_sup()
    r = await sup.start("stubborn", "x", None, str(ws), "read_only")
    pids = reap(await wait_pids(tmp / "spids"))
    c = asyncio.create_task(sup.cancel(r["task_id"]))
    await asyncio.sleep(0.2)
    c.cancel()
    marker = tmp / "marker"
    w = await sup.start("claude", f"FAKE_MODE=slow 0.1 {marker}", None, str(ws), "write")
    for _ in range(300):
        if not alive(pids):
            break
        assert not (await sup.get(r["task_id"]))["terminal"]
        assert not marker.exists(), "writer started while the cancelled group was still alive"
        await asyncio.sleep(0.05)
    assert alive(pids) == []
    assert (await sup.wait(w["task_id"], 15))["state"] == "succeeded"
    assert (await sup.get(r["task_id"]))["state"] == "cancelled"


async def test_shutdown_overlapping_cancel(make_sup, reap):
    sup, ws, tmp = make_sup()
    r = await sup.start("stubborn", "x", None, str(ws), "read_only")
    pids = reap(await wait_pids(tmp / "spids"))
    c = asyncio.create_task(sup.cancel(r["task_id"]))
    await asyncio.sleep(0.1)
    await asyncio.wait_for(sup.shutdown(), 15)
    assert alive(pids) == []
    await asyncio.gather(c, return_exceptions=True)
    s2 = Store(sup.s.state_dir)
    assert s2.get(r["task_id"])["state"] in ("cancelled", "interrupted")
    s2.close()


# ---- 3. stop while the native spawn is still connecting pipes -------------------------------

async def test_cancel_during_spawn_keeps_ownership(make_sup, reap, monkeypatch):
    sup, ws, tmp = make_sup()
    gate = gate_loop(monkeypatch, "connect_write_pipe")
    r = await sup.start("stubborn", "x", None, str(ws), "read_only")
    pids = reap(await wait_pids(tmp / "spids"))
    c = asyncio.create_task(sup.cancel(r["task_id"]))
    await asyncio.sleep(0.3)
    gate.set()
    res = await asyncio.wait_for(c, 15)
    assert res["state"] == "cancelled" and alive(pids) == []


# ---- 4. overlapping workspaces / writer fairness ---------------------------------------------

async def test_nested_workspace_writers_serialised(make_sup):
    sup, ws, tmp = make_sup(max_concurrency=2)
    (ws / "sub").mkdir()
    marker = tmp / "marker"
    a = await sup.start("claude", f"FAKE_MODE=slow 0.6 {marker}", None, str(ws), "write")
    b = await sup.start("claude", f"FAKE_MODE=slow 0.6 {marker}", None, str(ws / "sub"), "write")
    await sup.wait(a["task_id"], 20), await sup.wait(b["task_id"], 20)
    assert [l.split()[0] for l in marker.read_text().splitlines()] == ["start", "end", "start", "end"]


async def test_waiting_writer_blocks_new_readers(make_sup):
    sup, ws, tmp = make_sup(max_concurrency=2)
    ma, mw, mc = tmp / "ma", tmp / "mw", tmp / "mc"
    a = await sup.start("claude", f"FAKE_MODE=slow 0.8 {ma}", None, str(ws), "read_only")
    await asyncio.sleep(0.3)
    w = await sup.start("claude", f"FAKE_MODE=slow 0.3 {mw}", None, str(ws), "write")
    c = await sup.start("claude", f"FAKE_MODE=slow 0.1 {mc}", None, str(ws), "read_only")
    for t in (a, w, c):
        assert (await sup.wait(t["task_id"], 20))["state"] == "succeeded"
    t = {k: {l.split()[0]: float(l.split()[1]) for l in p.read_text().splitlines()}
         for k, p in (("a", ma), ("w", mw), ("c", mc))}
    assert t["w"]["start"] >= t["a"]["end"] and t["c"]["start"] >= t["w"]["end"], t


# ---- 5. runner exceptions -----------------------------------------------------------------

async def test_bad_metadata_is_failed_not_stuck(make_sup):
    sup, ws, _ = make_sup()
    r = await sup.start("claude", "FAKE_MODE=badmeta", None, str(ws), "read_only")
    j = await sup.wait(r["task_id"], 10)
    assert j["state"] == "failed" and "malformed_output" in j["error"], j


async def test_parser_exception_is_scrubbed_failure_and_group_gone(make_sup, monkeypatch):
    sup, ws, tmp = make_sup()

    def boom(*a, **k):
        raise RuntimeError("parser exploded sk-ant-abcdefghijklmnopqrst")

    monkeypatch.setattr(supmod, "parse_output", boom)
    r = await sup.start("claude", f"FAKE_MODE=orphan {tmp / 'o'}", None, str(ws), "read_only")
    j = await sup.wait(r["task_id"], 10)
    pids = [int(x) for x in (tmp / "o").read_text().split()]
    assert j["state"] == "failed" and "internal_error" in j["error"], j
    assert "abcdefghijklmnop" not in j["error"]
    assert alive(pids) == []


async def test_pipeline_exception_kills_group(make_sup, monkeypatch, reap):
    sup, ws, tmp = make_sup()

    async def bad_drain(stream, buf, cap_hit):  # fail once the live group (and its PIDs) exists
        await wait_pids(tmp / "p")
        raise RuntimeError("reader failed")

    for mod in (supmod, procs):
        if mod is not None:
            monkeypatch.setattr(mod, "_drain", bad_drain, raising=False)
    r = await sup.start("claude", f"FAKE_MODE=hang {tmp / 'p'}", None, str(ws), "read_only", timeout_seconds=30)
    pids = reap(await wait_pids(tmp / "p"))
    j = await sup.wait(r["task_id"], 10)
    assert j["state"] == "failed" and "internal_error" in j["error"], j
    assert alive(pids) == []


# ---- 6. usage JSON and permission denials ---------------------------------------------------

async def test_large_usage_is_valid_bounded_json(make_sup):
    sup, ws, _ = make_sup()
    r = await sup.start("claude", "FAKE_MODE=bigusage", None, str(ws), "read_only")
    j = await sup.wait(r["task_id"], 10)
    assert j["state"] == "succeeded"
    assert j["usage"]["input_tokens"] == 7 and j["usage"]["total_cost_usd"] == 0.5
    assert "k0" not in j["usage"] and len(json.dumps(j["usage"])) < 1000


async def test_stored_invalid_usage_read_defensively(make_sup):
    sup, ws, _ = make_sup()
    sup.store.create(dict(task_id="t_old", agent_id="claude", access="read_only", workspace=str(ws),
                          timeout_s=5, idem_key=None, request_hash="h"))
    sup.store.finish("t_old", "succeeded", usage_json='{"input_tokens": 1, "trunc')
    g = await sup.get("t_old")
    assert g["usage"] is None and "usage_unreadable" in g["warnings"]


async def test_permission_denials_visible_and_bounded(make_sup):
    sup, ws, _ = make_sup()
    r = await sup.start("claude", "FAKE_MODE=denied", None, str(ws), "read_only")
    j = await sup.wait(r["task_id"], 10)
    d = j["permission_denials"]
    assert d["count"] == 100 and len(d["items"]) <= 20 and d["truncated"]
    assert d["items"][0]["tool_name"] == "Bash" and len(json.dumps(d)) < 20_000


# ---- 7. false success ----------------------------------------------------------------------

@pytest.mark.parametrize("out", [b"{}", b'{"type":"system","subtype":"init"}',
                                 b'{"type":"result","subtype":"success","is_error":false}',
                                 b'{"type":"result","subtype":"success","result":"x"}',
                                 b'{"type":"result","subtype":"success","is_error":false,"result":""}',
                                 b'{"type":"result","subtype":"success","is_error":false,"result":"x","session_id":5}'])
def test_claude_requires_typed_success_envelope(out):
    assert not parse_output("claude", out, 0).ok


@pytest.mark.parametrize("lines", [
    ['{"type":"item.completed","item":{"type":"agent_message","text":"partial"}}'],
    ['{"type":"item.completed","item":{"type":"agent_message","text":"a"}}', '{"type":"turn.completed"}',
     '{"type":"error","message":"late failure"}'],
    ['{"type":"turn.completed","usage":{}}'],
])
def test_codex_requires_completed_turn_and_text(lines):
    assert not parse_output("codex", "\n".join(lines).encode(), 0).ok


async def test_system_object_and_progress_only_fail_end_to_end(make_sup):
    sup, ws, _ = make_sup()
    for agent, mode in (("claude", "sysobj"), ("codex", "progressonly")):
        r = await sup.start(agent, f"FAKE_MODE={mode}", None, str(ws), "read_only")
        j = await sup.wait(r["task_id"], 10)
        assert j["state"] == "failed", (agent, j)


# ---- 8. doctor probe cancellation --------------------------------------------------------

async def _cancelled_probe(tmp, reap, gate=None):
    stub = make_wrapper(tmp / "probe-stub", f'--fixture-stubborn "{tmp / "probe-pids"}"')
    env = build_env(tmp, [])
    t = asyncio.create_task(srv._probe([str(stub), "--version"], 30, env))
    pids = reap(await wait_pids(tmp / "probe-pids"))
    t.cancel()
    if gate is not None:
        await asyncio.sleep(0.2)
        gate.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(t, 15)
    assert alive(pids) == []


async def test_cancelled_probe_kills_group(tmp_path, reap):
    await _cancelled_probe(tmp_path, reap)


async def test_probe_cancelled_during_spawn_kills_group(tmp_path, reap, monkeypatch):
    await _cancelled_probe(tmp_path, reap, gate_loop(monkeypatch, "connect_read_pipe"))


# ---- 9. bounded control state and fds -----------------------------------------------------

async def test_control_maps_and_fds_bounded(make_sup):
    sup, ws, tmp = make_sup(retain_jobs=10)
    fds0 = len(os.listdir("/proc/self/fd"))
    for _ in range(25):
        r = await sup.start("claude", "hello", None, str(ws), "read_only")
        assert (await sup.wait(r["task_id"], 10))["state"] == "succeeded"
    for i in range(8):
        r = await sup.start("claude", f"FAKE_MODE=hang {tmp / f'h{i}'}", None, str(ws), "read_only")
        await wait_pids(tmp / f"h{i}")
        assert (await sup.cancel(r["task_id"]))["state"] == "cancelled"
    a = await sup.start("claude", "hello", None, str(ws), "read_only", idempotency_key="dup")
    await sup.start("claude", "hello", None, str(ws), "read_only", idempotency_key="dup")
    await sup.wait(a["task_id"], 10)
    maps = {k: len(v) for k, v in vars(sup).items() if isinstance(v, (dict, list, set))}
    assert sum(maps.values()) == 0, maps
    assert len(os.listdir("/proc/self/fd")) <= fds0
