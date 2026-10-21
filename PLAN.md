# PLAN — SPQR Agent Bridge (first release)

Goal: a local **stdio MCP facade** (`spqr-bridge`) letting Caesar start bounded tasks on
independently installed agent CLIs (Claude Code, Codex, OMP) using their real agent loops.

## Scope (in)
- Python >=3.12, official `mcp` SDK 2.3.0 (`from mcp.server import MCPServer`), stdlib
  `tomllib`, `sqlite3`, `asyncio` subprocesses; uv project env + `uv.lock`.
- Five tools: `list_agents`, `start_task`, `get_task`, `cancel_task`, `doctor`.
- TOML registry (trusted operator file chosen at startup), allowed workspace roots,
  per-agent access policy (`read_only` / `write`), no caller-controlled argv/env/model.
- Job store (SQLite, private state dir) with states queued/running/succeeded/failed/
  cancelled/timed_out/interrupted; restart marks queued/running as interrupted.
- Supervisor: process-group spawn, SIGTERM→SIGKILL, timeout, output byte caps,
  concurrency limit (default 2), admission limit, exclusive writer lease per workspace,
  idempotency keys, exclusive state-dir lock (`fcntl.flock`).
- Adapters: Claude (`-p --output-format json`, restricted read tools), Codex
  (`exec --json` with native sandbox, `--ignore-user-config`, ephemeral), OMP (disabled by
  default; no enforced file restriction).

## Scope (out)
HTTP listener, A2A, ACP, Redis, Docker, dashboards, automatic retries/fallback, durable
execution, OpenCode (deferred: mise shim missing node runtime), Hermes-to-Hermes collaboration
(use native `delegate_task` / `message_agent` / Kanban).

## Module layout
```
src/spqr_bridge/
  config.py     # TOML load + validation (pydantic)
  paths.py      # workspace canonicalisation / escape checks
  adapters.py   # argv builders + output parsers per backend
  envpolicy.py  # minimal env allowlist, depth guard
  store.py      # SQLite job store + state lock
  supervisor.py # process lifecycle, caps, leases, cancel
  server.py     # MCPServer tools, main()
```

## Method
Strict vertical TDD: one failing test → observe RED → minimal code → GREEN → next behaviour.
Evidence recorded in `docs/VERIFICATION.md`. Tests use **labelled fake executables**
(`tests/fixtures/fake_agent.py`) — never live models. One gated live Claude smoke
(`SPQR_LIVE_SMOKE=1`) is provided for Caesar to run; not executed by the implementer.

## Acceptance
`uv run pytest` green; MCP stdio test initialises the server and calls all five tools;
PNGs unchanged (sha256 recorded in VERIFICATION.md).

## Hardening gate (release-blocking repair, before activation)
Scope: fix nine reviewed defects without changing the architecture or framework. Linux only
(`/proc/self/fd`, `fcntl`, process groups). Claude stays read_only-only. Codex, OMP and writes
stay disabled. The work makes no live model requests and does not touch Hermes.

1. Queued path substitution: pin the admitted workspace as an O_DIRECTORY fd and check its
   canonical path and dev/ino. Revalidate before spawn and launch via `/proc/self/fd/N` (pass_fds).
2. Repeated cancellation: one idempotent stop event per job. The runner is never
   `Task.cancel`led. Terminal state is published and lease/slot are released only after group cleanup.
3. Cancel during spawn: the spawn runs in an owned inner task. A stop waits for the Process,
   then kills the group.
4. Overlapping leases: a single global FIFO reader/writer lease (writes serialised globally).
5. Runner exceptions: normalise metadata. A broad exception boundary records a scrubbed
   `failed` state. Persistence failure marks the service failed.
6. Usage JSON: whitelisted numeric usage, always valid JSON, defensive reads. Bounded
   `permission_denials` column.
7. False success: strict Claude result envelope and Codex completed turn plus final text.
8. Doctor probe: shared owned-process helper (`procs.py`), cancellation-safe.
   `flags_not_advertised` replaces `required_flags_missing`.
9. Unbounded memory: job control maps are pruned at terminal state. No per-path lease dict.

Method: regression tests in `tests/test_hardening.py` are run RED against the old code (behavioural
failures), then the fixes are applied and the tests re-run GREEN. Record: `docs/HARDENING.md`.
