#!/usr/bin/env python3
"""P305: a stand-in for an ACP agent (`kimi acp`, Agent Client Protocol v1, JSON-RPC 2.0 one object per line): no model,
deterministic. Shapes follow Kimi Code CLI 2.2.0 (packages/acp-server: modes default / plan / auto / yolo, approval options
approve_once (allow_once) / approve_always (allow_always) / reject (reject_once), questions = one allow_once per answer + a
reject_once "Skip", `auth_required` = -32000).

    fakeacp.py acp | --version

What a prompt does (its last text block):
    TOOL: <cmd>      a tool_call (title "Bash"), then session/request_permission; replies "PICKED: <optionId>"
    ASK              a question (two answers + Skip); replies "PICKED: <optionId>"
    SLEEP: <s>       works for s seconds; session/cancel ends it (stopReason "cancelled")
    YOLO             the agent switches itself to mode "yolo" (current_mode_update), then "ECHO: YOLO"
    QTOOL: <cmd>     P310: Qwen Code's tool shape (title = command line, allow_always ×2 / allow_once / reject_once)
    QYOLO            P310: mode → yolo announced as Qwen's `qwen/notify/session/mode-update`
    ENV              P310: replies whether OPENAI_API_KEY is set, OPENAI_BASE_URL, OPENAI_MODEL, QWEN_CODE_DISABLE_CRON
    DTOOL: <cmd>     P329: DeepSeek Harness' escalation shape (title "bash", kind other, rawInput.command +
                     sandbox_permissions; the request carries only the toolCallId; allow-once / reject-once)
    DENV             P329: the process's cwd, DSH_PERMISSION_MODE, DSH_TELEMETRY_MODE, which keys are set (never a value)
    anything else    "ECHO: <text>" in two chunks + a usage_update
Env: FAKE_ACP_LOG = JSONL of every client message (and answers); FAKE_ACP_AUTH=1 → session/new | load | resume refused with
-32000 (P423: FAKE_ACP_AUTH_DATA = its `data`); FAKE_ACP_PROMPT_ERR = every session/prompt answered with this error message
(code FAKE_ACP_PROMPT_CODE, default -32000); FAKE_ACP_MODE = the mode a new session starts in (default "default"); FAKE_ACP_SESSIONS = ids that exist (resume).
P329: FAKE_ACP_DSH=1 → dsh 0.2.0-rc.2's shapes: no authMethods / loadSession, embeddedContext false, sessions without modes
(session/set_mode is "method not found"), the model option's values JSON pairs '["provider","model"]'.
"""
import json
import os
import sys
import threading
import time
import uuid

LOCK = threading.Lock()
WAITS: dict = {}
STATE = {"n": 0, "cancel": threading.Event(), "mode": {}}
MODES = [{"id": m, "name": m.title()} for m in ("default", "plan", "auto", "yolo")]
CONFIG = [{"id": "model", "name": "Model", "category": "model", "type": "select", "currentValue": "kimi-for-coding",
           "options": [{"value": "kimi-for-coding", "name": "Kimi for coding"}, {"value": "k3", "name": "K3"}]},
          {"id": "thinking", "name": "Thinking", "category": "thought_level", "type": "select", "currentValue": "on",
           "options": [{"value": "on", "name": "On"}, {"value": "off", "name": "Off"}]}]


def log(**kw):
    p = os.environ.get("FAKE_ACP_LOG")
    if p:
        with open(p, "a") as f:
            f.write(json.dumps(kw, ensure_ascii=False) + "\n")


def out(obj):
    with LOCK:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def update(sid, u):
    out({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": u}})


def chunk(sid, text):
    update(sid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}})


def ask(method, params):
    STATE["n"] += 1
    rid = f"s{STATE['n']}"
    ev = threading.Event()
    WAITS[rid] = [ev, None]
    out({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
    ev.wait(60)
    return WAITS.pop(rid)[1] or {}


DSH_CONFIG = [{"id": "model", "name": "Model", "category": "model", "type": "select",
               "currentValue": '["agentj-router","Olares/Qwen3.8-27B"]',
               "options": [{"group": "agentj-router", "name": "Agent J route", "options": [
                   {"value": '["agentj-router","Olares/Qwen3.8-27B"]', "name": "Olares/Qwen3.8-27B"}]},
                   {"group": "deepseek-official", "name": "DeepSeek", "options": [
                       {"value": '["deepseek-official","deepseek-flash"]', "name": "DeepSeek-V41-Flash"},
                       {"value": '["deepseek-official","deepseek-v4-pro"]', "name": "DeepSeek-V4-Pro"}]}]}]


def dsh():
    return os.environ.get("FAKE_ACP_DSH") == "1"


def session(rid, sid):
    if dsh():
        return {"sessionId": sid, "configOptions": DSH_CONFIG}
    return {"sessionId": sid, "modes": {"currentModeId": STATE["mode"].setdefault(sid, os.environ.get("FAKE_ACP_MODE", "default")),
                                        "availableModes": MODES}, "configOptions": CONFIG}


def prompt(rid, p):
    sid = p.get("sessionId")
    blocks = p.get("prompt") or []
    text = next((b.get("text") for b in reversed(blocks) if b.get("type") == "text"), "") or ""
    STATE["cancel"].clear()
    if text.startswith("TOOL: "):
        tid = "tc-" + uuid.uuid4().hex[:6]
        update(sid, {"sessionUpdate": "tool_call", "toolCallId": tid, "title": "Bash", "kind": "execute", "status": "pending",
                     "rawInput": {"command": text[6:]}})
        r = ask("session/request_permission", {"sessionId": sid, "toolCall": {"toolCallId": tid},
                                               "options": [{"optionId": "approve_once", "name": "Approve once", "kind": "allow_once"},
                                                           {"optionId": "approve_always", "name": "Approve for this session",
                                                            "kind": "allow_always"},
                                                           {"optionId": "reject", "name": "Reject", "kind": "reject_once"}]})
        chunk(sid, "PICKED: " + str(((r.get("result") or {}).get("outcome") or {}).get("optionId")))
    elif text == "ASK":
        r = ask("session/request_permission", {"sessionId": sid, "toolCall": {"toolCallId": "q1", "title": "AskUserQuestion",
                                               "content": [{"type": "content", "content": {"type": "text", "text": "Which colour?"}}]},
                                               "options": [{"optionId": "q0_opt_0", "name": "Red", "kind": "allow_once"},
                                                           {"optionId": "q0_opt_1", "name": "Blue", "kind": "allow_once"},
                                                           {"optionId": "q0_skip", "name": "Skip", "kind": "reject_once"}]})
        chunk(sid, "PICKED: " + str(((r.get("result") or {}).get("outcome") or {}).get("optionId")))
    elif text.startswith("SLEEP: "):
        if STATE["cancel"].wait(float(text[7:])):
            out({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "cancelled"}})
            return
    elif text.startswith("QTOOL: "):        # P310: Qwen Code's shape — title = the command line, kind execute, rawInput.command
        tid = "call_" + uuid.uuid4().hex[:6]
        tc = {"toolCallId": tid, "title": text[7:] + " [in /w] (desc)", "kind": "execute", "status": "pending",
              "rawInput": {"command": text[7:], "description": "desc", "directory": "/w"}}
        update(sid, {"sessionUpdate": "tool_call", **tc, "title": "Shell", "rawInput": {}})
        r = ask("session/request_permission", {"sessionId": sid, "toolCall": tc,
                                               "options": [{"optionId": "proceed_always_project", "name": "Always (project)",
                                                            "kind": "allow_always"},
                                                           {"optionId": "proceed_always_user", "name": "Always (user)",
                                                            "kind": "allow_always"},
                                                           {"optionId": "proceed_once", "name": "Allow", "kind": "allow_once"},
                                                           {"optionId": "cancel", "name": "Reject", "kind": "reject_once"}]})
        chunk(sid, "PICKED: " + str(((r.get("result") or {}).get("outcome") or {}).get("optionId")))
    elif text == "QYOLO":                   # P310: Qwen Code announces a mode change with its own notification
        STATE["mode"][sid] = "yolo"
        out({"jsonrpc": "2.0", "method": "qwen/notify/session/mode-update", "params": {"v": 1, "sessionId": sid,
                                                                                        "currentModeId": "yolo"}})
        chunk(sid, "ECHO: QYOLO")
    elif text == "ENV":                     # P310: which model variables the process got (names + OPENAI_MODEL, never a key)
        chunk(sid, "ENV: " + json.dumps({"key": bool(os.environ.get("OPENAI_API_KEY")), "base": os.environ.get("OPENAI_BASE_URL"),
                                         "model": os.environ.get("OPENAI_MODEL"), "cron_off": os.environ.get("QWEN_CODE_DISABLE_CRON")}))
    elif text.startswith("DTOOL: "):        # P329: dsh asks only for an escalation retry, by toolCallId
        tid = uuid.uuid4().hex[:16]
        update(sid, {"sessionUpdate": "tool_call", "toolCallId": tid, "title": "bash", "kind": "other", "status": "in_progress",
                     "rawInput": {"command": text[7:], "description": "d", "justification": "j",
                                  "sandbox_permissions": "workspace-write"}})
        r = ask("session/request_permission", {"sessionId": sid, "toolCall": {"toolCallId": tid},
                                               "options": [{"optionId": "allow-once", "name": "Allow once", "kind": "allow_once"},
                                                           {"optionId": "reject-once", "name": "Reject", "kind": "reject_once"}]})
        chunk(sid, "PICKED: " + str(((r.get("result") or {}).get("outcome") or {}).get("optionId")))
    elif text == "DENV":
        chunk(sid, "DENV: " + json.dumps({"cwd": os.getcwd(), "perm": os.environ.get("DSH_PERMISSION_MODE"),
                                          "telemetry": os.environ.get("DSH_TELEMETRY_MODE"),
                                          "router_key": os.environ.get("DSH_ROUTER_API_KEY") == "agentj-no-key" and "placeholder"
                                          or bool(os.environ.get("DSH_ROUTER_API_KEY")),
                                          "deepseek_key": bool(os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("DSH_DEEPSEEK_API_KEY")),
                                          "deepseek_inner": bool(os.environ.get("DSH_DEEPSEEK_API_KEY")), "argv": sys.argv[1:]}))
    elif text == "YOLO":
        STATE["mode"][sid] = "yolo"
        update(sid, {"sessionUpdate": "current_mode_update", "currentModeId": "yolo"})
        chunk(sid, "ECHO: YOLO")
    else:
        chunk(sid, "ECHO: ")
        chunk(sid, text)
        update(sid, {"sessionUpdate": "usage_update", "used": 1200, "size": 262144})
    out({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})


def handle(m):
    method, rid, p = m.get("method"), m.get("id"), m.get("params") or {}
    if method is None:                      # an answer to one of our requests
        w = WAITS.get(m.get("id"))
        if w:
            w[1] = m
            w[0].set()
        return
    auth = os.environ.get("FAKE_ACP_AUTH") == "1"
    if method == "initialize" and dsh():
        out({"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": 1, "agentInfo": {"name": "deepseek-harness-acp", "version": "0.0.1"},
            "agentCapabilities": {"mcpCapabilities": {"http": True}, "promptCapabilities": {
                "image": False, "audio": False, "embeddedContext": False},
                "sessionCapabilities": {"close": {}, "list": {}, "resume": {}}}, "authMethods": []}})
    elif method == "session/set_mode" and dsh():
        out({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "Method not found"}})
    elif method == "initialize":
        out({"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": 1, "agentInfo": {"name": "Fake ACP", "version": "2.2.0"},
            "agentCapabilities": {"loadSession": True, "sessionCapabilities": {"resume": {}, "close": {}}},
            "authMethods": [{"id": "login", "type": "terminal", "name": "Login"}]}})
    elif method in ("session/new", "session/resume", "session/load"):
        if auth:
            out({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": "Authentication required",
                                                         **({"data": os.environ["FAKE_ACP_AUTH_DATA"]}
                                                            if os.environ.get("FAKE_ACP_AUTH_DATA") else {})}})
        elif method == "session/new":
            out({"jsonrpc": "2.0", "id": rid, "result": session(rid, "sess-" + uuid.uuid4().hex[:8])})
        elif p.get("sessionId") in (os.environ.get("FAKE_ACP_SESSIONS") or "").split(","):
            r = session(rid, p["sessionId"])
            r.pop("sessionId")
            out({"jsonrpc": "2.0", "id": rid, "result": r})
        else:
            out({"jsonrpc": "2.0", "id": rid, "error": {"code": -32002, "message": "session not found"}})
    elif method == "session/set_mode":
        STATE["mode"][p.get("sessionId")] = p.get("modeId")
        out({"jsonrpc": "2.0", "id": rid, "result": {}})
    elif method == "session/set_config_option":
        for o in (DSH_CONFIG if dsh() else CONFIG):
            if o["id"] == p.get("configId"):
                o["currentValue"] = p.get("value")
        out({"jsonrpc": "2.0", "id": rid, "result": {"configOptions": DSH_CONFIG if dsh() else CONFIG}})
    elif method == "session/prompt" and os.environ.get("FAKE_ACP_PROMPT_ERR"):
        out({"jsonrpc": "2.0", "id": rid, "error": {"code": int(os.environ.get("FAKE_ACP_PROMPT_CODE") or -32000),
                                                     "message": os.environ["FAKE_ACP_PROMPT_ERR"]}})
    elif method == "session/prompt":
        threading.Thread(target=prompt, args=(rid, p), daemon=True).start()
    elif method == "session/cancel":
        STATE["cancel"].set()
    elif method == "session/close":
        out({"jsonrpc": "2.0", "id": rid, "result": {}})
    elif rid is not None:
        out({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "method not found"}})


def main():
    if "--version" in sys.argv:
        print("0.2.0-rc.2" if dsh() else "2.2.0")
        return
    for line in sys.stdin:
        try:
            m = json.loads(line)
        except ValueError:
            continue
        log(**{"in": m})
        handle(m)


if __name__ == "__main__":
    main()
