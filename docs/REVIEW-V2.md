# SPQR v2 independent design review receipts

Jimmy appointed Codex and subagents with different points of view on 2026-10-20.
These are Phase 0 design reviews, not the mandatory post-implementation gates.
Each reviewer inspected but wrote none of the design or implementation code.
The builder revised DESIGN-V2.md after round 1. Reviewer verdict bodies below are
concatenated verbatim from their saved receipts; failures remain included.

Current outcome (round 4): all three independent reviewers PASS the Phase 0 design and
isolated baseline prerequisite. The unchanged isolated suite passes 91 tests with 1 live skip.
Earlier FAIL verdicts remain below. No implementation gate or production change is approved.
See [baseline resolution](reviews/v2-baseline-resolution.md) and retained artifacts.

---

## v2-phase0-security-r1.md

**Security verdict: FAIL for readiness to implement Phase 1.** The design correctly identifies the major risks, but the first phase’s authentication contract is still unspecified. This is a design verdict, not a claim that implementation tests have failed.

Evidence: read-only `nl -ba` inspection of `docs/DESIGN-V2.md`, `envpolicy.py`, `supervisor.py`, `server.py`, `config.py`, and the commission. No files changed and no live requests made.

1. **[P1] Specify how stdio obtains authenticated identity before initialization.**  
   `docs/DESIGN-V2.md:55–66` describes the future forwarding client and IPC authentication, while Phase 1 uses the existing standalone stdio server. It prohibits credentials in argv, environment passthrough and MCP payloads without naming the Phase 1 credential channel or bootstrap. Existing `server.py:74–86` creates the server without an authentication context. Implementers would have to invent the security-critical interface.

   **Resolution:** document one concrete Phase 1 bootstrap: how a test/client process receives its credential, how the server verifies it before MCP initialization, how the resulting context reaches every dispatcher, how transport scope is represented, and how credential errors remain generic. Define secret-resolver interfaces with fake test implementations so production secret-store selection need not block fixture-only work. State explicitly that the authenticated stdio path has no anonymous compatibility fallback.

2. **[P1] Resolve the “another principal’s token” test contradiction.**  
   The commission’s auth matrix requires another principal’s token to be denied. `docs/DESIGN-V2.md:61–65` instead derives identity solely from the bearer credential and rejects principal selection alongside it. Under that design, a valid token for B used by a caller claiming to be A is simply an authenticated B request; there is no independent expected identity to mismatch. Session binding can reject switching an established A session to B, and job ownership can reject B accessing A’s jobs, but those are different cases.

   **Resolution:** define the exact matrix: valid B credential on a new connection authenticates B; B credential on A’s established session fails; B credential cannot access A’s jobs or idempotency records. If the requirement instead means a credential copied into another commander’s client must fail before initialization, specify an additional trusted binding, such as transport/channel identity, rather than implying bearer tokens supply it.

3. **[P2] Define revocation for in-flight result delivery.**  
   `docs/DESIGN-V2.md:40–51` rechecks at request entry and launch but explicitly permits existing calls to finish. Existing `supervisor.py:330–335` waits and then returns a result. A principal revoked during a long poll could therefore receive job output after revocation; HTTP streams create the same issue. “Immediately refuse new calls” does not settle whether this is authorized.

   **Resolution:** specify authorization checkpoints for returning job output and emitting stream events. Recommended: revalidate before result delivery, terminate revoked streams, and let cancellation/cleanup continue without returning protected output. Record the decision and add planned tests for revocation during wait and established streaming sessions.

**Later-phase constraint, not a Phase 1 blocker:** the document honestly acknowledges that same-user unrestricted workers cannot enforce hostile recursion (`docs/DESIGN-V2.md:111–121`). Keep that explicit blocked gate. Before HTTP rollout, replace the isolation proposal with a concrete process/credential/network boundary and prove it; a depth header or inherited environment variable is insufficient (`envpolicy.py:18–31`, `supervisor.py:176–177`).

The principal-scoped idempotency, fail-closed registry reload, ownership checks, conservative budget reservations, and structured audit allowlists are sound design directions. Address the three contract gaps above and I can re-review Phase 0 without requiring completed implementation.

---

## v2-phase0-reliability-r1.md

**Verdict: FAIL for beginning Phase 1 implementation.** The architecture has useful reliability safeguards, but the phase plan and persistence contract need clarification first. This is a design review; I changed no files and ran no implementation tests.

1. **[P1] Phase 1 ownership and budgets depend on migration deferred to Phase 2.** `docs/DESIGN-V2.md:164–168` puts ownership, scoped idempotency and budget implementation in Phase 1, but schedules migration in Phase 2. Existing `store.py:19–39` has no principal or budget fields, and `idem_key` is globally unique. Define Phase 1’s schema migration and transaction API explicitly; Phase 2 should introduce daemon lifecycle and transport, not the ownership foundation.  
   **Resolution evidence:** revised phase boundary and a proposed transaction/schema covering principal-owned jobs, compound idempotency, reservations and audit. Tests may still be written afterward, RED first.

2. **[P1] Replay protection has no defined lifetime independent of job pruning.** The design promises “Replay returns no new reservation” at `docs/DESIGN-V2.md:138`, while preserving job pruning without defining durable idempotency receipts. Current `store.py:124–127` deletes terminal jobs, including their idempotency keys; replay after pruning can therefore launch/spend again. A persistent budget ledger alone does not prevent that.  
   **Resolution evidence:** specify durable principal-scoped key/request-hash/task references, retention or expiry semantics, and behavior after result pruning, revocation, restart and accounting-period rollover. Include a planned “prune original job, replay same key, zero new execution/reservation” test.

3. **[P1] The design declares its baseline prerequisite unmet.** `docs/DESIGN-V2.md:197–204` records 89 passing tests and two failures, then explicitly requires diagnosis before Phase 1. The local-config failure is structurally visible: `tests/test_shipped_config.py:12–21` applies a false-`live_verified` assertion to operator-local configuration. The initialization timeout’s cause remains unknown.  
   **Resolution evidence:** reproduce and diagnose both failures without changing production readiness flags or weakening existing assertions. Record a clean, isolated baseline with the exact command/environment and explain any unavoidable difference from the historical 91-test run. Do not present the historical result as current.

**Additional reliability design clarification before Phase 2:** `docs/DESIGN-V2.md:97–102` correctly demands cgroup cleanup before restart, but does not identify the mechanism that blocks a manually started daemon while old workers survive after the owner lock is released. `flock` proves daemon exclusivity, not worker absence. Specify service-only admission or another trusted containment-generation check, including behavior outside systemd and when cleanup remains incomplete. A complete implementation is not required for design approval; an explicit fail-closed state machine is.

The following are sound design intentions, **not verified implementations**: single Store/Supervisor lifetime, lock-before-migration, no PID-based restart killing, conservative unknown-cost reservations, and admission/reservation/audit atomicity.

---

## v2-phase0-scope-r1.md

**Verdict: FAIL — not yet ready to implement Phase 1.** The architecture is pointed in the right direction, but the first implementation slice lacks a concrete authentication contract and an achievable baseline-testing plan. These are design blockers, not requests to finish implementation before approval.

1. **Phase 1 does not define how a stdio caller authenticates.** `docs/DESIGN-V2.md:55–70` describes the later forwarding client and private IPC, but Phase 1 explicitly precedes both HTTP and daemon work. The draft forbids tokens in argv, MCP payloads and environment passthrough without specifying the remaining bootstrap mechanism. The existing entry point accepts only `--config` (`src/spqr_bridge/server.py:155–167`).  
   **Minimal correction:** Specify Phase 1’s exact credential input, verification point, session identity binding and secret-provider interface. For example, an inherited private credential FD supplied by a trusted launcher can carry secret bytes, while a non-secret selector identifies the credential reference. Test with an in-memory fake secret provider; defer production vault selection until before live credential issuance. Do not use an unauthenticated `--principal` flag as identity.

2. **“All 91 tests unchanged” conflicts with deny-by-default unless the test harness migration is designed.** `docs/DESIGN-V2.md:164–165` promises both. However, `tests/test_mcp_stdio.py:25–30` launches a server with no credential and then expects authenticated discovery and execution at lines 31–71. Simply adding deny-by-default breaks this test; preserving anonymous compatibility violates R1. The existing local-config failure is another explicit contradiction: `tests/test_shipped_config.py:21` requires `live_verified=false`, whereas the commission requires preserving the verified local setting. The baseline receipt correctly admits this at `docs/DESIGN-V2.md:195–204`, but offers no resolution path.  
   **Minimal correction:** Define an isolated harness that runs the original assertion files untouched with an explicitly provisioned fake principal/credential and test-only local configuration. Production defaults must remain denying, and test setup must not silently authorize arbitrary callers. Separate the historical suite’s functional assertions from the machine-local readiness assertion, preserving honest receipts for both. Resolve the initialize timeout before feature work, as the draft already requires.

3. **The draft turns builder choices into unnecessary permission dependencies.** `docs/DESIGN-V2.md:183–190` places transport/TLS, isolation design, receipt sourcing and other engineering choices in a collective “awaiting Jimmy” list. The commission expressly gives the builder the stdio-versus-endpoint design decision; Jimmy reserves principal grants, write capability, budget numbers, agent enabling and cutover. His latest instruction appoints subagent reviewers. Requiring him to choose the implementation’s security mechanism or secret-store abstraction risks stalling the authorized work.  
   **Minimal correction:** Split the list into **Jimmy-reserved decisions** and **builder research/proposals**. Inspect available secret storage and tailnet configuration read-only; recommend a concrete mechanism with evidence. Keep principal provisioning, real spend, production installation and cutover gated. Record Jimmy’s reviewer appointment as resolved for this review round.

**Nonblocking observations:** The explicit acknowledgment that HTTP breaks the environment-based recursion proof is good; keep network rollout blocked until confinement is demonstrated (`docs/DESIGN-V2.md:105–121`). The proposed full-budget reservation ledger and unassigned legacy ownership are sensible conservative choices. Neither requires live principal provisioning to begin isolated Phase 1 tests.

**Re-review evidence needed:** A revised design containing the exact Phase 1 authentication flow, the unchanged-test harness plan and a corrected decision/gate table. No production changes or live delegations are needed to clear this design review.

Read-only review; no files changed and no tests executed by this reviewer.

---

## v2-phase0-security-r2.md

**Security re-review verdict: PASS for the revised Phase 1 design contract.** This clears my three Phase 0 security-design blockers. It does not approve implementation, live access, deployment, or the later HTTP/recursion gates. The design itself still prohibits starting Phase 1 until the baseline failures are diagnosed (`docs/DESIGN-V2.md:265–278`).

Evidence: read-only `nl -ba docs/DESIGN-V2.md` inspection of revision 2, compared with my recorded first verdict in `docs/reviews/v2-phase0-security-r1.md`. No implementation tests or live requests were run for this re-review. The only write is this verdict receipt.

1. **Resolved: stdio credential bootstrap.** `docs/DESIGN-V2.md:55–71` now specifies a trusted launcher, SecretResolver contract, bounded single-use anonymous pipe, timeout, pipe-type validation, FD closure, transport-scope verification, authentication before MCP lifespan/initialize, generic failure, and no anonymous fallback. Fixture-only secret resolvers permit isolated work without inventing production credential storage. Implementation review must verify deadline/length enforcement, rejection of ordinary files and wrong FD types, closure on every failure, and no inheritance into workers.

2. **Resolved: another-principal credential matrix.** `docs/DESIGN-V2.md:73–78` explicitly distinguishes authenticating B on a new channel from switching credentials on A's session or accessing A's jobs. It correctly states the copied-bearer limitation. This is a precise interpretation of the commission's ambiguous matrix cell, not proof that copied credentials can be detected. Preserve that distinction in the tests and report.

3. **Resolved: revocation during result delivery.** `docs/DESIGN-V2.md:41–48` now requires revalidation before returning protected output after a wait and before every stream event; revoked streams close and results are withheld while cleanup continues. The implementation review must exercise revocation during long polling and existing streams, including registry read/validation failure, rather than testing only new connections.

**Remaining gated work, not blockers to this Phase 1 contract:** production secret retrieval and worker confinement remain unselected; the design explicitly blocks network rollout until the hostile-recursion boundary is proven (`docs/DESIGN-V2.md:139–155`). Service identity and previous-cgroup cleanup remain Phase 2 feasibility gates (`docs/DESIGN-V2.md:124–137`). Provider hard-cap semantics remain unproven (`docs/DESIGN-V2.md:193–202`). None of these may be reported as solved by this design approval.

**Implementation acceptance reminders:** authentication failure receipts must not introduce an alternate writer to the job store before the owner lock; fixture AuthContext injection must remain test-only; registry validation must reject duplicate credential assignments across principals and transport ambiguity; and safe audit fields must come from trusted enumerations/owned records rather than merely syntax-valid attacker text. These follow the design's single-owner, deny-by-default and secret-free-audit requirements and should be made explicit in the negative tests.

---

## v2-phase0-reliability-r2.md

**Verdict: FAIL for beginning Phase 1 implementation; reliability architecture objections from round 1 are resolved at design level.** No implementation behavior is certified by this re-review.

The revised design puts ownership/budget/audit schema migration in Phase 1 (`docs/DESIGN-V2.md:157–174`), specifies a single atomic admission transaction and conservative crash recovery, and retains principal-scoped idempotency tombstones independently of result pruning and accounting periods (`176–181`). It also defines a fail-closed service-only lifecycle and rejects arbitrary manual production startup before Store access (`130–137`). Those changes resolve my first two round-1 blockers and the additional Phase 2 lifecycle clarification. The actual launcher verification remains an expressly identified Phase 2 feasibility gate, which is appropriate for this design review.

**Remaining [P1] prerequisite: the initialization baseline is unresolved.** The design itself blocks Phase 1 until baseline failures are diagnosed (`docs/DESIGN-V2.md:265–278`). My fixture-only investigation reproduced the existing initialization timeout, then reproduced lack of response using a direct JSON-RPC wire with an empty-agent temporary registry. A third instrumented run proved the child imported the local source and entered the MCP/AnyIO event loop; it did not establish which task/transport stage hung. I stopped after three diagnostic attempts. Commands and outputs are retained in `docs/reviews/v2-baseline-diagnosis.md`; no source/tests, production config/state/services, or readiness flags changed, and no live model call occurred.

Clear this blocker by reporting the three-attempt stop and arranging a new bounded diagnostic step, establishing the initialization cause, and recording the isolated unchanged-assertion baseline proposed at `docs/DESIGN-V2.md:243–253`. Do not describe a suspected transport/lifecycle cause as established or claim a passing baseline. An isolated fixture baseline may coexist with the candid recorded real-local-config readiness failure; it must not erase that evidence.

---

## v2-phase0-scope-r2.md

**Verdict: PASS — the three scope-review design blockers are resolved in revision 2.** This approves the revised design contract from this reviewer's perspective; it does not approve an implementation, claim a green baseline, authorize live credentials or spend, or authorize production cutover.

1. **Stdio authentication now has a concrete Phase 1 flow.** `docs/DESIGN-V2.md:55–71` defines a trusted launcher, a single-use inherited anonymous pipe, a 4096-byte limit and two-second deadline, credential verification before MCP startup, immutable session identity, generic rejection and fixture-only SecretResolver implementations. This is sufficiently concrete to implement tests without selecting a production vault first. Review the actual implementation for FD lifecycle, spoofed/non-pipe FDs, deadline enforcement and absence of inherited secrets at the Phase 1 gate.

2. **The unchanged-test requirement has an explicit isolated harness plan.** `docs/DESIGN-V2.md:243–253` preserves original assertion files, provisions fake authority explicitly through a test plugin/launcher, keeps negative tests outside that bootstrap, and runs the readiness assertion against a conservative copied fixture config. Crucially, it retains the real-config failure receipt instead of claiming that fixture results prove production readiness. Assertion-file hashes and separately recorded harness changes make this auditable. This is an acceptable design resolution, subject to inspecting the actual harness for monkeypatches that bypass the behavior under test.

3. **Builder work and Jimmy-reserved decisions are separated.** `docs/DESIGN-V2.md:255–263` resolves reviewer appointment, reserves actual grants/caps/agent enabling and production changes for Jimmy, and assigns secret-store, TLS, isolation and receipt research to the builder. These investigations can proceed without repeatedly asking Jimmy to choose an engineering mechanism.

**Remaining stop condition:** `docs/DESIGN-V2.md:265–278` explicitly prevents Phase 1 implementation until the baseline failures are diagnosed and review blockers are cleared. The revised document does not itself diagnose the MCP initialize timeout or establish a green baseline. Preserve that stop condition; this PASS must not be reported as permission to skip it or as proof the existing 91 tests pass.

Review method: read `nl -ba docs/DESIGN-V2.md` revision 2 and compare the revised clauses with this reviewer's recorded round-one findings in `docs/reviews/v2-phase0-scope-r1.md`. No implementation tests or live calls were executed. The design was not edited; this verdict was saved to `docs/reviews/v2-phase0-scope-r2.md`.


---

# Round 3 — Jimmy-authorized alternative investigation

Protocol/task tracing replaced timeout-only diagnostics. The parsed opening request had not
reached its consumer at the snapshot; no causal defect or passing baseline was established.
Two server traces completed; the third diagnostic script failed with IndentationError before
server execution. Stopped at three attempts. Source, dependencies, tests and production were
unchanged. Full evidence and diagnostic scripts: reviews/v2-baseline-alternative.md.


## v2-phase0-security-r3.md

**Security diagnostic re-review verdict: Phase 1 implementation gate remains BLOCKED.** Revision 2's security-design PASS remains unchanged; the baseline MCP timeout is narrowed but not explained or repaired. No implementation or production approval follows from these diagnostics.

Evidence inspected without running tests or changing live state: `/tmp/spqr-alternative-attempt1.txt`, `/tmp/spqr-alternative-attempt2.txt`, and the installed SDK source at `mcp/server/stdio.py`, `mcp/server/runner.py`, `mcp/shared/_context_streams.py`, and `anyio/streams/memory.py`. This review writes only its receipt. Attempt 3 is reported by the builder as an IndentationError before a server run; it is not a third successful diagnostic execution or independent confirmation.

**What the receipts establish:** both instrumented runs report that stdin stayed open and a complete initialize line was flushed. Attempt 2 prints a parsed `SessionMessage` containing a JSON-RPC initialize request with ID 1 and protocolVersion 2025-06-18. Its stdin-reader stack reaches `stdio.py:195`, `ContextSendStream.send` at `_context_streams.py:37`, and AnyIO memory-stream `send` at `memory.py:248`. Therefore, for this observed request, OS-pipe reading and JSON parsing succeeded. A theory that the request never reached the server because stdin was already closed, input remained buffered, or fd diversion lost that input does not explain this observation.

The same snapshot shows the server waiting inside `_replay_from_opening_request` (`runner.py:695`), with `opening_request=None` and an empty `lead`. The stdout writer waits for an outgoing message. No initialize response appears in captured stdout. The next unobserved transition is the SDK's internal handoff from the parsed incoming message to the opening-request consumer, before protocol-era selection and normal request handling. SPQR's lifespan was entered (`server.py:82` in the async-generator listing); this snapshot does not show a store-lock startup failure.

**What the receipts do not establish:** `memory.py:248` is the initial cooperative `checkpoint()`, before `send_nowait` or the waiting-sender registration at lines 250–256. A task suspended there is not proof of a permanently blocked rendezvous, broken stream pairing, or AnyIO defect. The printed stream-object addresses alone do not demonstrate whether their underlying send/receive states are correctly paired. A single monitored stack snapshot does not prove deadlock or its cause. The server has not yet selected a protocol era; this does not establish a version-negotiation defect either. Captured `rc=0` is not handshake success when stdout contains no reply.

The earlier fd-diversion/buffering suspicion is now substantially narrowed: it cannot explain loss of this input frame before parsing, although these receipts do not test every possible output-side issue. There is no v2 authentication on the observed v1 path, so adding an auth exception or anonymous bypass would not address the demonstrated failure and would weaken the intended design.

**Required remaining gate:** establish a causal explanation or justified alternative with a behavioral regression test, preserve existing assertions, obtain a green isolated baseline with transparent harness/config provenance, and independently review any resulting fix before Phase 1 begins. Do not patch installed SDK internals, skip the MCP test, weaken authentication, or report the timeout solved from these observations. No further diagnostics were run for this review.

## v2-phase0-reliability-r3.md

**Verdict: FAIL for beginning Phase 1 implementation; reliability architecture remains acceptable at design level.** No implementation gate is passed by this verdict.

The previously identified phase/schema, idempotency-retention and service-startup architecture issues remain addressed by revision 2. This round revisited only the unresolved baseline prerequisite using Jimmy-authorized alternate diagnostics. No source/test, production configuration/state/service, or dependency changes occurred, and no model calls were made.

The initialization blocker is now localized more precisely: the SDK has read and parsed a valid initialize frame and is attempting to send its SessionMessage over the transport's context-aware in-memory channel. The main server remains in `_replay_from_opening_request` (`mcp/server/runner.py:695`) awaiting an opening item, with `opening_request=None` and `lead=[]`; stdout_writer is waiting for a response item that has not been generated. This localizes the observed stall to the transport-to-opening-consumer handoff, before protocol-era selection or initialize dispatch. It does not establish the underlying defect or justify a fix. In these runs there was no import failure, inability to parse the input frame, or response pending in stdout buffering; input remained open.

Two protocol/task-tracing attempts produced those observations. The third deeper instrumentation attempt failed with IndentationError before server execution. That error belongs to the diagnostic script, not SPQR. All three attempts and reproducible scripts are preserved in `docs/reviews/v2-baseline-alternative.md`. No further runs were made after this three-attempt limit.

**Remaining [P1] blocker:** `docs/DESIGN-V2.md` still requires diagnosis and an isolated unchanged-assertion baseline before Phase 1. The root cause and remedy of the observed handoff failure are unproved, and no passing baseline is available. Continue only after reporting this stop and agreeing a new bounded step; do not describe the localization as a proved SDK bug or replace the failed baseline with the diagnostic process's forced exit code zero.

## v2-phase0-scope-r3.md

**Verdicts: Phase 0 scope-design PASS remains; baseline readiness FAIL remains; implementation and delivery are NOT COMPLETE.** The alternative investigation improves localization but does not repair the initialize failure, reproduce the historical green suite, or authorize Phase 1 progression.

Evidence reviewed read-only: `/tmp/spqr-alternative-attempt1.txt`, `/tmp/spqr-alternative-attempt2.txt`, `/tmp/spqr-alternative-attempt3.txt`, and `docs/reviews/v2-baseline-alternative.md`, including the retained reproduction scripts. No further diagnostics or tests were run by this reviewer. No source or test files were changed.

Attempt 2 shows the initialize frame parsed as a SessionMessage and entering ContextSendStream.send. Its snapshot shows the SDK opening-request generator with opening_request=None and lead=[], while stdout is waiting for output. This narrows the observed stage to parsed-input handoff before initialize dispatch in that instrumented run. It does not establish the underlying defect, prove an AnyIO checkpoint bug, or rule out instrumentation/timing effects. The two-second snapshot alone is not proof of a permanent deadlock. The broader original initialize timeout remains the failed baseline evidence.

Attempts 1 and 2 return rc=0 because the diagnostic monitor calls os._exit(0), not because initialization succeeded. Attempt 3 returns rc=1 with IndentationError before the server executes. It is an instrumentation failure and contributes no new bridge-behavior evidence. The alternative report preserves these distinctions honestly; no successful fix or production-health claim is supported.

The three-attempt allowance is exhausted, including the failed instrumentation attempt. The next minimal step is a concise blocker report to Jimmy that cites these receipts, names the unproved cause, and explicitly says Phase 1 has not begun. Do not run a corrected fourth probe, change dependency versions, patch the SDK, increase test timeouts, weaken assertions, or cut over production under this exhausted allowance.

If Jimmy authorizes a fresh bounded repair round, propose one concrete controlled comparison first: a minimal SDK initialize reproduction with fixed instrumentation and a separately isolated dependency/runtime environment, preserving the current environment and original suite. Establish which variable is being tested before executing. A later fix still requires an unchanged-assertion suite receipt, environment provenance, and independent re-review. This is a proposed next investigation, not a claim that runtime/dependency compatibility is the cause.

The revised design's explicit baseline stop condition remains appropriate. Prior Phase 0 scope-design PASS approved the contract, not implementation readiness; it must not be presented as overriding this unresolved baseline FAIL.


---

# Round 4 — authorized 20-attempt diagnostic limit

Jimmy raised this blocker's limit to 20 and instructed continuation. Ten diagnostic/setup
attempts were used in this round. Unchanged stdio integration passes outside sandbox, and
the isolated full suite passes 91 tests with 1 skip. No bridge or test assertion fix was needed.
Reviewers assessed stored commands, hashes, JUnit, environment and config provenance.
The exact sandbox/runtime defect remains unproved; environment-dependent failure is observed.


## v2-phase0-security-r4.md

**Security baseline re-review verdict: PASS for clearing the recorded baseline prerequisite.** The Phase 0 security-design PASS remains unchanged. This verdict approves neither implementation nor production use; Jimmy's design approval and the commission's later independent implementation gates remain separate.

Evidence reviewed without running further tests: `docs/reviews/v2-baseline-resolution.md`; `baseline-round4/isolated_baseline.py`, `verification.json`, `inventory-verification.json`, `baseline-output.txt`; and the reusable `skills/spqr-stdio-baseline/SKILL.md`. My independent sandbox diagnostics are preserved in `docs/reviews/v2-baseline-security-round4.md` with exact commands and observed outputs.

The full retained output reports **91 passed, 1 skipped in 28.87s**. The verification receipt records exit 0, 22 source/test/package/document files matching before and after, unchanged real local config, and import from the temporary project's source. Supplementary verification records matching complete relative source/test inventory (19 files), matching example config, and the isolated environment/import path. The harness excludes real state and local config, constructs a conservative local fixture config from the example, removes live-smoke and principal environment overrides, and builds offline/frozen with the original Python interpreter version. The existing assertion files are copied unchanged. This is credible evidence of an isolated v1 baseline, not a result manufactured by weakening assertions or changing production readiness.

The unchanged real-checkout integration passing outside sandbox, together with my controls (memory/replay success and async-pipe timeout even without MCP transport), justifies treating the earlier timeout as execution-environment-dependent rather than introducing an unproved bridge/SDK patch. Precise runtime cause remains unproved. The actual local readiness assertion mismatch is preserved and explained by the difference between operator-local readiness and conservative fixture configuration; isolated green does not erase that real-checkout result or establish production health.

**Limits:** the harness's import assertion is a substring check, UV_PROJECT_ENVIRONMENT is not explicitly removed, and subprocesses have no outer timeout. These weaken reuse robustness but do not invalidate this completed run because the retained actual import/environment paths and supplementary inventory verification establish the observed provenance. Tighten these checks before relying on the harness in a different environment. The diagnostic skill accurately preserves the boundary, attempt-limit, unchanged-assertion, and no-production-patch lessons.

No security-design blocker from my first review remains. Proceeding with Phase 1 still requires Jimmy's design approval; Phase 1 implementation must be tested and independently reviewed afterward. Secret-store selection, hostile worker recursion isolation, service containment identity/cleanup, and hard provider-budget semantics retain their explicit future gates. No real principal grants, credentials, spend, cutover or push are authorized by this review.

## v2-phase0-reliability-r4.md

**Verdict: PASS for the reliability design and baseline prerequisite to begin isolated Phase 1 work.** This approves a proposed implementation contract, not an implementation, live deployment, security gate, principal grant, credential issuance, budget number or cutover.

I independently read `docs/reviews/v2-baseline-resolution.md`, the executed isolated-baseline harness, `baseline-round4/baseline-output.txt`, `verification.json`, and `inventory-verification.json`. The recorded complete suite result is **91 passed, 1 skipped in 28.87s**, exit code 0. The provenance checks report 22 copied files matching original before/after, the complete 19-file non-generated source/test inventory matching, the temporary import path under `/tmp/spqr-isolated-baseline-ell2ucn1/src/spqr_bridge`, the example config matching, and the actual operator-local config unchanged. The harness uses offline frozen dependency setup and a conservative local fixture config in the temporary project. I did not rerun the suite or make implementation changes during this review.

That evidence clears my previous baseline blocker. The original real-checkout **89 passed, 2 failed, 1 skipped** result remains valid for its sandbox/environment and live operator config; the isolated green result does not erase the legitimate live_verified mismatch or establish production health. Earlier tracing plus the unchanged stdio test's outside-sandbox pass supports environment-dependent async I/O/wakeup behavior; its precise runtime/syscall mechanism remains unproved. No production heartbeat, transport rewrite or dependency change is justified by this baseline investigation.

The revised design continues to resolve my architecture objections: Phase 1 owns schema migration and the atomic principal/job/reservation/audit transaction; durable scoped idempotency receipts survive pruning and period rollover; uncertain billing retains conservative holds; service-only production admission requires previous containment cleanup before Store access. Exact launcher identity and cleanup verification remain Phase 2 feasibility work, not behavior certified by this design verdict.

Phase 1 must preserve the recorded assertion files, write new behavioral tests RED first, use isolated fixture credentials/state, and receive the required independent adversarial implementation review before Phase 2. Any newly discovered contract incompatibility must be resolved explicitly rather than concealed by the harness. The retained round-1, round-2 and round-3 FAIL verdicts remain honest historical receipts.

## v2-phase0-scope-r4.md

**Verdict: PASS — isolated v1 baseline provenance is adequate and the baseline blocker is cleared. Phase 0 scope-design PASS remains. No v2 implementation, production-health or cutover approval is implied.**

Evidence reviewed: `docs/reviews/v2-baseline-resolution.md`, the retained `baseline-round4/isolated_baseline.py`, `setup-output.txt`, `baseline-output.txt`, `baseline-junit.xml`, `verification.json`, `inventory-verification.json`, and `skills/spqr-stdio-baseline/SKILL.md`. This re-review did not rerun tests or diagnostics or modify source/tests.

The setup log records CPython 3.14.8, a new .venv, and the package built/installed from `/tmp/spqr-isolated-baseline-ell2ucn1`. The recorded import path is that temporary project's `src/spqr_bridge/__init__.py`, addressing the actual-run risk of importing the original editable checkout or using another environment. `baseline-output.txt` reports **91 passed, 1 skipped in 28.87s**; JUnit independently records 92 tests, zero errors, zero failures and one skip. These are consistent receipts for the completed run.

`verification.json` records equality for 22 copied source/test/package/README files and the unchanged real-local-config hash. The supplementary `inventory-verification.json` records complete source/test relative-file-set equality for 19 non-generated files, matching example config, and the isolated environment/import path. That supplementary check addresses the earlier harness limitation that its original loop verified initial entries but would not detect added files. The conservative local config is explicitly a temporary fixture: it does not falsify or erase the original real-checkout readiness assertion failure.

The unchanged stdio integration test passing outside the sandbox supports an environment-dependent failure classification. The exact sandbox/runtime wakeup defect remains unproved. No heartbeat, SDK patch, timeout increase, assertion weakening or live dependency modification is justified by these receipts. The historical 89-pass/two-failure receipt remains true for its original environment and local config; the isolated green is a separate, accurately scoped result.

Reusable skill review: PASS for preserving the operational lessons, attempt limits, forced-exit caveat, isolated fixture provenance, and honest separation of baseline success from production permissions. Future harness implementations should additionally clear or explicitly set UV_PROJECT_ENVIRONMENT, assert resolved import containment rather than a substring, and use a bounded outer test timeout. Their absence in the retained historical harness does not invalidate this completed run because environment location, import location, inventory and successful termination were separately checked.

Delivery state: the baseline prerequisite is now satisfied; v2 remains design work. Jimmy's design approval, implementation review gates, real principal grants/caps, credentials, live verification and production cutover remain separate. This review authorizes none of those actions.

## Amendment A review (2026-10-21)

Reviewer: independent non-author subagent on a different model (Claude Sonnet), approved by Jimmy.
Author: Claude Opus. Verdict lines and findings preserved as delivered (reflowed into lists; wording unchanged in substance).

### Round 1 — on Amendment A revision 1

VERDICT: FAIL

1. [blocker] No loop bound on the default (no-grant) path. Without a grant there is no round counter (A3), and the retry cap only matches a byte-identical request hash, so an agent defeats it by changing one character of the task. Silence is the common case, so Decision 6's "may not spin forever" is unmet where it matters most. Fix: key no-grant guardrails on principal + agent + workspace (not task text) — e.g., refuse after 3 consecutive non-success terminals, or treat "no grant" as an implicit grant with max_rounds=5 per rolling window — and apply the same no_progress check there.
2. [blocker] Budget text outside the amendment is not marked superseded and still contradicts: line 105 ("budget policy" in registry; also per-principal agent allowlists/access modes/roots — A1 restricts to ceilings only), line 231 ("Currency scale and periods"), lines 232/238–240 ("available funds", cap reservation, settlement in the transaction paragraph), lines 246–248 (reservation/period-rollover test), the entire 260–269 budget paragraph, line 278 audit fields "reservation and actual cost", phase gates 1 and 4 (lines 292, 303: "budget tests", "budget races", "verified cost"), and line 332 "budget increase". Only the Ownership heading got a note. Fix: delete or strike these, or tag each "superseded by Amendment A"; the blanket "amendment wins" sentence is insufficient for a reviewer gate.
3. [blocker] Credential model contradiction: A4 says sha256-of-token in registry, no SecretResolver, but lines 127–137 and 164 still mandate a SecretResolver and registry "secret references". Mark superseded and strike the resolver research from builder work (line 327).
4. [should-fix] A2 claims v2 "neither requires nor reads" max_budget_usd, but adapters.py:39/86 passes `--max-budget-usd` to Claude whenever set. Jimmy ruled no dollar ceilings. Fix: v2 launch path must never emit that flag (test asserts it), or a cap-triggered stop will surface as `failed` and enter retry logic.
5. [should-fix] Usage hard stop is too narrow. After a usage stop only the *identical* no-grant request is refused, or only the stopped grant closes; a slightly different request, or another principal, immediately retries the exhausted shared pool. Fix: record a per-agent usage_stop marker that refuses all new starts to that agent (for every caller) until Jimmy clears it — simpler than per-grant tracking and matches "report and wait for Jimmy."
6. [should-fix] Usage detection is exploitable and weak. Phrase matching on agent output lets model/prompt-injected text ("usage limit reached") force `usage_exhausted` and close a grant (DoS lever). Fix: match only structured adapter error/stderr, never the model's result text; unrecognised exhaustion becomes `failed` → up to 3 retries. State this as a known limit and add a phrase-fixture test per adapter.
7. [should-fix] Grant lifecycle gaps (forge/reuse/steal): (a) round counter, retry counter and grant re-validation must run inside the same BEGIN IMMEDIATE admission transaction or concurrent starts overrun max_rounds; (b) re-resolve the grant (expiry/removal) again at queued launch, as the registry is; otherwise a revoked grant still runs queued jobs; (c) define whether an idempotent replay, refused start, or pre-launch failure consumes a round (suggest: only admitted jobs; replays don't); (d) snapshot the effective scope into the job row (jobs.grant_id + resolved scope) so later registry edits don't widen a running job; (e) state grants are single-principal and expiry is mandatory with a short maximum. A grant reusable for up to 10 tasks is closer to a short-lived standing grant than "per job at task time"; say explicitly this is the accepted reading.
8. [should-fix] Sub-delegation "no broader than parent" has no mechanism and is vacuous at depth 1. Say so plainly; don't add inheritance code now; tie any future depth to the recursion gate.
9. [nit] sha256 of token is acceptable only if the token is ≥128-bit random: require `secrets.token_urlsafe(32)`, no salt/KDF needed, `hmac.compare_digest` on the digest, and a `credential_id.secret` token format so lookup doesn't scan principals. Never log the digest.
10. [nit] no_progress comparison should use a stored hash of normalized result/error text on the job, not raw text — timestamps/nondeterministic output defeat it. Note it is best-effort.
11. [nit] Simplification: drop `retry_cap_reached` as a separate hash-matching mechanism — one per-principal/agent/workspace consecutive-failure counter covers both grant and no-grant cases. Drop the separate unauthenticated line file if a single audit sink can be used safely.
12. [nit] Rename "usage" in adapters.py USAGE_KEYS (token counts) or note it is unrelated to the new usage-exhaustion detection.

### Round 2 — on Amendment A revision 2

VERDICT: PASS

Revision 2 resolves findings 1–12. The remaining items are small, and none block Phase 1.

1. [should-fix] The superseded tags are applied line by line inside multi-line budget paragraphs. Untagged lines still read as live: 251, 253, 261, 275, 277–279. Commission line 11 still says "budgets". Line 152 ("resolver selected implicitly") is also untagged. Fix: one tag at the start of each superseded paragraph, or strike the paragraphs; remove "budgets" from line 11.
2. [should-fix] The grant's expiry field is not defined, so "no more than 24 hours after issue" is unenforceable. Fix: require issued_at and expires_at; registry validation rejects the whole load (fail closed) if expires_at - issued_at > 24h, if issued_at is in the future, or if either field is missing. Treat a missing max_rounds as 5.
3. [nit] The no-grant lineage can be reset by switching agent or workspace, or by waiting 60 minutes. Acceptable given the ceilings. State plainly that the bound is 3 failures and 5 rounds per lineage per hour; a principal with many ceiling roots can multiply it. Optionally add a per-principal hourly start ceiling later; not now.
4. [nit] "Synchronous admission" depends on SQLite calls being synchronous in the single owner process. Add one concurrency test (N simultaneous start_task calls against max_rounds=5 produce exactly 5 admissions).
5. [nit] Specify that usage_cleared compares against the stop's recorded UTC time, and reject a clear time in the future.
6. [nit] Implement the no-progress tripwire and the failure counter as one query and one helper so they cannot drift.
7. [nit] Keeping the separate auth-failure line file is justified. Cap its size and line length and write only allowlisted fields so an unauthenticated flood cannot fill the disk.
8. [nit] Fidelity is now complete. Additions beyond the rulings are limited to the launch helper and the usage_cleared line; both are reasonable and simple.

Security re-check: grant IDs are checked for ownership, expiry, per-principal use and re-check at launch, and the scope is snapshotted on the job, so no forge, widen, reuse or steal path was found. Hashing high-entropy tokens with sha256 is acceptable with compare_digest. No fail-open path found.

Author's disposition of round 2: items 1, 2, 3, 4, 5, 6 and 7 applied in revision 3 of the amendment and covered by tests (registry validation, concurrent admission, bounded auth log). Round 1 item 9's `credential_id.secret` format and item 10's stored digest were not adopted: principals are few, so a full constant-time scan is simpler, and the tripwire compares stored state/result/error directly (best effort, as stated).

## Phase 1 implementation review (2026-10-21)

Reviewer: independent non-author subagent on a different model (Claude Sonnet). Author: Claude Opus.

Round 1: VERDICT: FAIL. Ten findings: [blocker] write jobs could edit the registry (it could sit inside an allowed root); [should-fix] job pruning erased usage stops and loop-cap history; launch re-check ignored principal ceiling changes; v1 jobs would start reporting usage_exhausted; cancelled jobs could trip no_progress; [nit] 1-minute future tolerance on grant/clear times; refused get/cancel unaudited; list_agents advertised write without a grant; named-FIFO limit undocumented; workspace probing outside scope revealed existence.

Round 2 (after fixes, each with a test written first): VERDICT: PASS. All ten fixed. Remaining nits: the launcher's token file is not location-checked like the registry (documented in Amendment A4: it belongs outside every root); the registry-location check runs after authentication (harmless).

Evidence: `.venv/bin/python -m pytest -q -p no:cacheprovider` → 172 passed, 2 skipped (the same two skips as the clean baseline: no bridge.local.toml in the copy, live smoke not requested). The 90 pre-existing tests pass with their files byte-identical (sha256 checked before and after).

## Client slot (scripts/spqr-v2, slot.py, CLIENTS.md), 2026-10-21

Independent Sonnet review, round 1: **VERDICT: PASS**, no blockers or should-fix. Seven nits. Fixed:
newline in slot name (case pattern), missing-.venv message, doc wording (registry outside
allowed_roots, no symlinks, how to re-issue). Left: comma in --roots paths, non-BMP chars in
json.dumps, pre-existing DIR mode not checked, Claude Code/Codex/OMP command syntax not verified
on a machine with those clients.
