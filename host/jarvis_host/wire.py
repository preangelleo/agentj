"""Wire helpers (PROTOCOL.md §1–§4). Mirror of protocol/wire.js."""
from __future__ import annotations

import base64
import json
import re

from .noise import hmac256, sha256

PAIR_INIT, RESUME_INIT, HS_RESP, DATA = 1, 2, 3, 4
PAD = 256
MAX_JSON = 16 * 1024
MAX_TEXT = 4000  # UTF-16 code units (what a browser counts); see serve.text_units
PAIR_PROLOGUE = "agentjarvis/v1/pair\n"
RESUME_PROLOGUE = "agentjarvis/v1/resume\n"

# relay ↔ host ops
OP_DATA, OP_CLOSE, OP_OPEN, OP_GONE = 0x01, 0x02, 0x11, 0x12


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


def pad_json(obj: dict) -> bytes:
    j = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
    if len(j) > MAX_JSON:
        raise ValueError("message too large")
    total = -(-(len(j) + 2) // PAD) * PAD
    return (len(j).to_bytes(2, "big") + j).ljust(total, b"\x00")


def unpad_json(pt: bytes) -> dict:
    if len(pt) < 2 or len(pt) % PAD:
        raise ValueError("bad padding")
    n = int.from_bytes(pt[:2], "big")
    if n > MAX_JSON or n > len(pt) - 2 or any(pt[2 + n:]):
        raise ValueError("bad padding")
    obj = json.loads(pt[2:2 + n].decode("utf-8"))
    if not isinstance(obj, dict) or not isinstance(obj.get("t"), str):
        raise ValueError("bad message")
    return obj


def pairing_link(web_base: str, relay: str, channel: str, host_pub: bytes, pairing_id: bytes, psk: bytes,
                 expires: int) -> str:
    p = {"v": 1, "r": relay, "c": channel, "k": b64u(host_pub), "i": b64u(pairing_id), "p": b64u(psk), "x": expires}
    return web_base.rstrip("/") + "/#p=" + b64u(json.dumps(p, separators=(",", ":")).encode())
