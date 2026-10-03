"""Stand-in for `claude -p --input-format stream-json --output-format stream-json` in tests (no tokens, deterministic).

Like Claude Code it starts the MCP server named in --mcp-config (our real permtool, with the inherited environment) and
asks it via `tools/call` before running a "tool". Behaviour per user message:
  RUN: <shell command>   → permission request for Bash {command}; allow → run it in the cwd; reply with the outcome
  RUNSEQ: a ;; b ;; @sleep N ;; c  → the same for several commands, one after the other, in ONE turn (batch approval)
Like Claude Code, every call first goes through the PreToolUse hooks from --settings (the danger list): "ask" → the
permission tool even when an allow rule matches; "deny" / exit 2 → denied; "allow" → runs. Then the allow rules of
FAKE_CLAUDE_USER_SETTINGS (a settings.json path: permissions.allow, e.g. "Bash(rm:*)") — a match runs without asking.
  SLOW                   → reply after 1.5 s
  LONG                   → one 9 000-character reply (the host must split it, never truncate)
  CRASH                  → exit 3 mid-turn (the host restarts with --resume)
  [agentj 定时任务 … (a scheduled task's prompt) → runs its lines RUN: <cmd> / SLEEP: <s> / SAY: <text> in order
  {"type":"control_request"} → a control_response (read only between turns): get_context_usage / get_status / list_models
                           / set_model / interrupt answer like Claude Code 2.1.285; any other subtype → error
  /compact               → status compacting, compact_boundary (21262 → 1344), the kept synthetic message re-sent (the
                           replay serve must not push), result local_command "compact"
  /cost                  → a synthetic "Total cost: $0.0123 …" + result local_command "cost"
  /fake-skill            → "SKILL fake-skill ran" (a skill named in init.skills)
  anything else          → "ECHO: <text>"
Every turn ends with a rate_limit_event before its result (what /usage shows).
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

mcp = json.loads(arg("--mcp-config"))["mcpServers"]["agentj"]
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
settings = json.loads(arg("--settings") or "{}")
HOOKS = [h["command"] for m in (settings.get("hooks") or {}).get("PreToolUse", []) for h in m.get("hooks", [])
         if h.get("type") == "command"]
ALLOW = []
if os.environ.get("FAKE_CLAUDE_USER_SETTINGS"):
    with open(os.environ["FAKE_CLAUDE_USER_SETTINGS"]) as f:
        ALLOW = (json.load(f).get("permissions") or {}).get("allow") or []


def allowed_by_rule(cmd):
    for r in ALLOW:
        m = __import__("re").fullmatch(r"Bash\((.*)\)", r)
        if not m:
            continue
        pat = m.group(1)
        if pat.endswith(":*") and (cmd == pat[:-2] or cmd.startswith(pat[:-2] + " ")):
            return True
        if pat == cmd:
            return True
    return False


def hook_decision(tool, inp):
    for c in HOOKS:
        p = subprocess.run(c, shell=True, input=json.dumps({"hook_event_name": "PreToolUse", "tool_name": tool,
                                                           "tool_input": inp, "permission_mode": "default"}),
                           capture_output=True, text=True)
        if p.returncode == 2:
            return "deny", p.stderr.strip()
        if p.returncode == 0 and p.stdout.strip():
            d = json.loads(p.stdout).get("hookSpecificOutput", {}).get("permissionDecision")
            if d in ("ask", "deny", "allow"):
                return d, ""
    return None, ""


def run_one(cmd):
    """One Bash tool call as Claude Code would make it; returns the line the fake agent says."""
    tu = "toolu_" + uuid.uuid4().hex[:12]
    out({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": tu, "name": "Bash", "input": {"command": cmd}}]}})
    d, err = hook_decision("Bash", {"command": cmd})
    if d == "deny":
        return f"被 hook 拒绝：{err or cmd}"
    if d == "allow" or (d is None and allowed_by_rule(cmd)):
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        return f"已执行（未询问，退出码 {r.returncode}）：{cmd}"
    res = rpc("tools/call", {"name": "approve", "arguments": {"tool_name": "Bash", "input": {"command": cmd}, "tool_use_id": tu}})
    ans = json.loads(res["content"][0]["text"])
    if ans["behavior"] == "allow":
        r = subprocess.run(ans["updatedInput"]["command"], shell=True, capture_output=True, text=True)
        return f"已执行（退出码 {r.returncode}）：{cmd}"
    return f"被拒绝：{ans['message']}"
sid = arg("--resume") or str(uuid.uuid4())
MODEL = [arg("--model") or "claude-fake-1"]


def init():
    out({"type": "system", "subtype": "init", "session_id": sid, "tools": ["Bash"], "permissionMode": "default",
         "model": MODEL[0], "claude_code_version": "2.1.285-fake", "skills": ["fake-skill"],
         "slash_commands": ["fake-skill", "clear", "compact", "context", "model", "config", "usage"]})


init()


def say(text):
    out({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}, "session_id": sid})


def control(ev):
    req = ev.get("request") or {}
    st = req.get("subtype")
    if log:
        with open(log, "a") as f:
            f.write(json.dumps({"control": st}) + "\n")
    r = {"subtype": "success", "request_id": ev.get("request_id")}
    if st == "get_context_usage":
        r["response"] = {"categories": [{"name": "System prompt", "tokens": 6396, "kind": "used"},
                                        {"name": "Messages", "tokens": 2648, "kind": "used"},
                                        {"name": "Free space", "tokens": 179239, "kind": "free"}],
                         "totalTokens": 20761, "maxTokens": 200000, "percentage": 10}
    elif st == "get_status":
        r["response"] = {"sections": [{"title": "Session", "rows": [
            {"label": "Version", "value": "2.1.285-fake"}, {"label": "Session ID", "value": sid},
            {"label": "Peer address", "value": "uds:/run/user/0/cc.sock"}, {"label": "cwd", "value": os.getcwd()},
            {"label": "Auth token", "value": "CLAUDE_CODE_OAUTH_TOKEN"}]},
            {"title": "Environment", "rows": [{"label": "Model", "value": MODEL[0]}]}]}
    elif st == "list_models":
        r["response"] = {"models": [{"value": "default", "resolvedModel": "claude-fake-1", "displayName": "Default"},
                                    {"value": "haiku", "resolvedModel": "claude-fake-haiku", "displayName": "Haiku"}]}
    elif st == "set_model":
        MODEL[0] = req.get("model")
    elif st == "interrupt":
        r["response"] = {"still_queued": []}
    else:
        r = {"subtype": "error", "request_id": ev.get("request_id"), "error": f"unsupported {st}"}
    out({"type": "control_response", "response": r})


def synthetic(text):
    out({"type": "assistant", "message": {"model": "<synthetic>", "role": "assistant", "content": [{"type": "text", "text": text}]},
         "session_id": sid})


last_synth = ["Set model to `Fake` for this session only"]
for line in sys.stdin:
    ev = json.loads(line)
    if ev.get("type") == "control_request":          # e.g. interrupt (only read between turns: serve ends the process anyway)
        control(ev)
        continue
    text = ev["message"]["content"]
    init()
    if text == "/compact":
        out({"type": "system", "subtype": "status", "status": "compacting", "session_id": sid})
        time.sleep(0.2)
        out({"type": "system", "subtype": "status", "status": None, "compact_result": "success", "session_id": sid})
        out({"type": "system", "subtype": "compact_boundary", "session_id": sid, "compact_metadata": {
            "trigger": "manual", "pre_tokens": 21262, "post_tokens": 1344, "duration_ms": 1100}})
        synthetic(last_synth[0])                      # Claude Code re-sends the kept synthetic message: not a new reply
        out({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "unifiedWindows": {
            "five_hour": {"utilization": 0.45, "resetsAt": 1790946000}, "seven_day": {"utilization": 0.47, "resetsAt": 1791140400}}}})
        out({"type": "result", "subtype": "success", "is_error": False, "result": "", "local_command": "compact", "session_id": sid})
        continue
    if text == "/cost":
        synthetic("Total cost:            $0.0123\nUsage by model:\n    claude-fake-1:  10 input, 62 output ($0.0123)")
        out({"type": "result", "subtype": "success", "is_error": False, "local_command": "cost", "session_id": sid,
             "result": "Total cost:            $0.0123\nUsage by model:\n    claude-fake-1:  10 input, 62 output ($0.0123)"})
        continue
    if text.startswith("[agentj 定时任务"):       # a scheduled task run: its RUN.md is a tiny script for this stand-in
        for ln in text.splitlines():
            if ln.startswith("RUN: "):
                say(run_one(ln[5:]))
            elif ln.startswith("SLEEP: "):
                time.sleep(float(ln[7:]))
            elif ln.startswith("SAY: "):
                say(ln[5:])
    elif text.startswith("RUN: "):
        say(run_one(text[5:]))
    elif text.startswith("RUNSEQ: "):
        for step in text[8:].split(" ;; "):
            if step.startswith("@sleep "):
                time.sleep(float(step[7:]))
                continue
            say(run_one(step))
    elif text == "SLOW":
        time.sleep(1.5)
        say("慢回复")
    elif text == "LONG":
        say("长" * 9000)
    elif text == "CRASH":
        sys.exit(3)
    elif text == "/fake-skill":
        say("SKILL fake-skill ran")
    else:
        say(f"ECHO: {text}")
    out({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "unifiedWindows": {
        "five_hour": {"utilization": 0.45, "resetsAt": 1790946000}, "seven_day": {"utilization": 0.47, "resetsAt": 1791140400}}}})
    out({"type": "result", "subtype": "success", "is_error": False, "result": "", "session_id": sid})
