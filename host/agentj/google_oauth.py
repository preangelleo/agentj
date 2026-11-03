"""#436: Agent J's own local Google OAuth adapter (the planned 0.18.1 adapter) for purposes gog 0.43.0 cannot serve with
their minimum scope: youtube-publish (youtube.upload + youtube.readonly) and search-console-read (webmasters.readonly).

Standard library only. Desktop OAuth on the Agent J host: the owner's own Desktop client (bytes from the CLI, F4), PKCE S256,
a random `state`, a loopback listener on 127.0.0.1 with a random port that accepts exactly ONE callback whose state matches
(others get 400; after it the listener is closed), CONSENT_SECS like the Calendar flow. The code is exchanged at once; the
refresh token is kept in `<state>/google/oauth/<purpose>/token.json` (0600, directory 0700), the client in `client.json`
next to it. Access tokens live only in this process's memory. Nothing here prints, logs or returns a token, code, verifier,
state, callback URL, client secret or e-mail address (only google_connect's sha256 `acct`).
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import http.server
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import google as g

PURPOSES = ("youtube-publish", "search-console-read")
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com"           # YouTube Data API v3 and Search Console (webmasters/v3) share this host
PROBES = {"youtube-publish": "/youtube/v3/channels?part=id&mine=true", "search-console-read": "/webmasters/v3/sites"}
HTTP_TIMEOUT = 60
_TOKENS: dict[str, tuple[str, float]] = {}   # purpose dir → (access token, expiry): memory only
_TL = threading.Lock()


def pdir(st, purpose: str):
    return g.root(st) / "oauth" / purpose


def _dir(st, purpose: str):
    g._private_dir(g.root(st))
    g._private_dir(g.root(st) / "oauth")
    return g._private_dir(pdir(st, purpose))


def _load(st, purpose: str, name: str) -> dict | None:
    p = pdir(st, purpose) / name
    try:
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        return None
    try:
        doc = json.loads(os.read(fd, 1 << 16))
    except (OSError, ValueError):
        return None
    finally:
        os.close(fd)
    return doc if isinstance(doc, dict) else None


def _save(st, purpose: str, name: str, doc: dict) -> None:
    st.write_private(_dir(st, purpose) / name, json.dumps(doc).encode())


def save_client(st, purpose: str, buf) -> bool:
    """The Desktop client JSON's `installed` id / secret (shape already checked by google_connect.client_ok), 0600."""
    try:
        inst = json.loads(bytes(buf))["installed"]
        doc = {"client_id": inst["client_id"], **({"client_secret": inst["client_secret"]}
                                                  if isinstance(inst.get("client_secret"), str) else {})}
    except (ValueError, KeyError, TypeError):
        return False
    _save(st, purpose, "client.json", doc)
    return True


def has_client(st, purpose: str) -> bool:
    c = _load(st, purpose, "client.json")
    return bool(c and isinstance(c.get("client_id"), str))


# ------------------------------------------------------------------ HTTP (TLS straight to Google; tests patch the URLs)
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is returned as its 3xx, never followed: the Authorization header goes to the URL we named only."""

    def redirect_request(self, *_a, **_k):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def call(method: str, url: str, *, data=None, headers: dict | None = None, timeout: float = HTTP_TIMEOUT):
    """→ (status, headers dict lower-case, body bytes); (-1, {}, b"") when Google is unreachable. Never raises, never logs."""
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        with contextlib.suppress(Exception):
            return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, e.read()
        return e.code, {}, b""
    except (urllib.error.URLError, OSError, ValueError):
        return -1, {}, b""


def _json(body: bytes):
    try:
        return json.loads(body) if body else None
    except ValueError:
        return None


def _form(d: dict) -> bytes:
    return urllib.parse.urlencode(d).encode()


# ------------------------------------------------------------------ consent: PKCE + one loopback callback
def _open(st, url: str) -> None:
    """The owner's desktop browser on this computer (like gog): xdg-open / open with gog's allowlisted environment."""
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([opener, url], env=g.env(st), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


class _Once(http.server.HTTPServer):
    allow_reuse_address = False

    def __init__(self, want_state: str):
        self.want, self.got, self.refused, self.deadline = want_state, None, 0, time.monotonic() + 5
        super().__init__(("127.0.0.1", 0), _Callback)

    def handle_error(self, *_a):          # a cut / broken connection: nothing printed (the request line holds the code)
        return


class _Callback(http.server.BaseHTTPRequestHandler):
    """Round 1 (L3): one connection may not hold the consent window — 5 s per read, and cut at the consent deadline."""
    timeout = 5

    def log_message(self, *_a):          # the request line holds the code: never logged
        return

    def setup(self):
        left = max(0.05, self.server.deadline - time.monotonic())
        self.timeout = min(5.0, left)
        super().setup()
        self._dog = threading.Timer(left, self._cut)
        self._dog.daemon = True
        self._dog.start()

    def _cut(self):
        with contextlib.suppress(OSError):
            self.connection.shutdown(socket.SHUT_RDWR)

    def finish(self):
        self._dog.cancel()
        with contextlib.suppress(OSError):
            super().finish()

    def do_GET(self):
        srv = self.server
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        one = {k: v[0] for k, v in q.items() if len(v) == 1}
        ok = srv.got is None and urllib.parse.urlsplit(self.path).path == "/" and \
            secrets.compare_digest(one.get("state", ""), srv.want)
        if not ok:
            srv.refused += 1
            self._page(400, "Agent J: this sign-in link is not the one Agent J opened. Close this tab.")
            return
        srv.got = {"code": one.get("code"), "error": one.get("error"), "scope": one.get("scope")}
        self._page(200, "Agent J: Google sign-in done. You can close this tab.")

    def _page(self, code: int, text: str):
        body = text.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def consent(st, purpose: str, account: str, secs: float) -> tuple[str | None, list[str]]:
    """Browser consent for one purpose → (reason word or None, granted scopes). The refresh token is stored on success."""
    client = _load(st, purpose, "client.json")
    if not client:
        return "client_file_refused", []
    want = list(g.PURPOSES[purpose]["scopes"])
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(32)
    srv = _Once(state)
    try:
        redirect = f"http://127.0.0.1:{srv.server_address[1]}/"
        _open(st, AUTH_URL + "?" + urllib.parse.urlencode({
            "client_id": client["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": " ".join(want),
            "state": state, "code_challenge": challenge, "code_challenge_method": "S256", "access_type": "offline",
            "prompt": "consent", "login_hint": account, "include_granted_scopes": "false"}))
        deadline = srv.deadline = time.monotonic() + secs
        while srv.got is None:
            left = deadline - time.monotonic()
            if left <= 0:
                return "consent_timeout", []
            srv.timeout = min(left, 1.0)
            srv.handle_request()
        got = srv.got
    finally:
        srv.server_close()                  # exactly one callback: a repeat finds nothing listening
    if got.get("error") or not got.get("code"):
        return "consent_failed", []
    status, _h, body = call("POST", TOKEN_URL, data=_form({
        "code": got["code"], "client_id": client["client_id"], "client_secret": client.get("client_secret", ""),
        "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier}),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    del got, verifier
    tok = _json(body) if status == 200 else None
    if not isinstance(tok, dict) or not isinstance(tok.get("refresh_token"), str):
        return "consent_failed", []
    granted = str(tok.get("scope") or "").split()
    if not set(want) <= set(granted):
        return "scope_denied", granted
    _save(st, purpose, "token.json", {"refresh_token": tok["refresh_token"], "scopes": want, "at": g._now()})
    if isinstance(tok.get("access_token"), str):
        with _TL:
            _TOKENS[str(pdir(st, purpose))] = (tok["access_token"], time.time() + int(tok.get("expires_in") or 0) - 60)
    return None, granted


def has_token(st, purpose: str) -> bool:
    t = _load(st, purpose, "token.json")
    return bool(t and isinstance(t.get("refresh_token"), str))


def access(st, purpose: str) -> tuple[str | None, str | None]:
    """→ (access token, None) or (None, reason): auth_expired when Google refuses the refresh grant."""
    key = str(pdir(st, purpose))
    with _TL:
        cur = _TOKENS.get(key)
        if cur and cur[1] > time.time():
            return cur[0], None
    client, tok = _load(st, purpose, "client.json"), _load(st, purpose, "token.json")
    if not client or not tok or not isinstance(tok.get("refresh_token"), str):
        return None, "auth_expired"
    status, _h, body = call("POST", TOKEN_URL, data=_form({
        "client_id": client["client_id"], "client_secret": client.get("client_secret", ""),
        "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"}),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    doc = _json(body)
    if status == 200 and isinstance(doc, dict) and isinstance(doc.get("access_token"), str):
        if isinstance(doc.get("refresh_token"), str):           # a rotated refresh token replaces the old one atomically
            _save(st, purpose, "token.json", {**tok, "refresh_token": doc["refresh_token"]})
        with _TL:
            _TOKENS[key] = (doc["access_token"], time.time() + int(doc.get("expires_in") or 0) - 60)
        return doc["access_token"], None
    if status in (400, 401) and isinstance(doc, dict) and doc.get("error") in ("invalid_grant", "unauthorized_client"):
        return None, "auth_expired"
    return None, "probe_failed"


def api(st, purpose: str, method: str, path: str, *, body=None, headers: dict | None = None, url: str | None = None):
    """One authorized Google API call → (status, headers, parsed JSON or None) or (None, reason, None)."""
    tok, why = access(st, purpose)
    if tok is None:
        return None, why, None
    h = {"Authorization": "Bearer " + tok, **(headers or {})}
    if isinstance(body, (dict, list)):
        body, h["Content-Type"] = json.dumps(body).encode(), "application/json; charset=UTF-8"
    status, rh, raw = call(method, url or API + path, data=body, headers=h)
    return status, rh, _json(raw)


def disabled(doc) -> bool:
    txt = json.dumps(doc) if doc is not None else ""
    return any(w in txt for w in ("accessNotConfigured", "SERVICE_DISABLED", "has not been used", "is disabled"))


def probe(st, purpose: str) -> str | None:
    """A read-only call with the purpose's own scope → None (ready) or a reason word."""
    status, rh, doc = api(st, purpose, "GET", PROBES[purpose])
    if status is None:
        return rh
    if status == 200:
        return None
    return "api_disabled" if status == 403 and disabled(doc) else "probe_failed"


def forget(st, purpose: str) -> int:
    """disconnect: revoke our refresh token at Google (best effort) and delete only this purpose's files → files removed."""
    tok = _load(st, purpose, "token.json")
    if tok and isinstance(tok.get("refresh_token"), str):
        call("POST", REVOKE_URL, data=_form({"token": tok["refresh_token"]}),
             headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=15)
    with _TL:
        _TOKENS.pop(str(pdir(st, purpose)), None)
    n = 0
    for name in ("token.json", "client.json"):
        with contextlib.suppress(FileNotFoundError):
            os.unlink(pdir(st, purpose) / name)
            n += 1
    with contextlib.suppress(OSError):
        os.rmdir(pdir(st, purpose))
    return n
