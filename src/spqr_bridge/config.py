"""Trusted operator registry: TOML loaded once at server startup and validated strictly."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

ADAPTERS = ("claude", "codex", "omp")
_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
_AGENT_ID = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


class ConfigError(ValueError):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ServerConfig(_Strict):
    state_dir: Path
    allowed_roots: list[Path] = Field(min_length=1)
    max_concurrency: int = Field(default=2, ge=1, le=8)
    max_pending: int = Field(default=8, ge=1, le=64)
    default_timeout_seconds: int = Field(default=600, ge=1)
    max_timeout_seconds: int = Field(default=1800, ge=1, le=4 * 3600)
    max_output_bytes: int = Field(default=2_000_000, ge=1024)
    snippet_bytes: int = Field(default=16_000, ge=256, le=200_000)
    retain_jobs: int = Field(default=200, ge=10, le=10_000)
    kill_grace_seconds: float = Field(default=3.0, gt=0, le=30)

    @field_validator("state_dir")
    @classmethod
    def _abs_state(cls, v: Path) -> Path:
        if not v.is_absolute():
            raise ValueError("state_dir must be absolute")
        return v

    @field_validator("allowed_roots")
    @classmethod
    def _abs_roots(cls, v: list[Path]) -> list[Path]:
        out = []
        for r in v:
            if not r.is_absolute():
                raise ValueError(f"allowed root must be absolute: {r}")
            if r.resolve() == Path("/"):
                raise ValueError("allowed root may not be /")
            out.append(r.resolve())
        return out

    @model_validator(mode="after")
    def _state_outside_roots(self) -> "ServerConfig":
        # A project-local state dir inside a root is allowed (workspaces inside it are rejected at
        # launch), but a root may never be the state dir or live inside it.
        state = self.state_dir.resolve()
        for r in self.allowed_roots:
            if r == state or r.is_relative_to(state):
                raise ValueError("an allowed root must not be inside the state_dir")
        if self.default_timeout_seconds > self.max_timeout_seconds:
            raise ValueError("default_timeout_seconds exceeds max_timeout_seconds")
        return self


class AgentConfig(_Strict):
    adapter: Literal["claude", "codex", "omp"]
    binary: Path
    enabled: bool = False
    allow_write: bool = False
    env_passthrough: list[str] = Field(default_factory=list)
    max_turns: int = Field(default=8, ge=1, le=100)
    max_budget_usd: float | None = Field(default=None, gt=0, le=50)
    # Operator-recorded evidence that a live smoke passed; never set by the bridge itself.
    live_verified: bool = False
    description: str = ""

    @field_validator("binary")
    @classmethod
    def _abs_binary(cls, v: Path) -> Path:
        if not v.is_absolute():
            raise ValueError("binary must be an absolute path")
        return v

    @field_validator("env_passthrough")
    @classmethod
    def _env_names(cls, v: list[str]) -> list[str]:
        for name in v:
            if not _ENV_NAME.match(name):
                raise ValueError(f"env_passthrough entries must be variable NAMES only: {name[:16]!r}")
        return v

    @model_validator(mode="after")
    def _omp_no_write(self) -> "AgentConfig":
        if self.adapter == "omp" and self.allow_write:
            raise ValueError("omp has no enforced file restriction; allow_write is not supported")
        return self


class BridgeConfig(_Strict):
    server: ServerConfig
    agents: dict[str, AgentConfig] = Field(default_factory=dict)

    @field_validator("agents")
    @classmethod
    def _ids(cls, v: dict[str, AgentConfig]) -> dict[str, AgentConfig]:
        for k in v:
            if not _AGENT_ID.match(k):
                raise ValueError(f"invalid agent id {k!r}")
        return v


def load_config(path: str | Path) -> BridgeConfig:
    p = Path(path)
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"cannot read config {p}: {e}") from None
    try:
        return BridgeConfig.model_validate(data)
    except ValidationError as e:
        raise ConfigError(str(e)) from None
