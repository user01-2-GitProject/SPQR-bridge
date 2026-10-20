# Hardening record — release-blocking review repair

Scope: fix the nine reviewed defects without changing the architecture. **Linux only.**
All processes are the labelled FAKE fixture (`tests/fixtures/fake_agent.py`). No live model
requests were made, Hermes was not touched, and `live_verified` was not changed.

## Method (honest account)
- The original build's tests were *not* one-test-at-a-time TDD. Their RED was a coarse
  module-missing collection error (see `VERIFICATION.md`).
- For this pass, all regression tests were written first in `tests/test_hardening.py`, plus one
  added assertion block in `tests/test_mcp_stdio.py`. They were run against the **unmodified** code
  as one batch RED run, then fixed together and run GREEN. Every RED below is a behavioural
  assertion or crash in the old code, not an import or collection error (the optional `procs`
  import is guarded).
- Tests and fixes were batched rather than interleaved per defect because of the turn budget.

## RED against the old code (observed)
Command: `uv run --project "/home/jimmy/Projects (jimmy's)/SPQR" pytest -q -p no:cacheprovider --tb=line -rf tests/test_hardening.py tests/test_mcp_stdio.py`
Result: `23 failed, 5 passed in 31.41s`

| # | Test | Old-code failure |
|---|---|---|
| 1 | `test_queued_workspace_substitution_rejected` | state `succeeded` (it ran in the swapped-in outside dir), expected `failed workspace_changed` |
| 1 | `test_swap_between_final_check_and_spawn_uses_pinned_dir` | child cwd was `.../outside` (inode 17018615 ≠ admitted 17018614) |
| 2 | `test_concurrent_cancel_terminal_only_after_group_dead` | first version **passed** (both cancels landed in the same tick). Staggered by 0.3 s like the reviewer's repro, then RED: "cancel returned while the SIGTERM-ignoring group was still alive" `[309784, 309785]` |
| 2 | `test_shutdown_overlapping_cancel` | shutdown returned with PIDs `[309512, 309513]` alive |
| 2 | `test_cancelled_requester_and_replacement_writer` | **passed on old code** (cancelling the requester did not break the old runner). Kept as a guard, not counted as a reproduction |
| 3 | `test_cancel_during_spawn_keeps_ownership` | `cancelled` returned while descendant `[309515]` survived |
| 4 | `test_nested_workspace_writers_serialised` | marker `start,start,end,end` (two writers at once at `/ws` and `/ws/sub`) |
| 4 | `test_waiting_writer_blocks_new_readers` | late reader started at 31759.68, before the queued writer (started 31760.20) |
| 5 | `test_bad_metadata_is_failed_not_stuck` | stuck `running`; log: `sqlite3.ProgrammingError: Error binding parameter 6: type 'dict' is not supported`, "Task exception was never retrieved" |
| 5 | `test_parser_exception_is_scrubbed_failure_and_group_gone` | stuck `running`; unretrieved `RuntimeError` |
| 5 | `test_pipeline_exception_kills_group` | stuck `running` (reader error never observed) * |
| 6 | `test_large_usage_is_valid_bounded_json` | `JSONDecodeError: Unterminated string ... char 3982` in `get_task` |
| 6 | `test_stored_invalid_usage_read_defensively` | `JSONDecodeError ... char 20` |
| 6 | `test_permission_denials_visible_and_bounded` | `KeyError: 'permission_denials'` |
| 7 | `test_claude_requires_typed_success_envelope` ×5 of 6 | `{}`, result without `is_error`, `is_error:false` without result, empty result, `session_id:5` were all `ok=True`. The `type:system` case already failed (subtype `init`) and **passed** |
| 7 | `test_codex_requires_completed_turn_and_text[lines0]` | progress-only stream was `ok=True`. Two other cases (late error, no text) already **passed** |
| 7 | `test_system_object_and_progress_only_fail_end_to_end` | `succeeded` for a non-result object / progress-only stream |
| 8 | `test_cancelled_probe_kills_group` | PIDs `[309350, 309351]` alive after the cancelled probe |
| 8 | `test_probe_cancelled_during_spawn_kills_group` | descendant `[309354]` alive |
| 8 | `test_mcp_stdio_all_tools` (added assertions) | `required_flags_missing` still present; no `flags_not_advertised` |
| 9 | `test_control_maps_and_fds_bounded` | `{'_leases': 1, '_tasks': 34, '_stop_reason': 8}` retained |

\* While going GREEN, the injected `_drain` in `test_pipeline_exception_kills_group` was adjusted twice.
It first waited for a stdout byte that the `hang` fixture never writes. Then the fixed code killed the
group before the PID file existed. The final version raises once the PIDs are recorded. **This final
version was not re-run against the old code**, because there was no snapshot to revert to. Analytically,
the old code never observed reader exceptions, so it would stay `running` until the timeout.

## Fixes
| # | Fix | Where |
|---|---|---|
| 1 | `pin_workspace` opens the canonical dir `O_DIRECTORY\|O_NOFOLLOW\|O_CLOEXEC`, verifies the `/proc/self/fd` path, dev/ino and policy; `check_pinned` revalidates before spawn (`workspace_changed`); launch cwd `/proc/self/fd/N` with `pass_fds`; fd closed on every exit path | `paths.py`, `supervisor.py` |
| 2 | Per-job `_Job.stop` event + first-wins reason; runner never `Task.cancel`led; `cancel` awaits `shield(runner)`; persist terminal → release slot → release lease → drop job, all after group cleanup; `shutdown` waits for all runners even if itself cancelled | `supervisor.py` |
| 3 | `procs.run_group`: spawn + supervise + cleanup in an inner task nobody cancels; caller cancellation becomes a stop; a stop during spawn waits for the Process, then kills the group | `procs.py` |
| 4 | One global FIFO reader/writer `_Lease` (writer fairness, bounded by `max_pending`), no per-path dict | `supervisor.py` |
| 5 | Scalar `session_id`/`thread_id` validation → `malformed_output`; `except Exception` boundary → scrubbed, bounded `internal_error`; reader errors propagate after cleanup; `_persist` falls back to a minimal failed record, else sets `Supervisor.failed` (start refuses with `service_failed`, logs to stderr) | `adapters.py`, `procs.py`, `supervisor.py` |
| 6 | `normalize_usage` whitelist of finite numeric metrics + `omitted_fields`; defensive `_load_json` with `warnings`; new `permission_denials_json` column (migrated with `ALTER TABLE` when missing), `{count, items≤20 {tool_name, scrubbed input_excerpt≤200}, truncated}` | `adapters.py`, `store.py`, `supervisor.py` |
| 7 | Claude needs `type=result`, `subtype=success`, `is_error is False`, a non-empty string `result`. Codex needs `turn.completed` + agent text, and any failure event wins. OMP is still unverified best-effort and disabled | `adapters.py` |
| 8 | `_probe` uses `run_group` (cancel-safe, spawn-window-safe, capped output). `flags_not_advertised` + `flags_note` replace `required_flags_missing`; `--max-budget-usd` is checked when configured | `server.py`, `adapters.py` |
| 9 | `_jobs` holds active jobs only (removed by the runner after persistence); a global lease means no dict growth; no fd leak (checked via `/proc/self/fd`) | `supervisor.py` |

## GREEN
Command: `uv run --project "/home/jimmy/Projects (jimmy's)/SPQR" pytest -q -p no:cacheprovider --tb=short -rf tests`
```
............................................s........................... [ 78%]
....................                                                     [100%]
91 passed, 1 skipped in 34.58s
```
(64 original + 1 skipped live test, plus 27 new hardening tests. Original test assertions were not
weakened. `test_mcp_stdio_all_tools` gained doctor assertions.)

## Packaging/docs
`pydantic==2.14.1` is declared directly (lock updated project-locally with `uv lock --offline`).
`.verification/` is git-ignored. README now gives the Hermes `mcp add/test/remove` CLI and does not
run it. README and SECURITY now state Linux only, graceful-vs-abrupt shutdown, no crash
containment, and that the allowed root contains the control plane.

## Unresolved limitations
- No crash containment: if the bridge is SIGKILLed, agents may survive. `interrupted` is not proof of death.
- Pinning does not stop a same-user process from moving the directory or an ancestor after launch.
  The agent inherits one directory fd. Codex's `-C <path>` argument is still path-based (Codex is
  disabled).
- After normal leader exit, `killpg` targets a pgid that could in theory be reused once the whole
  group has exited (narrow window, pre-existing).
- If a group survives SIGKILL (e.g. uninterruptible sleep), the job records a WARNING but still
  becomes terminal after the 5 s SIGKILL wait.
- OMP parsing is unverified. Writes, Codex and OMP stay disabled.
