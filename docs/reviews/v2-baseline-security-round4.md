# Security diagnostic receipts — round 4

2026-10-20. Three diagnostic attempts of a four-attempt allocation; stopped after the parent
reported unchanged integration success outside sandbox. No source, tests, dependencies,
production configuration, state or services modified. Each command ran inside sandbox from
SPQR using `.venv/bin/python - <<'PY'` with the exact Python body below. Results are copied
from tool output. No live model calls. These are diagnostic observations, not fixes.

## security1 — memory and replay controls

```python
import anyio
from mcp.shared._context_streams import create_context_streams
from mcp.shared.message import SessionMessage
import mcp_types as types
from mcp.server.runner import _replay_from_opening_request

async def trial(kind):
    if kind == 'bare':
        send,recv=anyio.create_memory_object_stream(0)
    else:
        send,recv=create_context_streams(0)
    msg=SessionMessage(types.JSONRPCRequest(jsonrpc='2.0', id=1,method='initialize',params={}))
    async def producer():
        await send.send(msg)
        print(kind,'sent',flush=True)
    with anyio.fail_after(2):
        async with anyio.create_task_group() as tg:
            tg.start_soon(producer)
            if kind == 'replay':
                async with _replay_from_opening_request(recv) as (opening,replayed):
                    got=await replayed.receive()
                    print(kind,'got',got.message.id, opening.method,flush=True)
            else:
                got=await recv.receive()
                print(kind,'got',got.message.id,flush=True)
            tg.cancel_scope.cancel()
async def main():
    print('security1',flush=True)
    for kind in ('bare','context','replay'):
        try: await trial(kind)
        except BaseException as e: print(kind,'FAIL',type(e).__name__,str(e),flush=True)
anyio.run(main)
```

Observed exit 0, tool wall time 0.375364562s:

```text
security1
bare sent
bare got 1
context sent
context got 1
replay sent
replay got 1 initialize
```

## security2 — stdio with default and explicit streams

```python
import subprocess,sys,select,json
base='''import anyio,sys,os
from mcp.server.stdio import stdio_server
from mcp.server.runner import _replay_from_opening_request
from mcp.shared.message import SessionMessage
import mcp_types as types
async def main():
    KW
    async with stdio_server(**kw) as (read,write):
        async with _replay_from_opening_request(read) as (opening,replay):
            item=await replay.receive()
            print('RECEIVED',opening.method,file=sys.stderr,flush=True)
            await write.send(SessionMessage(types.JSONRPCResponse(jsonrpc='2.0',id=1,result={'ok':True})))
            await anyio.sleep(0.1)
anyio.run(main)
'''
for name,kw in [('default','kw={}'),('explicit',"kw={'stdin':anyio.wrap_file(os.fdopen(os.dup(0),'r')), 'stdout':anyio.wrap_file(os.fdopen(os.dup(1),'w'))}")]:
 p=subprocess.Popen([sys.executable,'-c',base.replace('KW',kw)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 p.stdin.write((json.dumps({'jsonrpc':'2.0','id':1,'method':'initialize','params':{}})+'\n').encode());p.stdin.flush()
 ready=select.select([p.stdout],[],[],3)[0]
 out=p.stdout.readline() if ready else b'TIMEOUT'
 p.terminate(); stdout,stderr=p.communicate(timeout=3)
 print('security2',name,'out',out.decode().strip(),'stderr',stderr.decode().strip())
```

Observed exit 0, tool wall time 5.916666242s:

```text
security2 default out TIMEOUT stderr
security2 explicit out TIMEOUT stderr
```

The harness exit is not server success; it terminates both subprocesses after observation.
Both modes time out, so these results do not support changing SDK fd diversion as a fix.

## security3 — async pipe read without MCP transport/replay

```python
import subprocess,sys,select
base='''import anyio,sys,os
from mcp.shared._context_streams import create_context_streams
async def main():
    send,recv=create_context_streams(0)
    async def producer():
        f=anyio.wrap_file(os.fdopen(os.dup(0),'r'))
        line=await f.readline()
        print('READ',repr(line),file=sys.stderr,flush=True)
        await send.send(line)
        print('SENT',file=sys.stderr,flush=True)
    async with anyio.create_task_group() as tg:
        tg.start_soon(producer)
        line=await recv.receive()
        print('GOT',repr(line),flush=True)
        tg.cancel_scope.cancel()
anyio.run(main)
'''
p=subprocess.Popen([sys.executable,'-c',base],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
p.stdin.write(b'hello\n');p.stdin.flush()
ready=select.select([p.stdout],[],[],3)[0]
out=p.stdout.readline() if ready else b'TIMEOUT'
p.terminate();stdout,stderr=p.communicate(timeout=3)
print('security3','out',out.decode().strip(),'stderr',stderr.decode().strip())
```

Observed exit 0, tool wall time 2.905727713s:

```text
security3 out TIMEOUT stderr
```

No READ marker appeared before forced termination. This removes MCP stdio transport and
opening-request replay from the reproduction, but does not alone prove which scheduling,
thread, pipe, or environment mechanism caused the delay. Combined with the separate
outside-sandbox integration success, it supports environment-dependent async I/O behavior.
Do not infer a universally broken AnyIO implementation or a proven kernel/syscall cause.
