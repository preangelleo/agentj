#!/usr/bin/env python3
"""A stand-in for `codex app-server` (Codex 0.159.2's JSON-RPC over stdio, the parts agentj uses): no model, deterministic.

    fakecodex.py app-server        (JSON-RPC 2.0, one object per line on stdin / stdout)
    fakecodex.py --version

Like Codex, what asks depends on the thread's approval policy: `untrusted` asks before every command that is not a known
read-only one (ls, pwd, cat, echo) and before every file change; `never` / unset runs everything without asking (so a test
sees whether serve asked for `untrusted`). What a turn does (the text of the message):
    RUN: <cmd>             one command (`/usr/bin/bash -lc '<cmd>'`); runs it with /bin/sh after "accept"
    RUNSEQ: a ;; b ;; …    several commands in order
    PATCH: add|update|delete <file>   one file change (an item/started fileChange, then item/fileChange/requestApproval)
    PERM: <path>           item/permissions/requestApproval for write access to <path>
    NET: <cmd>             a command with a networkApprovalContext
    SLEEP: <s>             works for s seconds (turn/interrupt ends it: status "interrupted")
    ASK: <json questions>  item/tool/requestUserInput; replies "ANSWERS: <the answers it got>"   (PROTOCOL §10.7)
    FORM                   mcpServer/elicitation/request; replies "FORM: <the answer>"
    AUTOCOMPACT [legacy]   Codex's own compaction inside the turn (contextCompaction item, meter → 5000; `legacy` adds
                           thread/compacted too), then "ECHO: …"     (P59)
    anything else          one agentMessage "ECHO: <text>"
Every client message and every answer to a server request is logged to FAKE_CX_LOG (JSONL). FAKE_CX_POLICY = the human's
approval_policy for config/read (JSON); FAKE_CX_THREADS = thread ids that "already exist" (resume); FAKE_CX_SANDBOX = the
human's effective sandbox (read-only | workspace-write | danger-full-access, default the last), reported by thread/start|resume,
and config/read's `sandbox_mode` (an explicit owner setting); FAKE_CX_THREAD_SANDBOX = the sandbox a pre-existing thread kept
(what a resume without `sandbox` gets, like Codex 0.159.2).
"""
import json
import os
import shlex
import subprocess
import sys
import threading
import time
import uuid

LOCK = threading.Lock()
WAITS: dict = {}            # server request id → [Event, response]
THREADS: dict = {}          # id → {"policy", "sandbox", "model", "total", "last", "ephemeral"}
STATE = {"n": 0, "interrupt": False, "turn": None}
SAFE = ("ls", "pwd", "cat", "echo")


def log(**kw):
    p = os.environ.get("FAKE_CX_LOG")
    if p:
        with open(p, "a") as f:
            f.write(json.dumps(kw, ensure_ascii=False) + "\n")


def out(obj):
    with LOCK:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def note(method, params):
    out({"method": method, "params": params})


def server_request(method, params, timeout=600):
    with LOCK:
        STATE["n"] += 1
        rid = STATE["n"]
        ev = threading.Event()
        WAITS[rid] = [ev, None]
    out({"method": method, "id": rid, "params": params})
    ev.wait(timeout)
    with LOCK:
        res = WAITS.pop(rid)[1]
    note("serverRequest/resolved", {"threadId": params.get("threadId"), "requestId": rid})
    return res or {}


def usage(tid, add):
    t = THREADS[tid]
    t["total"] += add
    t["last"] = int(os.environ.get("FAKE_CTX_USED") or 20000 + add)
    note("thread/tokenUsage/updated", {"threadId": tid, "turnId": STATE["turn"], "tokenUsage": {
        "total": {"totalTokens": t["total"], "inputTokens": t["total"] - 100, "cachedInputTokens": 5000, "outputTokens": 100,
                  "reasoningOutputTokens": 10},
        "last": {"totalTokens": t["last"], "inputTokens": t["last"] - 50, "cachedInputTokens": 0, "outputTokens": 50,
                 "reasoningOutputTokens": 0},
        "modelContextWindow": 258400}})
    note("account/rateLimits/updated", {"rateLimits": {"limitId": "codex", "primary": {"usedPercent": 20, "windowDurationMins": 10080,
                                                                                      "resetsAt": 1791046812}, "secondary": {"usedPercent": 7, "windowDurationMins": 300, "resetsAt": 1790950000}}})


def say(tid, turn, text):
    iid = "msg_" + uuid.uuid4().hex[:12]
    item = {"type": "agentMessage", "id": iid, "text": text, "phase": "final_answer"}
    note("item/started", {"item": {**item, "text": ""}, "threadId": tid, "turnId": turn})
    note("item/completed", {"item": item, "threadId": tid, "turnId": turn})


def asks(tid, cmd):
    pol = THREADS[tid]["policy"]
    return pol == "untrusted" and cmd.split()[0] not in SAFE


def command(tid, turn, cmd, net=False):
    full = "/usr/bin/bash -lc " + shlex.quote(cmd)
    iid = "exec-" + uuid.uuid4().hex[:12]
    item = {"type": "commandExecution", "id": iid, "command": full, "cwd": os.getcwd(), "status": "inProgress",
            "commandActions": [{"type": "unknown", "command": cmd}]}
    note("item/started", {"item": item, "threadId": tid, "turnId": turn})
    if asks(tid, cmd) or net:
        p = {"kind": "command", "threadId": tid, "turnId": turn, "itemId": iid, "startedAtMs": int(time.time() * 1000),
             "command": full, "cwd": os.getcwd(), "commandActions": item["commandActions"],
             "proposedExecpolicyAmendment": [cmd.split()[0]]}
        if net:
            p["networkApprovalContext"] = {"host": "example.com", "protocol": "https"}
        d = server_request("item/commandExecution/requestApproval", p).get("decision")
        if d != "accept":
            note("item/completed", {"item": {**item, "status": "declined"}, "threadId": tid, "turnId": turn})
            say(tid, turn, f"declined: {cmd}")
            return False
    subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, timeout=60)
    note("item/completed", {"item": {**item, "status": "completed"}, "threadId": tid, "turnId": turn})
    say(tid, turn, f"ran: {cmd}")
    return True


def patch(tid, turn, kind, path):
    full = path if os.path.isabs(path) else os.path.join(os.getcwd(), path)
    diff = {"add": "hi\n", "update": "@@ -1 +1 @@\n-keep me\n+changed\n", "delete": ""}[kind]
    iid = "patch-" + uuid.uuid4().hex[:12]
    item = {"type": "fileChange", "id": iid, "changes": [{"path": full, "kind": {"type": kind}, "diff": diff}], "status": "inProgress"}
    note("item/started", {"item": item, "threadId": tid, "turnId": turn})
    if THREADS[tid]["policy"] == "untrusted":
        d = server_request("item/fileChange/requestApproval", {"threadId": tid, "turnId": turn, "itemId": iid,
                                                                "startedAtMs": int(time.time() * 1000), "reason": None,
                                                                "grantRoot": None}).get("decision")
        if d != "accept":
            note("item/completed", {"item": {**item, "status": "declined"}, "threadId": tid, "turnId": turn})
            say(tid, turn, f"patch declined: {path}")
            return
    if kind == "delete":
        os.remove(full)
    else:
        with open(full, "w") as f:
            f.write("hi\n" if kind == "add" else "changed\n")
    say(tid, turn, f"patched: {kind} {path}")


# P57 (F24): the host's compaction preparation and host notes, as a cooperative Agent would treat them.
#   "[agentj:compact-prepare]" + a `<…/.agentj/handover/<key>.md>` path → writes that file (unless FAKE_NO_HANDOVER=1;
#   FAKE_PREP_SLEEP=s first → a preparation that times out) and replies "交接写好了"; lines starting 「（Agent J：」 /
#   "(Agent J: " (the handover note / the context reminder) are logged as {"host_note": …} and taken off, so the rest of
#   the message behaves as before; FAKE_CTX_USED = the context the next turn reports (compaction drops it).
def p57_prepare(text):
    import re as _re
    m = _re.search(r"`([^`]*\.agentj/handover/[^`]+\.md)`", text)
    if os.environ.get("FAKE_PREP_SLEEP"):
        time.sleep(float(os.environ["FAKE_PREP_SLEEP"]))
    if m and os.environ.get("FAKE_NO_HANDOVER") != "1":
        with open(m.group(1), "w") as f:
            f.write("# Handover\n- goal: the user's words\n- done / in progress / next\n- decisions\n- paths\n- open questions\n")
        return "交接写好了"
    return "没写交接"


def p57_notes(text, logf):
    keep = []
    for ln in text.split("\n"):
        if ln.startswith(("（Agent J：", "(Agent J: ")):
            logf(ln)
        else:
            keep.append(ln)
    return "\n".join(keep).strip("\n")


def run_turn(tid, turn, text):
    STATE["interrupt"] = False
    STATE["turn"] = turn
    note("turn/started", {"threadId": tid, "turn": {"id": turn, "status": "inProgress"}})
    status = "completed"
    if ("（Agent J：" in text or "(Agent J: " in text) and not text.startswith("[agentj:"):
        text = p57_notes(text, lambda ln: log(method="HOST_NOTE", path="", host_note=ln))
    try:
        if text.startswith("[agentj:compact-prepare]"):
            say(tid, turn, p57_prepare(text))
        elif text.startswith("RUN: "):
            command(tid, turn, text[5:].strip())
        elif text.startswith("RUNSEQ: "):
            for c in text[8:].split(";;"):
                command(tid, turn, c.strip())
        elif text.startswith("NET: "):
            command(tid, turn, text[5:].strip(), net=True)
        elif text.startswith("PATCH: "):
            kind, path = text[7:].split(None, 1)
            patch(tid, turn, kind, path.strip())
        elif text.startswith("PERM: "):
            r = server_request("item/permissions/requestApproval", {
                "threadId": tid, "turnId": turn, "itemId": "perm-1", "cwd": os.getcwd(), "startedAtMs": 0, "reason": "needs to write",
                "permissions": {"fileSystem": {"write": [text[6:].strip()]}, "network": None}})
            say(tid, turn, "granted: " + json.dumps(r.get("permissions"), sort_keys=True))
        elif text.startswith("ASK: "):                # item/tool/requestUserInput (PROTOCOL §10.7), questions as JSON
            r = server_request("item/tool/requestUserInput", {"threadId": tid, "turnId": turn, "itemId": "ui-1",
                                                               "questions": json.loads(text[5:])})
            say(tid, turn, "ANSWERS: " + json.dumps(r.get("answers"), ensure_ascii=False, sort_keys=True))
        elif text == "FORM":                          # an MCP elicitation (a form nobody on the phone can fill)
            r = server_request("mcpServer/elicitation/request", {"threadId": tid, "turnId": turn, "serverName": "x",
                                                                 "message": "fill this", "requestedSchema": {}})
            say(tid, turn, "FORM: " + json.dumps(r, sort_keys=True))
        elif text.startswith("SLEEP: "):
            t0 = time.time()
            while time.time() - t0 < float(text[7:]):
                if STATE["interrupt"]:
                    status = "interrupted"
                    break
                time.sleep(0.05)
            if status == "completed":
                say(tid, turn, "slept")
        elif text.startswith("AUTOCOMPACT"):          # P59: Codex's own compaction inside a turn (context full)
            legacy = text.strip() == "AUTOCOMPACT legacy"
            note("item/started", {"item": {"type": "contextCompaction", "id": "ac1"}, "threadId": tid, "turnId": turn})
            THREADS[tid]["last"] = 5000
            note("thread/tokenUsage/updated", {"threadId": tid, "turnId": turn, "tokenUsage": {
                "total": {"totalTokens": THREADS[tid]["total"]}, "last": {"totalTokens": 5000}, "modelContextWindow": 258400}})
            note("item/completed", {"item": {"type": "contextCompaction", "id": "ac1"}, "threadId": tid, "turnId": turn})
            if legacy:
                note("thread/compacted", {"threadId": tid, "turnId": turn})
            say(tid, turn, "ECHO: " + text)
            return                                    # no usage(): the meter stays at the compacted figure
        elif text.startswith("[agentj 定时任务"):        # a scheduled task: its prompt lines RUN: / SAY:
            for ln in text.splitlines():
                if ln.startswith("RUN: "):
                    command(tid, turn, ln[5:])
                elif ln.startswith("SAY: "):
                    say(tid, turn, ln[5:])
        else:
            say(tid, turn, f"ECHO: {text}")
        usage(tid, 1000)
    finally:
        note("turn/completed", {"threadId": tid, "turn": {"id": turn, "status": status, "items": []}})


SANDBOX = {"read-only": {"type": "readOnly", "networkAccess": False},
           "workspace-write": {"type": "workspaceWrite", "writableRoots": [], "networkAccess": False, "excludeTmpdirEnvVar": False,
                               "excludeSlashTmp": False},
           "danger-full-access": {"type": "dangerFullAccess"}}


def thread_answer(tid, p):
    """The effective sandbox: the thread's `sandbox` parameter, else the human's config (FAKE_CX_SANDBOX, default full access)."""
    t = THREADS[tid]
    sb = t["sandbox"] or os.environ.get("FAKE_CX_SANDBOX") or "danger-full-access"
    return {"thread": {"id": tid, "cwd": p.get("cwd")}, "model": t["model"], "cwd": p.get("cwd"),
            "approvalPolicy": t["policy"], "approvalsReviewer": p.get("approvalsReviewer") or "auto_review",
            "sandbox": SANDBOX[sb]}


def handle(m):
    method, rid, p = m.get("method"), m.get("id"), m.get("params") or {}
    if method == "initialize":
        return {"userAgent": "agentjarvis/0.159.2-fake (Linux; x86_64)", "codexHome": "~/.codex"}
    if method == "config/read":
        pol = json.loads(os.environ["FAKE_CX_POLICY"]) if os.environ.get("FAKE_CX_POLICY") else None
        # FAKE_CX_SANDBOX also stands for an explicit `sandbox_mode` in the owner's config.toml (F30: unset → Agent J sends
        # danger-full-access itself)
        return {"config": {"approval_policy": pol, "approvals_reviewer": "auto_review", "sandbox_mode": os.environ.get("FAKE_CX_SANDBOX"), "model": "gpt-fake",
                           "model_reasoning_effort": "medium"}}
    if method in ("thread/start", "thread/resume"):
        if method == "thread/resume":
            tid = p.get("threadId")
            if tid not in THREADS and tid not in json.loads(os.environ.get("FAKE_CX_THREADS") or "[]"):
                return {"__error": {"code": -32600, "message": f"thread not found: {tid}"}}
        else:
            tid = str(uuid.uuid4())
        old = THREADS.get(tid) or {"total": 0, "last": 0, "sandbox": os.environ.get("FAKE_CX_THREAD_SANDBOX") if method == "thread/resume" else None}
        # like Codex 0.159.2: a resume that names no sandbox keeps the thread's own (FAKE_CX_THREAD_SANDBOX for a thread that
        # existed before this process) — ADR-A175
        THREADS[tid] = {**old, "policy": p.get("approvalPolicy") or old.get("policy") or "never",
                        "sandbox": p.get("sandbox") or (old.get("sandbox") if method == "thread/resume" else None),
                        "model": p.get("model") or "gpt-fake", "ephemeral": p.get("ephemeral")}
        if method == "thread/start":
            note("thread/started", {"thread": {"id": tid}})
        return thread_answer(tid, p)
    if method == "turn/start":
        tid = p.get("threadId")
        if tid not in THREADS:
            return {"__error": {"code": -32600, "message": "thread not loaded"}}
        if p.get("model"):
            THREADS[tid]["model"] = p["model"]
        turn = str(uuid.uuid4())
        text = "".join(x.get("text", "") for x in p.get("input") or [])
        threading.Thread(target=run_turn, args=(tid, turn, text), daemon=True).start()
        return {"turn": {"id": turn, "status": "inProgress", "items": []}}
    if method == "turn/interrupt":
        STATE["interrupt"] = True
        return {}
    if method == "thread/compact/start":
        tid = p.get("threadId")

        def compact():
            turn = str(uuid.uuid4())
            STATE["turn"] = turn
            note("turn/started", {"threadId": tid, "turn": {"id": turn}})
            note("item/started", {"item": {"type": "contextCompaction", "id": "c1"}, "threadId": tid, "turnId": turn})
            time.sleep(0.3)
            THREADS[tid]["last"] = 4724
            note("thread/tokenUsage/updated", {"threadId": tid, "turnId": turn, "tokenUsage": {
                "total": {"totalTokens": THREADS[tid]["total"]}, "last": {"totalTokens": 4724}, "modelContextWindow": 258400}})
            note("item/completed", {"item": {"type": "contextCompaction", "id": "c1"}, "threadId": tid, "turnId": turn})
            note("turn/completed", {"threadId": tid, "turn": {"id": turn, "status": "completed", "items": []}})
        threading.Thread(target=compact, daemon=True).start()
        return {}
    if method == "model/list":
        effs = [{"reasoningEffort": e, "description": e} for e in ("low", "medium", "high")]
        return {"data": [{"id": "gpt-fake", "model": "gpt-fake", "displayName": "GPT Fake", "description": "default", "hidden": False,
                          "supportedReasoningEfforts": effs, "defaultReasoningEffort": "medium"},
                         {"id": "gpt-fake-mini", "model": "gpt-fake-mini", "displayName": "GPT Fake Mini", "description": "small",
                          "hidden": False},
                         {"id": "gpt-hidden", "model": "gpt-hidden", "displayName": "Hidden", "hidden": True}], "nextCursor": None}
    if method == "account/rateLimits/read":
        return {"rateLimits": {"limitId": "codex", "primary": {"usedPercent": 20, "windowDurationMins": 10080, "resetsAt": 1791046812},
                               "secondary": {"usedPercent": 7, "windowDurationMins": 300, "resetsAt": 1790950000}}}
    if method == "thread/delete":
        THREADS.pop(p.get("threadId"), None)
        return {}
    return {"__error": {"code": -32601, "message": f"unknown method {method}"}}


def main():
    a = sys.argv[1:]
    if "--version" in a:
        print("codex-cli 0.159.2-fake")
        return
    if a[:1] != ["app-server"]:
        sys.exit("usage: fakecodex.py app-server")
    for line in sys.stdin:
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if "method" not in m and "id" in m:                    # an answer to one of our server requests
            log(answer=m.get("result"), id=m.get("id"), error=m.get("error"))
            with LOCK:
                w = WAITS.get(m["id"])
                if w:
                    w[1] = m.get("result") or {}
                    w[0].set()
            continue
        log(method=m.get("method"), params=m.get("params"))
        if "id" not in m:                                       # a notification (initialized)
            continue
        r = handle(m)
        if isinstance(r, dict) and "__error" in r:
            out({"id": m["id"], "error": r["__error"]})
        else:
            out({"id": m["id"], "result": r})


if __name__ == "__main__":
    main()
