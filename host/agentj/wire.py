"""Wire helpers (PROTOCOL.md §1–§4, §10.0 / §10.1). Mirror of protocol/wire.js."""
from __future__ import annotations

import base64
import json
import re

from .noise import hmac256, sha256

PAIR_INIT, RESUME_INIT, HS_RESP, DATA = 1, 2, 3, 4
PAD = 256
MAX_JSON = 16 * 1024
MAX_TEXT = 4000  # UTF-16 code units (what a browser counts); see serve.text_units
# §10.0 (PROMPT-33): once BOTH ends announced capability "p33" the limits are these (a peer without it keeps the two above)
CAP_P33 = "p33"
MAX_JSON_P33 = 60 * 1024          # padded plaintext ≤ 61 696, ciphertext ≤ 61 712, relay payload ≤ 61 713 < 65 536
MAX_TEXT_P33 = 20_000             # UTF-16 code units per message (relay's limit)
FRAG_MAX_N = 128                  # §10.1: at most this many slices per message …
FRAG_MAX_TOTAL = 2 * 1024 * 1024  # … and at most this many UTF-8 bytes reassembled
PAIR_PROLOGUE = "agentjarvis/v1/pair\n"
RESUME_PROLOGUE = "agentjarvis/v1/resume\n"
_RID = re.compile(r"[A-Za-z0-9_-]{1,32}")      # request ids `r`
_ID22 = re.compile(r"[A-Za-z0-9_-]{22}")       # sid / bid: 128 random bits, chosen by the device

# relay ↔ host ops
OP_DATA, OP_CLOSE, OP_OPEN, OP_GONE = 0x01, 0x02, 0x11, 0x12
OP_BULK = 0x03   # §10.13: host → relay, raise one ready device socket's frame budget (a relay without it ignores it)


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s: str) -> bytes:
    if not isinstance(s, str) or not re.fullmatch(r"[A-Za-z0-9_-]*", s):
        raise ValueError("bad base64url")
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def pair_prologue(channel: str, pairing_id: bytes) -> bytes:
    return (PAIR_PROLOGUE + channel + "\n").encode() + pairing_id


def resume_prologue(channel: str) -> bytes:
    return (RESUME_PROLOGUE + channel).encode()


def device_id(pub: bytes) -> str:
    return b64u(sha256(b"agentjarvis/device/v1" + pub)[:12])


def channel_id(ed_pub: bytes) -> str:
    return b64u(sha256(b"agentjarvis/channel/v1" + ed_pub)[:16])


def safety_code(h: bytes) -> str:
    m = hmac256(h, b"agentjarvis-sas-v1")
    return f"{int.from_bytes(m[:4], 'big') % 1_000_000:06d}"


def dumps(obj) -> bytes:
    """The one JSON encoding of an app message (UTF-8, compact)."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()


def pad_json(obj: dict, max_json: int = MAX_JSON) -> bytes:
    j = dumps(obj)
    if len(j) > max_json:
        raise ValueError("message too large")
    total = -(-(len(j) + 2) // PAD) * PAD
    return (len(j).to_bytes(2, "big") + j).ljust(total, b"\x00")


def unpad_json(pt: bytes, max_json: int = MAX_JSON) -> dict:
    """max_json: MAX_JSON, or MAX_JSON_P33 once both ends announced p33 (§10.0) — the receiver enforces it too."""
    if len(pt) < 2 or len(pt) % PAD:
        raise ValueError("bad padding")
    n = int.from_bytes(pt[:2], "big")
    if n > max_json or n > len(pt) - 2 or any(pt[2 + n:]):
        raise ValueError("bad padding")
    obj = json.loads(pt[2:2 + n].decode("utf-8"))
    if not isinstance(obj, dict) or not isinstance(obj.get("t"), str):
        raise ValueError("bad message")
    return obj


def pairing_link(web_base: str, relay: str, channel: str, host_pub: bytes, pairing_id: bytes, psk: bytes,
                 expires: int) -> str:
    p = {"v": 1, "r": relay, "c": channel, "k": b64u(host_pub), "i": b64u(pairing_id), "p": b64u(psk), "x": expires}
    return web_base.rstrip("/") + "/#p=" + b64u(json.dumps(p, separators=(",", ":")).encode())


# ---------------------------------------------------------------- §10.0 text rules (PROMPT-33)
def units(s: str) -> int:
    """UTF-16 code units (what a browser's String.length counts). Lone surrogates count one each."""
    return sum(2 if ord(c) > 0xFFFF else 1 for c in s)


def text_problem(s, limit: int = MAX_TEXT_P33) -> str | None:
    """None when `s` is a valid p33 text: a str of ≤ limit UTF-16 code units, well-formed (no lone surrogate), no C0 control
    character except tab and newline (a sender turns CR LF / CR into LF first; a receiver that finds one says "shape").
    Else "shape" or "too_long"."""
    if not isinstance(s, str):
        return "shape"
    for ch in s:
        o = ord(ch)
        if (o < 0x20 and ch not in "\t\n") or 0xD800 <= o <= 0xDFFF:
            return "shape"
    if units(s) > limit:
        return "too_long"
    return None


def normalize_newlines(s: str) -> str:
    return s.replace("\r\n", "\n").replace("\r", "\n")


def well_formed(s: str) -> str:
    """Text the host sends (harness output) made safe for every receiver: lone surrogates → U+FFFD (they cannot even be
    encoded as UTF-8), CR LF / CR → LF, other C0 controls except tab / newline dropped."""
    out = []
    for ch in normalize_newlines(s):
        o = ord(ch)
        if 0xD800 <= o <= 0xDFFF:
            out.append("�")
        elif o < 0x20 and ch not in "\t\n":
            continue
        else:
            out.append(ch)
    return "".join(out)


def is_rid(v) -> bool:
    return isinstance(v, str) and _RID.fullmatch(v) is not None


def is_id22(v) -> bool:
    return isinstance(v, str) and _ID22.fullmatch(v) is not None


# ---------------------------------------------------------------- §10.1 frag: host → device messages over one frame
_FRAG_OVERHEAD = 120     # {"t":"frag","f":"<16 hex>","i":127,"n":128,"d":""} is 56 bytes; the rest is margin


def frag_split(obj: dict, fid: str, max_json: int = MAX_JSON_P33) -> list[dict]:
    """[obj] when it fits one frame, else N `frag` messages whose `d` slices (cut on code-point boundaries) concatenate to the
    inner message's JSON text, each frag JSON ≤ max_json bytes. Raises ValueError past FRAG_MAX_N / FRAG_MAX_TOTAL."""
    raw = dumps(obj)
    if len(raw) <= max_json:
        return [obj]
    if len(raw) > FRAG_MAX_TOTAL:
        raise ValueError("message too large even for frag")
    text = raw.decode()
    budget = max_json - _FRAG_OVERHEAD
    slices, cur, size = [], [], 0
    for ch in text:
        n = len(json.dumps(ch, ensure_ascii=False).encode()) - 2     # its cost inside the JSON string `d`
        if size + n > budget:
            slices.append("".join(cur))
            cur, size = [], 0
        cur.append(ch)
        size += n
    if cur:
        slices.append("".join(cur))
    if len(slices) > FRAG_MAX_N:
        raise ValueError("message too large even for frag")
    return [{"t": "frag", "f": fid, "i": i, "n": len(slices), "d": d} for i, d in enumerate(slices)]


class Defrag:
    """The receiver side of §10.1 (the phone's; here for tests and tools): feed every app message, get back the message to
    handle (a reassembled one, or the message itself), or None while a frag is open / after a broken one."""

    def __init__(self):
        self.f, self.n, self.parts, self.size = None, 0, [], 0

    def feed(self, m: dict) -> dict | None:
        if m.get("t") != "frag":
            self.f = None                          # anything else while open → the partial is dropped
            return m
        f, i, n, d = m.get("f"), m.get("i"), m.get("n"), m.get("d")
        if not (isinstance(f, str) and type(i) is int and type(n) is int and isinstance(d, str) and 0 <= i < n <= FRAG_MAX_N):
            self.f = None
            return None
        if i == 0:
            self.f, self.n, self.parts, self.size = f, n, [], 0
        if self.f != f or self.n != n or len(self.parts) != i:
            self.f = None
            return None
        self.parts.append(d)
        self.size += len(d.encode())
        if self.size > FRAG_MAX_TOTAL:
            self.f = None
            return None
        if len(self.parts) < n:
            return None
        self.f = None
        try:
            inner = json.loads("".join(self.parts))
        except ValueError:
            return None
        if not isinstance(inner, dict) or not isinstance(inner.get("t"), str) or inner["t"] == "frag":
            return None
        return inner
