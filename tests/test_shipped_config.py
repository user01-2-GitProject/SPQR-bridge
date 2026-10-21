"""Regression: shipped registries validate and keep conservative defaults."""

from pathlib import Path

import pytest

from spqr_bridge.config import load_config

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


@pytest.mark.parametrize("name", ["bridge.example.toml", "bridge.local.toml"])
def test_shipped_config_conservative(name):
    p = CONFIG_DIR / name
    if not p.exists():
        pytest.skip(f"{name} not present on this machine")
    cfg = load_config(p)
    assert cfg.agents["omp"].enabled is False
    assert cfg.agents["codex"].enabled is False
    assert all(a.allow_write is False for a in cfg.agents.values())
    assert all(a.live_verified is False for a in cfg.agents.values())
    assert cfg.agents["claude"].max_budget_usd is not None
