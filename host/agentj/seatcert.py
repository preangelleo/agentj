"""P185: the relay seat certificate (PROTOCOL §2 seat certificate, ADR-A198) — a host without a usable Agent J seat does not serve phones.

The Dashboard signs a bound host whose seat is usable (paid / comp / billing grace) a 72-hour certificate for its relay channel
(`POST /v1/host/relay/cert`); the host presents it when it authenticates to the relay and pushes each renewal on the open link
(`{"t":"cert"}`), and the relay (SEAT_CERT=required) refuses a host without a valid one (close 4002 "seat"). The same verdict
gates this host's own doors that do not pass the relay — phone and Telegram messages into the Agent and starting a pairing —
so a refused host says why and where to fix it instead of going silent.

Verdicts: ``ok`` (a valid cached certificate) · ``grace`` (bound, no certificate yet and the cloud gave no answer: up to 72 h
from the first try — an upgrade from 0.18 while our cloud is down never locks a paying owner out) · ``unbound`` (no Agent J
account here, or the Dashboard says this host is not bound) · ``suspended`` (402: the seat is unpaid past its billing grace) ·
``expired`` (the cloud has been unreachable for longer than the certificate lives) · ``exempt`` (a loopback relay: local tests /
development; AGENTJ_TEST_SEAT_ENFORCE=1 turns enforcement on there). Only a definitive answer from the Dashboard (not bound /
suspended) ends a seat early; an unreachable cloud never does — the cached certificate is used to its end.

The cache `<state>/seat/cert.json` (0600) holds only the certificate (a signature over channel + expiry), its expiry, the
last verdict and the first-try time. Never logs the certificate.
"""
from __future__ import annotations

import json
import os
import pathlib
import time
from urllib.parse import urlparse

from . import cloud

TTL_GRACE = 72 * 3600          # = the certificate's life (Dashboard RELAY_CERT_TTL_S); also the bootstrap grace
RENEW_AGE = 6 * 3600           # renew a certificate older than this (≥ 66 h of a cloud outage are then covered)
RETRY_FAIL = 600               # unreachable / rate limited / malformed answer → ask again after this
RETRY_REFUSED = 900            # not bound / suspended → ask again after this (a refused ask costs no rate budget; a re-bind is
                               # noticed at once: see due()) — paying on the account page resumes within 15 minutes
VERDICTS = ("ok", "grace", "unbound", "suspended", "expired", "exempt")
ALLOWED = ("ok", "grace", "exempt")

TEXT = {
    "zh": {
        "unbound": "这台电脑还没有绑定 Agent J 席位，所以手机和 Telegram 的消息不会交给 Agent，也不能配对新遥控器。"
                   "在账户页 {url} 买一个席位、点「复制安装提示词」，把那句话交给电脑上的 Agent（或自己运行 `agentj login --seat-file …`）完成绑定。",
        "suspended": "这台电脑的 Agent J 席位已暂停（超出已付席位、宽限期已过），消息不会交给 Agent。到账户页 {url} 续费或调整席位后自动恢复。",
        "expired": "这台电脑超过 72 小时没能和 Agent J 账户服务确认席位，暂时停用。检查网络后运行 `agentj doctor`；恢复联系后自动继续。",
        "pair": "配对新遥控器需要有效的 Agent J 席位。",
    },
    "en": {
        "unbound": "This computer is not bound to an Agent J seat, so phone and Telegram messages are not passed to the Agent and no new "
                   "remote can be paired. Buy a seat on the account page {url}, press “Copy installation prompt” and give that sentence "
                   "to the Agent on this computer (or run `agentj login --seat-file …` yourself).",
        "suspended": "This computer's Agent J seat is suspended (beyond the paid seats, past the grace period); messages are not passed "
                     "to the Agent. Renew or adjust seats on the account page {url} — it resumes by itself.",
        "expired": "This computer could not confirm its seat with the Agent J account service for more than 72 hours and is paused. "
                   "Check the network, then run `agentj doctor`; it continues once the service is reachable.",
        "pair": "Pairing a new remote needs a valid Agent J seat.",
    },
}


def _loopback(url: str) -> bool:
    try:
        return (urlparse(url).hostname or "") in cloud.LOOPBACK
    except ValueError:
        return False


def message(verdict: str, lang: str, url: str) -> str:
    t = TEXT["en" if lang == "en" else "zh"]
    return t.get(verdict, "").format(url=url)


class SeatCert:
    """One per serve (and read-only per CLI call). `post` / `clock` are injectable for tests."""

    def __init__(self, st, relay_url: str = "", *, post=None, clock=time.time):
        self.st = st
        self.relay_url = relay_url or ""
        self.post = post or cloud.post_json
        self.clock = clock
        self._next = 0.0          # earliest next ask (unix s); 0 = now
        self._linked = None       # host id in cloud.json at the last look ("" = none): a bind / re-bind is noticed at once

    # ------------------------------------------------------------ cache
    def path(self) -> pathlib.Path:
        return pathlib.Path(self.st.root) / "seat" / "cert.json"

    def read(self) -> dict:
        try:
            d = json.loads(self.path().read_text())
        except (OSError, ValueError):
            return {}
        return d if isinstance(d, dict) else {}

    def _write(self, d: dict) -> None:
        p = self.path()
        p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(d, f)
        os.replace(tmp, p)

    # ------------------------------------------------------------ verdict
    def exempt(self) -> bool:
        return _loopback(self.relay_url) and os.environ.get("AGENTJ_TEST_SEAT_ENFORCE") != "1"

    def cert(self, now: float | None = None) -> str | None:
        """The cached certificate while it is valid for ≥ 60 s more, else None."""
        now = self.clock() if now is None else now
        d = self.read()
        c, exp = d.get("cert"), d.get("exp")
        return c if isinstance(c, str) and type(exp) is int and exp > now + 60 else None

    def verdict(self, now: float | None = None) -> str:
        now = self.clock() if now is None else now
        if self.exempt():
            return "exempt"
        if not cloud.read_cloud(self.st):
            return "unbound"
        d = self.read()
        last = d.get("verdict")
        if last == "not_bound":
            return "unbound"
        if last == "suspended":
            return "suspended"
        if self.cert(now):
            return "ok"
        first = d.get("first_try")
        if type(first) in (int, float) and now < first + TTL_GRACE and not d.get("cert"):
            return "grace"
        return "expired" if d.get("cert") or type(first) in (int, float) else "grace"

    def allowed(self, now: float | None = None) -> bool:
        return self.verdict(now) in ALLOWED

    def view(self, now: float | None = None) -> dict:
        """For status / doctor / the phone: {verdict, exp, url} — never the certificate."""
        now = self.clock() if now is None else now
        d = self.read()
        exp = d.get("exp") if type(d.get("exp")) is int else None
        return {"verdict": self.verdict(now), "exp": exp, "url": cloud.app_url(self.st)}

    # ------------------------------------------------------------ renewal
    def due(self, now: float | None = None) -> bool:
        """Whether to ask the Dashboard now: never when exempt or unlinked; at once when cloud.json just appeared (a fresh
        bind); otherwise when the certificate is missing / older than RENEW_AGE and the retry time has passed."""
        now = self.clock() if now is None else now
        if self.exempt():
            return False
        link = cloud.read_cloud(self.st)
        hid = link["host_id"] if link else ""
        fresh = bool(hid) and self._linked is not None and hid != self._linked
        self._linked = hid
        if not hid:
            return False
        if fresh:
            self._next = 0.0
            try:
                d = self.read()
                if d.get("verdict") in ("not_bound", "suspended"):
                    self._write({})          # a new binding: the old refusal is not about it
            except OSError:
                pass
            return True
        if now < self._next:
            return False
        d = self.read()
        at = d.get("at")
        return not self.cert(now) or type(at) not in (int, float) or now - at >= RENEW_AGE

    def refresh(self, now: float | None = None) -> str:
        """One ask (blocking — call it off the event loop). Returns the result kind (cloud.relay_cert's)."""
        now = self.clock() if now is None else now
        res = cloud.relay_cert(self.st, post=self.post, now=lambda: now)
        d = self.read()
        if res.kind == "ok":
            d = {"cert": res.cert, "exp": res.exp, "at": now, "verdict": "ok"}
            self._next = 0.0
        elif res.kind in ("not_bound", "payment_required"):
            # a definitive answer: the seat ends now (the relay holds the last certificate to its expiry at most)
            d = {"verdict": "not_bound" if res.kind == "not_bound" else "suspended", "at": now}
            self._next = now + RETRY_REFUSED
        elif res.kind == "unlinked":
            d = {}
            self._next = 0.0
        else:                                   # unreachable / rate limited / malformed: keep what we have (and the last verdict)
            if not d.get("cert") and type(d.get("first_try")) not in (int, float):
                d["first_try"] = now
            self._next = now + RETRY_FAIL
        try:
            self._write(d)
        except OSError:
            pass
        try:
            self.st.log("seat_cert", reason=res.kind, status=res.status)
        except Exception:  # noqa: BLE001 — logging never costs the renewal
            pass
        return res.kind
