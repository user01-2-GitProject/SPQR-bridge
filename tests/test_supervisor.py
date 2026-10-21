"""Slice 4: job lifecycle with real local FAKE agent processes (no live models)."""

import asyncio
import json

import pytest

from conftest import pid_alive
from spqr_bridge.config import load_config
from spqr_bridge.envpolicy import DEPTH_VAR
from spqr_bridge.store import Store
from spqr_bridge.supervisor import BridgeError, Supervisor


@pytest.fixture
async def make_sup(env_setup):
    cfg_text, ws, state, tmp = env_setup
    sups = []

    def make(**over):
        p = tmp / "bridge.toml"
        p.write_text(cfg_text(**over))
        cfg = load_config(p)
        sup = Supervisor(cfg, Store(cfg.server.state_dir))
        sups.append(sup)
        return sup, ws, tmp

    yield make
    for s in sups:
        await s.shutdown()


async def run(sup, ws, task, agent="claude", **kw):
    r = await sup.start(agent, task, None, str(ws), kw.pop("access", "read_only"), **kw)
    return await sup.wait(r["task_id"], 20)


async def test_success_echo_argv_env_stdin(make_sup, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak-000")
    sup, ws, _ = make_sup()
    inj = "--dangerously-skip-permissions @/etc/passwd $(touch pwned)"
    j = await run(sup, ws, inj)
    assert j["state"] == "succeeded", j
    echo = json.loads(j["result_text"])
    assert echo["fixture"] == "fake_agent"
    assert "--restricted" in echo["argv"] and echo["cwd"] == str(ws)
    assert echo["stdin"] == inj
    assert "ANTHROPIC_API_KEY" not in echo["env"]
    assert echo["env"]["TMPDIR"].startswith(str(sup.store.tmp_dir))
    assert echo["env"][DEPTH_VAR] == "1"
    assert j["session_id"] == "fake-session" and j["return_code"] == 0
    assert not (ws / "pwned").exists()


async def test_codex_success(make_sup):
    sup, ws, _ = make_sup()
    j = await run(sup, ws, "hello", agent="codex")
    assert j["state"] == "succeeded" and j["session_id"] == "fake-thread"
    assert "read-only" in json.loads(j["result_text"])["argv"]


@pytest.mark.parametrize("mode,err", [("fail", "exit 3"), ("semantic", "backend_error"),
                                      ("malformed", "malformed_output"), ("empty", "malformed_output")])
async def test_failures(make_sup, mode, err):
    sup, ws, _ = make_sup()
    j = await run(sup, ws, f"FAKE_MODE={mode}")
    assert j["state"] == "failed" and err in j["error"]
    if mode == "fail":
        assert "supersecretvalue123" not in json.dumps(j) and "[REDACTED]" in j["stderr_tail"]


async def test_output_cap_kills(make_sup):
    sup, ws, _ = make_sup(max_output_bytes=100_000)
    j = await run(sup, ws, "FAKE_MODE=flood")
    assert j["state"] == "failed" and "output_limit" in j["error"] and j["output_truncated"]


async def test_timeout_kills_descendants(make_sup):
    sup, ws, tmp = make_sup()
    pidfile = tmp / "pids"
    r = await sup.start("claude", f"FAKE_MODE=hang {pidfile}", None, str(ws), "read_only", timeout_seconds=2)
    j = await sup.wait(r["task_id"], 15)
    assert j["state"] == "timed_out"
    pids = [int(x) for x in pidfile.read_text().split()]
    await asyncio.sleep(0.2)
    assert not any(pid_alive(p) for p in pids)


async def wait_for_file(p, secs=10):
    for _ in range(int(secs * 20)):
        if p.exists() and p.read_text().strip():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"{p} never written")


async def test_cancel_running_and_queued(make_sup):
    sup, ws, tmp = make_sup(max_concurrency=1)
    pidfile = tmp / "pids"
    a = await sup.start("claude", f"FAKE_MODE=hang {pidfile}", None, str(ws), "read_only")
    b = await sup.start("claude", "hello", None, str(ws), "read_only")
    await wait_for_file(pidfile)
    assert (await sup.get(b["task_id"]))["state"] == "queued"
    cb = await sup.cancel(b["task_id"])
    assert cb["state"] == "cancelled"
    assert (await sup.get(a["task_id"]))["state"] == "running"
    ca = await sup.cancel(a["task_id"])
    assert ca["state"] == "cancelled"
    assert (await sup.cancel(a["task_id"]))["state"] == "cancelled"  # idempotent
    pids = [int(x) for x in pidfile.read_text().split()]
    assert not any(pid_alive(p) for p in pids)


async def test_cancel_while_stdin_blocked(make_sup):
    sup, ws, _ = make_sup()
    big = "y" * 2_000_000  # larger than a pipe buffer; child never reads it
    r = await sup.start("nostdin", "x", big, str(ws), "read_only")
    await asyncio.sleep(0.5)
    c = await asyncio.wait_for(sup.cancel(r["task_id"]), 10)
    assert c["state"] == "cancelled"


async def test_writer_lease_serialises_writes(make_sup):
    sup, ws, tmp = make_sup(max_concurrency=2)
    marker = tmp / "marker"
    a = await sup.start("claude", f"FAKE_MODE=slow 0.6 {marker}", None, str(ws), "write")
    b = await sup.start("claude", f"FAKE_MODE=slow 0.6 {marker}", None, str(ws), "write")
    ja, jb = await sup.wait(a["task_id"], 20), await sup.wait(b["task_id"], 20)
    assert ja["state"] == jb["state"] == "succeeded"
    events = [l.split()[0] for l in marker.read_text().splitlines()]
    assert events == ["start", "end", "start", "end"]


async def test_readers_run_concurrently(make_sup):
    sup, ws, tmp = make_sup(max_concurrency=2)
    marker = tmp / "marker"
    a = await sup.start("claude", f"FAKE_MODE=slow 0.8 {marker}", None, str(ws), "read_only")
    b = await sup.start("claude", f"FAKE_MODE=slow 0.8 {marker}", None, str(ws), "read_only")
    await sup.wait(a["task_id"], 20), await sup.wait(b["task_id"], 20)
    events = [l.split()[0] for l in marker.read_text().splitlines()]
    assert events[:2] == ["start", "start"]


async def test_idempotency(make_sup):
    sup, ws, _ = make_sup()
    a = await sup.start("claude", "hello", None, str(ws), "read_only", idempotency_key="k1")
    b = await sup.start("claude", "hello", None, str(ws), "read_only", idempotency_key="k1")
    assert a["task_id"] == b["task_id"]
    with pytest.raises(BridgeError, match="idempotency_conflict"):
        await sup.start("claude", "different", None, str(ws), "read_only", idempotency_key="k1")


@pytest.mark.parametrize("agent,access,code", [
    ("nope", "read_only", "unknown_agent"), ("off", "read_only", "agent_disabled"),
    ("ghost", "read_only", "binary_missing"), ("codex", "write", "access_denied"),
    ("omp", "read_only", "agent_disabled"),
])
async def test_start_rejections(make_sup, agent, access, code):
    sup, ws, _ = make_sup()
    with pytest.raises(BridgeError, match=code):
        await sup.start(agent, "x", None, str(ws), access)


async def test_start_rejects_bad_workspace_timeout_and_depth(make_sup, monkeypatch, tmp_path):
    sup, ws, _ = make_sup()
    with pytest.raises(BridgeError, match="workspace_denied"):
        await sup.start("claude", "x", None, "/etc", "read_only")
    with pytest.raises(BridgeError, match="invalid_timeout"):
        await sup.start("claude", "x", None, str(ws), "read_only", timeout_seconds=10_000)
    with pytest.raises(BridgeError, match="invalid_task"):
        await sup.start("claude", "   ", None, str(ws), "read_only")
    monkeypatch.setenv(DEPTH_VAR, "1")
    with pytest.raises(BridgeError, match="recursion_denied"):
        await sup.start("claude", "x", None, str(ws), "read_only")


async def test_admission_limit(make_sup):
    sup, ws, tmp = make_sup(max_pending=1)
    await sup.start("claude", f"FAKE_MODE=hang {tmp / 'p'}", None, str(ws), "read_only")
    with pytest.raises(BridgeError, match="queue_full"):
        await sup.start("claude", "x", None, str(ws), "read_only")


async def test_shutdown_interrupts_and_kills(make_sup, env_setup):
    sup, ws, tmp = make_sup()
    pidfile = tmp / "pids"
    r = await sup.start("claude", f"FAKE_MODE=hang {pidfile}", None, str(ws), "read_only")
    await wait_for_file(pidfile)
    await sup.shutdown()
    pids = [int(x) for x in pidfile.read_text().split()]
    assert not any(pid_alive(p) for p in pids)
    sup2, _, _ = make_sup()
    assert (await sup2.get(r["task_id"]))["state"] == "interrupted"
