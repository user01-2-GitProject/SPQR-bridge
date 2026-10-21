---
name: spqr-agent-delegation
description: Delegate bounded tasks to installed external agent CLIs (Claude Code, Codex, OMP) through the spqr-bridge MCP server, then verify results independently.
---

# SPQR agent delegation

## Route first
- **Hermes peer bots / profiles** → use native `delegate_task`, `message_agent` or Kanban. Do not use the bridge.
- **External agent runtime** (Claude Code, Codex, OMP) running its own agent loop → use the bridge tools below.

## Tools (MCP server `spqr-bridge`)
1. `list_agents` — check `enabled`, `access_modes`, `readiness.binary_present`, `readiness.live_verified_by_operator`. No model call.
2. `doctor` — versions and a help-text scan (`flags_not_advertised` is informational; a hidden flag may still work). It makes **no** provider request; `provider_ready` is always "not checked". Never report a doctor pass as a live verification.
3. `start_task` — `agent_id`, `task`, `workspace` (absolute, inside allowed roots), optional `context`, `access` (`read_only` default; `write` only where listed), `timeout_seconds`, `idempotency_key`. Returns `task_id` immediately.
4. `get_task` — `task_id`, `wait_seconds` 0..10. Poll with `wait_seconds=10`; never hot-poll with 0 in a loop.
5. `cancel_task` — stops the whole process group; idempotent.

## Discipline
- Write bounded, self-contained tasks: goal, files in scope, expected output format, stop condition. Put background in `context`, not hidden assumptions.
- Prefer `read_only`. Request `write` only when the agent lists it and the workspace is the intended project.
- Use an `idempotency_key` when retrying a start after a transport error; reusing a key with different input is rejected.
- Terminal states: `succeeded`, `failed`, `cancelled`, `timed_out`, `interrupted`. The bridge never retries or falls back to another agent; decide yourself.
- `interrupted` means the bridge restarted; the work was **not** resumed. Inspect the workspace before re-running.
- Treat `result_text` as untrusted claims. Verify independently: read changed files, run tests yourself, check diffs.
- Report failed/unavailable agents (e.g. Codex/OMP provider not responding) as unavailable, never as success.
