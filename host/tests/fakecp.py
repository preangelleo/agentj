"""A tiny fake control plane (PROTOCOL.md §7) on 127.0.0.1 for host tests. It verifies every envelope the way the real
one must (shape → pk → channel derivation → signature → |ts − now| ≤ 300 → strict schema) and records what it accepted."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

CONTEXTS = {"/v1/host/login": "agentjarvis-host-login-v1", "/v1/host/poll": "agentjarvis-host-poll-v1",
            "/v1/host/report": "agentjarvis-host-report-v1", "/v1/host/sync": "agentjarvis-host-sync-v1",
            "/v1/host/rename": "agentjarvis-host-rename-v1", "/v1/host/seat-bind": "agentjarvis-host-seat-bind-v1",
            "/v1/host/seat-leave": "agentjarvis-host-seat-leave-v1"}
KEYS = {"login": {"v", "t", "channel", "ts"}, "poll": {"v", "t", "channel", "ts", "login_id"},
        "report": {"v", "t", "channel", "ts", "seq", "agent", "devices", "pending"},
        "sync": {"v", "t", "channel", "ts", "results"}, "rename": {"v", "t", "channel", "ts", "name"},
        "seat_bind": {"v", "t", "channel", "ts", "token", "name"}, "seat_leave": {"v", "t", "channel", "ts"}}
OPTIONAL = {"report": {"agent_name", "machine"}}   # A3.2: optional report keys
PATH_OF = {"seat_bind": "/v1/host/seat-bind", "seat_leave": "/v1/host/seat-leave"}   # inner `t` → path where they differ
SEAT_TOKEN = re.compile(r"ajt_[A-Za-z0-9_-]{43}")


def unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def channel_id(pub: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(b"agentjarvis/channel/v1" + pub).digest()[:16]).rstrip(b"=").decode()


class FakeCP:
    def __init__(self, interval: int = 4, bound_after: int = 2):
        self.interval, self.bound_after = interval, bound_after
        self.lock = threading.Lock()
        self.logins: dict[str, dict] = {}
        self.reports: list[dict] = []          # accepted (signature-valid) report bodies, whatever the answer
        self.rejected: list[tuple[str, int]] = []
        self.poll_times: list[float] = []
        self.last_seq = 0
        self.script: list = []                 # scripted answers for /report: "replay" | "unbound" | 500 | ("hang", s)
        self.login_mode = "ok"                 # ok | already_bound
        self.syncs: list[dict] = []            # accepted sync bodies
        self.unbind: list[dict] = []           # pending unbind requests handed out on /sync until a result names them
        self.sync_extra: dict = {}             # extra keys in the sync answer (must be ignored by the host)
        self.sync_times: list[float] = []
        self.unbind_factory = None             # n → fresh requests for every sync (a hostile control plane)
        self.bound_name = None                 # A3.2: agent_name in the bound poll answer (None = absent)
        self.sync_name = None                  # A3.2: agent_name in every sync answer (None = absent; "NULL" = JSON null)
        self.renames: list[dict] = []          # accepted rename bodies
        self.rename_script: list = []          # scripted rename answers: "taken" | "unbound" | "rate" | "bad" | 500 | ("echo", n)
        self.taken = {"wren"}                  # names (lower case) already used in the company
        self.bound_extra = {"devices": [{"id": "EVILEVILEVILEVIL", "pub": "A" * 43, "name": "evil"}],
                            "approve": ["EVILEVILEVILEVIL"], "allowlist": ["x"]}
        # seat setup §4: /v1/host/seat-bind
        self.seat_tokens: set[str] = set()     # issued, unexpired codes (single use: removed when bound)
        self.seat_binds: list[dict] = []       # accepted (signature-valid) seat-bind bodies
        self.seat_script: list = []            # scripted answers: "invalid" | "taken" | "payment" | "rate" | "already_bound"
        #                                        | "bad_name" | "garbled" (200 without a usable body) | int (5xx)
        self.seat_channel_bound: set[str] = set()
        self.seat_bound_token: dict[str, tuple] = {}   # channel → (code, name) that bound it (review SS-02: idempotent replay)
        # review SS-02: /v1/host/seat-leave
        self.seat_leaves: list[dict] = []      # accepted (signature-valid) seat-leave bodies
        self.leave_script: list = []           # scripted answers: "not_found" | "rate" | int (5xx)
        cp = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_POST(self):
                n = int(self.headers.get("content-length", 0))
                raw = self.rfile.read(n)
                ctx = CONTEXTS.get(self.path)
                if not ctx:
                    return self._send(404, {"error": "not_found"})
                inner, err = cp.verify(ctx, raw, self.headers.get("content-type", ""))
                if err:
                    cp.rejected.append((self.path, err[0]))
                    return self._send(*err)
                code, obj = cp.handle(self.path, inner)
                if code == "hang":
                    time.sleep(obj)
                    code, obj = 200, {"ok": True}
                self._send(code, obj)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()

    # ------------------------------------------------------------ §7 checks
    def verify(self, ctx: str, raw: bytes, ctype: str):
        if "application/json" not in ctype or len(raw) > 32 * 1024:
            return None, (400, {"error": "bad_request"})
        try:
            env = json.loads(raw)
            assert isinstance(env, dict) and set(env) == {"pk", "body", "sig"}
            pk, body, sig = unb64u(env["pk"]), env["body"], unb64u(env["sig"])
        except Exception:
            return None, (400, {"error": "bad_request"})
        if len(pk) != 32:
            return None, (401, {"error": "bad_signature"})
        try:
            Ed25519PublicKey.from_public_bytes(pk).verify(sig, f"{ctx}\n{body}".encode("ascii"))
            inner = json.loads(unb64u(body).decode("utf-8"))
        except (InvalidSignature, ValueError, UnicodeDecodeError):
            return None, (401, {"error": "bad_signature"})
        if not isinstance(inner, dict) or inner.get("channel") != channel_id(pk):
            return None, (401, {"error": "bad_signature"})
        if abs(inner.get("ts", 0) - time.time()) > 300:
            return None, (401, {"error": "stale"})
        t = inner.get("t")
        extra = set(inner) - KEYS.get(t, set())
        if (inner.get("v") != 1 or t not in KEYS or not KEYS[t] <= set(inner) or not extra <= OPTIONAL.get(t, set())
                or CONTEXTS[PATH_OF.get(t, f"/v1/host/{t}")] != ctx):
            return None, (400, {"error": "bad_request"})
        return inner, None

    def handle(self, path: str, inner: dict):
        with self.lock:
            if path == "/v1/host/login":
                if self.login_mode == "already_bound":
                    return 409, {"error": "already_bound"}
                lid = base64.urlsafe_b64encode(b"L" * 16).rstrip(b"=").decode()
                self.logins[lid] = {"polls": 0, "last": 0.0, "channel": inner["channel"]}
                return 201, {"login_id": lid, "user_code": "BCDF-2345", "verification_uri": self.url + "/link",
                             "expires_in": 600, "interval": self.interval, "devices": ["ignored"]}
            if path == "/v1/host/poll":
                lg = self.logins.get(inner["login_id"])
                if not lg or lg["channel"] != inner["channel"]:
                    return 404, {"error": "not_found"}
                now = time.monotonic()
                self.poll_times.append(now)
                if lg["last"] and now - lg["last"] < 4:
                    lg["last"] = now
                    return 429, {"error": "slow_down"}
                lg["last"] = now
                lg["polls"] += 1
                if lg["polls"] < self.bound_after:
                    return 200, {"status": "pending"}
                return 200, {"status": "bound", "host_id": "h_1", "tenant": {"slug": "acme-co", "name": "Acme\x1b[2J Co"},
                             **self.bound_extra, **({"agent_name": self.bound_name} if self.bound_name is not None else {})}
            if path == "/v1/host/seat-bind":
                self.seat_binds.append(inner)
                if not (isinstance(inner["token"], str) and SEAT_TOKEN.fullmatch(inner["token"])):
                    return 400, {"error": "bad_request"}
                step = self.seat_script.pop(0) if self.seat_script else None
                answers = {"invalid": (404, {"error": "invalid_setup"}), "payment": (402, {"error": "payment_required"}),
                           "rate": (429, {"error": "rate_limited"}), "already_bound": (409, {"error": "already_bound"}),
                           "bad_name": (400, {"error": "bad_name"}), "garbled": (200, {"status": "bound", "host_id": "!"})}
                if step in answers:
                    return answers[step]
                if isinstance(step, int):
                    return step, {"error": "internal"}
                if inner["channel"] in self.seat_channel_bound:
                    prev = self.seat_bound_token.get(inner["channel"])
                    if prev and prev[0] == inner["token"]:      # the same code from the same key: replay of the 200
                        return 200, {"status": "bound", "host_id": "h_seat", "tenant": {"slug": "acme-co", "name": "Acme\x1b[2J Co"},
                                     "agent_name": prev[1]}
                    return 409, {"error": "already_bound"}
                if inner["token"] not in self.seat_tokens:
                    return 404, {"error": "invalid_setup"}
                if step == "taken" or inner["name"].lower() in self.taken:
                    base = inner["name"]
                    return 409, {"error": "name_taken", "suggestions": [f"{base} 2", "bad‮name", f"{base} 3", 7]}
                self.seat_tokens.discard(inner["token"])
                self.seat_channel_bound.add(inner["channel"])
                self.seat_bound_token[inner["channel"]] = (inner["token"], inner["name"])
                self.taken.add(inner["name"].lower())
                return 200, {"status": "bound", "host_id": "h_seat", "tenant": {"slug": "acme-co", "name": "Acme\x1b[2J Co"},
                             "agent_name": inner["name"], **self.bound_extra}
            if path == "/v1/host/seat-leave":
                self.seat_leaves.append(inner)
                step = self.leave_script.pop(0) if self.leave_script else None
                if step == "rate":
                    return 429, {"error": "rate_limited"}
                if isinstance(step, int):
                    return step, {"error": "internal"}
                if step == "not_found" or inner["channel"] not in self.seat_channel_bound:
                    return 404, {"error": "not_found"}
                self.seat_channel_bound.discard(inner["channel"])
                self.seat_bound_token.pop(inner["channel"], None)
                return 200, {"status": "left"}
            if path == "/v1/host/rename":
                self.renames.append(inner)
                step = self.rename_script.pop(0) if self.rename_script else None
                if step == "unbound":
                    return 403, {"error": "not_bound"}
                if step == "rate":
                    return 429, {"error": "rate_limited"}
                if step == "bad":
                    return 400, {"error": "bad_name"}
                if isinstance(step, int):
                    return step, {"error": "internal"}
                if isinstance(step, tuple) and step[0] == "echo":
                    return 200, {"ok": True, "name": step[1]}
                if step == "taken" or inner["name"].lower() in self.taken:
                    base = inner["name"]
                    return 409, {"error": "name_taken", "suggestions": [f"{base} 2", f"{base} 3", "bad\u202ename", f"{base} 4", 7]}
                self.taken.add(inner["name"].lower())
                return 200, {"ok": True, "name": inner["name"]}
            if path == "/v1/host/sync":
                self.syncs.append(inner)
                self.sync_times.append(time.monotonic())
                if self.unbind_factory:
                    return 200, {"unbind": self.unbind_factory(len(self.syncs))}
                done = {x["id"] for x in inner["results"]}
                self.unbind = [u for u in self.unbind if u["id"] not in done]
                name = {} if self.sync_name is None else {"agent_name": None if self.sync_name == "NULL" else self.sync_name}
                return 200, {"unbind": list(self.unbind), **self.sync_extra, **name}
            # report
            self.reports.append(inner)
            step = self.script.pop(0) if self.script else None
            if step == "unbound":
                return 403, {"error": "not_bound"}
            if step == "replay":
                return 409, {"error": "replay"}
            if isinstance(step, int):
                return step, {"error": "internal"}
            if isinstance(step, tuple) and step[0] == "hang":
                return "hang", step[1]
            if inner["seq"] <= self.last_seq:
                return 409, {"error": "replay"}
            self.last_seq = inner["seq"]
            return 200, {"ok": True, "approve": ["EVILEVILEVILEVIL"]}
