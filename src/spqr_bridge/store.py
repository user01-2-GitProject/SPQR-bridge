"""SQLite job metadata in a private, exclusively locked state directory.

Records persist across restarts; execution does not. On startup any queued/running record
left by a previous process is marked `interrupted` and never resumed.
"""

from __future__ import annotations

import fcntl
import os
import sqlite3
import time
from pathlib import Path

TERMINAL = ("succeeded", "failed", "cancelled", "timed_out", "interrupted")
ACTIVE = ("queued", "running")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  task_id TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL,
  access TEXT NOT NULL,
  workspace TEXT NOT NULL,
  timeout_s INTEGER NOT NULL,
  idem_key TEXT UNIQUE,
  request_hash TEXT NOT NULL,
  state TEXT NOT NULL,
  created_at REAL NOT NULL,
  started_at REAL,
  finished_at REAL,
  return_code INTEGER,
  result_text TEXT,
  result_truncated INTEGER DEFAULT 0,
  output_truncated INTEGER DEFAULT 0,
  error TEXT,
  stderr_tail TEXT,
  session_id TEXT,
  usage_json TEXT,
  permission_denials_json TEXT
)
"""
_FINISH_FIELDS = {"return_code", "result_text", "result_truncated", "output_truncated", "error",
                  "stderr_tail", "session_id", "usage_json", "permission_denials_json"}
_MIGRATIONS = {"permission_denials_json": "ALTER TABLE jobs ADD COLUMN permission_denials_json TEXT"}


class StateLockError(RuntimeError):
    pass


class Store:
    def __init__(self, state_dir: Path):
        os.umask(0o077)
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.state_dir, 0o700)
        self.tmp_dir = self.state_dir / "tmp"
        self.tmp_dir.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.tmp_dir, 0o700)
        lock_path = self.state_dir / "server.lock"
        self._lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self._lock_fd)
            raise StateLockError(f"state dir {self.state_dir} is in use by another bridge process; "
                                 "use a separate state_dir per server instance") from None
        db_path = self.state_dir / "jobs.sqlite3"
        fd = os.open(db_path, os.O_RDWR | os.O_CREAT, 0o600)
        os.close(fd)
        os.chmod(db_path, 0o600)
        self.db = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute(_SCHEMA)
        have = {r["name"] for r in self.db.execute("PRAGMA table_info(jobs)")}
        for col, ddl in _MIGRATIONS.items():
            if col not in have:
                self.db.execute(ddl)
        cur = self.db.execute(
            "UPDATE jobs SET state='interrupted', finished_at=?, "
            "error=COALESCE(error, 'interrupted: bridge server restarted before completion; not resumed') "
            "WHERE state IN ('queued','running')", (time.time(),))
        self.recovered = cur.rowcount

    def close(self) -> None:
        try:
            self.db.close()
        finally:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)

    def create(self, job: dict) -> None:
        self.db.execute(
            "INSERT INTO jobs (task_id, agent_id, access, workspace, timeout_s, idem_key, request_hash, "
            "state, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (job["task_id"], job["agent_id"], job["access"], job["workspace"], job["timeout_s"],
             job.get("idem_key"), job["request_hash"], job.get("state", "queued"), time.time()))

    def get(self, task_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM jobs WHERE task_id=?", (task_id,)).fetchone()
        return dict(row) if row else None

    def find_idempotent(self, key: str) -> dict | None:
        row = self.db.execute("SELECT * FROM jobs WHERE idem_key=?", (key,)).fetchone()
        return dict(row) if row else None

    def mark_running(self, task_id: str) -> bool:
        cur = self.db.execute("UPDATE jobs SET state='running', started_at=? WHERE task_id=? AND state='queued'",
                              (time.time(), task_id))
        return cur.rowcount == 1

    def finish(self, task_id: str, state: str, **fields) -> bool:
        """Set a terminal state once; later racing finishers are ignored (returns False)."""
        assert state in TERMINAL
        unknown = set(fields) - _FINISH_FIELDS
        if unknown:
            raise ValueError(f"unknown fields {unknown}")
        cols = ["state=?", "finished_at=?"] + [f"{k}=?" for k in fields]
        vals = [state, time.time(), *fields.values(), task_id]
        cur = self.db.execute(
            f"UPDATE jobs SET {', '.join(cols)} WHERE task_id=? AND state IN ('queued','running')", vals)
        return cur.rowcount == 1

    def prune(self, retain: int) -> int:
        cur = self.db.execute(
            "DELETE FROM jobs WHERE state IN ('succeeded','failed','cancelled','timed_out','interrupted') "
            "AND task_id NOT IN (SELECT task_id FROM jobs ORDER BY created_at DESC LIMIT ?)", (retain,))
        return cur.rowcount
