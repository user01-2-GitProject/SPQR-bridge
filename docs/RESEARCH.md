# Research summary — SPQR Agent Bridge

This condenses Caesar's researched brief. Numbered citations refer to the source ledger
below (IDs match Caesar's `sources.json`). Statements marked *local observation* come from
inspection of this machine by Caesar and are not web citations.

## Problem
Caesar (Hermes profile `caesar`) needs to hand bounded tasks to independently installed agent
runtimes (Claude Code, Codex, OMP) and have them run their own agent loops, rather than
calling a plain model-completion endpoint.

## Options compared

| Option | Fit | Verdict |
|---|---|---|
| Native Hermes delegation / messaging / Kanban [2][4][5] | Hermes-to-Hermes bots | Keep for Hermes peers; not for foreign CLIs |
| Hermes MCP server mode [2] | Exposes messaging-channel tools, not a generic worker | Not applicable |
| ACP [3][9] | Agent↔editor interaction, approvals, diff streaming | Unnecessary for bounded delegation |
| A2A [4] | Framework/process/machine boundaries | Only if remote heterogeneous peers become real |
| Per-agent MCP servers | Codex `mcp-server` absent in installed v0.163.0 (*local observation*); OMP MCP wrapper broken (`spawn bun ENOENT`, *local observation*) | Stale/missing, heterogeneous |
| **Local stdio MCP facade + adapters** | One policy surface, short MCP calls, normalised failures | **Chosen** |

## Key facts
- MCP transports evolve between spec revisions; use an SDK rather than hand-written
  JSON-RPC, and do not rely on the newer task extension [6].
- The official Python SDK v2 replaces `FastMCP` with `MCPServer` (`from mcp.server import
  MCPServer`) [19]. Standalone `fastmcp` (4.1.1 at retrieval) is a separate package [10][17];
  not needed. TypeScript SDK versions at retrieval: v1 1.32.1 [15], v2 server 2.3.1 [16].
- Codex non-interactive `exec` supports JSONL events, ephemeral runs and least-privilege
  sandboxes [7].
- Claude Code headless print mode emits structured JSON; permissions and discovery
  (hooks, MCP config) must be controlled explicitly [8].
- Hermes programmatic surfaces (ACP, TUI RPC, OpenAI-compatible HTTP) serve different
  consumers [3]; the docs index is [1].

## Local runtime observations (Caesar, direct evidence)
- Claude Code v2.1.304 `/home/jimmy/.local/bin/claude`: live JSON print-mode smoke passed.
- Codex v0.163.0 `/home/jimmy/.local/bin/codex`: logged in, bounded calls did not complete.
- OMP v18.12.3 `/home/jimmy/.local/bin/omp`: native binary, two bounded live calls timed out.
- OpenCode: mise shim with missing node runtime — deferred.

## Sources

[1] https://hermes-agent.nousresearch.com/docs/llms.txt
[2] https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp
[3] https://hermes-agent.nousresearch.com/docs/developer-guide/programmatic-integration
[4] https://hermes-agent.nousresearch.com/docs/user-guide/messaging/a2a
[5] https://hermes-agent.nousresearch.com/docs/user-guide/bot-mode
[6] https://modelcontextprotocol.io/specification/2026-07-28/basic/transports
[7] https://developers.openai.com/codex/noninteractive
[8] https://code.claude.com/docs/en/headless
[9] https://agentclientprotocol.com/overview/introduction
[10] https://gofastmcp.com/getting-started/quickstart
[15] https://registry.npmjs.org/@modelcontextprotocol/sdk/latest
[16] https://registry.npmjs.org/@modelcontextprotocol/server/latest
[17] https://pypi.org/pypi/fastmcp/json
[19] https://py.sdk.modelcontextprotocol.io/v2/migration
