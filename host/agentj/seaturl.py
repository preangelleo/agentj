"""P545 (FEEDBACK #309, PROTOCOL §40): the seat's own phone address — m.agentj.app/<seat id>.

One computer seat = one channel. Its public seat id is the first 40 bits of SHA-256("agentj/seat-url/v1\\n" ‖ channel),
written as 8 lower-case base32 letters (a-z, 2-7). One-way: the channel (128 random bits) cannot be recovered from it, so
the id is safe to print, show on the account page and put in a home-screen shortcut. The phone page, the account
dashboard and this module compute the same id (vectors: protocol/vectors/p545-seat-url.json)."""
from __future__ import annotations

import hashlib
import re

LABEL = b"agentj/seat-url/v1\n"
ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"
SEAT_RE = re.compile(r"^[a-z2-7]{8}$")
CHANNEL_RE = re.compile(r"^[A-Za-z0-9_-]{22}$")
WEB = "https://m.agentj.app"


def seat_id(channel: str) -> str:
    if not isinstance(channel, str) or not CHANNEL_RE.match(channel):
        raise ValueError("bad channel")
    n = int.from_bytes(hashlib.sha256(LABEL + channel.encode("ascii")).digest()[:5], "big")
    return "".join(ALPHABET[(n >> (35 - 5 * i)) & 31] for i in range(8))


def seat_url(channel: str, web: str = WEB) -> str:
    """https://m.agentj.app/<seat id> — only for our own web origin; any other web (tests, a legacy host) keeps its root."""
    base = (web or WEB).rstrip("/")
    return f"{base}/{seat_id(channel)}" if base == WEB else base
