"""`agentj admin`: the host-local Agent admin page (A3.2 contract §4, hardened after review A32-01/05/08).

Threat model in one paragraph: the page can approve a remote, so it is exactly as trusted as `agentj pair` (ARCHITECTURE G-A8)
and no more reachable than this machine's loopback interface. It binds 127.0.0.1 only (there is no option to change that).
Admission: a one-time 10-minute token printed on this terminal in the URL *fragment* (`/#t=`, never sent in a request line);
the page POSTs it once to /api/session and gets a session secret that lives only in that tab's sessionStorage (origin-scoped,
port included) and travels as `Authorization: Bearer` — there is no cookie, so nothing is attached to requests for other
localhost ports. Sessions: 30 min idle, 8 h absolute, at most 4, all revoked when a fresh link is printed. Anything without a
valid session — and any foreign Host header (DNS rebinding) — gets a bare 404; static assets carry no data and need no session.
A write also needs our own Origin and a JSON body ≤ 4 KiB. At most 16 requests are handled at once (more are closed at once)
and headers + body must arrive within 10 s. Approval still needs the human to type the phone's 6-digit code *and* the
approval passphrase (L2, gate.py — so holding the link or a tab session is not enough): the page is a control-socket client
of `agentj serve` speaking the very protocol `agentj pair` speaks. Stdlib only; segno renders the QR.
"""
from __future__ import annotations

import hmac
import json
import pathlib
import re
import secrets
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import cloud, gate, names
from .state import MAX_DEVICES
from .text import clean_label, machine_name

BIND = "127.0.0.1"                 # never configurable
TOKEN_TTL = 600                    # one-time admission token (s)
SESSION_IDLE = 30 * 60             # a session unused this long is gone (s); sessions live in memory only
SESSION_MAX_AGE = 8 * 3600         # absolute session lifetime (s)
MAX_SESSIONS = 4                   # the oldest is dropped when a 5th logs in
MAX_HANDLERS = 16                  # concurrent requests; more are closed immediately
REQUEST_DEADLINE = 10              # headers + body must have arrived within this (s), however slowly they drip
MAX_BODY = 4096
PAIR_WAIT = 8                      # how long a POST waits for serve's answer to a code / unbind (s)
ASSET_DIR = pathlib.Path(__file__).resolve().parent / "admin"
# Static files carry no data and need no session. The brand files under admin/brand/ are a byte copy of the shared brand
# kit (its sync script with `--set admin` writes them; a test runs it with --check). Only
# these types are ever served, from a map built once at import — a request path is looked up, never joined onto a directory.
BRAND_TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".woff2": "font/woff2",
               ".png": "image/png", ".webp": "image/webp"}
LANGS = ("zh", "en")


def _assets() -> dict[str, tuple[str, str]]:
    a = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
         "/app.css": ("app.css", "text/css; charset=utf-8"), "/favicon.ico": ("favicon.ico", "image/x-icon"),
         "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png")}
    for lang in LANGS:     # the built dictionaries only (never the *.zh.src.json source)
        a[f"/i18n/admin.{lang}.json"] = (f"i18n/admin.{lang}.json", "application/json; charset=utf-8")
    brand = ASSET_DIR / "brand"
    for f in sorted(brand.rglob("*")) if brand.is_dir() else ():
        if f.is_file() and f.suffix in BRAND_TYPES:
            rel = f.relative_to(ASSET_DIR).as_posix()
            a["/" + rel] = (rel, BRAND_TYPES[f.suffix])
    return a


ASSETS = _assets()
_LANG_QUERY = re.compile(r"lang=(?:zh|en)")   # brand/lang.js keeps ?lang= in the address bar when storage is blocked
SECURITY_HEADERS = (
    ("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                                "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
                                "form-action 'none'"),
    ("X-Frame-Options", "DENY"), ("Cache-Control", "no-store"), ("Referrer-Policy", "no-referrer"),
    ("X-Content-Type-Options", "nosniff"), ("Cross-Origin-Resource-Policy", "same-origin"),
)
_DEVICE_ID = re.compile(r"[A-Za-z0-9_-]{16}")
_REVOKE = re.compile(r"/api/devices/([A-Za-z0-9_-]{16})/revoke")
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
_BEARER = re.compile(r"Bearer ([A-Za-z0-9_-]{43})")
DENY_REASONS = {"code_mismatch", "denied", "timeout", "abandoned", "bad_handshake", "hs_timeout", "device_limit",
                "no_pending_device", "gone", "replaced", "serve_gone", "relay_down", "cancelled", "pass_locked", "pass_not_set"}


# ------------------------------------------------------------------ admission: one-time token (fragment) → bearer session
class Auth:
    def __init__(self):
        self.lock = threading.Lock()
        self.token: tuple[str, float] | None = None       # (value, monotonic deadline); only the newest link is valid
        self.sessions: dict[str, list[float]] = {}         # secret → [created, last used] (monotonic)

    def new_token(self) -> str:
        """A fresh link: the previous link stops working and every open session is logged out (review A32-01)."""
        t = secrets.token_urlsafe(32)
        with self.lock:
            self.token = (t, time.monotonic() + TOKEN_TTL)
            self.sessions.clear()
        return t

    def _prune(self, now: float) -> None:
        self.sessions = {k: v for k, v in self.sessions.items()
                         if now - v[0] < SESSION_MAX_AGE and now - v[1] < SESSION_IDLE}

    def redeem(self, t) -> str | None:
        """Single use: the first presentation consumes the token (valid or expired). → a new session secret, or None."""
        with self.lock:
            cur = self.token
            if not (cur and isinstance(t, str) and _TOKEN.fullmatch(t) and hmac.compare_digest(t, cur[0])):
                return None
            self.token = None
            now = time.monotonic()
            if now > cur[1]:
                return None
            self._prune(now)
            while len(self.sessions) >= MAX_SESSIONS:
                self.sessions.pop(min(self.sessions, key=lambda k: self.sessions[k][0]))
            sid = secrets.token_urlsafe(32)
            self.sessions[sid] = [now, now]
            return sid

    def check(self, sid: str | None) -> bool:
        if not sid:
            return False
        with self.lock:
            now = time.monotonic()
            self._prune(now)
            for k, v in self.sessions.items():
                if hmac.compare_digest(sid, k):
                    v[1] = now
                    return True
            return False

    def end(self, sid: str) -> None:
        with self.lock:
            self.sessions = {k: v for k, v in self.sessions.items() if not hmac.compare_digest(sid, k)}


# ------------------------------------------------------------------ one pairing conversation with serve (= `agentj pair`)
class PairError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Pairing:
    """Holds the control-socket stream of one `{"cmd":"pair"}` and mirrors serve's events into a view. Sends only what
    `agentj pair` sends: a code the human typed, an unbind the human picked. Closing it = abandoned (serve denies)."""

    def __init__(self, st):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(5)
        try:
            self.sock.connect(str(st.sock_path))
        except (FileNotFoundError, ConnectionRefusedError):
            self.sock.close()
            raise PairError("serve_not_running") from None
        except OSError:
            self.sock.close()
            raise PairError("serve_busy") from None
        self.rfile = self.sock.makefile("rb")
        self.cond = threading.Condition()
        self.seq, self.last_ev, self.closed = 0, "", False
        self.phase, self.reason, self.device, self.deadline = "waiting", None, None, None
        self.makes_room: dict | None = None   # 0.15.1: {"kind": "evict"|"replaces", "device": {id, name, paired_at, online}}
        self.pass_wrong: int | None = None   # tries left after a wrong approval passphrase (the device keeps waiting)
        try:
            self.sock.sendall(b'{"cmd":"pair"}\n')
            first = json.loads(self.rfile.readline() or b"{}")
        except (OSError, ValueError):
            self.close()
            raise PairError("serve_busy") from None
        if first.get("ev") != "link" or not isinstance(first.get("link"), str):
            self.close()
            reason = first.get("reason")
            raise PairError(reason if reason in DENY_REASONS else "pair_failed")
        self.link, self.expires = first["link"], int(first.get("expires") or 0)
        import segno
        self.qr_svg = segno.make(self.link, error="m").svg_data_uri(scale=6, border=2, dark="#141414", light="#faf9f5")
        self.sock.settimeout(None)
        threading.Thread(target=self._reader, daemon=True, name="agentj-admin-pair").start()

    def _reader(self) -> None:
        while True:
            try:
                line = self.rfile.readline()
            except (OSError, ValueError):
                line = b""
            with self.cond:
                if not line:
                    self.closed = True
                    if self.phase in ("waiting", "pending", "full"):
                        self.phase, self.reason = "denied", "serve_gone"
                    self.seq += 1
                    self.cond.notify_all()
                    return
                try:
                    ev = json.loads(line)
                except ValueError:
                    ev = {}
                if isinstance(ev, dict):
                    self._apply(ev)
                self.seq += 1
                self.cond.notify_all()

    def _apply(self, ev: dict) -> None:
        kind = ev.get("ev")
        self.last_ev = kind if isinstance(kind, str) else ""
        if kind == "pass_wrong":
            left = ev.get("left")
            self.pass_wrong = left if isinstance(left, int) and not isinstance(left, bool) else 0
            return
        if kind == "pending":
            self.phase = "full" if ev.get("full") else "pending"   # "full" = a serve before 0.15.1 (unbind first)
            self.device = {"id": str(ev.get("device", ""))[:16], "name": clean_label(str(ev.get("name", "")))}
            self.makes_room = _room("replaces", ev.get("replaces")) or _room("evict", ev.get("evict"))
            d = ev.get("deadline_in")
            self.deadline = time.monotonic() + (d if isinstance(d, (int, float)) and not isinstance(d, bool) else 0)
        elif kind == "approved":
            self.phase, self.reason = "approved", None
            self.device = {"id": str(ev.get("device", ""))[:16], "name": clean_label(str(ev.get("name", "")))}
            self.makes_room = _room("replaced", ev.get("replaced")) or _room("evicted", ev.get("evicted"))
        elif kind == "expired":
            self.phase, self.reason = "expired", None
        elif kind in ("denied", "gone", "replaced", "error"):
            r = ev.get("reason") if kind in ("denied", "error") else kind
            self.phase, self.reason = "denied", (r if r in DENY_REASONS else "denied")
        # "unbound" only acknowledges an unbind; serve re-sends "pending" right after it

    def send(self, obj: dict) -> None:
        self.sock.sendall((json.dumps(obj) + "\n").encode())

    def wait_for(self, pred, timeout: float = PAIR_WAIT) -> None:
        with self.cond:
            self.cond.wait_for(lambda: pred() or self.closed, timeout)

    def close(self) -> None:
        for f in (lambda: self.sock.shutdown(socket.SHUT_RDWR), self.sock.close):
            try:
                f()
            except OSError:
                pass

    def view(self) -> dict:
        with self.cond:
            v: dict = {"phase": self.phase}
            if self.phase == "waiting":
                v.update(qr_svg=self.qr_svg, link=self.link, expires=self.expires)
            if self.device and self.phase in ("pending", "full", "approved"):
                v["device"] = dict(self.device)
            if self.phase in ("pending", "full") and self.deadline is not None:
                v["deadline_in"] = max(0, int(self.deadline - time.monotonic()))
            if self.phase == "denied":
                v["reason"] = self.reason
            if self.phase == "pending" and self.pass_wrong is not None:
                v["pass_wrong"] = self.pass_wrong
            if self.makes_room and self.phase in ("pending", "approved"):
                v["makes_room"] = {"kind": self.makes_room["kind"], "device": dict(self.makes_room["device"])}
            return v


def _room(kind: str, d) -> dict | None:
    """A remote serve says approving replaces / replaced (0.15.1), cleaned for the page: id, one-line name, time, online."""
    if not isinstance(d, dict) or not (isinstance(d.get("id"), str) and _DEVICE_ID.fullmatch(d["id"])):
        return None
    pa = d.get("paired_at")
    return {"kind": kind, "device": {"id": d["id"], "name": clean_label(str(d.get("name", ""))),
                                     "paired_at": int(pa) if isinstance(pa, (int, float)) and not isinstance(pa, bool) else 0,
                                     "online": d.get("online") is True}}


# ------------------------------------------------------------------ the page's actions
class Admin:
    def __init__(self, st):
        self.st = st
        self.lock = threading.Lock()       # one pairing at a time; actions on it are serialised
        self.pairing: Pairing | None = None

    def serve_status(self) -> dict | None:
        try:
            res = names.ctl_call(self.st, {"cmd": "status"}, timeout=2)
        except names.ServeBusy:
            return {"ok": True, "relay_up": False, "sessions": [], "busy": True}   # there, but not answering
        return res if isinstance(res, dict) and res.get("ok") else None

    def state(self) -> dict:
        status = self.serve_status()
        online = {s.get("device") for s in (status or {}).get("sessions", []) if s.get("state") == "ready"}
        devs = [{"id": d, "name": clean_label(str(v.get("name", ""))), "online": d in online,
                 "paired_at": int(v.get("paired_at")) if isinstance(v.get("paired_at"), (int, float)) else 0}
                for d, v in self.st.devices().items() if isinstance(d, str) and _DEVICE_ID.fullmatch(d) and isinstance(v, dict)]
        devs.sort(key=lambda d: d["paired_at"])
        link = cloud.read_cloud(self.st)
        with self.lock:
            pairing = self.pairing.view() if self.pairing else None
        return {"agent_name": self.st.agent_name(), "machine": machine_name(), "channel": self.st.config()["channel"],
                "version": cloud.VERSION, "serve": {"running": status is not None, "relay_up": bool(status and status.get("relay_up"))},
                "dashboard": {"linked": link is not None, "tenant": link["tenant"] if link else None},
                "remote_unbind": self.st.remote_unbind(), "limit": MAX_DEVICES, "devices": devs, "pairing": pairing,
                "passphrase_set": gate.is_set(self.st)}

    def rename(self, body: dict) -> tuple[int, dict]:
        res = names.rename(self.st, body.get("name"))
        if res["ok"]:
            return 200, {"ok": True, "name": res["name"]}
        code = {"name_required": 400, "bad_name": 400, "name_taken": 409, "not_bound": 409, "rate_limited": 429}.get(res["error"], 502)
        return code, {"ok": False, "error": res["error"], "message": names.RENAME_MESSAGES.get(res["error"], ""),
                      "suggestions": res["suggestions"]}

    def pair_start(self) -> tuple[int, dict]:
        with self.lock:
            if self.pairing:
                self.pairing.close()
                self.pairing = None
            try:
                self.pairing = Pairing(self.st)
            except PairError as e:
                return 409, {"ok": False, "error": e.reason}
            self.st.log("admin_pair_start")
            return 200, {"ok": True, "pairing": self.pairing.view()}

    def pair_code(self, body: dict) -> tuple[int, dict]:
        code, pw = body.get("code"), body.get("passphrase")
        if not isinstance(code, str) or len(code) > 32:
            return 400, {"ok": False, "error": "bad_code"}
        with self.lock:
            p = self.pairing
            if not p or p.phase != "pending":
                return 409, {"ok": False, "error": "unbind_first" if p and p.phase == "full" else "no_pending_device"}
            if code.strip() and (not isinstance(pw, str) or not pw or len(pw) > gate.MAX_LEN):
                return 400, {"ok": False, "error": "passphrase_required"}
            seq = p.seq
            p.pass_wrong = None
            try:   # serve checks the passphrase, then compares the code with the phone's (passkey entry) — one try
                p.send({"cmd": "code", "code": code, **({"pass": pw} if code.strip() else {})})
            except OSError:
                return 409, {"ok": False, "error": "serve_gone"}
            p.wait_for(lambda: p.seq != seq and (p.phase in ("approved", "denied", "expired") or p.pass_wrong is not None),
                       timeout=PAIR_WAIT + 2)
            return 200, {"ok": True, "pairing": p.view()}

    def pair_unbind(self, body: dict) -> tuple[int, dict]:
        did = body.get("device")
        if not (isinstance(did, str) and _DEVICE_ID.fullmatch(did)):
            return 400, {"ok": False, "error": "bad_device"}
        with self.lock:
            p = self.pairing
            if not p or p.phase != "full":
                return 409, {"ok": False, "error": "not_full"}
            if did not in self.st.devices() or (p.device and did == p.device.get("id")):
                return 409, {"ok": False, "error": "unknown_device"}
            seq = p.seq
            try:
                p.send({"cmd": "unbind", "device": did})
            except OSError:
                return 409, {"ok": False, "error": "serve_gone"}
            p.wait_for(lambda: p.seq != seq and p.last_ev != "unbound")
            return 200, {"ok": did not in self.st.devices(), "pairing": p.view()}

    def pair_cancel(self) -> tuple[int, dict]:
        with self.lock:
            if self.pairing:
                self.pairing.close()        # serve: abandoned → the waiting device is denied and closed
                self.pairing = None
        return 200, {"ok": True}

    def revoke(self, did: str) -> tuple[int, dict]:
        try:
            res = names.ctl_call(self.st, {"cmd": "revoke", "device": did})
        except names.ServeBusy:   # serve is there but silent: never edit behind its back (review A32-02)
            return 503, {"ok": False, "error": "serve_busy"}
        if res is None:   # serve certainly not running: edit the allowlist directly (under its lock), like `agentj revoke`
            ok = self.st.remove_device(did)
            if ok:
                self.st.log("revoked", device=did)
            res = {"ok": ok}
        return (200, {"ok": True}) if res.get("ok") else (409, {"ok": False, "error": "unknown_device"})

    def remote_unbind(self, body: dict) -> tuple[int, dict]:
        on = body.get("on")
        if not isinstance(on, bool):
            return 400, {"ok": False, "error": "bad_request"}
        self.st.set_remote_unbind(on)
        self.st.log("remote_unbind_switch", status="on" if on else "off")
        return 200, {"ok": True, "remote_unbind": self.st.remote_unbind()}

    def close(self) -> None:
        with self.lock:
            if self.pairing:
                self.pairing.close()
                self.pairing = None


# ------------------------------------------------------------------ HTTP
def make_handler(admin: Admin, auth: Auth, port_ref: list):
    class Handler(BaseHTTPRequestHandler):
        server_version = "agentj-admin"
        sys_version = ""
        timeout = REQUEST_DEADLINE       # per recv; the absolute deadline below bounds a slow drip

        def setup(self):
            super().setup()
            self._deadline = threading.Timer(REQUEST_DEADLINE, self._expire)
            self._deadline.daemon = True
            self._deadline.start()

        def _expire(self):               # headers + body did not arrive in time: cut the connection
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        def finish(self):
            self._deadline.cancel()
            try:
                super().finish()
            except OSError:
                pass

        def log_message(self, *a):       # never log request lines or headers
            pass

        def send_error(self, code, message=None, explain=None):   # never http.server's HTML error page
            self._bare(404 if code in (404, 405, 501) else code)

        # ---- responses
        def _common(self):
            for k, v in SECURITY_HEADERS:
                self.send_header(k, v)

        def _bare(self, code: int, headers: tuple = ()):
            self.close_connection = True
            self.send_response(code)
            self._common()
            for k, v in headers:
                self.send_header(k, v)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _json(self, code: int, obj: dict):
            b = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self._common()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def _asset(self, name: str, ctype: str):
            b = (ASSET_DIR / name).read_bytes()
            self.send_response(200)
            self._common()
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        # ---- checks
        def _host_ok(self) -> bool:
            port = port_ref[0]
            hosts = self.headers.get_all("Host") or []
            return len(hosts) == 1 and hosts[0] in (f"127.0.0.1:{port}", f"localhost:{port}")

        def _session(self) -> str | None:
            vals = self.headers.get_all("Authorization") or []
            m = _BEARER.fullmatch(vals[0]) if len(vals) == 1 else None
            return m.group(1) if m and auth.check(m.group(1)) else None

        def _body(self):
            """Write checks: our own Origin, JSON, a declared length ≤ 4 KiB. → dict, or None after an error answer."""
            port = port_ref[0]
            origins = self.headers.get_all("Origin") or []
            if len(origins) != 1 or origins[0] not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
                self._bare(403)
                return None
            if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
                self._bare(403)
                return None
            if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length") or []) != 1:
                self._bare(411)
                return None
            try:
                n = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self._bare(411)
                return None
            if n < 0 or n > MAX_BODY:
                self._bare(413)
                return None
            raw = self.rfile.read(n)
            self._deadline.cancel()      # the whole request is in; a slow action (pairing, Dashboard) may take its time
            try:
                body = json.loads(raw or b"{}")
            except (ValueError, UnicodeDecodeError):
                body = None
            if not isinstance(body, dict):
                self._json(400, {"ok": False, "error": "bad_json"})
                return None
            return body

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def _route(self, method: str):
            try:
                self._route_inner(method)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        def _route_inner(self, method: str):
            if not self._host_ok():
                return self._bare(404)
            u = urllib.parse.urlsplit(self.path)
            path = u.path
            if method == "GET":
                self._deadline.cancel()
                if path in ASSETS and (not u.query or (path == "/" and (_LANG_QUERY.fullmatch(u.query) or u.query in ("pair=1", "pair=1&lang=zh", "pair=1&lang=en", "lang=zh&pair=1", "lang=en&pair=1")))):
                    return self._asset(*ASSETS[path])    # static, no data: no session needed
                if path == "/api/state" and not u.query and self._session():
                    return self._json(200, admin.state())
                return self._bare(404)
            if path == "/api/session":                   # redeem the one-time token from the URL fragment
                body = self._body()
                if body is None:
                    return
                sid = auth.redeem(body.get("token"))
                if not sid:
                    return self._bare(404)
                admin.st.log("admin_login")
                return self._json(200, {"ok": True, "session": sid})
            sid = self._session()
            if not sid:
                return self._bare(404)
            body = self._body()
            if body is None:
                return
            if path == "/api/session/end":
                auth.end(sid)
                return self._json(200, {"ok": True})
            routes = {"/api/name": lambda: admin.rename(body), "/api/pair/start": admin.pair_start,
                      "/api/pair/code": lambda: admin.pair_code(body), "/api/pair/cancel": admin.pair_cancel,
                      "/api/pair/unbind": lambda: admin.pair_unbind(body),
                      "/api/remote-unbind": lambda: admin.remote_unbind(body)}
            if path in routes:
                return self._json(*routes[path]())
            m = _REVOKE.fullmatch(path)
            if m:
                return self._json(*admin.revoke(m.group(1)))
            return self._bare(404)

    return Handler


class _BoundedServer(ThreadingHTTPServer):
    """At most MAX_HANDLERS requests in flight; a connection beyond that is closed at once (review A32-05/08)."""
    daemon_threads = True

    def __init__(self, addr, handler, slots: int):
        self.slots = threading.BoundedSemaphore(slots)
        super().__init__(addr, handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class AdminServer:
    """The listening page. `port=0` → a random free port. Always 127.0.0.1."""

    def __init__(self, st, port: int = 0):
        self.st = st
        self.admin = Admin(st)
        port_ref = [port]
        self.auth = Auth()
        self.httpd = _BoundedServer((BIND, port), make_handler(self.admin, self.auth, port_ref), MAX_HANDLERS)
        self.port = self.httpd.server_address[1]
        port_ref[0] = self.port
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True, name="agentj-admin-http")

    def start(self) -> "AdminServer":
        self.thread.start()
        return self

    def new_link(self) -> str:
        """A fresh one-time link (fragment: the token never appears in a request line); revokes every open session."""
        return f"http://127.0.0.1:{self.port}/#t={self.auth.new_token()}"

    def stop(self) -> None:
        self.admin.close()
        self.httpd.shutdown()
        self.httpd.server_close()
