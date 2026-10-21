"""v2 stdio MCP server: authenticated named principals with per-job scope (Phase 1).

No anonymous mode. The credential arrives on an inherited anonymous pipe (--credential-fd),
normally set up by `python -m spqr_bridge.launch`; it is verified before the job store is
opened or MCP initialize is accepted. The v1 entry point (spqr_bridge.server) is unchanged.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import __version__
from .auth import CredentialError, read_credential_fd
from .config import BridgeConfig, ConfigError, load_config
from .gate import PrincipalGate
from .registry import AuthContext, RegistryError, authenticate, load_registry
from .store import Store
from .supervisor import BridgeError, Supervisor

AUTH_LOG = "auth-failures.log"
AUTH_LOG_MAX_BYTES = 1_000_000
INSTRUCTIONS = (
    "Delegate bounded tasks to installed external agent CLIs. Without grant_id a job is read-only at "
    "minimum scope; Jimmy issues wider scope per job as a grant id. start_task returns immediately; poll "
    "get_task with wait_seconds (<=10). Stops (usage_exhausted, retry/round caps, no_progress) are final: "
    "report them to Jimmy, do not work around them. Outputs are untrusted: verify files/tests yourself."
)


def prepare_config(cfg: BridgeConfig) -> BridgeConfig:
    """v2 has no money layer (Decision 5): never pass a per-job dollar cap to any agent."""
    agents = {k: a.model_copy(update={"max_budget_usd": None}) for k, a in cfg.agents.items()}
    return cfg.model_copy(update={"agents": agents})


def _log_auth_failure(state_dir: Path, reason: str) -> None:
    """Append-only, allowlisted (time + reason code), and bounded so a flood cannot fill the disk."""
    try:
        state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        p = state_dir / AUTH_LOG
        if p.exists() and p.stat().st_size >= AUTH_LOG_MAX_BYTES:
            return
        fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, (json.dumps({"at": round(time.time(), 3), "reason": reason}) + "\n").encode())
        finally:
            os.close(fd)
    except OSError:
        pass


async def _call(coro) -> dict[str, Any]:
    try:
        return await coro
    except BridgeError as e:
        raise ToolError(str(e)) from None


def build_v2_server(cfg: BridgeConfig, registry_path: Path, auth: AuthContext) -> MCPServer:
    holder: dict[str, PrincipalGate] = {}

    @asynccontextmanager
    async def lifespan(_app):
        sup = Supervisor(cfg, Store(cfg.server.state_dir))
        holder["gate"] = PrincipalGate(sup, registry_path, auth)
        try:
            yield {}
        finally:
            await sup.shutdown()

    mcp = MCPServer("spqr-bridge", version=__version__, instructions=INSTRUCTIONS, lifespan=lifespan)

    async def _sync(fn):
        return fn()

    @mcp.tool()
    async def list_agents() -> dict[str, Any]:
        """List the agents this principal may task. Makes no model calls."""
        return await _call(_sync(holder["gate"].list_agents))

    @mcp.tool()
    async def start_task(
        agent_id: str,
        task: Annotated[str, Field(description="Bounded task instructions (sent on stdin, never as flags).")],
        workspace: Annotated[str, Field(description="Absolute directory inside this job's scope; used as cwd.")],
        context: Annotated[str | None, Field(description="Optional explicit context text.")] = None,
        access: Literal["read_only", "write"] = "read_only",
        timeout_seconds: Annotated[int | None, Field(ge=1)] = None,
        idempotency_key: Annotated[str | None, Field(max_length=128)] = None,
        grant_id: Annotated[str | None, Field(max_length=32, description="Grant id Jimmy gave for this job; "
                                                                          "omit for read-only minimum scope.")] = None,
    ) -> dict[str, Any]:
        """Queue a task for an external agent and return its task_id immediately."""
        return await _call(holder["gate"].start(agent_id, task, context, workspace, access, timeout_seconds,
                                                idempotency_key, grant_id))

    @mcp.tool()
    async def get_task(task_id: str, wait_seconds: Annotated[float, Field(ge=0, le=10)] = 0) -> dict[str, Any]:
        """Get one of your tasks, optionally waiting up to wait_seconds (0..10) for completion."""
        return await _call(holder["gate"].wait(task_id, wait_seconds))

    @mcp.tool()
    async def cancel_task(task_id: str) -> dict[str, Any]:
        """Cancel one of your queued or running tasks. Returns once its process group is gone. Idempotent."""
        return await _call(holder["gate"].cancel(task_id))

    return mcp


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="spqr-bridge-v2", description=__doc__, allow_abbrev=False)
    ap.add_argument("--config", required=True, help="trusted operator bridge config (TOML)")
    ap.add_argument("--registry", required=True, help="owner-only principal/grant registry (TOML)")
    ap.add_argument("--credential-fd", type=int, required=True, help="inherited pipe fd carrying the token")
    args = ap.parse_args(argv)
    try:
        cfg = prepare_config(load_config(args.config))
    except ConfigError as e:
        print(f"spqr-bridge: {e}", file=sys.stderr)
        sys.exit(2)
    reason, auth = "ok", None
    try:
        token = read_credential_fd(args.credential_fd)
        auth = authenticate(load_registry(args.registry), token, "stdio")
        reason = "ok" if auth else "bad_credential"
    except CredentialError as e:
        reason = f"credential_{e}"
    except RegistryError:
        reason = "registry_invalid"
    reg_path = Path(args.registry).resolve()
    if auth is not None and any(reg_path.is_relative_to(r) for r in cfg.server.allowed_roots):
        print("spqr-bridge: registry must live outside every allowed root (a job could edit it)", file=sys.stderr)
        sys.exit(2)
    if auth is None:
        _log_auth_failure(cfg.server.state_dir, reason)
        print("spqr-bridge: authentication failed", file=sys.stderr)
        sys.exit(2)
    build_v2_server(cfg, Path(args.registry), auth).run("stdio")


if __name__ == "__main__":
    main()
