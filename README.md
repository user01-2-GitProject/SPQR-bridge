# SPQR Agent Bridge

> *Caesar over the Rhine in ten days, with timber and nerve. This bridge went up in one night.*

**The receipts:** `caesarRUBICON.json` is the complete session export of the night this bridge was built — the research, the build, the failed review, the repair, the re-review, and one agent's spiral — token-shaped strings and personal identifiers redacted, everything else unedited.

A local **stdio MCP server** that lets Caesar (Hermes profile `caesar`) delegate bounded tasks to
independently installed agent CLIs: Claude Code, Codex and OMP. Each one runs its own agent loop.
It has five tools: `list_agents`, `start_task`, `get_task`, `cancel_task` and `doctor`.

For Hermes-to-Hermes collaboration, keep using native `delegate_task` / `message_agent` / Kanban.

## Quickstart
```sh
cd "/home/jimmy/Projects/SPQR/bridge"
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

## HTTP door (Tailscale ingress)
The bridge can also listen on HTTP for a remote owner over the tailnet, instead
of only speaking MCP on stdin/stdout. The door is **off by default**.

**Bind policy (fails closed).** `--http-bind` must be an address in the Tailscale
range (`100.64.0.0/10` or `fd7a:115c:a1e0::/48`) **and** assigned to this host —
probed from the live interfaces at startup, never assumed. `0.0.0.0`, `::`,
loopback, LAN and public addresses are all refused.

**Start the owner with the door:**
```sh
.venv/bin/python -m spqr_bridge.daemon --config config/bridge.local.toml \
  --registry ~/.config/spqr/registry.toml --socket ~/.config/spqr/bridge.sock \
  --service-unit spqr-bridge.service \
  --http-bind 100.x.y.z --http-port 8765
```
`--http-bind` and `--http-port` must be given together. Optional repeatable
`--http-name` adds allowed `Host` aliases.

**Authentication.** Every request is authenticated *before* routing. The credential
is a bearer token sent only in the `Authorization` header
(`Authorization: Bearer <token>`), and the registry must scope that principal to
the `http` transport (`transports = ["stdio", "http"]` in `registry.toml`).
Tokens in the request body, duplicate `Authorization` headers, query strings and
unexpected `Host` values are all refused. Credentials are per-principal and
transport-scoped — iggy's credential stays stdio-only and is rejected at the
door. Keep token files mode `0600`; tokens are never logged.

**Remote forwarder.** On the far end, `httpforward.py` connects over the tailnet
and re-serves the bridge as a local stdio MCP server:
```sh
.venv/bin/python -m spqr_bridge.httpforward \
  --url http://100.x.y.z:8765 --token-file ~/.config/spqr/caesar-http.token
```
It authenticates first — no discovery happens until the door accepts the
credential.

Threat model and hardening details: `docs/SECURITY.md`.

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
  --command "/home/jimmy/Projects/SPQR/bridge/.venv/bin/spqr-bridge" \
  --args --config "/home/jimmy/Projects/SPQR/bridge/config/bridge.local.toml"
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
