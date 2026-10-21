# Baseline round 4 — reliability diagnostic attempts

Jimmy authorized up to 20 total new attempts; this reviewer used 2 of its allocated 8. No source/test/dependency changes, production registry/state/service changes, or provider calls were made.

## Result

The suspected memory-stream defect was **not reproduced** by direct raw AnyIO or SDK ContextStreams rendezvous: both complete immediately. A delayed cross-thread event-loop notification is observable in this sandbox: a worker sleeping 0.1 seconds returns to the awaiting coroutine only after a separate 1.5-second timeout timer wakes the loop. With a diagnostic 25ms heartbeat task, the same worker returns at 0.101 seconds. This heartbeat is a temporary discriminating instrument, not a proposed production workaround.

The parent independently reports the unchanged existing stdio integration test passing outside the sandbox (1 passed in 11.24s). That result is parent-supplied, not a command executed by this reviewer. Together with the sandbox reproduction this identifies environment-dependent initialization failure. The exact kernel/SDK/Python wakeup mechanism is not proved here; do not call it a production bridge defect or assert an identified syscall failure.

## Attempt 1 — corrected deep stack and stream identity trace

Command: `.venv/bin/python /tmp/spqr-round4-deep.py`.
Fixed the previous temporary instrumentation indentation mistake. Logged coroutine and async-generator await chains, cancellations and raw memory-stream state identity. Parsed-input sender and opening consumer have the same state identity, ruling out disconnected stream objects in this run. Consumer is waiting on an Event. Sender is at asyncio.sleep(0) and a task-step handle is already queued in event-loop _ready. Forced diagnostic exit occurs at the monitor timer; its exit code zero does not mean handshake success.

This snapshot could catch newly unblocked work before it receives another scheduling turn; it does not prove that the sender checkpoint is permanently stuck. That limitation motivated attempt 2.

```text
Round4 attempt1: stdin kept open; full initialize line flushed
rc=0
stdout=
stderr:
RECEIVE ENTER 140422795465328 STATE 140422795593440
RECEIVE ENTER 140422795465616 STATE 140422798341648
SEND ITEM 140422795465184 STATE 140422795593440 SessionMessage SessionMessage(message=JSONRPCRequest(jsonrpc='2.0', id=1, method='initialize', params={'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'fixture-diagnosis', 'version': '0'}}), metadata=None)
TASK <Task pending name='__main__.traced' coro=<traced() running at /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/_backends/_asyncio.py:2531> wait_for=<Future pending cb=[Task.task_wakeup()]> cb=[_run_until_complete_cb() at /usr/lib/python3.14/asyncio/base_events.py:181, <1 more>, WorkerThread.stop()]> CANCELS 0
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/_backends/_asyncio.py 2531 wrapper
 AWAIT <string> 49 traced
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/server/mcpserver/server.py 1077 run_stdio_async
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/server/lowlevel/server.py 723 run
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/server/runner.py 627 serve_dual_era_loop
 AWAIT /usr/lib/python3.14/contextlib.py 214 __aenter__
TASK <Task pending name='mcp.server.stdio.stdio_server.<locals>.stdout_writer' coro=<TaskHandle._run_coro() running at /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/_core/_tasks.py:327> wait_for=<Future pending cb=[Task.task_wakeup()]> cb=[TaskGroup._spawn.<locals>.task_done() at /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/_backends/_asyncio.py:870]> CANCELS 0
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/_core/_tasks.py 327 _run_coro
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/server/stdio.py 202 stdout_writer
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/shared/_context_streams.py 89 __anext__
 AWAIT <string> 10 rtrace
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/shared/_context_streams.py 71 receive
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/streams/memory.py 126 receive
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/_backends/_asyncio.py 1936 wait
 AWAIT /usr/lib/python3.14/asyncio/locks.py 213 wait
TASK <Task pending name='mcp.server.stdio.stdio_server.<locals>.stdin_reader' coro=<TaskHandle._run_coro() running at /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/_core/_tasks.py:327> cb=[TaskGroup._spawn.<locals>.task_done() at /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/_backends/_asyncio.py:870]> CANCELS 0
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/_core/_tasks.py 327 _run_coro
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/server/stdio.py 195 stdin_reader
 AWAIT <string> 17 strace
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/shared/_context_streams.py 37 send
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/streams/memory.py 248 send
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/lowlevel.py 40 checkpoint
 AWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/_backends/_asyncio.py 2564 checkpoint
 AWAIT /usr/lib/python3.14/asyncio/tasks.py 700 sleep
 AWAIT /usr/lib/python3.14/asyncio/tasks.py 694 __sleep0
TASK <Task pending name='Task-2' coro=<monitor() running at <string>:25>> CANCELS 0
 AWAIT <string> 30 monitor
ASYNCGEN /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/server/stdio.py 212 stdio_server
ASYNCGEN /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/server/mcpserver/server.py 152 wrap
ASYNCGEN /home/jimmy/Projects (jimmy's)/SPQR/src/spqr_bridge/server.py 82 lifespan
ASYNCGEN /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/server/runner.py 695 _replay_from_opening_request
 GENAWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/shared/_context_streams.py 89 __anext__
 GENAWAIT <string> 10 rtrace
 GENAWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/mcp/shared/_context_streams.py 71 receive
 GENAWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/streams/memory.py 126 receive
 GENAWAIT /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/_backends/_asyncio.py 1936 wait
 GENAWAIT /usr/lib/python3.14/asyncio/locks.py 213 wait
ASYNCGEN /home/jimmy/Projects (jimmy's)/SPQR/.venv/lib/python3.14/site-packages/anyio/_core/_fileio.py 112 __aiter__
LOOP READY deque([<Handle <_asyncio.TaskStepMethWrapper object at 0x7fb6bad7a740>()>])
```

## Attempt 2 — minimal rendezvous and thread-notification discrimination

Command: `.venv/bin/python /tmp/spqr-round4-minimal.py > /tmp/spqr-round4-attempt2.txt 2>&1`.

Raw AnyIO/ContextStreams transfers use task-group send and parent receive. Thread test awaits `anyio.to_thread.run_sync(time.sleep, .1)` with a 1.5s fail_after deadline; comparison adds a 25ms asyncio heartbeat. No network or bridge state is involved.

```text
create_memory_object_stream fixture elapsed 0.0
create_context_streams fixture elapsed 0.0
thread wake heartbeat False elapsed 1.502
thread wake heartbeat True elapsed 0.101
```

The first thread test did not raise a timeout; it resumed late at the timer boundary. Thus it measures delayed completion observation, not 1.5 seconds of actual worker computation.

## Scripts

### spqr-round4-deep.py

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
    if counts['recv']<12: print('RECEIVE ENTER',id(self),'STATE',id(self._inner._state),file=sys.stderr)
    item=await rcv(self)
    counts['recv']+=1
    if counts['recv']<12: print('RECEIVE ITEM',id(self),type(item).__name__,repr(item),file=sys.stderr)
    return item
async def strace(self,item):
    counts['send']+=1
    if counts['send']<12: print('SEND ITEM',id(self),'STATE',id(self._inner._state),type(item).__name__,repr(item),file=sys.stderr)
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
    text='Round4 attempt1: stdin kept open; full initialize line flushed\nrc='+str(p.returncode)+'\nstdout='+out+'\nstderr:\n'+err
    print(text);Path('/tmp/spqr-round4-attempt1.txt').write_text(text)
```

### spqr-round4-minimal.py

```python
import asyncio, time, anyio
from mcp.shared._context_streams import create_context_streams
async def main():
    for factory in (anyio.create_memory_object_stream,create_context_streams):
        start=time.monotonic()
        s,r=factory(0)
        async with anyio.create_task_group() as tg:
            tg.start_soon(s.send,'fixture')
            item=await r.receive()
        print(factory.__name__,item,'elapsed',round(time.monotonic()-start,3),flush=True)
    for heartbeat in (False,True):
        stop=asyncio.Event()
        async def pulse():
            while not stop.is_set(): await asyncio.sleep(.025)
        t=asyncio.create_task(pulse()) if heartbeat else None
        start=time.monotonic()
        try:
            with anyio.fail_after(1.5):
                await anyio.to_thread.run_sync(time.sleep,.1)
            print('thread wake heartbeat',heartbeat,'elapsed',round(time.monotonic()-start,3),flush=True)
        except BaseException as e: print('thread wake heartbeat',heartbeat,type(e).__name__,'elapsed',round(time.monotonic()-start,3),flush=True)
        stop.set()
        if t: await t
anyio.run(main)
```
