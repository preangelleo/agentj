"""Plaza publisher signatures (P426 #47 · #181-2): the publishing host signs its own package; nobody else can.

Why: a 「已认证」 label used to be earned by bytes the operator downloaded from the server, so a compromised Worker / D1 could pass
a forged package off as one of the operator's own. Now the host that publishes signs, with its own long-term Ed25519 identity key
(`State.signing_key()`, the key every host envelope is already signed with; the private key never leaves the host, 0600), this
canonical text (UTF-8, `\\n`-joined, no trailing newline):

    agentjarvis-plaza-host-sig/v1
    name=<name>
    type=<skill|workflow>
    version=<version>
    sha256=<sha256 hex of the bundle bytes>
    host=<channel id = b64url(SHA-256("agentjarvis/channel/v1" ‖ pubkey)[:16])>
    signed_at=<unix seconds>

and sends `host_sig` (b64url, 64 bytes), `host_pubkey` (b64url, 32 bytes) and `host_signed_at` with the publish request. The
server checks it against the envelope's own key, stores it and serves it unchanged — it never signs and never rewrites it.
Verifiers (the operator's auto-certify with a locally pinned key; every installing host, TOFU per package name) recompute the
text from the bytes THEY downloaded, so a swapped byte, name, version or key fails. The first line is a domain separator: the
same key's envelope signatures (`agentjarvis-host-…-v1\\n<b64url body>`) can never be read as one of these and vice versa.

Standard library only (the operator tools load this file by path); `cryptography` is used when present, else a small RFC 8032
verifier (verification only — signing needs the host's key object).
"""
from __future__ import annotations

import base64
import hashlib
import re

DOMAIN = "agentjarvis-plaza-host-sig/v1"
_B64U = re.compile(r"[A-Za-z0-9_-]+")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_NAME = re.compile(r"[a-z0-9][a-z0-9-]{1,39}")
_SEMVER = re.compile(r"(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})(-[0-9A-Za-z.-]{1,40})?")
MAX_SIGNED_AT = 2 ** 41      # unix seconds, far beyond any real clock; keeps the field a small safe integer everywhere


class TrustError(ValueError):
    """A malformed publisher-signature field (never a verification result: verify() answers True / False)."""


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def unb64u(s, n: int) -> bytes:
    """Exactly `n` bytes from unpadded b64url, else TrustError."""
    if not isinstance(s, str) or not _B64U.fullmatch(s) or len(s) != (4 * n + 2) // 3:
        raise TrustError("bad b64url")
    try:
        b = base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    except (ValueError, TypeError):
        raise TrustError("bad b64url") from None
    if len(b) != n or b64u(b) != s:
        raise TrustError("bad b64url")
    return b


def channel_of(pub: bytes) -> str:
    """= wire.channel_id: the host id the server knows this key by."""
    return b64u(hashlib.sha256(b"agentjarvis/channel/v1" + pub).digest()[:16])


def fingerprint(pub: bytes) -> str:
    """Full fingerprint: SHA-256 hex of the raw 32-byte public key (what an operator pins)."""
    return hashlib.sha256(pub).hexdigest()


def short_fp(fp: str) -> str:
    """What a person reads: the first 16 hex of the fingerprint in groups of four."""
    return "-".join(fp[i:i + 4] for i in range(0, 16, 4))


def signed_text(name: str, type_: str, version: str, sha256: str, host: str, signed_at: int) -> bytes:
    if not (isinstance(name, str) and _NAME.fullmatch(name)) or type_ not in ("skill", "workflow") \
            or not (isinstance(version, str) and _SEMVER.fullmatch(version)) or not (isinstance(sha256, str) and _HEX64.fullmatch(sha256)) \
            or not (isinstance(host, str) and _B64U.fullmatch(host)) or not _signed_at_ok(signed_at):
        raise TrustError("bad signed fields")
    return "\n".join((DOMAIN, f"name={name}", f"type={type_}", f"version={version}", f"sha256={sha256}", f"host={host}",
                      f"signed_at={signed_at}")).encode("utf-8")


def _signed_at_ok(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and 0 < v < MAX_SIGNED_AT


def sign(signing_key, name: str, type_: str, version: str, sha256: str, signed_at: int) -> dict:
    """The publish fields for this package, signed with the host's Ed25519 key object (cryptography Ed25519PrivateKey)."""
    from cryptography.hazmat.primitives import serialization
    pub = signing_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    sig = signing_key.sign(signed_text(name, type_, version, sha256, channel_of(pub), int(signed_at)))
    return {"host_sig": b64u(sig), "host_pubkey": b64u(pub), "host_signed_at": int(signed_at)}


def fields(d) -> tuple[bytes, bytes, int] | None:
    """(pubkey, sig, signed_at) from a server answer / publish request carrying host_sig + host_pubkey + host_signed_at; None
    when none of the three is there (an unsigned package); TrustError when some are there but malformed or incomplete."""
    if not isinstance(d, dict):
        return None
    have = [k for k in ("host_sig", "host_pubkey", "host_signed_at") if d.get(k) is not None]
    if not have:
        return None
    if len(have) != 3 or not _signed_at_ok(d["host_signed_at"]):
        raise TrustError("incomplete publisher signature")
    return unb64u(d["host_pubkey"], 32), unb64u(d["host_sig"], 64), d["host_signed_at"]


def verify(pub: bytes, sig: bytes, name: str, type_: str, version: str, sha256: str, signed_at: int) -> bool:
    """True only when `sig` is pub's Ed25519 signature over the canonical text of exactly these values (host = pub's channel)."""
    try:
        msg = signed_text(name, type_, version, sha256, channel_of(pub), signed_at)
    except TrustError:
        return False
    if not (isinstance(pub, bytes) and len(pub) == 32 and isinstance(sig, bytes) and len(sig) == 64):
        return False
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        return ed25519_verify(pub, msg, sig)
    try:
        Ed25519PublicKey.from_public_bytes(pub).verify(sig, msg)
        return True
    except (InvalidSignature, ValueError):
        return False


# ---- Ed25519 verification, RFC 8032 §5.1.7 (cofactorless) — the fallback when `cryptography` is absent (operator tools)
_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _recover_x(y: int, sign_bit: int) -> int:
    xx = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P:
        x = x * _SQRT_M1 % _P
    if (x * x - xx) % _P:
        raise ValueError("not on curve")
    if x == 0 and sign_bit:
        raise ValueError("bad sign")
    return _P - x if x & 1 != sign_bit else x


def _decode(b: bytes):
    y = int.from_bytes(b, "little")
    s, y = y >> 255, y & ((1 << 255) - 1)
    if y >= _P:
        raise ValueError("range")
    x = _recover_x(y, s)
    return (x, y, 1, x * y % _P)


def _add(p, q):
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = p[3] * 2 * _D * q[3] % _P
    d = p[2] * 2 * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(s: int, p):
    q = (0, 1, 1, 0)
    while s:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q


_BY = 4 * pow(5, _P - 2, _P) % _P
_B = (_recover_x(_BY, 0), _BY, 1, _recover_x(_BY, 0) * _BY % _P)


def ed25519_verify(pub: bytes, msg: bytes, sig: bytes) -> bool:
    try:
        if len(sig) != 64 or len(pub) != 32:
            return False
        a, r = _decode(pub), _decode(sig[:32])
        s = int.from_bytes(sig[32:], "little")
        if s >= _L:
            return False
        h = int.from_bytes(hashlib.sha512(sig[:32] + pub + msg).digest(), "little") % _L
        x, y = _mul(s, _B), _add(r, _mul(h, a))
        return (x[0] * y[2] - y[0] * x[2]) % _P == 0 and (x[1] * y[2] - y[1] * x[2]) % _P == 0
    except ValueError:
        return False
