"""Host ↔ control plane (PROTOCOL.md §7): signed envelopes.

The host *pushes* metadata. Answers are parsed through explicit whitelists (login / poll status fields, an `error`
code, `ok`, F19 pinned-signature official data in notices.json, and — A3.1 sync — a list of unbind requests {id, device}), and binding answers are written only to cloud.json — and only after the human at this terminal confirmed the tenant (`agentj login`). This module must never
touch the allowlist (devices.json) — enforced by tests/test_cloud.py (AST check): unbind requests are only *returned* to
`serve`, which checks them against its own allowlist, switch and hourly limit before revoking anything. A3.2: the Agent
name in poll / sync / rename answers is whitelisted through text.agent_name_problem (§1) and only *returned*; the caller
(CLI after the human's y, serve, `agentj name`) stores it — it is a display string and never reaches devices.json. Every cloud.json mutation (login, seq update, unlink) runs under one fcntl lock file in the
state dir (cloud.lock, 0600), so the CLI and `serve` never interleave (A3-04).
Seat setup (contract seat-setup §4): `seat_bind` sends a setup code the human handed to this host (`ajt_…`, checked locally
against its shape, never logged, never printed, never sent anywhere but `/v1/host/seat-bind`); the answer goes through the
same bound-answer whitelist as `poll` and is written only to cloud.json (`via: "seat"`). There is no y/N: possession of the
code is the human's decision.
"""
from __future__ import annotations

import contextlib
import fcntl
import http.client
import json
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, NamedTuple

from . import __version__ as VERSION, wire   # VERSION: single source in agentj/__init__.py
from .envcompat import getenv
from .text import agent_name_problem, clean_label, clean_line, is_clean_agent_name, machine_name, normalise_agent_name

AGENT = f"agentj/{VERSION}"
DEFAULT_API = "https://agentj.app/api"
DEFAULT_APP = "https://agentj.app/account"   # the Dashboard this host tells its human to open (F7)
CTX_LOGIN, CTX_POLL, CTX_REPORT = "agentjarvis-host-login-v1", "agentjarvis-host-poll-v1", "agentjarvis-host-report-v1"
CTX_SYNC = "agentjarvis-host-sync-v1"
CTX_RENAME = "agentjarvis-host-rename-v1"
CTX_DECLINE = "agentjarvis-host-decline-v1"   # L2 / G-A11: the human at the host said no to the tenant it was bound to
CTX_SEAT_BIND = "agentjarvis-host-seat-bind-v1"   # seat setup §4: bind this host to one seat with a setup code
CTX_SEAT_LEAVE = "agentjarvis-host-seat-leave-v1"   # review SS-02: take this seat-bound host out of its company
CTX_UPGRADE_AUTH = "agentjarvis-host-upgrade-auth-v1"   # F12 / contract C2: spend an upgrade authorization code (update.py)
CTX_PEER_CERT = "agentjarvis-host-peer-cert-v1"   # P71 (PROTOCOL §17.3): the Agent friends seat certificate for one mailbox
# Agent plaza P2 (PROTOCOL §7 plaza routes; agentj/plaza.py): one context per route, so no signature is valid on two
PLAZA_KINDS = ("search", "get", "mine", "post", "reply", "resolve", "report")
CTX_PLAZA = {k: f"agentjarvis-host-plaza-{k}-v1" for k in PLAZA_KINDS}   # wire strings: never renamed
CTX_PLAZA_POST = CTX_PLAZA["post"]
MAX_PLAZA_RESPONSE = 2 * 1024 * 1024   # a post with 50 replies of ≤ 4000 characters each
MAX_SUGGESTIONS = 3
MAX_SYNC_ITEMS = 10
UNBIND_RESULTS = ("revoked", "unknown_device", "disabled", "rate_limited")
HTTP_TIMEOUT = 10
MAX_ENVELOPE = 32 * 1024
MAX_RESPONSE = 16 * 1024
MAX_DEVICES = 64
MIN_POLL = 4            # DASHBOARD_API §3: poll ≥ 4 s apart
SLOW_DOWN_STEP = 5      # 429 slow_down → +5 s (RFC 8628 §3.5)
MAX_SEQ = 2**53 - 1
LOOPBACK = {"127.0.0.1", "localhost", "::1"}

_DEVICE_ID = re.compile(r"[A-Za-z0-9_-]{16}")
_LOGIN_ID = re.compile(r"[A-Za-z0-9_-]{16,64}")
_UNBIND_ID = re.compile(r"[A-Za-z0-9_-]{22}")
_HOST_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_USER_CODE = re.compile(r"[BCDFGHJKLMNPQRSTVWXZ2-9]{4}-[BCDFGHJKLMNPQRSTVWXZ2-9]{4}")
_SLUG = re.compile(r"(?!.*--)[a-z0-9][a-z0-9-]{1,28}[a-z0-9]")
_ERROR = re.compile(r"[a-z_]{1,32}")
_PRINTABLE_URL = re.compile(r"[\x21-\x7e]{1,512}")
SEAT_CODE = re.compile(r"ajt_[A-Za-z0-9_-]{43}")   # 256 random bits, base64url (contract seat-setup §1)


class CloudError(Exception):
    """Transport failure; `kind` is a metadata-only class: timeout | network | refused_url | too_large."""
    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


class ReportResult(NamedTuple):
    kind: str            # ok | unbound | fail | unlinked
    status: str = ""     # "200" | "http_4xx" | "http_5xx" | "timeout" | "network" | "bad_response" | ...
    seq: int | None = None
    account: dict | None = None   # A1: account language
    notices: list | None = None   # verified official data, saved privately before callback


# ------------------------------------------------------------------ envelope
def envelope(context: str, inner: dict, signing_key) -> dict:
    """§7: body = b64url(UTF-8 JSON); sig = Ed25519(sk, context + "\\n" + body) over the body text exactly as sent."""
    body = wire.b64u(json.dumps(inner, ensure_ascii=False, separators=(",", ":")).encode())
    sig = signing_key.sign(f"{context}\n{body}".encode("ascii"))
    from cryptography.hazmat.primitives import serialization
    pub = signing_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return {"pk": wire.b64u(pub), "body": body, "sig": wire.b64u(sig)}


def channel_of(st) -> str:
    return wire.channel_id(st.signing_pub())


# ------------------------------------------------------------------ URLs
def check_url(url: str) -> str:
    """https anywhere; plain http only to loopback (tests). No credentials, query or fragment. → base without '/'."""
    if not isinstance(url, str) or not _PRINTABLE_URL.fullmatch(url):
        raise CloudError("refused_url")
    u = urllib.parse.urlsplit(url)
    host = (u.hostname or "").lower()
    if u.username or u.password or u.query or u.fragment or not host:
        raise CloudError("refused_url")
    if u.scheme == "https" or (u.scheme == "http" and host in LOOPBACK):
        return url.rstrip("/")
    raise CloudError("refused_url")


def api_url(st, cloud: dict | None = None, override: str | None = None) -> str:
    """--api flag → env AGENTJ_API_URL → the API this host was bound with (cloud.json) → config `api` → default."""
    cfg_api = None
    try:
        cfg_api = st.config().get("api")
    except (OSError, ValueError):
        pass
    for cand in (override, getenv("AGENTJ_API_URL"), (cloud or {}).get("api"), cfg_api, DEFAULT_API):
        if cand:
            return check_url(cand)
    return DEFAULT_API


def app_url(st) -> str:
    """The Dashboard URL printed by `agentj login`: env AGENTJ_APP_URL → config `app` → default. Never the server's."""
    cfg_app = None
    try:
        cfg_app = st.config().get("app")
    except (OSError, ValueError):
        pass
    for cand in (getenv("AGENTJ_APP_URL"), cfg_app, DEFAULT_APP):
        if cand:
            return check_url(cand)
    return DEFAULT_APP


def _origin(url: str) -> tuple:
    u = urllib.parse.urlsplit(url)
    return (u.scheme, (u.hostname or "").lower(), u.port or (443 if u.scheme == "https" else 80))


def dashboard_uri(app: str, server_uri: str | None) -> str:
    """What to print: the server's verification_uri only when it is on our configured Dashboard origin, else the
    configured Dashboard itself — a compromised control plane cannot send the human to a look-alike site (F7)."""
    if server_uri:
        try:
            if _origin(check_url(server_uri)) == _origin(app):
                return server_uri
        except CloudError:
            pass
    return app + "/"


# ------------------------------------------------------------------ HTTP
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):  # a redirect could reroute the signed body (or downgrade to http): never follow
        return None


def status_class(status: int) -> str:
    return f"http_{status // 100}xx"


def post_json(url: str, payload: dict, timeout: float = HTTP_TIMEOUT, max_response: int = MAX_RESPONSE) -> tuple[int, dict]:
    """POST JSON with TLS verification on. Returns (status, response object or {}). Raises CloudError on transport errors.
    Answers larger than `max_response` bytes are read as {} (16 KiB by default; the plaza reads up to 2 MiB)."""
    check_url(url)
    data = json.dumps(payload, separators=(",", ":")).encode()
    if len(data) > MAX_ENVELOPE:
        raise CloudError("too_large")
    u = urllib.parse.urlsplit(url)
    handlers: list = [_NoRedirect()]
    if (u.hostname or "").lower() in LOOPBACK:
        handlers.append(urllib.request.ProxyHandler({}))  # never send a loopback request through a proxy
    else:
        handlers.append(urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"content-type": "application/json", "accept": "application/json",
                                          "user-agent": AGENT})
    try:
        with opener.open(req, timeout=timeout) as r:
            status, raw = r.status, r.read(max_response + 1)
    except urllib.error.HTTPError as e:
        status = e.code
        try:
            raw = e.read(max_response + 1)
        except Exception:
            raw = b""
    except urllib.error.URLError as e:
        raise CloudError("timeout" if isinstance(e.reason, (socket.timeout, TimeoutError)) else "network") from None
    except (socket.timeout, TimeoutError):
        raise CloudError("timeout") from None
    except (OSError, http.client.HTTPException, ValueError):
        raise CloudError("network") from None
    obj: dict = {}
    if raw and len(raw) <= max_response:
        try:
            parsed = json.loads(raw)
            obj = parsed if isinstance(parsed, dict) else {}
        except (ValueError, RecursionError):   # a hostile answer nested deeper than the parser goes
            obj = {}
    return status, obj


# ------------------------------------------------------------------ whitelisted response parsing
def _int(v, lo: int, hi: int) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi else None


def parse_error(obj: dict) -> str | None:
    e = obj.get("error")
    return e if isinstance(e, str) and _ERROR.fullmatch(e) else None


def parse_login(obj: dict) -> dict | None:
    """201 answer → {login_id, user_code, verification_uri, expires_in, interval}; anything else in it is ignored."""
    lid, code, uri = obj.get("login_id"), obj.get("user_code"), obj.get("verification_uri")
    exp, iv = _int(obj.get("expires_in"), 1, 3600), _int(obj.get("interval"), 1, 60)
    if not (isinstance(lid, str) and _LOGIN_ID.fullmatch(lid) and isinstance(code, str) and _USER_CODE.fullmatch(code)
            and exp and iv):
        return None
    try:
        uri = check_url(uri)  # printed on the terminal: https (or loopback http), printable ASCII only
    except CloudError:
        return None
    return {"login_id": lid, "user_code": code, "verification_uri": uri, "expires_in": exp, "interval": max(MIN_POLL, iv)}


def parse_agent_name(v) -> str | None:
    """A control-plane Agent name → the normalised name if it passes §1, else None (never cleaned into something valid)."""
    return normalise_agent_name(v) if isinstance(v, str) and len(v) <= 256 and agent_name_problem(v) is None else None


def parse_poll(obj: dict) -> dict | None:
    """200 answer → {status} (+ {host_id, tenant{slug, name}, agent_name} when bound); anything else in it is ignored.
    agent_name is None when missing or not a valid §1 name."""
    status = obj.get("status")
    if status not in ("pending", "bound", "rejected", "expired"):
        return None
    if status != "bound":
        return {"status": status}
    hid, ten = obj.get("host_id"), obj.get("tenant")
    if not (isinstance(hid, str) and _HOST_ID.fullmatch(hid) and isinstance(ten, dict)):
        return None
    slug, name = ten.get("slug"), ten.get("name")
    if not (isinstance(slug, str) and _SLUG.fullmatch(slug) and isinstance(name, str)):
        return None
    return {"status": "bound", "host_id": hid, "tenant": {"slug": slug, "name": clean_line(name, 64) or slug},
            "agent_name": parse_agent_name(obj.get("agent_name"))}


# ------------------------------------------------------------------ cloud.json
def read_cloud(st) -> dict | None:
    """The Dashboard link, or None when not linked (missing or unreadable file)."""
    try:
        d = json.loads(st.cloud_path.read_text())
    except (FileNotFoundError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(d, dict):
        return None
    ten = d.get("tenant") if isinstance(d.get("tenant"), dict) else {}
    try:
        api = check_url(d.get("api"))
    except CloudError:
        return None
    hid, slug = d.get("host_id"), ten.get("slug")
    if not (isinstance(hid, str) and _HOST_ID.fullmatch(hid) and isinstance(slug, str) and _SLUG.fullmatch(slug)):
        return None
    return {"api": api, "host_id": hid,
            "tenant": {"slug": slug, "name": clean_line(str(ten.get("name", "")), 64) or slug},
            "linked_at": _int(d.get("linked_at"), 0, MAX_SEQ) or 0, "last_seq": _int(d.get("last_seq"), 0, MAX_SEQ) or 0,
            # files written before the seat path existed (≤ 0.6) were all made by the code path
            "via": "seat" if d.get("via") == "seat" else "code",
            "no_language": _parse_no_language(d.get("no_language"))}


def _parse_no_language(v) -> dict | None:
    """A1 back-compat marker: {"at": ms, "agent": AGENT, "api": url} = that server refused the report's language keys."""
    if not isinstance(v, dict):
        return None
    at, agent, api = _int(v.get("at"), 0, MAX_SEQ), v.get("agent"), v.get("api")
    if at is None or not isinstance(agent, str) or len(agent) > 64 or not isinstance(api, str) or not _PRINTABLE_URL.fullmatch(api):
        return None
    return {"at": at, "agent": agent, "api": api, **({"notices_only": True} if v.get("notices_only") is True else {})}


@contextlib.contextmanager
def cloud_lock(st):
    """Exclusive lock for every cloud.json mutation, across threads and processes (flock on a private lock file)."""
    fd = os.open(st.cloud_lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # releases the lock


def _write_cloud_file(st, d: dict) -> None:
    """The one writer of cloud.json (0600, tmp + rename). Callers hold cloud_lock."""
    rec = {"api": d["api"], "host_id": d["host_id"], "tenant": {"slug": d["tenant"]["slug"], "name": d["tenant"]["name"]},
           "linked_at": int(d["linked_at"]), "last_seq": int(d["last_seq"]),
           "via": "seat" if d.get("via") == "seat" else "code"}
    nl = _parse_no_language(d.get("no_language"))
    if nl:
        rec["no_language"] = nl
    st.write_private(st.cloud_path, json.dumps(rec, indent=1, ensure_ascii=False).encode())


def write_cloud(st, d: dict) -> None:
    with cloud_lock(st):
        _write_cloud_file(st, d)


def delete_cloud(st) -> bool:
    with cloud_lock(st):
        try:
            st.cloud_path.unlink()
            return True
        except FileNotFoundError:
            return False


def _store_seq(st, host_id: str, seq: int) -> None:
    """Persist last_seq — under the lock, re-reading first, so a cloud.json that `agentj unlink` removed is never
    recreated (the unlink either finished before the re-read, or waits until this write is done and then removes it)."""
    with cloud_lock(st):
        cur = read_cloud(st)
        if cur and cur["host_id"] == host_id and seq > cur["last_seq"]:
            cur["last_seq"] = seq
            _write_cloud_file(st, cur)


def next_seq(last_seq: int, now: float) -> int:
    return min(MAX_SEQ, max(last_seq + 1, int(now * 1000)))


# ------------------------------------------------------------------ report
def norm_pending(p: dict | None) -> dict:
    p = p or {}
    count = p.get("count") if isinstance(p.get("count"), int) and not isinstance(p.get("count"), bool) else 0
    count = max(0, min(MAX_DEVICES, count))
    since = p.get("since")
    since = int(since) if count and isinstance(since, (int, float)) and not isinstance(since, bool) and since > 0 else None
    return {"count": count, "since": since}


def report_devices(st, online) -> list[dict]:
    """The host's allowlist as §7 metadata: id, cleaned label, paired_at, online. At most 64 (the most recently paired)."""
    online = set(online or ())
    devs = []
    for did, v in st.devices().items():
        if not (isinstance(did, str) and _DEVICE_ID.fullmatch(did) and isinstance(v, dict)):
            continue
        pa = v.get("paired_at")
        pa = int(pa) if isinstance(pa, (int, float)) and not isinstance(pa, bool) and 0 <= pa <= MAX_SEQ else 0
        devs.append({"id": did, "name": clean_label(str(v.get("name", ""))), "paired_at": pa, "online": did in online})
    # paired_at has 1 s resolution: equal seconds keep allowlist order (= pairing order; the sort is stable), so "the most
    # recently paired" is exact even for devices paired within the same second (a random-id tie-break dropped them at random)
    devs.sort(key=lambda d: d["paired_at"])
    return devs[-MAX_DEVICES:]


def build_report(st, online, pending, *, seq: int, ts: int) -> dict:
    """Inner report body, key order as in §7 / the vector; A3.2 appends agent_name (§1 or null) and machine (≤ 64 or null);
    A1 (0.15) appends the owner's one language value and when it was last set on this host (ms; 0 = never set here)."""
    from . import preferences, notices
    lang, at = preferences.language_state(st)
    return {"v": 1, "t": "report", "channel": channel_of(st), "ts": int(ts), "seq": int(seq), "agent": AGENT,
            "devices": report_devices(st, online), "pending": norm_pending(pending), "agent_name": _clean_or_none(st.agent_name()),
            "machine": machine_name() if st.report_machine() else None, "language": lang, "language_at": int(at), "harness": (st.agent_config() or {}).get("kind", "unknown"),
            "notices_v": 1, "notice_receipts": notices.receipts(st)}


def parse_account_language(obj) -> dict | None:
    """A1: the 200 report answer's {"language", "language_at"} (the account's value after the merge), else None. Only
    'zh' / 'en' and a non-negative integer ms are accepted; anything else is ignored (an older server sends neither)."""
    if not isinstance(obj, dict):
        return None
    lang, at = obj.get("language"), obj.get("language_at")
    if lang in ("zh", "en") and type(at) is int and 0 <= at <= MAX_SEQ:
        return {"language": lang, "language_at": at}
    return None


def _clean_or_none(name):
    """The server is strict for host-sent names (already normalised, §1-valid); anything else is reported as null."""
    return name if is_clean_agent_name(name) else None


def _report_envelope(st, inner: dict) -> dict:
    sk = st.signing_key()
    env = envelope(CTX_REPORT, inner, sk)
    while len(json.dumps(env, separators=(",", ":"))) > MAX_ENVELOPE and inner["devices"]:
        inner["devices"] = inner["devices"][1:]  # drop the oldest pairing until it fits
        env = envelope(CTX_REPORT, inner, sk)
    return env


LANGUAGE_KEYS = ("language", "language_at")
NOTICE_KEYS = ("harness", "notices_v", "notice_receipts")
NO_LANGUAGE_REPROBE_MS = 24 * 3600 * 1000   # a server that refused the language keys is asked again after a day


def _skip_language(cloud: dict, api: str, now_ms: int) -> bool:
    """A1 back-compat: this server (same api, same agentj version) refused the language keys less than a day ago."""
    nl = cloud.get("no_language")
    return bool(nl and not nl.get("notices_only") and nl["agent"] == AGENT and nl["api"] == api and 0 <= now_ms - nl["at"] < NO_LANGUAGE_REPROBE_MS)


def _skip_notices(cloud: dict, api: str, now_ms: int) -> bool:
    nl = cloud.get('no_language')
    return bool(nl and nl['agent'] == AGENT and nl['api'] == api and 0 <= now_ms - nl['at'] < NO_LANGUAGE_REPROBE_MS)


def _store_no_language(st, host_id: str, mark: dict | None) -> None:
    """Record (or clear, mark=None) the "server has no language sync" marker — under the lock, re-reading first (as _store_seq)."""
    with cloud_lock(st):
        cur = read_cloud(st)
        if cur and cur["host_id"] == host_id and cur.get("no_language") != mark:
            cur["no_language"] = mark
            _write_cloud_file(st, cur)


def report_post_json(url, payload):
    return post_json(url, payload, max_response=128 * 1024)


def send_report(st, online, pending, *, post: Callable = report_post_json, now: Callable = time.time) -> ReportResult:
    """One signed report (blocking; serve runs it off the event loop). A 409 replay is retried once with a bigger seq.
    F19 back-compat first retries without notice capability/receipts/harness, preserving F13 language.
    A1 back-compat: a ≤ 0.14 server refuses unknown inner keys with 400 bad_request (before its seq gate, so the seq is not
    spent there); a report carrying `language` / `language_at` that gets that answer is retried once at once without them
    (fresh, bigger seq). If that one is accepted, cloud.json remembers it (`no_language`) and later reports leave the keys out
    until the marker is a day old, agentj's version changes or the API URL changes (a relink writes a fresh file)."""
    cloud = read_cloud(st)
    if not cloud:
        return ReportResult("unlinked")
    try:
        base = api_url(st, cloud)
        url = base + "/v1/host/report"
    except CloudError as e:
        return ReportResult("fail", e.kind)
    last = cloud["last_seq"]
    strip = _skip_language(cloud, base, int(now() * 1000))
    strip_notices = _skip_notices(cloud, base, int(now()*1000))
    replay_retry = probe_retry = False
    notice_retry = False
    seq = None
    while True:
        t = now()
        seq = next_seq(last, t)
        inner = build_report(st, online, pending, seq=seq, ts=int(t))
        if strip_notices:
            for k in NOTICE_KEYS:
                inner.pop(k, None)
        if strip:
            for k in LANGUAGE_KEYS:
                inner.pop(k, None)
        env = _report_envelope(st, inner)
        _store_seq(st, cloud["host_id"], seq)
        try:
            status, obj = post(url, env)
        except CloudError as e:
            return ReportResult("fail", e.kind, seq)
        if status == 200 and obj.get("ok") is True:
            if notice_retry and not probe_retry:
                _store_no_language(st, cloud["host_id"], {"at": int(t * 1000), "agent": AGENT, "api": base, "notices_only": True})
                st.log("report_notices", result="server_without_notices")
            elif probe_retry:     # accepted only without the keys: an older server
                _store_no_language(st, cloud["host_id"], {"at": int(t * 1000), "agent": AGENT, "api": base})
                st.log("report_language", result="server_without_language_sync")
            elif not strip_notices and cloud.get("no_language"):
                _store_no_language(st, cloud["host_id"], None)   # the re-probe went through: the server syncs now
            data = None
            from . import notices
            snap = notices.snapshot(obj.get("notices"), cloud["host_id"], seq, int(t*1000))
            if snap is not None:
                data = notices.ingest(st, snap, cloud["host_id"], int(t*1000))
                notices.acknowledge(st, inner.get("notice_receipts", []))
            return ReportResult("ok", "200", seq, None if strip else parse_account_language(obj), data)
        err = parse_error(obj)
        if status == 409 and err == "replay" and not replay_retry:
            replay_retry = True
            last = seq  # self-heal: next try uses max(last_seq + 1, now_ms)
            continue
        if status == 400 and err == "bad_request" and not strip_notices:
            strip_notices = notice_retry = True
            last = seq
            continue
        if status == 400 and err == "bad_request" and not strip:
            strip = probe_retry = True
            last = seq  # fresh seq for the retry (the refused one never reached the server's seq gate, but never reuse it)
            continue
        if status == 403 and err == "not_bound":
            return ReportResult("unbound", status_class(status), seq)
        return ReportResult("fail", status_class(status) if status != 200 else "bad_response", seq)


# ------------------------------------------------------------------ login (RFC 8628 shape)
def login(st, api: str, *, show: Callable[[dict], None], confirm: Callable[[dict, str | None], bool], post: Callable = post_json,
          sleep: Callable = time.sleep, now: Callable = time.time, on_poll: Callable[[str], None] | None = None) -> dict:
    """Signed login, then poll every `interval` s (≥ 4; 429 → +5 s) until bound / rejected / expired.
    Returns {"status": bound|declined|rejected|expired|already_bound|rate_limited|error, ...}. On bound, `confirm(tenant)`
    asks the human whether this is their tenant (F5); only a yes writes cloud.json, a no writes nothing ("declined").
    A3.2: confirm(tenant, agent_name) also shows the Agent name the Dashboard gave this host; a bound result carries it
    (`agent_name`, None if absent / invalid) so the caller sets the local name after the yes — this module never does."""
    base = check_url(api)
    sk, ch = st.signing_key(), channel_of(st)
    status, obj = post(base + "/v1/host/login", envelope(CTX_LOGIN, {"v": 1, "t": "login", "channel": ch, "ts": int(now())}, sk))
    if status == 409 and parse_error(obj) == "already_bound":
        return {"status": "already_bound"}
    if status == 429:
        return {"status": "rate_limited"}
    if status != 201:
        return {"status": "error", "http": status_class(status), "error": parse_error(obj)}
    lg = parse_login(obj)
    if not lg:
        return {"status": "error", "http": "bad_response"}
    show(lg)
    deadline, interval = now() + lg["expires_in"], lg["interval"]
    while True:
        sleep(interval)
        if now() > deadline + interval:
            return {"status": "expired"}
        inner = {"v": 1, "t": "poll", "channel": ch, "ts": int(now()), "login_id": lg["login_id"]}
        try:
            status, obj = post(base + "/v1/host/poll", envelope(CTX_POLL, inner, sk))
        except CloudError as e:
            if on_poll:
                on_poll(e.kind)
            continue  # transient: keep polling until the code expires
        if status == 429:
            interval += SLOW_DOWN_STEP
            if on_poll:
                on_poll("slow_down")
            continue
        if status == 404:
            return {"status": "expired"}
        if status >= 500:
            if on_poll:
                on_poll(status_class(status))
            continue
        if status != 200:
            return {"status": "error", "http": status_class(status), "error": parse_error(obj)}
        p = parse_poll(obj)
        if not p:
            return {"status": "error", "http": "bad_response"}
        if p["status"] == "pending":
            continue
        if p["status"] == "bound":
            if not confirm(p["tenant"], p["agent_name"]):
                return {"status": "declined", "tenant": p["tenant"], "host_id": p["host_id"],
                        "undone": decline(st, base, lg["login_id"], post=post, now=now)}
            write_cloud(st, {"api": base, "host_id": p["host_id"], "tenant": p["tenant"], "linked_at": int(now()),
                             "last_seq": 0, "via": "code"})
            st.log("cloud_linked", tenant=p["tenant"]["slug"], kind="code")
            return {"status": "bound", "tenant": p["tenant"], "host_id": p["host_id"], "agent_name": p["agent_name"]}
        return {"status": p["status"]}


# ------------------------------------------------------------------ seat bind (seat setup §4: a setup code from the owner)
SEAT_RESULTS = ("bound", "name_taken", "invalid_setup", "payment_required", "already_bound", "bad_name", "name_required",
                "bad_code", "rate_limited", "error")


def seat_code_ok(code) -> bool:
    """The local shape check: `ajt_` + 43 base64url characters, nothing else (no whitespace, no prefix / suffix)."""
    return isinstance(code, str) and SEAT_CODE.fullmatch(code) is not None


def seat_bind(st, api: str, token: str, name: str, *, post: Callable = post_json, now: Callable = time.time) -> dict:
    """Signed `/v1/host/seat-bind` (PROTOCOL §7). Refuses locally — nothing sent — a code that is not `ajt_` + 43 base64url
    characters (`bad_code`) and a name that fails the Agent-name rules (`bad_name` / `name_required`); the name is sent
    normalised. Returns {"status": one of SEAT_RESULTS, ...}. On bound, cloud.json is written (`via: "seat"`) exactly like
    the code path and `cloud_linked kind=seat` is logged; the caller sets the local Agent name. The code is never logged,
    never put in a result and never sent anywhere else; the answer passes the same whitelist as a bound poll answer, and
    nothing in it can add or approve a device (this module never touches the allowlist)."""
    if not seat_code_ok(token):
        return {"status": "bad_code"}
    problem = agent_name_problem(name)
    if problem:
        return {"status": problem}
    name = normalise_agent_name(name)
    base = check_url(api)
    inner = {"v": 1, "t": "seat_bind", "channel": channel_of(st), "ts": int(now()), "token": token, "name": name}
    try:
        status, obj = post(base + "/v1/host/seat-bind", envelope(CTX_SEAT_BIND, inner, st.signing_key()))
    except CloudError as e:
        st.log("seat_bind", result="error", status=e.kind)
        return {"status": "error", "http": e.kind}
    err = parse_error(obj)
    if status == 200:
        p = parse_poll(obj)
        if not p or p["status"] != "bound":
            st.log("seat_bind", result="error", status="bad_response")
            return {"status": "error", "http": "bad_response"}
        write_cloud(st, {"api": base, "host_id": p["host_id"], "tenant": p["tenant"], "linked_at": int(now()),
                         "last_seq": 0, "via": "seat"})
        st.log("cloud_linked", tenant=p["tenant"]["slug"], kind="seat")
        return {"status": "bound", "tenant": p["tenant"], "host_id": p["host_id"], "agent_name": p["agent_name"] or name}
    kind = ("name_taken" if status == 409 and err == "name_taken" else
            "already_bound" if status == 409 and err == "already_bound" else
            "invalid_setup" if status == 404 else
            "payment_required" if status == 402 else
            "rate_limited" if status == 429 else
            err if status == 400 and err in ("bad_name", "name_required") else "error")
    st.log("seat_bind", result=kind, status=status_class(status))
    out: dict = {"status": kind}
    if kind == "name_taken":
        out["suggestions"] = parse_suggestions(obj)
    elif kind == "error":
        out.update(http=status_class(status), error=err)
    return out


def seat_leave(st, base: str, *, post: Callable = post_json, now: Callable = time.time) -> dict:
    """Signed `/v1/host/seat-leave` (PROTOCOL §7, review SS-02): take this host — bound through a seat setup — out of its
    company (host unbound there, the setup revoked, the seat freed). It can only take the host *out*; nothing in the answer
    is acted on but its status. Returns {"status": "left" | "not_found" | "rate_limited" | "fail", "http": …}; never raises
    (a transport failure is "fail" with the class in "http"). Does not touch cloud.json — the caller (`agentj unlink`)
    removes it afterwards whatever the answer."""
    inner = {"v": 1, "t": "seat_leave", "channel": channel_of(st), "ts": int(now())}
    try:
        status, obj = post(check_url(base) + "/v1/host/seat-leave", envelope(CTX_SEAT_LEAVE, inner, st.signing_key()))
    except CloudError as e:
        st.log("seat_leave", result="fail", status=e.kind)
        return {"status": "fail", "http": e.kind}
    res = ("left" if status == 200 and obj.get("status") == "left" else "not_found" if status == 404
           else "rate_limited" if status == 429 else "fail")
    st.log("seat_leave", result=res, status=status_class(status))
    return {"status": res, "http": status_class(status)}


def decline(st, base: str, login_id: str, *, post: Callable = post_json, now: Callable = time.time) -> str:
    """Signed "decline" (PROTOCOL §7, L2 / G-A11): undo the binding this login just made, so a stranger who read the code
    off this terminal does not keep this host in their tenant. Returns undone | already_confirmed | not_found | fail."""
    inner = {"v": 1, "t": "decline", "channel": channel_of(st), "ts": int(now()), "login_id": login_id}
    try:
        status, obj = post(check_url(base) + "/v1/host/decline", envelope(CTX_DECLINE, inner, st.signing_key()))
    except CloudError as e:
        st.log("cloud_decline", result="fail", status=e.kind)
        return "fail"
    err = parse_error(obj)
    res = ("undone" if status == 200 else "already_confirmed" if status == 409 and err == "already_confirmed"
           else "not_found" if status == 404 else "fail")
    st.log("cloud_decline", result=res, status=status_class(status))
    return res


# ------------------------------------------------------------------ upgrade authorization (F12, contract C2)
UPGRADE_AUTH_ERRORS = ("invalid_authorization", "version_mismatch", "expired", "already_used")


def upgrade_auth(st, code: str, version: str, *, post: Callable = post_json, now: Callable = time.time) -> dict:
    """Signed `POST /v1/host/upgrade-auth` (PROTOCOL §7, F12): spend the owner's upgrade authorization code (from the
    upgrade email) for exactly `version`. The caller has already checked the code's shape and that `version` is the newest
    published release. Returns {"status": "ok" | "unlinked" | "invalid_authorization" | "version_mismatch" | "expired" |
    "already_used" | "not_bound" | "rate_limited" | "unsupported" (bare 404: the server predates upgrade-auth) | "unreachable" |
    "fail", "http": …} (+ "version": the grant's version on a mismatch,
    "expires_at" on ok). Never raises. The code is never logged, printed or stored; nothing in the answer is acted on but
    its status — the answer cannot pick what gets installed (the caller pins `version`)."""
    cloud = read_cloud(st)
    if not cloud:
        return {"status": "unlinked", "http": ""}
    try:
        url = api_url(st, cloud) + "/v1/host/upgrade-auth"
    except CloudError as e:
        return {"status": "fail", "http": e.kind}
    inner = {"v": 1, "t": "upgrade_auth", "channel": channel_of(st), "ts": int(now()), "code": code, "version": version}
    try:
        status, obj = post(url, envelope(CTX_UPGRADE_AUTH, inner, st.signing_key()))
    except CloudError as e:
        st.log("upgrade_auth", result="unreachable", status=e.kind)
        return {"status": "unreachable", "http": e.kind}
    err = parse_error(obj)
    out: dict = {"http": status_class(status)}
    if status == 200 and obj.get("ok") is True and obj.get("version") == version:
        out["status"] = "ok"
        exp = _int(obj.get("expires_at"), 0, MAX_SEQ)
        if exp is not None:
            out["expires_at"] = exp
    elif status in (404, 409, 410) and err in UPGRADE_AUTH_ERRORS:
        out["status"] = err
        v = obj.get("version")
        if err == "version_mismatch" and isinstance(v, str) and _PRINTABLE_URL.fullmatch(v) and len(v) <= 32:
            out["version"] = v
    elif status == 404 and err is None:
        out["status"] = "unsupported"      # a bare 404 (no JSON error): a ≤ 0.14 server has no upgrade-auth route yet
    elif status == 404:
        out["status"] = "invalid_authorization"
    elif status == 403 and err == "not_bound":
        out["status"] = "not_bound"
    elif status == 410:
        out["status"] = "expired"
    elif status == 429:
        out["status"] = "rate_limited"
    elif status >= 500:
        out["status"] = "unreachable"
    else:
        out["status"] = "fail"
    st.log("upgrade_auth", result=out["status"], status=out["http"])
    return out


# ------------------------------------------------------------------ sync (A3.1: the owner's unbind requests)
class SyncResult(NamedTuple):
    kind: str                       # ok | unbound | fail | unlinked
    status: str = ""
    unbind: tuple = ()              # ((request id, device id), ...) — requests only; serve decides
    name: tuple = ("none", None)    # A3.2 agent_name: ("none", None) absent/null · ("ok", name) · ("bad", None) refused


def parse_sync_name(obj: dict) -> tuple:
    """The sync answer's agent_name → ("none", None) when absent / null, ("ok", name) when it passes §1, else ("bad", None).
    A bad name never fails the sync (the unbind requests are still processed); serve keeps its local name."""
    if obj.get("agent_name") is None:
        return ("none", None)
    n = parse_agent_name(obj.get("agent_name"))
    return ("ok", n) if n is not None else ("bad", None)


def parse_sync(obj: dict) -> tuple | None:
    """200 answer → ((id, device), ...), ≤ 10, ids unique; anything else in it is ignored, a malformed list → None."""
    items = obj.get("unbind")
    if not isinstance(items, list) or len(items) > MAX_SYNC_ITEMS:
        return None
    out, seen = [], set()
    for x in items:
        if not isinstance(x, dict):
            return None
        rid, dev = x.get("id"), x.get("device")
        if not (isinstance(rid, str) and _UNBIND_ID.fullmatch(rid) and isinstance(dev, str) and _DEVICE_ID.fullmatch(dev)) or rid in seen:
            return None
        seen.add(rid)
        out.append((rid, dev))
    return tuple(out)


def send_sync(st, results: list[tuple[str, str]], *, post: Callable = post_json, now: Callable = time.time) -> SyncResult:
    """One signed sync (blocking): report what was done with earlier requests, get the pending ones. Never touches devices."""
    cloud = read_cloud(st)
    if not cloud:
        return SyncResult("unlinked")
    try:
        url = api_url(st, cloud) + "/v1/host/sync"
    except CloudError as e:
        return SyncResult("fail", e.kind)
    res = [{"id": i, "result": r} for i, r in results[:MAX_SYNC_ITEMS] if _UNBIND_ID.fullmatch(i) and r in UNBIND_RESULTS]
    inner = {"v": 1, "t": "sync", "channel": channel_of(st), "ts": int(now()), "results": res}
    try:
        status, obj = post(url, envelope(CTX_SYNC, inner, st.signing_key()))
    except CloudError as e:
        return SyncResult("fail", e.kind)
    if status == 200:
        req = parse_sync(obj)
        return SyncResult("ok", "200", req, parse_sync_name(obj)) if req is not None else SyncResult("fail", "bad_response")
    if status == 403 and parse_error(obj) == "not_bound":
        return SyncResult("unbound", status_class(status))
    return SyncResult("fail", status_class(status))


# ------------------------------------------------------------------ rename (A3.2: host-initiated Agent rename)
class RenameResult(NamedTuple):
    kind: str                       # ok | name_taken | bad_name | not_bound | rate_limited | unlinked | unreachable | fail
    name: str | None = None         # ok: the canonical name the Dashboard stored (or ours when its echo is unusable)
    suggestions: tuple = ()         # name_taken: ≤ 3 free names, each valid per §1
    status: str = ""


def parse_suggestions(obj: dict) -> tuple:
    items = obj.get("suggestions")
    if not isinstance(items, list):
        return ()
    out = []
    for x in items[:10]:
        n = parse_agent_name(x)
        if n is not None and n not in out:
            out.append(n)
    return tuple(out[:MAX_SUGGESTIONS])


def rename(st, name: str, *, post: Callable = post_json, now: Callable = time.time) -> RenameResult:
    """Ask the Dashboard to rename this host's Agent (signed, `/v1/host/rename`). Blocking. Writes nothing: the caller stores
    the name locally only on "ok". A malformed name is refused here, before anything is sent."""
    if agent_name_problem(name):
        return RenameResult("bad_name")
    name = normalise_agent_name(name)
    cloud = read_cloud(st)
    if not cloud:
        return RenameResult("unlinked")
    try:
        url = api_url(st, cloud) + "/v1/host/rename"
    except CloudError as e:
        return RenameResult("fail", status=e.kind)
    inner = {"v": 1, "t": "rename", "channel": channel_of(st), "ts": int(now()), "name": name}
    try:
        status, obj = post(url, envelope(CTX_RENAME, inner, st.signing_key()))
    except CloudError as e:
        return RenameResult("unreachable", status=e.kind)
    err = parse_error(obj)
    if status == 200 and obj.get("ok") is True:
        echo = parse_agent_name(obj.get("name"))
        return RenameResult("ok", echo or name, status="200")
    if status == 409 and err == "name_taken":
        return RenameResult("name_taken", suggestions=parse_suggestions(obj), status=status_class(status))
    if status == 400 and err in ("bad_name", "name_required"):
        return RenameResult("bad_name", status=status_class(status))
    if status == 403 and err == "not_bound":
        return RenameResult("not_bound", status=status_class(status))
    if status == 429:
        return RenameResult("rate_limited", status=status_class(status))
    if status >= 500:
        return RenameResult("unreachable", status=status_class(status))
    return RenameResult("fail", status=status_class(status) if status != 200 else "bad_response")


# ------------------------------------------------------------------ peer certificate (P71, PROTOCOL §17.3)
_MBOX = re.compile(r"[A-Za-z0-9_-]{22}")
_CERT = re.compile(r"[A-Za-z0-9_-]{1,1400}\.[A-Za-z0-9_-]{86}")


class PeerCertResult(NamedTuple):
    kind: str                       # ok | not_bound | payment_required | rate_limited | unlinked | unreachable | fail
    cert: str | None = None
    exp: int | None = None          # unix s
    status: str = ""


def parse_peer_cert(obj: dict, mbox: str, now: float) -> tuple[str, int] | None:
    """A 200 answer {"cert","exp"}: the certificate's own payload must name this mailbox and an expiry in the future (the
    signature is the relay's business — the host does not hold the cloud's public key)."""
    cert, exp = obj.get("cert"), _int(obj.get("exp"), 0, MAX_SEQ)
    if not isinstance(cert, str) or not _CERT.fullmatch(cert) or exp is None or exp <= now:
        return None
    try:
        p = json.loads(wire.unb64u(cert.split(".", 1)[0]))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(p, dict) or p.get("v") != 1 or p.get("mbox") != mbox or _int(p.get("exp"), 0, MAX_SEQ) != exp:
        return None
    return cert, exp


def peer_cert(st, mbox: str, *, post: Callable = post_json, now: Callable = time.time) -> PeerCertResult:
    """One signed `/v1/host/peer/cert` (blocking): a 7-day certificate for this host's mailbox, or why not. 403 not_bound /
    402 payment_required mean "needs a paid seat" — the caller then stays off the mailbox and asks again much later."""
    if not isinstance(mbox, str) or not _MBOX.fullmatch(mbox):
        return PeerCertResult("fail", status="bad_mbox")
    cloud = read_cloud(st)
    if not cloud:
        return PeerCertResult("unlinked")
    try:
        url = api_url(st, cloud) + "/v1/host/peer/cert"
    except CloudError as e:
        return PeerCertResult("fail", status=e.kind)
    t = now()
    inner = {"v": 1, "t": "peer_cert", "channel": channel_of(st), "ts": int(t), "mbox": mbox}
    try:
        status, obj = post(url, envelope(CTX_PEER_CERT, inner, st.signing_key()))
    except CloudError as e:
        return PeerCertResult("unreachable", status=e.kind)
    err = parse_error(obj)
    if status == 200:
        got = parse_peer_cert(obj, mbox, t)
        return PeerCertResult("ok", got[0], got[1], "200") if got else PeerCertResult("fail", status="bad_response")
    if status == 403:
        return PeerCertResult("not_bound", status=status_class(status))
    if status == 402:
        return PeerCertResult("payment_required", status=status_class(status))
    if status == 429:
        return PeerCertResult("rate_limited", status=status_class(status))
    if status >= 500:
        return PeerCertResult("unreachable", status=status_class(status))
    return PeerCertResult("fail", status=status_class(status) if err is None else err)


# ------------------------------------------------------------------ Agent plaza P2 (agentj/plaza.py builds and renders)
def plaza_call(st, kind: str, fields: dict, *, post: Callable = post_json, now: Callable = time.time) -> tuple[int, dict]:
    """One signed `/v1/host/plaza/<kind>` (PROTOCOL §7). Needs a Dashboard link (cloud.json): raises CloudError("unlinked")
    otherwise. Returns (status, answer object) — the caller whitelists the answer; nothing in it is ever executed, written to
    the allowlist or used as a path (plaza text is data). Transport errors raise CloudError."""
    if kind not in PLAZA_KINDS:
        raise ValueError(kind)
    cloud = read_cloud(st)
    if not cloud:
        raise CloudError("unlinked")
    url = api_url(st, cloud) + f"/v1/host/plaza/{kind}"
    inner = {"v": 1, "t": f"plaza_{kind}", "channel": channel_of(st), "ts": int(now()), **fields}
    env = envelope(CTX_PLAZA[kind], inner, st.signing_key())
    if len(json.dumps(env, separators=(",", ":"))) > MAX_ENVELOPE:
        raise CloudError("too_large")
    return post(url, env, max_response=MAX_PLAZA_RESPONSE)


# ------------------------------------------------------------------ skill & workflow plaza (protocol/PLAZA_PACKAGES.md §4; agentj/market.py)
PKG_ROUTES = ("search", "get", "mine", "installed", "like", "report", "publish")
CTX_PKG = {r: f"agentjarvis-host-plaza-pkg-{r}-v1" for r in PKG_ROUTES}   # wire strings: never renamed
MAX_BUNDLE = 2 * 1024 * 1024          # = bundle.MAX_BUNDLE: what a download may be and what an upload may carry
TRANSFER_TIMEOUT = 60
_TOKEN = re.compile(r"[A-Za-z0-9_-]{8,400}\.[A-Za-z0-9_-]{8,100}")


def plaza_pkg_call(st, route: str, fields: dict, *, post: Callable = post_json, now: Callable = time.time) -> tuple[int, dict]:
    """One signed `POST /v1/host/plaza/pkg/<route>` (context `agentjarvis-host-plaza-pkg-<route>-v1`, t = `plaza_pkg_<route>`).
    Same rules as plaza_call: needs the Dashboard link; the answer is whitelisted by the caller and never executed."""
    if route not in PKG_ROUTES:
        raise ValueError(route)
    cloud = read_cloud(st)
    if not cloud:
        raise CloudError("unlinked")
    url = api_url(st, cloud) + f"/v1/host/plaza/pkg/{route}"
    inner = {"v": 1, "t": f"plaza_pkg_{route}", "channel": channel_of(st), "ts": int(now()), **fields}
    env = envelope(CTX_PKG[route], inner, st.signing_key())
    if len(json.dumps(env, separators=(",", ":"))) > MAX_ENVELOPE:
        raise CloudError("too_large")
    return post(url, env, max_response=MAX_PLAZA_RESPONSE)


def transfer_url(st, url, kind: str) -> str:
    """A signed download (`dl`) / upload (`up`) URL from a server answer is accepted only when it is exactly
    `<the configured API base>/v1/plaza/<kind>/<token>` (base = origin + its path, e.g. https://agentj.app/api): same scheme, host and port as the API this host signs to (https;
    plain http only when that API is itself a loopback test server), no credentials, query or fragment. Else
    CloudError("refused_url") — a compromised answer cannot make the host fetch from, or upload to, anywhere else."""
    if kind not in ("dl", "up"):
        raise ValueError(kind)
    base = api_url(st, read_cloud(st))
    if not isinstance(url, str) or not _PRINTABLE_URL.fullmatch(url):
        raise CloudError("refused_url")
    u = urllib.parse.urlsplit(url)
    if u.username or u.password or u.query or u.fragment or _origin(url) != _origin(base):
        raise CloudError("refused_url")
    if u.scheme != "https" and not (u.scheme == "http" and (u.hostname or "").lower() in LOOPBACK):
        raise CloudError("refused_url")
    prefix = urllib.parse.urlsplit(base).path.rstrip("/") + f"/v1/plaza/{kind}/"   # under the API's own path (…/api)
    if not u.path.startswith(prefix) or not _TOKEN.fullmatch(u.path[len(prefix):]):
        raise CloudError("refused_url")
    return url


def _opener(url: str):
    handlers: list = [_NoRedirect()]
    if (urllib.parse.urlsplit(url).hostname or "").lower() in LOOPBACK:
        handlers.append(urllib.request.ProxyHandler({}))
    else:
        handlers.append(urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    return urllib.request.build_opener(*handlers)


def _transport_error(e: Exception) -> CloudError:
    if isinstance(e, urllib.error.URLError):
        return CloudError("timeout" if isinstance(e.reason, (socket.timeout, TimeoutError)) else "network")
    if isinstance(e, (socket.timeout, TimeoutError)):
        return CloudError("timeout")
    return CloudError("network")


def http_get(url: str, *, max_bytes: int = MAX_BUNDLE, timeout: float = TRANSFER_TIMEOUT) -> tuple[int, bytes]:
    """Plain GET (TLS verified, no redirects, no proxy for loopback). A body over `max_bytes` → CloudError("too_large")."""
    req = urllib.request.Request(url, method="GET", headers={"accept": "application/octet-stream", "user-agent": AGENT})
    try:
        with _opener(url).open(req, timeout=timeout) as r:
            status, raw = r.status, r.read(max_bytes + 1)
    except urllib.error.HTTPError as e:
        return e.code, b""
    except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as e:
        raise _transport_error(e) from None
    if len(raw) > max_bytes:
        raise CloudError("too_large")
    return status, raw


def http_put(url: str, body: bytes, *, timeout: float = TRANSFER_TIMEOUT) -> tuple[int, dict]:
    """Plain PUT of a bundle (application/octet-stream). → (status, JSON answer or {})."""
    req = urllib.request.Request(url, data=body, method="PUT", headers={
        "content-type": "application/octet-stream", "accept": "application/json", "user-agent": AGENT})
    try:
        with _opener(url).open(req, timeout=timeout) as r:
            status, raw = r.status, r.read(MAX_RESPONSE + 1)
    except urllib.error.HTTPError as e:
        status = e.code
        try:
            raw = e.read(MAX_RESPONSE + 1)
        except Exception:
            raw = b""
    except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as e:
        raise _transport_error(e) from None
    obj: dict = {}
    if raw and len(raw) <= MAX_RESPONSE:
        try:
            parsed = json.loads(raw)
            obj = parsed if isinstance(parsed, dict) else {}
        except (ValueError, RecursionError):
            obj = {}
    return status, obj


def plaza_download(st, url, *, get: Callable = http_get) -> tuple[int, bytes]:
    """GET a signed download URL (transfer_url-checked) → (status, bundle bytes ≤ 2 MiB)."""
    url = transfer_url(st, url, "dl")
    status, raw = get(url, max_bytes=MAX_BUNDLE, timeout=TRANSFER_TIMEOUT)
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_BUNDLE:
        raise CloudError("too_large")
    return status, bytes(raw)


def plaza_upload(st, url, body: bytes, *, put: Callable = http_put) -> tuple[int, dict]:
    """PUT a bundle (≤ 2 MiB) to a signed upload URL (transfer_url-checked)."""
    url = transfer_url(st, url, "up")
    if len(body) > MAX_BUNDLE:
        raise CloudError("too_large")
    return put(url, body, timeout=TRANSFER_TIMEOUT)
