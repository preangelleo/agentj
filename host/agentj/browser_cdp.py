"""Local CDP operations for tabs, URLs and login QR codes. Evaluate fixed expressions only."""
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


# Locate and scroll to a visible QR element using packaged selectors.
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

# Read image or canvas pixels in an isolated world without raising the window.
# Cross-origin images still require CORS permission.
_QR_PIXELS = """(function(origin, sels){
  if (location.origin !== origin || document.readyState !== 'complete') return null;
  for (const sel of sels) for (const candidate of document.querySelectorAll(sel)) {
    const el = candidate.matches('img,canvas') ? candidate : candidate.querySelector('img,canvas');
    if (!el) continue;
    const rect = el.getBoundingClientRect(), style = getComputedStyle(el);
    if (rect.width < 80 || rect.height < 80 || rect.width > 640 || rect.height > 640 ||
      style.visibility !== 'visible' || style.display === 'none' || +style.opacity === 0) continue;
    const image = el.tagName === 'IMG';
    if (image && !el.complete) continue;
    const w = image ? el.naturalWidth : el.width, h = image ? el.naturalHeight : el.height;
    if (w < 21 || h < 21 || w > 640 || h > 640 || Math.abs(w-h) > Math.max(w,h)*0.15) continue;
    try {
      const out = document.createElement('canvas');
      out.width = 2*w+32; out.height = 2*h+32;
      const ctx = out.getContext('2d');
      ctx.fillStyle = '#fff'; ctx.fillRect(0,0,out.width,out.height);
      ctx.imageSmoothingEnabled = false; ctx.drawImage(el,16,16,2*w,2*h);
      const png = out.toDataURL('image/png');
      if (png.length <= 699080) return png.slice(22);
    } catch (e) { /* canvas tainted or unavailable; try the clipped browser fallback */ }
  }
  return null;
})(%s, %s)"""


FRAME_DEPTH = 3
MAX_SIDE = 640  # CSS pixels, before margin and scale
MIN_CONTRAST = 64
MIN_EDGES = 8


def painted(png: bytes) -> bool:
    """Check contrast and edges along five scan lines of the bounded PNG. An undecodable PNG is inconclusive."""
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
    """Traverse embedded frames only through allowlisted ancestors."""
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
    """Find the viewport rectangle in the main or allowed embedded document, or report loading/absent."""
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


def _qr_dom(cdp, session, selectors, tree, origins):
    root = tree.get("frame", {})
    evaluate = getattr(type(cdp), "qr_evaluate", None)
    for frame in itertools.chain((root,), _frames(tree, origins)):
        fid, origin = frame.get("id"), frame.get("securityOrigin")
        if not isinstance(fid, str) or not _origin_ok(origin, origins):
            continue
        try:
            ctx = cdp.call("Page.createIsolatedWorld", {"frameId": fid, "worldName": "agentj-qr"}, session=session,
                           timeout=5).get("executionContextId")
            if type(ctx) is not int:
                continue
            params = {"expression": _QR_PIXELS % (json.dumps(origin), json.dumps(selectors)),
                      "contextId": ctx, "returnByValue": True}
            r = (evaluate(cdp, session, params) if evaluate else
                 cdp.call("Runtime.evaluate", params, session=session, timeout=5))
            png = _png((r.get("result") or {}).get("value"))
            if png and painted(png):
                return png
        except CDPError:
            continue  # an unattached iframe, a navigated page, or a renderer going away
    return None


def _png(data):
    if not isinstance(data, str) or len(data) > 699052:
        return None
    try:
        png = base64.b64decode(data, validate=True)
    except ValueError:
        return None
    return png if png.startswith(b"\x89PNG\r\n\x1a\n") and len(png) <= 512 * 1024 else None


def _root_id(frame):
    return tuple(frame.get(k) for k in ("id", "loaderId", "securityOrigin"))


def capture_qr(cdp: CDP, session: str, selectors: list[str], pad: float = 8.0, origins=(), *, root_origins=()) -> bytes | None:
    """Capture the QR element with a margin. Distinguish absence, frame loading and a blank crop."""
    def frame_tree():
        return cdp.call("Page.getFrameTree", session=session, timeout=5).get("frameTree", {})

    before = None
    if root_origins:
        tree = frame_tree()
        before = _root_id(tree.get("frame", {}))
        if not all(isinstance(v, str) and v for v in before) or before[-1] not in root_origins:
            return None
        png = _qr_dom(cdp, session, [s for s in selectors if isinstance(s, str)][:12], tree, set(origins))
        if _root_id(frame_tree().get("frame", {})) != before:
            return None
        if png:
            return png
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
    if before is not None and _root_id(frame_tree().get("frame", {})) != before:
        return None
    png = _png(r.get("data"))
    if not png:
        return None
    if not painted(png):
        raise QRBlank("blank")
    return png
