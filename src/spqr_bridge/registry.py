"""v2 operator registry: principals (identity + ceilings), per-job grants, usage-stop clears.

Owner-only TOML, re-read for every authorization decision. Any read or validation error fails
closed. The registry stores sha256 digests of high-entropy tokens, never the tokens.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import tomllib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import (AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, field_validator,
                      model_validator)

MAX_REGISTRY_BYTES = 256 * 1024
MAX_GRANT_LIFETIME = timedelta(hours=24)
TRANSPORTS = ("stdio", "http")
_ID = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class RegistryError(ValueError):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _abs_paths(v: list[Path]) -> list[Path]:
    for p in v:
        if not p.is_absolute():
            raise ValueError(f"path must be absolute: {p}")
    return [p.resolve() for p in v]


class Principal(_Strict):
    enabled: bool = False
    token_sha256: list[str] = Field(min_length=1)
    transports: list[Literal["stdio", "http"]] = Field(min_length=1)
    expires_at: AwareDatetime | None = None
    # outer ceilings only; the scope of any one job comes from a grant (or the minimum)
    agents: list[str] = Field(default_factory=list)
    roots: list[Path] = Field(default_factory=list)
    allow_write: bool = False

    @field_validator("token_sha256")
    @classmethod
    def _hex(cls, v: list[str]) -> list[str]:
        if not all(_HEX64.match(d) for d in v):
            raise ValueError("token_sha256 entries must be 64 lowercase hex chars")
        return v

    @field_validator("roots")
    @classmethod
    def _roots(cls, v: list[Path]) -> list[Path]:
        return _abs_paths(v)


class Grant(_Strict):
    principal: str
    access: Literal["read_only", "write"]
    agents: list[str] | None = None   # None = the principal's ceiling
    roots: list[Path] | None = None
    max_rounds: int = Field(default=5, ge=5, le=10)
    issued_at: AwareDatetime
    expires_at: AwareDatetime

    @field_validator("roots")
    @classmethod
    def _roots(cls, v):
        return None if v is None else _abs_paths(v)

    @model_validator(mode="after")
    def _lifetime(self) -> "Grant":
        if self.issued_at > datetime.now(timezone.utc):
            raise ValueError("grant issued_at is in the future")
        if not (self.issued_at < self.expires_at <= self.issued_at + MAX_GRANT_LIFETIME):
            raise ValueError("grant must expire after issue and within 24 hours of it")
        return self


class Registry(_Strict):
    principals: dict[str, Principal] = Field(default_factory=dict)
    grants: dict[str, Grant] = Field(default_factory=dict)
    usage_cleared: dict[str, AwareDatetime] = Field(default_factory=dict)
    revision: str = ""

    @model_validator(mode="after")
    def _consistent(self) -> "Registry":
        for k in [*self.principals, *self.grants, *self.usage_cleared]:
            if not _ID.match(k):
                raise ValueError(f"invalid id {k[:40]!r}")
        seen: set[str] = set()
        for p in self.principals.values():
            for d in p.token_sha256:
                if d in seen:
                    raise ValueError("a credential digest is listed more than once")
                seen.add(d)
        for g in self.grants.values():
            if g.principal not in self.principals:
                raise ValueError("grant names an unknown principal")
        now = datetime.now(timezone.utc)
        if any(t > now for t in self.usage_cleared.values()):
            raise ValueError("usage_cleared time is in the future")
        return self


@dataclass(frozen=True)
class AuthContext:
    """Produced only by credential verification. Tool arguments never carry authority."""
    principal_id: str
    transport: str
    credential_digest: str  # not secret; used to re-check the credential is still listed. Never logged.
    registry_revision: str


def token_digest(token: bytes) -> str:
    return hashlib.sha256(token).hexdigest()


def _read_owner_only(p: Path) -> bytes:
    try:
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as e:
        raise RegistryError(f"cannot open registry ({e.strerror})") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise RegistryError("registry must be a regular file owned by this user with mode 0600")
        data = os.read(fd, MAX_REGISTRY_BYTES + 1)
        if len(data) > MAX_REGISTRY_BYTES:
            raise RegistryError("registry too large")
        return data
    finally:
        os.close(fd)


def load_registry(path: str | Path) -> Registry:
    raw = _read_owner_only(Path(path))
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise RegistryError(f"registry is not valid TOML: {type(e).__name__}") from None
    if "revision" in data:
        raise RegistryError("revision is computed, not configured")
    try:
        reg = Registry.model_validate(data)
    except ValidationError as e:
        raise RegistryError(f"invalid registry ({e.error_count()} errors)") from None
    rev = hashlib.sha256(json.dumps(reg.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
    return reg.model_copy(update={"revision": rev})


def principal_valid(reg: Registry, pid: str, digest: str, transport: str) -> Principal | None:
    p = reg.principals.get(pid)
    if p is None or not p.enabled or transport not in p.transports:
        return None
    if p.expires_at is not None and p.expires_at <= datetime.now(timezone.utc):
        return None
    if not any(hmac.compare_digest(digest, d) for d in p.token_sha256):
        return None
    return p


def authenticate(reg: Registry, token: bytes, transport: str) -> AuthContext | None:
    if not token or transport not in TRANSPORTS:
        return None
    digest = token_digest(token)
    found = None
    for pid, p in reg.principals.items():  # check every entry; no early exit on match
        if any(hmac.compare_digest(digest, d) for d in p.token_sha256):
            found = pid
    if found is None or principal_valid(reg, found, digest, transport) is None:
        return None
    return AuthContext(found, transport, digest, reg.revision)
