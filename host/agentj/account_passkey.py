"""P191 (audit B1) — preparation only, not wired: the host verifying an ACCOUNT passkey assertion itself.

Today an account-page request (remote_pair.py) is trusted because the Dashboard signed it; the host never sees the passkey.
The target (documentation/product/12_ZERO_ACCESS.md §8b ①, P191): the browser makes a fresh WebAuthn `get` whose challenge
binds the exact signed request (`challenge`), the Dashboard forwards the assertion inside the request, and the host checks it
against account passkey public keys it pinned while the owner was at the computer (`verify`). A compromised control plane can
then still sign, but cannot produce the owner's passkey signature.

Pins mirror what the Dashboard stores per credential (webauthn.ts `Registered`): {"id": b64u credential id, "alg": COSE alg,
"jwk": public JWK}. Nothing here reads the network or state yet; remote_pair.py is unchanged.
"""
from __future__ import annotations

import hashlib
import hmac
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

from . import wire
from .passkey import ALG_EDDSA, ALG_ES256, ALG_RS256, FLAG_UP, FLAG_UV, MAX_AD, MAX_CD, MAX_CRED_ID, PasskeyError, auth_data, b64, load_spki, verify_sig

ACCOUNT_ORIGIN = "https://agentj.app"
ACCOUNT_RP = "agentj.app"
DOMAIN = b"agentj-account-passkey-v1"
MAX_PINS = 20


def challenge(context: str, body_b64u: str) -> bytes:
    """The WebAuthn challenge for one signed account-page request: binds its context and exact body (not a session)."""
    return hashlib.sha256(DOMAIN + b"\n" + context.encode("ascii") + b"\n" + body_b64u.encode("ascii")).digest()


def jwk_spki(jwk: dict, alg: int) -> bytes:
    """A Dashboard-stored public JWK → SubjectPublicKeyInfo DER (then load_spki enforces the curve / size for `alg`)."""
    def n(v):
        return int.from_bytes(wire.unb64u(v), "big")
    try:
        if alg == ALG_ES256 and jwk.get("kty") == "EC" and jwk.get("crv") == "P-256":
            key = ec.EllipticCurvePublicNumbers(n(jwk["x"]), n(jwk["y"]), ec.SECP256R1()).public_key()
        elif alg == ALG_EDDSA and jwk.get("kty") == "OKP" and jwk.get("crv") == "Ed25519":
            key = ed25519.Ed25519PublicKey.from_public_bytes(wire.unb64u(jwk["x"]))
        elif alg == ALG_RS256 and jwk.get("kty") == "RSA":
            key = rsa.RSAPublicNumbers(n(jwk["e"]), n(jwk["n"])).public_key()
        else:
            raise PasskeyError("bad", "jwk_alg")
    except (KeyError, TypeError, ValueError, AttributeError):
        raise PasskeyError("bad", "jwk") from None
    spki = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    load_spki(spki, alg)
    return spki


def _client_data(cd: bytes, want: bytes, origin: str) -> None:
    try:
        c = json.loads(cd.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise PasskeyError("bad", "cd_json") from None
    if not isinstance(c, dict) or c.get("type") != "webauthn.get":
        raise PasskeyError("bad", "cd_type")
    ch = c.get("challenge")
    if not isinstance(ch, str) or not hmac.compare_digest(ch.rstrip("=").encode(), wire.b64u(want).encode()):
        raise PasskeyError("bad", "cd_challenge")
    if c.get("crossOrigin") is True:
        raise PasskeyError("bad", "cd_cross_origin")
    if c.get("origin") != origin:   # exactly the account page; no test widening here
        raise PasskeyError("bad", "cd_origin")


def verify(pins: list, assertion: dict, want: bytes, *, origin: str = ACCOUNT_ORIGIN, rp: str = ACCOUNT_RP) -> str:
    """An account passkey assertion {"id","cd","ad","sig"} (b64u strings) over challenge `want` → the pinned credential id.
    Raises PasskeyError("bad", reason). User presence AND verification are required (owner approval, not a tap)."""
    if not isinstance(pins, list) or not 0 < len(pins) <= MAX_PINS or not isinstance(assertion, dict):
        raise PasskeyError("bad", "pins")
    cred = wire.b64u(b64(assertion.get("id"), MAX_CRED_ID, "id"))
    cd, ad, sig = b64(assertion.get("cd"), MAX_CD, "cd"), b64(assertion.get("ad"), MAX_AD, "ad"), b64(assertion.get("sig"), 1024, "sig")
    pin = next((p for p in pins if isinstance(p, dict) and isinstance(p.get("id"), str) and hmac.compare_digest(p["id"], cred)), None)
    if pin is None:
        raise PasskeyError("bad", "unknown_credential")
    _client_data(cd, want, origin)
    rp_hash, flags, _count = auth_data(ad)
    if not hmac.compare_digest(rp_hash, hashlib.sha256(rp.encode()).digest()):
        raise PasskeyError("bad", "rp_hash")
    if not flags & FLAG_UP or not flags & FLAG_UV:
        raise PasskeyError("bad", "flags")
    alg = pin.get("alg")
    if type(alg) is not int or not isinstance(pin.get("jwk"), dict):
        raise PasskeyError("bad", "pin")
    verify_sig(load_spki(jwk_spki(pin["jwk"], alg), alg), alg, ad + hashlib.sha256(cd).digest(), sig)
    return cred
