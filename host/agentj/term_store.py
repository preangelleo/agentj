"""P408 (ADR-P408-terminal H2 H3 L2 M5, PROTOCOL §38): the web terminal's files under `<state>/terminal/` (0700).

- `config.json` (0600): `{"on", "entries": [{"device", "until"?, "sudo", "web"}], "minutes"}` — written only by serve's
  `terminal_cfg` (widening after the owner's terminal + a passkey remote's card) or by a narrowing CLI call. `prune` drops
  entries whose device left the allowlist, lost its passkey or whose `until` passed, and their caches.
- P414: the password lives in the vault (term_vault.py: `vault-<kid>.json` + `wrap-<device>.json`); `meta` / `save` / `load` /
  `forget` / `drop_local` / `prune` keep P408's signatures on top of it. P408's `login-<device>.bin` (below) is read only to
  migrate it on its first successful load.
- `login-<device>.bin` (0600, P408 v1): the computer password, AES-256-GCM, AAD `v|mode|channel|device|credential`.
  `prf`: K = HKDF(prf ‖ kek, salt, "agentjarvis-term-login-v1") — the phone's WebAuthn PRF output is needed to open it.
  `local`: K = HKDF(kek, salt, …) — only while the Agent is fenced and independent (`local_ok`), deleted when that stops.
  `kek` = HKDF over the host's X25519 identity file with this module's own salt / info (not mail's). Only ciphertext, the
  salt, the mode and the credential id are stored. A temporary entry never gets a file (session mode, memory only).
- `locks.json` (0600): wrong passwords — 3 in a row on one device → 15 min; 10 in an hour on this host → 1 h.
"""
from __future__ import annotations

import contextlib
import json
import os
import pathlib
import re
import secrets
import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from . import term_vault as vault, wire

DIR = "terminal"
MIN_DEFAULT, MIN_MAX, IDLE_S = 15, 60, 300
DEV_FAILS, DEV_LOCK = 3, 900
HOST_FAILS, HOST_WINDOW, HOST_LOCK = 10, 3600, 3600
INFO = b"agentjarvis-term-login-v1"
_DEV = re.compile(r"[A-Za-z0-9_-]{1,64}")


def root(st) -> pathlib.Path:
    d = st.root / DIR
    d.mkdir(mode=0o700, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(d, 0o700)
    return d


def _read(path: pathlib.Path) -> dict:
    try:
        d = json.loads(path.read_text())
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    return d if isinstance(d, dict) else {}


# ---------------------------------------------------------------- config
def read_config(st) -> dict:
    d = _read(root(st) / "config.json")
    m = d.get("minutes")
    out = {"on": d.get("on") is True, "entries": [],
           "minutes": m if type(m) is int and 1 <= m <= MIN_MAX else MIN_DEFAULT}
    for e in d.get("entries") or []:
        if isinstance(e, dict) and isinstance(e.get("device"), str) and _DEV.fullmatch(e["device"]):
            x = {"device": e["device"], "sudo": e.get("sudo") is not False, "web": e.get("web") is True}
            if type(e.get("until")) is int:
                x["until"] = e["until"]
            out["entries"].append(x)
    return out


def write_config(st, cfg: dict) -> dict:
    st.write_private(root(st) / "config.json", json.dumps(cfg, ensure_ascii=False).encode())
    return read_config(st)


def entry(cfg: dict, device: str) -> dict | None:
    return next((e for e in cfg["entries"] if e["device"] == device), None)


def is_web(st, device: str) -> bool:
    """P122: a remote that is the web version (it holds a cookie renewal ticket)."""
    rec = st.devices().get(device)
    return isinstance(rec, dict) and isinstance(rec.get("rt"), dict)


PHONE_OS = ("iOS", "Android", "华为手机", "华为平板")   # P417 #185: js/device-label.js describeDevice() OS words
DESK_OS = ("macOS", "Windows", "Linux")


def device_kind(st, device: str | None) -> str:
    """P417 #185: "phone" · "desktop" · "unknown" — from the label the remote gave itself AT PAIRING (`self_name`,
    「网页 · <OS> <browser>」 built from its user agent; fixed until it pairs again; the owner's rename never touches it).
    A label without that shape (older records, other clients) is "unknown": it may still open when the owner listed it at
    the computer, but it may not switch the terminal on remotely. Only a routing fact the device itself claimed — the gates
    stay the signature, the passkey, the computer password, the recording and the time limit."""
    from .devnames import device_names, platform_of
    rec = st.devices().get(device) if device else None
    if not isinstance(rec, dict):
        return "unknown"
    plat = platform_of(device_names(rec)["self_name"])
    word = plat.split(" ", 1)[0] if plat else ""
    return "phone" if word in PHONE_OS else "desktop" if word in DESK_OS else "unknown"


def add_owner_entry(st, device: str, web: bool) -> dict:
    """P417 #185: a remote switched the terminal on for itself (signature + passkey + the computer password, checked by
    sudo) → on, and this device an owner entry (no `until`)."""
    cfg = read_config(st)
    keep = [e for e in cfg["entries"] if e["device"] != device]
    return write_config(st, {"on": True, "entries": keep + [{"device": device, "sudo": True, "web": bool(web)}],
                             "minutes": cfg["minutes"]})


def prune(st, cfg: dict | None = None, now: float | None = None) -> dict:
    """Drop entries that no longer hold (device unpaired, passkey gone, `until` passed). P414: the vault's wraps follow the
    remote, not the terminal list (a §11 sudo card may use a `prf` wrap): a wrap goes when its device is unpaired or its
    passkey changed; a `local` wrap (terminal only) also when the device is not an owner entry. P408 v1 caches of devices not
    on the list go too. Writes only when something changed."""
    cfg = cfg or read_config(st)
    now = time.time() if now is None else now
    keep = [e for e in cfg["entries"] if st.passkey_of_device(e["device"]) and e.get("until", now + 1) > now]
    if keep != cfg["entries"]:
        cfg = write_config(st, {**cfg, "entries": keep})
    listed = {e["device"] for e in keep if "until" not in e}
    for p in root(st).glob("login-*.bin"):
        if p.name[6:-4] not in listed:
            forget(st, p.name[6:-4])
    for dev, m in vault.wraps(st).items():
        pk = st.passkey_of_device(dev)
        if not pk or pk["id"] != m["cred"] or (m["mode"] == "local" and dev not in listed):
            vault.drop_wrap(st, dev)
    vault.gc(st)
    return cfg


# ---------------------------------------------------------------- the cached password (P414: on the vault, term_vault.py)
def _path(st, device: str) -> pathlib.Path:
    """P408 v1 file (read only for the migration)."""
    if not _DEV.fullmatch(device or ""):
        raise ValueError("device")
    return root(st) / f"login-{device}.bin"


def kek(st) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"agentj-term",
                info=b"agentjarvis-term-kek-v1").derive(st.x25519_path.read_bytes())


def _key(st, mode: str, salt: bytes, prf) -> bytes:
    if mode == "prf":
        if not prf or len(prf) < 16:
            raise ValueError("prf")
        ikm = bytearray(prf)
        ikm += kek(st)
    else:
        ikm = bytearray(kek(st))
    try:
        return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=INFO).derive(ikm)
    finally:
        for i in range(len(ikm)):
            ikm[i] = 0


def aad(mode: str, channel: str, device: str, cred: str) -> bytes:
    return f"1|{mode}|{channel}|{device}|{cred}".encode()


def _legacy(st, device: str) -> dict | None:
    try:
        d = _read(_path(st, device))
        if d.get("v") == 1 and d.get("mode") in ("prf", "local") and isinstance(d.get("cred"), str):
            return {"mode": d["mode"], "salt": wire.unb64u(d["salt"]), "cred": d["cred"], "ct": wire.unb64u(d["ct"]),
                    "legacy": True}
    except (ValueError, TypeError, KeyError):
        pass
    return None


def meta(st, device: str) -> dict | None:
    """{"mode", "salt" (bytes), "cred"[, "kid" | "legacy"]} of this device's wrap (P414) or P408 v1 cache, or None."""
    m = vault.wrap_meta(st, device)
    if m and vault.vault_path(st, m["kid"]).exists():
        return {"mode": m["mode"], "salt": m["salt"], "cred": m["cred"], "kid": m["kid"]}
    return _legacy(st, device)


def save(st, channel: str, device: str, cred: str, mode: str, salt: bytes, pw: bytearray, prf) -> str:
    """A new kid for this device (P414: other remotes keep theirs); a 64-byte PRF value = prf1 ‖ prf2: prf1 is over `salt`."""
    p1, _p2 = vault.split_prf(prf) if mode == "prf" else (None, None)
    try:
        kid = vault.create(st, channel, device, cred, mode, salt, pw, p1)
    finally:
        vault._wipe(p1)
        vault._wipe(_p2)
    with contextlib.suppress(FileNotFoundError, ValueError):
        _path(st, device).unlink()
    return kid


def load(st, channel: str, device: str, cred: str, prf, vn: bytes | None = None) -> bytearray | None:
    """The password (the caller wipes it), or None: no cache, another credential, PRF missing / wrong, damaged. P414: a
    64-byte PRF value with `vn` rotates the wrap (M3); a P408 v1 file migrates to the vault on its first successful load."""
    if vault.wrap_meta(st, device):
        got = vault.unlock(st, channel, device, cred, prf, vn)
        return got[1] if got else None
    m = _legacy(st, device)
    if not m or m["cred"] != cred:
        return None
    p1, p2 = vault.split_prf(prf) if m["mode"] == "prf" else (None, None)
    try:
        try:
            pt = AESGCM(_key(st, m["mode"], m["salt"], p1)).decrypt(m["ct"][:12], m["ct"][12:],
                                                                    aad(m["mode"], channel, device, cred))
        except Exception:   # noqa: BLE001 — wrong PRF / damaged / another host: not usable, never a detail
            return None
        out = bytearray(pt)
        vault._wipe(pt)
        with contextlib.suppress(OSError, ValueError):       # P414: migrate (re-save as v2 under the same salt, old file goes)
            save(st, channel, device, cred, m["mode"], m["salt"], out, p1)
            if p2 is not None and vn:
                again = vault.unlock(st, channel, device, cred, prf, vn)
                if again:
                    vault._wipe(again[1])
        return out
    finally:
        vault._wipe(p1)
        vault._wipe(p2)


def forget_kid(st, device: str) -> int:
    """L2 (N4): sudo's own wrong-password message for this device's stored password → its kid and every wrap on it go."""
    m = vault.wrap_meta(st, device)
    n = vault.kill_kid(st, m["kid"]) if m else 0
    with contextlib.suppress(FileNotFoundError, ValueError):
        _path(st, device).unlink()
        n += 1
    vault.gc(st)
    return n


def forget(st, device: str | None = None) -> int:
    """Delete one device's wrap / v1 cache (or every one, and every kid) → how many files went. A kid left with no wrap goes."""
    n = 0
    if device is None:
        paths = list(root(st).glob("login-*.bin")) + list(root(st).glob("wrap-*.json")) + list(root(st).glob("vault-*.json"))
    else:
        paths = [_path(st, device), vault.wrap_path(st, device)]
    for p in paths:
        with contextlib.suppress(FileNotFoundError):
            p.unlink()
            if not p.name.startswith("vault-"):
                n += 1
    vault.gc(st)
    return n


def drop_local(st) -> int:
    """`local` caches are deleted the moment the Agent is no longer fenced + independent (N2)."""
    n = 0
    for p in root(st).glob("login-*.bin"):
        m = _legacy(st, p.name[6:-4])
        if m is None or m["mode"] == "local":
            with contextlib.suppress(FileNotFoundError):
                p.unlink()
                n += 1
    for dev, m in vault.wraps(st).items():
        if m["mode"] == "local":
            n += vault.drop_wrap(st, dev)
    vault.gc(st)
    return n


# ---------------------------------------------------------------- wrong-password locks
def _locks(st) -> dict:
    d = _read(root(st) / "locks.json")
    return {"dev": d.get("dev") if isinstance(d.get("dev"), dict) else {},
            "host": [t for t in d.get("host") or [] if isinstance(t, (int, float))],
            "host_until": d.get("host_until") if isinstance(d.get("host_until"), (int, float)) else 0}


def locked_for(st, device: str, now: float | None = None) -> int:
    now = time.time() if now is None else now
    d = _locks(st)
    rec = d["dev"].get(device) if isinstance(d["dev"].get(device), dict) else {}
    until = max(d["host_until"], rec.get("until", 0) if isinstance(rec.get("until"), (int, float)) else 0)
    return max(0, int(until - now + 0.999))


def note(st, device: str, ok: bool, now: float | None = None) -> int:
    """Record one password check → seconds locked now (0 = not locked)."""
    now = time.time() if now is None else now
    d = _locks(st)
    rec = d["dev"].get(device) if isinstance(d["dev"].get(device), dict) else {}
    if ok:
        d["dev"].pop(device, None)
    else:
        fails = int(rec.get("fails", 0)) + 1
        rec = {"fails": 0, "until": now + DEV_LOCK} if fails >= DEV_FAILS else {"fails": fails, "until": rec.get("until", 0)}
        d["dev"][device] = rec
        d["host"] = [t for t in d["host"] if now - t < HOST_WINDOW] + [now]
        if len(d["host"]) >= HOST_FAILS:
            d["host_until"], d["host"] = now + HOST_LOCK, []
    st.write_private(root(st) / "locks.json", json.dumps(d).encode())
    return locked_for(st, device, now)
