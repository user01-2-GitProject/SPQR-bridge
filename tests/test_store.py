"""Slice 3: SQLite job store, private state, exclusive lock, restart recovery."""

import stat

import pytest

from spqr_bridge.store import StateLockError, Store


def mode(p):
    return stat.S_IMODE(p.stat().st_mode)


def new_job(store, tid, state="queued", **kw):
    store.create(dict(task_id=tid, agent_id="claude", access="read_only", workspace="/w",
                      timeout_s=10, idem_key=kw.get("idem_key"), request_hash=kw.get("h", "h1"),
                      state=state))


def test_state_dir_and_db_private(tmp_path):
    s = Store(tmp_path / "state")
    assert mode(tmp_path / "state") == 0o700
    assert mode(tmp_path / "state" / "jobs.sqlite3") == 0o600
    assert mode(s.tmp_dir) == 0o700
    s.close()


def test_exclusive_lock(tmp_path):
    a = Store(tmp_path / "state")
    with pytest.raises(StateLockError):
        Store(tmp_path / "state")
    a.close()
    Store(tmp_path / "state").close()  # released on close


def test_restart_marks_inflight_interrupted_and_keeps_completed(tmp_path):
    s = Store(tmp_path / "state")
    new_job(s, "a", "queued")
    new_job(s, "b", "running")
    new_job(s, "c", "queued")
    assert s.finish("c", "succeeded", result_text="done", return_code=0)
    s.close()
    s2 = Store(tmp_path / "state")
    assert s2.recovered == 2
    assert s2.get("a")["state"] == "interrupted"
    assert s2.get("b")["state"] == "interrupted"
    assert s2.get("c")["state"] == "succeeded" and s2.get("c")["result_text"] == "done"
    s2.close()


def test_finish_only_once(tmp_path):
    s = Store(tmp_path / "state")
    new_job(s, "a", "running")
    assert s.finish("a", "cancelled") is True
    assert s.finish("a", "succeeded", result_text="late") is False
    assert s.get("a")["state"] == "cancelled"
    s.close()


def test_idempotency_lookup(tmp_path):
    s = Store(tmp_path / "state")
    new_job(s, "a", idem_key="k1", h="hashA")
    j = s.find_idempotent("k1")
    assert j["task_id"] == "a" and j["request_hash"] == "hashA"
    assert s.find_idempotent("missing") is None
    s.close()


def test_get_unknown_returns_none(tmp_path):
    s = Store(tmp_path / "state")
    assert s.get("nope'; DROP TABLE jobs;--") is None
    s.close()
