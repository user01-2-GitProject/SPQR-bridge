"""Shared adapter capabilities and permission limitations for client discovery."""

CAPABILITIES = {
    "claude": "Claude Code agent loop via `claude -p --output-format json`. read_only: --restricted, tools "
              "Read/Grep/Glob, permission-mode dontAsk. write: adds Edit/Write with acceptEdits. Never Bash.",
    "codex": "Codex agent loop via `codex exec --json` with native sandbox read-only or workspace-write; "
             "--ephemeral, --ignore-user-config.",
    "omp": "Oh-My-Pi agent loop via `omp -p --mode json` (best-effort output parsing).",
    "command": "Operator-configured executable and fixed argv, task on stdin, plain-text stdout.",
}
LIMITATIONS = {
    "claude": ["Enforcement is Claude Code's own permission/tool system; the workspace allowlist only sets cwd "
               "and is not an OS sandbox.", "Read tools may read files outside the workspace if Claude permits."],
    "codex": ["--ignore-user-config ignores ~/.codex/config.toml (model, profiles, MCP servers), so Codex uses its "
              "built-in default model.", "Live provider readiness not verified on this machine."],
    "omp": ["No enforced file restriction (read_only is prompt-level only); disabled by default.",
            "JSON output schema not verified; parsing is best-effort."],
    "command": ["No bridge-enforced filesystem confinement; read_only is policy, not an OS sandbox.",
                "Operator must configure native restrictions; write grants are unsupported.",
                "Exit zero and nonempty stdout do not independently prove task completion; no structured usage metrics."],
}
