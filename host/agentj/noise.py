"""Minimal Noise (rev 34) for agentj: Noise_IK / Noise_IKpsk2 (host ↔ device) and Noise_XX / Noise_KK (host ↔ host, agent
friends, PROTOCOL §17.4) over 25519 / AESGCM / SHA256.

Mirror of protocol/noise.js for IK / IKpsk2 (the JS side has no XX / KK: phones never handshake with friends).
Primitives come only from `cryptography` (OpenSSL) and the stdlib (hmac/hashlib). Pinned by protocol/vectors/noise-ik.json
and noise-xx-kk.json, and cross-checked against the `noiseprotocol` package in tests.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
from collections.abc import Callable

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import serialization

IK = "Noise_IK_25519_AESGCM_SHA256"
IKPSK2 = "Noise_IKpsk2_25519_AESGCM_SHA256"
XX = "Noise_XX_25519_AESGCM_SHA256"
KK = "Noise_KK_25519_AESGCM_SHA256"
PATTERNS = {
    IK: [["e", "es", "s", "ss"], ["e", "ee", "se"]],
    IKPSK2: [["e", "es", "s", "ss"], ["e", "ee", "se", "psk"]],
    XX: [["e"], ["e", "ee", "s", "es"], ["s", "se"]],
    KK: [["e", "es", "ss"], ["e", "ee", "se"]],
}
# pre-messages: (initiator's static known to the responder, responder's static known to the initiator)
PRE = {IK: (False, True), IKPSK2: (False, True), XX: (False, False), KK: (True, True)}
MAX_NONCE = 2**64 - 1  # reserved by the spec (the JS side stops earlier, at 2^53: both fail closed long before)


class NoiseError(Exception):
    pass


def sha256(d: bytes) -> bytes:
    return hashlib.sha256(d).digest()


def hmac256(key: bytes, data: bytes) -> bytes:
    return _hmac.new(key, data, hashlib.sha256).digest()


def hkdf(ck: bytes, ikm: bytes, n: int) -> list[bytes]:
    temp = hmac256(ck, ikm)
    o1 = hmac256(temp, b"\x01")
    o2 = hmac256(temp, o1 + b"\x02")
    if n == 2:
        return [o1, o2]
    return [o1, o2, hmac256(temp, o2 + b"\x03")]


class Keypair:
    def __init__(self, priv: X25519PrivateKey):
        self.priv = priv
        self.pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)

    @classmethod
    def generate(cls) -> "Keypair":
        return cls(X25519PrivateKey.generate())

    @classmethod
    def from_private(cls, raw: bytes) -> "Keypair":
        return cls(X25519PrivateKey.from_private_bytes(raw))

    def private_bytes(self) -> bytes:
        return self.priv.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                       serialization.NoEncryption())


def dh(kp: Keypair, pub: bytes) -> bytes:
    if len(pub) != 32:
        raise NoiseError("bad public key")
    pub = bytes(pub)
    try:
        return kp.priv.exchange(X25519PublicKey.from_public_bytes(pub))
    except ValueError as e:  # all-zero output (small-order point)
        raise NoiseError("bad DH") from e


def _nonce(n: int) -> bytes:
    return b"\x00\x00\x00\x00" + n.to_bytes(8, "big")


class CipherState:
    def __init__(self, k: bytes | None = None):
        self.k = k
        self.n = 0
        self._aead = AESGCM(k) if k is not None else None

    def has_key(self) -> bool:
        return self.k is not None

    def encrypt(self, ad: bytes, pt: bytes) -> bytes:
        if self._aead is None:
            return pt
        if self.n >= MAX_NONCE:
            raise NoiseError("nonce exhausted")
        ct = self._aead.encrypt(_nonce(self.n), pt, ad)
        self.n += 1
        return ct

    def decrypt(self, ad: bytes, ct: bytes) -> bytes:
        if self._aead is None:
            return ct
        if self.n >= MAX_NONCE:
            raise NoiseError("nonce exhausted")
        try:
            pt = self._aead.decrypt(_nonce(self.n), ct, ad)
        except Exception as e:  # InvalidTag
            raise NoiseError("decrypt failed") from e
        self.n += 1  # only after a successful decrypt
        return pt


class Handshake:
    def __init__(self, protocol: str, initiator: bool, s: Keypair, prologue: bytes = b"",
                 rs: bytes | None = None, psk: bytes | None = None, e: Keypair | None = None):
        if protocol not in PATTERNS:
            raise NoiseError("unsupported protocol")
        if protocol == IKPSK2 and (psk is None or len(psk) != 32):
            raise NoiseError("psk required")
        self.protocol, self.initiator, self.s, self.rs, self.psk, self.e = protocol, initiator, s, rs, psk, e
        self.re: bytes | None = None
        self.msgs = PATTERNS[protocol]
        self.i = 0
        name = protocol.encode()
        self.h = name.ljust(32, b"\x00") if len(name) <= 32 else sha256(name)
        self.ck = self.h
        self.cs = CipherState()
        self.h_before_payload: bytes | None = None  # h after the last message's tokens, before its payload (§17.4 signs it)
        pre_i, pre_r = PRE[protocol]
        if (pre_r if initiator else pre_i) and (rs is None or len(rs) != 32):
            raise NoiseError("remote static key required")
        self.mix_hash(prologue)
        if pre_i:  # pre-message -> s (the initiator's static)
            self.mix_hash(s.pub if initiator else rs)
        if pre_r:  # pre-message <- s (the responder's static)
            self.mix_hash(rs if initiator else s.pub)

    def mix_hash(self, d: bytes) -> None:
        self.h = sha256(self.h + d)

    def mix_key(self, ikm: bytes) -> None:
        self.ck, k = hkdf(self.ck, ikm, 2)
        self.cs = CipherState(k)

    def mix_key_and_hash(self, ikm: bytes) -> None:
        self.ck, th, k = hkdf(self.ck, ikm, 3)
        self.mix_hash(th)
        self.cs = CipherState(k)

    def encrypt_and_hash(self, pt: bytes) -> bytes:
        ct = self.cs.encrypt(self.h, pt)
        self.mix_hash(ct)
        return ct

    def decrypt_and_hash(self, ct: bytes) -> bytes:
        pt = self.cs.decrypt(self.h, ct)
        self.mix_hash(ct)
        return pt

    def _dh(self, tok: str) -> bytes:
        i = self.initiator
        if tok == "ee":
            return dh(self.e, self.re)
        if tok == "ss":
            return dh(self.s, self.rs)
        if tok == "es":
            return dh(self.e, self.rs) if i else dh(self.s, self.re)
        return dh(self.s, self.re) if i else dh(self.e, self.rs)  # se

    def my_turn(self) -> bool:
        return (self.i % 2 == 0) == self.initiator

    def done(self) -> bool:
        return self.i >= len(self.msgs)

    def write_message(self, payload: bytes | Callable[[bytes], bytes] = b"") -> bytes:
        """payload may be a function of the handshake hash after this message's tokens (PROTOCOL §17.4 signs that hash)."""
        if self.done() or not self.my_turn():
            raise NoiseError("not my turn")
        psk_mode = self.protocol == IKPSK2
        out = []
        for tok in self.msgs[self.i]:
            if tok == "e":
                if self.e is None:
                    self.e = Keypair.generate()
                out.append(self.e.pub)
                self.mix_hash(self.e.pub)
                if psk_mode:
                    self.mix_key(self.e.pub)
            elif tok == "s":
                out.append(self.encrypt_and_hash(self.s.pub))
            elif tok == "psk":
                self.mix_key_and_hash(self.psk)
            else:
                self.mix_key(self._dh(tok))
        self.h_before_payload = self.h
        out.append(self.encrypt_and_hash(payload(self.h) if callable(payload) else payload))
        self.i += 1
        return b"".join(out)

    def read_message(self, msg: bytes) -> bytes:
        if self.done() or self.my_turn():
            raise NoiseError("not their turn")
        psk_mode = self.protocol == IKPSK2
        msg = bytes(msg)
        o = 0

        def take(n: int) -> bytes:
            nonlocal o
            if o + n > len(msg):
                raise NoiseError("short message")
            b = msg[o:o + n]
            o += n
            return b

        for tok in self.msgs[self.i]:
            if tok == "e":
                self.re = take(32)
                self.mix_hash(self.re)
                if psk_mode:
                    self.mix_key(self.re)
            elif tok == "s":
                self.rs = self.decrypt_and_hash(take(48 if self.cs.has_key() else 32))
            elif tok == "psk":
                self.mix_key_and_hash(self.psk)
            else:
                self.mix_key(self._dh(tok))
        self.h_before_payload = self.h
        payload = self.decrypt_and_hash(msg[o:])
        self.i += 1
        return payload

    def split(self) -> tuple[CipherState, CipherState, bytes]:
        """(send, recv, handshake_hash) for this side."""
        if not self.done():
            raise NoiseError("handshake not finished")
        k1, k2 = hkdf(self.ck, b"", 2)
        c1, c2 = CipherState(k1), CipherState(k2)
        return (c1, c2, self.h) if self.initiator else (c2, c1, self.h)
