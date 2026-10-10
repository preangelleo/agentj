"""P114 (ADR-A194 §3): the smallest CDP client Agent J itself needs — create / close its own tabs, read a tab's URL, and crop a
login QR code. Fixed expressions only; nothing here evaluates text from a page, a model or a phone. It never reads cookies,
storage, passwords, auth headers or page text, and it closes only the tabs it created (never the owner's login tab)."""
from __future__ import annotations

import base64
import contextlib
import itertools
import json
import time

from websockets.sync.client import connect

MAX_FRAME = 32 * 1024 * 1024


class CDPError(Exception):
    pass


class CDP:
    def __init__(self, ws_url: str, timeout: float = 10.0):
        if not ws_url.startswith("ws://127.0.0.1:"):
            raise CDPError("loopback endpoints only")
        try:
            self.ws = connect(ws_url, open_timeout=timeout, max_size=MAX_FRAME, origin=None, proxy=None)
        except TypeError:          # websockets < 15: no proxy argument (and no proxy support either)
            self.ws = connect(ws_url, open_timeout=timeout, max_size=MAX_FRAME)
        except Exception as e:  # noqa: BLE001 — refused / timeout / handshake
            raise CDPError(type(e).__name__) from None
        self.ws = self.ws.__enter__() or self.ws   # the context-manager form (websockets ≥ 17 warns otherwise); close() exits it
        self.ids = itertools.count(1)
        self.timeout = timeout

    def call(self, method: str, params: dict | None = None, session: str | None = None, timeout: float | None = None) -> dict:
        i = next(self.ids)
        msg = {"id": i, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        try:
            self.ws.send(json.dumps(msg))
            end = time.monotonic() + (timeout or self.timeout)
            while True:
                left = end - time.monotonic()
                if left <= 0:
                    raise CDPError(f"{method}: timeout")
                raw = self.ws.recv(timeout=left)
                m = json.loads(raw)
                if m.get("id") == i:
                    if "error" in m:
                        raise CDPError(f"{method}: {str(m['error'].get('message', ''))[:120]}")
                    return m.get("result") or {}
        except CDPError:
            raise
        except Exception as e:  # noqa: BLE001 — closed socket, timeout
            raise CDPError(f"{method}: {type(e).__name__}") from None

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.ws.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------ tabs
    def new_tab(self, url: str, background: bool = True) -> str:
        return self.call("Target.createTarget", {"url": url, "background": background})["targetId"]

    def close_tab(self, target: str) -> None:
        with contextlib.suppress(CDPError):
            self.call("Target.closeTarget", {"targetId": target}, timeout=5)

    def attach(self, target: str) -> str:
        return self.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]

    def detach(self, session: str) -> None:
        with contextlib.suppress(CDPError):
            self.call("Target.detachFromTarget", {"sessionId": session}, timeout=5)

    def tab_alive(self, target: str) -> bool:
        try:
            return any(t.get("targetId") == target for t in self.call("Target.getTargets").get("targetInfos", []))
        except CDPError:
            return False

    def location(self, session: str) -> tuple[str, str]:
        """(location.href, document.readyState) of an attached tab — two fixed expressions, nothing else."""
        r = self.call("Runtime.evaluate", {"expression": "[location.href, document.readyState]", "returnByValue": True},
                      session=session, timeout=5)
        v = (r.get("result") or {}).get("value")
        if not isinstance(v, list) or len(v) != 2:
            return "", ""
        return str(v[0])[:4096], str(v[1])[:16]


def present(cdp: CDP, session: str, selectors: list[str]) -> bool:
    """Is any of the template's packaged selectors on the page? A boolean only — no text, no attributes."""
    expr = "(function(s){for(var i=0;i<s.length;i++){if(document.querySelector(s[i]))return true}return false})(%s)" % json.dumps(
        [x for x in selectors if isinstance(x, str)][:8])
    r = cdp.call("Runtime.evaluate", {"expression": expr, "returnByValue": True}, session=session, timeout=5)
    return (r.get("result") or {}).get("value") is True


# A fixed function (no page / model / phone text is ever evaluated): finds the QR element by the template's packaged
# selectors, scrolls it into view and returns its rectangle. Square-ish, 80–640 CSS px, visible — otherwise null.
_FIND_QR = """(function(sels){
  function ok(el){ if(!el) return null; var r=el.getBoundingClientRect();
    if(r.width<80||r.height<80||r.width>640||r.height>640) return null;
    if(Math.abs(r.width-r.height)>Math.max(r.width,r.height)*0.15) return null;
    var cs=getComputedStyle(el); if(cs.visibility==='hidden'||cs.display==='none'||+cs.opacity===0) return null;
    if(el.tagName==='IMG'&&!(el.complete&&el.naturalWidth>0)) return null;
    if(el.tagName!=='IMG'&&el.tagName!=='CANVAS'){ var im=el.querySelector('img'); if(im&&!(im.complete&&im.naturalWidth>0)) return null;
      if(!im&&!el.querySelector('canvas')&&!/url[(]/.test(cs.backgroundImage)) return null; }
    return el; }
  for (var i=0;i<sels.length;i++){ var list=document.querySelectorAll(sels[i]);
    for (var j=0;j<list.length;j++){ var el=ok(list[j]); if(el){ el.scrollIntoView({block:'center',inline:'center'});
      var r=el.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; } } }
  return null; })(%s)"""


def find_qr(cdp: CDP, session: str, selectors: list[str]) -> tuple[float, float, float, float] | None:
    """The QR rectangle in main-viewport CSS pixels; searches the main frame, then same-process child frames."""
    expr = _FIND_QR % json.dumps([s for s in selectors if isinstance(s, str)][:12])
    r = cdp.call("Runtime.evaluate", {"expression": expr, "returnByValue": True}, session=session, timeout=5)
    v = (r.get("result") or {}).get("value")
    if isinstance(v, list) and len(v) == 4:
        return tuple(float(x) for x in v)
    with contextlib.suppress(CDPError):
        tree = cdp.call("Page.getFrameTree", session=session, timeout=5).get("frameTree", {})
        for child in tree.get("childFrames", []) or []:
            fid = child.get("frame", {}).get("id")
            if not fid:
                continue
            ctx = cdp.call("Page.createIsolatedWorld", {"frameId": fid, "worldName": "agentj-qr"}, session=session).get("executionContextId")
            rr = cdp.call("Runtime.evaluate", {"expression": expr, "returnByValue": True, "contextId": ctx}, session=session, timeout=5)
            vv = (rr.get("result") or {}).get("value")
            if not (isinstance(vv, list) and len(vv) == 4):
                continue
            owner = cdp.call("DOM.getFrameOwner", {"frameId": fid}, session=session).get("backendNodeId")
            quad = cdp.call("DOM.getBoxModel", {"backendNodeId": owner}, session=session).get("model", {}).get("content", [])
            if len(quad) >= 2:
                return (float(quad[0]) + float(vv[0]), float(quad[1]) + float(vv[1]), float(vv[2]), float(vv[3]))
    return None


def capture_qr(cdp: CDP, session: str, selectors: list[str], pad: float = 8.0) -> bytes | None:
    """PNG bytes of the QR rectangle only (plus a small white margin) — never the rest of the page."""
    rect = find_qr(cdp, session, selectors)
    if rect is None:
        return None
    x, y, w, h = rect
    with contextlib.suppress(CDPError):           # a background tab may never paint: bring it forward for the capture
        cdp.call("Page.bringToFront", session=session, timeout=5)
    scroll = cdp.call("Runtime.evaluate", {"expression": "[scrollX, scrollY]", "returnByValue": True}, session=session).get(
        "result", {}).get("value") or [0, 0]
    clip = {"x": max(0.0, x - pad + float(scroll[0])), "y": max(0.0, y - pad + float(scroll[1])),
            "width": w + 2 * pad, "height": h + 2 * pad, "scale": 2}
    r = cdp.call("Page.captureScreenshot", {"format": "png", "clip": clip, "captureBeyondViewport": False,
                                            "fromSurface": True}, session=session, timeout=15)
    data = r.get("data")
    if not isinstance(data, str):
        return None
    png = base64.b64decode(data)
    return png if png.startswith(b"\x89PNG") and len(png) <= 512 * 1024 else None
