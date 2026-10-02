#!/usr/bin/env python3
"""A stand-in for `opencode serve` (OpenCode 1.18.32's HTTP API, the parts jarvis uses): no model, deterministic.

    fakeopencode.py serve --hostname 127.0.0.1 --port N      (prints "opencode server listening on http://127.0.0.1:N")
    fakeopencode.py --version

HTTP Basic auth against OPENCODE_SERVER_PASSWORD (user OPENCODE_SERVER_USERNAME or "opencode"); 401 otherwise. Endpoints:
GET /event (SSE, chunked like Bun), /path, /config, /agent, /permission, /session/status, /session/{id},
/session/{id}/message; POST /session, /session/{id}/prompt_async, /permission/{id}/reply, /question/{id}/reject;
PATCH /session/{id} (rules appended, as OpenCode merges them). Permission rules are evaluated like OpenCode: last match of
agent rules + session rules.

What a prompt does (the text of the message):
    RUN: <cmd>            one bash call (asks unless the rules allow / deny it); runs it with /bin/sh on "once"
    RUNSEQ: a ;; b ;; …   several bash calls in order
    EDIT: <file>          an edit permission request (metadata {filepath, diff}); writes "edited" on "once"
    DROPSSE               closes every event stream, then answers while nobody listens (the client must catch up)
    FOREIGN: <cmd>        asks, then the server ITSELF replies "once" (what a stolen password would allow)
    SLEEP: <s>            works for s seconds (POST /session/{id}/abort ends it, like OpenCode: MessageAbortedError)
    anything else         one text part "ECHO: <text>"
Slash-command endpoints (PROMPT-26 7a): POST /session/{id}/summarize (a summary text part that is NOT a reply, then the
assistant message with summary:true and small tokens), POST /session/{id}/abort, GET /config/providers, GET /global/health;
GET /session/{id} carries cost + tokens, assistant messages carry modelID / providerID / tokens.
Every request is logged (method, path, JSON body) to FAKE_OC_LOG (JSONL). FAKE_OC_AGENT_RULES = the build agent's ruleset
(JSON); default = OpenCode's own defaults. FAKE_OC_ENV_LOG = file to dump the names of OPENCODE_* variables it got.
"""
import base64
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from jarvis_host.agent_opencode import evaluate  # noqa: E402  (OpenCode's matcher, ported; the e2e checks the real one)

DEFAULT_RULES = [
    {"permission": "*", "pattern": "*", "action": "allow"}, {"permission": "doom_loop", "pattern": "*", "action": "ask"},
    {"permission": "external_directory", "pattern": "*", "action": "ask"},
    {"permission": "question", "pattern": "*", "action": "deny"}, {"permission": "plan_enter", "pattern": "*", "action": "deny"},
    {"permission": "plan_exit", "pattern": "*", "action": "deny"}, {"permission": "read", "pattern": "*", "action": "allow"},
    {"permission": "read", "pattern": "*.env", "action": "ask"}, {"permission": "question", "pattern": "*", "action": "allow"},
    {"permission": "plan_enter", "pattern": "*", "action": "allow"}]

LOCK = threading.RLock()
SESSIONS: dict = {}          # id → {"permission": [...], "messages": [...], "busy": bool}
PENDING: dict = {}           # permission id → {"info": {...}, "ev": Event, "reply": str|None, "message": str|None}
CLIENTS: list = []           # open SSE writers
N = [0]
RUN = os.urandom(4).hex()
CWD = os.getcwd()


def nid(prefix):
    with LOCK:
        N[0] += 1
        return f"{prefix}_{N[0]:06d}fake{RUN}"         # unique per start (inside the fence every start is pid 2)


def log(**kw):
    p = os.environ.get("FAKE_OC_LOG")
    if p:
        with open(p, "a") as f:
            f.write(json.dumps(kw) + "\n")


def publish(t, props):
    data = ("data: " + json.dumps({"id": nid("evt"), "type": t, "properties": props}) + "\n\n").encode()
    chunk = f"{len(data):x}\r\n".encode() + data + b"\r\n"
    with LOCK:
        for c in list(CLIENTS):
            try:
                c.wfile.write(chunk)
                c.wfile.flush()
            except OSError:
                CLIENTS.remove(c)


def drop_streams():
    with LOCK:
        for c in list(CLIENTS):
            try:
                c.wfile.write(b"0\r\n\r\n")
                c.wfile.flush()
                c.connection.shutdown(2)
            except OSError:
                pass
        CLIENTS.clear()


def agent_rules():
    raw = os.environ.get("FAKE_OC_AGENT_RULES")
    return json.loads(raw) if raw else DEFAULT_RULES


def text_part(sid, msg, text):
    now = int(time.time() * 1000)
    part = {"id": nid("prt"), "sessionID": sid, "messageID": msg["info"]["id"], "type": "text", "text": text,
            "time": {"start": now, "end": now}}
    msg["parts"].append(part)
    publish("message.part.updated", {"sessionID": sid, "part": part, "time": now})


def ask(sid, permission, patterns, metadata, foreign=False):
    """'allow' | 'deny' | ('once' | 'reject', message) — like Permission.ask."""
    rules = [agent_rules(), SESSIONS[sid]["permission"]]
    need = False
    for p in patterns:
        a = evaluate(permission, p, *rules)
        if a == "deny":
            return "deny"
        need |= a == "ask"
    if not need:
        return "allow"
    pid = nid("per")
    info = {"id": pid, "sessionID": sid, "permission": permission, "patterns": patterns, "metadata": metadata,
            "always": [patterns[0].split(" ")[0] + " *"], "tool": {"messageID": "msg_x", "callID": "call_x"}}
    slot = {"info": info, "ev": threading.Event(), "reply": None, "message": None}
    with LOCK:
        PENDING[pid] = slot
    publish("permission.asked", info)
    if foreign:
        time.sleep(0.3)
        reply_to(pid, "once", None)
    slot["ev"].wait(600)
    return slot["reply"] or "reject", slot["message"]


def reply_to(pid, reply, message):
    with LOCK:
        slot = PENDING.pop(pid, None)
    if not slot:
        return False
    slot["reply"], slot["message"] = reply, message
    publish("permission.replied", {"sessionID": slot["info"]["sessionID"], "requestID": pid, "reply": reply})
    slot["ev"].set()
    if reply == "reject":           # like OpenCode: a reject also rejects the session's other open requests
        with LOCK:
            others = [k for k, v in PENDING.items() if v["info"]["sessionID"] == slot["info"]["sessionID"]]
        for k in others:
            reply_to(k, "reject", None)
    return True


def bash_call(sid, msg, cmd, foreign=False):
    r = ask(sid, "bash", [cmd], {"command": cmd}, foreign=foreign)
    if r == "deny":
        text_part(sid, msg, f"被规则拒绝: {cmd}")
        return False
    if r == "allow" or r[0] == "once":
        subprocess.run(["/bin/sh", "-c", cmd], cwd=CWD, capture_output=True, timeout=60)
        text_part(sid, msg, f"ran: {cmd}")
        return True
    text_part(sid, msg, f"被拒绝: {r[1] or 'rejected'}")
    return False


def run_prompt(sid, text):
    s = SESSIONS[sid]
    s["busy"] = True
    s["abort"] = False
    s["messages"].append({"info": {"id": nid("msg"), "role": "user", "time": {"created": int(time.time() * 1000)}},
                          "parts": [{"id": nid("prt"), "type": "text", "text": text, "sessionID": sid}]})
    msg = {"info": {"id": nid("msg"), "role": "assistant", "time": {"created": int(time.time() * 1000)}, "modelID": "big-pickle",
                    "providerID": "opencode", "cost": 0, "tokens": {"total": 8124, "input": 6178, "output": 5, "reasoning": 0,
                                                                   "cache": {"read": 1941, "write": 0}}}, "parts": []}
    s["messages"].append(msg)
    publish("session.status", {"sessionID": sid, "status": {"type": "busy"}})
    try:
        if text.startswith("RUN: "):
            bash_call(sid, msg, text[5:].strip())
        elif text.startswith("RUNSEQ: "):
            for c in text[8:].split(";;"):
                if not bash_call(sid, msg, c.strip()):
                    break
        elif text.startswith("FOREIGN: "):
            bash_call(sid, msg, text[9:].strip(), foreign=True)
        elif text.startswith("EDIT: "):
            path = os.path.join(CWD, text[6:].strip())
            diff = f"Index: {path}\n===\n--- {path}\n+++ {path}\n@@ -1,1 +1,1 @@\n-old\n+edited\n"
            r = ask(sid, "edit", [os.path.relpath(path, "/")], {"filepath": path, "diff": diff})
            if r == "allow" or (r != "deny" and r[0] == "once"):
                open(path, "w").write("edited\n")
                text_part(sid, msg, f"edited: {path}")
            else:
                text_part(sid, msg, "被拒绝: edit")
        elif text.startswith("SLEEP: "):
            t0 = time.time()
            while time.time() - t0 < float(text[7:]) and not s.get("abort"):
                time.sleep(0.05)
            if s.get("abort"):
                publish("session.error", {"sessionID": sid, "error": {"name": "MessageAbortedError", "data": {}}})
            else:
                text_part(sid, msg, "slept")
        elif text.strip() == "DROPSSE":
            drop_streams()
            time.sleep(0.5)
            text_part(sid, msg, "after drop")
        else:
            text_part(sid, msg, f"ECHO: {text}")
    finally:
        s["busy"] = False
        publish("session.status", {"sessionID": sid, "status": {"type": "idle"}})
        publish("session.idle", {"sessionID": sid})


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, obj=None):
        body = b"" if obj is None else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth(self):
        pw = os.environ.get("OPENCODE_SERVER_PASSWORD", "")
        user = os.environ.get("OPENCODE_SERVER_USERNAME") or "opencode"
        want = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
        if not pw or self.headers.get("Authorization") != want:
            log(method=self.command, path=self.path, status=401)
            self._send(401, {"error": "unauthorized"})
            return False
        return True

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n)) if n else None
        except ValueError:
            return None

    def do_GET(self):
        if not self._auth():
            return
        p = self.path.split("?")[0]
        log(method="GET", path=p)
        parts = p.strip("/").split("/")
        if p == "/event":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            with LOCK:
                CLIENTS.append(self)
            publish("server.connected", {})
            while True:
                time.sleep(0.5)
                with LOCK:
                    if self not in CLIENTS:
                        return
        elif p == "/path":
            self._send(200, {"home": os.environ.get("HOME"), "worktree": CWD, "directory": CWD})
        elif p == "/config":
            self._send(200, {})
        elif p == "/agent":
            self._send(200, [{"name": "build", "mode": "primary", "permission": agent_rules()},
                             {"name": "plan", "mode": "primary", "permission": agent_rules() + [
                                 {"permission": "edit", "pattern": "*", "action": "deny"}]}])
        elif p == "/permission":
            with LOCK:
                self._send(200, [v["info"] for v in PENDING.values()])
        elif p == "/session/status":
            self._send(200, {k: {"type": "busy"} for k, v in SESSIONS.items() if v["busy"]})
        elif len(parts) == 2 and parts[0] == "session" and parts[1] in SESSIONS:
            self._send(200, {"id": parts[1], "permission": SESSIONS[parts[1]]["permission"], "cost": 0,
                             "tokens": {"input": 6178, "output": 5, "reasoning": 0, "cache": {"read": 1941, "write": 0}}})
        elif p == "/config/providers":
            self._send(200, {"providers": [{"id": "opencode", "name": "OpenCode Zen", "models": {
                "big-pickle": {"id": "big-pickle", "name": "Big Pickle", "limit": {"context": 200000, "output": 32000}},
                "fake-two": {"id": "fake-two", "name": "Fake Two", "limit": {"context": 100000}}}}],
                "default": {"opencode": "big-pickle"}})
        elif p == "/global/health":
            self._send(200, {"healthy": True, "version": "1.18.32-fake"})
        elif len(parts) == 3 and parts[0] == "session" and parts[2] == "message" and parts[1] in SESSIONS:
            self._send(200, SESSIONS[parts[1]]["messages"])
        else:
            self._send(404, {"name": "NotFoundError"})

    def do_POST(self):
        if not self._auth():
            return
        p = self.path.split("?")[0]
        body = self._body()
        log(method="POST", path=p, body=body)
        parts = p.strip("/").split("/")
        if p == "/session":
            sid = nid("ses")
            SESSIONS[sid] = {"permission": list((body or {}).get("permission") or []), "messages": [], "busy": False}
            self._send(200, {"id": sid, "permission": SESSIONS[sid]["permission"]})
        elif len(parts) == 3 and parts[0] == "session" and parts[2] == "prompt_async":
            if parts[1] not in SESSIONS:
                return self._send(404, {"name": "NotFoundError"})
            text = "".join(x.get("text", "") for x in (body or {}).get("parts", []) if isinstance(x, dict))
            threading.Thread(target=run_prompt, args=(parts[1], text), daemon=True).start()
            self._send(204)
        elif len(parts) == 3 and parts[0] == "permission" and parts[2] == "reply":
            r = (body or {}).get("reply")
            if r not in ("once", "always", "reject"):
                return self._send(400, {"error": "bad reply"})
            ok = reply_to(parts[1], r, (body or {}).get("message"))
            self._send(200 if ok else 404, True if ok else {"name": "NotFoundError"})
        elif len(parts) == 4 and parts[0] == "session" and parts[2] == "permissions":
            ok = reply_to(parts[3], (body or {}).get("response"), None)
            self._send(200 if ok else 404, ok)
        elif len(parts) == 3 and parts[0] == "question":
            self._send(200, True)
        elif len(parts) == 3 and parts[0] == "session" and parts[2] == "abort" and parts[1] in SESSIONS:
            SESSIONS[parts[1]]["abort"] = True
            self._send(200, True)
        elif len(parts) == 3 and parts[0] == "session" and parts[2] == "summarize" and parts[1] in SESSIONS:
            sid = parts[1]
            s = SESSIONS[sid]
            publish("session.status", {"sessionID": sid, "status": {"type": "busy"}})
            msg = {"info": {"id": nid("msg"), "role": "assistant", "summary": True, "mode": "compaction",
                            "time": {"created": int(time.time() * 1000)}, "modelID": (body or {}).get("modelID"),
                            "providerID": (body or {}).get("providerID"), "cost": 0,
                            "tokens": {"total": 694, "input": 427, "output": 82, "reasoning": 42, "cache": {"read": 143, "write": 0}}},
                   "parts": []}
            s["messages"].append(msg)
            text_part(sid, msg, "SUMMARY of the conversation (not a reply)")
            time.sleep(0.3)
            publish("session.status", {"sessionID": sid, "status": {"type": "idle"}})
            self._send(200, True)
        else:
            self._send(404, {"name": "NotFoundError"})

    def do_PATCH(self):
        if not self._auth():
            return
        p = self.path.split("?")[0]
        body = self._body()
        log(method="PATCH", path=p, body=body)
        parts = p.strip("/").split("/")
        if len(parts) == 2 and parts[0] == "session" and parts[1] in SESSIONS:
            if isinstance((body or {}).get("permission"), list):
                SESSIONS[parts[1]]["permission"] += body["permission"]
            self._send(200, {"id": parts[1], "permission": SESSIONS[parts[1]]["permission"]})
        else:
            self._send(404, {"name": "NotFoundError"})


def main():
    a = sys.argv[1:]
    if "--version" in a:
        print("1.18.32-fake")
        return
    if a[:1] != ["serve"]:
        sys.exit("usage: fakeopencode.py serve --hostname H --port N")
    if os.environ.get("FAKE_OC_ENV_LOG"):
        with open(os.environ["FAKE_OC_ENV_LOG"], "w") as f:
            json.dump({k: (v if k in ("OPENCODE_CONFIG_CONTENT", "OPENCODE_DISABLE_MODELS_FETCH", "OPENCODE_DISABLE_AUTOUPDATE",
                                      "OPENCODE_DISABLE_SHARE", "OPENCODE_DISABLE_LSP_DOWNLOAD") else "set")
                       for k, v in os.environ.items() if k.startswith("OPENCODE_")}, f)
    port = int(a[a.index("--port") + 1]) if "--port" in a else 4096
    host = a[a.index("--hostname") + 1] if "--hostname" in a else "127.0.0.1"
    for s in json.loads(os.environ.get("FAKE_OC_SESSIONS") or "[]"):     # sessions that "already exist" (resume)
        SESSIONS[s] = {"permission": [], "messages": [], "busy": False}
    srv = ThreadingHTTPServer((host, port), H)
    srv.daemon_threads = True
    print(f"opencode server listening on http://{host}:{port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
