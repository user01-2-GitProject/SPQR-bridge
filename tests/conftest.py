import os
import sys
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "fake_agent.py"


def make_wrapper(path: Path, extra: str = "") -> Path:
    """Executable wrapper around the FAKE agent fixture (test-only)."""
    path.write_text(f'#!/bin/sh\nexec "{sys.executable}" -I "{FIXTURE}" {extra} "$@"\n')
    path.chmod(0o700)
    return path


@pytest.fixture
def env_setup(tmp_path):
    """Returns (config_toml_text_factory, workspace, state_dir, bin_dir)."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "README.txt").write_text("fixture workspace\n")
    state = tmp_path / "state"
    bins = tmp_path / "bin"
    bins.mkdir()
    fake_claude = make_wrapper(bins / "claude")
    fake_codex = make_wrapper(bins / "codex")
    nostdin = make_wrapper(bins / "claude-nostdin", "--fixture-nostdin")

    def cfg_text(**server_overrides):
        server = {"max_concurrency": 2, "max_pending": 8, "default_timeout_seconds": 30,
                  "max_timeout_seconds": 60, "max_output_bytes": 1_000_000, "kill_grace_seconds": 1}
        server.update(server_overrides)
        lines = ["[server]", f'state_dir = "{state}"', f'allowed_roots = ["{ws}"]']
        lines += [f"{k} = {v}" for k, v in server.items()]
        lines += ["", "[agents.claude]", 'adapter = "claude"', f'binary = "{fake_claude}"',
                  "enabled = true", "allow_write = true", "max_budget_usd = 0.1",
                  "", "[agents.codex]", 'adapter = "codex"', f'binary = "{fake_codex}"',
                  "enabled = true",
                  "", "[agents.nostdin]", 'adapter = "claude"', f'binary = "{nostdin}"', "enabled = true",
                  "", "[agents.off]", 'adapter = "claude"', f'binary = "{fake_claude}"', "enabled = false",
                  "", "[agents.ghost]", 'adapter = "claude"', f'binary = "{bins}/missing"', "enabled = true",
                  "", "[agents.omp]", 'adapter = "omp"', f'binary = "{bins}/omp-missing"']
        return "\n".join(lines) + "\n"

    return cfg_text, ws, state, tmp_path


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # zombies count as dead
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split(")")[-1].split()[0] != "Z"
    except FileNotFoundError:
        return False
