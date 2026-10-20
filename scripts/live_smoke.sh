#!/bin/sh
# Explicit, operator-run LIVE smoke: real Claude Code call through the bridge over MCP stdio.
# Costs real money (capped by max_budget_usd in the registry, recommended <= 0.20).
set -eu
cd "$(dirname "$0")/.."
SPQR_LIVE_SMOKE=1 exec uv run pytest -m live -s -v tests/test_live_claude.py "$@"
