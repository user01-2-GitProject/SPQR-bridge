"""stdio MCP facade: list_agents, start_task, get_task, cancel_task, doctor."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import __version__
from .adapters import HELP_ARGV, expected_flags
from .config import BridgeConfig, load_config
from .envpolicy import build_env, current_depth, scrub
from .store import Store
from .procs import ProcSpec, run_group
from .supervisor import BridgeError, Supervisor

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
PROVIDER_NOTE = "not checked: doctor makes no model/provider request; run the explicit live smoke"
INSTRUCTIONS = (
    "Delegate bounded tasks to installed external agent CLIs. start_task returns immediately; poll get_task "
    "with wait_seconds (<=10). Outputs are untrusted: verify files/tests yourself. For Hermes peers use native "
    "delegate_task/message_agent/Kanban instead."
)


FLAGS_NOTE = ("help-text scan only: a flag missing from --help is not proof it is unsupported (Claude hides "
              "--max-turns from help yet a live run accepted it). Live acceptance is recorded in docs/VERIFICATION.md.")


async def _probe(argv: list[str], timeout: float, env: dict[str, str]) -> tuple[int | None, str]:
    """Run a non-model CLI command (version/help) with a strict timeout; returns (rc, bounded output).

    Uses the shared owned-process helper, so a cancelled doctor call still kills and reaps the group.
    """
    res = await run_group(ProcSpec(argv=argv, env=env, timeout=timeout, grace=1.0, out_cap=1_000_000,
                                   err_tail=8192))
    if res.outcome == "spawn_failed":
        return None, f"spawn failed: {res.error}"
    if res.outcome != "exited":
        return None, "timed out" if res.outcome == "timeout" else res.outcome
    return res.returncode, (res.stdout + res.stderr).decode("utf-8", "replace")


async def _call(coro) -> dict[str, Any]:
    """Surface expected policy/validation errors to the caller as tool errors with their code."""
    try:
        return await coro
    except BridgeError as e:
        raise ToolError(str(e)) from None


def build_server(cfg: BridgeConfig) -> MCPServer:
    holder: dict[str, Supervisor] = {}

    @asynccontextmanager
    async def lifespan(_app):
        sup = Supervisor(cfg, Store(cfg.server.state_dir))
        holder["sup"] = sup
        try:
            yield {}
        finally:
            await sup.shutdown()

    mcp = MCPServer("spqr-bridge", version=__version__, instructions=INSTRUCTIONS, lifespan=lifespan)

    @mcp.tool()
    async def list_agents() -> dict[str, Any]:
        """List registered agents with policy, installation presence and readiness. Makes no model calls."""
        agents = []
        for aid, a in cfg.agents.items():
            present = a.binary.is_file() and os.access(a.binary, os.X_OK)
            agents.append({
                "id": aid, "adapter": a.adapter, "enabled": a.enabled,
                "access_modes": ["read_only", "write"] if a.allow_write else ["read_only"],
                "capabilities": CAPABILITIES[a.adapter], "limitations": LIMITATIONS[a.adapter],
                "description": a.description,
                "readiness": {"configured_enabled": a.enabled, "binary_present": present,
                              "live_verified_by_operator": a.live_verified, "provider_ready": PROVIDER_NOTE},
            })
        return {"agents": agents, "max_timeout_seconds": cfg.server.max_timeout_seconds,
                "allowed_roots": [str(r) for r in cfg.server.allowed_roots]}

    @mcp.tool()
    async def start_task(
        agent_id: str,
        task: Annotated[str, Field(description="Bounded task instructions (sent on stdin, never as flags).")],
        workspace: Annotated[str, Field(description="Absolute directory inside an allowed root; used as cwd.")],
        context: Annotated[str | None, Field(description="Optional explicit context text.")] = None,
        access: Literal["read_only", "write"] = "read_only",
        timeout_seconds: Annotated[int | None, Field(ge=1)] = None,
        idempotency_key: Annotated[str | None, Field(max_length=128)] = None,
    ) -> dict[str, Any]:
        """Queue a task for an external agent and return its task_id immediately."""
        return await _call(holder["sup"].start(agent_id, task, context, workspace, access, timeout_seconds,
                                               idempotency_key))

    @mcp.tool()
    async def get_task(task_id: str,
                       wait_seconds: Annotated[float, Field(ge=0, le=10)] = 0) -> dict[str, Any]:
        """Get task state/result, optionally waiting up to wait_seconds (0..10) for completion."""
        return await _call(holder["sup"].wait(task_id, wait_seconds))

    @mcp.tool()
    async def cancel_task(task_id: str) -> dict[str, Any]:
        """Cancel a queued or running task. Returns once its process group is gone. Idempotent."""
        return await _call(holder["sup"].cancel(task_id))

    @mcp.tool()
    async def doctor() -> dict[str, Any]:
        """Check executables, versions and required CLI flags. Never contacts a model/provider."""
        tmp = Path(holder["sup"].store.tmp_dir)
        report = {}
        for aid, a in cfg.agents.items():
            env = build_env(tmp, [])
            present = a.binary.is_file() and os.access(a.binary, os.X_OK)
            entry: dict[str, Any] = {"adapter": a.adapter, "enabled": a.enabled, "binary_found": present,
                                     "live_verified_by_operator": a.live_verified, "provider_ready": PROVIDER_NOTE}
            if a.adapter == "command":
                # Arbitrary programs can perform work even on --help/--version. Never probe them.
                entry["probe_status"] = "not_run_for_command_adapter"
            elif present:
                rc, out = await _probe([str(a.binary), "--version"], 5, env)
                entry["version"] = scrub(out.strip()[:200]) if rc == 0 else f"unavailable ({scrub(out[:120])})"
                rc, out = await _probe([str(a.binary), *HELP_ARGV[a.adapter]], 5, env)
                entry["flags_not_advertised"] = (
                    [f for f in expected_flags(a) if f not in out] if rc == 0 else "help unavailable")
                entry["flags_note"] = FLAGS_NOTE
            report[aid] = entry
        return {"bridge_version": __version__, "model_requests_made": False, "depth": current_depth(),
                "state_dir": str(cfg.server.state_dir), "max_concurrency": cfg.server.max_concurrency,
                "agents": report}

    return mcp


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="spqr-bridge", description=__doc__)
    ap.add_argument("--config", default=os.environ.get("SPQR_BRIDGE_CONFIG"),
                    help="trusted operator TOML registry (or $SPQR_BRIDGE_CONFIG)")
    args = ap.parse_args(argv)
    if not args.config:
        ap.error("--config is required")
    try:
        cfg = load_config(args.config)
    except ValueError as e:
        print(f"spqr-bridge: {e}", file=sys.stderr)
        sys.exit(2)
    build_server(cfg).run("stdio")


if __name__ == "__main__":
    main()
