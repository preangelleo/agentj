"""P414 (ADR-P414-bang, PROTOCOL §37.9): 电脑密码保险箱 — the computer password as ciphertext under a random key K, K wrapped
per remote. Pure storage (no I/O on the event loop: serve calls these through asyncio.to_thread).

Files under `<state>/terminal/` (0600):
- `vault-<kid>.json` = `{"v":2, "kid", "ct": iv ‖ AES-256-GCM(K, pw, aad "agentj-vault-v2|<channel>|<kid>")}`. K = 32 random
  bytes that never touch the disk in the clear.
- `wrap-<device>.json` = `{"v":2, "kid", "mode", "salt", "cred", "ct": iv ‖ AES-256-GCM(W, K, aad
  "agentj-wrap-v2|mode|channel|device|cred|kid")}`. `prf`: W = HKDF-SHA256(prf ‖ kek, salt, "agentjarvis-vault-wrap-v2") — that
  remote's WebAuthn PRF output over its own `salt` is needed; `local` (P408's fenced-only mode, terminal only): W = HKDF(kek, …).
  `kek` = term_store.kek (HKDF over the host's X25519 identity file, P408).

K, W, PRF outputs and the password are bytearrays wiped in `finally` (the caller wipes what it gets back). M3: a 64-byte PRF
value = prf1 ‖ prf2 (WebAuthn `eval.first` = the wrap's salt, `eval.second` = the host-issued next salt `vn`): unwrap with prf1,
re-wrap K under (prf2, vn). L2: a kid is deleted (with every wrap on it) only on sudo's own wrong-password message. A kid with no
wrap left is deleted (`gc`). P408 `login-<device>.bin` (v1) migrates on its first successful load.
"""
from __future__ import annotations

import contextlib
import json
import pathlib
import re
import secrets

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from . import wire

WRAP_INFO = b"agentjarvis-vault-wrap-v2"
PRF_LEN = 32
_DEV = re.compile(r"[A-Za-z0-9_-]{1,64}")
_KID = re.compile(r"[0-9a-f]{16}")


def _wipe(b) -> None:
    from .elevate import wipe
    wipe(b)


def _root(st) -> pathlib.Path:
    from .term_store import root
    return root(st)


def _read(path: pathlib.Path) -> dict:
    try:
        d = json.loads(path.read_text())
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    return d if isinstance(d, dict) else {}


def wrap_path(st, device: str) -> pathlib.Path:
    if not _DEV.fullmatch(device or ""):
        raise ValueError("device")
    return _root(st) / f"wrap-{device}.json"


def vault_path(st, kid: str) -> pathlib.Path:
    if not _KID.fullmatch(kid or ""):
        raise ValueError("kid")
    return _root(st) / f"vault-{kid}.json"


def split_prf(prf) -> tuple[bytearray | None, bytearray | None]:
    """M3: 64 B = prf1 ‖ prf2 (rotation), else (prf, None). New bytearrays — the caller wipes them and its own input."""
    if not prf:
        return None, None
    if len(prf) == 2 * PRF_LEN:
        return bytearray(prf[:PRF_LEN]), bytearray(prf[PRF_LEN:])
    return bytearray(prf), None


def _wkey(st, mode: str, salt: bytes, prf) -> bytes:
    from .term_store import kek
    if mode == "prf":
        if not prf or len(prf) < 16:
            raise ValueError("prf")
        ikm = bytearray(prf)
        ikm += kek(st)
    else:
        ikm = bytearray(kek(st))
    try:
        return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=WRAP_INFO).derive(ikm)
    finally:
        _wipe(ikm)


def wrap_aad(mode: str, channel: str, device: str, cred: str, kid: str) -> bytes:
    return f"agentj-wrap-v2|{mode}|{channel}|{device}|{cred}|{kid}".encode()


def vault_aad(channel: str, kid: str) -> bytes:
    return f"agentj-vault-v2|{channel}|{kid}".encode()


# ---------------------------------------------------------------- reading
def wrap_meta(st, device: str) -> dict | None:
    """{"kid", "mode", "salt" (bytes), "cred", "ct"} of this device's wrap, or None."""
    try:
        d = _read(wrap_path(st, device))
        if d.get("v") == 2 and d.get("mode") in ("prf", "local") and isinstance(d.get("cred"), str) and \
                isinstance(d.get("kid"), str) and _KID.fullmatch(d["kid"]):
            return {"kid": d["kid"], "mode": d["mode"], "salt": wire.unb64u(d["salt"]), "cred": d["cred"],
                    "ct": wire.unb64u(d["ct"])}
    except (ValueError, TypeError, KeyError):
        pass
    return None


def wraps(st) -> dict:
    """device → wrap meta, every wrap file."""
    out = {}
    for p in _root(st).glob("wrap-*.json"):
        dev = p.name[5:-5]
        m = wrap_meta(st, dev) if _DEV.fullmatch(dev) else None
        if m:
            out[dev] = m
    return out


def kids(st) -> set:
    return {p.name[6:-5] for p in _root(st).glob("vault-*.json") if _KID.fullmatch(p.name[6:-5])}


def has_prf(st, device: str) -> bool:
    """M5: `has` counts `prf` wraps for this device's CURRENT passkey only (a `local` wrap never answers a sudo card)."""
    m = wrap_meta(st, device)
    pk = st.passkey_of_device(device)
    return bool(m and m["mode"] == "prf" and pk and pk["id"] == m["cred"] and vault_path(st, m["kid"]).exists())


def prf_peers(st, device: str | None) -> list[str]:
    """The OTHER paired remotes holding a usable `prf` wrap."""
    return sorted(d for d in st.devices() if d != device and has_prf(st, d))


def any_vault(st) -> bool:
    return bool(kids(st))


# ---------------------------------------------------------------- K
def _unwrap(st, channel: str, device: str, cred: str, prf) -> tuple[str, bytearray] | None:
    m = wrap_meta(st, device)
    if not m or m["cred"] != cred or len(m["ct"]) < 12 + 16:
        return None
    w = None
    try:
        w = _wkey(st, m["mode"], m["salt"], prf)
        pt = AESGCM(w).decrypt(m["ct"][:12], m["ct"][12:], wrap_aad(m["mode"], channel, device, cred, m["kid"]))
    except Exception:   # noqa: BLE001 — wrong PRF / damaged / another host: not usable, never a detail
        return None
    finally:
        _wipe(w)
    k = bytearray(pt)
    _wipe(pt)
    if len(k) != 32:
        _wipe(k)
        return None
    return m["kid"], k


def put_wrap(st, channel: str, device: str, cred: str, mode: str, salt: bytes, k: bytearray, prf, kid: str) -> None:
    """Write (replace) this device's wrap of K; an orphaned old kid goes."""
    if mode not in ("prf", "local"):
        raise ValueError("mode")
    old = wrap_meta(st, device)
    w = _wkey(st, mode, salt, prf if mode == "prf" else None)
    try:
        iv = secrets.token_bytes(12)
        ct = AESGCM(w).encrypt(iv, bytes(k), wrap_aad(mode, channel, device, cred, kid))
    finally:
        _wipe(w)
    st.write_private(wrap_path(st, device), json.dumps({"v": 2, "kid": kid, "mode": mode, "salt": wire.b64u(salt),
                                                        "cred": cred, "ct": wire.b64u(iv + ct)}).encode())
    if old and old["kid"] != kid:
        gc(st)


def _decrypt_pw(st, channel: str, kid: str, k: bytearray) -> bytearray | None:
    d = _read(vault_path(st, kid))
    try:
        if d.get("v") != 2 or d.get("kid") != kid:
            return None
        blob = wire.unb64u(d["ct"])
        pt = AESGCM(bytes(k)).decrypt(blob[:12], blob[12:], vault_aad(channel, kid))
    except Exception:   # noqa: BLE001
        return None
    out = bytearray(pt)
    _wipe(pt)
    return out


# ---------------------------------------------------------------- the operations serve uses
def create(st, channel: str, device: str, cred: str, mode: str, salt: bytes, pw: bytearray, prf) -> str:
    """A NEW kid for this device (other remotes keep theirs); → the kid."""
    kid = secrets.token_hex(8)
    k = bytearray(secrets.token_bytes(32))
    try:
        iv = secrets.token_bytes(12)
        ct = AESGCM(bytes(k)).encrypt(iv, bytes(pw), vault_aad(channel, kid))
        st.write_private(vault_path(st, kid), json.dumps({"v": 2, "kid": kid, "ct": wire.b64u(iv + ct)}).encode())
        put_wrap(st, channel, device, cred, mode, salt, k, prf, kid)
    finally:
        _wipe(k)
    return kid


def unlock(st, channel: str, device: str, cred: str, prf, vn: bytes | None = None) -> tuple[str, bytearray] | None:
    """→ (kid, the password) — the caller wipes the password — or None. M3: a 64-byte `prf` with `vn` re-wraps K under
    (prf2, vn) after the unwrap with prf1 (the old wrap is replaced)."""
    p1, p2 = split_prf(prf)
    got = None
    try:
        got = _unwrap(st, channel, device, cred, p1)
        if got is None:
            return None
        kid, k = got
        pw = _decrypt_pw(st, channel, kid, k)
        if pw is None:
            return None
        m = wrap_meta(st, device)
        if m and m["mode"] == "prf" and p2 is not None and vn and len(vn) == 32:
            with contextlib.suppress(OSError, ValueError):
                put_wrap(st, channel, device, cred, "prf", vn, k, p2, kid)
        return kid, pw
    finally:
        if got is not None:
            _wipe(got[1])
        _wipe(p1)
        _wipe(p2)


def share(st, channel: str, src: str, src_cred: str, src_prf, src_vn: bytes | None, dst: str, dst_cred: str, dst_salt: bytes,
          dst_prf) -> bool:
    """`vshare`: unwrap K with the approving remote's PRF (rotating its wrap per M3), wrap K for the requesting remote."""
    p1, p2 = split_prf(src_prf)
    d1, _d2 = split_prf(dst_prf)
    got = None
    try:
        m = wrap_meta(st, src)
        if not m or m["mode"] != "prf":
            return False
        got = _unwrap(st, channel, src, src_cred, p1)
        if got is None or not d1:
            return False
        kid, k = got
        if not vault_path(st, kid).exists():
            return False
        put_wrap(st, channel, dst, dst_cred, "prf", dst_salt, k, d1, kid)
        if p2 is not None and src_vn and len(src_vn) == 32:
            with contextlib.suppress(OSError, ValueError):
                put_wrap(st, channel, src, src_cred, "prf", src_vn, k, p2, kid)
        return True
    finally:
        if got is not None:
            _wipe(got[1])
        for b in (p1, p2, d1, _d2):
            _wipe(b)


def drop_wrap(st, device: str) -> int:
    n = 0
    with contextlib.suppress(FileNotFoundError, ValueError):
        wrap_path(st, device).unlink()
        n = 1
    gc(st)
    return n


def kill_kid(st, kid: str) -> int:
    """L2: sudo's own wrong-password message for a stored password → that kid and every wrap on it go."""
    n = 0
    for dev, m in wraps(st).items():
        if m["kid"] == kid:
            with contextlib.suppress(FileNotFoundError):
                wrap_path(st, dev).unlink()
                n += 1
    with contextlib.suppress(FileNotFoundError, ValueError):
        vault_path(st, kid).unlink()
    return n


def gc(st) -> int:
    """A kid with no wrap left is deleted; a wrap whose kid is gone too."""
    ws = wraps(st)
    used = {m["kid"] for m in ws.values()}
    n = 0
    for kid in kids(st) - used:
        with contextlib.suppress(FileNotFoundError):
            vault_path(st, kid).unlink()
            n += 1
    have = kids(st)
    for dev, m in ws.items():
        if m["kid"] not in have:
            with contextlib.suppress(FileNotFoundError):
                wrap_path(st, dev).unlink()
    return n


def info(st, device: str | None) -> dict:
    """The `vault` host block (§37.9) for one session's device."""
    m = wrap_meta(st, device) if device else None
    return {"has": bool(device) and has_prf(st, device), "any": any_vault(st), "peers": len(prf_peers(st, device)),
            "mode": m["mode"] if m else None}
