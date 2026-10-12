"""P114 (ADR-A194 §5): the login QR card and the daily sign-in check, inside serve.

A QR code is short-lived login material. It is made only when the owner is actively signing in (good morning, an explicit
request, or 「刷新」 on the card) — never by the daily check, never at night by itself. Only a packaged template's QR element
is cropped (nothing else of the page); the PNG lives in this process's memory and goes only to paired phones inside the
end-to-end encrypted session, as a dedicated card that is not chat history and never reaches the Agent's context. TTL
≤ 120 s; success / expiry / cancel / Stop everything wipe it. The audit keeps the card id, site, result and time only.

Success is decided by the browser itself (the login tab reaches the site's signed-in URL), never by "I scanned it".
Telegram: a configured bot gets a text reminder without any link; the QR image itself goes to the owner's private chat
only after the owner turned that on with a tap on a paired phone's card (Telegram can read it) — revocable any time.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import datetime as _dt
import hashlib
import json
import re
import secrets
import threading
import time

from . import browser, browser_sites as bs
from .browser_cdp import CDP, CDPError, capture_qr, present

CTX = "agentj.login.v1"
TTL_MAX = 120
QR_WAIT = 30.0                  # some sign-in pages animate in for several seconds
SETTLE = 1.5                    # a crop counts only when two crops this far apart are identical (no fade / spinner)
POLL = 2.0
MAX_OPEN = 4
PHONE_TYPES = ("login_qr_check", "login_qr_cancel", "login_qr_tg", "login_qr_refresh")
_ID = re.compile(r"[0-9a-f]{32}")
QUIET = (22, 7)                 # no unprompted reminders between 22:00 and 07:00 local


def bind_digest(channel: str, site: str, cid: str, nonce: str, until: int) -> str:
    return hashlib.sha256("\n".join([CTX, channel or "", site, cid, nonce, str(until)]).encode()).hexdigest()


def quiet(now: _dt.datetime | None = None) -> bool:
    h = (now or _dt.datetime.now()).hour
    return h >= QUIET[0] or h < QUIET[1]


class LoginCards:
    """Owned by elevate.Elevator (same Agent socket, same phone dispatch)."""

    def __init__(self, elev):
        self.elev = elev
        self.host = elev.host
        self.st = elev.st
        self.cards: dict[str, dict] = {}
        self.tabs: dict[str, str] = {}          # site → the login tab this host opened (kept: it is the owner's page)
        self.bg: set = set()                    # phone-initiated work running beside the receive loop

    # ---------------------------------------------------------- records (never the image)
    def _results_path(self):
        return browser.bdir(self.st) / "login-results.json"

    def _remember(self, cid: str, rec: dict) -> None:
        d = browser._read_json(self._results_path()) or {}
        d[cid] = {**d.get(cid, {}), **rec, "at": int(time.time())}
        if len(d) > 30:
            for k in sorted(d, key=lambda k: d[k].get("at", 0))[:-30]:
                d.pop(k, None)
        browser._write_json(self.st, self._results_path(), d)

    def _audit(self, c: dict, result: str, **kw) -> None:
        rec = {"ts": int(time.time()), "id": c["id"], "site": c["site"], "result": result}
        rec.update({k: v for k, v in kw.items() if v is not None})
        self.st.append_private(browser.bdir(self.st) / "login.log", json.dumps(rec, ensure_ascii=False))

    def _active(self, on: bool) -> None:
        p = browser.bdir(self.st) / "login-active.json"
        if on:
            browser._write_json(self.st, p, {"until": int(time.time()) + TTL_MAX + 30})
        elif not self.cards:
            with contextlib.suppress(FileNotFoundError):
                p.unlink()

    # ---------------------------------------------------------- the Agent (or the owner's refresh) asks
    async def request(self, req: dict, by: str = "agent") -> dict:
        site = req.get("site")
        tpl = bs.templates()
        if not isinstance(site, str) or site not in tpl and site not in bs.load(self.st)["sites"]:
            return {"result": "refused", "why": "unknown_site", "detail": "agentj browser sites list"}
        if self.host.stopped():
            return {"result": "stopped"}
        if not browser.settings(self.st)["enabled"]:
            return {"result": "disabled"}
        if site not in bs.load(self.st)["sites"]:
            await asyncio.to_thread(bs.add, self.st, site)
        rec = bs.load(self.st)["sites"][site]
        t = tpl.get(rec.get("template")) or {}
        rt = await asyncio.to_thread(browser.runtime, self.st)
        if not rt:
            return {"result": "no_browser", "hint": browser.HINTS["not_running"]}
        for c in self.cards.values():
            if c["site"] == site:                 # one card per site: a repeated good morning reuses it
                return {"result": "sent", "id": c["id"], "reused": True, "ttl": max(0, int(c["deadline"] - time.monotonic()))}
        if not t.get("qr"):
            return await self._host_login(site, t, rec, rt)
        if not self.host._approvers():
            return {"result": "no_device"}
        if len(self.cards) >= MAX_OPEN:
            return {"result": "busy"}
        try:
            got = await asyncio.to_thread(self._open_and_capture, rt, site, t)
        except CDPError as e:
            self.st.log("login_qr_failed", site=site, reason=str(e)[:60])
            return {"result": "failed", "why": "browser"}
        if got["state"] == "in":
            await asyncio.to_thread(bs.check, self.st, [site], False, "login")
            return {"result": "already", "site": site}
        if got["state"] != "qr":
            return {"result": "no_qr", "headless": bool(rt.get("headless")), "site": site,
                    "hint": "请在这台电脑上 Agent J 的专用浏览器里登录 / sign in on this computer's Agent J browser"
                    if not rt.get("headless") else "这台电脑没有屏幕，也没找到二维码 / no screen and no QR code on this page"}
        ttl = min(TTL_MAX, int((t.get("qr") or {}).get("ttl") or TTL_MAX))
        cid, nonce = secrets.token_hex(16), secrets.token_hex(16)
        until = int(time.time()) + ttl
        lang = bs._lang(self.st)
        c = {"id": cid, "site": site, "title": t["title"][lang], "img": bytearray(got["png"]), "nonce": nonce,
             "deadline": time.monotonic() + ttl, "until": until, "by": by, "cdp": got["cdp"], "session": got["session"],
             "bind": bind_digest(self.host.channel, site, cid, nonce, until), "lock": threading.Lock()}
        c["timer"] = asyncio.get_running_loop().call_later(ttl, lambda: self._end(c, "expired"))
        self.cards[cid] = c
        self._active(True)
        self._audit(c, "sent", by=by)
        self._remember(cid, {"site": site, "result": "pending", "until": until})
        self.st.log("login_qr_card", id=cid, site=site, by=by)
        c["watch"] = asyncio.create_task(self._watch(c))
        self.host.push_notify("ask")
        await self.host._send_ready(lambda s: self.card_msg(c))
        tg = await self._telegram(c)
        return {"result": "sent", "id": cid, "ttl": ttl, "site": site, "devices": len(self.host._approvers()), "telegram": tg}

    async def _host_login(self, site: str, t: dict, rec: dict, rt: dict) -> dict:
        url = t.get("login_url") or rec.get("check_url")
        if rt.get("headless"):
            return {"result": "host_login_unavailable", "site": site,
                    "hint": "这台电脑没有屏幕：请在有屏幕的电脑上登录这个网站，或选支持扫码的网站 / no screen here"}
        res = await asyncio.to_thread(bs.open_url, self.st, url)
        return {"result": "host_login", "site": site, "opened": bool(res.get("ok")),
                "hint": "已在这台电脑的 Agent J 浏览器打开登录页：请主人在电脑前登录，然后运行 agentj browser check --site " + site
                        + " / opened the sign-in page on this computer"}

    def _open_and_capture(self, rt: dict, site: str, t: dict) -> dict:
        """(thread) Reuse or open the login tab, wait up to 15 s for the QR element, crop it. The CDP connection stays with
        the card (its watcher); the tab is never closed by us."""
        cdp = CDP(rt["ws"])
        ok = False
        try:
            target = self.tabs.get(site)
            if not target or not cdp.tab_alive(target):
                target = cdp.new_tab(t["login_url"], background=bool(rt.get("headless")))
                self.tabs[site] = target
            session = cdp.attach(target)
            end = time.monotonic() + QR_WAIT
            while time.monotonic() < end:
                href, _ = cdp.location(session)
                if bs.classify(href, t) == "in" and not (t.get("out_dom") and present(cdp, session, t["out_dom"])):
                    return {"state": "in"}
                png = capture_qr(cdp, session, t["qr"]["selectors"])
                if png:
                    time.sleep(SETTLE)
                    again = capture_qr(cdp, session, t["qr"]["selectors"])
                    if again == png:
                        ok = True
                        return {"state": "qr", "png": png, "cdp": cdp, "session": session}
                    continue
                time.sleep(1.0)
            return {"state": "none"}
        finally:
            if not ok:
                cdp.close()

    def card_msg(self, c: dict) -> dict:
        return {"t": "login_qr", "id": c["id"], "n": c["nonce"], "site": c["site"], "title": c["title"],
                "img": base64.b64encode(bytes(c["img"])).decode(), "ttl": max(0, int(c["deadline"] - time.monotonic())),
                "bind": c["bind"], "tg": self._tg_state(), "channel": "Telegram"}

    def _tg_state(self) -> str:
        """none (no Telegram) | text (reminders only) | qr (the owner allowed QR codes there)."""
        from . import telegram
        cfg = telegram.configuration(self.st)
        if not cfg or not (self.host.telegram and self.host.telegram.enabled()):
            return "none"
        return "qr" if bs.tg_consent(self.st) else "text"

    async def on_ready(self, s) -> None:
        for c in list(self.cards.values()):
            await self.host.send_app(s, self.card_msg(c))

    # ---------------------------------------------------------- watching the login tab
    def _signed_in(self, c: dict) -> bool:
        with c["lock"]:                         # the watcher and a phone's 「我扫好了」 share one connection
            href, _ = c["cdp"].location(c["session"])
            t = bs.templates()[bs.load(self.st)["sites"][c["site"]]["template"]]
            if bs.classify(href, t) != "in":
                return False
            return not (t.get("out_dom") and present(c["cdp"], c["session"], t["out_dom"]))

    async def _watch(self, c: dict) -> None:
        try:
            while c["id"] in self.cards:
                await asyncio.sleep(POLL)
                if c["id"] not in self.cards:
                    return
                try:
                    if await asyncio.to_thread(self._signed_in, c) and await self._success(c):
                        return
                except CDPError:
                    continue
        except asyncio.CancelledError:
            pass

    async def _success(self, c: dict) -> bool:
        """The login tab looks signed in: the trusted check (fresh tab, stable URL, sign-in form cross-check) decides."""
        if c.get("confirming"):
            return False
        c["confirming"] = True
        try:
            res = await asyncio.to_thread(bs.check, self.st, [c["site"]], False, "login")
        finally:
            c["confirming"] = False
        status = next((x["status"] for x in res.get("sites", []) if x["site"] == c["site"]), None)
        if status != "logged_in" or c["id"] not in self.cards:
            return False
        self._end(c, "done")
        lang = getattr(self.host, "lang", "zh")
        hm = time.strftime("%H:%M")
        note = (f"「{c['title']}」已登录 {hm}（体检确认：{status}）" if lang != "en"
                else f"{c['title']} signed in at {hm} (check: {status})")
        with contextlib.suppress(Exception):
            self.host.hist_add({"k": "sys", "text": ""}, note, "done")
        return True

    # ---------------------------------------------------------- the phone answers
    async def on_phone(self, s, obj: dict) -> None:
        t = obj.get("t")
        if not self.st.sign_key(s.device):            # only an approved device (same rule as the other cards)
            self.st.log("login_qr_refused", device=s.device, reason="no_key")
            return
        if t == "login_qr_refresh":
            site = obj.get("site")
            if isinstance(site, str) and site in bs.load(self.st)["sites"]:
                res = await self.request({"site": site}, by=s.device)
                if res.get("result") != "sent":
                    await self.host.send_app(s, {"t": "login_qr_err", "id": "", "site": site, "why": res.get("result", "failed")})
            return
        cid = obj.get("id")
        c = self.cards.get(cid) if isinstance(cid, str) and _ID.fullmatch(cid) else None
        if c is None or obj.get("n") != c["nonce"]:
            return await self.host.send_app(s, {"t": "login_qr_err", "id": cid if isinstance(cid, str) else "", "why": "gone"})
        if t == "login_qr_cancel":
            return self._end(c, "cancelled", device=s.device)
        if t == "login_qr_check":
            try:
                done = await asyncio.to_thread(self._signed_in, c)
            except CDPError:
                done = False
            if done and await self._success(c):
                return
            return await self.host.send_app(s, {"t": "login_qr_state", "id": cid, "state": "waiting"})
        if t == "login_qr_tg":
            on = obj.get("on") is True
            await asyncio.to_thread(bs.set_tg_consent, self.st, on, s.device)
            self._audit(c, "tg_consent_on" if on else "tg_consent_off", device=s.device)
            if on:
                await self._telegram(c)
            await self.host._send_ready(lambda x: {"t": "login_qr_state", "id": cid, "state": "settings", "tg": self._tg_state()})

    # ---------------------------------------------------------- Telegram (text by default; QR only with consent)
    async def _telegram(self, c: dict) -> str:
        from . import telegram
        state = self._tg_state()
        if state == "none":
            return "none"
        cfg = telegram.configuration(self.st)
        secs = max(0, int(c["deadline"] - time.monotonic()))
        zh = getattr(self.host, "lang", "zh") != "en"
        try:
            if state == "qr":
                cap = (f"Agent J 登录二维码：{c['title']}，{secs} 秒内有效（Telegram 能看到这张图）" if zh else
                       f"Agent J sign-in QR: {c['title']}, valid {secs} s (Telegram can see this image)")
                await asyncio.to_thread(telegram.upload, cfg, "sendPhoto", {"chat_id": cfg["owner_id"], "caption": cap},
                                        "photo", "login-qr.png", "image/png", bytes(c["img"]))
            else:
                text = (f"「{c['title']}」需要登录：请打开已配对的 Agent J 手机网页扫码（{secs} 秒内有效）。" if zh else
                        f"{c['title']} needs a sign-in: open your paired Agent J phone page to scan ({secs} s).")
                await asyncio.to_thread(telegram.api, cfg, "sendMessage", {"chat_id": cfg["owner_id"], "text": text})
            self._audit(c, "telegram_" + state)
            return state
        except ValueError:
            self._audit(c, "telegram_failed")
            return "failed"

    # ---------------------------------------------------------- the end of a card
    def _end(self, c: dict, result: str, device: str | None = None) -> None:
        if self.cards.pop(c["id"], None) is None:
            return
        for k in ("timer", "watch"):
            with contextlib.suppress(Exception):
                c[k].cancel()
        c["img"][:] = b"\0" * len(c["img"])
        c["img"] = bytearray()
        with contextlib.suppress(Exception):
            c["cdp"].detach(c["session"])
            c["cdp"].close()
        self._active(False)
        self._audit(c, result, device=device)
        self._remember(c["id"], {"site": c["site"], "result": result, "until": None})
        self.st.log("login_qr_done", id=c["id"], site=c["site"], result=result)
        done = {"t": "login_qr_done", "id": c["id"], "site": c["site"], "result": result}
        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop().create_task(self.host._send_ready(lambda s: done))

    def end_all(self, result: str) -> None:
        for c in list(self.cards.values()):
            self._end(c, result)

    def result(self, req: dict) -> dict:
        cid = req.get("id")
        d = browser._read_json(self._results_path()) or {}
        if not isinstance(cid, str) or cid not in d:
            return {"result": "unknown"}
        out = dict(d[cid])
        if out.get("result") == "pending" and out.get("until"):
            out["secs"] = max(0, out["until"] - int(time.time()))
        return out


# ---------------------------------------------------------------- the daily check (serve job)
async def daily_loop(host, every: float = 60.0) -> None:
    """Once a day at browser.check_time: a read-only check of every site. Status only — no QR. A site that newly needs a
    sign-in gets one short note (chat history + Telegram text if configured), outside quiet hours only."""
    st = host.st
    while True:
        await asyncio.sleep(every)
        with contextlib.suppress(Exception):
            if not browser.settings(st)["enabled"] or not bs.load(st)["sites"] or not bs.daily_due(st):
                continue
            if not await asyncio.to_thread(browser.runtime, st):
                continue
            res = await asyncio.to_thread(bs.check, st, None, False, "daily")
            bs.daily_done(st)
            lost = [c for c in res.get("changed", []) if c["to"] == "auth_required"]
            if lost and not quiet():
                await notify_lost(host, lost)


async def notify_lost(host, lost: list[dict]) -> None:
    st = host.st
    titles = {x["site"]: x["title"] for x in bs.summary(st)}
    names = "、".join(titles.get(c["site"], c["site"]) for c in lost)
    zh = getattr(host, "lang", "zh") != "en"
    text = (f"每日登录体检：{names} 需要重新登录。方便时对我说「早上好」，我带你扫码或在电脑上登录。" if zh else
            f"Daily sign-in check: {names} needs a fresh sign-in. Say “good morning” when convenient and I will walk you through it.")
    with contextlib.suppress(Exception):
        host.agent_notice(text)
    from . import telegram
    cfg = telegram.configuration(st)
    if cfg and host.telegram and host.telegram.enabled():
        with contextlib.suppress(ValueError):
            await asyncio.to_thread(telegram.api, cfg, "sendMessage", {"chat_id": cfg["owner_id"], "text": text})
