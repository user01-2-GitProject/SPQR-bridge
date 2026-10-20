"""TEST FIXTURE — FAKE AGENT. Not a model, not a live agent run.

Emulates the *output formats* of Claude Code (`-p --output-format json`) and Codex
(`exec --json`) so the bridge can be exercised with real local processes. Behaviour is
selected by a `FAKE_MODE=...` first line in the stdin prompt.
"""

import json
import os
import subprocess
import sys
import time

args = sys.argv[1:]
if args and args[0] == "--fixture-nostdin":
    time.sleep(300)  # never reads stdin
    sys.exit(0)
if args and args[0] == "--fixture-stubborn":
    # Ignores SIGTERM, never reads stdin, leaves a same-group SIGTERM-ignoring descendant with
    # redirected stdio, and records both PIDs before the parent has finished wiring its pipes.
    import signal
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    child = subprocess.Popen([sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, "
                              "signal.SIG_IGN); time.sleep(300)"], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(args[1], "w") as f:
        f.write(f"{os.getpid()} {child.pid}")
    time.sleep(300)
    sys.exit(0)

is_codex = bool(args) and args[0] == "exec"
stdin = sys.stdin.read()
first = stdin.splitlines()[0] if stdin else ""
mode, _, rest = first.removeprefix("FAKE_MODE=").partition(" ") if first.startswith("FAKE_MODE=") else ("echo", "", "")


def emit(text, error=None):
    if is_codex:
        print(json.dumps({"type": "thread.started", "thread_id": "fake-thread"}))
        if error:
            print(json.dumps({"type": "turn.failed", "error": {"message": error}}))
        else:
            print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": text}}))
            print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}))
    else:
        print(json.dumps({"type": "result", "subtype": "error_during_execution" if error else "success",
                          "is_error": bool(error), "result": text, "session_id": "fake-session",
                          "total_cost_usd": 0, "usage": {"input_tokens": 1}}))


if mode == "echo":
    emit(json.dumps({"fixture": "fake_agent", "argv": args, "cwd": os.getcwd(),
                     "env": dict(os.environ), "stdin": stdin}))
elif mode == "fail":
    print("fatal: auth failed token=supersecretvalue123 sk-ant-abcdefghijklmnopqrst", file=sys.stderr)
    sys.exit(3)
elif mode == "semantic":
    emit("", error="fake semantic failure")
elif mode == "malformed":
    print("this is not json")
elif mode == "empty":
    pass
elif mode == "flood":
    chunk = b"x" * 65536
    for _ in range(200):
        sys.stdout.buffer.write(chunk)
    sys.stdout.flush()
elif mode == "hang":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
    with open(rest, "w") as f:
        f.write(f"{os.getpid()} {child.pid}")
    time.sleep(300)
elif mode == "orphan":  # exits successfully but leaves a descendant behind in its group
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(rest, "w") as f:
        f.write(f"{os.getpid()} {child.pid}")
    emit("orphan done")
elif mode == "badmeta":  # Claude-shaped success with a non-scalar session_id
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "x",
                      "session_id": {}, "usage": {"input_tokens": 1}}))
elif mode == "bigusage":
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "ok",
                      "session_id": "s", "total_cost_usd": 0.5,
                      "usage": {"input_tokens": 7, **{f"k{i}": "v" * 50 for i in range(500)}}}))
elif mode == "denied":
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "partial",
                      "session_id": "s", "permission_denials": [
                          {"tool_name": "Bash", "tool_use_id": f"u{i}", "tool_input": {"command": "x" * 5000}}
                          for i in range(100)]}))
elif mode == "sysobj":  # a non-result Claude object must not count as success
    print(json.dumps({"type": "system", "subtype": "init", "session_id": "s"}))
elif mode == "progressonly":  # Codex progress without a completed turn
    print(json.dumps({"type": "thread.started", "thread_id": "t"}))
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "partial"}}))
elif mode == "slow":
    secs, marker = rest.split(" ", 1)
    with open(marker, "a") as f:
        f.write(f"start {time.monotonic()}\n")
    time.sleep(float(secs))
    with open(marker, "a") as f:
        f.write(f"end {time.monotonic()}\n")
    emit("slow done")
