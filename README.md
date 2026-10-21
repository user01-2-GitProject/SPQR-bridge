# SPQR Agent Bridge

> *Caesar over the Rhine in ten days, with timber and nerve. This bridge went up in one night.*

**The receipts:** `caesarRUBICON.json` is the complete session export of the night this bridge was built — the research, the build, the failed review, the repair, the re-review, and one agent's spiral — token-shaped strings and personal identifiers redacted, everything else unedited.

A local **stdio MCP server** that lets Caesar (Hermes profile `caesar`) delegate bounded tasks to
independently installed agent CLIs: Claude Code, Codex and OMP. Each one runs its own agent loop.
It has five tools: `list_agents`, `start_task`, `get_task`, `cancel_task` and `doctor`.

For Hermes-to-Hermes collaboration, keep using native `delegate_task` / `message_agent` / Kanban.

## v2 — decided, in build

The product, in the operator's words: *"I tell an agent a task and it has the ability to task others."*

- **Principals, not possession.** Every caller is a named principal the operator designates. No identity, no service. The registry is operator-owned local config; adding or revoking a principal takes effect without a rebuild.
- **One daemon, many commanders.** A single long-lived bridge owns the job store; Hermes profiles and other clients connect instead of spawning their own server. Tailnet-only HTTP endpoint, per-principal tokens — never public, tokens never in git or logs.
- **Open roster.** Claude, Codex, OMP at launch, OpenCode next; adding any agent in the operator's stable is registry config, never a rebuild. Chained delegation follows the same registry, depth, and scope rules.
- **Scope is set at the time of use.** No standing grants — each job carries the operator's per-job scope (read/write, workspaces, who it may task in turn). Sub-delegations inherit nothing broader; unspecified scope defaults to the minimum.
- **The limit is usage.** No per-job dollar cap layer. Usage exhaustion is a hard stop — no retries, no paid fallback, no provider-switching — reported as a usage-stop.
- **Loop guardrails.** Failure retries cap at 3–5, then stop and report. Progress loops cap at 5–10, with a no-new-state tripwire.
- **Jobs are owned and audited.** A job belongs to its principal; nobody reads or cancels another's jobs. Every call is audit-logged: principal, tool, job, agent, outcome.
- **Global delegation skill.** `spqr-agent-delegation` ships by default in every harness and profile; onboarding a new agent = the skill (knows how) + a registry entry (is allowed).

Borrowed from StarNet, adapted to the rules above: **Task Briefs** (a job that hits a real fork pauses and asks one concrete question with options instead of guessing), **OUTBOX** (deliverables land as real files), a **usage ledger** shown as-is — accounting, not a governor — and the **provable-state law**: the bridge reports only job state it can back with a signal. StarNet's per-job budget caps were considered and rejected; usage is the ceiling here.

## Quickstart
```sh
cd "/home/jimmy/Projects (jimmy's)/SPQR"
uv sync                                    # project-local .venv from uv.lock (mcp==2.3.0)
uv run pytest                              # deterministic tests with FAKE agent fixtures; no model calls
cp config/bridge.example.toml config/bridge.local.toml   # already present on this machine
.venv/bin/spqr-bridge --config config/bridge.local.toml # speaks MCP on stdin/stdout
```
Live smoke (real Claude call, costs money, budget capped at $0.20 in the local config):
```sh
sh scripts/live_smoke.sh
```
After it passes, set `live_verified = true` for `claude` in `config/bridge.local.toml`. That flag
is only informational and is reported by `list_agents`/`doctor`.

## Tool contract
| Tool | Input | Notes |
|---|---|---|
| `list_agents` | — | policy, access modes, binary presence, operator live-verified flag; no model call |
| `doctor` | — | `--version` plus a `--help` text scan, 5 s timeouts; `flags_not_advertised` is informational only (Claude hides `--max-turns` from help but accepts it); `model_requests_made: false`; `provider_ready` is always "not checked" |
| `start_task` | `agent_id`, `task`, `workspace`, `context?`, `access` (`read_only`\|`write`), `timeout_seconds?`, `idempotency_key?` | returns `task_id` immediately |
| `get_task` | `task_id`, `wait_seconds` 0..10 | state, `result_text` (≤ snippet_bytes), `error`, `return_code`, timings, truncation flags, `session_id`, whitelisted numeric `usage`, bounded `permission_denials` (count + ≤20 scrubbed items), `warnings`, scrubbed `stderr_tail` |
| `cancel_task` | `task_id` | returns only after the process group is killed and reaped; idempotent; concurrent calls share one cleanup |

Task states are `queued`, `running`, `succeeded`, `failed`, `cancelled`, `timed_out` and
`interrupted`. Error codes are prefixed, for example `unknown_agent:`, `agent_disabled:`,
`binary_missing:`, `access_denied:`, `workspace_denied:`, `invalid_timeout:`, `queue_full:`,
`idempotency_conflict:` and `recursion_denied:`.

## Agent status on this machine
| Agent | Registry default | Status |
|---|---|---|
| claude | enabled, read_only | Caesar's earlier live print-mode smoke passed; bridge live smoke **not yet run** |
| codex | disabled | logged in, but bounded live calls never completed; enable after a successful smoke |
| omp | disabled | live calls timed out; no enforced file restriction, so write is rejected |
| opencode | not included | mise shim is missing its node runtime; deferred |

## Installing into Hermes (operator action; not performed by the implementer)
Use the Hermes native custom-server CLI for the `caesar` profile (not the catalog manager), with
absolute paths:
```sh
hermes --profile caesar mcp add spqr-bridge \
  --command "/home/jimmy/Projects (jimmy's)/SPQR/.venv/bin/spqr-bridge" \
  --args --config "/home/jimmy/Projects (jimmy's)/SPQR/config/bridge.local.toml"
hermes --profile caesar mcp test spqr-bridge
```
Then start a fresh Hermes session and run `/reload-mcp`. No env is needed beyond the defaults.
`HOME` and `PATH` must reach the bridge so the CLIs can find their own auth.

**Removal:** `hermes --profile caesar mcp remove spqr-bridge`. Job history in `.state/` is kept.
Deleting it is a separate, destructive decision for the operator: stop the server first and review
what you are removing. Nothing in this repo deletes it for you.

Run **one server per state_dir**. A second process on the same state dir fails with a lock error.
For multiple instances, give each its own config with a distinct `state_dir`.

The portable Caesar workflow is in `skills/spqr-agent-delegation/SKILL.md` (copy it manually if wanted).

## Limitations
- **Linux only.** Workspace pinning uses `/proc/self/fd`, and process control uses POSIX sessions
  and process groups plus `fcntl`. Nothing was tested on macOS or Windows.
- This is not an OS sandbox. The workspace allowlist only sets the launch cwd. Enforcement comes
  from each CLI's own permissions/sandbox. See `docs/SECURITY.md`.
- The configured allowed root contains this project's source, config and `.state/`. Excluding the
  state dir as a cwd is **not** filesystem protection. Writes stay disabled; enabling editing first
  needs a separate worker area outside the control plane.
- Writes (if ever enabled) are serialised **globally**: one writer excludes every other task.
- Jobs are server-local asyncio tasks. They are **not durable**. A graceful shutdown stops and
  reaps every job's process group. An abrupt SIGKILL or crash of the bridge skips that cleanup, and
  agent processes **may keep running**. On restart, queued/running records become `interrupted`.
  That is a bookkeeping state, **not** proof that the old processes died. The bridge does not kill
  persisted PIDs on restart, because PIDs can be reused. Crash containment (cgroup/service manager)
  is deferred. There are no automatic retries or fallback.
- `doctor`'s `flags_not_advertised` only scans help text. A listed flag may still be accepted (live
  Claude runs accept `--max-turns`). Only a live smoke proves acceptance.
- OMP's JSON output format is unverified, so its parsing is best-effort. OMP is disabled.
- Results are untrusted text. Verify files and tests independently.

Docs: `PLAN.md`, `docs/RESEARCH.md`, `docs/SECURITY.md`, `docs/VERIFICATION.md`.
