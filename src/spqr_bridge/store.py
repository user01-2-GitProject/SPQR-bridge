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

TERMINAL = ("succeeded", "failed", "cancelled", "timed_out", "interrupted", "usage_exhausted")
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
_MIGRATIONS = {"permission_denials_json": "ALTER TABLE jobs ADD COLUMN permission_denials_json TEXT",
               # v2 ownership (NULL = legacy v1 row, invisible to every principal)
               "principal_id": "ALTER TABLE jobs ADD COLUMN principal_id TEXT",
               "grant_id": "ALTER TABLE jobs ADD COLUMN grant_id TEXT",
               "scope_json": "ALTER TABLE jobs ADD COLUMN scope_json TEXT"}
_V2_SCHEMA = (
    # durable tombstones: survive job pruning, so a key is never reused
    """CREATE TABLE IF NOT EXISTS idempotency_receipts (
      principal_id TEXT NOT NULL, idem_key TEXT NOT NULL, request_hash TEXT NOT NULL,
      task_id TEXT NOT NULL, created_at REAL NOT NULL, UNIQUE (principal_id, idem_key))""",
    # allowlisted fields only: never task text, paths, tokens or digests
    """CREATE TABLE IF NOT EXISTS audit_events (
      seq INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, principal_id TEXT NOT NULL,
      tool TEXT NOT NULL, outcome TEXT NOT NULL, task_id TEXT, agent_id TEXT, grant_id TEXT)""",
    # durable usage stops: survive job pruning; cleared only by Jimmy's [usage_cleared] line
    "CREATE TABLE IF NOT EXISTS usage_stops (agent_id TEXT NOT NULL, at REAL NOT NULL, task_id TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS jobs_lineage ON jobs (principal_id, grant_id, agent_id, created_at)",
)
_V2_COLUMNS = ("principal_id", "grant_id", "scope_json")


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
        for ddl in _V2_SCHEMA:
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
        """Insert a job; v2 ownership columns and an idempotency receipt go in the same transaction."""
        now = time.time()
        extra = [c for c in _V2_COLUMNS if job.get(c) is not None]
        cols = ["task_id", "agent_id", "access", "workspace", "timeout_s", "idem_key", "request_hash",
                "state", "created_at", *extra]
        vals = [job["task_id"], job["agent_id"], job["access"], job["workspace"], job["timeout_s"],
                job.get("idem_key"), job["request_hash"], job.get("state", "queued"), now,
                *(job[c] for c in extra)]
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute(f"INSERT INTO jobs ({', '.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
            if job.get("receipt_key") is not None:
                self.db.execute(
                    "INSERT INTO idempotency_receipts (principal_id, idem_key, request_hash, task_id, created_at) "
                    "VALUES (?,?,?,?,?)",
                    (job["principal_id"], job["receipt_key"], job["request_hash"], job["task_id"], now))
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")

    # ---- v2 queries (all scoped by principal where they return job data) ----
    def receipt(self, principal_id: str, key: str) -> dict | None:
        row = self.db.execute("SELECT * FROM idempotency_receipts WHERE principal_id=? AND idem_key=?",
                              (principal_id, key)).fetchone()
        return dict(row) if row else None

    def lineage(self, principal_id: str, grant_id: str | None, agent_id: str, workspace: str,
                since: float) -> list[dict]:
        """Jobs in one loop lineage, newest first: the grant, or principal+agent+workspace since `since`."""
        if grant_id is not None:
            q, a = "principal_id=? AND grant_id=?", (principal_id, grant_id)
        else:
            q, a = ("principal_id=? AND grant_id IS NULL AND agent_id=? AND workspace=? AND created_at>?",
                    (principal_id, agent_id, workspace, since))
        return [dict(r) for r in self.db.execute(
            f"SELECT state, result_text, error FROM jobs WHERE {q} ORDER BY created_at DESC, rowid DESC", a)]

    def usage_stopped(self, agent_id: str, cleared_at: float) -> bool:
        return self.db.execute("SELECT 1 FROM usage_stops WHERE agent_id=? AND at>? LIMIT 1",
                               (agent_id, cleared_at)).fetchone() is not None

    def audit(self, principal_id: str, tool: str, outcome: str, task_id: str | None = None,
              agent_id: str | None = None, grant_id: str | None = None) -> None:
        self.db.execute("INSERT INTO audit_events (at, principal_id, tool, outcome, task_id, agent_id, grant_id) "
                        "VALUES (?,?,?,?,?,?,?)", (time.time(), principal_id, tool, outcome, task_id, agent_id,
                                                   grant_id))

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
        now = time.time()
        cols = ["state=?", "finished_at=?"] + [f"{k}=?" for k in fields]
        vals = [state, now, *fields.values(), task_id]
        self.db.execute("BEGIN IMMEDIATE")
        try:
            cur = self.db.execute(
                f"UPDATE jobs SET {', '.join(cols)} WHERE task_id=? AND state IN ('queued','running')", vals)
            if cur.rowcount == 1 and state == "usage_exhausted":
                self.db.execute("INSERT INTO usage_stops (agent_id, at, task_id) "
                                "SELECT agent_id, ?, task_id FROM jobs WHERE task_id=?", (now, task_id))
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")
        return cur.rowcount == 1

    def prune(self, retain: int) -> int:
        """v2 rows younger than a day (the longest grant) are kept: loop caps read them."""
        cur = self.db.execute(
            "DELETE FROM jobs WHERE state IN ('succeeded','failed','cancelled','timed_out','interrupted','usage_exhausted') "
            "AND NOT (principal_id IS NOT NULL AND created_at > ?) "
            "AND task_id NOT IN (SELECT task_id FROM jobs ORDER BY created_at DESC LIMIT ?)",
            (time.time() - 86400, retain))
        return cur.rowcount
