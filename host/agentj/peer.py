"""Agent friends, transport layer (PROTOCOL §17.1, §17.2 client side, §17.4, §17.5 outbox): the peer keys and Agent ID, the
relay mailbox socket, the Noise XX (adding a friend) / KK (every later session) handshakes, and the sender outbox.

Everything above transport — who is a friend, groups, limits, the owner's approval, the peer session — lives behind the `cb`
object (`friends.py` / `peer_guard.py` / serve wiring). This module never decides policy: a request, a message, a receipt is
handed to `cb` and `cb` decides. Two properties it does own:
- **Indistinguishable refusals** (§17.4 / §17.5): as long as `cb.accepting()`, B always answers XX msg1 with msg2, and A's
  application-visible state for a request is only ever "pending" → "expired" (or the friend's `facc`). Whether msg3 drew a
  `0x22` changes only A's retry schedule and the local log, never `outbox_rows()` or a `cb` call.
- **No frame while stopped** (§17.9 stop-everything): `stopped(True)` silences every send; inbound frames still reach `cb`.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import pathlib
import re
import secrets
import sqlite3
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from . import wire
from .noise import KK, XX, Handshake, Keypair, NoiseError, sha256
from .state import _write_private

# ---------------------------------------------------------------- §17.1 identity
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"   # Crockford base32
_VAL = {c: i for i, c in enumerate(ALPHABET)}
_FIX = str.maketrans({"I": "1", "L": "1", "O": "0"})
_HEX16 = re.compile(r"[0-9a-f]{16}")
_NONCE = re.compile(r"[A-Za-z0-9_-]{43}")      # mailbox auth challenge n = b64url(32 random bytes), as §2
_MBOX = re.compile(r"[A-Za-z0-9_-]{22}")


def _check_char(vals: list[int]) -> str:
    return ALPHABET[sum((i + 1) * v for i, v in enumerate(vals)) % 31]


def _id75(x_pub: bytes, ed_pub: bytes) -> int:
    if len(x_pub) != 32 or len(ed_pub) != 32:
        raise ValueError("bad key")
    return int.from_bytes(sha256(b"agentj/peer-id/v1\n" + x_pub + ed_pub)[:10], "big") >> 5


def _fmt(id75: int) -> str:
    vals = [(id75 >> (5 * (14 - i))) & 31 for i in range(15)]
    s = "".join(ALPHABET[v] for v in vals) + _check_char(vals)
    return "AJ-" + "-".join(s[i:i + 4] for i in range(0, 16, 4))


def agent_id(x_pub: bytes, ed_pub: bytes) -> str:
    """`AJ-XXXX-XXXX-XXXX-XXXX`: 75 bits of SHA-256 over both peer public keys + one check character (§17.1)."""
    return _fmt(_id75(x_pub, ed_pub))


def parse_id(s) -> str | None:
    """Canonical ID, or None. Accepts lower case, spaces / dashes anywhere, the `AJ` prefix or not, I/L → 1, O → 0."""
    if not isinstance(s, str) or len(s) > 64:
        return None
    t = re.sub(r"[\s-]", "", s).upper()
    if len(t) == 18 and t.startswith("AJ"):
        t = t[2:]
    t = t.translate(_FIX)
    if len(t) != 16 or any(c not in _VAL for c in t):
        return None
    vals = [_VAL[c] for c in t[:15]]
    if _check_char(vals) != t[15]:
        return None
    return "AJ-" + "-".join(t[i:i + 4] for i in range(0, 16, 4))


def _id_int(aid: str) -> int:
    p = parse_id(aid)
    if p is None:
        raise ValueError("bad agent id")
    n = 0
    for c in p[3:].replace("-", "")[:15]:
        n = (n << 5) | _VAL[c]
    return n


def id_raw(aid: str) -> bytes:
    """`id75 << 5` as 10 bytes big-endian."""
    return (_id_int(aid) << 5).to_bytes(10, "big")


def mbox_of(aid: str) -> str:
    """The relay mailbox of an ID (22 b64url chars); one-way — an ID cannot be recovered from it."""
    return wire.b64u(sha256(b"agentj/mbox/v1\n" + id_raw(aid))[:16])


def _ed_pub(ed: Ed25519PrivateKey) -> bytes:
    return ed.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _ed_verify(pub: bytes, sig: bytes, msg: bytes) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(pub).verify(sig, msg)
        return True
    except Exception:  # InvalidSignature, bad key length
        return False


class PeerKeys:
    """`<state>/peer/peer_x25519.key` + `peer_ed25519.key` (raw 32 bytes each; dir 0700, files 0600). Separate from the host,
    device and approval keys."""

    def __init__(self, x: Keypair, ed: Ed25519PrivateKey):
        self.x, self.ed = x, ed
        self.x_pub = x.pub
        self.ed_pub = _ed_pub(ed)
        self.id = agent_id(self.x_pub, self.ed_pub)
        self.mbox = mbox_of(self.id)

    @staticmethod
    def _paths(st) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
        d = pathlib.Path(st.root) / "peer"
        return d, d / "peer_x25519.key", d / "peer_ed25519.key"

    @classmethod
    def load(cls, st) -> "PeerKeys | None":
        _, xp, ep = cls._paths(st)
        if not (xp.exists() and ep.exists()):
            return None
        return cls(Keypair.from_private(xp.read_bytes()), Ed25519PrivateKey.from_private_bytes(ep.read_bytes()))

    @classmethod
    def load_or_create(cls, st) -> "PeerKeys":
        k = cls.load(st)
        if k:
            return k
        d, xp, ep = cls._paths(st)
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)
        x, ed = Keypair.generate(), Ed25519PrivateKey.generate()
        _write_private(xp, x.private_bytes())
        _write_private(ep, ed.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                            serialization.NoEncryption()))
        return cls(x, ed)


# ---------------------------------------------------------------- §17.5 cards
def canonical_json(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign_card(keys: PeerKeys, card: dict) -> dict:
    """A signed copy of `card` (`v` 1, `id` = this host's ID, `ts` now unless given); `sig` covers everything else."""
    c = {k: v for k, v in card.items() if k != "sig"}
    c["v"], c["id"] = 1, keys.id
    c.setdefault("ts", int(time.time() * 1000))
    c["sig"] = wire.b64u(keys.ed.sign(canonical_json(c)))
    return c


def verify_card(card, ed_pub: bytes, x_pub: bytes | None = None) -> bool:
    """The signature by `ed_pub`; with `x_pub` also `card["id"] == agent_id(x_pub, ed_pub)`."""
    if not isinstance(card, dict) or not isinstance(card.get("sig"), str):
        return False
    if x_pub is not None and card.get("id") != agent_id(x_pub, ed_pub):
        return False
    try:
        sig = wire.unb64u(card["sig"])
    except ValueError:
        return False
    return _ed_verify(ed_pub, sig, canonical_json({k: v for k, v in card.items() if k != "sig"}))


# ---------------------------------------------------------------- §17.2 / §17.3 auth and certificate
def mailbox_auth(keys: PeerKeys, mbox: str, n: str, cert: str) -> str:
    """The auth text answering a relay challenge. Signs only an `n` of the relay's exact shape."""
    if not isinstance(n, str) or not _NONCE.fullmatch(n):
        raise ValueError("bad challenge")
    sig = keys.ed.sign(f"agentj-mbox-auth-v1\n{mbox}\n{n}".encode())
    return json.dumps({"t": "auth", "x": wire.b64u(keys.x_pub), "pk": wire.b64u(keys.ed_pub), "sig": wire.b64u(sig),
                       "cert": cert}, separators=(",", ":"))


def self_signed_cert(seed: bytes, mbox: str, host: str = "test", days: float = 7, now: float | None = None) -> str:
    """A §17.3 certificate signed with a raw 32-byte seed — tests and a loopback relay (`AGENTJ_TEST_PEER_CERT_KEY`)."""
    p = json.dumps({"v": 1, "mbox": mbox, "host": host, "exp": int((now or time.time()) + days * 86400)},
                   separators=(",", ":")).encode()
    sig = Ed25519PrivateKey.from_private_bytes(seed).sign(b"agentj-peer-cert-v1\n" + p)
    return wire.b64u(p) + "." + wire.b64u(sig)


def verify_cert(cert, pubs: list[bytes], mbox: str, now: float | None = None) -> bool:
    """What the relay checks (§17.2): a signature by one of `pubs`, `exp` in the future, its `mbox` = this mailbox."""
    try:
        a, b = cert.split(".")
        p, sig = wire.unb64u(a), wire.unb64u(b)
        body = json.loads(p)
    except Exception:
        return False
    if not any(_ed_verify(k, sig, b"agentj-peer-cert-v1\n" + p) for k in pubs):
        return False
    return isinstance(body, dict) and body.get("mbox") == mbox and isinstance(body.get("exp"), int) \
        and body["exp"] > (now or time.time())


def kk_prologue(mbox_a: str, mbox_b: str) -> bytes:
    lo, hi = sorted((mbox_a, mbox_b))
    return f"agentj/v1/peer-kk\n{lo}\n{hi}".encode()


XX_PROLOGUE = b"agentj/v1/peer-xx"

# ---------------------------------------------------------------- §17.2 frames
F_DATA, F_NOT_DELIVERED = 0x21, 0x22
K_XX1, K_XX2, K_XX3, K_KK1, K_KK2, K_DATA = 0x31, 0x32, 0x33, 0x34, 0x35, 0x36
MAX_PAYLOAD = 65_536


def _pad(obj: dict, limit: int = wire.MAX_JSON_P33) -> bytes:
    """§3 padding for any JSON object (handshake payloads carry no `t`)."""
    j = wire.dumps(obj)
    if len(j) > limit:
        raise ValueError("message too large")
    total = -(-(len(j) + 2) // wire.PAD) * wire.PAD
    return (len(j).to_bytes(2, "big") + j).ljust(total, b"\x00")


def _unpad(pt: bytes, limit: int = wire.MAX_JSON_P33) -> dict:
    if len(pt) < 2 or len(pt) % wire.PAD:
        raise ValueError("bad padding")
    n = int.from_bytes(pt[:2], "big")
    if n > limit or n > len(pt) - 2 or any(pt[2 + n:]):
        raise ValueError("bad padding")
    obj = json.loads(pt[2:2 + n].decode("utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("bad message")
    return obj


def _texts_ok(obj: dict) -> bool:
    """§17.4: texts ≤ 20 000 UTF-16 units, well-formed (larger = the session is closed, never truncated)."""
    for k in ("text", "note", "ctx"):
        if k in obj and wire.text_problem(obj[k]) is not None:
            return False
    return True


class _Sess:
    __slots__ = ("kind", "init", "mbox", "sid", "hs", "peer", "x", "ed", "send", "recv", "live", "t", "row", "pending")

    def __init__(self, kind: str, init: bool, mbox: str, sid: bytes, hs: Handshake, t: float):
        self.kind, self.init, self.mbox, self.sid, self.hs, self.t = kind, init, mbox, sid, hs, t
        self.peer = self.x = self.ed = self.send = self.recv = self.row = None
        self.live = False
        self.pending = False   # a KK session with a host we sent a request to (not yet a friend): only `facc` is accepted


_SCHEMA = """CREATE TABLE IF NOT EXISTS outbox(
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, peer TEXT NOT NULL, mbox TEXT NOT NULL, mid TEXT, obj TEXT NOT NULL,
  ack INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'queued', created REAL NOT NULL, expires REAL NOT NULL,
  next_at REAL NOT NULL DEFAULT 0, tries INTEGER NOT NULL DEFAULT 0, sid TEXT, msg3_at REAL, x TEXT, ed TEXT)"""


class PeerNet:
    """The mailbox connection + handshakes + outbox of one host. All methods run on the event loop that runs `run()`."""

    # tunables (tests shrink them; values from §17.4 / §17.5)
    REQ_BACKOFF = (5.0, 1800.0)       # XX msg1 retries of a request
    MSG_BACKOFF = (5.0, 600.0)        # unacknowledged pmsg / facc resent on a live session
    # KK handshake retries towards a friend with queued mail. Capped low on purpose: a friend coming back online sends us
    # nothing by itself, so this cap is the longest wait between "back online" and delivery (ADR §9.7: ≤ 60 s). Cost: one
    # KK msg1 + the relay's 0x22 per 30 s per unreachable friend with something queued (≤ 24 h).
    KK_BACKOFF = (5.0, 30.0)
    REQ_TTL = 7 * 86400.0
    MSG_TTL = 86400.0
    SENT_GRACE = 5.0                  # msg3 drew no 0x22 within this → the request is "sent" (stop resending msg1)
    IDLE = 600.0                      # a live session idle this long ends
    HS_TIMEOUT = 30.0                 # a handshake not finished in this long is dropped
    RECONNECT = (1.0, 60.0)
    TICK = 1.0                        # outbox pump period (real seconds)
    MAX_SESSIONS = 256
    MAX_XX_RESP = 32                  # responder XX handshakes in flight (requests from strangers)
    EXPIRED_KEEP = 7 * 86400.0        # expired rows stay visible this long

    def __init__(self, st, keys: PeerKeys, relay_url: str, cert_fn, cb, clock=time.time):
        self.st, self.keys, self.cb, self.clock, self.cert_fn = st, keys, cb, clock, cert_fn
        self.url = f"{relay_url.rstrip('/')}/v1/mbox/{keys.mbox}"
        self.ws = None
        self.up = False
        self._stopped = False
        self._stopping = asyncio.Event()
        self._wake: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.sessions: dict[tuple[str, bytes], _Sess] = {}
        self.kk: dict[str, _Sess] = {}                 # peer mbox → the KK session used for sending
        self.hs_next: dict[str, float] = {}            # peer mbox → earliest next KK attempt
        self.hs_fail: dict[str, int] = {}
        self.paused: dict[str, float] = {}             # peer id → until (clock)
        self.slow: set[str] = set()                    # peers that sent `limited`: one unacknowledged message at a time
        d = pathlib.Path(st.root) / "peer"
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)
        path = d / "outbox.sqlite"
        if not path.exists():
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))
        os.chmod(path, 0o600)
        self.db = sqlite3.connect(str(path), isolation_level=None)
        self.db.row_factory = sqlite3.Row
        # The pump runs on serve's event loop: with the default rollback journal every autocommit write costs several
        # fsyncs (0.1–0.4 s per pump step measured on a disk-backed state folder), which stalls the loop and, with short
        # timers, starves the backlog. WAL + synchronous=NORMAL: no fsync per commit; an agentj crash loses nothing, only an
        # OS crash / power loss can roll back the last few commits. The -wal / -shm files take the database file's 0600 mode.
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute(_SCHEMA)

    # ------------------------------------------------------------ small helpers
    def _log(self, ev: str, **kw) -> None:
        with contextlib.suppress(Exception):
            self.st.log(ev, **kw)

    def _status(self, ev: str, **kw) -> None:
        try:
            self.cb.on_status(ev, **kw)
        except Exception as e:  # a callback bug never takes the mailbox down
            self._log("peer_cb_error", where="on_status", err=type(e).__name__)

    def _kick(self) -> None:
        if self._wake is None or self._loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            self._wake.set()
        else:
            self._loop.call_soon_threadsafe(self._wake.set)

    @staticmethod
    def _backoff(rng: tuple[float, float], tries: int) -> float:
        lo, hi = rng
        return min(lo * (2 ** min(tries, 30)), hi)

    def _peer_of_mbox(self, mbox: str):
        """(peer id, x_pub, ed_pub, pending) for a mailbox we may run KK with: a friend, else a host we sent a request to."""
        pid = self.cb.friend_by_mbox(mbox)
        if pid:
            k = self.cb.friend_keys(pid)
            if k:
                return pid, k[0], k[1], False
        r = self.db.execute("SELECT peer,x,ed FROM outbox WHERE kind='req' AND mbox=? AND state IN ('queued','sent') "
                            "AND x IS NOT NULL ORDER BY id DESC LIMIT 1", (mbox,)).fetchone()
        if r:
            return r["peer"], wire.unb64u(r["x"]), wire.unb64u(r["ed"]), True
        return None

    def _keys_of_peer(self, peer: str):
        k = self.cb.friend_keys(peer)
        if k:
            return k[0], k[1], False
        r = self.db.execute("SELECT x,ed FROM outbox WHERE kind='req' AND peer=? AND state IN ('queued','sent') "
                            "AND x IS NOT NULL ORDER BY id DESC LIMIT 1", (peer,)).fetchone()
        if r:
            return wire.unb64u(r["x"]), wire.unb64u(r["ed"]), True
        return None

    def _forget(self, s: _Sess) -> None:
        self.sessions.pop((s.mbox, s.sid), None)
        if self.kk.get(s.mbox) is s:
            del self.kk[s.mbox]

    def _drop_all_sessions(self) -> None:
        self.sessions.clear()
        self.kk.clear()

    async def _tx(self, to_mbox: str, kind: int, sid: bytes, body: bytes) -> bool:
        if self._stopped or not self.up or self.ws is None:
            return False
        frame = bytes([F_DATA]) + wire.unb64u(to_mbox) + bytes([kind]) + sid + body
        if len(frame) - 17 > MAX_PAYLOAD:
            raise ValueError("payload too large")
        try:
            await self.ws.send(frame)
            return True
        except Exception as e:  # the reader notices the dead socket and reconnects
            self._log("peer_tx_failed", err=type(e).__name__)
            return False

    # ------------------------------------------------------------ public API
    async def add(self, target_id: str, card: dict, note: str) -> str:
        """A side: queue a friend request (idempotent per target while pending) and start XX. Returns its `rid`."""
        tid = parse_id(target_id)
        if tid is None:
            raise ValueError("bad agent id")
        if tid == self.keys.id:
            raise ValueError("that is my own id")
        if not isinstance(note, str) or wire.text_problem(note, 280) is not None:
            raise ValueError("bad note")
        now = self.clock()
        r = self.db.execute("SELECT id,mid FROM outbox WHERE kind='req' AND peer=? AND state IN ('queued','sent') "
                            "ORDER BY id DESC LIMIT 1", (tid,)).fetchone()
        obj = {"card": sign_card(self.keys, card), "note": note}
        if r:
            self.db.execute("UPDATE outbox SET obj=?, state='queued', next_at=0, msg3_at=NULL WHERE id=?",
                            (json.dumps(obj), r["id"]))
            rid = r["mid"]
        else:
            rid = secrets.token_hex(8)
            self.db.execute("INSERT INTO outbox(kind,peer,mbox,mid,obj,ack,created,expires,next_at) VALUES "
                            "('req',?,?,?,?,1,?,?,0)", (tid, mbox_of(tid), rid, json.dumps(obj), now, now + self.REQ_TTL))
        self._log("peer_req_queued", rid=rid)
        self._kick()
        return rid

    def send(self, peer_id: str, obj: dict) -> None:
        """Queue an app message for a friend (§17.5). `pmsg` with `mid` and `facc` (rid = mid) stay until the friend's `pack`;
        everything else leaves the outbox once sent. Raises ValueError for a malformed / over-long message (never truncated)."""
        pid = parse_id(peer_id)
        if pid is None:
            raise ValueError("bad agent id")
        if not isinstance(obj, dict) or not isinstance(obj.get("t"), str):
            raise ValueError("bad message")
        if not _texts_ok(obj):
            raise ValueError("text too long or malformed")
        t = obj["t"]
        if t in ("facc", "card") and isinstance(obj.get("card"), dict) \
                and not verify_card(obj["card"], self.keys.ed_pub, self.keys.x_pub):
            obj = {**obj, "card": sign_card(self.keys, obj["card"])}
        _pad(obj)  # size check
        mid = obj.get("mid") if t == "pmsg" else obj.get("rid") if t == "facc" else obj.get("mid")
        ack = int((t == "pmsg" and isinstance(mid, str)) or (t == "facc" and isinstance(mid, str)))
        now = self.clock()
        if ack and self.db.execute("SELECT 1 FROM outbox WHERE kind='msg' AND peer=? AND mid=? AND ack=1 AND "
                                   "state='queued'", (pid, mid)).fetchone():
            return  # the same idempotency key is already waiting
        self.db.execute("INSERT INTO outbox(kind,peer,mbox,mid,obj,ack,created,expires,next_at) VALUES "
                        "('msg',?,?,?,?,?,?,?,0)", (pid, mbox_of(pid), mid if isinstance(mid, str) else None,
                                                    json.dumps(obj, ensure_ascii=False), ack, now, now + self.MSG_TTL))
        self._kick()

    def pause(self, peer_id: str, seconds: float) -> None:
        pid = parse_id(peer_id) or peer_id
        try:
            sec = max(0.0, min(float(seconds), 86400.0))
        except (TypeError, ValueError):
            sec = 60.0
        self.paused[pid] = self.clock() + sec
        self.slow.add(pid)

    def drop_peer(self, peer_id: str) -> None:
        pid = parse_id(peer_id) or peer_id
        self.db.execute("DELETE FROM outbox WHERE peer=?", (pid,))
        mb = mbox_of(pid) if parse_id(pid) else None
        for s in list(self.sessions.values()):
            if s.mbox == mb or s.peer == pid:
                self._forget(s)
        self.paused.pop(pid, None)
        self.slow.discard(pid)
        if mb:
            self.hs_next.pop(mb, None)
            self.hs_fail.pop(mb, None)

    def stopped(self, on: bool) -> None:
        self._stopped = bool(on)
        if not on:
            self._kick()

    def outbox_rows(self) -> list[dict]:
        """What /friends shows. A request is only ever `pending` or `expired` (§17.5: nothing more is knowable)."""
        out = []
        for r in self.db.execute("SELECT * FROM outbox ORDER BY id"):
            row = {"kind": r["kind"], "peer": r["peer"], "state": "expired" if r["state"] == "expired" else "pending",
                   "created": r["created"], "expires": r["expires"]}
            if r["kind"] == "req":
                row["rid"] = r["mid"]
            else:
                row["t"] = json.loads(r["obj"]).get("t")
                if r["mid"]:
                    row["mid"] = r["mid"]
            out.append(row)
        return out

    async def stop(self) -> None:
        self._stopping.set()
        if self.ws is not None:
            with contextlib.suppress(Exception):
                await self.ws.close()

    # ------------------------------------------------------------ connection
    async def run(self) -> None:
        from websockets.asyncio.client import connect
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        pump = asyncio.create_task(self._pump_loop())
        backoff = self.RECONNECT[0]
        try:
            while not self._stopping.is_set():
                cert = None
                try:
                    cert = await self.cert_fn()
                except Exception as e:
                    self._log("peer_cert_failed", err=type(e).__name__)
                if cert:
                    try:
                        async with connect(self.url, max_size=2**17, ping_interval=20, ping_timeout=20,
                                           open_timeout=15) as ws:
                            await self._auth(ws, cert)
                            self._on_up(ws)
                            backoff = self.RECONNECT[0]
                            async for m in ws:
                                if isinstance(m, bytes):
                                    await self._frame(m)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:  # network, auth refusal (4003), replaced (4001) → back off, retry
                        self._log("peer_mbox_down", reason=type(e).__name__)
                    finally:
                        self._on_down()
                if self._stopping.is_set():
                    break
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stopping.wait(), backoff)
                backoff = min(backoff * 2, self.RECONNECT[1])
        finally:
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await pump
            self._on_down()

    async def _auth(self, ws, cert: str) -> None:
        msg = json.loads(await asyncio.wait_for(ws.recv(), 10))
        if not isinstance(msg, dict) or msg.get("t") != "challenge":
            raise ValueError("bad challenge")
        await ws.send(mailbox_auth(self.keys, self.keys.mbox, msg.get("n"), cert))
        ok = json.loads(await asyncio.wait_for(ws.recv(), 10))
        if not isinstance(ok, dict) or ok.get("t") != "ok":
            raise ValueError("relay refused auth")

    def _on_up(self, ws) -> None:
        self.ws, self.up = ws, True
        self._drop_all_sessions()   # §17.4: a mailbox reconnect ends every session
        self.hs_next.clear()
        self.hs_fail.clear()
        # retry everything at once (requests whose msg3 is still in its grace window are resent too)
        self.db.execute("UPDATE outbox SET next_at=0, msg3_at=NULL, sid=NULL WHERE state='queued'")
        self._log("peer_mbox_up", mbox=self.keys.mbox)
        self._status("connected")
        self._kick()

    def _on_down(self) -> None:
        was = self.up
        self.ws, self.up = None, False
        self._drop_all_sessions()
        if was:
            self.db.execute("UPDATE outbox SET msg3_at=NULL WHERE state='queued' AND msg3_at IS NOT NULL")
            self._status("disconnected")

    # ------------------------------------------------------------ inbound
    async def _frame(self, f: bytes) -> None:
        if len(f) < 17:
            return
        op, mb = f[0], wire.b64u(f[1:17])
        if op == F_NOT_DELIVERED:
            return self._not_delivered(mb)
        if op != F_DATA or len(f) < 17 + 9 or mb == self.keys.mbox:
            return
        kind, sid, body = f[17], bytes(f[18:26]), bytes(f[26:])
        self._answered(mb)
        try:
            if kind == K_XX1:
                await self._xx1(mb, sid, body)
            elif kind == K_XX2:
                await self._xx2(mb, sid, body)
            elif kind == K_XX3:
                self._xx3(mb, sid, body)
            elif kind == K_KK1:
                await self._kk1(mb, sid, body)
            elif kind == K_KK2:
                self._kk2(mb, sid, body)
            elif kind == K_DATA:
                await self._data(mb, sid, body)
        except Exception as e:  # anything a peer sends costs at most its own session
            s = self.sessions.get((mb, sid))
            if s:
                self._forget(s)
            self._log("peer_frame_dropped", kind=kind, err=type(e).__name__)

    def _answered(self, mb: str) -> None:
        """The target's mailbox answers again: retry what waits for it now (§17.5)."""
        if self.hs_next.get(mb):
            self.hs_next[mb] = 0
        cur = self.db.execute("UPDATE outbox SET next_at=0 WHERE mbox=? AND state='queued' AND next_at>? AND "
                              "(kind='msg' AND sid IS NULL)", (mb, self.clock()))
        if cur.rowcount:
            self._kick()

    def _not_delivered(self, mb: str) -> None:
        for s in list(self.sessions.values()):
            if s.mbox == mb and (s.init or s.live):
                self._forget(s)
        self.db.execute("UPDATE outbox SET msg3_at=NULL WHERE kind='req' AND mbox=? AND state='queued'", (mb,))
        self.db.execute("UPDATE outbox SET sid=NULL WHERE kind='msg' AND mbox=? AND state='queued'", (mb,))
        self._log("peer_not_delivered")

    def _new_sess(self, kind: str, init: bool, mb: str, sid: bytes, hs: Handshake) -> _Sess | None:
        if len(self.sessions) >= self.MAX_SESSIONS:
            return None
        s = self.sessions[(mb, sid)] = _Sess(kind, init, mb, sid, hs, self.clock())
        return s

    # XX, B side
    async def _xx1(self, mb: str, sid: bytes, body: bytes) -> None:
        if self._stopped or not self.cb.accepting():
            return  # friends off / stopped: say nothing at all
        if (mb, sid) in self.sessions:
            return
        if sum(1 for s in self.sessions.values() if s.kind == "xx" and not s.init) >= self.MAX_XX_RESP:
            return
        hs = Handshake(XX, False, self.keys.x, prologue=XX_PROLOGUE)
        hs.read_message(body)  # msg1 payload ignored (empty)
        s = self._new_sess("xx", False, mb, sid, hs)
        if s is None:
            return
        msg2 = hs.write_message(lambda h: _pad({"pk": wire.b64u(self.keys.ed_pub),
                                                "sig": wire.b64u(self.keys.ed.sign(b"agentj-peer-xx-v1\n" + h))}))
        await self._tx(mb, K_XX2, sid, msg2)

    def _xx3(self, mb: str, sid: bytes, body: bytes) -> None:
        s = self.sessions.get((mb, sid))
        if not s or s.kind != "xx" or s.init:
            return
        self._forget(s)  # one shot either way
        p = _unpad(s.hs.read_message(body), 8192)
        h, rs = s.hs.h_before_payload, s.hs.rs
        pk, sig = wire.unb64u(p["pk"]), wire.unb64u(p["sig"])
        if len(pk) != 32 or not _ed_verify(pk, sig, b"agentj-peer-xx-v1\n" + h):
            return self._log("peer_xx_bad_sig")
        aid = agent_id(rs, pk)
        if mbox_of(aid) != mb:   # the relay-filled `from` must be the mailbox of the key's own ID
            return self._log("peer_xx_bad_from")
        fr = p.get("freq")
        if not (isinstance(fr, dict) and isinstance(fr.get("rid"), str) and _HEX16.fullmatch(fr["rid"])
                and isinstance(fr.get("note"), str) and wire.text_problem(fr["note"], 280) is None
                and isinstance(fr.get("ts"), int) and verify_card(fr.get("card"), pk, rs)):
            return self._log("peer_xx_bad_freq")
        # A request from this mailbox: that side holds no KK session with us (e.g. it dropped everything of us on our `bye`),
        # so any we still have is dead — forget it, or our facc / next message goes over it first and waits MSG_BACKOFF.
        for o in [x for x in self.sessions.values() if x.kind == "kk" and x.mbox == mb]:
            self._forget(o)
        self.db.execute("UPDATE outbox SET sid=NULL, next_at=0 WHERE kind='msg' AND mbox=? AND state='queued' "
                        "AND sid IS NOT NULL", (mb,))
        if not self.cb.accepting():
            return
        try:
            self.cb.on_request(aid, rs, pk, fr, mb)
        except Exception as e:
            self._log("peer_cb_error", where="on_request", err=type(e).__name__)

    # XX, A side
    async def _start_xx(self, row) -> None:
        sid = secrets.token_bytes(8)
        hs = Handshake(XX, True, self.keys.x, prologue=XX_PROLOGUE)
        s = self._new_sess("xx", True, row["mbox"], sid, hs)
        if s is None:
            return
        s.row, s.peer = row["id"], row["peer"]
        await self._tx(row["mbox"], K_XX1, sid, hs.write_message(b""))

    async def _xx2(self, mb: str, sid: bytes, body: bytes) -> None:
        s = self.sessions.get((mb, sid))
        if not s or s.kind != "xx" or not s.init:
            return
        self._forget(s)
        p = _unpad(s.hs.read_message(body), 4096)
        pk, sig = wire.unb64u(p["pk"]), wire.unb64u(p["sig"])
        if len(pk) != 32 or agent_id(s.hs.rs, pk) != s.peer \
                or not _ed_verify(pk, sig, b"agentj-peer-xx-v1\n" + s.hs.h_before_payload):
            return self._log("peer_xx_bad_responder")   # not the ID we were given: a MITM or a wrong mailbox
        row = self.db.execute("SELECT * FROM outbox WHERE id=? AND state='queued'", (s.row,)).fetchone()
        if not row:
            return
        o = json.loads(row["obj"])
        freq = {"rid": row["mid"], "card": o["card"], "note": o["note"], "ts": int(self.clock() * 1000)}
        msg3 = s.hs.write_message(lambda h: _pad({"pk": wire.b64u(self.keys.ed_pub),
                                                  "sig": wire.b64u(self.keys.ed.sign(b"agentj-peer-xx-v1\n" + h)),
                                                  "freq": freq}, 8192))
        if await self._tx(mb, K_XX3, sid, msg3):
            self.db.execute("UPDATE outbox SET x=?, ed=?, msg3_at=? WHERE id=?",
                            (wire.b64u(s.hs.rs), wire.b64u(pk), self.clock(), row["id"]))

    # KK
    async def _start_kk(self, peer: str, mb: str, x: bytes, ed: bytes, pending: bool) -> None:
        sid = secrets.token_bytes(8)
        hs = Handshake(KK, True, self.keys.x, prologue=kk_prologue(self.keys.mbox, mb), rs=x)
        s = self._new_sess("kk", True, mb, sid, hs)
        if s is None:
            return
        s.peer, s.x, s.ed, s.pending = peer, x, ed, pending
        self.kk[mb] = s
        n = self.hs_fail.get(mb, 0)
        self.hs_fail[mb] = n + 1
        self.hs_next[mb] = self.clock() + self._backoff(self.KK_BACKOFF, n)
        await self._tx(mb, K_KK1, sid, hs.write_message(b""))

    async def _kk1(self, mb: str, sid: bytes, body: bytes) -> None:
        if self._stopped or (mb, sid) in self.sessions:
            return
        who = self._peer_of_mbox(mb)
        if not who:
            return  # not a friend: drop, answer nothing
        pid, x, ed, pending = who
        hs = Handshake(KK, False, self.keys.x, prologue=kk_prologue(self.keys.mbox, mb), rs=x)
        try:
            hs.read_message(body)
        except NoiseError:
            return self._log("peer_kk_undecryptable")
        mine = self.kk.get(mb)
        if mine and mine.init and not mine.live and self.keys.mbox < mb:
            return  # simultaneous start: the smaller mailbox keeps its own
        if mine:
            self._forget(mine)
        s = self._new_sess("kk", False, mb, sid, hs)
        if s is None:
            return
        s.peer, s.x, s.ed, s.pending = pid, x, ed, pending
        msg2 = hs.write_message(b"")
        s.send, s.recv, _ = hs.split()
        s.live = True
        self.kk[mb] = s
        self.hs_fail[mb], self.hs_next[mb] = 0, 0
        self.db.execute("UPDATE outbox SET sid=NULL, next_at=0 WHERE kind='msg' AND mbox=? AND state='queued'", (mb,))
        await self._tx(mb, K_KK2, sid, msg2)
        self._kick()

    def _kk2(self, mb: str, sid: bytes, body: bytes) -> None:
        s = self.sessions.get((mb, sid))
        if not s or s.kind != "kk" or not s.init or s.live:
            return
        s.hs.read_message(body)
        s.send, s.recv, _ = s.hs.split()
        s.live, s.t = True, self.clock()
        self.hs_fail[mb], self.hs_next[mb] = 0, 0
        self._kick()

    async def _data(self, mb: str, sid: bytes, body: bytes) -> None:
        s = self.sessions.get((mb, sid))
        if not s or not s.live:
            return
        s.t = self.clock()
        obj = _unpad(s.recv.decrypt(b"", body))
        if not isinstance(obj.get("t"), str) or not _texts_ok(obj):
            self._forget(s)
            return self._log("peer_session_closed", why="bad_message")
        obj = {k: v for k, v in obj.items() if not k.startswith("_")}
        t = obj["t"]
        if s.pending:
            if t != "facc":
                return  # not a friend yet: only the acceptance of our request
            if self.cb.friend_by_mbox(mb):
                s.pending = False
        if t == "facc":
            row = self.db.execute("SELECT * FROM outbox WHERE kind='req' AND peer=? AND mid=? AND state IN "
                                  "('queued','sent')", (s.peer, obj.get("rid"))).fetchone()
            if row is None and s.pending:
                return
            ed = s.ed
            if ed is None or not verify_card(obj.get("card"), ed, s.x):
                return self._log("peer_facc_bad_card")
            obj["_x"], obj["_pk"] = wire.b64u(s.x), wire.b64u(ed)
            if row is not None:
                self.db.execute("DELETE FROM outbox WHERE id=?", (row["id"],))
            if self._app(s.peer, obj):
                self.send(s.peer, {"t": "pack", "mid": obj["rid"], "s": "got"})  # transport receipt: B dequeues its facc
            if s.pending and self.cb.friend_by_mbox(mb):
                s.pending = False
            return
        if t == "pack" and isinstance(obj.get("mid"), str):
            cur = self.db.execute("DELETE FROM outbox WHERE kind='msg' AND peer=? AND mid=? AND ack=1",
                                  (s.peer, obj["mid"]))
            if cur.rowcount:
                self._status("delivered", peer=s.peer, mid=obj["mid"], s=obj.get("s"))
                if s.peer in self.slow and not self.db.execute(
                        "SELECT 1 FROM outbox WHERE kind='msg' AND peer=? AND state='queued' AND ack=1 LIMIT 1",
                        (s.peer,)).fetchone():
                    self.slow.discard(s.peer)        # the backlog after a `limited` is through: normal sending again
        elif t == "limited":
            self.pause(s.peer, obj.get("retry", 60))
        self._app(s.peer, obj)

    def _app(self, peer: str, obj: dict) -> bool:
        try:
            self.cb.on_app(peer, obj)
            return True
        except Exception as e:
            self._log("peer_cb_error", where="on_app", err=type(e).__name__)
            return False

    # ------------------------------------------------------------ outbox pump
    async def _pump_loop(self) -> None:
        while True:
            try:
                await self._pump()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # one bad row never stops the pump
                self._log("peer_pump_error", err=type(e).__name__)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self.TICK)
            self._wake.clear()

    async def _pump(self) -> None:
        now = self.clock()
        # expiry (application-visible)
        for r in self.db.execute("SELECT * FROM outbox WHERE state IN ('queued','sent') AND expires<=?", (now,)).fetchall():
            self.db.execute("UPDATE outbox SET state='expired' WHERE id=?", (r["id"],))
            self._status("expired", peer=r["peer"], kind=r["kind"], mid=r["mid"])
        self.db.execute("DELETE FROM outbox WHERE state='expired' AND expires<=?", (now - self.EXPIRED_KEEP,))
        # sessions: handshakes that never finished, idle live sessions
        for s in list(self.sessions.values()):
            if (not s.live and now - s.t > self.HS_TIMEOUT) or (s.live and now - s.t > self.IDLE):
                self._forget(s)
        # a request whose msg3 drew no 0x22 within the grace window is "sent": stop resending msg1, wait for facc
        for r in self.db.execute("SELECT id,mid FROM outbox WHERE kind='req' AND state='queued' AND msg3_at IS NOT NULL "
                                 "AND msg3_at<=?", (now - self.SENT_GRACE,)).fetchall():
            self.db.execute("UPDATE outbox SET state='sent' WHERE id=?", (r["id"],))
            self._log("peer_req_sent", rid=r["mid"])   # local log only: never a cb / outbox_rows difference
        if self._stopped or not self.up:
            return
        # requests: XX msg1 with backoff
        for r in self.db.execute("SELECT * FROM outbox WHERE kind='req' AND state='queued' AND msg3_at IS NULL AND "
                                 "next_at<=?", (now,)).fetchall():
            if any(s.kind == "xx" and s.init and s.row == r["id"] for s in self.sessions.values()):
                continue
            self.db.execute("UPDATE outbox SET tries=tries+1, next_at=? WHERE id=?",
                            (now + self._backoff(self.REQ_BACKOFF, r["tries"]), r["id"]))
            await self._start_xx(r)
        # messages, per friend
        rows = self.db.execute("SELECT * FROM outbox WHERE kind='msg' AND state='queued' AND next_at<=? ORDER BY id",
                               (now,)).fetchall()
        by_peer: dict[str, list] = {}
        for r in rows:
            if self.paused.get(r["peer"], 0) > now:
                continue
            by_peer.setdefault(r["peer"], []).append(r)
        for peer, rs in by_peer.items():
            mb = rs[0]["mbox"]
            s = self.kk.get(mb)
            if s and s.live and any(r["sid"] == s.sid.hex() for r in rs):
                self._forget(s)  # sent on this session and still no pack: the other side probably lost it
                s = None
            if s and s.live:
                if peer in self.slow:
                    rs = self._slow_rows(peer, s, rs)
                for r in rs:
                    if not await self._send_row(s, r, now):
                        break
            elif s is None and self.hs_next.get(mb, 0) <= now:
                k = self._keys_of_peer(peer)
                if k:
                    await self._start_kk(peer, mb, k[0], k[1], k[2])

    def _slow_rows(self, peer: str, s: _Sess, rs: list) -> list:
        """After a friend's `limited` (§17.5): once the pause ends the backlog is not sent in one burst (the friend would drop
        all but the first without another receipt in that window) but one unacknowledged message at a time — the next one only
        after the previous one's `pack`. Receipts and other unacknowledged-free rows go as usual. Back to normal once nothing
        acknowledged is queued for that friend (checked here and on every `pack`)."""
        if not self.db.execute("SELECT 1 FROM outbox WHERE kind='msg' AND peer=? AND state='queued' AND ack=1 LIMIT 1",
                               (peer,)).fetchone():
            self.slow.discard(peer)
            return rs
        inflight = self.db.execute("SELECT 1 FROM outbox WHERE kind='msg' AND peer=? AND state='queued' AND ack=1 AND sid=? "
                                   "LIMIT 1", (peer, s.sid.hex())).fetchone() is not None
        out = []
        for r in rs:
            if not r["ack"]:
                out.append(r)
            elif not inflight:
                out.append(r)
                inflight = True
        return out

    async def _send_row(self, s: _Sess, r, now: float) -> bool:
        obj = json.loads(r["obj"])
        if s.pending and obj.get("t") != "pack":
            return False
        ct = s.send.encrypt(b"", _pad(obj))
        if not await self._tx(s.mbox, K_DATA, s.sid, ct):
            return False
        s.t = now
        if r["ack"]:
            self.db.execute("UPDATE outbox SET tries=tries+1, next_at=?, sid=? WHERE id=?",
                            (now + self._backoff(self.MSG_BACKOFF, r["tries"]), s.sid.hex(), r["id"]))
        else:
            self.db.execute("DELETE FROM outbox WHERE id=?", (r["id"],))
        return True
