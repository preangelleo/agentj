"""The approval passphrase (批准口令, L2 / G-A8): a second gate in front of every device approval that only the human has.

The 6-digit code proves *which phone* is being paired; the passphrase proves *a human at this host* is the one approving.
The agent runs fenced (fence.py) and cannot see this state directory; even a process that reaches the control socket or
holds an admin-page session cannot approve without the passphrase. Only an scrypt hash is stored (approver.json, 0600),
next to a persisted failure ledger (5 wrong in a row → locked, the lock doubles from 1 min up to 1 h; a restart resets
nothing). Never logged, never sent anywhere, never shown.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time

MIN_LEN = 8
MAX_LEN = 256
FREE_TRIES = 5                 # wrong passphrases in a row before the first lock
LOCK_FIRST, LOCK_MAX = 60, 3600
N, R, P = 2 ** 15, 8, 1        # scrypt: ~32 MiB, ~0.1 s per check on a laptop


class GateError(Exception):
    """reason: not_set | locked | wrong | too_short | too_long | mismatch | exists."""

    def __init__(self, reason: str, **kw):
        super().__init__(reason)
        self.reason, self.info = reason, kw


def _hash(secret: str, salt: bytes, n: int = N, r: int = R, p: int = P) -> bytes:
    return hashlib.scrypt(secret.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=128 * 1024 * 1024, dklen=32)


def _load(st) -> dict | None:
    try:
        d = json.loads(st.approver_path.read_text())
    except (FileNotFoundError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("v") == 1 else None


def is_set(st) -> bool:
    d = _load(st)
    return bool(d and isinstance(d.get("hash"), str))


def check_new(secret: str) -> None:
    if len(secret) < MIN_LEN:
        raise GateError("too_short", min=MIN_LEN)
    if len(secret) > MAX_LEN:
        raise GateError("too_long", max=MAX_LEN)


def set_passphrase(st, new: str, old: str | None = None) -> None:
    """First set, or change (needs the old one, same lock rules as an approval)."""
    check_new(new)
    with st.config_lock():
        d = _load(st)
        if d and d.get("hash"):
            if old is None:
                raise GateError("exists")
            _verify_locked(st, d, old)
        salt = secrets.token_bytes(16)
        rec = {"v": 1, "kdf": "scrypt", "n": N, "r": R, "p": P, "salt": salt.hex(), "hash": _hash(new, salt).hex(),
               "set_at": int(time.time()), "fails": 0, "locks": 0, "locked_until": 0}
        st.write_private(st.approver_path, json.dumps(rec).encode())
    st.log("passphrase_set", change=bool(d and d.get("hash")))


def lock_left(st, now: float | None = None) -> int:
    d = _load(st) or {}
    return max(0, int(d.get("locked_until", 0) - (now or time.time())))


def tries_left(st) -> int:
    d = _load(st) or {}
    return max(0, FREE_TRIES - int(d.get("fails", 0)))


def verify(st, secret: str | None, now: float | None = None) -> int:
    """Raise GateError(not_set | locked | wrong) or return the number of free tries the human had left (for messages)."""
    with st.config_lock():
        d = _load(st)
        if not d or not d.get("hash"):
            raise GateError("not_set")
        return _verify_locked(st, d, secret or "", now)


def _verify_locked(st, d: dict, secret: str, now: float | None = None) -> int:
    now = now or time.time()
    if d.get("locked_until", 0) > now:
        raise GateError("locked", seconds=int(d["locked_until"] - now))
    try:
        want = bytes.fromhex(d["hash"])
        got = _hash(secret[:MAX_LEN], bytes.fromhex(d["salt"]), int(d.get("n", N)), int(d.get("r", R)), int(d.get("p", P)))
    except (KeyError, ValueError, TypeError):
        raise GateError("not_set") from None
    if hmac.compare_digest(want, got):
        if d.get("fails") or d.get("locks"):
            d["fails"], d["locks"], d["locked_until"] = 0, 0, 0
            st.write_private(st.approver_path, json.dumps(d).encode())
        return FREE_TRIES
    d["fails"] = int(d.get("fails", 0)) + 1
    left = FREE_TRIES - d["fails"]
    if left <= 0:
        d["locks"] = int(d.get("locks", 0)) + 1
        d["locked_until"] = now + min(LOCK_MAX, LOCK_FIRST * 2 ** (d["locks"] - 1))
        d["fails"] = 0
        left = 0
    st.write_private(st.approver_path, json.dumps(d).encode())
    st.log("passphrase_wrong", locked=left == 0)
    raise GateError("locked" if left == 0 else "wrong", left=left,
                    seconds=int(d["locked_until"] - now) if left == 0 else 0)


def reset(st) -> None:
    """Forgotten passphrase: remove it. The caller also revokes every device (cli: `agentj passphrase reset`)."""
    with st.config_lock():
        if os.path.exists(st.approver_path):
            os.unlink(st.approver_path)
    st.log("passphrase_reset")


MESSAGES = {
    "not_set": "还没有设置批准口令：先在这台电脑的终端里运行 `agentj passphrase set`（只有你知道，别让 Agent 替你设）。",
    "locked": "批准口令连续输错，已暂时锁定，请稍后再试。",
    "wrong": "批准口令不对。",
    "too_short": f"批准口令至少 {MIN_LEN} 个字符。",
    "too_long": f"批准口令最多 {MAX_LEN} 个字符。",
    "mismatch": "两次输入不一致。",
    "exists": "已经设置过批准口令；要改用 `agentj passphrase change`，忘了就用 `agentj passphrase reset`（所有手机都要重新配对）。",
}
