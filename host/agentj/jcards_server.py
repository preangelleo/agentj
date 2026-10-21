"""P432 (#66 Cards phase 2): a card's own server on this computer (spec AGENTJ-CARDS-SPEC §2.7 / §2.11, PROTOCOL §34).

A template card may take its data — and run a button — from a small server the owner's Agent installed on this computer.
The page still only draws a built-in template; no card code reaches the phone, and the phone never reaches the server:
jcard_get / jcard_call travel the same end-to-end session to serve, which asks http://127.0.0.1:<port><path> itself.
card.json declares everything: one port (1024–65535, not a well-known local service), the data path (`data.get`, GET) and
≤ ROUTES_MAX button routes (POST); a path is plain segments only — no query, no host, no variable part.

Every request: the fixed loopback address, no redirects (http.client never follows one), `X-AgentJ-Card: <id>` sent and
REQUIRED back (a service that is not this card's server is never shown), ≤ TIMEOUT s, a response ≤ the card's maxBytes
(data) / CALL_MAX (a button), one request at a time per card, data at most once per GET_GAP s (a second ask inside it gets the
same answer), a button at most once per CALL_GAP s. What comes back goes through the same template pipeline as a data file
(privacy.redact, parse, bounds); a button's reply is one cleaned line. The POST body is made here — {card, action, item?,
lang} — nothing the phone types reaches the server. The fence shares the network, so this adds no reach an Agent lacks.
P432 security review: the deadline is enforced by a watchdog that shuts the socket (a server trickling bytes cannot hold a
thread past TIMEOUT), and a button's POST goes out only after `OPTIONS <route>` came back 200 with the same X-AgentJ-Card —
a local service that is not this card's server never receives the POST.
"""
from __future__ import annotations

import http.client
import json
import re
import socket
import threading
import time

from . import jcards, privacy

HOST = "127.0.0.1"
PORT_MIN, PORT_MAX = 1024, 65535
# local services a card must never be pointed at (browser / node debuggers, container and database daemons, VNC, k8s, admin
# APIs: Caddy, etcd, Nomad, CouchDB, Vault, Consul, Jupyter, Ollama, RabbitMQ)
DENY_PORTS = frozenset((2019, 2375, 2376, 2379, 2380, 3306, 4646, 5432, 5900, 5901, 5984, 6379, 6443, 8200, 8500, 8888, 9200, 9222,
                        9229, 11211, 11434, 15672, 27017))
PATH_RE = re.compile(r"/(?:[A-Za-z0-9._~-]{1,64}/?){0,16}")
PATH_MAX = 200
ROUTES_MAX = 4
TIMEOUT = 5.0
CALL_MAX = 4096
GET_GAP, CALL_GAP = 1.0, 2.0
HEADER = "X-AgentJ-Card"
MESSAGE_MAX = 200
# a plaza card's server is a GitHub repository pinned to one commit (Leo Q2: 钉 commit，不收分支名)
REPO_RE = re.compile(r"https://github\.com/[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}")
REF_RE = re.compile(r"[0-9a-f]{40}")
SERVER_KEYS = {"port", "routes", "repo", "ref"}
WHYS = ("server_down", "server_timeout", "not_card", "server_status", "too_big", "too_soon", "busy", "bad_reply")

_guard = threading.Lock()
_locks: dict = {}
_last_get: dict = {}
_last_call: dict = {}


def path_ok(p) -> bool:
    return (isinstance(p, str) and len(p) <= PATH_MAX and bool(PATH_RE.fullmatch(p))
            and not any(s in (".", "..") for s in p.split("/")))


def check(doc: dict, errs: list) -> None:
    """card.json `server` (jcards.check calls this): {port, routes?: [{id, path}], repo?, ref?}."""
    m = jcards._m
    srv = doc.get("server")
    if srv is None:
        return
    if not isinstance(srv, dict) or set(srv) - SERVER_KEYS or "port" not in srv:
        errs.append(m("server", "server", "server 写成 {\"port\": 端口, \"routes\"?: […], \"repo\"?, \"ref\"?}",
                      "server is {\"port\": n, \"routes\"?: […], \"repo\"?, \"ref\"?}"))
        return
    port = srv["port"]
    if type(port) is not int or not PORT_MIN <= port <= PORT_MAX or port in DENY_PORTS:
        errs.append(m("server", "server.port", f"server.port 是 {PORT_MIN}–{PORT_MAX} 的整数，不能是调试器 / 数据库等本机服务的端口",
                      f"server.port is an integer {PORT_MIN}–{PORT_MAX}, never a debugger / database port"))
    routes = srv.get("routes", [])
    if not isinstance(routes, list) or len(routes) > ROUTES_MAX:
        errs.append(m("server", "server.routes", f"server.routes 是列表，最多 {ROUTES_MAX} 个", f"server.routes is a list of at most {ROUTES_MAX}"))
        routes = []
    seen = set()
    for i, r in enumerate(routes):
        p = f"server.routes[{i}]"
        if not isinstance(r, dict) or set(r) != {"id", "path"} or not isinstance(r.get("id"), str) \
                or not jcards.ID_RE.fullmatch(r["id"]) or r["id"] in seen or not path_ok(r.get("path")):
            errs.append(m("server", p, f"{p} 是 {{id, path}}：id 小写不重复，path 以 / 开头、只含字母数字 . _ ~ -（不带 ? 和 ..）",
                          f"{p} is {{id, path}}: a unique lower-case id, a path starting with / of letters, digits . _ ~ - (no ? or ..)"))
            continue
        seen.add(r["id"])
    if ("repo" in srv) != ("ref" in srv) or ("repo" in srv and not (isinstance(srv["repo"], str) and REPO_RE.fullmatch(srv["repo"])
                                                                   and isinstance(srv["ref"], str) and REF_RE.fullmatch(srv["ref"]))):
        errs.append(m("server", "server.repo", "server.repo（https://github.com/<作者>/<仓库>）和 server.ref（40 位 commit）一起写",
                      "server.repo (https://github.com/<owner>/<repo>) goes with server.ref (a 40-hex commit)"))


def route_ids(doc: dict) -> set:
    srv = doc.get("server") if isinstance(doc.get("server"), dict) else {}
    rs = srv.get("routes") if isinstance(srv.get("routes"), list) else []
    return {r["id"] for r in rs if isinstance(r, dict) and isinstance(r.get("id"), str)}


def where(doc: dict, path: str, method: str = "GET") -> str:
    return f"{method} http://{HOST}:{doc['server']['port']}{path}"


def _route(doc: dict, rid: str) -> str | None:
    return next((r["path"] for r in doc["server"].get("routes", []) if r["id"] == rid), None)


def _lock(cid: str) -> threading.Lock:
    with _guard:
        return _locks.setdefault(cid, threading.Lock())


def _request(cid: str, port: int, method: str, path: str, body: bytes | None, most: int) -> tuple[bytes, str, bool]:
    """→ (bytes ≤ most, content type, cut). Raises LookupError(why)."""
    deadline = time.monotonic() + TIMEOUT
    conn = http.client.HTTPConnection(HOST, port, timeout=TIMEOUT)
    late = []

    def cut_off():                            # the whole exchange, not each recv: a trickle cannot outlive the deadline
        late.append(1)
        try:
            conn.sock and conn.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
    dog = threading.Timer(TIMEOUT, cut_off)
    dog.daemon = True
    dog.start()
    try:
        hdr = {HEADER: cid, "Accept": "application/json, text/plain;q=0.9, text/markdown;q=0.9", "User-Agent": "agentj-card/1",
               "Connection": "close"}
        if body is not None:
            hdr["Content-Type"] = "application/json"
        conn.request(method, path, body=body, headers=hdr)
        res = conn.getresponse()
        if late:
            raise LookupError("server_timeout")
        if res.status != 200:
            raise LookupError("server_status")
        if (res.getheader(HEADER) or "").strip() != cid:
            raise LookupError("not_card")
        out = b""
        while len(out) <= most:
            if time.monotonic() > deadline:
                raise LookupError("server_timeout")
            chunk = res.read(min(65536, most + 1 - len(out)))
            if not chunk:
                break
            out += chunk
        if late:
            raise LookupError("server_timeout")
        return out[:most], (res.getheader("Content-Type") or "").split(";")[0].strip().lower(), len(out) > most
    except (ConnectionRefusedError, ConnectionResetError, OSError) as e:
        if late or isinstance(e, (socket.timeout, TimeoutError)):
            raise LookupError("server_timeout") from None
        raise LookupError("server_down") from None
    except http.client.HTTPException:
        raise LookupError("server_timeout" if late else "bad_reply") from None
    finally:
        dog.cancel()
        conn.close()


def get_data(card: dict) -> tuple[bytes | None, str | None, bool]:
    """jcards._read for a card whose data comes from its server → (bytes, problem, cut)."""
    doc, cid = card["doc"], card["id"]
    most = doc["data"].get("maxBytes", jcards.DATA_DEFAULT)
    lk = _lock(cid)
    if not lk.acquire(timeout=TIMEOUT + 1):
        return None, "busy", False
    try:
        now = time.monotonic()
        hit = _last_get.get(cid)
        if hit and now - hit[0] < GET_GAP and hit[1] == (doc["server"]["port"], doc["data"]["get"]):
            return hit[2]
        try:
            raw, ctype, cut = _request(cid, doc["server"]["port"], "GET", doc["data"]["get"], None, most)
            ans = (raw, None, cut)
            if doc["template"] == "checklist" and cut:
                ans = (None, "too_big", False)
        except LookupError as e:
            ans = (None, str(e.args[0]), False)
        _last_get[cid] = (time.monotonic(), (doc["server"]["port"], doc["data"]["get"]), ans)
        return ans
    finally:
        lk.release()


def call(root, cid, act, item=None, lang: str = "zh") -> dict:
    """§34 jcard_call: the owner confirmed button `act` (of the card, or of one checklist item) → POST to its route."""
    fail = lambda why: {"ok": False, "id": cid if isinstance(cid, str) else None, "act": act if isinstance(act, str) and jcards.ID_RE.fullmatch(act) else None, "why": why}
    c = jcards.find(root, cid)
    if not c:
        return fail("not_found")
    if not c["on"]:
        return fail("off")
    doc = c["doc"]
    if item is not None and not (isinstance(item, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,32}", item)):
        return fail("bad_item")
    keys = ("itemActions",) if item is not None else ("actions",)
    a = next((x for k in keys for x in doc.get(k, []) if x.get("id") == act and "call" in x), None) if isinstance(act, str) else None
    if a is None or not isinstance(doc.get("server"), dict):
        return fail("not_found")
    path = _route(doc, a["call"])
    if path is None:
        return fail("not_found")
    lk = _lock(cid)
    if not lk.acquire(blocking=False):
        return fail("busy")
    try:
        now = time.monotonic()
        if now - _last_call.get(cid, -1e9) < CALL_GAP:
            return fail("too_soon")
        _last_call[cid] = now
        body = json.dumps({"card": cid, "action": act, **({"item": item} if item is not None else {}),
                           "lang": lang if lang in ("zh", "en") else "zh"}).encode()
        try:
            _request(cid, doc["server"]["port"], "OPTIONS", path, None, 1024)   # this card's server, or no POST at all
            raw, ctype, cut = _request(cid, doc["server"]["port"], "POST", path, body, CALL_MAX)
        except LookupError as e:
            return fail(str(e.args[0]))
    finally:
        lk.release()
    ok, text = True, raw.decode("utf-8", "replace")
    if ctype == "application/json" and not cut:
        try:
            obj = json.loads(text)
        except ValueError:
            return fail("bad_reply")
        if not isinstance(obj, dict):
            return fail("bad_reply")
        ok, text = obj.get("ok") is not False, obj.get("message") if isinstance(obj.get("message"), str) else ""
    _last_get.pop(cid, None)                  # the next jcard_get reads fresh data
    return {"ok": ok, "id": cid, "act": act, "message": jcards._clean(privacy.redact(text, owner=True), MESSAGE_MAX)}
