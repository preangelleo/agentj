"""P114 (ADR-A194 §4–§6): the sites Agent J keeps signed in, their read-only checks and the P86 registry projection.

`<state>/browser/sites.json` (0600) is the local authority: stable site_id, template, allowed origins, profile_ref, adapter
version, automation_refs, status, check time, an enumerated reason and the login method. No e-mail, user id, cookie,
password or response body — ever. Status: logged_in / auth_required / unknown / unavailable / disabled. A timeout, a DOM
drift, a network error or a risk page is NOT auth_required: those are unknown / unavailable. Only the trusted check writes
a status; the main Agent reads it and can never set it.

Checks are serial per profile (a lock), 15 s per try plus two safe retries, each in a tab this module created and closes in
finally. The daily check (default 08:00 local, catches up once after sleep) only writes status: it never makes or sends a
QR code. Before a task, `check --if-stale` re-checks anything older than 15 minutes.
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import fcntl
import hashlib
import json
import pathlib
import re
import time
from urllib.parse import urlsplit

from . import browser
from .browser_cdp import CDP, CDPError, present
from .state import State

TEMPLATES_PATH = pathlib.Path(__file__).with_name("browser_sites.json")
FRESH = 15 * 60                 # a logged_in older than this is stale for the registry and re-checked before a task
TRY_SECS = 15.0
RETRIES = 2
STABLE = 3.0                    # an out/err/offsite URL must hold this long (single-page apps redirect after load)
STABLE_IN = 5.0                 # "signed in" must hold longer: an app URL can turn into the sign-in page seconds after load
STATUSES = ("logged_in", "auth_required", "unknown", "unavailable", "disabled")
_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")


def templates() -> dict:
    return json.loads(TEMPLATES_PATH.read_text())["templates"]


def adapter_version() -> int:
    return int(json.loads(TEMPLATES_PATH.read_text())["adapter_version"])


def _lang(st: State) -> str:
    with contextlib.suppress(Exception):
        from . import preferences as p
        return "en" if p.get(p.effective(st), "appearance.language", "zh") == "en" else "zh"
    return "zh"


def template_list(st: State) -> list[dict]:
    lang = _lang(st)
    return [{"site": k, "title": t["title"][lang], "login_method": t["login_method"], "note": t.get("note", {}).get(lang, "")}
            for k, t in templates().items()]


# ---------------------------------------------------------------- the local state
def sites_path(st: State) -> pathlib.Path:
    return browser.bdir(st) / "sites.json"


def load(st: State) -> dict:
    d = browser._read_json(sites_path(st))
    if not isinstance(d, dict) or d.get("schema_version") != 1 or not isinstance(d.get("sites"), dict):
        return {"schema_version": 1, "revision": 0, "sites": {}}
    return d


def _save(st: State, d: dict) -> None:
    d["revision"] = int(d.get("revision", 0)) + 1
    browser._write_json(st, sites_path(st), d)


@contextlib.contextmanager
def _lock(st: State, name: str = "sites.lock", wait: bool = True):
    browser.ensure_dir(st)
    with open(browser.bdir(st) / name, "a+") as f:
        import os
        os.chmod(f.name, 0o600)
        fcntl.flock(f, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _origin(url: str) -> str:
    u = urlsplit(url)
    return f"{u.scheme}://{u.netloc}".lower()


def add(st: State, site, origin=None, url=None, title=None) -> dict:
    """Add a template site, or a custom one (`custom` + https origin + start URL on it). Never enables any task."""
    if not isinstance(site, str):
        return {"ok": False, "reason": "shape", "detail": "site: a template id or custom"}
    tpl = templates()
    with _lock(st):
        d = load(st)
        if site in tpl:
            t = tpl[site]
            rec = {"site_id": site, "template": site, "origins": t["origins"], "login_method": t["login_method"]}
        elif site == "custom" or site.startswith("custom-"):
            if not (isinstance(origin, str) and re.fullmatch(r"https://[a-z0-9.-]+(:\d{1,5})?", origin.lower())):
                return {"ok": False, "reason": "shape", "detail": "custom: --origin https://example.com"}
            origin = origin.lower()
            if not (isinstance(url, str) and _origin(url) == origin):
                return {"ok": False, "reason": "shape", "detail": "custom: --url must be a page on --origin"}
            slug = re.sub(r"[^a-z0-9-]+", "-", urlsplit(origin).hostname or "")[:32].strip("-")
            sid = site if site.startswith("custom-") and _SLUG.fullmatch(site) else f"custom-{slug}"
            name = title if isinstance(title, str) and 0 < len(title) <= 40 and "\n" not in title else urlsplit(origin).hostname
            rec = {"site_id": sid, "template": "custom", "origins": [origin], "login_method": "host", "check_url": url,
                   "title": name}
            site = sid
        else:
            return {"ok": False, "reason": "unknown_site", "detail": "templates: " + ", ".join(tpl) + ", custom"}
        old = d["sites"].get(site) or {}
        rec.update(profile_ref="default", adapter_version=adapter_version(), automation_refs=old.get("automation_refs", []),
                   status=old.get("status", "unknown"), reason=old.get("reason", "not_checked"),
                   checked_at=old.get("checked_at"), added_at=old.get("added_at") or int(time.time()))
        d["sites"][site] = rec
        _save(st, d)
    st.log("browser_site_added", site=site)
    return {"ok": True, "site": site, "added": not old, "note": "added to the daily check; no task was enabled / 只加入体检，没有启用任何任务"}


def remove(st: State, site) -> dict:
    with _lock(st):
        d = load(st)
        if not isinstance(site, str) or site not in d["sites"]:
            return {"ok": False, "reason": "unknown_site"}
        del d["sites"][site]
        _save(st, d)
    st.log("browser_site_removed", site=site)
    return {"ok": True, "site": site, "note": "no longer checked; the browser keeps its sign-in until you sign out there / 不再体检；浏览器里的登录没有退出"}


def _title(st: State, rec: dict, lang: str) -> str:
    t = templates().get(rec.get("template"))
    return t["title"][lang] if t else rec.get("title") or rec["site_id"]


def _stale(rec: dict, now: float) -> bool:
    return not rec.get("checked_at") or now - rec["checked_at"] > FRESH


def summary(st: State) -> list[dict]:
    lang, now = _lang(st), time.time()
    enabled = browser.settings(st)["enabled"]
    out = []
    for sid, rec in sorted(load(st)["sites"].items()):
        status = rec.get("status", "unknown") if enabled else "disabled"
        out.append({"site": sid, "title": _title(st, rec, lang), "status": status, "reason": rec.get("reason"),
                    "checked_at": rec.get("checked_at"), "stale": _stale(rec, now), "login_method": rec.get("login_method"),
                    "qr": bool((templates().get(rec.get("template")) or {}).get("qr"))})
    return out


# ---------------------------------------------------------------- one read-only check
def classify(url: str, rule: dict) -> str:
    """in | out | error | offsite | none — from the URL only."""
    if not url or url.startswith(("chrome-error:", "about:neterror")):
        return "error"
    if any(re.search(p, url) for p in rule.get("in_url", [])):
        return "in"
    if any(re.search(p, url) for p in rule.get("out_url", [])):
        return "out"
    if _origin(url) not in [o.lower() for o in rule.get("origins", [])]:
        return "offsite"
    return "none"


def _rule(rec: dict) -> dict | None:
    t = templates().get(rec.get("template"))
    if t:
        return t
    return None                    # custom: no trusted probe → unknown


def probe(cdp: CDP, rule: dict, url: str, secs: float = TRY_SECS, clock=time.monotonic, sleep=time.sleep) -> tuple[str, str]:
    """Open the fixed check page in a new background tab, watch its URL, close the tab. → (status, reason)."""
    target = cdp.new_tab("about:blank")
    try:
        session = cdp.attach(target)
        cdp.call("Page.navigate", {"url": url}, session=session, timeout=secs)
        end, last, since = clock() + secs, None, clock()
        while clock() < end:
            try:
                href, ready = cdp.location(session)
            except CDPError:
                href, ready = "", ""
            c = classify(href, rule) if href else "none"
            if c == "in" and rule.get("out_dom"):     # an app URL that can still show a sign-in form: cross-check
                try:
                    c = "out" if present(cdp, session, rule["out_dom"]) else "in"
                except CDPError:
                    c = "none"
            if c != last:
                last, since = c, clock()
            if c == "in" and ready == "complete" and clock() - since >= STABLE_IN:   # apps redirect to sign-in after load
                return "logged_in", "ok"
            if c in ("out", "error", "offsite") and ready == "complete" and clock() - since >= STABLE:
                return {"out": ("auth_required", "signed_out"), "error": ("unavailable", "offline"),
                        "offsite": ("unknown", "offsite")}[c]
            sleep(0.5)
        if last == "out":
            return "auth_required", "signed_out"
        if last == "error":
            return "unavailable", "offline"
        return "unknown", "timeout"
    finally:
        cdp.close_tab(target)


def _audit(st: State, **kw) -> None:
    st.append_private(browser.bdir(st) / "checks.jsonl", json.dumps({"ts": int(time.time()), **kw}, ensure_ascii=False))


def check(st: State, sites=None, if_stale: bool = False, reason: str = "manual", _cdp=None) -> dict:
    """Re-check the given sites (default: all) serially. Never logs in, refreshes cookies, clicks or signs out."""
    if not browser.settings(st)["enabled"]:
        return {"ok": False, "reason": "disabled", "hint": "agentj browser enable"}
    d = load(st)
    ids = [s for s in (sites or list(d["sites"])) if isinstance(s, str)]
    unknown = [s for s in ids if s not in d["sites"]]
    if unknown:
        return {"ok": False, "reason": "unknown_site", "detail": ", ".join(unknown)}
    now = time.time()
    if if_stale:
        ids = [s for s in ids if _stale(d["sites"][s], now) or d["sites"][s].get("status") != "logged_in"]
    rt = browser.runtime(st) if _cdp is None else {"ws": "fake"}
    results = {}
    if not rt:
        for s in ids:
            results[s] = ("unavailable", "no_browser")
    else:
        with _lock(st, "profile.lock"):
            cdp = _cdp or CDP(rt["ws"])
            try:
                for s in ids:
                    rule = _rule(d["sites"][s])
                    if rule is None:
                        results[s] = ("unknown", "unsupported")
                        continue
                    url = rule["check_url"]
                    got = ("unknown", "error")
                    for _ in range(RETRIES + 1):
                        try:
                            got = probe(cdp, rule, url)
                        except CDPError:
                            got = ("unknown", "error")
                        if got[0] in ("logged_in", "auth_required"):
                            break
                    results[s] = got
            finally:
                if _cdp is None:
                    cdp.close()
    changed = []
    with _lock(st):
        d = load(st)
        at = int(time.time())
        for s, (status, why) in results.items():
            rec = d["sites"].get(s)
            if rec is None:
                continue
            if rec.get("status") != status:
                changed.append({"site": s, "from": rec.get("status"), "to": status})
            rec.update(status=status, reason=why, checked_at=at, adapter_version=adapter_version())
            _audit(st, site=s, status=status, reason=why, by=reason)
        if results:
            _save(st, d)
    st.log("browser_check", n=len(results), by=reason)
    return {"ok": True, "checked": len(results), "changed": changed, "sites": summary(st)}


def open_url(st: State, url) -> dict:
    """Open a page in a visible tab of Agent J's browser (the owner signs in there on this computer)."""
    if not isinstance(url, str) or not re.fullmatch(r"https?://[^\s]{1,2000}", url):
        return {"ok": False, "reason": "shape", "detail": "an http(s) URL"}
    rt = browser.runtime(st)
    if not rt:
        return {"ok": False, "reason": "not_running", "hint": browser.HINTS["not_running"]}
    with CDP(rt["ws"]) as cdp:
        cdp.new_tab(url, background=False)
    return {"ok": True, "headless": bool(rt.get("headless")),
            "note": "opened on this computer's screen" if not rt.get("headless") else
                    "no screen here (headless): use a QR sign-in from the phone, or a computer with a screen"}


# ---------------------------------------------------------------- P86 capability registry projection (§6)
def _digest(rec: dict) -> str:
    t = templates().get(rec.get("template")) or {"custom": rec.get("check_url")}
    return hashlib.sha256(json.dumps({"t": t, "v": adapter_version()}, sort_keys=True).encode()).hexdigest()


def _iso(ts) -> str | None:
    return _dt.datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds") if ts else None


def registry(st: State, *, now: float | None = None) -> list[dict]:
    """One `browser` entry for the runtime plus one per site (P86 closed schema; no new enums, credential_name null)."""
    now = time.time() if now is None else now
    enabled = browser.settings(st)["enabled"]
    rt = browser.runtime(st) if enabled else None
    rt_ok = bool(rt)
    man_digest = browser.manifest_digest()
    entries = [{
        "id": "browser-runtime", "kind": "browser", "title": {"zh": "专用浏览器", "en": "Dedicated browser"},
        "source_ref": "browser/runtime", "scope": [], "provides": ["browser.runtime"], "depends_on": [],
        "status": "ready" if rt_ok else "unavailable", "credential_name": None,
        "check": {"status": "ok" if rt_ok else ("unsupported" if not enabled else "offline"), "adapter_id": "browser-local",
                  "last_verified_at": _iso(now) if rt_ok else None, "expires_at": _iso(now + FRESH) if rt_ok else None,
                  "source_digest": man_digest}}]
    for sid, rec in sorted(load(st)["sites"].items()):
        t = templates().get(rec.get("template"))
        title = t["title"] if t else {"zh": rec.get("title") or sid, "en": rec.get("title") or sid}
        status, fresh = rec.get("status", "unknown"), not _stale(rec, now)
        if not enabled:
            entry_status, chk = "unavailable", "unsupported"
        elif not rt_ok:
            entry_status, chk = "unavailable", "offline"
        elif status == "logged_in" and fresh:
            entry_status, chk = "ready", "ok"
        elif status == "logged_in":
            entry_status, chk = "stale", "ok"
        elif status == "auth_required":
            entry_status, chk = "unavailable", "auth_required"
        elif status == "unavailable":
            entry_status, chk = "unavailable", "offline"
        else:
            entry_status, chk = "unverified", "timeout" if rec.get("reason") == "timeout" else "unsupported"
        entries.append({
            "id": f"browser-{sid}", "kind": "browser", "title": title, "source_ref": f"browser/sites/{sid}",
            "scope": [rec["origins"][0], "read"], "provides": [f"browser.{sid}.read"], "depends_on": ["browser-runtime"],
            "status": entry_status, "credential_name": None,
            "check": {"status": chk, "adapter_id": "site-safe", "last_verified_at": _iso(rec.get("checked_at")),
                      "expires_at": _iso(rec["checked_at"] + FRESH) if entry_status == "ready" else None,
                      "source_digest": _digest(rec)}})
    return entries


def first_run_probe(st: State, item: str, ctx: dict) -> tuple[str, str | None]:
    """Trusted first-run adapter: existing local evidence only; never download, log in or start a site check."""
    if "browser_evidence" not in ctx:ctx["browser_evidence"] = {e["id"]: e for e in registry(st)}
    entries = ctx["browser_evidence"]
    if entries["browser-runtime"]["status"] != "ready":
        return "attention", "browser_off" if not browser.settings(st)["enabled"] else "browser_not_running"
    if item == "browser":return "verified", None
    mapping = {"gmail": ("gmail",), "youtube": ("youtube-studio",), "bili": ("bilibili",),
               "douyin": ("douyin-creator",), "wechat": ("mp-weixin", "channels-weixin")}
    ids = mapping.get(item)
    if item == "sites":ids = tuple(s for s in load(st)["sites"] if s.startswith("custom-"))
    rows = [entries.get("browser-" + sid) for sid in ids or ()]
    if not rows or any(e is None for e in rows):return "waiting_local", "site_not_added"
    if all(e["status"] == "ready" for e in rows):return "verified", None
    if any(e["check"]["status"] == "auth_required" for e in rows):return "waiting_local", "site_signin_required"
    return "attention", "site_check_stale" if any(e["status"] == "stale" for e in rows) else "site_check_needed"


# ---------------------------------------------------------------- the daily check's clock
def daily_path(st: State) -> pathlib.Path:
    return browser.bdir(st) / "daily.json"


def daily_due(st: State, now: _dt.datetime | None = None) -> bool:
    """Due once a day at check_time (local); a computer that slept through it catches up once when it wakes."""
    now = now or _dt.datetime.now().astimezone()
    hh, mm = (int(x) for x in browser.settings(st)["check_time"].split(":"))
    at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if now < at:
        return False
    last = browser._read_json(daily_path(st)) or {}
    return last.get("date") != now.date().isoformat()


def daily_done(st: State, now: _dt.datetime | None = None) -> None:
    now = now or _dt.datetime.now().astimezone()
    browser._write_json(st, daily_path(st), {"date": now.date().isoformat(), "at": int(now.timestamp())})


# ---------------------------------------------------------------- Telegram QR consent (ADR-A194 §5, Leo item 6)
def consent_path(st: State) -> pathlib.Path:
    return browser.bdir(st) / "consent.json"


def tg_consent(st: State) -> bool:
    d = browser._read_json(consent_path(st))
    return isinstance(d, dict) and d.get("telegram_qr") is True


def set_tg_consent(st: State, on: bool, device: str | None) -> None:
    browser._write_json(st, consent_path(st), {"telegram_qr": bool(on), "device": device, "at": int(time.time())})
    st.log("browser_tg_consent", on=bool(on), device=device)


def telegram_qr(st: State, value) -> dict:
    """status | off. Turning it ON is only the owner's tap on a paired phone's login card — never the Agent."""
    if value in (None, "status"):
        return {"ok": True, "telegram_qr": tg_consent(st),
                "note": "on = login QR codes also go to the owner's Telegram private chat (Telegram can read them)"}
    if value == "off":
        set_tg_consent(st, False, None)
        return {"ok": True, "telegram_qr": False}
    return {"ok": False, "reason": "owner_only",
            "detail": "only the owner can turn this on, from a login card on a paired phone / 只能由主人在手机登录卡上打开"}
