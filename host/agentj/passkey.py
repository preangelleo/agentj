"""F20 (0.15.2, PROTOCOL §12): a passkey (WebAuthn) proves "the same already-approved phone" — never more.

Pure functions only (no state, no I/O except one test switch read from the environment): the host's checks of a
registration (`pk_reg`) and of a restore assertion (`pk_restore`), the user handle layout and the restore challenge. The
session glue lives in serve.py, the allowlist edits in state.py. Written so F17's sudo / secret cards can later ask for a
passkey ceremony before 「同意」 with the same verifier (PROTOCOL §11 GAP).

What is deliberately NOT checked: the attestation (the page asks for 'none'; the registering session is already the approved
device and could register any key it likes), and the signature counter of a synced passkey (iCloud Keychain sends 0)."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa

from . import wire
from .envcompat import getenv

ALG_ES256, ALG_EDDSA, ALG_RS256 = -7, -8, -257
ALGS = (ALG_ES256, ALG_EDDSA, ALG_RS256)
HANDLE_V = 1
HANDLE_LEN = 50                 # 0x01 ‖ relay index u8 ‖ channel (16) ‖ host X25519 public key (32)
RESTORE_LABEL = b"agentjarvis/passkey/restore/v1\n"
OFFER_TTL = 300                 # s: a pk_offer nonce is good for one pk_reg within this
TS_WINDOW_MS = 5 * 60 * 1000    # |now − ts| of a restore
NONCE_KEEP = 600                # s a restore nonce is remembered (replay)
RATE_N, RATE_WINDOW = 10, 60    # pk_restore attempts per host per minute
MAX_CRED_ID = 1023              # bytes (WebAuthn's own limit)
MAX_CD = 4096                   # bytes of clientDataJSON we are willing to parse
MAX_AD = 1024                   # bytes of authenticatorData (37 + extensions; we never ask for any)
MAX_SPKI = 1024                 # an RSA-4096 SPKI is ~550 bytes
FLAG_UP, FLAG_UV = 0x01, 0x04
# The web origins this host serves (state.DEFAULT_WEB + the legacy one). A page on any other origin cannot have been
# served by us, so its WebAuthn ceremony is not ours. Tests: AGENTJ_TEST_PASSKEY_ORIGIN = "loopback" (any
# http://127.0.0.1:<port> / http://localhost:<port>) or one exact origin — the same kind of switch as
# AGENTJ_TEST_PUSH_ORIGIN (webpush.py). Unset in production; there is no other way to widen this list.
WEB_ORIGINS = ("https://m.agentj.app", "https://alpha-web.agentjarvis.net")
TEST_ENV = "AGENTJ_TEST_PASSKEY_ORIGIN"


class PasskeyError(ValueError):
    """why = what the phone is told (PROTOCOL §12: unknown | bad | expired | revoked); detail = host.log only."""
    def __init__(self, why: str, detail: str = ""):
        super().__init__(f"{why}: {detail}" if detail else why)
        self.why, self.detail = why, detail or why


# ---------------------------------------------------------------- origins / rp id
def origin_allowed(origin) -> bool:
    if not isinstance(origin, str) or len(origin) > 256:
        return False
    if origin in WEB_ORIGINS:
        return True
    test = getenv(TEST_ENV)
    if not test:
        return False
    if test != "loopback":
        return origin == test
    try:
        u = urlsplit(origin)
        u.port   # noqa: B018  (raises on a bad port)
    except ValueError:
        return False
    return (u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost") and not u.username and not u.password
            and origin == f"http://{u.netloc}" and not u.path)


def rp_of(origin: str) -> str:
    """The rp id a page on `origin` uses (= location.hostname, PROTOCOL §12)."""
    return urlsplit(origin).hostname or ""


# ---------------------------------------------------------------- user handle (≤ 64 bytes; ours is 50)
def user_handle(relay_index: int, channel: str, host_pub: bytes) -> bytes:
    ch = wire.unb64u(channel)
    if len(ch) != 16 or len(host_pub) != 32 or not 0 <= relay_index <= 255:
        raise ValueError("bad handle parts")
    return bytes([HANDLE_V, relay_index]) + ch + host_pub


def handle_matches(uh: bytes, channel: str, host_pub: bytes) -> bool:
    """Is `uh` a handle THIS host would build (version 1, its channel + key)? The relay index is the phone's business."""
    if len(uh) != HANDLE_LEN or uh[0] != HANDLE_V:
        return False
    return hmac.compare_digest(uh[2:], wire.unb64u(channel) + host_pub)


# ---------------------------------------------------------------- restore challenge (device-made, bound to the new key)
def restore_challenge(dev_x25519: bytes, dev_ed25519: bytes, ts_ms: int, nonce: bytes) -> bytes:
    """SHA-256(label ‖ new device X25519 pub ‖ its Ed25519 approval pub ‖ ts u64 BE ‖ nonce(16)). The host recomputes it
    from the IK-authenticated static key of THIS session and the msg1 `sk`, so an assertion is worthless to anyone who
    does not hold the new (non-extractable) private key."""
    if len(dev_x25519) != 32 or len(dev_ed25519) != 32 or len(nonce) != 16 or not 0 <= ts_ms < 2**64:
        raise ValueError("bad challenge parts")
    return hashlib.sha256(RESTORE_LABEL + dev_x25519 + dev_ed25519 + ts_ms.to_bytes(8, "big") + nonce).digest()


# ---------------------------------------------------------------- pieces
def b64(v, limit: int, what: str) -> bytes:
    if not isinstance(v, str) or not v or len(v) > (limit * 4 + 2) // 3 + 2:
        raise PasskeyError("bad", what)
    try:
        b = wire.unb64u(v)
    except ValueError:
        raise PasskeyError("bad", what) from None
    if not b or len(b) > limit:
        raise PasskeyError("bad", what)
    return b


def client_data(cd: bytes, typ: str, challenge: bytes) -> str:
    """clientDataJSON → its origin, after checking type, challenge and origin. Raises PasskeyError("bad", …)."""
    try:
        c = json.loads(cd.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise PasskeyError("bad", "cd_json") from None
    if not isinstance(c, dict) or c.get("type") != typ:
        raise PasskeyError("bad", "cd_type")
    ch = c.get("challenge")
    if not isinstance(ch, str) or not hmac.compare_digest(ch.rstrip("=").encode(), wire.b64u(challenge).encode()):
        raise PasskeyError("bad", "cd_challenge")
    if c.get("crossOrigin") is True:
        raise PasskeyError("bad", "cd_cross_origin")
    origin = c.get("origin")
    if not origin_allowed(origin):
        raise PasskeyError("bad", "cd_origin")
    return origin


def auth_data(ad: bytes) -> tuple[bytes, int, int]:
    """authenticatorData → (rpIdHash, flags, signCount)."""
    if len(ad) < 37:
        raise PasskeyError("bad", "ad_short")
    return ad[:32], ad[32], int.from_bytes(ad[33:37], "big")


def load_spki(spki: bytes, alg: int):
    """SubjectPublicKeyInfo DER → a public key of exactly the kind `alg` names (P-256 / Ed25519 / RSA ≥ 2048)."""
    try:
        k = serialization.load_der_public_key(spki)
    except (ValueError, TypeError):
        raise PasskeyError("bad", "spki") from None
    ok = (alg == ALG_ES256 and isinstance(k, ec.EllipticCurvePublicKey) and isinstance(k.curve, ec.SECP256R1)) \
        or (alg == ALG_EDDSA and isinstance(k, ed25519.Ed25519PublicKey)) \
        or (alg == ALG_RS256 and isinstance(k, rsa.RSAPublicKey) and k.key_size >= 2048)
    if not ok:
        raise PasskeyError("bad", "spki_alg")
    return k


def verify_sig(key, alg: int, data: bytes, sig: bytes) -> None:
    try:
        if alg == ALG_ES256:
            key.verify(sig, data, ec.ECDSA(hashes.SHA256()))       # DER-encoded, as WebAuthn returns it
        elif alg == ALG_EDDSA:
            key.verify(sig, data)
        elif alg == ALG_RS256:
            key.verify(sig, data, padding.PKCS1v15(), hashes.SHA256())
        else:
            raise PasskeyError("bad", "alg")
    except (InvalidSignature, ValueError, TypeError):
        raise PasskeyError("bad", "signature") from None


# ---------------------------------------------------------------- the two ceremonies
def verify_registration(obj: dict, nonce: bytes) -> dict:
    """A `pk_reg` answering the offer `nonce` → the record stored on the device: {"id","alg","spki","rp","at"}."""
    cred = b64(obj.get("id"), MAX_CRED_ID, "id")
    alg = obj.get("alg")
    if type(alg) is not int or alg not in ALGS:
        raise PasskeyError("bad", "alg")
    spki = b64(obj.get("spki"), MAX_SPKI, "spki")
    load_spki(spki, alg)
    origin = client_data(b64(obj.get("cd"), MAX_CD, "cd"), "webauthn.create", nonce)
    return {"id": wire.b64u(cred), "alg": alg, "spki": wire.b64u(spki), "rp": rp_of(origin), "at": int(time.time())}


def restore_fields(obj: dict) -> dict:
    """Shape of a `pk_restore` (bytes decoded, ts an int). Raises PasskeyError("bad", …)."""
    ts = obj.get("ts")
    if type(ts) is not int or not 0 <= ts < 2**63:
        raise PasskeyError("bad", "ts")
    nonce = b64(obj.get("nonce"), 16, "nonce")
    if len(nonce) != 16:
        raise PasskeyError("bad", "nonce")
    return {"id": wire.b64u(b64(obj.get("id"), MAX_CRED_ID, "id")), "cd": b64(obj.get("cd"), MAX_CD, "cd"),
            "ad": b64(obj.get("ad"), MAX_AD, "ad"), "sig": b64(obj.get("sig"), 1024, "sig"),
            "uh": b64(obj.get("uh"), 64, "uh"), "ts": ts, "nonce": nonce}


def verify_assertion(pk: dict, f: dict, challenge: bytes) -> int:
    """A restore assertion `f` (restore_fields) against the stored `pk` record → the signCount to store (0 = synced
    passkey, not tracked). The caller already matched f["id"] to pk["id"] and checked the user handle."""
    client_data(f["cd"], "webauthn.get", challenge)
    rp_hash, flags, count = auth_data(f["ad"])
    if not isinstance(pk.get("rp"), str) or not hmac.compare_digest(rp_hash, hashlib.sha256(pk["rp"].encode()).digest()):
        raise PasskeyError("bad", "rp_hash")
    if not flags & FLAG_UP or not flags & FLAG_UV:
        raise PasskeyError("bad", "flags")
    alg = pk.get("alg")
    try:
        spki = wire.unb64u(pk.get("spki") or "")
    except ValueError:
        raise PasskeyError("bad", "stored_spki") from None
    verify_sig(load_spki(spki, alg), alg, f["ad"] + hashlib.sha256(f["cd"]).digest(), f["sig"])
    stored = pk.get("sc") if type(pk.get("sc")) is int else 0
    if count and stored and count <= stored:
        raise PasskeyError("bad", "sign_count")   # a cloned authenticator (a synced one always says 0)
    return count


# ---------------------------------------------------------------- in-memory guards (reset by a restart — fine: ts ≤ 5 min)
class Guard:
    """Rate limit (≤ RATE_N attempts per RATE_WINDOW s per host) and restore-nonce replay memory (NONCE_KEEP s)."""
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.hits: list[float] = []
        self.seen: dict[bytes, float] = {}

    def attempt(self) -> bool:
        now = self.clock()
        self.hits = [t for t in self.hits if now - t < RATE_WINDOW]
        if len(self.hits) >= RATE_N:
            return False
        self.hits.append(now)
        return True

    def fresh(self, nonce: bytes) -> bool:
        """True once per nonce within NONCE_KEEP s (and remembers it)."""
        now = self.clock()
        self.seen = {k: t for k, t in self.seen.items() if now - t < NONCE_KEEP}
        if nonce in self.seen:
            return False
        self.seen[nonce] = now
        return True


def ts_ok(ts_ms: int, now_ms: int | None = None) -> bool:
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    return abs(now_ms - ts_ms) <= TS_WINDOW_MS
