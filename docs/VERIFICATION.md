# Verification log — SPQR Agent Bridge

All results below are actual outputs from this session. They come from deterministic tests that
use **labelled FAKE agent executables** (`tests/fixtures/fake_agent.py` via `tests/conftest.py`
wrappers). These fakes emulate the CLI output formats only. **No live model was invoked** by the
implementer.

Environment: Python 3.14.8 (uv project venv), mcp 2.3.0, pydantic 2.14.1, pytest 9.1.1,
pytest-asyncio 1.4.0, uv 0.13.2.

## TDD record (vertical slices, test-first)
Each slice was one behavioural module. Its tests were written first and run to RED (the module
didn't exist or behaved differently), then the minimal implementation was written and re-run to
GREEN. The slices were coarser than one test at a time because of the 40-turn budget.

| # | Slice | RED (observed) | GREEN (observed) |
|---|---|---|---|
| 1 | config / path policy / env allowlist (`test_policy.py`) | `ModuleNotFoundError: No module named 'spqr_bridge.config'` — collection error | passed in the combined run below |
| 2 | adapters argv + parsing (`test_adapters.py`) | `ModuleNotFoundError: No module named 'spqr_bridge.adapters'` | slices 1–3 run: `39 passed, 1 error` (error = slice 4 RED) |
| 3 | SQLite store / lock / restart (`test_store.py`) | `ModuleNotFoundError: No module named 'spqr_bridge.store'` | same run: 39 passed |
| 4 | supervisor with real fake processes (`test_supervisor.py`) | `ModuleNotFoundError: No module named 'spqr_bridge.supervisor'` | `21 passed` (with slice 5 RED in the same run) |
| 5 | MCP stdio server, all five tools (`test_mcp_stdio.py`) | server subprocess: `No module named spqr_bridge.server` → `MCPError: Connection closed`; `1 failed, 21 passed` | first impl failed at `unknown_agent` (`UnexpectedToolError` masked the message; fixed by mapping `BridgeError`→`ToolError`), then `1 passed in 11.38s` |
| 6 | state dir may sit inside root; roots inside state rejected; workspace inside state rejected | `2 failed, 15 passed` (`state_dir must not be inside an allowed workspace root`) | full suite `62 passed, 1 skipped` |
| 7 | shipped configs conservative (`test_shipped_config.py`) | regression check, green on first run (not test-first) | included in final run |

## Final run
Command:
```
uv run --project "/home/jimmy/Projects (jimmy's)/SPQR" pytest -q "/home/jimmy/Projects (jimmy's)/SPQR/tests"
```
Output:
```
.................s...............................................        [100%]
64 passed, 1 skipped in 19.07s
```
The skipped test is `tests/test_live_claude.py` (gated by `SPQR_LIVE_SMOKE=1`).

## Coverage of required behaviours (test names)
- Schema/config validation: `test_config_*`, `test_omp_write_is_rejected`, `test_shipped_config_conservative`
- Native argv: `test_claude_read_only_argv`, `test_claude_write_argv_scoped_tools_no_bash`, `test_codex_argv_sandbox_modes`, `test_omp_argv_read_only_only`
- Prompt injection / leading flags: `test_prompt_injection_stays_in_stdin[*]`, and end-to-end in `test_success_echo_argv_env_stdin` (literal stdin, no `pwned` file)
- Path/symlink escapes: `test_workspace_*`
- Env secret exclusion: `test_env_excludes_secrets_and_sets_tmpdir`, plus end-to-end in a child process via `test_success_echo_argv_env_stdin`
- Unknown/disabled/missing agent, write denied: `test_start_rejections[*]`, `test_start_rejects_bad_workspace_timeout_and_depth`
- Async start/get + success parsing: `test_success_echo_argv_env_stdin`, `test_codex_success`
- Nonzero / semantic error / malformed / empty: `test_failures[*]` (also secret scrubbing of stderr)
- Output caps: `test_output_cap_kills`
- Timeout + descendant cleanup: `test_timeout_kills_descendants` (checks the child and grandchild PIDs are gone)
- Queued/running cancel, stdin-blocked cancel: `test_cancel_running_and_queued`, `test_cancel_while_stdin_blocked`
- Concurrency / writer lease: `test_writer_lease_serialises_writes`, `test_readers_run_concurrently`, `test_admission_limit`
- Idempotency mismatch: `test_idempotency`, `test_idempotency_lookup`
- Persisted terminal results / restart interrupted / shutdown: `test_restart_marks_inflight_interrupted_and_keeps_completed`, `test_shutdown_interrupts_and_kills`, `test_finish_only_once`
- Exclusive state lock: `test_exclusive_lock`, and while a real MCP server runs, in `test_mcp_stdio_all_tools`
- MCP stdio initialisation, discovery and all five tools: `test_mcp_stdio_all_tools`
- Private state perms (0700/0600): `test_state_dir_and_db_private`

## PNG preservation (sha256, before and after: identical)
```
a20946d282fed5e90a7ecad7c81eb48bcb55c7a469716d3a1a43ff2153ae7056  Roman AI Empire Organizational Chart.png
dc97790e04b374a93fc0fb0d7e5bf026a171f5cc81b72bac12a639596badc187  Roman AI Senate Workflow Dashboard.png
```

## Not verified by the implementer
- **Live Claude via the bridge:** not run (forbidden for the implementer). Run `sh scripts/live_smoke.sh`.
- **Installed CLI flags:** the sandbox denied running `claude --help`, `codex exec --help` and
  `omp --help`. The argv follows the researched brief. `doctor` now reports `flags_not_advertised`,
  an informational help-text scan. The parent's live run showed Claude accepts `--max-turns` even
  though `--help` omits it (parent evidence, kept private in `.verification/`, which is git-ignored).

## Hardening pass (release-blocking review)
See `docs/HARDENING.md` for the regression RED/GREEN record. Suite after hardening:
`91 passed, 1 skipped`. `live_verified` was not changed. The parent reruns the live smoke after integration.
- **Codex / OMP live:** unavailable (per Caesar's earlier observations) and disabled in the local registry.
- **Hermes integration:** not installed. See the README for manual registration.
