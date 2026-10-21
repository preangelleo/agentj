"""P428 §1: guided Google authorization through Agent J's pinned gog — `agentj google connect|disconnect`.

Only the existing flow: our gog 0.43.0 in its isolated GOG_HOME and its own keyring namespace (google.KEYRING_SERVICE, F3),
the owner's own Desktop OAuth client, browser consent on the Agent J host. The host never opens a client path (F4): the CLI
reads the file with the caller's own permissions and sends its bytes (`client_b64`); the host checks the JSON shape, hands the
bytes to gog through stdin (gog keeps them in its keyring) and zeroes them. The token stays in gog's store and is never read,
exported or printed. The e-mail address is only ever an argv item of `gog auth add` / `gog auth remove`: never stored, logged
or returned — only its sha256 (`acct`), so disconnect removes our account and nothing else.
Progress is `<state>/google/state.json` → `connections[purpose] = {step, at, reason?, acct?}`.
gog has no events-only Calendar scope, so every Calendar call Agent J makes goes through `gog_calendar` (exact command list).
"""
from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import threading

from . import google as g

CONNECTABLE = ("calendar-read", "calendar-write")
STEPS = ("client_needed", "client_saved", "oauth_consent_required", "token_verified", "ready")
REASONS = ("consent_timeout", "consent_failed", "api_disabled", "probe_failed", "client_file_refused", "not_installed")
CLIENT_MAX = 64 << 10
B64_MAX = 4 * (CLIENT_MAX // 3 + 1)
_B64 = re.compile(r"[A-Za-z0-9+/]+={0,2}")
_HEX = re.compile(r"[0-9a-f]{64}")
CONSENT = "5m"
CONSENT_SECS = 330                      # gog's own --timeout 5m, plus a margin before we stop waiting
CAL_COMMANDS = "calendar.events,calendar.create,calendar.update,calendar.delete,calendar.calendars"
_EMAIL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@[A-Za-z0-9][A-Za-z0-9.-]{0,189}\.[A-Za-z]{2,63}")
_RUNNING: dict[str, threading.Thread] = {}
_GUARD = threading.Lock()
CONSOLE = [
    ("新建（或选）你自己的 Google Cloud 项目", "create (or pick) your own Google Cloud project",
     "https://console.cloud.google.com/projectcreate"),
    ("启用 Google Calendar API", "enable the Google Calendar API",
     "https://console.cloud.google.com/apis/library/calendar-json.googleapis.com"),
    ("OAuth 同意屏幕：External，发布状态切到 In production（不是提交验证）",
     "OAuth consent screen: External, publishing status In production (not Submit for verification)",
     "https://console.cloud.google.com/apis/credentials/consent"),
    ("凭据 → 创建 OAuth 客户端 ID → 桌面应用 → 下载 JSON（存在这台电脑上）",
     "Credentials → Create OAuth client ID → Desktop app → Download JSON (onto this computer)",
     "https://console.cloud.google.com/apis/credentials"),
]


def clean(conns) -> dict:
    """The record keeps only known purposes, known steps, an integer time and a fixed reason word (v1 records: none)."""
    return {k: {"step": c["step"], "at": c.get("at") if isinstance(c.get("at"), int) else 0,
                **({"reason": c["reason"]} if c.get("reason") in REASONS else {}),
                **({"acct": c["acct"]} if isinstance(c.get("acct"), str) and _HEX.fullmatch(c["acct"]) else {})}
            for k, c in (conns if isinstance(conns, dict) else {}).items()
            if k in g.PURPOSES and isinstance(c, dict) and c.get("step") in STEPS}


def effective(conns: dict, pid: str) -> dict | None:
    """A Calendar purpose's own connection; calendar-read is also served by a ready calendar-write grant (full Calendar)."""
    if pid not in CONNECTABLE:
        return None
    c = conns.get(pid)
    if pid == "calendar-read" and (c or {}).get("step") != "ready" and (conns.get("calendar-write") or {}).get("step") == "ready":
        return conns["calendar-write"]
    return c


def projection(row: dict) -> tuple[str, dict]:
    """P86 status + check for one purpose row: `ready` only for a verified Calendar connection (7-day expiry)."""
    step, ver = row.get("step"), row.get("verified_at")
    if step == "ready" and ver:
        from datetime import datetime
        exp = g._iso(datetime.fromisoformat(ver.replace("Z", "+00:00")).timestamp() + g.CLI_TTL)
        return "ready", {"status": "ok", "last_verified_at": ver, "expires_at": exp}
    if step in ("client_saved", "oauth_consent_required", "token_verified"):
        return "unverified", {"status": "auth_required", "last_verified_at": None, "expires_at": None}
    return ("discovered" if row["api_state"] == "existing_unverified" else "unavailable",
            {"status": "unsupported", "last_verified_at": None, "expires_at": None})


def _key(st) -> str:
    return str(g.root(st))


def running(st) -> bool:
    with _GUARD:
        t = _RUNNING.get(_key(st))
        return bool(t and t.is_alive())


def _acct(account: str) -> str:
    """F3: what Agent J keeps of the connected account — its sha256, never the address."""
    return hashlib.sha256(account.strip().casefold().encode()).hexdigest()


def _set(st, purpose: str, step: str, reason: str | None = None, acct: str | None = None) -> dict:
    with g._lock(st):
        rec = g.read(st)
        acct = acct or ((rec.get("connections") or {}).get(purpose) or {}).get("acct")
        conn = {"step": step, "at": g._now(), **({"reason": reason} if reason else {}), **({"acct": acct} if acct else {})}
        rec["connections"] = {**(rec.get("connections") or {}), purpose: conn}
        g._write(st, rec)
    st.log("google_connect", kind=purpose, result=step, reason=reason)
    return conn


def _conn(st, purpose: str) -> dict:
    return (g.read(st).get("connections") or {}).get(purpose) or {"step": "client_needed"}


def _out(purpose: str, conn: dict, **extra) -> dict:
    res = {"ok": not conn.get("reason"), "purpose": purpose, "step": conn["step"]}
    if conn.get("reason"):
        res["why"] = conn["reason"]
    return {**res, **extra}


def read_client(path) -> bytearray | None:
    """The CLI side (the caller's own permissions, F4): regular file, not a symlink, ours, 1 B–64 KiB, read once through a
    no-follow fd. The host never opens a client path."""
    if not isinstance(path, str) or not path.startswith("/") or len(path) > 4096 or "\0" in path:
        return None
    try:
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() \
                or not 0 < info.st_size <= CLIENT_MAX:
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        return None
    try:
        f = os.fstat(fd)
        if not stat.S_ISREG(f.st_mode) or (f.st_dev, f.st_ino) != (info.st_dev, info.st_ino):
            return None
        buf = bytearray()
        while len(buf) <= CLIENT_MAX:
            chunk = os.read(fd, CLIENT_MAX + 1 - len(buf))
            if not chunk:
                break
            buf += chunk
        return buf if 0 < len(buf) <= CLIENT_MAX else None
    except OSError:
        return None
    finally:
        os.close(fd)


def client_ok(buf) -> bool:
    """A Desktop OAuth client JSON: an object with `installed.client_id` (a string). Nothing of it is logged or returned."""
    try:
        doc = json.loads(bytes(buf))
        inst = doc.get("installed") if isinstance(doc, dict) else None
        return isinstance(inst, dict) and isinstance(inst.get("client_id"), str) and bool(inst["client_id"])
    except (ValueError, TypeError):
        return False


def client_from(req: dict) -> bytearray | None:
    """`client_b64` of an elevate / in-process request → the bytes (bytearray, zeroed by `connect`), or None."""
    v = req.get("client_b64")
    try:
        return bytearray(base64.b64decode(v, validate=True)) if isinstance(v, str) else None
    except (binascii.Error, ValueError):
        return None


def _gog(st, argv: list[str], timeout: float, data: bytes = b"") -> tuple[int, str, str]:
    """Run OUR gog → (code, stdout, stderr), for this module's own classification only: never to a caller or a log."""
    g._private_dir(g.gog_home(st))
    try:
        p = subprocess.run([str(g.binary(st)), *argv], capture_output=True, timeout=timeout, env=g.env(st), input=data)
    except subprocess.TimeoutExpired:
        return -2, "", ""
    except OSError:
        return -1, "", ""
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def gog_calendar(st, argv: list[str], timeout: float = g.PROBE_TIMEOUT, allow: str = CAL_COMMANDS) -> tuple[int, object]:
    """Every Calendar call Agent J makes: `-a auto --json --no-input --enable-commands-exact <calendar commands>`.
    Returns (exit code, parsed JSON or None); gog's stdout / stderr are never propagated."""
    g._private_dir(g.gog_home(st))
    try:
        p = subprocess.run([str(g.binary(st)), "-a", "auto", "--json", "--no-input", "--enable-commands-exact", allow,
                            *argv], capture_output=True, timeout=timeout, env=g.env(st), stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return -2, None
    except OSError:
        return -1, None
    try:
        return p.returncode, json.loads(p.stdout) if p.stdout.strip() else None
    except ValueError:
        return p.returncode, None


def _probe(st, purpose: str, acct: str | None = None) -> dict:
    code, body = gog_calendar(st, ["calendar", "calendars", "--max", "1"], allow="calendar.calendars")
    if code == 0:
        _set(st, purpose, "token_verified", acct=acct)
        return _set(st, purpose, "ready")
    txt = json.dumps(body) if body is not None else ""
    disabled = code == 6 or any(w in txt for w in ("accessNotConfigured", "SERVICE_DISABLED", "has not been used", "is disabled"))
    return _set(st, purpose, "oauth_consent_required", "api_disabled" if disabled else "probe_failed", acct=acct)


def _consent(st, purpose: str, account: str) -> None:
    try:
        code, out, err = _gog(st, ["auth", "add", account, *g.PURPOSES[purpose]["gog"], "--force-consent", "--timeout",
                                   CONSENT], CONSENT_SECS)
        if code == 0:
            for other in _accounts(st) or []:      # one Google account (P428 scope): `-a auto` needs exactly one token;
                if isinstance(other, str) and other.casefold() != account.casefold() and _EMAIL.fullmatch(other):
                    _gog(st, ["--no-input", "auth", "remove", other, "-y"], g.PROBE_TIMEOUT)   # our keyring namespace only
            _probe(st, purpose, _acct(account))
            return
        low = (out + err).lower()
        timed = code == -2 or "timeout" in low or "timed out" in low or "deadline" in low
        _set(st, purpose, "oauth_consent_required", "consent_timeout" if timed else "consent_failed")
    except Exception:           # never let a thread die silently with the step stuck at "running"
        with contextlib.suppress(Exception):
            _set(st, purpose, "oauth_consent_required", "consent_failed")


def connect(st, purpose, client=None, account=None, wait: bool = False) -> dict:
    """`client`: the client JSON's bytes (a bytearray, zeroed here whatever happens) — never a path (F4)."""
    try:
        return _connect(st, purpose, client, account, wait)
    finally:
        if isinstance(client, bytearray):
            client[:] = b"\0" * len(client)


def _connect(st, purpose, client, account, wait: bool) -> dict:
    if purpose not in CONNECTABLE:
        return {"ok": False, "why": "purpose_not_connectable", "connectable": list(CONNECTABLE)}
    if not g.installed(st):
        return _out(purpose, {**_conn(st, purpose), "reason": "not_installed"})
    p = g.PURPOSES[purpose]
    info = {"scopes": list(p["scopes"]), "scope_note": p.get("scope_note")}
    if client is not None:
        code = -1
        if isinstance(client, (bytes, bytearray)) and 0 < len(client) <= CLIENT_MAX and client_ok(client):
            code = _gog(st, ["--no-input", "--json", "auth", "credentials", "set", "-"], g.PROBE_TIMEOUT, bytes(client))[0]
        if code != 0:               # a wrong file never downgrades a connection that already got further
            cur = _conn(st, purpose)
            if cur["step"] == "client_needed":
                cur = _set(st, purpose, "client_needed", "client_file_refused")
            return _out(purpose, {**cur, "reason": "client_file_refused"})
        conn = _set(st, purpose, "client_saved")
        if account is None:
            return _out(purpose, conn, **info, delete_download="the downloaded JSON is stored now; you may delete it",
                        next=f"agentj google connect --purpose {purpose} --account EMAIL")
    if account is not None:
        if not isinstance(account, str) or len(account) > 254 or not _EMAIL.fullmatch(account):
            return {"ok": False, "purpose": purpose, "why": "shape", "detail": "account: one Google e-mail address"}
        with _GUARD:
            t = _RUNNING.get(_key(st))
            if t and t.is_alive():
                return {"ok": False, "purpose": purpose, "why": "busy", "step": "oauth_consent_required"}
            _set(st, purpose, "oauth_consent_required")
            t = threading.Thread(target=_consent, args=(st, purpose, account), name="google-consent", daemon=True)
            _RUNNING[_key(st)] = t
            t.start()
        del account
        if wait:
            t.join()
            return _out(purpose, _conn(st, purpose), **info)
        return _out(purpose, _conn(st, purpose), **info, running=True,
                    owner=["a Google sign-in page opens in the browser on this computer: sign in and do 2-step there",
                           "on the unverified-app warning check it is YOUR project, then Advanced → Continue (yourself)",
                           "approve the Calendar permission"],
                    next=f"agentj google connect --status --purpose {purpose}")
    conn = _conn(st, purpose)
    if running(st):
        return _out(purpose, conn, running=True)
    if conn["step"] in ("ready", "token_verified") or conn.get("reason") in ("api_disabled", "probe_failed"):
        return _out(purpose, _probe(st, purpose), **info)       # re-check after enabling the API / refresh `ready`
    if conn["step"] == "client_needed":
        return _out(purpose, conn, **info, owner=[{"zh": z, "en": e, "url": u} for z, e, u in CONSOLE],
                    next=f"agentj google connect --purpose {purpose} --client-file /absolute/path/client.json")
    return _out(purpose, conn, **info, next=f"agentj google connect --purpose {purpose} --account EMAIL")


def connect_status(st, purpose=None) -> dict:
    if purpose is not None and purpose not in g.PURPOSES:
        return {"ok": False, "why": "shape", "detail": "purpose: " + " | ".join(g.PURPOSES)}
    conns = g.read(st).get("connections") or {}
    run = running(st)
    out = {}
    for pid in CONNECTABLE if purpose is None else (purpose,):
        c = {k: v for k, v in (conns.get(pid) or {"step": "client_needed"}).items() if k != "acct"} if pid in CONNECTABLE \
            else {"step": "not_connectable"}
        if run and c["step"] == "oauth_consent_required" and not c.get("reason"):
            c["running"] = True
        out[pid] = c
    return {"ok": True, "connections": out, "connectable": list(CONNECTABLE)}


def _accounts(st) -> list[str] | None:
    code, out, _ = _gog(st, ["--json", "--no-input", "auth", "list"], g.PROBE_TIMEOUT)
    try:
        rows = json.loads(out) if code == 0 else None
    except ValueError:
        return None
    accs = rows.get("accounts") if isinstance(rows, dict) else None
    return [a if isinstance(a, str) else a.get("email") for a in accs or [] if isinstance(a, (str, dict))] \
        if isinstance(accs, list) else None


def disconnect(st, purpose) -> dict:
    """Removes the account Agent J connected (sha256 match, F3) from OUR gog's isolated store and keyring namespace (one
    Calendar grant serves both calendar purposes) and clears both connections; no match → nothing removed (`removed: 0`).
    The owner's client stays in gog's keyring; nothing outside GOG_HOME / our keyring namespace is touched."""
    if purpose not in CONNECTABLE:
        return {"ok": False, "why": "purpose_not_connectable", "connectable": list(CONNECTABLE)}
    if not g.installed(st):
        return {"ok": False, "purpose": purpose, "why": "not_installed"}
    if running(st):
        return {"ok": False, "purpose": purpose, "why": "busy", "step": "oauth_consent_required"}
    accs = _accounts(st)
    if accs is None:
        return {"ok": False, "purpose": purpose, "why": "probe_failed"}
    conns = g.read(st).get("connections") or {}
    mine = {c.get("acct") for k, c in conns.items() if k in CONNECTABLE and isinstance(c, dict) and c.get("acct")}
    accs = [a for a in accs if isinstance(a, str) and _EMAIL.fullmatch(a) and _acct(a) in mine]
    removed = 0
    for a in accs:
        removed += _gog(st, ["--no-input", "auth", "remove", a, "-y"], g.PROBE_TIMEOUT)[0] == 0
    with g._lock(st):
        rec = g.read(st)
        rec["connections"] = {k: v for k, v in (rec.get("connections") or {}).items() if k not in CONNECTABLE}
        g._write(st, rec)
    st.log("google_connect", kind=purpose, result="disconnected", count=removed)
    return {"ok": removed == len(accs), "purpose": purpose, "step": "client_needed", "removed": removed,
            **({} if removed == len(accs) else {"why": "remove_failed"})}


def request_shape(req: dict) -> str | None:
    """elevate.sock `{"t":"google","op":…}`: fixed shapes. Returns a problem or None."""
    op = req.get("op")
    if op not in g.OPS:
        return "op: " + " | ".join(g.OPS)
    if "client_file" in req:                    # F4: the host never opens a path sent over the socket
        return "client_file: send the file's bytes (client_b64), never a path"
    extra = set(req) - {"t", "op", "purpose", "client_b64", "account"}
    if op not in ("connect", "connect_status", "disconnect"):
        return "fields" if extra or set(req) & {"purpose", "client_b64", "account"} else None
    pur = req.get("purpose")
    if extra or not (isinstance(pur, str) and pur in g.PURPOSES or (op == "connect_status" and pur is None)):
        return "purpose: " + " | ".join(g.PURPOSES)
    if op != "connect" and set(req) & {"client_b64", "account"}:
        return "fields"
    if req.get("account") is not None and not (isinstance(req["account"], str) and 0 < len(req["account"]) <= 4096):
        return "account: a string"
    b = req.get("client_b64")
    if b is not None and not (isinstance(b, str) and 0 < len(b) <= B64_MAX and _B64.fullmatch(b)):
        return "client_b64: base64 of the client JSON (≤ 64 KiB)"
    return None


def cli_request(a) -> tuple[dict | None, dict]:
    """`connect [--status]` / `disconnect`: one purpose; the client file is read here with the caller's own permissions (a
    no-follow open) and its bytes are sent (F4), so the host never opens a path and never reads more than the session could."""
    pur = a.purpose or []
    if a.action == "connect" and a.connect_status:
        if len(pur) > 1:
            return {"ok": False, "why": "shape", "detail": "at most one --purpose"}, {}
        return None, {"t": "google", "op": "connect_status", "purpose": pur[0] if pur else None}
    if len(pur) != 1:
        return {"ok": False, "why": "shape", "detail": "exactly one --purpose (calendar-write)"}, {}
    req = {"t": "google", "op": a.action, "purpose": pur[0]}
    if a.action == "connect":
        if a.client_file:
            buf = read_client(os.path.abspath(os.path.expanduser(a.client_file)))
            if buf is None:
                return {"ok": False, "purpose": pur[0], "why": "client_file_refused"}, {}
            req["client_b64"] = base64.b64encode(bytes(buf)).decode()
            buf[:] = b"\0" * len(buf)
        if a.account:
            req["account"] = a.account
    elif a.client_file or a.account:
        return {"ok": False, "why": "shape", "detail": "disconnect takes only --purpose"}, {}
    return None, req


def add_args(p) -> None:
    p.add_argument("--status", dest="connect_status", action="store_true",
                   help="connect：只看授权进度 / connect: show the authorization progress only")
    p.add_argument("--client-file", metavar="PATH",
                   help="connect：主人下载的桌面 OAuth 客户端 JSON 路径（交给 gog 保存，不打印）/ connect: path of the "
                        "downloaded Desktop OAuth client JSON (handed to gog, never printed)")
    p.add_argument("--account", metavar="EMAIL",
                   help="connect：要授权的 Google 账号（只交给 gog，不保存）/ connect: the Google account to authorize "
                        "(passed to gog only, never stored)")


def execute(op: str, st, req: dict) -> dict:
    if op == "connect":
        return connect(st, req.get("purpose"), _client(req), req.get("account"))
    return connect_status(st, req.get("purpose")) if op == "connect_status" else disconnect(st, req.get("purpose"))


def cli_local(st, req: dict, quiet: bool) -> dict:
    """serve is not running: this CLI process owns the state, so it waits for the consent thread itself."""
    if req["op"] != "connect":
        return execute(req["op"], st, req)
    if req.get("account") and not quiet:
        print("等待你在这台电脑的浏览器里完成 Google 授权（≤5 分钟）/ waiting for the Google consent in this computer's browser "
              "(≤5 min)", file=sys.stderr)
    return connect(st, req["purpose"], _client(req), req.get("account"), wait=True)


def _client(req: dict):
    """None when the request carries no client; b"" (refused by connect) when its client_b64 does not decode."""
    if "client_b64" not in req:
        return None
    buf = client_from(req)
    return buf if buf is not None else b""
