# Security boundaries — SPQR Agent Bridge

**Summary:** the bridge is a policy and supervision layer. It is **not an OS sandbox**.
Enforcement of file and tool access comes from each CLI's native mechanism.
**Platform: Linux only** (`/proc/self/fd` pinning, POSIX process groups, `fcntl`). Nothing is
claimed for macOS or Windows.

## What the bridge enforces
- **No caller-controlled execution.** Tools accept only agent_id, task, context, workspace, access,
  timeout and idempotency key. Executables, argv, env, model and provider come from the trusted
  operator registry (`--config`), validated strictly (unknown keys rejected, absolute binaries).
- **argv only, never a shell.** `asyncio.create_subprocess_exec`; prompt and context go on **stdin**, so
  text like `--dangerously-skip-permissions`, `@/etc/passwd` or `$(...)` stays literal (tested).
- **Workspace allowlist.** Canonical `resolve(strict=True)`; symlinks are followed and the target is
  checked against allowed roots by path component (`/root-evil` ≠ `/root`); must be an existing
  directory; the bridge state dir is never a valid workspace. **This restricts launch cwd only.**
  An agent can still reach other paths if its own permission system allows it.
- **Workspace pinning.** At admission the canonical directory is opened (`O_DIRECTORY|O_NOFOLLOW|
  O_CLOEXEC`), and its `/proc/self/fd` path and dev/ino are checked against the policy. The fd stays
  with the queued job. Just before spawn the bridge re-checks that the fd still points at the
  admitted path and that the path still names the same dev/ino, and rejects any change as
  `workspace_changed`. The child is launched with cwd `/proc/self/fd/<fd>` (fd passed via `pass_fds`),
  so a path swap after the last check cannot redirect the launch. The agent inherits that one
  directory fd. The fd is closed on duplicate/idempotent return, admission failure, cancel, completion
  and shutdown. This does **not** defeat a same-user process that moves the directory or an
  ancestor after launch. It is not a sandbox.
- **The allowed root holds the control plane.** The local config's root is the project itself, which
  contains source, config and `.state/`. Refusing the state dir as a cwd is **not** filesystem
  protection. Writes stay disabled. Before editing is enabled, separate the worker area from
  the control plane. No guarantee is made beyond the backend's own enforcement.
- **Minimal environment.** Only PATH, HOME, USER, LOGNAME, LANG, LC_ALL, TERM, XDG_* plus
  operator-listed variable *names*. Caesar/Hermes API keys are not inherited (tested). TMPDIR is a
  private per-task dir under `<state>/tmp/<task_id>` (0700), removed at task end.
- **Recursion guard.** `SPQR_BRIDGE_DEPTH` increments per hop; a server whose depth is ≥1 refuses
  `start_task`. Downstream CLIs also get no bridge MCP config (Claude: `--strict-mcp-config` with an
  empty config; Codex: `--ignore-user-config`). This is defence in depth, not a sandbox: a child
  could strip the variable.
- **Bounds.** Timeout ≤ `max_timeout_seconds`; stdout capped at `max_output_bytes`. On cap, the
  process group is killed and the task fails with `output_limit_exceeded` (bytes past the cap are
  drained and discarded until the kill). stderr keeps only the last 4 KiB. `result_text` is cut at
  `snippet_bytes`.
- **Process groups and single-owner lifecycle.** Each agent starts in a new session. Timeout, cancel,
  output cap, graceful shutdown and even normal exit send SIGTERM to the group, wait
  `kill_grace_seconds`, then SIGKILL. Each job has one runner task that is never `Task.cancel`led.
  Cancel and shutdown set an idempotent stop event and await that runner (shielded). The spawn is
  never abandoned mid-creation. The terminal state is published, and the slot and lease released,
  only after the group has been killed and the leader reaped. Doctor probes use the same helper
  (`procs.run_group`), including when the doctor call itself is cancelled. Tests cover
  SIGTERM-ignoring fixtures, concurrent cancels and cancels during spawn. A descendant that calls
  `setsid()` itself leaves the group and is not tracked (limitation).
- **Leases.** One global reader/writer lease with FIFO grants. Readers share it, and a writer
  excludes all others, so nested or overlapping workspaces cannot run two writers. Writes are
  serialised globally.
- **State.** State dir 0700, SQLite db 0600, exclusive `flock` (one live server per state dir).
  Parameterised SQL. Raw prompts are **not** stored (only a SHA-256 request hash for idempotency).
  Retained: metadata, ≤`snippet_bytes` of result, scrubbed stderr tail. Terminal records beyond
  `retain_jobs` are pruned. In-memory job control state is dropped when a job turns terminal. Clearing
  `.state/` is an operator decision (server stopped). Stored `usage` is a whitelist of numeric metrics
  and is always valid JSON. Unreadable legacy values come back as `null` plus a `warnings` entry.
- **Secret scrubbing** of results, errors and stderr is best-effort regex redaction (sk-…, Bearer,
  key/token/secret/password=…, gh*_…, AIza…). It is not a guarantee.

## Per-backend enforcement
| Agent | read_only | write | Notes |
|---|---|---|---|
| Claude Code | `--restricted`, `--tools`/`--allowedTools Read,Grep,Glob`, `--permission-mode dontAsk`, `--permission-prompts none`, strict empty MCP, no session persistence, turn/budget caps | adds `Edit,Write` with `acceptEdits`; **no Bash** | `allow_write=false` in local config until verified |
| Codex | `exec --sandbox read-only --ephemeral --ignore-user-config` | `--sandbox workspace-write` | never `danger-full-access`/bypass; no unsandboxed fallback |
| OMP | prompt-level only | rejected by config validation | disabled by default |

`doctor` scans each CLI's `--help` text and reports `flags_not_advertised`. This is informational
only: Claude omits `--max-turns` from its help, yet a live invocation accepted it. Acceptance is
proved only by a live smoke, which `doctor` never runs (it makes zero model requests).

## Not provided
No HTTP listener, no network exposure, no auth layer (stdio only, so the client is the trust
boundary), no durable execution, no automatic retries or model fallback.
**Crash containment is not provided.** Graceful shutdown cleans up every job's process group. An
abrupt SIGKILL or crash of the bridge does not, and agent processes may keep running. The
`interrupted` state written at the next start is bookkeeping, not proof that those processes died.
The bridge deliberately does not kill persisted PIDs on restart (PID reuse). Containment through a
cgroup or service manager is deferred. Agent outputs are untrusted: verify independently.
