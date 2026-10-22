---
name: spqr-agent-delegation
description: Delegate scoped tasks through SPQR to registered external agent runtimes, track their results, and use the shared Library. Applies to authenticated SPQR MCP clients; installing this skill grants no authority.
---

# SPQR agent delegation

Use the operator-provided connection and your own named slot. Any stdio MCP client can
connect. `scripts/spqr-connect SLOT` forwards to a shared local owner; the older
`scripts/spqr-v2 SLOT` owns its own server and permits only one holder per state directory.
Never start another owner to bypass an occupied slot, a usage stop or a task limit.
Hermes-native peers can still use native delegation, messaging and Kanban when that is the
task's intended route. The bridge is the route to other registered external runtimes.

## Every order: remember to read and write in the Library

Read relevant Library entries available within the task's permissions before acting.
Record useful findings, decisions and results afterward, and link entries read or written
in the completion report. If the job is read-only, return a proposed Library update for
an authorized writer instead. If the Library is unavailable, report that explicitly;
never claim a read or write that did not occur. A skill does not grant Library access.

## Tools

- `list_agents`: inspect the principal's taskable roster. Enabled, installed and live-verified
  are separate facts. Do not infer account readiness or filesystem confinement from listing.
- `start_task`: give `agent_id`, bounded `task`, absolute `workspace`, optional `context`,
  `timeout_seconds`, `idempotency_key` and `grant_id`. Returns a task ID immediately.
  Include objective, files in scope, deliverable, success criteria and stop condition.
- `get_task`: poll your task with `wait_seconds=10`, not a hot loop with zero wait.
- `cancel_task`: cancel your task and wait for process-group cleanup.

v2 has no `doctor` tool. Native v1's doctor makes no model calls and cannot prove readiness.
Without a grant, work is read-only inside your principal's ceiling. Wider scope requires
a grant issued by Jimmy; pass its ID. Never create or widen grants, edit the operator registry,
borrow another principal's credentials or switch identities to evade a refusal.

## Stops and verification

`usage_exhausted` and `usage_stopped` are final: report to Jimmy. No automatic retry,
paid fallback, provider/account switch or reassignment to evade exhaustion. A separately
assigned task may use another registered runtime when Jimmy directs it.
`retry_cap_reached`, `round_cap_reached` and `no_progress` also stop for Jimmy.
Defaults are three consecutive failures and five rounds; a grant can permit up to ten
rounds. Changing task wording does not reset a lineage.

Use the same idempotency key to resolve an uncertain admission after a transport failure;
never resubmit with a new key merely because the reply was lost. `interrupted` means the
owner restarted and did not resume work. Inspect files before any authorized rerun.

Verify returned claims through files and appropriate checks. Report the task ID, actual
terminal state, deliverables, validation and Library references. A successful command exit
does not prove the work is correct. Generic command adapters do not provide native usage
metrics or filesystem confinement unless their configured runtime provides it.
