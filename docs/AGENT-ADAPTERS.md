# Adding an agent runtime

The bridge distinguishes the **client** calling MCP (a principal/slot) from the
**runtime** doing a delegated task (an agent entry in bridge configuration).
See CLIENTS.md for clients. New agent IDs can use a native adapter (`claude`,
`codex`, `omp`) or the operator-configured `command` adapter without rebuilding
the bridge. Adding a runtime does not grant any principal permission to use it.

## Operator-configured command

Choose an installed executable whose documented noninteractive mode accepts the
whole task on stdin and emits its final response as plain text on stdout. Configure
the CLI's own read-only/permission controls in its fixed arguments. For example,
the shape of an entry is:

```toml
[agents.my_runtime]
adapter = "command"
binary = "/absolute/path/to/installed-agent"
argv = ["documented-stdin-mode", "documented-read-only-option"]
enabled = false
allow_write = false
live_verified = false
description = "Operator-configured runtime; not live verified"
```

The arguments above describe the configuration shape, not real CLI flags. Replace
them with arguments verified against the installed runtime before enabling it.
The executable and each argument are passed directly to process execution, with
no shell added by the bridge. Arguments are literal: `$HOME`, `{workspace}`, and
similar strings are not expanded. The process's working directory is the pinned
workspace; task and optional context go only to stdin. Callers cannot override
arguments, executable, environment, model, or parser. The existing environment
allowlist, timeout, output limits, process-group cancellation, principal/grant
checks, usage stops, and loop caps still apply.

`argv` permits at most 64 strings, each at most 4096 characters, at most 16384
UTF-8 bytes total, without control characters. Native adapters reject nonempty
`argv`, so configuration cannot bypass their established invocation controls.

Generic command jobs support only `read_only`; configuration with
`allow_write = true` is rejected. **This does not enforce filesystem confinement.**
The operator must supply and verify the runtime's native restrictions, or run it
under external confinement. Do not enable an unrestricted runtime for untrusted
instructions merely because the bridge labels its access read-only. The operator
configuration and executable are trusted code, not caller-controlled data.

## Results and readiness

For `command`, success means exit status zero plus nonempty stdout. This is an
execution receipt, not independent proof of task correctness or semantic success;
a runtime that prints an error but exits zero cannot be diagnosed generically.
Empty output and nonzero exits fail. No structured session ID or usage metrics
are inferred from text. Existing backend-failure usage-exhaustion detection remains
best effort; an unknown quota error may remain `failed`. No automatic fallback or
retry is introduced.

`doctor` reports executable presence but **does not execute** command-adapter
programs, including with `--help` or `--version`: those switches are not guaranteed
to be side-effect-free on arbitrary executables. It reports
`probe_status = "not_run_for_command_adapter"`. A bounded read-only live smoke
and output review are required before claiming a new runtime works. This feature
does not install programs, log in, enable agents, issue slots, or alter live config.
