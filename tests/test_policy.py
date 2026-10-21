"""Slice 1: config validation, workspace path policy, environment allowlist."""

import os
from pathlib import Path

import pytest

from spqr_bridge.config import ConfigError, load_config
from spqr_bridge.envpolicy import DEPTH_VAR, build_env
from spqr_bridge.paths import PathPolicyError, resolve_workspace


def write_cfg(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "bridge.toml"
    p.write_text(body)
    return p


BASE = """
[server]
state_dir = "{state}"
allowed_roots = ["{root}"]

[agents.claude]
adapter = "claude"
binary = "/usr/bin/true"
enabled = true
"""


def test_config_loads_and_defaults(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    cfg = load_config(write_cfg(tmp_path, BASE.format(state=tmp_path / "st", root=root)))
    assert cfg.server.max_concurrency == 2
    assert cfg.agents["claude"].allow_write is False
    assert cfg.agents["claude"].enabled is True


@pytest.mark.parametrize(
    "extra",
    [
        'binary_args = ["--dangerously-skip-permissions"]\n',  # unknown key
        'adapter_typo = "x"\n',
    ],
)
def test_config_rejects_unknown_keys(tmp_path, extra):
    root = tmp_path / "ws"
    root.mkdir()
    load_config(write_cfg(tmp_path, BASE.format(state=tmp_path / "st", root=root)))  # valid baseline
    body = BASE.format(state=tmp_path / "st", root=root) + extra
    with pytest.raises(ConfigError):
        load_config(write_cfg(tmp_path, body))


def test_config_rejects_relative_binary_and_bad_adapter(tmp_path):
    body = BASE.format(state=tmp_path / "st", root=tmp_path).replace("/usr/bin/true", "claude")
    with pytest.raises(ConfigError):
        load_config(write_cfg(tmp_path, body))
    body = BASE.format(state=tmp_path / "st", root=tmp_path).replace('adapter = "claude"', 'adapter = "shell"')
    with pytest.raises(ConfigError):
        load_config(write_cfg(tmp_path, body))


def test_config_rejects_secret_like_values_and_bad_env_names(tmp_path):
    body = BASE.format(state=tmp_path / "st", root=tmp_path) + 'env_passthrough = ["ANTHROPIC_API_KEY=sk-ant-123"]\n'
    with pytest.raises(ConfigError):
        load_config(write_cfg(tmp_path, body))


def test_config_rejects_root_inside_or_equal_state_dir(tmp_path):
    (tmp_path / "st" / "sub").mkdir(parents=True)
    for root in (tmp_path / "st", tmp_path / "st" / "sub"):
        with pytest.raises(ConfigError):
            load_config(write_cfg(tmp_path, BASE.format(state=tmp_path / "st", root=root)))


def test_workspace_inside_state_dir_rejected(tmp_path):
    # state may live inside a root (project-local .state), but is never a launch cwd
    state = tmp_path / ".state"
    (state / "tmp").mkdir(parents=True)
    cfg = load_config(write_cfg(tmp_path, BASE.format(state=state, root=tmp_path)))
    assert resolve_workspace(str(tmp_path), cfg.server.allowed_roots, exclude=[state]) == tmp_path.resolve()
    with pytest.raises(PathPolicyError):
        resolve_workspace(str(state / "tmp"), cfg.server.allowed_roots, exclude=[state])


def test_omp_write_is_rejected(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    body = BASE.format(state=tmp_path / "st", root=root).replace('adapter = "claude"', 'adapter = "omp"')
    body += "allow_write = true\n"
    with pytest.raises(ConfigError):
        load_config(write_cfg(tmp_path, body))


# --- workspace path policy -------------------------------------------------


def test_workspace_inside_root_ok(tmp_path):
    root = tmp_path / "root"
    (root / "proj").mkdir(parents=True)
    assert resolve_workspace(str(root / "proj"), [root]) == (root / "proj").resolve()
    assert resolve_workspace(str(root / "proj" / ".." / "proj"), [root]) == (root / "proj").resolve()


def test_workspace_prefix_sibling_rejected(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    sib = tmp_path / "root-evil"
    sib.mkdir()
    with pytest.raises(PathPolicyError):
        resolve_workspace(str(sib), [root])


def test_workspace_symlink_escape_rejected(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside)
    with pytest.raises(PathPolicyError):
        resolve_workspace(str(root / "link"), [root])


@pytest.mark.parametrize("bad", ["", "relative/path", "/nonexistent/zzz"])
def test_workspace_invalid_rejected(tmp_path, bad):
    with pytest.raises(PathPolicyError):
        resolve_workspace(bad, [tmp_path])


def test_workspace_must_be_directory(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    with pytest.raises(PathPolicyError):
        resolve_workspace(str(f), [tmp_path])


# --- environment allowlist -------------------------------------------------


def test_env_excludes_secrets_and_sets_tmpdir(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("HERMES_SECRET", "nope")
    monkeypatch.setenv("CUSTOM_ALLOWED", "yes")
    env = build_env(tmp_path / "tmp", passthrough=["CUSTOM_ALLOWED"])
    assert "ANTHROPIC_API_KEY" not in env
    assert "OPENAI_API_KEY" not in env
    assert "HERMES_SECRET" not in env
    assert env["CUSTOM_ALLOWED"] == "yes"
    assert env["TMPDIR"] == str(tmp_path / "tmp")
    assert env["PATH"] == os.environ["PATH"]
    assert env[DEPTH_VAR] == "1"


def test_env_depth_guard_increments(tmp_path, monkeypatch):
    monkeypatch.setenv(DEPTH_VAR, "1")
    assert build_env(tmp_path, passthrough=[])[DEPTH_VAR] == "2"
