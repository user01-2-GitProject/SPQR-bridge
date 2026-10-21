---
name: spqr-stdio-baseline
description: Diagnose SPQR MCP stdio initialization timeouts and reproduce the original test baseline without changing live configuration or weakening assertions.
---

Use when SPQR's fake-agent MCP stdio initialization hangs, especially inside an agent sandbox.

The 2026-10-20 investigation observed the same unchanged integration test time out inside the
sandbox and pass outside it. Minimal memory streams worked; worker-thread completion in the
sandbox was delayed until an unrelated timer. This is evidence of environment-dependent I/O,
not proof of a particular AnyIO or SDK defect. Reverify rather than assuming every timeout has
this cause. See docs/reviews/v2-baseline-round4.md and docs/reviews/v2-baseline-alternative.md.

- Preserve production: never stop the gateway, open its Store, change readiness flags, or make
  live model calls for this diagnosis. Use FAKE fixtures and isolated state; unset live-smoke flags.
- Keep an attempt ledger and use the current user-specified limit. Report exhausted limits;
  do not silently reset them or hardcode the temporary 20-attempt authorization as a future default.
- First compare the exact unchanged failing test across the relevant execution boundary. An
  outside-sandbox run requires the execution tool's normal escalation/review mechanism. Do not
  add a heartbeat, increase timeouts, edit installed dependencies or patch transport just to pass.
- If tracing is needed, keep stdin open, send one complete flushed JSON-RPC frame, and distinguish
  parsing, memory-stream delivery, dispatch and generated output. A forced monitor exit code zero
  is not successful initialization. A snapshot at checkpoint is not proof of deadlock.
- To reproduce the v1 baseline independently, copy current working source/tests (including
  uncommitted work), pyproject.toml, uv.lock, README.md and example config to a temporary project.
  Do not copy state, real local config, Git metadata or the live virtual environment. Create an
  explicit conservative local fixture config in the copy. Use the same interpreter version;
  Python 3.13 and 3.14 require different cached binary wheels. Build offline/frozen when cached.
- Confirm imports resolve to the temporary copy. Hash source and test files before/after,
  compare exact relative file sets, and verify real local config unchanged without printing it.
  Retain setup logs, environment/interpreter versions, full pytest output and JUnit evidence.
- Preserve both outcomes: an isolated fixture result does not erase the real local readiness
  assertion mismatch. Do not change live_verified to satisfy an old conservative-config test.
- Have independent reviewers assess evidence and scope before clearing a blocked gate. Store
  their verdicts verbatim, including failures, in docs/REVIEW-V2.md. A baseline PASS does not
  grant principals, spend, production cutover or approval for subsequent implementation phases.
