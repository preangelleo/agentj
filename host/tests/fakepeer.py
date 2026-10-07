#!/usr/bin/env python3
"""Stand-in for `claude` / `codex` / `opencode` in a peer-session turn (test_peer_session.py). First argument = which one.

Records what it was started with — argv, environment, cwd and what the cwd holds, stdin, what it can see of the state
directory and of the owner's instruction files — to $HOME/fake-<role>.jsonl (HOME is the test's temporary home, visible
inside the fence), then prints that harness's JSON events. Behaviour from $HOME/fake-mode.json:
{"text": model output, "usage": false, "tool": true, "sleep": s, "state": path, "fail": "login"}.
"""
import json
import os
import sys
import time
import uuid

role, args = sys.argv[1], sys.argv[2:]
home = os.environ.get("HOME", "")


def mode():
    try:
        with open(os.path.join(home, "fake-mode.json")) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


if role == "claude" and args == ["--help"]:
    print("  --tools <tools...>\n  --strict-mcp-config\n  --mcp-config <configs...>\n  --system-prompt <prompt>\n"
          "  --safe-mode\n  --setting-sources <sources>\n  --disable-slash-commands\n  --system-prompt-snapshot <on|off>")
    sys.exit(0)
if role == "codex" and args[:2] == ["features", "list"]:
    for f in ("shell_tool", "unified_exec", "apps", "plugins", "browser_use", "computer_use", "image_generation",
              "in_app_browser", "multi_agent", "view_image", "code_mode_host", "hooks"):
        print(f"{f:40} stable             true")
    sys.exit(0)
if role == "opencode" and args[:2] == ["run", "--help"]:
    print("--agent --format --pure --title --dir --session")
    sys.exit(0)

m = mode()
stdin = sys.stdin.read()


def peek(p):
    try:
        if os.path.isdir(p):
            return sorted(os.listdir(p))
        with open(p) as fh:
            return fh.read()
    except OSError as e:
        return f"ERR:{type(e).__name__}"


rec = {"t": "start", "at": time.time(), "argv": args, "env": dict(os.environ), "cwd": os.getcwd(),
       "cwd_list": sorted(os.listdir(".")), "stdin": stdin, "pid": os.getpid(),
       "state": peek(m["state"]) if m.get("state") else None,
       "claude_md": peek(os.path.join(home, ".claude", "CLAUDE.md")),
       "codex_agents": peek(os.path.join(home, ".codex", "AGENTS.md")),
       "skills": peek(os.path.join(home, ".agents", "skills")),
       "oc_conf": peek(os.path.join(home, ".config", "opencode"))}
if role == "codex":
    for a in args:
        if a.startswith("model_instructions_file="):
            rec["instructions"] = peek(json.loads(a.split("=", 1)[1]))
log = os.path.join(home, f"fake-{role}.jsonl")
with open(log, "a") as fh:
    fh.write(json.dumps(rec) + "\n")
if m.get("sleep"):
    time.sleep(float(m["sleep"]))
with open(log, "a") as fh:
    fh.write(json.dumps({"t": "end", "at": time.time()}) + "\n")

text = m.get("text", '{"decision":"reply","text":"你好，很高兴认识你","topic":"寒暄"}')
usage = m.get("usage", True)


def out(ev):
    print(json.dumps(ev, ensure_ascii=False), flush=True)


if role == "claude":
    sid = args[args.index("--resume") + 1] if "--resume" in args else str(uuid.uuid4())
    out({"type": "system", "subtype": "init", "session_id": sid, "tools": [], "mcp_servers": []})
    if m.get("fail") == "login":
        out({"type": "result", "is_error": True, "result": "Failed to authenticate: OAuth expired", "session_id": sid})
        sys.exit(1)
    content = [{"type": "text", "text": text}]
    if m.get("tool"):
        content.insert(0, {"type": "tool_use", "name": "Bash", "input": {"command": "ls ~/.ssh"}})
    out({"type": "assistant", "message": {"content": content}, "session_id": sid})
    res = {"type": "result", "is_error": False, "result": text, "session_id": sid}
    if usage:
        res["usage"] = {"input_tokens": 100, "cache_creation_input_tokens": 10, "cache_read_input_tokens": 5, "output_tokens": 20}
    out(res)
elif role == "codex":
    sid = args[-2] if "resume" in args else str(uuid.uuid4())     # exec resume [flags] <id> -
    out({"type": "thread.started", "thread_id": sid})
    out({"type": "turn.started"})
    if m.get("tool"):
        out({"type": "item.started", "item": {"id": "i0", "type": "command_execution", "command": "ls ~/.ssh"}})
    out({"type": "item.completed", "item": {"id": "i1", "type": "agent_message", "text": text}})
    out({"type": "turn.completed", **({"usage": {"input_tokens": 200, "cached_input_tokens": 50, "output_tokens": 30}}
                                     if usage else {})})
else:
    sid = args[args.index("-s") + 1] if "-s" in args else "ses_" + uuid.uuid4().hex[:20]
    out({"type": "step_start", "sessionID": sid, "part": {"type": "step-start"}})
    if m.get("tool"):
        out({"type": "tool_use", "sessionID": sid, "part": {"type": "tool", "tool": "bash"}})
    out({"type": "text", "sessionID": sid, "part": {"type": "text", "text": text}})
    fin = {"type": "step-finish", "reason": "stop"}
    if usage:
        fin["tokens"] = {"total": 321, "input": 300, "output": 21, "reasoning": 0, "cache": {"read": 0, "write": 0}}
    out({"type": "step_finish", "sessionID": sid, "part": fin})
