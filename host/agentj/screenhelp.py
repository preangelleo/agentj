"""P411 (#172 + #76): screen help — `agentj qr push` and `agentj screen ask` put a picture on the owner's phones.

Browser automation stops at a sign-in QR code, a captcha or a "are you human" page. The Agent (or the owner's own script —
systemd / cron, no Agent session) sends the picture to the owner's paired remotes and waits:
  * `agentj qr push [<png> | --page [--selector CSS]]` — the QR code cut out of the picture (qrcrop.py: local zxing-cpp,
    the owner's vision model only as an opt-in fallback, the cut decoded again and used only when it matches; else the
    original), shown large with a countdown; 「我扫好了」 / 「取消」. `--wait-until page-change` watches the browser tab and
    closes the card by itself when the page moves on (the scan worked).
  * `agentj screen ask [<png> | --page | --screen] --question …` — the whole page / screen in high resolution with one
    question; the owner scans, taps 「已完成」, or types a short answer (a verification code) that goes back to the caller.

The card (help_card) is NOT chat history: like P114's sign-in card it lives in serve's memory only, goes only to this
seat's paired remotes inside the end-to-end encrypted session (never friends, Bots, Telegram or e-mail), and is wiped when it
ends (answer / cancel / expiry ≤ TTL_MAX / Stop everything / restart). Only a device holding an approval key answers. The
typed answer is kept in memory until the caller reads it (≤ KEEP_S) and is never logged; one that looks like a key or
password is refused on the phone (credentials go through `agentj secret request`, never through this card).

The channel is narrow on purpose (#76): elevate.sock (same uid only, reachable from inside the Agent fence); pictures only
(PNG / JPEG / WebP by their leading bytes), ≤ IMG_MAX bytes; RATE cards a minute per source and RATE_ALL in total;
MAX_OPEN open at once; a local log line per card and per outcome (id, kind, caller, bytes — never the picture, the
question or the answer). Untrusted pixels are parsed only in the caller's process (the crop), never in serve; serve's
browser capture is fixed CDP calls (the selector travels as JSON data into a fixed function, like browser_cdp.find_qr).
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time

IMG_MAX = 1_400_000              # bytes of one picture: base64 + JSON stays under the 2 MiB §10.1 fragment total
READ_MAX = 32 * 1024 * 1024      # a file to cut a QR code from may be bigger than what is sent
TEXT_MAX = 120                   # the caption / question (characters)
ANSWER_MAX = 200                 # the owner's typed answer
TTL = {"qr": (120, 30, 600), "screen": (300, 30, 900)}     # default, min, max seconds
TTL_MAX = 900
MAX_OPEN = 4
RATE, RATE_ALL = 6, 20           # cards a minute per source / from everyone
WAIT_MAX = 170                   # one blocking call (a harness's shell timeout is often 2–3 min); then `screen wait <id>`
KEEP_S, KEEP_MAX = 1800, 32      # finished outcomes kept for `screen wait`
PHONE_TYPES = ("help_done", "help_text", "help_cancel")
KINDS = ("qr", "screen")
ENDS = ("done", "answered", "cancelled", "expired", "page_changed", "withdrawn", "stopped", "gone")
_ID = re.compile(r"[0-9a-f]{32}")
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def sniff(img: bytes) -> str | None:
    if img.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if img.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if img[:4] == b"RIFF" and img[8:12] == b"WEBP":
        return "image/webp"
    return None


def _clip(s, n: int) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[:n - 1] + "…"


class Limiter:
    def __init__(self, clock=time.monotonic):
        self.clock, self.hits = clock, {}

    def take(self, key: str, limit: int, window: float = 60.0) -> bool:
        now = self.clock()
        q = [t for t in self.hits.get(key, []) if now - t < window]
        if len(q) >= limit:
            self.hits[key] = q
            return False
        q.append(now)
        self.hits[key] = q
        return True


# ------------------------------------------------------------------ serve's side (owned by elevate.Elevator)
class HelpCards:
    def __init__(self, elev):
        self.elev, self.host, self.st = elev, elev.host, elev.st
        self.cards: dict[str, dict] = {}
        self.results: dict[str, dict] = {}
        self.limiter = Limiter()
        self.bg: set = set()

    # ---------------------------------------------------------- the caller asks
    def _check(self, req: dict) -> dict:
        kind = req.get("kind")
        if kind not in KINDS:
            raise ValueError("kind: qr | screen")
        img = req.get("img")
        if not isinstance(img, str) or len(img) > IMG_MAX * 4 // 3 + 8:
            raise ValueError("img: base64, at most %d bytes" % IMG_MAX)
        raw = base64.b64decode(img, validate=True)
        mime = sniff(raw)
        if not mime:
            raise ValueError("img: a PNG, JPEG or WebP picture")
        if len(raw) > IMG_MAX:
            raise ValueError("img: at most %d bytes" % IMG_MAX)
        text = req.get("text", "")
        if not isinstance(text, str):
            raise ValueError("text")
        from .privacy import owner_text
        text = owner_text(_clip(text, TEXT_MAX))
        dflt, lo, hi = TTL[kind]
        ttl = req.get("ttl")
        ttl = dflt if not isinstance(ttl, int) or isinstance(ttl, bool) else min(max(ttl, lo), hi)
        reply = req.get("reply", "text" if kind == "screen" else "done")
        if reply not in ("done", "text"):
            raise ValueError("reply: done | text")
        src = req.get("from") or ""
        if not isinstance(src, str) or (src and not NAME_RE.fullmatch(src)):
            raise ValueError("from: letters, digits, . _ - (1–64)")
        by = "agent" if req.get("by") == "agent" else "auto"
        return {"kind": kind, "img": bytearray(raw), "mime": mime, "text": text, "ttl": ttl, "reply": reply,
                "from": src or ("agent" if by == "agent" else "script"), "by": by}

    async def push(self, req: dict) -> dict:
        try:
            c = self._check(req)
        except (ValueError, TypeError) as e:
            return {"result": "bad", "detail": str(e)[:120]}
        if self.host.stopped():
            return {"result": "stopped"}
        if not self.host._approvers():
            return {"result": "no_device"}
        if len(self.cards) >= MAX_OPEN:
            return {"result": "busy"}
        if not self.limiter.take(c["by"] + ":" + c["from"], RATE) or not self.limiter.take("*", RATE_ALL):
            self.st.log("screen_help_refused", kind=c["kind"], by=c["by"], reason="rate")
            return {"result": "rate"}
        cid = secrets.token_hex(16)
        loop = asyncio.get_running_loop()
        c.update(id=cid, n=secrets.token_hex(16), until=int(time.time()) + c["ttl"], deadline=time.monotonic() + c["ttl"],
                 fut=loop.create_future())
        c["timer"] = loop.call_later(c["ttl"], lambda: self._end(c, "expired"))
        self.cards[cid] = c
        devices = sum(1 for s in list(self.host.sessions.values()) if s.state == "ready" and self.st.is_allowed(s.pub))
        self.st.log("screen_help", id=cid, kind=c["kind"], by=c["by"], src=c["from"], bytes=len(c["img"]), ttl=c["ttl"],
                    count=devices)                      # never the picture, the question or the answer
        self.host.push_notify("ask")
        await self.host._send_ready(lambda s: self.card_msg(c))
        out = {"result": "sent", "id": cid, "ttl": c["ttl"], "devices": devices}
        wait = req.get("wait")
        if isinstance(wait, (int, float)) and not isinstance(wait, bool) and wait > 0:
            res = await self.wait({"id": cid, "wait": wait})
            return {**out, **res}
        return out

    async def wait(self, req: dict) -> dict:
        cid = req.get("id")
        if not isinstance(cid, str) or not _ID.fullmatch(cid):
            return {"result": "unknown"}
        c = self.cards.get(cid)
        if c is not None:
            wait = req.get("wait")
            wait = min(max(float(wait), 0.0), WAIT_MAX) if isinstance(wait, (int, float)) and not isinstance(wait, bool) \
                else WAIT_MAX
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(c["fut"]), wait)
            if cid in self.cards:
                return {"result": "pending", "id": cid, "secs": max(0, int(c["deadline"] - time.monotonic()))}
        r = self.results.get(cid)
        if r is None:
            return {"result": "unknown"}
        out = {"result": r["result"], "id": cid}
        if r["result"] == "answered":
            if r.get("text") is not None:
                out["answer"] = r["text"]
                r["text"] = None                         # handed over once, then gone from memory
            else:
                out["answer_taken"] = True
        return out

    async def end_req(self, req: dict) -> dict:
        """The caller closes its own card: `page_changed` (the browser moved on) or `withdrawn`."""
        cid, why = req.get("id"), req.get("result")
        c = self.cards.get(cid) if isinstance(cid, str) else None
        if why not in ("page_changed", "withdrawn"):
            return {"result": "bad", "detail": "result: page_changed | withdrawn"}
        if c is None:
            return await self.wait({"id": cid, "wait": 0})
        self._end(c, why)
        return {"result": why, "id": cid}

    # ---------------------------------------------------------- the browser (fixed CDP calls only)
    async def capture(self, req: dict) -> dict:
        return await asyncio.to_thread(self._capture, req)

    async def page(self, req: dict) -> dict:
        return await asyncio.to_thread(self._page, req)

    def _capture(self, req: dict) -> dict:
        from . import browser
        from .browser_cdp import CDP, CDPError
        tab, sel = req.get("tab"), req.get("selector")
        if tab is not None and (not isinstance(tab, str) or not 0 < len(tab) <= 200):
            return {"result": "bad", "detail": "tab: a piece of the page address (≤ 200)"}
        if sel is not None and (not isinstance(sel, str) or not 0 < len(sel) <= 300):
            return {"result": "bad", "detail": "selector: one CSS selector (≤ 300)"}
        rt = browser.runtime(self.st)
        if not rt:
            return {"result": "no_browser"}
        try:
            with CDP(rt["ws"]) as cdp:
                tid, session, url = _pick_tab(cdp, tab)
                if not tid:
                    return {"result": "no_tab"}
                # A background tab never paints: Page.captureScreenshot then hangs (60 s, 10-20). Bring it to the
                # front first; it stays there — the owner is about to look at exactly this page anyway.
                with contextlib.suppress(CDPError):
                    cdp.call("Target.activateTarget", {"targetId": tid}, timeout=5)
                params = {"format": "png", "captureBeyondViewport": False, "fromSurface": True}
                if sel:
                    r = cdp.call("Runtime.evaluate", {"expression": _RECT % json.dumps(sel), "returnByValue": True},
                                 session=session, timeout=5)
                    v = (r.get("result") or {}).get("value")
                    if not (isinstance(v, list) and len(v) == 4):
                        return {"result": "no_element"}
                    x, y, w, h = (float(a) for a in v)
                    params["clip"] = {"x": max(0.0, x - 8), "y": max(0.0, y - 8), "width": w + 16, "height": h + 16,
                                      "scale": 2}
                data = base64.b64decode(cdp.call("Page.captureScreenshot", params, session=session, timeout=20)["data"])
                mime = "image/png"
                for q in (85, 70):                       # a big page as JPEG rather than nothing
                    if len(data) <= IMG_MAX:
                        break
                    data = base64.b64decode(cdp.call("Page.captureScreenshot", {**params, "format": "jpeg", "quality": q},
                                                     session=session, timeout=20)["data"])
                    mime = "image/jpeg"
                if len(data) > IMG_MAX:
                    return {"result": "too_big"}
                return {"result": "ok", "img": base64.b64encode(data).decode(), "mime": mime, "target": tid,
                        "page": _h(url)}
        except (CDPError, KeyError, ValueError):
            return {"result": "failed"}

    def _page(self, req: dict) -> dict:
        from . import browser
        from .browser_cdp import CDP, CDPError, present
        tid, sel = req.get("target"), req.get("selector")
        if not isinstance(tid, str) or not re.fullmatch(r"[0-9A-Za-z]{8,64}", tid):
            return {"result": "bad"}
        rt = browser.runtime(self.st)
        if not rt:
            return {"result": "gone"}
        try:
            with CDP(rt["ws"]) as cdp:
                if not cdp.tab_alive(tid):
                    return {"result": "gone"}
                s = cdp.attach(tid)
                href, _ = cdp.location(s)
                out = {"result": "ok", "page": _h(href)}
                if isinstance(sel, str) and 0 < len(sel) <= 300:
                    out["present"] = present(cdp, s, [sel])
                cdp.detach(s)
                return out
        except CDPError:
            return {"result": "failed"}

    # ---------------------------------------------------------- the phones
    def card_msg(self, c: dict) -> dict:
        return {"t": "help_card", "id": c["id"], "n": c["n"], "kind": c["kind"], "mime": c["mime"],
                "img": base64.b64encode(bytes(c["img"])).decode(), "text": c["text"], "reply": c["reply"],
                "by": c["by"], "from": c["from"], "ttl": max(0, int(c["deadline"] - time.monotonic()))}

    def open_msgs(self, s=None) -> list[dict]:
        return [self.card_msg(c) for c in list(self.cards.values())]

    async def on_ready(self, s) -> None:
        for m in self.open_msgs(s):
            await self.host.send_app(s, m)

    async def on_phone(self, s, obj: dict) -> None:
        t = obj.get("t")
        if not self.st.sign_key(s.device):               # only a device that may approve (same rule as every card)
            self.st.log("screen_help_refused", device=s.device, reason="no_key")
            return
        cid = obj.get("id")
        c = self.cards.get(cid) if isinstance(cid, str) and _ID.fullmatch(cid) else None
        if c is None or obj.get("n") != c["n"]:
            return await self.host.send_app(s, {"t": "help_err", "id": cid if isinstance(cid, str) else "", "why": "gone"})
        if t == "help_cancel":
            return self._end(c, "cancelled", device=s.device)
        if t == "help_done":
            return self._end(c, "done", device=s.device)
        if t == "help_text":
            text = obj.get("text")
            if c["reply"] != "text" or not isinstance(text, str) or not text.strip() or len(text) > ANSWER_MAX:
                return await self.host.send_app(s, {"t": "help_err", "id": cid, "why": "shape"})
            text = text.strip()
            from .taskspec import secret_like
            if secret_like(text):                         # a password / key never travels this way
                self.st.log("screen_help_refused", id=cid, device=s.device, reason="secret")
                return await self.host.send_app(s, {"t": "help_err", "id": cid, "why": "secret"})
            return self._end(c, "answered", device=s.device, text=text)

    def _end(self, c: dict, result: str, device: str | None = None, text: str | None = None) -> None:
        if self.cards.pop(c["id"], None) is None:
            return
        with contextlib.suppress(Exception):
            c["timer"].cancel()
        c["img"][:] = b"\0" * len(c["img"])
        c["img"] = bytearray()
        self._sweep()
        self.results[c["id"]] = {"result": result, "text": text, "ended": time.monotonic()}
        if not c["fut"].done():
            c["fut"].set_result(result)
        self.st.log("screen_help_end", id=c["id"], kind=c["kind"], result=result, device=device)
        done = {"t": "help_end", "id": c["id"], "result": result}
        from . import bg
        with contextlib.suppress(RuntimeError):
            bg.spawn(self.host._send_ready(lambda s: done), "help_end", bg.host_log(self.host))

    def _sweep(self) -> None:
        now = time.monotonic()
        for k in [k for k, r in self.results.items() if now - r["ended"] > KEEP_S]:
            self.results.pop(k, None)
        while len(self.results) >= KEEP_MAX:
            self.results.pop(next(iter(self.results)))

    def end_all(self, result: str) -> None:
        for c in list(self.cards.values()):
            self._end(c, result)


def _h(url) -> str:
    return hashlib.sha256(str(url or "").encode()).hexdigest()[:16]


# A fixed function (the selector is JSON data, never code): the first visible match, scrolled into view → its rectangle.
_RECT = """(function(sel){ var l; try { l = document.querySelectorAll(sel); } catch (e) { return null; }
  for (var i=0;i<l.length;i++){ var el=l[i], r=el.getBoundingClientRect(), cs=getComputedStyle(el);
    if (r.width<8||r.height<8||cs.visibility==='hidden'||cs.display==='none'||+cs.opacity===0) continue;
    el.scrollIntoView({block:'center',inline:'center'}); r=el.getBoundingClientRect();
    return [r.left+scrollX, r.top+scrollY, r.width, r.height]; }
  return null; })(%s)"""


def _pick_tab(cdp, tab: str | None):
    """(targetId, session, url) of the page to show: a visible page first; `tab` = a piece of its address."""
    pages = [t for t in cdp.call("Target.getTargets").get("targetInfos", []) if t.get("type") == "page"
             and not str(t.get("url", "")).startswith(("devtools://", "chrome-extension://"))]
    if tab:
        pages = [t for t in pages if tab in str(t.get("url", ""))]
    first = None
    for t in pages[:12]:
        s = cdp.attach(t["targetId"])
        r = cdp.call("Runtime.evaluate", {"expression": "document.visibilityState", "returnByValue": True}, session=s,
                     timeout=5)
        if (r.get("result") or {}).get("value") == "visible":
            if first:
                cdp.detach(first[1])
            return t["targetId"], s, t.get("url", "")
        if first is None:
            first = (t["targetId"], s, t.get("url", ""))
        else:
            cdp.detach(s)
    if first:
        with contextlib.suppress(Exception):
            cdp.call("Page.bringToFront", session=first[1], timeout=5)
        return first
    return None, None, None


# ------------------------------------------------------------------ the CLI (runs as the caller)
NOTE = {
    "sent": {"zh": "已推到主人的手机（{devices} 台遥控器在线），卡片 {ttl} 秒内有效。需要结果时运行 agentj screen wait {id}。",
             "en": "On the owner's phone ({devices} remotes online), valid {ttl} s. Run agentj screen wait {id} for the outcome."},
    "done": {"zh": "主人点了「已完成」（扫码/处理好了）。先检查页面确实已经往下走了，再继续。",
             "en": "The owner tapped Done. Check that the page really moved on before carrying on."},
    "answered": {"zh": "主人回填了答案（answer 字段）。只把它填进当前这一步，不要记下来、不要转述到别处。",
                 "en": "The owner typed an answer (the answer field). Use it for this step only; never store or repeat it."},
    "page_changed": {"zh": "浏览器页面已经变了（多半扫码成功），卡片已自动关闭。继续自动化。",
                     "en": "The browser page changed (the scan most likely worked); the card closed by itself. Carry on."},
    "cancelled": {"zh": "主人在手机上取消了。不要重试同一张卡；停下来告诉主人卡在哪一步。",
                  "en": "The owner cancelled. Do not resend the same card; stop and tell the owner where it is stuck."},
    "expired": {"zh": "卡片过期了，没有人处理。码可能也过期了：刷新页面后再推一次，或停下来告诉主人。",
                "en": "The card expired unanswered. The code may have expired too: refresh the page and push again, or stop."},
    "pending": {"zh": "卡片还在手机上，主人还没处理。不要说已经完成；运行 agentj screen wait {id} 继续等。",
                "en": "Still on the phone, not answered yet. Do not say it is done; run agentj screen wait {id} to keep waiting."},
    "withdrawn": {"zh": "卡片已撤回。", "en": "The card was withdrawn."},
    "answer_taken": {"zh": "答案已经交给过调用方一次，不再重复给出。", "en": "The answer was already handed over once."},
    "no_device": {"zh": "没有可作答的已配对遥控器，没有推出去。改用文字告诉主人。",
                  "en": "No paired remote that can answer: nothing was sent. Tell the owner in text."},
    "stopped": {"zh": "主人按了紧急停止，没有推出去。", "en": "Emergency stop is on: nothing was sent."},
    "gone": {"zh": "Agent J 停止或重启了，卡片已失效。需要的话重新推送。", "en": "Agent J stopped or restarted; the card is gone."},
    "busy": {"zh": f"手机上已经有 {MAX_OPEN} 张求助卡没处理，先等等。", "en": f"{MAX_OPEN} help cards are already open; wait."},
    "rate": {"zh": "推得太频繁（每分钟上限），稍后再试。", "en": "Too many cards a minute; try again later."},
    "unknown": {"zh": "找不到这张卡（过期太久或 Agent J 重启过）。", "en": "No such card (too old, or Agent J restarted)."},
    "no_browser": {"zh": "Agent J 的浏览器没有在运行（agentj browser status）。", "en": "Agent J's browser is not running."},
    "no_tab": {"zh": "浏览器里没有可截的页面（--tab 对不上？）。", "en": "No page to capture (does --tab match?)."},
    "no_element": {"zh": "页面上找不到 --selector 指的元素。去掉 --selector 改发整页。",
                   "en": "No visible element for --selector. Drop it to send the whole page."},
    "no_screen": {"zh": "这台电脑截不了整个屏幕（没有图形界面或缺截图工具）。改用 --page 或给一张图片。",
                  "en": "Cannot capture the screen here (no display or no screenshot tool). Use --page or a picture file."},
    "too_big": {"zh": f"图片超过 {IMG_MAX // 1000} KB 上限，没有推出去。截小一点的范围（--selector）或压缩后再推。",
                "en": f"The picture is over {IMG_MAX // 1000} KB: not sent. Capture less (--selector) or compress it."},
    "failed": {"zh": "截图失败。", "en": "The capture failed."},
    "bad": {"zh": "参数不对，没有推出去：{detail}", "en": "Bad request, nothing sent: {detail}"},
    "unavailable": {"zh": "Agent J 没有在运行（或连不上），没有推出去。", "en": "Agent J is not running (or unreachable): nothing sent."},
}
EXIT = {"sent": 0, "done": 0, "answered": 0, "page_changed": 0, "cancelled": 3, "pending": 4, "bad": 2, "unavailable": 1}


def _lang() -> str:
    try:
        from . import main_identity
        from .state import State
        return main_identity.language_of(State().agent_config() or {})
    except Exception:  # noqa: BLE001
        return "zh"


def _call(req: dict, timeout: float) -> dict:
    from . import elevate
    from .names import elevate_sock
    from .state import State
    st = State()
    res = elevate.client_request(st, req, timeout=timeout, path=elevate_sock(st))
    if res.get("result") == "refused":            # an older serve without screen help
        return {"result": "unavailable"}
    return res


def _out(res: dict, a, extra: dict | None = None) -> None:
    lang = _lang()
    r = res.get("result", "unavailable")
    if r == "answered" and res.get("answer_taken"):
        r2 = "answer_taken"
    else:
        r2 = r
    out = {"ok": r in ("sent", "done", "answered", "page_changed", "pending"), "result": r,
           "shown": r in ("sent", "done", "answered", "page_changed", "pending", "cancelled", "expired", "withdrawn")}
    for k in ("id", "answer", "ttl", "devices", "secs"):
        if k in res:
            out[k] = res[k]
    if extra:
        out.update(extra)
    tpl = NOTE.get(r2, NOTE["unavailable"])[lang if lang in ("zh", "en") else "zh"]
    out["note"] = tpl.format(id=res.get("id", ""), ttl=res.get("ttl", ""), devices=res.get("devices", 0),
                             detail=res.get("detail", ""))
    print(json.dumps(out, ensure_ascii=False, indent=None if a.compact else 2))
    sys.exit(EXIT.get(r, 5))


def _caller() -> str:
    from . import ctl_auth
    return "agent" if set(os.environ) & set(ctl_auth.AGENT_ENV) else "auto"


def _run_out(cmd: list[str], timeout: float = 5) -> str:
    if not shutil.which(cmd[0]):
        return ""
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        return subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
                              check=True).stdout
    return ""


def wayland_output() -> str | None:
    """The one Wayland output to capture — the focused one (Hyprland follows the pointer, sway the keyboard),
    else the first active one. None = cannot tell (then grim takes everything, as before)."""
    for cmd in (["hyprctl", "-j", "monitors"], ["swaymsg", "-r", "-t", "get_outputs"]):
        try:
            mons = json.loads(_run_out(cmd) or "null")
        except ValueError:
            continue
        if not isinstance(mons, list):
            continue
        mons = [m for m in mons if isinstance(m, dict) and isinstance(m.get("name"), str) and m.get("active", True)
                and not m.get("disabled")]
        for m in mons:
            if m.get("focused"):
                return m["name"]
        if mons:
            return mons[0]["name"]
    return None


def x11_crop() -> str | None:
    """`WxH+X+Y` of the primary X11 monitor when there is more than one; None = one screen (or unknown)."""
    geo = [(" primary " in ln, g) for ln in _run_out(["xrandr", "--query"]).splitlines() if " connected" in ln
           for g in re.findall(r"\b(\d+x\d+\+\d+\+\d+)\b", ln)[:1]]
    if len(geo) < 2:
        return None
    return next((g for p, g in geo if p), geo[0][1])


def grab_screen(timeout: float = 20) -> tuple[bytes | None, str]:
    """One screen with the system's own tool (macOS screencapture · Wayland grim · X11 import / scrot /
    gnome-screenshot) into a private temporary folder, read back, deleted. → (bytes | None, why).
    With several monitors only one is taken (Wayland: the focused output; macOS: the main display; X11: the
    primary monitor) — the whole desktop is one stitched picture (7680×9440 measured 10-20), mostly empty."""
    d = tempfile.mkdtemp(prefix="agentj-screen-")
    try:
        out = wayland_output() if sys.platform != "darwin" and os.environ.get("WAYLAND_DISPLAY") else None
        crop = x11_crop() if sys.platform != "darwin" and os.environ.get("DISPLAY") else None
        for jpeg in (False, True):
            f = os.path.join(d, "s.jpg" if jpeg else "s.png")
            cmds = []
            if sys.platform == "darwin":
                cmds.append(["screencapture", "-x", "-D", "1", "-t", "jpg" if jpeg else "png", f])
            else:
                if os.environ.get("WAYLAND_DISPLAY"):
                    one = ["-o", out] if out else []
                    cmds.append(["grim", *one, "-t", "jpeg", "-q", "85", f] if jpeg else ["grim", *one, f])
                if os.environ.get("DISPLAY") and not jpeg:
                    cmds.append(["import", "-window", "root", *(["-crop", crop, "+repage"] if crop else []), f])
                    if not crop:
                        cmds += [["scrot", "-o", f], ["gnome-screenshot", "-f", f]]
            for cmd in cmds:
                if not shutil.which(cmd[0]):
                    continue
                with contextlib.suppress(OSError, subprocess.SubprocessError):
                    subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   timeout=timeout, check=True)
                    with open(f, "rb") as fh:
                        data = fh.read(IMG_MAX + 1)
                    if sniff(data) and len(data) <= IMG_MAX:
                        return data, ""
                    if sniff(data):
                        break                            # too big as PNG: the JPEG round
        return None, "no_screen"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _picture(a, kind: str) -> tuple[bytes | None, dict]:
    """→ (picture, {"target", "page"} when it came from the browser) or (None, {"result": why})."""
    if a.image:
        try:
            with open(a.image, "rb") as fh:
                data = fh.read(READ_MAX + 1)
        except OSError as e:
            return None, {"result": "bad", "detail": f"{a.image}: {type(e).__name__}"}
        if len(data) > READ_MAX or not sniff(data):
            return None, {"result": "bad", "detail": "a PNG, JPEG or WebP picture"}
        return data, {}
    if getattr(a, "screen", False):
        data, why = grab_screen()
        return (data, {}) if data else (None, {"result": why})
    req = {"t": "help_capture"}
    if a.tab:
        req["tab"] = a.tab
    if a.selector:
        req["selector"] = a.selector
    res = _call(req, 60)
    if res.get("result") != "ok":
        return None, res
    return base64.b64decode(res["img"]), {"target": res.get("target"), "page": res.get("page")}


def cmd_push(a, kind: str) -> None:
    by = _caller()
    if a.image and (a.page or getattr(a, "screen", False)):
        return _out({"result": "bad", "detail": "a picture file OR --page / --screen"}, a)
    if not a.image and not getattr(a, "screen", False):
        a.page = True
    data, src = _picture(a, kind)
    if data is None:
        return _out(src, a)
    extra = {}
    if kind == "qr" and not a.no_crop:
        from . import qrcrop
        cut, info = qrcrop.tidy(data, vision=a.vision) if sniff(data) == "image/png" else (None, {"reason": "not_png"})
        extra["crop"] = {k: info.get(k) for k in ("method", "codes", "reason", "ms") if info.get(k) is not None}
        if cut:
            data = cut
    if len(data) > IMG_MAX:
        return _out({"result": "too_big"}, a, extra)
    until = getattr(a, "wait_until", "answered" if kind == "screen" else "none")
    if until == "page-change" and not src.get("target"):
        return _out({"result": "bad", "detail": "--wait-until page-change needs the browser page (--page)"}, a, extra)
    text = a.text if kind == "qr" else a.question
    wait = 0 if until in ("none", "page-change") else min(max(a.wait_s, 0), WAIT_MAX)
    req = {"t": "help_push", "kind": kind, "img": base64.b64encode(data).decode(), "text": text or "",
           "ttl": a.ttl, "reply": getattr(a, "reply", "done"), "by": by, "wait": wait}
    if a.source:
        req["from"] = a.source
    res = _call(req, wait + 30)
    if until == "page-change" and res.get("result") == "sent":
        res = _watch_page(res, src, a.selector, min(max(a.wait_s, 0), TTL_MAX))
    _out(res, a, extra)


def _watch_page(sent: dict, src: dict, selector, budget: float) -> dict:
    """Poll: the card's own outcome, or the tab's address / the QR element changing → close the card as page_changed."""
    cid, end = sent["id"], time.monotonic() + budget
    while time.monotonic() < end:
        res = _call({"t": "help_wait", "id": cid, "wait": 2}, 30)
        if res.get("result") != "pending":
            return res
        pg = _call({"t": "help_page", "target": src["target"], **({"selector": selector} if selector else {})}, 30)
        if pg.get("result") == "gone" or (pg.get("result") == "ok" and (pg.get("page") != src.get("page")
                                                                           or pg.get("present") is False)):
            return _call({"t": "help_end", "id": cid, "result": "page_changed"}, 30)
    return {**_call({"t": "help_wait", "id": cid, "wait": 0}, 30), "id": cid}


def cmd_qr(a) -> None:
    cmd_push(a, "qr")


def cmd_screen_ask(a) -> None:
    cmd_push(a, "screen")


def cmd_screen_wait(a) -> None:
    wait = min(max(a.wait_s, 0), WAIT_MAX)
    _out(_call({"t": "help_wait", "id": a.id, "wait": wait}, wait + 30), a)


def cmd_screen_cancel(a) -> None:
    _out(_call({"t": "help_end", "id": a.id, "result": "withdrawn"}, 30), a)


def _common(p, kind: str) -> None:
    p.add_argument("image", nargs="?", help="图片文件（PNG/JPEG/WebP）；不给 = 截 Agent J 浏览器当前页面 / a picture file; "
                   "none = the current page of Agent J's browser")
    p.add_argument("--page", action="store_true", help="截 Agent J 浏览器当前页面（默认）/ capture the browser page (default)")
    p.add_argument("--selector", metavar="CSS", help="只截页面上这个元素 / capture only this element")
    p.add_argument("--tab", metavar="TEXT", help="网址里含这段文字的标签页 / the tab whose address contains this")
    p.add_argument("--ttl", type=int, help="卡片有效秒数（%d–%d，默认 %d）/ seconds the card stays" % (TTL[kind][1], TTL[kind][2],
                                                                                         TTL[kind][0]))
    p.add_argument("--from", dest="source", metavar="NAME", help="来源名（主人自己的脚本用）/ the source name a script shows")
    p.add_argument("--wait-s", type=float, default=110, help=f"最多等几秒（单次 ≤ {WAIT_MAX}）/ seconds to wait")
    p.add_argument("--compact", action="store_true", help="单行 JSON / one-line JSON")


def add_parser(sub) -> None:
    q = sub.add_parser("qr", help="扫码求助：把页面上的二维码裁好推到主人手机 / push the page's QR code to the owner's phone")
    qs = q.add_subparsers(dest="qr_cmd", required=True)
    p = qs.add_parser("push", help="裁出二维码推到主人手机（大图 + 倒计时）/ cut the QR code out and push it to the owner's phone",
                      description="默认截 Agent J 浏览器当前页面；本地检测（zxing-cpp）裁出二维码，裁后再解码一致才用，否则发原图。"
                                  "输出 JSON；只有 shown=true 才算推到了手机。/ Captures the browser page by default; the cut is "
                                  "used only when it decodes the same, else the original goes. Prints JSON. Exit 0 sent/done/"
                                  "page_changed, 3 cancelled, 4 pending, 5 not shown/expired, 2 bad, 1 Agent J not running.")
    _common(p, "qr")
    p.add_argument("--text", help="一句说明（≤ %d）/ one line under the code" % TEXT_MAX)
    p.add_argument("--wait-until", choices=("none", "answered", "page-change"), default="none",
                   help="none 推完就返回；answered 等主人点「我扫好了」；page-change 盯着页面，变了就自动关卡 / when to return")
    p.add_argument("--no-crop", action="store_true", help="不裁剪，发原图 / send the picture as it is")
    p.add_argument("--vision", action="store_true", help="本地认不出时用主人配置的视觉模型兜底（AGENTJ_VISION_URL / _MODEL / "
                   "_API_KEY）/ fall back to the owner's vision model")
    p.set_defaults(fn=cmd_qr)
    s = sub.add_parser("screen", help="屏幕求助：整页/整屏高清截图推给主人，等他扫码、点完成或回填文字 / screen help")
    ss = s.add_subparsers(dest="screen_cmd", required=True)
    p = ss.add_parser("ask", help="把截图和一个问题推到主人手机并等答案 / push a screenshot with a question and wait",
                      description="主人可以扫码、点「已完成」，或回一段文字（如验证码）。输出 JSON：result = answered（answer 字段）| "
                                  "done | cancelled | expired | pending | …；pending 时用 agentj screen wait <id> 继续等。/ The owner "
                                  "scans, taps Done or types a short answer (returned in `answer`).")
    _common(p, "screen")
    p.add_argument("--screen", action="store_true", help="截整个电脑屏幕（而不是浏览器页面）/ the whole screen")
    p.add_argument("--question", "-q", help="问主人的一句话（≤ %d），如「请扫码」「验证码是什么」/ the question" % TEXT_MAX)
    p.add_argument("--reply", choices=("text", "done"), default="text", help="text = 可以回填文字（默认）；done = 只要点完成 / "
                   "whether the owner can type an answer")
    p.set_defaults(fn=cmd_screen_ask, wait_until="answered")
    p = ss.add_parser("wait", help="继续等一张求助卡的结果 / keep waiting for a card's outcome")
    p.add_argument("id")
    p.add_argument("--wait-s", type=float, default=110)
    p.add_argument("--compact", action="store_true")
    p.set_defaults(fn=cmd_screen_wait)
    p = ss.add_parser("cancel", help="撤回一张求助卡 / withdraw a card")
    p.add_argument("id")
    p.add_argument("--compact", action="store_true")
    p.set_defaults(fn=cmd_screen_cancel)


def run_argparse_for_tests(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    add_parser(ap.add_subparsers(dest="cmd"))
    return ap.parse_args(argv)
