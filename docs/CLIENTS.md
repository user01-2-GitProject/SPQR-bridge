# Bridge v2 clients

Any agent that can run a stdio MCP server can be a bridge client. Hermes may be first, but nothing
here is Hermes-specific: a **slot** is a named principal in the registry plus one token file, and
whichever agent runs the slot's command holds it.

## Contract

- **Transport:** stdio MCP. The client starts one process and speaks MCP on its stdin/stdout.
- **Command:** `scripts/spqr-v2 SLOT`, nothing else. It runs
  `python -m spqr_bridge.launch --token-file DIR/SLOT.token -- --config C --registry DIR/registry.toml`.
- **Credential:** `DIR/SLOT.token` (mode 0600). The launcher passes it on a pipe, so the token never
  appears in the client's config, argv, environment or MCP traffic. The client never sees it.
- **Tools:** `list_agents`, `start_task`, `get_task`, `cancel_task`. Without `grant_id` a job is
  read-only. jimmy issues wider scope per job as a grant id ("use grant fix-login").
- **Stops are final:** `usage_exhausted`, retry/round caps and `no_progress` go back to jimmy.
- **One holder at a time:** the server locks its `state_dir`, so while one client has the bridge
  open, a second one fails to start. (Phase 2's daemon removes this limit.)
- **Swapping the holder:** point a different agent at the same slot and remove it from the old
  one. To cut someone off, set `enabled = false` on the slot or drop its digest from the registry.

Defaults: `DIR` is `~/.config/spqr` (override with `SPQR_CONFIG_DIR`). `C` is
`config/bridge.local.toml` in this repo (override with `SPQR_BRIDGE_CONFIG`). The registry must
be outside every `allowed_roots` entry, or v2 refuses to start; keep the token there too. Point
clients at the script's real path, not a symlink, so it can find the repo's `.venv`.

## Issue a slot (once, by jimmy)

```sh
cd "/home/jimmy/Projects (jimmy's)/SPQR"
.venv/bin/python -m spqr_bridge.slot new caesar --agents claude --roots "/home/jimmy/Projects (jimmy's)/SPQR/workspaces"
```

This writes `~/.config/spqr/caesar.token` and prints a `[principals.caesar]` block. Paste the block
into `~/.config/spqr/registry.toml` and `chmod 600` it. It never overwrites an existing token: to re-issue, delete
`caesar.token` first and replace the old block.

## One command per client

Below, `SPQR="/home/jimmy/Projects (jimmy's)/SPQR"`, and `caesar` is the slot.

**Hermes** (`~/.hermes/config.yaml`):

```yaml
mcp_servers:
  spqr:
    command: "/home/jimmy/Projects (jimmy's)/SPQR/scripts/spqr-v2"
    args: ["caesar"]
    env:
      SPQR_CONFIG_DIR: "/home/jimmy/.config/spqr"
```

Then `/reload-mcp` or restart Hermes.

**Claude Code:**

```sh
claude mcp add -s user spqr -- "$SPQR/scripts/spqr-v2" caesar
```

**Codex:**

```sh
codex mcp add spqr -- "$SPQR/scripts/spqr-v2" caesar
```

**OMP, or any other MCP client:** add a stdio server named `spqr` with command
`$SPQR/scripts/spqr-v2` and args `["caesar"]`. If the client strips `HOME` from the environment,
also set `SPQR_CONFIG_DIR=/home/jimmy/.config/spqr`.

Check it: ask the agent to call `list_agents`. If it fails, the reason is on stderr (client log)
and, for auth failures, in `<state_dir>/auth-failures.log`.
