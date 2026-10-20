"""P411 (#172): cut the QR code out of a screenshot — `agentj qr push` sends the owner a clean code to scan, not a whole page.

Order (the reference implementation's measurements: local detection is the most accurate and free; a vision model is only
a fallback; Jev only gives axis-aligned boxes and is not used):
  1. local: zxing-cpp (one ~1 MB wheel, no numpy / OpenCV) finds and decodes every QR code in the picture;
  2. only when that decodes nothing AND the owner configured a vision model (AGENTJ_VISION_URL / _MODEL / _API_KEY in the
     caller's own environment, `--vision`): the model returns the code's four corners;
  3. the code (the largest one) is cut out along its bounding box, a fresh white quiet zone is drawn around it (never the
     page's own pixels, so no dark background comes along), and the cut is decoded again. It is used only when it decodes
     to the same content as the original (local path) / to a QR code at all (vision path); otherwise the original goes.
Never raises: the worst outcome is "send the original". Content is compared by SHA-256 only and never returned or logged
(a login QR is a credential for its validity). PNG in / PNG out with the stdlib (zlib); any other picture type is sent as is.
Runs in the CALLER's process (the Agent or the owner's own script) — untrusted pixels are never parsed inside serve.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import struct
import time
import urllib.request
import zlib

PNG_SIG = b"\x89PNG\r\n\x1a\n"
MAX_PIXELS = 40_000_000          # 8K × 5K; bigger pictures are not decoded (sent as they are)
PAD = 0.10                       # white quiet zone around the cut, × the code's side (QR wants ≥ 4 modules)
MIN_SIDE = 40                    # a code smaller than this many pixels is not cut
VISION_ENV = ("AGENTJ_VISION_URL", "AGENTJ_VISION_MODEL", "AGENTJ_VISION_API_KEY")
VISION_PROMPT = (
    "This image contains one QR code. Return the 4 outer corners of the QR code's module grid (the outer edge of the "
    "three finder squares; exclude any white card, border or background). Coordinates normalized 0-1000 relative to the "
    'image width (x) and height (y), origin top-left. Reply with JSON only: {"top_left":[x,y],"top_right":[x,y],'
    '"bottom_right":[x,y],"bottom_left":[x,y]}')
CHANNELS = {0: 1, 2: 3, 4: 2, 6: 4}


class Img:
    """8-bit pixels, row-major, `ch` channels (1 gray · 2 gray+alpha · 3 RGB · 4 RGBA)."""

    def __init__(self, w: int, h: int, ch: int, data: bytes):
        self.w, self.h, self.ch, self.data = w, h, ch, data


# ------------------------------------------------------------------ PNG (stdlib)
def png_decode(png: bytes) -> Img | None:
    """8 / 16-bit gray, gray+alpha, RGB, RGBA, non-interlaced → Img; anything else (palette, interlaced, broken) → None."""
    try:
        if not png.startswith(PNG_SIG):
            return None
        pos, idat, ihdr = 8, [], None
        while pos + 8 <= len(png):
            n, kind = struct.unpack(">I4s", png[pos:pos + 8])
            body = png[pos + 8:pos + 8 + n]
            if len(body) != n:
                return None
            if kind == b"IHDR":
                ihdr = struct.unpack(">IIBBBBB", body)
            elif kind == b"IDAT":
                idat.append(body)
            elif kind == b"IEND":
                break
            pos += 12 + n
        if not ihdr:
            return None
        w, h, depth, ctype, _, _, interlace = ihdr
        if ctype not in CHANNELS or depth not in (8, 16) or interlace or not w or not h or w * h > MAX_PIXELS:
            return None
        ch = CHANNELS[ctype]
        bpp = ch * depth // 8
        stride = w * bpp
        raw = zlib.decompressobj().decompress(b"".join(idat), (stride + 1) * h + 1)
        if len(raw) < (stride + 1) * h:
            return None
        out = bytearray(stride * h)
        prev = bytearray(stride)
        for y in range(h):
            f = raw[y * (stride + 1)]
            cur = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
            if f == 1:
                for i in range(bpp, stride):
                    cur[i] = (cur[i] + cur[i - bpp]) & 255
            elif f == 2:
                cur = bytearray(map(_add, cur, prev))
            elif f == 3:
                for i in range(stride):
                    a = cur[i - bpp] if i >= bpp else 0
                    cur[i] = (cur[i] + ((a + prev[i]) >> 1)) & 255
            elif f == 4:
                for i in range(stride):
                    if i >= bpp:
                        a, c = cur[i - bpp], prev[i - bpp]
                    else:
                        a = c = 0
                    b = prev[i]
                    p = a + b - c
                    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                    cur[i] = (cur[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
            elif f:
                return None
            out[y * stride:(y + 1) * stride] = cur
            prev = cur
        data = bytes(out) if depth == 8 else bytes(out[0::2])        # 16-bit: the high byte of each sample
        return Img(w, h, ch, data)
    except (zlib.error, struct.error, ValueError, MemoryError):
        return None


def _add(a: int, b: int) -> int:
    return (a + b) & 255


def png_encode(img: Img) -> bytes:
    ctype = {1: 0, 2: 4, 3: 2, 4: 6}[img.ch]
    stride = img.w * img.ch
    rows = b"".join(b"\0" + img.data[y * stride:(y + 1) * stride] for y in range(img.h))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    return (PNG_SIG + chunk(b"IHDR", struct.pack(">IIBBBBB", img.w, img.h, 8, ctype, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


# ------------------------------------------------------------------ detection (zxing-cpp, optional at import time)
def _zx():
    try:
        import zxingcpp
        return zxingcpp
    except Exception:  # noqa: BLE001 — not installed / no wheel for this platform: no local detection
        return None


def available() -> bool:
    return _zx() is not None


def find(img: Img) -> list[tuple[str, list]]:
    """[(sha256 of the content, [[x, y] × 4 corners: TL TR BR BL])] for every QR code zxing reads; [] without zxing."""
    zx = _zx()
    if zx is None:
        return []
    fmt = {1: zx.ImageFormat.Lum, 2: zx.ImageFormat.LumA, 3: zx.ImageFormat.RGB, 4: zx.ImageFormat.RGBA}[img.ch]
    try:
        view = zx.ImageView(img.data, img.w, img.h, fmt)
        found = zx.read_barcodes(view, formats=zx.BarcodeFormat.QRCode)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for b in found:
        raw = bytes(b.bytes) if getattr(b, "bytes", None) is not None else (b.text or "").encode()
        p = b.position
        corners = [[p.top_left.x, p.top_left.y], [p.top_right.x, p.top_right.y], [p.bottom_right.x, p.bottom_right.y],
                   [p.bottom_left.x, p.bottom_left.y]]
        out.append((hashlib.sha256(raw).hexdigest(), corners))
    return out


def cut(img: Img, corners: list, pad: float = PAD) -> Img:
    """The corners' bounding box, then a white quiet zone drawn fresh around it."""
    xs = [float(c[0]) for c in corners]
    ys = [float(c[1]) for c in corners]
    x0, y0 = max(0, int(min(xs))), max(0, int(min(ys)))
    x1, y1 = min(img.w, int(round(max(xs))) + 1), min(img.h, int(round(max(ys))) + 1)
    side = max(x1 - x0, y1 - y0)
    if x1 - x0 < MIN_SIDE or y1 - y0 < MIN_SIDE:
        raise ValueError("too_small")
    p = max(4, int(round(side * pad)))
    ch = img.ch
    white = bytes([255] * ch)
    w = (x1 - x0) + 2 * p
    stride = img.w * ch
    edge = white * w
    rows = [edge] * p
    side_pad = white * p
    for y in range(y0, y1):
        rows.append(side_pad + img.data[y * stride + x0 * ch:y * stride + x1 * ch] + side_pad)
    rows += [edge] * p
    return Img(w, (y1 - y0) + 2 * p, ch, b"".join(rows))


# ------------------------------------------------------------------ the vision fallback (the owner's own key, opt-in)
def vision_configured(env=None) -> bool:
    env = os.environ if env is None else env
    return all(env.get(k) for k in VISION_ENV)


def vision_corners(png: bytes, w: int, h: int, env=None, timeout: float = 30) -> list | None:
    """An OpenAI-compatible chat completion with the picture; → corners in pixels or None. The key is sent only to the URL
    the owner configured and never printed."""
    env = os.environ if env is None else env
    url = env["AGENTJ_VISION_URL"].rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    body = {"model": env["AGENTJ_VISION_MODEL"], "temperature": 0, "messages": [{"role": "user", "content": [
        {"type": "text", "text": VISION_PROMPT},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}]}]}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={
        "Authorization": "Bearer " + env["AGENTJ_VISION_API_KEY"], "Content-Type": "application/json",
        "User-Agent": "agentj-qr"})
    with urllib.request.urlopen(req, timeout=timeout) as r:      # noqa: S310 — the owner's configured endpoint
        out = json.loads(r.read(1 << 20))
    txt = (((out.get("choices") or [{}])[0].get("message") or {}).get("content")) or ""
    m = re.search(r"\{.*\}", txt, re.S)
    j = json.loads(m.group(0)) if m else {}
    keys = ("top_left", "top_right", "bottom_right", "bottom_left")
    if not all(isinstance(j.get(k), list) and len(j[k]) == 2 for k in keys):
        return None
    return [[float(j[k][0]) / 1000 * w, float(j[k][1]) / 1000 * h] for k in keys]


# ------------------------------------------------------------------ the whole step
def tidy(png: bytes, vision: bool = False, env=None, _vision=None) -> tuple[bytes | None, dict]:
    """→ (the cut PNG | None = send the original, info). info: method local|vision|None, codes, reason, ms. Never raises."""
    t0 = time.monotonic()
    info: dict = {"method": None, "codes": 0, "local": available()}
    try:
        img = png_decode(png)
        if img is None:
            info["reason"] = "not_png"
            return None, info
        found = find(img)
        info["codes"] = len(found)
        if found:
            want = {h for h, _ in found}

            def area(c):
                xs, ys = [p[0] for p in c[1]], [p[1] for p in c[1]]
                return (max(xs) - min(xs)) * (max(ys) - min(ys))
            _, corners = max(found, key=area)
            out = cut(img, corners)
            got = {h for h, _ in find(out)}
            if got and got <= want:
                info["method"] = "local"
                return png_encode(out), info
            info["reason"] = "verify_failed"
            return None, info
        if not vision or not vision_configured(env):
            info["reason"] = "no_code" if info["local"] else "no_local_detector"
            return None, info
        try:
            corners = (_vision or vision_corners)(png, img.w, img.h, env)
        except Exception as e:  # noqa: BLE001 — network / quota / parse: as if nothing was found
            info["reason"] = "vision_error:" + type(e).__name__
            return None, info
        if not corners:
            info["reason"] = "vision_none"
            return None, info
        out = cut(img, corners)
        if find(out):
            info["method"] = "vision"
            return png_encode(out), info
        info["reason"] = "verify_failed"
        return None, info
    except Exception as e:  # noqa: BLE001 — the promise: never fail the push
        info["reason"] = "error:" + type(e).__name__
        return None, info
    finally:
        info["ms"] = round((time.monotonic() - t0) * 1000)
