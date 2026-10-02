"""Stand-in for `claude -p --input-format stream-json --output-format stream-json` in tests (no tokens, deterministic).

Like Claude Code it starts the MCP server named in --mcp-config (our real permtool, with the inherited environment) and
asks it via `tools/call` before running a "tool". Behaviour per user message:
  RUN: <shell command>   → permission request for Bash {command}; allow → run it in the cwd; reply with the outcome
  SLOW                   → reply after 1.5 s
  LONG                   → one 9 000-character reply (the host must split it, never truncate)
  CRASH                  → exit 3 mid-turn (the host restarts with --resume)
  anything else          → "ECHO: <text>"
FAKE_CLAUDE_LOG (optional): append one JSON line per start with argv, so tests can assert the flags.
"""
import json
import os
import subprocess
import sys
import time
import uuid


def arg(name):
    a = sys.argv
    return a[a.index(name) + 1] if name in a else None


def out(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


log = os.environ.get("FAKE_CLAUDE_LOG")
if log:
    with open(log, "a") as f:
        f.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd()}) + "\n")

mcp = json.loads(arg("--mcp-config"))["mcpServers"]["agentjarvis"]
perm = subprocess.Popen([mcp["command"], *mcp["args"]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
rid = 0


def rpc(method, params):
    global rid
    rid += 1
    perm.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}) + "\n")
    perm.stdin.flush()
    while True:
        m = json.loads(perm.stdout.readline())
        if m.get("id") == rid:
            return m["result"]


rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "fake", "version": "0"}})
sid = arg("--resume") or str(uuid.uuid4())
out({"type": "system", "subtype": "init", "session_id": sid, "tools": ["Bash"], "permissionMode": "default"})


def say(text):
    out({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}, "session_id": sid})


for line in sys.stdin:
    ev = json.loads(line)
    text = ev["message"]["content"]
    if text.startswith("RUN: "):
        cmd = text[5:]
        tu = "toolu_" + uuid.uuid4().hex[:12]
        out({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": tu, "name": "Bash", "input": {"command": cmd}}]}})
        res = rpc("tools/call", {"name": "approve", "arguments": {"tool_name": "Bash", "input": {"command": cmd}, "tool_use_id": tu}})
        ans = json.loads(res["content"][0]["text"])
        if ans["behavior"] == "allow":
            r = subprocess.run(ans["updatedInput"]["command"], shell=True, capture_output=True, text=True)
            say(f"已执行（退出码 {r.returncode}）：{cmd}")
        else:
            say(f"被拒绝：{ans['message']}")
    elif text == "SLOW":
        time.sleep(1.5)
        say("慢回复")
    elif text == "LONG":
        say("长" * 9000)
    elif text == "CRASH":
        sys.exit(3)
    else:
        say(f"ECHO: {text}")
    out({"type": "result", "subtype": "success", "is_error": False, "result": "", "session_id": sid})
