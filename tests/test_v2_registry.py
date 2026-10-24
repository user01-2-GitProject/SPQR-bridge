"""v2 Phase 1: operator registry (principals, grants, usage clears) and credential checks."""

import os
import threading
import time

import pytest

from spqr_bridge.auth import CredentialError, _wipe, read_credential_fd
from spqr_bridge.registry import RegistryError, authenticate, load_registry
from v2_helpers import TOKENS, digest, registry_text, soon, write_registry


@pytest.fixture
def reg(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    path = tmp_path / "registry.toml"

    def write(**kw):
        write_registry(path, registry_text(ws, **kw))
        return load_registry(path)
    return write, path, ws


def test_valid_registry_loads(reg):
    write, _, ws = reg
    r = write(grants={"g1": {"principal": "caesar", "access": "write", "agents": ["claude"],
                             "roots": [str(ws)], "max_rounds": 7},
                      "g2": {"principal": "iggy", "access": "read_only"}},
              cleared={"claude": soon(-1)})
    assert set(r.principals) == {"caesar", "iggy"}
    assert r.grants["g1"].max_rounds == 7 and r.grants["g2"].max_rounds == 5
    assert "claude" in r.usage_cleared
    assert len(r.revision) == 64


def test_authenticate_by_token(reg):
    write, _, _ = reg
    r = write()
    ctx = authenticate(r, TOKENS["iggy"].encode(), "stdio")
    assert ctx is not None and ctx.principal_id == "iggy" and ctx.transport == "stdio"
    assert ctx.registry_revision == r.revision


@pytest.mark.parametrize("token", [b"", b"nope", TOKENS["caesar"].encode() + b"x"])
def test_wrong_token_denied(reg, token):
    write, _, _ = reg
    assert authenticate(write(), token, "stdio") is None


def test_disabled_expired_and_wrong_transport_denied(reg):
    write, _, _ = reg
    tok = TOKENS["caesar"].encode()
    assert authenticate(write(principals={"caesar": {"enabled": False}}), tok, "stdio") is None
    assert authenticate(write(principals={"caesar": {"expires_at": "2020-01-01T00:00:00Z"}}), tok, "stdio") is None
    assert authenticate(write(), tok, "http") is None
    assert authenticate(write(principals={"caesar": {"transports": ["http"]}}), tok, "stdio") is None


def test_rotation_allows_overlapping_credentials(reg):
    write, _, _ = reg
    r = write(principals={"caesar": {"token_sha256": [digest(TOKENS["caesar"]), digest("new-" + "c" * 43)]}})
    assert authenticate(r, b"new-" + b"c" * 43, "stdio").principal_id == "caesar"
    assert authenticate(r, TOKENS["caesar"].encode(), "stdio").principal_id == "caesar"


@pytest.mark.parametrize("over", [
    {"principals": {"caesar": {"token_sha256": ["not-hex"]}}},
    {"principals": {"caesar": {"surprise": 1}}},
    {"principals": {"caesar": {"roots": ["relative/path"]}}},
    {"principals": {"Bad Name": {"token_sha256": ["0" * 64]}}},
    {"grants": {"g1": {"principal": "ghost", "access": "read_only"}}},
    {"grants": {"g1": {"principal": "caesar", "access": "read_only", "max_rounds": 11}}},
    {"grants": {"g1": {"principal": "caesar", "access": "read_only", "max_rounds": 4}}},
    {"grants": {"g1": {"principal": "caesar", "access": "admin"}}},
    {"grants": {"g1": {"principal": "caesar", "access": "read_only", "expires_at": "2027-01-01T00:00:00"}}},
    {"grants": {"g1": {"principal": "caesar", "access": "read_only", "expires_at": soon(25)}}},
    {"grants": {"g1": {"principal": "caesar", "access": "read_only", "issued_at": soon(1), "expires_at": soon(2)}}},
    {"cleared": {"claude": soon(1)}},
    {"cleared": {"claude": soon(10 / 60)}},
    {"grants": {"g1": {"principal": "caesar", "access": "read_only", "issued_at": soon(10 / 60)}}},
    {"cleared": {"nobody here": soon(-1)}},
])
def test_invalid_registry_rejected(reg, over):
    write, _, _ = reg
    with pytest.raises(RegistryError):
        write(**over)


def test_duplicate_token_across_principals_rejected(reg):
    write, _, _ = reg
    with pytest.raises(RegistryError):
        write(principals={"iggy": {"token_sha256": [digest(TOKENS["caesar"])]}})


def test_grant_requires_expiry(reg, tmp_path):
    _, path, ws = reg
    for times in ("", f'issued_at = "{soon(-1)}"\n', f'expires_at = "{soon(1)}"\n'):
        write_registry(path, registry_text(ws) + '[grants.g1]\nprincipal = "caesar"\naccess = "read_only"\n' + times)
        with pytest.raises(RegistryError):
            load_registry(path)


def test_unreadable_or_missing_registry_fails_closed(reg, tmp_path):
    with pytest.raises(RegistryError):
        load_registry(tmp_path / "missing.toml")
    _, path, _ = reg
    write_registry(path, "this is [not toml")
    with pytest.raises(RegistryError):
        load_registry(path)


def test_registry_not_owner_only_rejected(reg):
    write, path, _ = reg
    write()
    os.chmod(path, 0o644)
    with pytest.raises(RegistryError):
        load_registry(path)


def test_revision_changes_with_policy(reg):
    write, _, ws = reg
    a = write().revision
    b = write(grants={"g1": {"principal": "caesar", "access": "read_only", "expires_at": soon()}}).revision
    assert a != b


# ---- credential pipe ---------------------------------------------------------

def test_read_credential_from_pipe():
    r, w = os.pipe()
    os.write(w, TOKENS["caesar"].encode() + b"\n")
    os.close(w)
    assert read_credential_fd(r) == TOKENS["caesar"].encode()
    with pytest.raises(OSError):
        os.fstat(r)  # closed after receipt


def test_oversized_credential_rejected():
    r, w = os.pipe()
    os.write(w, b"x" * 5000)
    os.close(w)
    with pytest.raises(CredentialError):
        read_credential_fd(r)


def test_regular_file_fd_rejected(tmp_path):
    p = tmp_path / "tok"
    p.write_text(TOKENS["caesar"])
    fd = os.open(p, os.O_RDONLY)
    with pytest.raises(CredentialError):
        read_credential_fd(fd)


@pytest.mark.parametrize("fd", [0, 1, 2, -1, 999])
def test_bad_fd_numbers_rejected(fd):
    with pytest.raises(CredentialError):
        read_credential_fd(fd)


def test_credential_deadline():
    r, w = os.pipe()  # writer never closes
    t0 = time.monotonic()
    with pytest.raises(CredentialError):
        read_credential_fd(r, deadline=0.3)
    assert time.monotonic() - t0 < 2
    os.close(w)


def test_wipe_zeroes_buffer_in_place():
    buf = bytearray(b"super-secret-token")
    n = len(buf)
    _wipe(buf)
    assert len(buf) == n            # length preserved
    assert bytes(buf) == bytes(n)   # every byte is 0x00


def test_wipe_handles_empty_buffer():
    buf = bytearray()
    _wipe(buf)  # must not raise on a zero-length buffer
    assert bytes(buf) == b""


def test_credential_read_returns_independent_copy():
    # The returned token must survive in-place wiping of the internal read
    # buffer: it has to be a genuine immutable copy, not an alias into the
    # bytearray that read_credential_fd zeroes on exit.
    r, w = os.pipe()
    os.write(w, TOKENS["caesar"].encode() + b"\n")
    os.close(w)
    token = read_credential_fd(r)
    assert token == TOKENS["caesar"].encode()
    assert isinstance(token, bytes)
