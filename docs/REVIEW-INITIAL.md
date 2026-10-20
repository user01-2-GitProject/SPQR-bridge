# Initial independent release review — blocked

**Verdict:** do not activate the initial build. The independently rerun baseline was `64 passed, 1 skipped`, and an actual MCP → Claude → Read-tool nonce check succeeded. These establish the happy path, not safe lifecycle behavior.

Reviewer: isolated read-only subagent, returned structured failed review. No source changes or live agent/model calls by the reviewer. Findings below were reported from deterministic fixture subprocess reproductions; the hardening regression suite must reproduce and close each one. Parent directly corroborated the malformed-success and invalid-JSON problems.

| Gate | Initial finding | Required release evidence |
|---|---|---|
| Workspace identity | Replacing a queued workspace with a symlink allowed actual cwd outside the root | Pin opened directory identity; reject substitution; close final pathname check/use race; regression |
| Repeated cancellation | Second cancellation released slot/lease before SIGTERM-ignoring group died | Single-owner stop/cleanup; concurrent callers wait until cleanup; regression |
| Spawn cancellation | Cancellation during pipe initialization orphaned an ordinary same-group descendant | Own creation to settlement and cleanup; native spawn-window regression |
| Overlapping workspace leases | Parent and child directories admitted concurrent writers | Conservative global reader/writer exclusion or correct hierarchical overlap; regression |
| Unexpected runner exceptions | Wrong-type session metadata caused SQLite error and permanently running record | Validate metadata, cleanup, terminal failed record; regression |
| Usage metadata | Slicing serialized usage to 4000 chars produced unreadable successful record | Structurally bounded valid JSON; defensive old-record reads; regression |
| Success semantics | Claude `{}`/system objects and Codex progress-only stream returned success | Affirmative final envelope and completed turn; regression |
| Doctor cancellation | Cancelled probe left subprocess alive | Same ownership/cleanup discipline for probes; regression |
| In-memory retention | Task/reason/lease maps grew despite SQLite retention | Remove completed controls safely; repeated-job/descriptor regression |

Other required corrections: expose bounded permission-denial metadata; distinguish help-unadvertised flags from unsupported flags; state Linux scope; document that abrupt bridge death may leave agent processes alive and restart reconciliation is not proof of termination. Keep editing disabled and explain that the project root contains the bridge control plane.

The repair assignment is bounded, regression-first, uses one implementation owner, preserves the architecture and original PNGs, and makes no global/profile/auth changes. Final acceptance requires parent-run tests, a second targeted independent review, a new harmless live MCP nonce check, and verified native Hermes registration.
