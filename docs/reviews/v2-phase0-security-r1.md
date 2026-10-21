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
