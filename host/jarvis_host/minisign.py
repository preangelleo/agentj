"""minisign signature verification (verify only) for official plaza packages (protocol/PLAZA_PACKAGES.md §2).

A `.minisig` file is four lines:

    untrusted comment: <anything — ignored>
    base64( "ED" ‖ key id (8 bytes, little-endian) ‖ Ed25519 signature over BLAKE2b-512(message) (64) )
    trusted comment: <text>
    base64( Ed25519 signature over (the 64-byte signature ‖ the trusted comment's UTF-8 bytes) )   ← the global signature

Only the prehashed algorithm `ED` (minisign's default since 0.8) is accepted; the legacy `Ed` (signature over the raw
message) is refused. The key id must be in the keyring (`TRUSTED_KEYS`, compiled in: the plaza key; tests inject their own)
and equal the id inside that public key. For a plaza package the trusted comment must be exactly
`agentjarvis-plaza/v1 name=<name> version=<version> type=<type> sha256=<bundle sha256 hex>` and every field must equal the
bundle's manifest and the bundle bytes (`verify_package`). Nothing else makes the host call a package 官方认证 / certified.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import re

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# key id (hex as `minisign` prints it) → public key (the base64 line of the .pub file)
TRUSTED_KEYS = {"B79F925B0585F9D4": "RWTU+YUFW5KftwwG/MQxB1V0OEpzftjDN8udv1Id9oYK9+OUanc2uyb5"}
MAX_SIG = 1024
TRUSTED_PREFIX = "agentjarvis-plaza/v1"
_TC = re.compile(r"agentjarvis-plaza/v1 name=([a-z0-9][a-z0-9-]{1,39}) version=(\S{5,60}) type=(skill|workflow) "
                 r"sha256=([0-9a-f]{64})")


class MinisignError(ValueError):
    """One line: why the signature is refused."""


def _b64(s: str, n: int, what: str) -> bytes:
    try:
        raw = base64.b64decode(s.strip(), validate=True)
    except (binascii.Error, ValueError):
        raise MinisignError(f"{what}: bad base64") from None
    if len(raw) != n:
        raise MinisignError(f"{what}: {len(raw)} bytes, expected {n}")
    return raw


def key_id_hex(kid: bytes) -> str:
    return kid[::-1].hex().upper()


def parse_pubkey(line: str) -> tuple[str, bytes]:
    """The base64 line of a minisign .pub → (key id hex, 32-byte Ed25519 key)."""
    raw = _b64(line, 42, "public key")
    if raw[:2] != b"Ed":
        raise MinisignError("public key: not an Ed25519 minisign key")
    return key_id_hex(raw[2:10]), raw[10:]


def parse_sig(text: str) -> dict:
    """→ {alg, key_id, sig, trusted_comment, global_sig}; raises MinisignError."""
    if not isinstance(text, str) or len(text.encode("utf-8", "replace")) > MAX_SIG:
        raise MinisignError("signature: missing or larger than 1 KiB")
    lines = text.replace("\r\n", "\n").rstrip("\n").split("\n")
    if len(lines) != 4:
        raise MinisignError("signature: expected 4 lines")
    if not lines[0].startswith("untrusted comment: ") or not lines[2].startswith("trusted comment: "):
        raise MinisignError("signature: comment lines missing")
    raw = _b64(lines[1], 74, "signature")
    return {"alg": raw[:2], "key_id": key_id_hex(raw[2:10]), "sig": raw[10:],
            "trusted_comment": lines[2][len("trusted comment: "):], "global_sig": _b64(lines[3], 64, "global signature")}


def verify(message: bytes, sig_text: str, keyring: dict | None = None) -> str:
    """Verify a minisign signature over `message`. Returns the (verified) trusted comment; raises MinisignError."""
    keyring = TRUSTED_KEYS if keyring is None else keyring
    s = parse_sig(sig_text)
    if s["alg"] != b"ED":
        raise MinisignError("signature: only prehashed (ED) signatures are accepted")
    pub_line = keyring.get(s["key_id"])
    if not pub_line:
        raise MinisignError(f"signature: key {s['key_id']} is not trusted")
    kid, pk = parse_pubkey(pub_line)
    if kid != s["key_id"]:
        raise MinisignError("signature: keyring entry does not match its key id")
    key = Ed25519PublicKey.from_public_bytes(pk)
    try:
        key.verify(s["sig"], hashlib.blake2b(bytes(message), digest_size=64).digest())
    except InvalidSignature:
        raise MinisignError("signature: does not verify over these bytes") from None
    try:
        tc = s["trusted_comment"].encode("utf-8")
        key.verify(s["global_sig"], s["sig"] + tc)
    except (InvalidSignature, UnicodeEncodeError):
        raise MinisignError("signature: the trusted comment is not signed (global signature invalid)") from None
    return s["trusted_comment"]


def trusted_comment(name: str, version: str, type_: str, sha256_hex: str) -> str:
    return f"{TRUSTED_PREFIX} name={name} version={version} type={type_} sha256={sha256_hex}"


def parse_trusted(tc: str) -> dict | None:
    m = _TC.fullmatch(tc or "")
    return dict(zip(("name", "version", "type", "sha256"), m.groups())) if m else None


def verify_package(bundle_bytes: bytes, sig_text: str, manifest: dict, keyring: dict | None = None) -> dict:
    """Signature valid, key trusted, trusted comment = name / version / type of the manifest + SHA-256 of these bytes.
    Returns the parsed trusted comment; raises MinisignError."""
    tc = verify(bundle_bytes, sig_text, keyring)
    want = trusted_comment(manifest.get("name"), manifest.get("version"), manifest.get("type"),
                           hashlib.sha256(bytes(bundle_bytes)).hexdigest())
    if tc != want:
        raise MinisignError("signature: signed for another package / version / type / bundle")
    return parse_trusted(tc) or {}
