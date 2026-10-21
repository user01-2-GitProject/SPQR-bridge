# Baseline initialization diagnosis — 2026-10-20

Scope: fixture-only isolated state. No source/test edits, production configuration/state/service changes, or live model calls. Stopped after three diagnostic attempts; the underlying cause is **unresolved**.

## Attempt 1: isolated existing test

Command: `.venv/bin/python -m pytest -q -p no:cacheprovider --tb=short tests/test_mcp_stdio.py::test_mcp_stdio_all_tools > /tmp/spqr-baseline-stdio-attempt1.txt 2>&1`

Observed: `1 failed in 42.45s`; MCP `initialize` request timed out. Test fixture writes its own config/state under pytest temporary paths and uses FAKE executables; no provider call occurs before initialization.

## Attempt 2: direct wire, no SDK client dispatcher

A temporary empty-agent config was passed to `[sys.executable, "-m", "spqr_bridge.server", "--config", temporary_config]` with `env=get_default_environment()` (the MCP SDK subprocess environment). `subprocess.Popen` piped a newline-terminated JSON-RPC initialize request (`protocolVersion=2025-06-18`, empty capabilities, fixture clientInfo), then closed input via `communicate(..., timeout=8)`. The child was killed/reaped after its timeout.

Observed: `direct wire timed out; stdout:  stderr:` (both empty). This reproduces lack of response without the SDK client request dispatcher. It does not by itself establish why the server hangs; closing stdin also changes lifecycle conditions.

## Attempt 3: same direct wire with stack diagnostic

Same isolated configuration/request/environment, child entrypoint instrumented without editing source:
`import faulthandler,runpy; faulthandler.dump_traceback_later(4, exit=True); runpy.run_module("spqr_bridge.server",run_name="__main__")`

Observed: instrumented child exit code 1 after four-second diagnostic. Server reached `server.py:167` -> MCP `server.py:421` -> AnyIO asyncio event loop. An AnyIO worker thread was idle in queue.get. No stdout or ordinary error text. Stack output below. This proves source import and event-loop entry, **not** which asyncio task or transport stage stopped making progress.

## Import and environment checks

Command: `.venv/bin/python -c 'import spqr_bridge, mcp, sys; print(sys.executable); print(spqr_bridge.__file__); print(mcp.__file__)'`

Observed:
- `/home/jimmy/Projects (jimmy's)/SPQR/.venv/bin/python`
- `/home/jimmy/Projects (jimmy's)/SPQR/src/spqr_bridge/__init__.py`
- `/home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/__init__.py`

Source inspection: `tests/test_mcp_stdio.py:29` spawns `sys.executable -m spqr_bridge.server`; SDK `mcp/client/stdio.py:126–131` merges only its inherited environment allowlist and explicit params. No SPQR/PYTHON/VIRTUAL_ENV-prefixed variables were observed in the diagnostic shell. These checks do not prove equivalent environment to the original historical passing run.

Separate readiness failure remains structurally explained: `tests/test_shipped_config.py:12–21` explicitly asserts false live_verified for both example and operator-local registries. This conflicts with the commission's legitimate live_verified=true local Claude status. No readiness flags changed.

## Conclusion

Initialization timeout reproduced with both the existing fixture client and a direct-wire diagnostic. Import failure was not observed, and the instrumented server reached its event loop. A transport/lifecycle issue is a hypothesis, not a proved cause. Further investigation requires a new bounded diagnostic step authorized after reporting the three-attempt stop. Baseline remains unresolved and no green result is claimed.

## Attempt 1 complete failure output

```text
F                                                                        [100%]
=================================== FAILURES ===================================
___________________________ test_mcp_stdio_all_tools ___________________________
  + Exception Group Traceback (most recent call last):
  |   File "/home/jimmy/Projects (jimmy's)/SPQR/tests/test_mcp_stdio.py", line 30, in test_mcp_stdio_all_tools
  |     async with Client(params, read_timeout_seconds=30) as c:
  |                ~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  |   File "/home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/client/client.py", line 458, in __aenter__
  |     async with AsyncExitStack() as exit_stack:
  |                ~~~~~~~~~~~~~~^^
  |   File "/usr/lib/python3.14/contextlib.py", line 768, in __aexit__
  |     raise exc
  |   File "/usr/lib/python3.14/contextlib.py", line 751, in __aexit__
  |     cb_suppress = await cb(*exc_details)
  |                   ^^^^^^^^^^^^^^^^^^^^^^
  |   File "/usr/lib/python3.14/contextlib.py", line 235, in __aexit__
  |     await self.gen.athrow(value)
  |   File "/home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/client/stdio.py", line 201, in stdio_client
  |     async with anyio.create_task_group() as tg:
  |                ~~~~~~~~~~~~~~~~~~~~~~~^^
  |   File "/home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/_backends/_asyncio.py", line 847, in __aexit__
  |     raise BaseExceptionGroup(
  |         "unhandled errors in a TaskGroup", self._exceptions
  |     ) from None
  | ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)
  +-+---------------- 1 ----------------
    | Exception Group Traceback (most recent call last):
    |   File "/home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/client/stdio.py", line 205, in stdio_client
    |     yield read_stream, write_stream
    |   File "/usr/lib/python3.14/contextlib.py", line 751, in __aexit__
    |     cb_suppress = await cb(*exc_details)
    |                   ^^^^^^^^^^^^^^^^^^^^^^
    |   File "/home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/client/session.py", line 514, in __aexit__
    |     result = await self._task_group.__aexit__(exc_type, exc_val, exc_tb)
    |              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    |   File "/home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/_backends/_asyncio.py", line 847, in __aexit__
    |     raise BaseExceptionGroup(
    |         "unhandled errors in a TaskGroup", self._exceptions
    |     ) from None
    | ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)
    +-+---------------- 1 ----------------
      | Traceback (most recent call last):
      |   File "/home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/client/client.py", line 465, in __aenter__
      |     await negotiate_auto(session)
      |   File "/home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/client/_probe.py", line 81, in negotiate_auto
      |     await session.initialize()  # every other rpc-error → legacy (the denylist)
      |     ^^^^^^^^^^^^^^^^^^^^^^^^^^
      |   File "/home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/client/session.py", line 654, in initialize
      |     result = await self.send_request(
      |              ^^^^^^^^^^^^^^^^^^^^^^^^
      |     ...<9 lines>...
      |     )
      |     ^
      |   File "/home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/client/session.py", line 586, in send_request
      |     raw = await self._dispatcher.send_raw_request(method, data.get("params"), opts)
      |           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
      |   File "/home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/shared/jsonrpc_dispatcher.py", line 442, in send_raw_request
      |     raise MCPError(code=REQUEST_TIMEOUT, message=f"Request {method!r} timed out") from None
      | mcp.shared.exceptions.MCPError: Request 'initialize' timed out
      +------------------------------------
=========================== short test summary info ============================
FAILED tests/test_mcp_stdio.py::test_mcp_stdio_all_tools - MCPError(-32001, "...
1 failed in 42.45s
```

## Attempt 3 complete stack output

```text
instrumented rc: 1
stdout: 
stderr:
Timeout (0:00:04)!
Thread 0x00007f7a14aa96c0 [AnyIO worker th] (most recent call first):
  File "/usr/lib/python3.14/threading.py", line 369 in wait
  File "/usr/lib/python3.14/queue.py", line 199 in get
  File "/home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/_backends/_asyncio.py", line 1089 in run
  File "/usr/lib/python3.14/threading.py", line 1082 in _bootstrap_inner
  File "/usr/lib/python3.14/threading.py", line 1044 in _bootstrap

Thread 0x00007f7a1debabc0 [python] (most recent call first):
  File "/usr/lib/python3.14/selectors.py", line 452 in select
  File "/usr/lib/python3.14/asyncio/base_events.py", line 2024 in _run_once
  File "/usr/lib/python3.14/asyncio/base_events.py", line 678 in run_forever
  File "/usr/lib/python3.14/asyncio/base_events.py", line 707 in run_until_complete
  File "/usr/lib/python3.14/asyncio/runners.py", line 128 in run
  File "/home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/_backends/_asyncio.py", line 2548 in run
  File "/home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/_core/_eventloop.py", line 83 in run
  File "/home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/server/mcpserver/server.py", line 421 in run
  File "/home/jimmy/Projects (jimmy's)/SPQR/src/spqr_bridge/server.py", line 167 in main
  File "/home/jimmy/Projects (jimmy's)/SPQR/src/spqr_bridge/server.py", line 171 in <module>
  File "<frozen runpy>", line 88 in _run_code
  File "<frozen runpy>", line 234 in run_module
  File "<string>", line 1 in <module>
```
