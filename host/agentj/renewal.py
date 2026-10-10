"""P122 (0.17.4, PROTOCOL §12.1): the renewal ticket — "the same phone" after its browser deleted the page's storage.

Pure functions only (no state, no I/O). Every allowlisted device's record may hold ONE ticket {"i": id (16 B), "k": secret
(32 B), "at"} (b64url). The page never keeps it in script-readable storage: it hands it straight to the web origin, which
stores `1.<user handle>.<i>.<k>` as a first-party HttpOnly + Secure + SameSite=Strict cookie scoped to `/.aj/rt` (worker.ts).
Safari's 7-day eviction (ITP) deletes script-writable storage — the device key with it — but not such a cookie. A page that
lost its keys makes new ones, computes the restore challenge below over them, and asks the origin for a proof; the origin
answers HMAC-SHA256(k, PROOF_LABEL ‖ challenge) and the handle/id, never the secret. The host recomputes the proof from THIS
session's IK-authenticated static key and msg1 `sk`, so a proof is worthless with any other key, after 5 min, or twice.

What a ticket can do: exactly what §12's passkey restore does (the listed record takes the new key), nothing more. It lives
inside the device record, so every way a record leaves the allowlist (revoke, eviction, iid replace, Dashboard unbind) kills it;
each use rotates it (a copied cookie stops working once the phone used its own); a passkey restore replaces it."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from . import wire
from .passkey import PasskeyError, b64

RESTORE_LABEL = b"agentjarvis/rt/restore/v1\n"
PROOF_LABEL = b"agentjarvis/rt/proof/v1\n"
ID_LEN, KEY_LEN = 16, 32


def new_ticket(now: int | None = None) -> dict:
    """A fresh ticket for one device record."""
    return {"i": wire.b64u(secrets.token_bytes(ID_LEN)), "k": wire.b64u(secrets.token_bytes(KEY_LEN)),
            "at": int(time.time()) if now is None else now}


def restore_challenge(dev_x25519: bytes, dev_ed25519: bytes, ts_ms: int, nonce: bytes) -> bytes:
    """SHA-256(label ‖ new device X25519 pub ‖ its Ed25519 approval pub ‖ ts u64 BE (ms) ‖ nonce(16)) — §12's layout, own label."""
    if len(dev_x25519) != 32 or len(dev_ed25519) != 32 or len(nonce) != 16 or not 0 <= ts_ms < 2**64:
        raise ValueError("bad challenge parts")
    return hashlib.sha256(RESTORE_LABEL + dev_x25519 + dev_ed25519 + ts_ms.to_bytes(8, "big") + nonce).digest()


def proof(key: bytes, challenge: bytes) -> bytes:
    """What the web origin computes from the cookie (worker.ts rtProof): HMAC-SHA256(k, PROOF_LABEL ‖ challenge)."""
    return hmac.new(key, PROOF_LABEL + challenge, hashlib.sha256).digest()


def restore_fields(obj: dict) -> dict:
    """Shape of an `rt_restore` {"i","p","uh","ts","nonce"[,"iid"]}. Raises PasskeyError("bad", …)."""
    ts = obj.get("ts")
    if type(ts) is not int or not 0 <= ts < 2**63:
        raise PasskeyError("bad", "ts")
    nonce = b64(obj.get("nonce"), 16, "nonce")
    i = b64(obj.get("i"), ID_LEN, "rt_id")
    p = b64(obj.get("p"), 32, "rt_proof")
    if len(nonce) != 16 or len(i) != ID_LEN or len(p) != 32:
        raise PasskeyError("bad", "rt_shape")
    return {"i": wire.b64u(i), "p": p, "uh": b64(obj.get("uh"), 64, "uh"), "ts": ts, "nonce": nonce}


def verify(rt: dict, f: dict, challenge: bytes) -> None:
    """The stored ticket `rt` (its id already matched f["i"]) against the proof. Raises PasskeyError("bad", …)."""
    try:
        key = wire.unb64u(rt.get("k") or "")
    except ValueError:
        raise PasskeyError("bad", "stored_rt") from None
    if len(key) != KEY_LEN:
        raise PasskeyError("bad", "stored_rt")
    if not hmac.compare_digest(proof(key, challenge), f["p"]):
        raise PasskeyError("bad", "rt_proof")
