# Alternative baseline diagnosis — protocol and task tracing

Scope: fresh temporary empty-agent registry and state, no model calls, no source/test or production changes. These attempts replace timeout-only diagnosis with runtime tracing. Three attempts were made; the third failed in diagnostic instrumentation before the server ran. No fourth attempt was made.

## Established stage

The parent flushes a full newline-terminated initialize request and **keeps stdin open**. The SDK reads and parses that frame successfully into `SessionMessage(JSONRPCRequest(method="initialize", ...))`. Instrumented `ContextSendStream.send` logs that exact item before awaiting its underlying memory-stream send.

At the two-second task snapshot:
- stdin_reader is suspended in `mcp/server/stdio.py:195` -> `ContextSendStream.send` -> `anyio/streams/memory.py:248` -> AnyIO checkpoint -> asyncio.sleep(0).
- Main server is in `serve_dual_era_loop` entering `_replay_from_opening_request`; async-generator frame is at `mcp/server/runner.py:695`, awaiting the opening request through `async for item in read_stream`. Its locals show `opening_request=None` and `lead=[]`.
- stdout_writer is waiting for an item from its output memory stream (`stdio.py:202`). There is no response waiting to be flushed.
- The tracing wrapper logs no completed transport send and no received opening item before the snapshot.

**Concrete localization:** first-request delivery from the parsed-input transport into the SDK opening-request memory-stream consumer has not completed. This is before protocol-era selection, initialize handler dispatch or response generation. The evidence rules out import failure, stdin close/EOF as the sole trigger, inability to read/parse this initialize frame, v2 authentication (not implemented), and a generated response stuck in stdout buffering for these traced runs.

**Still unproved:** why this in-memory delivery does not progress. Seeing a task at checkpoint does not prove checkpoint itself is defective. No SDK fix, dependency change, baseline passage, or production-health claim follows from these observations. Installed package metadata reports MCP 2.3.0 and AnyIO 4.15.1; interpreter is the project .venv Python 3.14.

## Attempt 1

Command: `.venv/bin/python /tmp/spqr-alternative-diagnose.py`.
Temporary child monkeypatches `MCPServer.run_stdio_async` to start an asyncio monitor. After two seconds it prints task coroutine-await chains to stderr and exits using `os._exit(0)` (diagnostic exit, **not successful initialization**). Parent writes/flushed request and holds input open. Full stdout is empty.

```text
Attempt 1: stdin kept open; full initialize line flushed
rc=0
stdout=
stderr:
TASK __main__.traced
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/_backends/_asyncio.py 2531 wrapper
 AWAIT <string> 19 traced
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/server/mcpserver/server.py 1077 run_stdio_async
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/server/lowlevel/server.py 723 run
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/server/runner.py 627 serve_dual_era_loop
 AWAIT /usr/lib/python3.14/contextlib.py 214 __aenter__
TASK mcp.server.stdio.stdio_server.<locals>.stdout_writer
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/_core/_tasks.py 327 _run_coro
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/server/stdio.py 202 stdout_writer
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/shared/_context_streams.py 89 __anext__
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/shared/_context_streams.py 71 receive
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/streams/memory.py 126 receive
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/_backends/_asyncio.py 1936 wait
 AWAIT /usr/lib/python3.14/asyncio/locks.py 213 wait
TASK mcp.server.stdio.stdio_server.<locals>.stdin_reader
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/_core/_tasks.py 327 _run_coro
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/server/stdio.py 195 stdin_reader
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/shared/_context_streams.py 37 send
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/streams/memory.py 248 send
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/lowlevel.py 40 checkpoint
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/_backends/_asyncio.py 2564 checkpoint
 AWAIT /usr/lib/python3.14/asyncio/tasks.py 700 sleep
 AWAIT /usr/lib/python3.14/asyncio/tasks.py 694 __sleep0
TASK Task-2
 AWAIT <string> 13 monitor
```

## Attempt 2

Command: `.venv/bin/python /tmp/spqr-alternative-trace.py`.
Same monitor plus wrappers logging entry/completion of `ContextSendStream.send` and `ContextReceiveStream.receive`, and suspended async-generator frames/local opening-request state. Instrumentation is process-local; installed files are unchanged.

```text
Attempt 2: stdin kept open; full initialize line flushed
rc=0
stdout=
stderr:
RECEIVE ENTER 139692748769024
RECEIVE ENTER 139692748769552
SEND ITEM 139692748768784 SessionMessage SessionMessage(message=JSONRPCRequest(jsonrpc='2.0', id=1, method='initialize', params={'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'fixture-diagnosis', 'version': '0'}}), metadata=None)
TASK __main__.traced
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/_backends/_asyncio.py 2531 wrapper
 AWAIT <string> 42 traced
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/server/mcpserver/server.py 1077 run_stdio_async
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/server/lowlevel/server.py 723 run
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/server/runner.py 627 serve_dual_era_loop
 AWAIT /usr/lib/python3.14/contextlib.py 214 __aenter__
TASK mcp.server.stdio.stdio_server.<locals>.stdout_writer
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/_core/_tasks.py 327 _run_coro
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/server/stdio.py 202 stdout_writer
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/shared/_context_streams.py 89 __anext__
 AWAIT <string> 10 rtrace
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/shared/_context_streams.py 71 receive
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/streams/memory.py 126 receive
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/_backends/_asyncio.py 1936 wait
 AWAIT /usr/lib/python3.14/asyncio/locks.py 213 wait
TASK mcp.server.stdio.stdio_server.<locals>.stdin_reader
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/_core/_tasks.py 327 _run_coro
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/server/stdio.py 195 stdin_reader
 AWAIT <string> 17 strace
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/shared/_context_streams.py 37 send
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/streams/memory.py 248 send
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/lowlevel.py 40 checkpoint
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/_backends/_asyncio.py 2564 checkpoint
 AWAIT /usr/lib/python3.14/asyncio/tasks.py 700 sleep
 AWAIT /usr/lib/python3.14/asyncio/tasks.py 694 __sleep0
TASK Task-2
 AWAIT <string> 30 monitor
ASYNCGEN /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/server/stdio.py 212 stdio_server
ASYNCGEN /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/server/mcpserver/server.py 152 wrap
ASYNCGEN /home/jimmy/Projects (jimmy's)/SPQR/src/spqr_bridge/server.py 82 lifespan
ASYNCGEN /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/server/runner.py 695 _replay_from_opening_request
OPENING LOCALS {'read_stream': <mcp.shared._context_streams.ContextReceiveStream object at 0x7f0cc0ab0700>, 'opening_request': None, 'replay_receive': MemoryObjectReceiveStream(_state=_MemoryObjectStreamState(max_buffer_size=0, buffer=deque([]), open_send_channels=1, open_receive_channels=1, waiting_receivers=OrderedDict(), waiting_senders=OrderedDict()), _closed=False), 'replayed': <mcp.shared._context_streams.ContextReceiveStream object at 0x7f0cc0ab1870>, 'replay_then_relay': <function _replay_from_opening_request.<locals>.replay_then_relay at 0x7f0cc0adbed0>, 'lead': [], 'replay_send': MemoryObjectSendStream(_state=_MemoryObjectStreamState(max_buffer_size=0, buffer=deque([]), open_send_channels=1, open_receive_channels=1, waiting_receivers=OrderedDict(), waiting_senders=OrderedDict()), _closed=False)}
ASYNCGEN /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/_core/_fileio.py 112 __aiter__
```

## Attempt 3

Command: `.venv/bin/python /tmp/spqr-alternative-deep.py`.
Attempted to extend generator-await/ready-queue tracing. The generated child script contained an indentation error; no server or protocol execution occurred in this attempt. This is an instrumentation mistake, not evidence about the bridge.

```text
Attempt 3: stdin kept open; full initialize line flushed
rc=1
stdout=
stderr:
  File "<string>", line 45
    if f.f_code.co_name=='_replay_from_opening_request': print('OPENING LOCALS',repr(f.f_locals),file=sys.stderr)
IndentationError: unexpected indent
```

## Reproduction scripts

These scripts contain only fixture protocol/config data and diagnostic instrumentation. Retained below so temporary paths are not the sole record.

### spqr-alternative-diagnose.py

```python
import subprocess, sys, json, tempfile
from pathlib import Path
from mcp.client.stdio import get_default_environment
code = r'''
import asyncio, os, sys
from mcp.server import MCPServer
orig = MCPServer.run_stdio_async
async def monitor():
    await asyncio.sleep(2)
    for t in asyncio.all_tasks():
        print('TASK', t.get_name(), file=sys.stderr)
        c=t.get_coro()
        for _ in range(25):
            if c is None: break
            frame=getattr(c,'cr_frame',None) or getattr(c,'gi_frame',None)
            if frame: print(' AWAIT',frame.f_code.co_filename,frame.f_lineno,frame.f_code.co_name,file=sys.stderr)
            c=getattr(c,'cr_await',None) or getattr(c,'gi_yieldfrom',None)
    sys.stderr.flush()
    os._exit(0)
async def traced(self):
    asyncio.create_task(monitor())
    return await orig(self)
MCPServer.run_stdio_async=traced
from spqr_bridge.server import main
main()
'''
with tempfile.TemporaryDirectory(prefix='spqr-alternative-') as d:
    root=Path(d);(root/'workspace').mkdir();cfg=root/'config.toml'
    cfg.write_text('[server]\nstate_dir = '+json.dumps(str(root/'state'))+'\nallowed_roots = ['+json.dumps(str(root/'workspace'))+']\n')
    wire=json.dumps({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'fixture-diagnosis','version':'0'}}})+'\n'
    p=subprocess.Popen([sys.executable,'-c',code,'--config',str(cfg)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=get_default_environment())
    p.stdin.write(wire.encode());p.stdin.flush()
    p.wait(timeout=6)
    out=p.stdout.read().decode();err=p.stderr.read().decode();p.stdin.close()
    text='Attempt 1: stdin kept open; full initialize line flushed\nrc='+str(p.returncode)+'\nstdout='+out+'\nstderr:\n'+err
    print(text);Path('/tmp/spqr-alternative-attempt1.txt').write_text(text)
```

### spqr-alternative-trace.py

```python
import subprocess, sys, json, tempfile
from pathlib import Path
from mcp.client.stdio import get_default_environment
code = r'''
import asyncio, os, sys
from mcp.server import MCPServer
from mcp.shared._context_streams import ContextReceiveStream, ContextSendStream
rcv=ContextReceiveStream.receive
snd=ContextSendStream.send
counts={'recv':0,'send':0}
async def rtrace(self):
    if counts['recv']<12: print('RECEIVE ENTER',id(self),file=sys.stderr)
    item=await rcv(self)
    counts['recv']+=1
    if counts['recv']<12: print('RECEIVE ITEM',id(self),type(item).__name__,repr(item),file=sys.stderr)
    return item
async def strace(self,item):
    counts['send']+=1
    if counts['send']<12: print('SEND ITEM',id(self),type(item).__name__,repr(item),file=sys.stderr)
    await snd(self,item)
    if counts['send']<12: print('SEND DONE',id(self),file=sys.stderr)
ContextReceiveStream.receive=rtrace
ContextSendStream.send=strace
orig = MCPServer.run_stdio_async
async def monitor():
    await asyncio.sleep(2)
    for t in asyncio.all_tasks():
        print('TASK', t.get_name(), file=sys.stderr)
        c=t.get_coro()
        for _ in range(25):
            if c is None: break
            frame=getattr(c,'cr_frame',None) or getattr(c,'gi_frame',None)
            if frame: print(' AWAIT',frame.f_code.co_filename,frame.f_lineno,frame.f_code.co_name,file=sys.stderr)
            c=getattr(c,'cr_await',None) or getattr(c,'gi_yieldfrom',None)
    import gc, types
    for g in gc.get_objects():
        if isinstance(g,types.AsyncGeneratorType) and g.ag_frame:
            f=g.ag_frame
            print('ASYNCGEN',f.f_code.co_filename,f.f_lineno,f.f_code.co_name,file=sys.stderr)
            if f.f_code.co_name=='_replay_from_opening_request': print('OPENING LOCALS',repr(f.f_locals),file=sys.stderr)
    sys.stderr.flush()
    os._exit(0)
async def traced(self):
    asyncio.create_task(monitor())
    return await orig(self)
MCPServer.run_stdio_async=traced
from spqr_bridge.server import main
main()
'''
with tempfile.TemporaryDirectory(prefix='spqr-alternative-') as d:
    root=Path(d);(root/'workspace').mkdir();cfg=root/'config.toml'
    cfg.write_text('[server]\nstate_dir = '+json.dumps(str(root/'state'))+'\nallowed_roots = ['+json.dumps(str(root/'workspace'))+']\n')
    wire=json.dumps({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'fixture-diagnosis','version':'0'}}})+'\n'
    p=subprocess.Popen([sys.executable,'-c',code,'--config',str(cfg)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=get_default_environment())
    p.stdin.write(wire.encode());p.stdin.flush()
    p.wait(timeout=6)
    out=p.stdout.read().decode();err=p.stderr.read().decode();p.stdin.close()
    text='Attempt 2: stdin kept open; full initialize line flushed\nrc='+str(p.returncode)+'\nstdout='+out+'\nstderr:\n'+err
    print(text);Path('/tmp/spqr-alternative-attempt2.txt').write_text(text)
```

### spqr-alternative-deep.py

```python
import subprocess, sys, json, tempfile
from pathlib import Path
from mcp.client.stdio import get_default_environment
code = r'''
import asyncio, os, sys
from mcp.server import MCPServer
from mcp.shared._context_streams import ContextReceiveStream, ContextSendStream
rcv=ContextReceiveStream.receive
snd=ContextSendStream.send
counts={'recv':0,'send':0}
async def rtrace(self):
    if counts['recv']<12: print('RECEIVE ENTER',id(self),file=sys.stderr)
    item=await rcv(self)
    counts['recv']+=1
    if counts['recv']<12: print('RECEIVE ITEM',id(self),type(item).__name__,repr(item),file=sys.stderr)
    return item
async def strace(self,item):
    counts['send']+=1
    if counts['send']<12: print('SEND ITEM',id(self),type(item).__name__,repr(item),file=sys.stderr)
    await snd(self,item)
    if counts['send']<12: print('SEND DONE',id(self),file=sys.stderr)
ContextReceiveStream.receive=rtrace
ContextSendStream.send=strace
orig = MCPServer.run_stdio_async
async def monitor():
    await asyncio.sleep(2)
    for t in asyncio.all_tasks():
        print('TASK',repr(t),'CANCELS',t.cancelling(),file=sys.stderr)
        c=t.get_coro()
        for _ in range(25):
            if c is None: break
            frame=getattr(c,'cr_frame',None) or getattr(c,'gi_frame',None)
            if frame: print(' AWAIT',frame.f_code.co_filename,frame.f_lineno,frame.f_code.co_name,file=sys.stderr)
            c=getattr(c,'cr_await',None) or getattr(c,'gi_yieldfrom',None)
    import gc, types
    for g in gc.get_objects():
        if isinstance(g,types.AsyncGeneratorType) and g.ag_frame:
            f=g.ag_frame
            print('ASYNCGEN',f.f_code.co_filename,f.f_lineno,f.f_code.co_name,file=sys.stderr)
            c=g.ag_await
            for _ in range(25):
                if c is None: break
                ff=getattr(c,'cr_frame',None) or getattr(c,'gi_frame',None)
                if ff: print(' GENAWAIT',ff.f_code.co_filename,ff.f_lineno,ff.f_code.co_name,file=sys.stderr)
                c=getattr(c,'cr_await',None) or getattr(c,'gi_yieldfrom',None)
    loop=asyncio.get_running_loop()
    print('LOOP READY',repr(loop._ready),file=sys.stderr)
            if f.f_code.co_name=='_replay_from_opening_request': print('OPENING LOCALS',repr(f.f_locals),file=sys.stderr)
    sys.stderr.flush()
    os._exit(0)
async def traced(self):
    asyncio.create_task(monitor())
    return await orig(self)
MCPServer.run_stdio_async=traced
from spqr_bridge.server import main
main()
'''
with tempfile.TemporaryDirectory(prefix='spqr-alternative-') as d:
    root=Path(d);(root/'workspace').mkdir();cfg=root/'config.toml'
    cfg.write_text('[server]\nstate_dir = '+json.dumps(str(root/'state'))+'\nallowed_roots = ['+json.dumps(str(root/'workspace'))+']\n')
    wire=json.dumps({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'fixture-diagnosis','version':'0'}}})+'\n'
    p=subprocess.Popen([sys.executable,'-c',code,'--config',str(cfg)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=get_default_environment())
    p.stdin.write(wire.encode());p.stdin.flush()
    p.wait(timeout=6)
    out=p.stdout.read().decode();err=p.stderr.read().decode();p.stdin.close()
    text='Attempt 3: stdin kept open; full initialize line flushed\nrc='+str(p.returncode)+'\nstdout='+out+'\nstderr:\n'+err
    print(text);Path('/tmp/spqr-alternative-attempt3.txt').write_text(text)
```
