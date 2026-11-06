"""#547 read-only, domain-separated proof of current pairing for the seat's web preview.

The web publisher selects the candidate. This proof grants no pairing, approval or host capability.
A navigation lease is at most sixty seconds; revocation prevents the next proof, not past downloads.
"""
from __future__ import annotations

import json
import re
import time
from . import wire
from .seaturl import seat_id

LABEL = b"agentj/web-preview/host/v1\n"
KEYS = ["v", "origin", "seat", "manifestSha256", "devicePublicKey", "nonce", "expires"]


def attest(st, channel: str, device_pub: bytes, message: object, *, now: int | None = None) -> str | None:
    if not isinstance(message, str) or len(message) > 1024:
        return None
    try:
        m = json.loads(message)
        clock = int(time.time()) if now is None else now
        if not isinstance(m, dict) or list(m) != KEYS or json.dumps(m, separators=(",", ":"), ensure_ascii=False) != message:
            return None
        if type(m["v"]) is not int or m["v"] != 1 or m["origin"] != "https://m.agentj.app" or m["seat"] != seat_id(channel):
            return None
        if not isinstance(m["manifestSha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", m["manifestSha256"]):
            return None
        if not isinstance(m["nonce"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", m["nonce"]):
            return None
        if type(m["expires"]) is not int or not clock < m["expires"] <= clock + 60:
            return None
        # Fresh authoritative allowlist read (State.devices filters revoked credentials). Bind the exact approval key.
        rec = st.devices().get(wire.device_id(device_pub))
        if not rec or rec.get("sk") != m["devicePublicKey"] or not st.is_allowed(device_pub):
            return None
        return wire.b64u(st.signing_key().sign(LABEL + message.encode("utf-8")))
    except (ValueError, TypeError, KeyError, OSError):
        return None
