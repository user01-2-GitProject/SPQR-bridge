# SPQR v2 — principal-based bridge design

Status: Phase 0 proposal, revision 2, 2026-10-20; Amendment A (Decisions 4–6) 2026-10-21. Jimmy appointed Codex and subagents
with differing perspectives for this review round. Jimmy approves the design
before implementation. Round 4 independent design/baseline reviews pass; no implementation gate has passed. No production cutover is authorized.

## Commission and boundaries

Implement R1–R5 from Jimmy's commission: authenticated named principals, an operator-owned
revocable registry, one job-owning daemon, tailnet-only MCP access, and principal-scoped
ownership, policy and audit. Names such as caesar and iggy are examples, not role
checks or grants. Jimmy alone chooses principals, permissions, caps, enabled agents and cutover.
Codex and OMP remain disabled. Existing source/tests and the live gateway stay intact.

## Amendment A — Jimmy's Decisions 4–6 (2026-10-21, revision 2 after independent review)

Source: docs/DECISIONS-V2-JIMMY.md. Where this amendment and older text below disagree,
this amendment wins; contradicting lines below are tagged "[superseded by Amendment A…]".

A1. Scope is per job, set by Jimmy when he gives the task (Decision 4).
- The registry carries identity only: principal ID, enabled flag, credential hashes, allowed
  transports, expiry, and outer ceilings (agents, workspace roots, write allowed at all).
  It carries no standing scope.
- Jimmy issues scope as a **grant**: an entry in the operator registry (`[grants.<id>]`)
  for exactly one principal, with access (read_only or write), agents, workspace roots,
  max_rounds (default 5) and mandatory issued_at and expires_at. The whole registry fails
  closed if either time is missing, issued_at is in the future, or expires_at - issued_at
  is over 24 hours. No MCP tool can
  create, widen or list grants. Jimmy tells the agent "use grant <id>"; the agent passes
  `grant_id` to start_task. Accepted reading: one grant covers one piece of work, which
  may take several rounds (A3), so it is short-lived and bounded rather than standing.
- Effective scope = grant ∩ principal ceiling ∩ global agent policy. A grant can never
  enable a disabled agent, add a root outside allowed_roots, or allow write where the
  agent or principal ceiling forbids it. A grant issued to A is refused for B with the
  same generic error as a missing or expired grant.
- The grant is re-checked at admission and again just before the queued job launches;
  a grant removed or expired in between fails the job before any process starts. The
  job row records grant_id and the resolved scope, so later registry edits cannot widen it.
- No grant_id means minimum scope: read_only, the principal's ceiling agents and roots,
  the default timeout. Silence is never a broad grant.
- Sub-delegation "no broader than its parent" has no mechanism yet and is vacuous in
  Phase 1, because depth stays 1 (bridge-launched agents cannot start bridge tasks).
  Any depth above 1 waits for the recursion gate and then becomes a grant field.

A2. The limit is usage; no money layer (Decision 5).
- No budget accounts, ledgers, reservations, settlement, currency or periods, and no
  dollar caps. The v2 entry point clears every agent's max_budget_usd at startup, so v2
  never passes --max-budget-usd (tested). v1 stays as it is until cutover.
- When an agent reports it ran out of usage, the job ends in terminal state
  `usage_exhausted`. Hard stop: never retried, no paid/per-token fallback, no switching
  agent or provider.
- The stop is recorded per agent and refuses new starts of that agent for every principal
  (`usage_stopped`) until Jimmy clears it with one registry line, `[usage_cleared]
  <agent> = <UTC time>`, which clears stops whose recorded UTC time is before it. A clear
  time in the future is rejected (it would pre-clear future stops). The bridge reports;
  it does not ration or guess when the window resets.
- Detection only looks at backend-reported failures (nonzero exit, Claude error envelope,
  Codex error event, stderr), never at a successful answer, using phrases listed in
  adapters.py. A model or injected text can still make a failing run look like a usage
  stop; the cost is one stopped agent that Jimmy clears. Unrecognised exhaustion stays
  `failed` and falls under A3. Unrelated: adapters' USAGE_KEYS are token counts.

A3. Loop guardrails (Decision 6). A **lineage** is the work being looped on: the grant,
or without a grant, the same principal + agent + workspace within a rolling 60 minutes.
- Failure cap: **3**. After 3 consecutive non-success outcomes (failed, timed_out) in a
  lineage, further starts are refused with `retry_cap_reached`; editing the task text does
  not reset it. A success resets it. 3 is Jimmy's standing "stuck after 3 tries, stop".
- Progress cap: **5** by default, grant max_rounds may set 5–10. Without a grant it is 5
  per lineage. Exceeding it is refused with `round_cap_reached`. Only admitted jobs count;
  idempotent replays and refused starts do not.
- No-progress tripwire: if the last two finished jobs in a lineage ended with the same state
  and the same digest of result/error text, the next start is refused with `no_progress`.
  Best effort: nondeterministic output (timestamps) can defeat it; the caps still hold.
- Plainly: without a grant the bound is 3 failures and 5 rounds per principal, agent and
  workspace per hour; a principal with many ceiling roots multiplies it. A per-principal
  hourly ceiling can be added later if that matters.
- Admission is synchronous inside the single event loop (no await between the checks and
  the job insert), so concurrent starts cannot overrun a cap (tested with simultaneous
  starts). The failure cap and the tripwire share one lineage query.
- Agent-internal turn limits (e.g. Claude --max-turns) are unchanged and separate.

A4. Phase 1 simplifications chosen by the builder (keep it uncomplicated).
- Credentials: tokens are at least 32 random bytes (`secrets.token_urlsafe(32)`). The
  registry stores only their sha256; no salt or KDF is needed at that entropy. The server
  needs no SecretResolver; only the launcher holds the token. Digests are compared with
  hmac.compare_digest and never logged.
- v2 runs as its own entry point (`python -m spqr_bridge.v2 --config C --registry R
  --credential-fd N`), normally started by `python -m spqr_bridge.launch --token-file F`,
  which refuses a token file that is not owner-only. No anonymous mode. The v1 entry point
  and its tests stay unchanged as the production binding until Jimmy orders cutover; at
  cutover v1 is retired, not kept as a fallback. v2 has no doctor tool in Phase 1.
- Ownership: new nullable columns on jobs (principal_id, grant_id, scope_json); v2
  idempotency lives in idempotency_receipts UNIQUE(principal_id, key), which survive job
  pruning (a pruned replay returns the task ID with state `result_expired`). Legacy rows
  (principal_id NULL) are invisible to every principal.
- Audit (Phase 1 minimum): append-only audit_events rows with allowlisted fields only
  (time, principal, tool, task/grant/agent IDs, outcome code). Authentication failures go
  to an append-only line file in state_dir, because opening the job store for an
  unauthenticated caller would run crash recovery on its behalf. That file holds only time
  and reason code and stops growing at 1 MB.
- Registry placement: the registry must live outside every allowed root (v2 refuses to
  start otherwise) and no workspace may contain it, so a write job cannot rewrite its own
  scope. Same-user workers are still not OS-confined (see Recursion section); this closes
  the obvious path, not every path. The launcher's token file belongs outside roots too.
- Durability: usage stops are their own table (usage_stops), so pruning jobs never lifts one;
  pruning keeps v2 rows younger than 24 hours so the A3 caps cannot be reset by volume.
- The `usage_exhausted` state is produced only by the v2 entry point; v1 keeps reporting
  `failed` until cutover.
- Revocation in Phase 1: the registry is re-read on every call and before launch; a
  disabled, expired or removed principal gets a generic auth error and cannot read results.
  An unreadable or invalid registry fails closed. Stopping already-running jobs waits for
  Jimmy's revocation ruling (still open).

## Observed starting point

Evidence inspected locally: `server.py:build_server` creates a Supervisor/Store per server
lifespan; `main` runs stdio. `config.py:load_config` loads once. `store.py:Store.__init__`
locks server.lock before opening/recovering SQLite; active jobs become interrupted, never resumed.
`store.py:find_idempotent` is global. `supervisor.py:start` reads process environment depth;
`paths.py` pins workspace fds; `procs.py:run_group` owns process-group cleanup.
`docs/SECURITY.md` explicitly disclaims OS sandboxing and crash containment.
These are source observations, not proof of the currently deployed process configuration.
The initial checkout has staged additions and unstaged README edits; preserve them.

## Identity and registry

Use an immutable AuthContext produced only by credential verification: principal_id,
credential_id, transport, registry_revision. Tool arguments contain no authority fields.
Prompts, identity headers, session IDs, job IDs and displayed names confer no authority.
Every MCP method, including initialization, discovery, HTTP GET/DELETE and notifications,
requires authentication. Authenticated sessions remain bound to their principal and credential;
revalidate on every request. No anonymous discovery or health detail endpoint.

A separate git-ignored operator registry specifies stable principal IDs, enabled status,
credential references and expiry, permitted transports, agent allowlists, access modes,
workspace roots, time/turn limits and budget policy. Ship only a nonfunctional example.  [superseded by Amendment A1: registry holds identity and ceilings only; scope is per-job grants]
No tool provisions principals, writes the registry, grants access or raises caps.
Principal IDs must not be reused for different people/agents: historical ownership survives revocation.

Load and strictly validate the registry before each authorization decision and again before
queued launch. Read one bounded snapshot through a safe file open, checking ownership,
permissions and file type; operator updates use atomic replacement. Invalid/unreadable config
fails closed; no stale-grant fallback. Policy revision is a digest of non-secret normalized policy.
Registry changes and launch admission serialize inside the daemon so authorization has a
specified decision point. Revalidate before returning protected output after a wait and before
each stream event. Revocation closes streams and withholds results; cleanup continues. Data
already emitted cannot be recalled. New operations use the new snapshot.

Proposed revocation behavior, subject to Jimmy's decision: immediately refuse new calls,
cancel queued jobs, request running-job termination, audit completion. A registry watcher
supplements per-call checks so idle revoked sessions do not leave queued work eligible.
Polling interval and cancellation latency must be measured and documented before approval.

## Phase 1 authentication contract

Phase 1 remains standalone stdio in isolated state, with no HTTP or IPC listener. A trusted
launcher obtains the credential through a SecretResolver interface and supplies it via a  [superseded by Amendment A4: registry stores token hashes; no SecretResolver]
single-use inherited anonymous pipe FD; only the FD number travels in argv. The server reads
a bounded credential envelope (maximum 4096 bytes, deadline 2 seconds), closes the FD, verifies
the credential and its `stdio` scope against the current registry, and creates AuthContext
before starting the MCP lifespan or accepting initialize. Missing/invalid/expired credentials
fail generically, with an unauthenticated audit receipt and no service. No `--principal` authority,
no anonymous backward-compatibility mode. Do not accept a named disk path as a credential FD;
validate pipe type and close-on-exec after receipt. No worker inherits it.

[SecretResolver paragraph superseded by Amendment A4: the registry stores token hashes and only
the launcher holds the token.] Existing v1 production remains on its unchanged code/binding. Phase 2's stdio
forwarder uses the same bootstrap and authenticates to daemon IPC; it never owns a Store.

Bearer matrix semantics: a valid B token on a new channel authenticates B, not A. A B token
on A's established session is denied; B cannot access A's jobs or idempotency receipts. A token
on the wrong transport is denied even if otherwise valid. A copied bearer authenticates its
owner; rejecting copied credentials before initialize would require an additional independent
channel identity and cannot be claimed from bearer authentication alone. Record this precise
interpretation when testing the commission's "another principal's token" cell.

## Credentials and transport

Propose a single daemon with Streamable HTTP and a thin stdio forwarding client for Hermes.
The forwarding client owns no Store or execution state. This preserves Hermes' command binding
while all profiles connect to one owner. Phase 1 exercises AuthContext and policy through the
existing stdio server in isolated test state before any HTTP implementation is added.
The v1 production stdio binding remains until Jimmy orders cutover.

Use independent high-entropy bearer credentials scoped to principal and transport. Never accept
principal selection alongside a token. A token permitted only on stdio must fail on HTTP;
stdio forwarding exchanges its local credential over a private local IPC ingress, not HTTP.
Both ingresses share the same authorization/dispatcher and Supervisor. Private IPC is not
identity by itself: credential verification is still mandatory. HTTP credentials cannot use IPC.
No token in argv, URLs, MCP payloads, prompts, diagnostics or environment passthrough.

Registry holds secret references, not plaintext tokens. Secret-store selection is unresolved;  [superseded by Amendment A4: registry stores token hashes; no SecretResolver]
inspect and propose an existing operator mechanism before implementing retrieval. No new
credential is created or distributed without Jimmy's direction. A file fallback, if approved,
must be outside worker-readable areas, git-ignored, owner-only, and never published.
Constant-time credential comparison; reject duplicate/ambiguous Authorization headers,
query credentials, oversized headers and malformed encodings. Generic errors never echo input.
Rotation permits explicit overlapping credentials; revocation/expiry applies to existing sessions.

Bind HTTP only to an explicit address verified as assigned to the rig's tailnet interface;
reject wildcard, loopback-only substitute, public addresses, and implicit defaults. If unavailable,
fail closed; never fall back. Verify IPv4/IPv6 independently. No public proxy, Tailscale Funnel,
or forwarded-header trust. Validate Host/Origin and bound bodies, sessions, rates and idle time.
Use HTTPS if supported by the approved tailnet configuration; direct HTTP would rely on the
WireGuard tunnel for confidentiality and needs explicit design acceptance. Endpoint address,
TLS arrangement and actual client compatibility remain unverified.

## Single owner, migration and crash containment

One daemon lifespan owns exactly one Store/Supervisor. Client disconnects and HTTP session
expiry never shut it down. Keep flock across migration, recovery and the entire lifetime.
A second daemon fails before reading/mutating jobs. Never unlink a hot lock file.
Use isolated state and fixture agents throughout development.

Migration adds immutable principal ownership and replaces global idempotency with
UNIQUE(principal_id, idem_key), in a transaction under the owner lock. Legacy jobs remain
unassigned and inaccessible through principal tools unless Jimmy approves an explicit mapping.
Do not infer caesar ownership. Back up stopped production state before an approved migration;
record schema version and reversible rollback procedure. No running job is transferred/resumed.

The SIGKILL requirement requires new OS containment. Proposed production unit uses systemd
cgroup containment (KillMode=control-group); all workers remain in its controlled descendant
cgroups. Restart must await complete old-cgroup cleanup before acquiring state/recovering jobs.
Require a tested service-manager behavior for daemon SIGKILL, child setsid, timeouts and
uninterruptible processes; if cleanup cannot be established, refuse restart/admission.
Do not kill persisted numeric PIDs. interrupted is bookkeeping, never proof of process death.
Production daemon admission is service-only: a trusted launcher supplies the unit invocation
identity and confirms the previous containment generation empty through the service manager.
Lifecycle states: stopped -> cleaning_previous -> lock_acquired -> migrated/recovered -> serving.
Failure or surviving descendants stays cleaning_previous/failed; no serving transition. An
arbitrary manual production invocation must fail before opening Store. Fixture-only standalone
mode uses separate temporary state and cannot be selected by an HTTP caller. An environment
flag alone is not proof of service identity; exact launcher verification is a Phase 2 feasibility
gate. Unit installation and cutover are future operator actions, not part of Phase 0.

## Recursion and worker trust boundary

SPQR_BRIDGE_DEPTH remains defense in depth, checked by the forwarding client and propagated
to workers. An HTTP header claiming depth cannot prove lineage: the daemon's environment
stays at zero and a hostile child can lie. Passing a credential proves possession, not ancestry.

To satisfy hostile bridge→agent→bridge denial, workers must be unable to retrieve commander
credentials or reach an authenticated commander proxy. Proposed boundary: separate daemon/
credential identity and confined worker identity, explicit read-only workspace mounts, private
HOME/XDG/provider-auth arrangement, no commander config/secret-store access, no inherited
bridge sockets/fds, and restricted access to bridge ingresses. Preserve the one pinned workspace
fd deliberately. Retain strict empty downstream MCP configuration and depth guard.
Same-user unrestricted workers cannot satisfy this guarantee; do not claim they can.
Exact sandbox/service design and provider-login compatibility require an isolated feasibility
proof and Jimmy's approval. Until proven, the recursion gate is blocked and network rollout
cannot pass. Commander credentials intentionally supplied by an operator remain authority;
no bearer scheme can distinguish a copied credential from its rightful holder.

## Phase 1 persistence contract

Schema migration belongs to Phase 1, under the existing lock; Phase 2 only adds transport and
service lifecycle. Proposed tables: jobs(principal_id nullable only for legacy rows, task_id,
policy_revision, existing metadata); idempotency_receipts(principal_id, key, request_hash,
task_id, created_at), unique(principal_id,key); [budget tables removed by Amendment A]; audit_events(sequence, request_id, safe structured fields); audit_outbox(sequence,
export_status). Foreign/missing
ownership checks precede every query response. No caller-controlled SQL identifiers.

Admission checks idempotency, the global pending bound, current policy and the A3 caps, inserts
the job and appends the admission audit, with no await in between (Amendment A3). No process
launches before the insert. A duplicate matching receipt returns the same task reference; a
mismatched request hash fails. Crash recovery records interrupted jobs before enabling admission.

Idempotency receipts are durable tombstones, independent of job/result pruning, principal
revocation and restart. Keys never expire or become reusable
implicitly. A replay whose result was pruned returns the original task ID and result_expired,
without execution. Explicit administrative archival must preserve key non-reuse;
no automatic deletion in v2. Planned test: prune original job, replay key, assert zero new executions.

## Ownership and permissions

All get/wait/cancel/idempotency lookup paths filter principal ownership before returning any
metadata or registering a waiter. Foreign and missing IDs return the same bounded error.
Validate IDs before SQL/dispatch. Scope list_agents/doctor to permitted agents and suppress
host/control-plane details. Every execution uses the intersection of global agent policy and
principal policy; no principal override can enable a disabled agent or grant global writes.
Keep global concurrency=2, pending=8 and FIFO reader/writer lease. Preserve workspace pinning,
environment allowlist, output caps and single runner cancellation semantics.

[Money paragraph removed by Amendment A2: no budgets, reservations or dollar caps.]

## Audit and failure behavior

Write structured append-only receipts for authentication, protocol validation, each tool call,
admission, spawn, terminal outcome, policy changes and crash recovery. Fields: sequence/request
ID, UTC timestamp, principal or unauthenticated, safe tool enum, validated job/agent IDs or null,
policy revision, safe outcome code. Foreign-job attempts
must not disclose target ownership. No raw headers, tokens, fingerprints, prompts, responses,
paths or arbitrary exception strings. Redaction alone is insufficient: allowlist fields.
A token embedded in a malformed ID must never be logged. Audit denied calls too.

Persist admission/audit together in SQLite, and use a durable audit outbox for
exported receipts. Audit storage failure prevents new execution; cleanup still runs and records
terminal state when possible. Bound transport abuse with rate/size limits and explicit overflow
receipts; no silent claim that every request was recorded if disk failure prevented it.
Local audit is not tamper-proof against the machine owner. Obtain Rubicon receipt schema from
Jimmy's chosen source before asserting equivalent compliance; no such schema was verified here.

## Phases and evidence gates

0. Jimmy assigns reviewers and approves this design, including unresolved choices below.
1. Write new auth/ownership/transport-scope/expiry/reload/idempotency/scope/usage-stop/loop-cap tests RED first;
   implement stdio authentication in isolated state. Preserve all existing 91 tests unchanged.
   Jimmy-selected independent non-author adversarial review; preserve verdict verbatim in
   docs/REVIEW-V2.md. Failed verdict stops progression; repair tests-first, then re-review.
2. Add daemon, isolated IPC/HTTP, containment and migration-compatibility tests before implementation.
   Exercise malformed headers, session theft, revocation, daemon SIGKILL, hot restart, competing
   owners, concurrent admissions and cancel storms. Same independent review gate. Prepare
   migration and rollback; only Jimmy orders production cutover. Verify Caesar live path afterward.
3. Document repeatable generic Hermes profile binding and tailnet client contract. Jimmy and
   iggy build iggy's client separately. No implicit authorization through a profile/role name.
4. Run full policy/audit/revocation drills, hostile paths (symlink swap, rename, dot-dot), all
   principal recursion attempts, fuzz bounds, admission races, and real bounded delegations for
   Caesar via Hermes and iggy via tailnet. Record job IDs, never tokens.
   Final independent non-author review chosen by Jimmy; all verdicts retained unedited.

Every claim needs a command/test/job receipt. No live verification or review passage is claimed
by this proposal. Stop after three failed attempts on a step and report the blocker.
Do not push to GitHub without Jimmy's approval.

## Test harness and remaining decisions

Keep all existing test assertion files unchanged. Add an explicitly loaded test-only pytest
plugin/harness that provisions a fake registry and credential through the trusted pipe launcher
for the existing stdio fixture, and supplies an explicit fixture AuthContext to internal tests.
No production auth bypass or ambient test-mode variable. The harness is test infrastructure,
not evidence that anonymous requests remain allowed. New negative tests run without bootstrap
and must fail. The existing readiness test runs against a copied checkout with a conservative
local fixture config, never the live local registry; retain the real-checkout failure receipt
alongside that isolated baseline. Do not mutate, hide or relabel production live_verified state.
Record harness changes separately and prove original assertion-file hashes unchanged.

Jimmy-reserved: actual principals, grants, write capability, cap numbers, agent enabling, legacy
ownership assignment, production installation and cutover. No real credential issuance or live
spend is authorized by this draft. Jimmy's reviewer selection is resolved for this round.

Builder work: inspect tailnet configuration read-only, choose and
justify TLS/worker confinement mechanisms, locate Rubicon receipts, and prove service
launch/cleanup compatibility. These engineering investigations do not require Jimmy to choose
the implementation. Proposed revoke-and-stop behavior is the conservative default for review;
measure propagation before rollout and surface any product-policy question to Jimmy.

No choice here authorizes a principal, a write grant, a disabled adapter,
production service change, deployment or push. Design approval remains pending. Phase 1
cannot begin until the recorded baseline failures are diagnosed and review blockers cleared.

## Phase 0 baseline receipt

Command (2026-10-20): `.venv/bin/python -m pytest -q -p no:cacheprovider --tb=short`
Observed: **89 passed, 2 failed, 1 skipped in 59.56s**. No source or tests changed.
Failures: `test_mcp_stdio_all_tools` timed out during MCP initialize;
`test_shipped_config_conservative[bridge.local.toml]` asserts all live_verified values
are false, which conflicts with the operator-recorded live verification in this local config.
Do not erase verified readiness or weaken the existing test to manufacture a green baseline.
The 91-pass historical claim is not reproduced in this run. Diagnose these failures before
Phase 1 implementation; this run does not establish their cause or production health.


## Revision 2 review outcome

Jimmy appointed Codex/subagent review with differing perspectives. Three independent reviewers
initially returned FAIL. After design revisions, security and scope passed their contracts;
reliability still fails readiness to begin Phase 1 because baseline diagnosis is unresolved.
All verdicts, including failures, are preserved verbatim in REVIEW-V2.md. The diagnostic
receipt is reviews/v2-baseline-diagnosis.md: isolated initialize timeout reproduced, minimal
direct-wire timeout reproduced, instrumented server reached the AnyIO event loop; no cause
proved. Three attempts exhausted. Stop and report; no Phase 1 code or production changes.


## Alternative investigation and round 3

Jimmy authorized another approach and re-review. Runtime protocol/task traces show the valid
initialize request parsed and waiting in the internal transport send path while the opening
consumer had not received it at the snapshot. This is localization, not proof of deadlock or
an SDK/AnyIO defect. Two traces ran; the third diagnostic script failed with IndentationError
before server execution. No fix or green baseline was established. Three-attempt stop applied.
Round 3 reviewers retain design contract PASS and baseline/implementation readiness FAIL.
See REVIEW-V2.md for unedited verdicts and reviews/v2-baseline-alternative.md for full receipts.


## Round 4 — baseline prerequisite cleared

Jimmy authorized continuation with a 20-attempt limit for this diagnostic blocker; this supersedes
its earlier three-attempt stop. Ten diagnostic/setup attempts used in this round. The unchanged
stdio integration test passes outside sandbox (1 passed in 11.24s). A separate copied checkout,
separate locked/offline Python 3.14 environment, and conservative local fixture config reproduce
**91 passed, 1 skipped in 28.87s**. Original source/assertions and real local config unchanged.
All three independent reviewers now PASS the design and baseline prerequisite. Historical
failures above remain honest receipts; the exact sandbox async-I/O defect remains unproved.
See reviews/v2-baseline-resolution.md, baseline-round4 artifacts, and REVIEW-V2.md.
No Phase 1 code, real credential issuance, production cutover or push occurred. Jimmy's design
approval remains distinct from the reviewers' technical verdicts. The temporary 20-attempt limit
applies to this blocker; it does not silently replace unrelated standing rules.
