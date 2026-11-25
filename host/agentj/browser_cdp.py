"""P114 (ADR-A194 §3): the smallest CDP client Agent J itself needs — create / close its own tabs, read a tab's URL, and crop a
login QR code. Fixed expressions only; nothing here evaluates text from a page, a model or a phone. It never reads cookies,
storage, passwords, auth headers or page text, and it closes only the tabs it created (never the owner's login tab)."""
from __future__ import annotations

import base64
import contextlib
import itertools
import json
import socket
import struct
import time
from urllib.parse import urlsplit

from websockets.sync.client import connect

MAX_FRAME = 32 * 1024 * 1024


class CDPError(Exception):
    pass


class CDPTimeout(CDPError):
    pass


class CDP:
    def __init__(self, ws_url: str, timeout: float = 10.0):
        try:
            endpoint = urlsplit(ws_url)
            port = endpoint.port
            local = endpoint.scheme == "ws" and bool(port) and endpoint.netloc == f"127.0.0.1:{port}"
        except (TypeError, ValueError):
            local = False
        if not local:
            raise CDPError("loopback endpoints only")
        sock = None
        deadline = time.monotonic() + timeout
        try:
            # A preconnected socket keeps the handshake on this host, including on clients that follow redirects.
            sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
            ws = connect(ws_url, sock=sock, open_timeout=max(0, deadline - time.monotonic()), max_size=MAX_FRAME, origin=None)
            self.ws = ws.__enter__() or ws
        except Exception as exc:  # refused, timeout, redirected or invalid handshake
            if sock is not None:
                sock.close()
            raise CDPError(type(exc).__name__) from None
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
                    raise CDPTimeout(f"{method}: timeout")
                raw = self.ws.recv(timeout=left)
                m = json.loads(raw)
                if m.get("id") == i:
                    if "error" in m:
                        raise CDPError(f"{method}: {str(m['error'].get('message', ''))[:120]}")
                    return m.get("result") or {}
        except TimeoutError:
            raise CDPTimeout(f"{method}: timeout") from None
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


FRAME_DEPTH = 3
MAX_SIDE = 640      # CSS px, same bound as _FIND_QR; the crop is at most (640 + 2 × 8) × 2 device px per side
MIN_CONTRAST = 64   # a QR always has dark and light modules
MIN_EDGES = 8       # ... and a scan line through its middle crosses many of them; an unpainted crop crosses none


def painted(png: bytes) -> bool:
    """Did the browser actually paint a code-like pattern into this crop? Reads only the luminance along five scan lines of
    our own browser's screenshot (never decodes the code or keeps pixels). This is the one place serve reads screenshot
    pixels: the PNG comes from Agent J's own Chromium encoder for a clip bounded by MAX_SIDE, capped at 512 KB, and only a
    boolean comes out. An unpainted window gives a flat crop (at most a
    solid band); a QR gives dozens of dark/light edges per line. An undecodable PNG is given the benefit of the doubt."""
    from .qrcrop import png_decode
    try:
        w, h = struct.unpack(">II", png[16:24])
    except struct.error:
        return True
    if w * h > ((MAX_SIDE + 16) * 2) ** 2:
        return False      # larger than any crop we ask for: refuse rather than decode
    img = png_decode(png)
    if img is None:
        return True
    ch, w = img.ch, img.w

    def luma(x, y):
        i = (y * w + x) * ch
        d = img.data
        return d[i] if ch < 3 else (d[i] * 3 + d[i + 1] * 6 + d[i + 2]) // 10

    lines = [[luma(x, img.h * k // 10) for x in range(w)] for k in (3, 4, 5, 6, 7)]
    lo, hi = min(min(r) for r in lines), max(max(r) for r in lines)
    if hi - lo < MIN_CONTRAST:
        return False
    mid = (lo + hi) // 2
    best = 0
    for r in lines:
        bits = [v > mid for v in r]
        best = max(best, sum(1 for i in range(1, w) if bits[i] != bits[i - 1]))
    return best >= MIN_EDGES


class QRBlank(CDPError):
    """The QR element was located but the browser painted nothing there (window hidden / screen asleep)."""


class QRFrameLoading(CDPError):
    """An embedded sign-in window of the template's own origins is still loading."""


def _frames(tree: dict, allowed: set, depth: int = 0):
    """Embedded windows to search: children of the page, and children of windows that are themselves on the list.
    A listed origin nested inside an unlisted window (an ad, a third-party widget) is never reached."""
    for child in tree.get("childFrames", []) or []:
        frame = child.get("frame", {})
        if not _origin_ok(frame.get("securityOrigin"), allowed):
            continue
        yield frame
        if depth + 1 < FRAME_DEPTH:
            yield from _frames(child, allowed, depth + 1)


def _origin_ok(origin, origins) -> bool:
    return isinstance(origin, str) and origin in origins


def locate_qr(cdp: CDP, session: str, selectors: list[str], origins=()) -> tuple[str, tuple | None]:
    """("found", rect in main-viewport CSS px) · ("loading", None) when an allowed embedded window is still loading ·
    ("absent", None). Searches the main frame, then embedded windows whose origin is one of the template's packaged
    origins (never an arbitrary third-party frame). A frame in another process (no isolated world) counts as absent."""
    expr = _FIND_QR % json.dumps([s for s in selectors if isinstance(s, str)][:12])
    r = cdp.call("Runtime.evaluate", {"expression": expr, "returnByValue": True}, session=session, timeout=5)
    v = (r.get("result") or {}).get("value")
    if isinstance(v, list) and len(v) == 4:
        return "found", tuple(float(x) for x in v)
    allowed = {o for o in origins if isinstance(o, str)}
    loading = False
    tree = cdp.call("Page.getFrameTree", session=session, timeout=5).get("frameTree", {})
    framed = "(function(){return [location.origin, %s]})()" % expr     # L2: the origin is re-read in the same evaluation
    for frame in _frames(tree, allowed):
        fid = frame.get("id")
        if not fid:
            continue
        try:
            ctx = cdp.call("Page.createIsolatedWorld", {"frameId": fid, "worldName": "agentj-qr"}, session=session,
                           timeout=5).get("executionContextId")
            st = cdp.call("Runtime.evaluate", {"expression": "document.readyState", "returnByValue": True,
                                               "contextId": ctx}, session=session, timeout=5)
            if (st.get("result") or {}).get("value") != "complete":
                loading = True
                continue
            rr = cdp.call("Runtime.evaluate", {"expression": framed, "returnByValue": True, "contextId": ctx},
                          session=session, timeout=5)
            got = (rr.get("result") or {}).get("value")
            if not (isinstance(got, list) and len(got) == 2 and _origin_ok(got[0], allowed)):
                continue          # navigated away between the frame tree and this evaluation
            vv = got[1]
            if not (isinstance(vv, list) and len(vv) == 4):
                continue          # loaded, no usable QR element (yet): the caller keeps polling until its deadline
            # relative to its own window; DOM.getBoxModel reports the window's owner element in main-viewport pixels
            owner = cdp.call("DOM.getFrameOwner", {"frameId": fid}, session=session, timeout=5).get("backendNodeId")
            quad = cdp.call("DOM.getBoxModel", {"backendNodeId": owner}, session=session,
                            timeout=5).get("model", {}).get("content", [])
            if len(quad) < 2:
                continue
            x, y = float(vv[0]) + float(quad[0]), float(vv[1]) + float(quad[1])
            return "found", (x, y, float(vv[2]), float(vv[3]))
        except CDPTimeout:
            raise
        except CDPError:
            continue
    return ("loading" if loading else "absent"), None


def find_qr(cdp: CDP, session: str, selectors: list[str], origins=()) -> tuple[float, float, float, float] | None:
    """The QR rectangle in main-viewport CSS pixels, or None."""
    state, rect = locate_qr(cdp, session, selectors, origins)
    return rect if state == "found" else None


def capture_qr(cdp: CDP, session: str, selectors: list[str], pad: float = 8.0, origins=()) -> bytes | None:
    """PNG bytes of the QR element's rectangle only (plus a small white margin) — never the rest of the page.
    None: no QR element. Raises QRFrameLoading while an allowed embedded window is still loading, QRBlank when the element
    is there but the crop came back unpainted."""
    state, rect = locate_qr(cdp, session, selectors, origins)
    if state == "loading":
        raise QRFrameLoading("loading")
    if rect is None:
        return None
    x, y, w, h = rect
    if not (80 <= w <= MAX_SIDE and 80 <= h <= MAX_SIDE):   # never trust the page's own rectangle for the clip size
        return None
    with contextlib.suppress(CDPError):           # a background tab may never paint: bring it forward for the capture
        cdp.call("Page.bringToFront", session=session, timeout=5)
    scroll = cdp.call("Runtime.evaluate", {"expression": "[scrollX, scrollY]", "returnByValue": True}, session=session).get(
        "result", {}).get("value") or [0, 0]
    clip = {"x": max(0.0, x - pad + float(scroll[0])), "y": max(0.0, y - pad + float(scroll[1])),
            "width": w + 2 * pad, "height": h + 2 * pad, "scale": 2}
    r = cdp.call("Page.captureScreenshot", {"format": "png", "clip": clip, "captureBeyondViewport": False,
                                            "fromSurface": True}, session=session, timeout=10)
    data = r.get("data")
    if not isinstance(data, str):
        return None
    png = base64.b64decode(data)
    if not png.startswith(b"\x89PNG") or len(png) > 512 * 1024:
        return None
    if not painted(png):
        raise QRBlank("blank")
    return png
