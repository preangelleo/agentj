#!/usr/bin/env python3
"""A stand-in for OpenCode v2's `opencode serve` (2.0.23's /api protocol, the parts agentj uses; P60): no model, deterministic.

    fakeopencode2.py serve --hostname 127.0.0.1 --port N      (prints "server listening on http://127.0.0.1:N")
    fakeopencode2.py --version                                 ("opencode v2.0.23")

Like the real 2.0.23 (measured, reports/qa/p60/): HTTP Basic auth (OPENCODE_SERVER_PASSWORD), JSON answers wrapped as
{"data": …}, and every legacy v1 route (/session, /event, /provider …) answers 200 with an HTML page; GET /api/agent is empty
on the first call (the agent list loads lazily). Events on GET /api/event are {type, data}.

What a prompt does (its text):
    RUN: <cmd>     permission.asked (action shell); "once" → runs it with /bin/sh, text = its output; reject → "REJECTED: <msg>"
    SLEEP: <s>     works for s seconds; POST …/interrupt ends it (session.execution.interrupted)
    NOKEY          session.execution.failed {type: provider.no-route, message: "Model unavailable: <provider>/<model>"}
    BADKEY         session.step.failed + session.execution.failed {type: provider.auth, status: 401}
    WHOAMI         "RUN-ID: <random per process>"
    anything else  "ECHO: <text>"
FAKE_OC_LOG = JSONL of every request (method, path, body); FAKE_OC2_CONNECTED = comma list of integrations with a connection
(default "opencode"); FAKE_OC2_CONFIG_PROVIDERS = comma list of providers declared in the config.
"""
import base64
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOCK = threading.RLock()
SESSIONS: dict = {}
PENDING: dict = {}
CLIENTS: list = []
N = [0]
RUN = os.urandom(4).hex()
AGENT_CALLS = [0]
HTML = b"<!doctype html><html><body>opencode v2 web</body></html>"


def nid(prefix):
    with LOCK:
        N[0] += 1
        return f"{prefix}_{RUN}{N[0]:06d}"


def log(method, path, body):
    p = os.environ.get("FAKE_OC_LOG")
    if p:
        with LOCK, open(p, "a") as f:
            f.write(json.dumps({"m": method, "p": path, "b": body}) + "\n")


def emit(t, data):
    line = ("data: " + json.dumps({"id": nid("evt"), "created": int(time.time() * 1000), "type": t, "data": data}) + "\n\n").encode()
    with LOCK:
        for w in list(CLIENTS):
            try:
                w.write(b"%x\r\n" % len(line) + line + b"\r\n")
                w.flush()
            except OSError:
                CLIENTS.remove(w)


def text(sid, s):
    mid = nid("msg")
    SESSIONS[sid]["messages"].insert(0, {"id": mid, "type": "assistant", "time": {"created": int(time.time() * 1000)},
                                         "agent": "build", "model": SESSIONS[sid].get("model") or {},
                                         "content": [{"type": "text", "text": s}],
                                         "tokens": {"input": 100, "output": 5, "reasoning": 0, "cache": {"read": 0, "write": 0}}})
    emit("session.step.started", {"sessionID": sid, "assistantMessageID": mid})
    emit("session.text.ended", {"sessionID": sid, "assistantMessageID": mid, "ordinal": 0, "text": s})
    emit("session.step.ended", {"sessionID": sid, "assistantMessageID": mid, "finish": "stop", "cost": 0.0001,
                                "tokens": {"input": 100, "output": 5, "reasoning": 0, "cache": {"read": 0, "write": 0}}})


def finish(sid, kind="succeeded", **extra):
    s = SESSIONS[sid]
    s["busy"] = False
    s["time"]["idle"] = int(time.time() * 1000)
    emit("session.execution." + kind, {"sessionID": sid, **extra})


def work(sid, prompt):
    s = SESSIONS[sid]
    emit("session.inbox.delivered", {"sessionID": sid})
    emit("session.execution.started", {"sessionID": sid})
    if prompt.startswith("RUN: "):
        cmd = prompt[5:]
        rid = nid("per")
        ev = threading.Event()
        PENDING[rid] = {"sid": sid, "ev": ev, "reply": None, "message": None,
                        "info": {"id": rid, "sessionID": sid, "action": "shell", "resources": [cmd], "save": [cmd.split()[0] + " *"]}}
        emit("permission.asked", PENDING[rid]["info"])
        ev.wait(120)
        p = PENDING.pop(rid)
        if p["reply"] == "once":
            out = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, text=True, timeout=60).stdout.strip()
            text(sid, "OUT: " + out)
        else:
            text(sid, "REJECTED: " + (p["message"] or ""))
        finish(sid)
    elif prompt.startswith("SLEEP: "):
        end = time.time() + float(prompt[7:])
        while time.time() < end and not s.get("interrupt"):
            time.sleep(0.05)
        if s.pop("interrupt", False):
            finish(sid, "interrupted", reason="user")
        else:
            text(sid, "SLEPT")
            finish(sid)
    elif prompt == "NOKEY":
        m = s.get("model") or {}
        finish(sid, "failed", error={"type": "provider.no-route",
                                     "message": f"Model unavailable: {m.get('providerID')}/{m.get('id')}"})
    elif prompt == "BADKEY":
        err = {"type": "provider.auth", "message": "User not found.", "status": 401, "response": {"body": "{}"}}
        emit("session.step.failed", {"sessionID": sid, "assistantMessageID": nid("msg"), "error": err})
        finish(sid, "failed", error=err)
    elif prompt == "WHOAMI":
        text(sid, "RUN-ID: " + RUN)
        finish(sid)
    else:
        text(sid, "ECHO: " + prompt)
        finish(sid)


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _auth(self):
        want = "Basic " + base64.b64encode(f"{os.environ.get('OPENCODE_SERVER_USERNAME', 'opencode')}:"
                                           f"{os.environ.get('OPENCODE_SERVER_PASSWORD', '')}".encode()).decode()
        if self.headers.get("Authorization") != want:
            self._send(401, {"_tag": "UnauthorizedError", "message": "unauthorized"})
            return False
        return True

    def _send(self, code, obj=None, raw=None, ctype="application/json"):
        b = raw if raw is not None else (b"" if obj is None else json.dumps(obj).encode())
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n)) if n else None
        except ValueError:
            return None

    def _route(self, method):
        body = self._body() if method in ("POST", "PATCH", "PUT") else None
        if not self._auth():
            return
        path = self.path.split("?")[0]
        log(method, path, body)
        if not path.startswith("/api/") and path != "/openapi.json":
            return self._send(200, raw=HTML, ctype="text/html")            # what 2.0.23 does for the v1 routes
        parts = path.split("/")[2:]
        if method == "GET" and path == "/api/event":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            with LOCK:
                CLIENTS.append(self.wfile)
            first = b'data: {"type":"server.connected","data":{}}\n\n'
            self.wfile.write(b"%x\r\n" % len(first) + first + b"\r\n")
            self.wfile.flush()
            while self.wfile in CLIENTS:
                time.sleep(0.2)
            return
        if method == "GET" and path == "/api/info":
            return self._send(200, {"version": "2.0.23", "pid": os.getpid(), "urls": [], "paths": {"tmp": "/tmp/opencode"}})
        if method == "GET" and path == "/api/agent":
            AGENT_CALLS[0] += 1
            data = [] if AGENT_CALLS[0] == 1 else [
                {"id": "build", "name": "build", "mode": "primary", "permissions": [
                    {"action": "*", "resource": "*", "effect": "allow"}, {"action": "read", "resource": "*.env", "effect": "ask"},
                    {"action": "shell", "resource": "rm *", "effect": "deny"}]}]
            return self._send(200, {"location": {"directory": os.getcwd()}, "data": data})
        if method == "GET" and path == "/api/integration":
            conn = [x for x in os.environ.get("FAKE_OC2_CONNECTED", "opencode").split(",") if x]
            ids = sorted(set(conn) | {"openrouter", "deepseek", "opencode"})
            return self._send(200, {"data": [{"id": i, "name": i, "methods": [], "connections": [{"type": "env"}] if i in conn else []}
                                             for i in ids]})
        if method == "GET" and path == "/api/config":
            cp = [x for x in os.environ.get("FAKE_OC2_CONFIG_PROVIDERS", "").split(",") if x]
            return self._send(200, {"data": {"provider": {p: {} for p in cp}}})
        if method == "GET" and path == "/api/model":
            return self._send(200, {"data": [{"id": "chat", "providerID": p, "name": f"{p} chat", "enabled": True,
                                              "limit": {"context": 128000}} for p in ("deepseek", "openrouter", "opencode")]})
        if path == "/api/session" and method == "POST":
            sid = nid("ses")
            SESSIONS[sid] = {"id": sid, "model": (body or {}).get("model"), "permissions": (body or {}).get("permissions"),
                             "messages": [], "busy": False, "time": {"created": int(time.time() * 1000)}, "cost": 0,
                             "tokens": {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
                             "instructions": {}}
            return self._send(200, {"data": self._info(sid)})
        if parts[:1] == ["session"] and len(parts) >= 2:
            sid = parts[1]
            s = SESSIONS.get(sid)
            if s is None:
                return self._send(404, {"_tag": "SessionNotFoundError", "sessionID": sid, "message": "not found"})
            rest = parts[2:]
            if not rest and method == "GET":
                return self._send(200, {"data": self._info(sid)})
            if not rest and method == "PATCH":
                if "permissions" in (body or {}):
                    s["permissions"] = body["permissions"]
                return self._send(200, {"data": self._info(sid)})
            if rest == ["prompt"] and method == "POST":
                if not isinstance((body or {}).get("text"), str):
                    return self._send(400, {"_tag": "InvalidRequestError", "message": "text"})
                if s["busy"]:
                    return self._send(409, {"_tag": "ConflictError", "message": "busy"})
                s["busy"] = True
                iid = nid("msg")
                s["messages"].insert(0, {"id": iid, "type": "user", "text": body["text"], "time": {"created": int(time.time() * 1000)}})
                # like 2.0.23 (measured, P64): every prompt — the phone's or one typed on the desktop — is an inbox event first
                emit("session.inbox.enqueued", {"inboxID": iid, "sessionID": sid,
                                                "item": {"type": "user", "payload": {"text": body["text"]}, "delivery": "steer"}})
                threading.Thread(target=work, args=(sid, body["text"]), daemon=True).start()
                return self._send(200, {"data": {"id": iid, "sessionID": sid, "type": "user"}})
            if rest == ["interrupt"] and method == "POST":
                s["interrupt"] = True
                return self._send(200, {"interrupted": True})
            if rest == ["model"] and method == "POST":
                s["model"] = (body or {}).get("model")
                return self._send(204)
            if rest == ["compact"] and method == "POST":
                def compact():
                    emit("session.execution.started", {"sessionID": sid})
                    text(sid, "SUMMARY (not a reply)")
                    finish(sid)
                threading.Thread(target=compact, daemon=True).start()
                return self._send(200, {"data": {"id": nid("msg"), "sessionID": sid, "type": "compaction"}})
            if rest == ["message"] and method == "GET":
                return self._send(200, {"data": s["messages"]})
            if rest == ["permission"] and method == "GET":
                return self._send(200, {"data": [p["info"] for p in PENDING.values() if p["sid"] == sid]})
            if len(rest) == 3 and rest[0] == "permission" and rest[2] == "reply" and method == "POST":
                p = PENDING.get(rest[1])
                if not p:
                    return self._send(404, {"_tag": "PermissionNotFoundError", "requestID": rest[1], "message": "gone"})
                p["reply"], p["message"] = (body or {}).get("decision"), (body or {}).get("message")
                emit("permission.replied", {"sessionID": sid, "requestID": rest[1], "reply": p["reply"]})
                p["ev"].set()
                return self._send(204)
        if parts[:3] == ["experimental", "session", parts[2] if len(parts) > 2 else ""] and len(parts) == 6 \
                and parts[3:5] == ["instructions", "entries"] and method == "PUT":
            s = SESSIONS.get(parts[2])
            if s is None:
                return self._send(404, {"_tag": "SessionNotFoundError", "message": "not found"})
            s["instructions"][parts[5]] = (body or {}).get("value")
            return self._send(204)
        return self._send(404, {"_tag": "NotFound", "message": path})

    def _info(self, sid):
        s = SESSIONS[sid]
        return {"id": sid, "projectID": "p", "model": s["model"], "permissions": s["permissions"], "cost": s["cost"],
                "tokens": s["tokens"], "time": s["time"], "title": "Agent J", "location": {"directory": os.getcwd()}}

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PATCH(self):
        self._route("PATCH")

    def do_PUT(self):
        self._route("PUT")

    def do_DELETE(self):
        self._route("DELETE")


def main():
    a = sys.argv[1:]
    if a[:1] in (["--version"], ["-v"]):
        print("opencode v2.0.23")
        return
    if a[:1] != ["serve"]:
        sys.exit(2)
    port = int(a[a.index("--port") + 1])
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    srv.daemon_threads = True
    print(f"server listening on http://127.0.0.1:{port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
