# Baseline resolution — 2026-10-20

Jimmy authorized continuation and raised this blocker's diagnostic limit to 20. This round
used 10 diagnostic/setup attempts: parent 5, reliability 2, security 3. No source, assertion,
installed dependency, production configuration, state or service changes. No live model calls.

## Environment comparison

Exact existing command, outside sandbox through normal require_escalated review:
`.venv/bin/python -m pytest -q -p no:cacheprovider --tb=short tests/test_mcp_stdio.py::test_mcp_stdio_all_tools`
Observed exit 0: **1 passed in 11.24s**. Earlier sandbox run of this unchanged test failed
initialize after 42.45s (v2-baseline-diagnosis.md). Same project interpreter/source.

Reliability's minimal probe: bare/context memory channels complete immediately; thread
completion observed at 1.502 seconds without heartbeat and .101 seconds with 25ms heartbeat
inside sandbox. Security's independent tests: in-memory replay succeeds; async pipe read
can hang even after removing MCP. See v2-baseline-round4.md for reliability commands/scripts.
The observed failure is environment-dependent async I/O/wakeup behavior, not evidence for a
bridge auth/SDK fix. Precise sandbox/runtime syscall defect remains unproved. Do not introduce
a heartbeat or transport patch into production to compensate for this test environment.
Earlier memory-channel snapshots are not proof of a memory-channel defect: a diagnostic timer
can wake the loop and exit before a queued sender resumes.

## Full isolated baseline

Executed script: `baseline-round4/isolated_baseline.py`, invoked using original .venv Python
outside sandbox. Temporary project: `/tmp/spqr-isolated-baseline-ell2ucn1`.
`uv sync --offline --frozen --python <original-project>/.venv/bin/python` created a separate
Python 3.14 environment. Import probe returned the temporary project's src path. Full command:
`/tmp/spqr-isolated-baseline-ell2ucn1/.venv/bin/python -m pytest -q -p no:cacheprovider --tb=short --junitxml=/tmp/spqr-isolated-baseline-ell2ucn1/baseline-junit.xml`

Observed exit 0: **91 passed, 1 skipped in 28.87s**. All original assertion files unchanged.
Live-smoke/config/PYTHONPATH/VIRTUAL_ENV environment overrides removed for run. Conservative
example copied to temporary local config; actual operator-local config never copied or edited.
Hash verification: 22 copied source/test/package/README files match original before/after;
real local config hash unchanged. Supplementary complete source/test inventory comparison
confirms all 19 non-generated files match, including relative file sets; example config matches.
The actual temporary import path and environment creation were checked. Original real-checkout
89-pass/2-fail receipt remains valid for that environment/config; isolated green does not mean
its live_verified assertion now passes or prove production health.

Two setup failures are retained as facts: initial uv auto-selected Python 3.13 and lacked its
cached pydantic-core wheel; next attempt selected correct Python 3.14 but omitted the required
README from the temporary copy. Final setup included README and succeeded. No dependencies
were downloaded or changed in the live environment.

Artifacts in baseline-round4/: full pytest output, JUnit, setup log, executed harness, hashes,
verification result and supplementary inventory check. Harness review noted stricter future
UV_PROJECT_ENVIRONMENT clearing, import containment checking and outer timeout are advisable;
the actual path, completed exit and supplementary inventory establish this run's provenance.
This is v1 baseline reproduction, not the future Phase 1 auth harness.

## Scope

Clears the baseline blocker subject to independent re-review. Design approval, principal grants,
cap numbers, credentials, implementation gates and production cutover remain distinct. No
Phase 1 implementation began. Reusable diagnostic skill added under skills/spqr-stdio-baseline;
its validator reports `Skill is valid!`.
