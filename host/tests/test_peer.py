"""Agent friends transport (PROTOCOL §17.1, §17.2, §17.4, §17.5): Agent ID, cards, mailbox auth, and two real `PeerNet`s talking
through a Python stand-in for the relay mailbox (challenge / auth + certificate check / 0x21 forwarding with a relay-filled
`from` / 0x22) — adding a friend, messages and receipts, offline queueing, expiry, strangers, stop-everything, and the
five refusals that must look the same to the requester.
Run (in host/): .venv/bin/python -m unittest discover -s tests -p test_peer.py
"""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import asyncio
import json
import os
import pathlib
import re
import stat
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey  # noqa: E402
from websockets.asyncio.server import serve as ws_serve  # noqa: E402

from agentj import peer, wire  # noqa: E402
from agentj.noise import XX, Handshake, Keypair  # noqa: E402
from agentj.peer import PeerKeys, PeerNet, agent_id, mbox_of, parse_id  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
IDV = json.loads((ROOT / "protocol/vectors/peer-id.json").read_text())
H = bytes.fromhex


# ---------------------------------------------------------------- pure functions
class Identity(unittest.TestCase):
    def test_vectors(self):
        for v in IDV["vectors"]:
            k = PeerKeys(Keypair.from_private(H(v["x25519_priv"])), Ed25519PrivateKey.from_private_bytes(H(v["ed25519_seed"])))
            self.assertEqual(k.x_pub.hex(), v["x25519_pub"])
            self.assertEqual(k.ed_pub.hex(), v["ed25519_pub"])
            self.assertEqual(k.id, v["id"])
            self.assertEqual(agent_id(k.x_pub, k.ed_pub), v["id"])
            self.assertEqual(peer.id_raw(v["id"]).hex(), v["id_raw"])
            self.assertEqual(int(v["id_raw"], 16) >> 5, int(v["id75_hex"], 16))
            self.assertEqual(int(v["h"][:20], 16) >> 5, int(v["id75_hex"], 16))  # the first 75 bits of h
            self.assertEqual(mbox_of(v["id"]), v["mbox"])
            self.assertEqual(k.mbox, v["mbox"])
            self.assertRegex(v["id"], r"^AJ-[0-9A-HJKMNP-TV-Z]{4}(-[0-9A-HJKMNP-TV-Z]{4}){3}$")
        for c in IDV["parse"]:
            with self.subTest(c=c["in"]):
                self.assertEqual(parse_id(c["in"]), c["out"])

    def test_parse_rules(self):
        k = PeerKeys(Keypair.generate(), Ed25519PrivateKey.generate())
        body = k.id[3:].replace("-", "")
        self.assertEqual(parse_id(k.id.lower()), k.id)
        self.assertEqual(parse_id(" ".join(body)), k.id)
        conf = body.replace("1", "i").replace("0", "o")
        self.assertEqual(parse_id(conf), k.id)  # I / L → 1, O → 0, any case
        # §17.1's check = Σ (i+1)·v_i mod 31 (31 prime, weights 1..15): every single substitution is caught except a swap of the
        # values 0 ↔ Z (delta ±31, two characters nobody confuses); every adjacent transposition of different values is caught
        for i in range(16):
            for c in peer.ALPHABET:
                if c == body[i]:
                    continue
                d = peer.ALPHABET.index(c) - peer.ALPHABET.index(body[i])
                got = parse_id(body[:i] + c + body[i + 1:])
                if i < 15 and abs(d) == 31:
                    self.assertIsNotNone(got)
                else:
                    self.assertIsNone(got, (i, c))
        for i in range(14):
            if body[i] != body[i + 1] and abs(peer.ALPHABET.index(body[i]) - peer.ALPHABET.index(body[i + 1])) != 31:
                self.assertIsNone(parse_id(body[:i] + body[i + 1] + body[i] + body[i + 2:]))
        self.assertIsNone(parse_id("AJ-" + body[:15] + "U"))
        self.assertIsNone(parse_id(None))
        self.assertIsNone(parse_id("x" * 100))
        with self.assertRaises(ValueError):
            peer.id_raw("AJ-0000")

    def test_mbox_shape(self):
        k = PeerKeys(Keypair.generate(), Ed25519PrivateKey.generate())
        self.assertRegex(k.mbox, r"^[A-Za-z0-9_-]{22}$")
        self.assertEqual(len(wire.unb64u(k.mbox)), 16)

    def test_card(self):
        k = PeerKeys(Keypair.generate(), Ed25519PrivateKey.generate())
        other = PeerKeys(Keypair.generate(), Ed25519PrivateKey.generate())
        c = peer.sign_card(k, {"name": "小贾", "owner": "", "intro": "你好", "lang": "zh", "caps": ["chat"]})
        self.assertEqual(c["id"], k.id)
        self.assertTrue(peer.verify_card(c, k.ed_pub, k.x_pub))
        self.assertTrue(peer.verify_card(c, k.ed_pub))
        self.assertFalse(peer.verify_card(c, other.ed_pub))
        self.assertFalse(peer.verify_card({**c, "intro": "改了"}, k.ed_pub))
        self.assertFalse(peer.verify_card(c, k.ed_pub, other.x_pub))  # id does not hash back to these keys
        forged = peer.sign_card(other, {**c, "id": k.id})              # someone else signing a card with my id
        self.assertFalse(peer.verify_card({**forged, "id": k.id}, other.ed_pub, other.x_pub))
        self.assertEqual(peer.canonical_json({"b": 1, "a": "中"}), '{"a":"中","b":1}'.encode())

    def test_auth_and_cert(self):
        k = PeerKeys(Keypair.generate(), Ed25519PrivateKey.generate())
        n = wire.b64u(os.urandom(32))
        seed = os.urandom(32)
        cert = peer.self_signed_cert(seed, k.mbox)
        a = json.loads(peer.mailbox_auth(k, k.mbox, n, cert))
        self.assertEqual(set(a), {"t", "x", "pk", "sig", "cert"})
        Ed25519PublicKey.from_public_bytes(k.ed_pub).verify(wire.unb64u(a["sig"]),
                                                            f"agentj-mbox-auth-v1\n{k.mbox}\n{n}".encode())
        self.assertLessEqual(len(peer.mailbox_auth(k, k.mbox, n, cert)), 2048)
        for bad in ("short", "x" * 44, n[:-1] + "!", None):
            with self.assertRaises(ValueError):
                peer.mailbox_auth(k, k.mbox, bad, cert)
        pub = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw()
        self.assertTrue(peer.verify_cert(cert, [pub], k.mbox))
        self.assertFalse(peer.verify_cert(cert, [pub], "A" * 22))
        self.assertFalse(peer.verify_cert(cert, [os.urandom(32)], k.mbox))
        self.assertFalse(peer.verify_cert(peer.self_signed_cert(seed, k.mbox, days=-1), [pub], k.mbox))
        self.assertFalse(peer.verify_cert("garbage", [pub], k.mbox))

    def test_cert_vector(self):
        """protocol/vectors/peer-cert.json (shared with the Dashboard signer and the relay verifier)."""
        v = json.loads((ROOT / "protocol/vectors/peer-cert.json").read_text())
        seed = H(v["seed_hex"])
        self.assertEqual(peer.self_signed_cert(seed, v["mbox"], v["host"], now=v["now"]), v["cert"])
        pub = wire.unb64u(v["public_key"])
        self.assertTrue(peer.verify_cert(v["cert"], [pub], v["mbox"], now=v["now"]))
        self.assertFalse(peer.verify_cert(v["cert"], [pub], v["mbox"], now=v["exp"]))
        for bad in v["invalid"]:
            self.assertFalse(peer.verify_cert(bad["cert"], [pub], v["mbox"], now=v["now"]), bad["why"])

    def test_keys_files(self):
        with tempfile.TemporaryDirectory() as d:
            st = FakeSt(pathlib.Path(d) / "s")
            self.assertIsNone(PeerKeys.load(st))
            k = PeerKeys.load_or_create(st)
            k2 = PeerKeys.load(st)
            self.assertEqual((k.id, k.x_pub, k.ed_pub), (k2.id, k2.x_pub, k2.ed_pub))
            pd = pathlib.Path(d) / "s" / "peer"
            self.assertEqual(stat.S_IMODE(pd.stat().st_mode), 0o700)
            for f in ("peer_x25519.key", "peer_ed25519.key"):
                self.assertEqual(stat.S_IMODE((pd / f).stat().st_mode), 0o600)
                self.assertEqual(len((pd / f).read_bytes()), 32)


# ---------------------------------------------------------------- the stand-in relay mailbox
_PATH = re.compile(r"/v1/mbox/([A-Za-z0-9_-]{22})")


class FakeMailbox:
    """§17.2, faithfully enough for the host: challenge → auth (mbox derivation, signature, certificate) → ok, else 4003;
    a newer socket replaces the old (4001); 0x21 forwarded with the sender's authenticated mbox as `from`; no such / offline
    mailbox → 0x22 back to the sender. `log` = (from, to, kind) for every frame; kind "nd" = a 0x22 sent to `to`."""

    def __init__(self, cert_pub: bytes):
        self.cert_pub = cert_pub
        self.socks: dict[str, object] = {}
        self.log: list[tuple] = []
        self.refused = 0

    async def __aenter__(self):
        self.server = await ws_serve(self._handler, "127.0.0.1", 0, max_size=2**17)
        self.url = f"ws://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"
        return self

    async def __aexit__(self, *a):
        self.server.close()
        await self.server.wait_closed()

    def kinds(self, src: str, dst: str) -> list:
        return [k for f, t, k in self.log if f == src and t == dst]

    async def inject(self, to: str, from_mbox: str, payload: bytes) -> None:
        await self.socks[to].send(bytes([0x21]) + wire.unb64u(from_mbox) + payload)

    async def _handler(self, ws):
        m = _PATH.fullmatch(ws.request.path)
        if not m or ws.request.headers.get("Origin"):
            return await ws.close(4004)
        mbox = m.group(1)
        n = wire.b64u(os.urandom(32))
        await ws.send(json.dumps({"t": "challenge", "n": n}))
        try:
            a = json.loads(await asyncio.wait_for(ws.recv(), 10))
            x, pk = wire.unb64u(a["x"]), wire.unb64u(a["pk"])
            assert len(json.dumps(a)) <= 2048
            assert mbox_of(agent_id(x, pk)) == mbox
            Ed25519PublicKey.from_public_bytes(pk).verify(wire.unb64u(a["sig"]),
                                                          f"agentj-mbox-auth-v1\n{mbox}\n{n}".encode())
            assert peer.verify_cert(a["cert"], [self.cert_pub], mbox)
        except Exception:
            self.refused += 1
            return await ws.close(4003)
        old = self.socks.get(mbox)
        if old is not None:
            await old.close(4001)
        self.socks[mbox] = ws
        await ws.send(json.dumps({"t": "ok"}))
        try:
            async for f in ws:
                if isinstance(f, str) or len(f) < 18 or f[0] != 0x21:
                    continue
                if len(f) - 17 > 65536:
                    return await ws.close(1009)
                to, payload = wire.b64u(f[1:17]), f[17:]
                self.log.append((mbox, to, payload[0]))
                dst = self.socks.get(to)
                try:
                    if dst is None:
                        raise ConnectionError
                    await dst.send(bytes([0x21]) + wire.unb64u(mbox) + payload)
                except Exception:
                    self.log.append((to, mbox, "nd"))
                    await ws.send(bytes([0x22]) + f[1:17] + b"\x00")
        except Exception:
            pass
        finally:
            if self.socks.get(mbox) is ws:
                del self.socks[mbox]


class FakeSt:
    def __init__(self, root):
        self.root = pathlib.Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.logs = []

    def log(self, ev, **kw):
        self.logs.append((ev, kw))


class CB:
    """What friends.py / serve will be: a friend list, and a record of every callback."""

    def __init__(self):
        self.friends: dict[str, tuple[bytes, bytes]] = {}
        self.requests, self.apps, self.statuses = [], [], []
        self.accept = True
        self.on_req = None
        self.on_msg = None

    def friend_keys(self, pid):
        return self.friends.get(pid)

    def friend_by_mbox(self, mb):
        return next((p for p in self.friends if mbox_of(p) == mb), None)

    def on_request(self, pid, x, ed, freq, from_mbox):
        self.requests.append((pid, x, ed, freq, from_mbox))
        if self.on_req:
            self.on_req(pid, x, ed, freq, from_mbox)

    def accepting(self):
        return self.accept

    def on_app(self, pid, obj):
        self.apps.append((pid, obj))
        if obj["t"] == "facc":
            self.friends[pid] = (wire.unb64u(obj["_x"]), wire.unb64u(obj["_pk"]))
        if self.on_msg:
            self.on_msg(pid, obj)

    def on_status(self, ev, **kw):
        self.statuses.append((ev, kw))

    def app_view(self):
        return [(p, o["t"]) for p, o in self.apps]


def tune(net: PeerNet, **kw):
    net.REQ_BACKOFF = (0.1, 0.4)
    net.MSG_BACKOFF = (0.1, 0.4)
    net.KK_BACKOFF = (0.1, 0.4)
    net.SENT_GRACE = 0.3
    net.TICK = 0.02
    net.RECONNECT = (0.05, 0.2)
    net.HS_TIMEOUT = 1.0
    for k, v in kw.items():
        setattr(net, k, v)


class Node:
    def __init__(self, base, name, relay, seed, clock=time.time, keys=None):
        self.st = FakeSt(pathlib.Path(base) / name)
        self.keys = keys or PeerKeys.load_or_create(self.st)
        self.cb = CB()
        self.relay, self.seed, self.clock = relay, seed, clock
        self.net = self.task = None

    async def cert(self):
        return peer.self_signed_cert(self.seed, self.keys.mbox)

    async def start(self, **kw):
        self.net = PeerNet(self.st, self.keys, self.relay.url, self.cert, self.cb, clock=self.clock)
        tune(self.net, **kw)
        self.task = asyncio.create_task(self.net.run())
        await until(lambda: self.net.up and self.relay.socks.get(self.keys.mbox) is not None)
        return self

    async def stop(self):
        await self.net.stop()
        await asyncio.wait_for(self.task, 5)
        self.net.db.close()


async def until(pred, timeout=6.0):
    t = time.monotonic() + timeout
    while time.monotonic() < t:
        if pred():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met in time")


def accept_with_facc(node_b: Node):
    """B's owner says yes at once: B keeps A's keys and sends facc."""
    def on_req(pid, x, ed, freq, mb):
        node_b.cb.friends[pid] = (x, ed)
        node_b.net.send(pid, {"t": "facc", "rid": freq["rid"], "card": {"name": "B", "owner": "", "intro": "",
                                                                         "lang": "zh", "caps": ["chat"]}})
    return on_req


CARD = {"name": "A", "owner": "", "intro": "hi", "lang": "zh", "caps": ["chat"]}


class Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.seed = os.urandom(32)
        self.relay = await FakeMailbox(Ed25519PrivateKey.from_private_bytes(self.seed).public_key()
                                       .public_bytes_raw()).__aenter__()
        self.nodes = []

    async def asyncTearDown(self):
        for n in self.nodes:
            if n.task and not n.task.done():
                await n.stop()
        await self.relay.__aexit__()
        self.tmp.cleanup()

    def node(self, name, **kw) -> Node:
        n = Node(self.tmp.name, name, self.relay, self.seed, **kw)
        self.nodes.append(n)
        return n

    async def friends(self):
        a, b = await self.node("a").start(), await self.node("b").start()
        b.cb.on_req = accept_with_facc(b)
        await a.net.add(b.keys.id, CARD, "我是 A")
        await until(lambda: b.keys.id in a.cb.friends and not b.net.outbox_rows())
        return a, b


class Mailbox(Base):
    async def test_add_accept_message_roundtrip(self):
        a, b = await self.node("a").start(), await self.node("b").start()
        b.cb.on_req = accept_with_facc(b)
        rid = await a.net.add(b.keys.id, CARD, "我是 A")
        self.assertRegex(rid, r"^[0-9a-f]{16}$")
        self.assertEqual([(r["kind"], r["state"], r["rid"]) for r in a.net.outbox_rows()], [("req", "pending", rid)])
        await until(lambda: b.cb.requests)
        pid, x, ed, freq, mb = b.cb.requests[0]
        self.assertEqual((pid, x, ed, mb), (a.keys.id, a.keys.x_pub, a.keys.ed_pub, a.keys.mbox))
        self.assertEqual((freq["rid"], freq["note"]), (rid, "我是 A"))
        self.assertTrue(peer.verify_card(freq["card"], a.keys.ed_pub, a.keys.x_pub))
        # B's facc reaches A over a fresh KK; A acknowledges it; both outboxes empty
        await until(lambda: ("facc" in [o["t"] for _, o in a.cb.apps]) and not b.net.outbox_rows())
        self.assertEqual(a.cb.friends[b.keys.id], (b.keys.x_pub, b.keys.ed_pub))
        self.assertEqual(a.net.outbox_rows(), [])
        self.assertTrue(any(e == "delivered" and kw["mid"] == rid for e, kw in b.cb.statuses))
        self.assertEqual(self.relay.kinds(a.keys.mbox, b.keys.mbox)[:2], [0x31, 0x33])
        self.assertEqual(self.relay.kinds(b.keys.mbox, a.keys.mbox)[:3], [0x32, 0x34, 0x36])
        # pmsg → pack
        b.cb.on_msg = lambda p, o: o["t"] == "pmsg" and b.net.send(p, {"t": "pack", "mid": o["mid"], "s": "replied"})
        a.net.send(b.keys.id, {"t": "pmsg", "mid": "1" * 16, "thread": "2" * 16, "irt": None, "text": "你好", "ts": 1})
        await until(lambda: any(e == "delivered" and kw["mid"] == "1" * 16 for e, kw in a.cb.statuses))
        self.assertEqual([o["text"] for _, o in b.cb.apps if o["t"] == "pmsg"], ["你好"])
        self.assertEqual(a.net.outbox_rows(), [])
        self.assertTrue(any(o["t"] == "pack" and o["s"] == "replied" for _, o in a.cb.apps))
        # the other direction reuses the live session
        b.net.send(a.keys.id, {"t": "pmsg", "mid": "3" * 16, "thread": "2" * 16, "irt": "1" * 16, "text": "嗨", "ts": 2})
        await until(lambda: any(o.get("mid") == "3" * 16 for _, o in a.cb.apps))

    async def test_offline_queue_then_delivered(self):
        a, b = await self.friends()
        await b.stop()
        a.net.send(b.keys.id, {"t": "pmsg", "mid": "4" * 16, "thread": "5" * 16, "irt": None, "text": "在吗", "ts": 3})
        await until(lambda: "nd" in self.relay.kinds(b.keys.mbox, a.keys.mbox))
        await asyncio.sleep(0.3)
        self.assertEqual([(r["t"], r["state"]) for r in a.net.outbox_rows()], [("pmsg", "pending")])
        b2 = self.node("b", keys=b.keys)
        b2.cb.friends = dict(b.cb.friends)
        b2.cb.on_msg = lambda p, o: o["t"] == "pmsg" and b2.net.send(p, {"t": "pack", "mid": o["mid"], "s": "got"})
        await b2.start()
        await until(lambda: any(o.get("text") == "在吗" for _, o in b2.cb.apps))
        await until(lambda: a.net.outbox_rows() == [])

    async def test_offline_friend_is_retried_at_most_every_kk_cap(self):
        """ADR §9.7: however long the friend was away, the next KK attempt is ≤ KK_BACKOFF[1] away — so a message waits at
        most that long after the friend is back (the friend sends nothing by itself on reconnect)."""
        self.assertLessEqual(PeerNet.KK_BACKOFF[1], 30.0)
        self.assertEqual(PeerNet.MSG_BACKOFF, (5.0, 600.0))
        now = [time.time()]
        a = await self.node("a", clock=lambda: now[0]).start(KK_BACKOFF=PeerNet.KK_BACKOFF)
        b = PeerKeys(Keypair.generate(), Ed25519PrivateKey.generate())   # a friend that is away
        a.cb.friends[b.id] = (b.x_pub, b.ed_pub)
        a.net.send(b.id, {"t": "pmsg", "mid": "8" * 16, "thread": "8" * 16, "irt": None, "text": "x", "ts": 6})
        gaps = []
        for _ in range(12):                                # an hour-long absence, in fast-forward
            await until(lambda: a.net.hs_next.get(b.mbox, 0) > now[0])
            gaps.append(a.net.hs_next[b.mbox] - now[0])
            now[0] = a.net.hs_next[b.mbox] + 0.01
            a.net._kick()
        self.assertEqual(round(gaps[0]), 5)
        self.assertLessEqual(max(gaps), 30.0 + 0.1)
        self.assertGreaterEqual(len([g for g in gaps if g > 29]), 5)

    async def test_outbox_survives_restart(self):
        a, b = await self.friends()
        await b.stop()
        await a.stop()
        a2 = self.node("a", keys=a.keys)
        a2.cb.friends = dict(a.cb.friends)
        a2.net = PeerNet(a2.st, a2.keys, self.relay.url, a2.cert, a2.cb)  # queue while not even connected
        a2.net.send(b.keys.id, {"t": "pmsg", "mid": "6" * 16, "thread": "6" * 16, "irt": None, "text": "重启前", "ts": 4})
        a2.net.db.close()
        await a2.start()
        b2 = self.node("b", keys=b.keys)
        b2.cb.friends = dict(b.cb.friends)
        await b2.start()
        await until(lambda: any(o.get("text") == "重启前" for _, o in b2.cb.apps))

    async def test_message_expires_after_24h(self):
        now = [time.time()]
        a = await self.node("a", clock=lambda: now[0]).start()
        b = PeerKeys(Keypair.generate(), Ed25519PrivateKey.generate())  # a friend that never comes online
        a.cb.friends[b.id] = (b.x_pub, b.ed_pub)
        a.net.send(b.id, {"t": "pmsg", "mid": "7" * 16, "thread": "7" * 16, "irt": None, "text": "x", "ts": 5})
        await until(lambda: "nd" in self.relay.kinds(b.mbox, a.keys.mbox))
        now[0] += 86400 - 10
        await asyncio.sleep(0.1)
        self.assertEqual([r["state"] for r in a.net.outbox_rows()], ["pending"])
        now[0] += 11
        await until(lambda: any(e == "expired" for e, _ in a.cb.statuses))
        self.assertEqual([r["state"] for r in a.net.outbox_rows()], ["expired"])
        self.assertEqual([kw["mid"] for e, kw in a.cb.statuses if e == "expired"], ["7" * 16])

    async def test_request_expires_after_7_days(self):
        now = [time.time()]
        a = await self.node("a", clock=lambda: now[0]).start()
        ghost = PeerKeys(Keypair.generate(), Ed25519PrivateKey.generate())
        await a.net.add(ghost.id, CARD, "")
        await until(lambda: "nd" in self.relay.kinds(ghost.mbox, a.keys.mbox))
        now[0] += 6 * 86400
        await asyncio.sleep(0.1)
        self.assertEqual([r["state"] for r in a.net.outbox_rows()], ["pending"])
        now[0] += 86400 + 1
        await until(lambda: [r["state"] for r in a.net.outbox_rows()] == ["expired"])
        self.assertTrue(any(e == "expired" and kw["kind"] == "req" for e, kw in a.cb.statuses))

    async def test_stranger_kk_is_dropped(self):
        """A host that is not B's friend (but knows B's public keys) gets nothing back; B's cb hears nothing."""
        b = await self.node("b").start()
        c = await self.node("c").start()
        c.cb.friends[b.keys.id] = (b.keys.x_pub, b.keys.ed_pub)
        c.net.send(b.keys.id, {"t": "pmsg", "mid": "8" * 16, "thread": "8" * 16, "irt": None, "text": "hi", "ts": 6})
        await until(lambda: 0x34 in self.relay.kinds(c.keys.mbox, b.keys.mbox))
        await asyncio.sleep(0.5)
        self.assertEqual(self.relay.kinds(b.keys.mbox, c.keys.mbox), [])
        self.assertEqual(b.cb.apps, [])
        self.assertEqual([r["state"] for r in c.net.outbox_rows()], ["pending"])

    async def test_kk_with_wrong_static_is_dropped(self):
        """B lists a friend id whose mailbox C cannot own: even C's valid KK msg1 (to B's real key) does not decrypt for B."""
        a, b = await self.friends()
        c = await self.node("c").start()
        c.cb.friends[b.keys.id] = (b.keys.x_pub, b.keys.ed_pub)
        s_before = len(b.cb.apps)
        # the relay forges `from` = A's mailbox for a KK msg1 made with C's static
        hs = peer.Handshake(peer.KK, True, c.keys.x, prologue=peer.kk_prologue(a.keys.mbox, b.keys.mbox), rs=b.keys.x_pub)
        await self.relay.inject(b.keys.mbox, a.keys.mbox, bytes([0x34]) + os.urandom(8) + hs.write_message(b""))
        await asyncio.sleep(0.3)
        self.assertEqual(len(b.cb.apps), s_before)
        self.assertTrue(any(ev == "peer_kk_undecryptable" for ev, _ in b.st.logs))

    async def test_not_accepting_answers_nothing(self):
        a, b = await self.node("a").start(), await self.node("b").start()
        b.cb.accept = False
        await a.net.add(b.keys.id, CARD, "")
        await until(lambda: self.relay.kinds(a.keys.mbox, b.keys.mbox).count(0x31) >= 2)
        self.assertEqual(self.relay.kinds(b.keys.mbox, a.keys.mbox), [])
        self.assertEqual(b.cb.requests, [])
        self.assertEqual(a.cb.apps, [])
        self.assertEqual([r["state"] for r in a.net.outbox_rows()], ["pending"])

    async def test_refusals_indistinguishable(self):
        """§17.4 / §17.5: not existing · refused · blocked · not discoverable · offline → the requester's application state
        and received application messages are identical. Transport differs only in what the relay alone could tell: offline
        and non-existent draw 0x22; the three refusals complete the handshake (B is online) and then stay silent."""
        views, transport = {}, {}
        for case in ("missing", "refused", "blocked", "undiscoverable", "offline"):
            relay = await FakeMailbox(Ed25519PrivateKey.from_private_bytes(self.seed).public_key()
                                      .public_bytes_raw()).__aenter__()
            try:
                a = Node(self.tmp.name, f"{case}-a", relay, self.seed)
                b = Node(self.tmp.name, f"{case}-b", relay, self.seed)
                await a.start()
                if case in ("refused", "blocked", "undiscoverable"):
                    await b.start()
                    # refused = the owner never answers; blocked / not discoverable = cb drops at once. All the same to A.
                    b.cb.on_req = {"refused": lambda *x: None,
                                   "blocked": lambda *x, b=b: b.cb.friends.pop(x[0], None),
                                   "undiscoverable": lambda *x: None}[case]
                target = b.keys.id
                await a.net.add(target, CARD, "加个好友")
                await asyncio.sleep(1.5)
                views[case] = {
                    "outbox": [(r["kind"], r["peer"] == target, r["state"]) for r in a.net.outbox_rows()],
                    "apps": a.cb.app_view(),
                    "status": [e for e, _ in a.cb.statuses if e not in ("connected", "disconnected")],
                    "requests": [],
                }
                transport[case] = (relay.kinds(b.keys.mbox, a.keys.mbox), b.cb.requests)
                await a.stop()
                if b.task:
                    await b.stop()
            finally:
                await relay.__aexit__()
        first = views["missing"]
        self.assertEqual(first, {"outbox": [("req", True, "pending")], "apps": [], "status": [], "requests": []})
        for case, v in views.items():
            self.assertEqual(v, first, case)
        for case in ("missing", "offline"):
            self.assertIn("nd", transport[case][0])
            self.assertNotIn(0x32, transport[case][0])
        for case in ("refused", "blocked", "undiscoverable"):
            kinds, reqs = transport[case]
            self.assertIn(0x32, kinds)
            self.assertNotIn("nd", kinds)
            self.assertEqual(set(kinds), {0x32})  # nothing after msg2: silence
            self.assertGreaterEqual(len(reqs), 1)  # B's cb saw the request and dropped it

    async def test_simultaneous_kk(self):
        a, b = await self.friends()
        for n in (a, b):  # forget the live session on both sides, then both send at once
            n.net._drop_all_sessions()
        b.cb.on_msg = lambda p, o: o["t"] == "pmsg" and b.net.send(p, {"t": "pack", "mid": o["mid"], "s": "got"})
        a.cb.on_msg = lambda p, o: o["t"] == "pmsg" and a.net.send(p, {"t": "pack", "mid": o["mid"], "s": "got"})
        a.net.send(b.keys.id, {"t": "pmsg", "mid": "a" * 16, "thread": "c" * 16, "irt": None, "text": "A→B", "ts": 7})
        b.net.send(a.keys.id, {"t": "pmsg", "mid": "b" * 16, "thread": "c" * 16, "irt": None, "text": "B→A", "ts": 7})
        await until(lambda: any(o.get("text") == "A→B" for _, o in b.cb.apps)
                    and any(o.get("text") == "B→A" for _, o in a.cb.apps))
        await until(lambda: a.net.outbox_rows() == [] and b.net.outbox_rows() == [])
        self.assertEqual(a.net.kk[b.keys.mbox].sid, b.net.kk[a.keys.mbox].sid)  # one session survived

    async def test_lost_session_recovers(self):
        """B forgets the session (restart / reconnect); A's next pmsg on the stale session is dropped, then retried on a new
        KK because no pack came."""
        a, b = await self.friends()
        b.cb.on_msg = lambda p, o: o["t"] == "pmsg" and b.net.send(p, {"t": "pack", "mid": o["mid"], "s": "got"})
        b.net._drop_all_sessions()
        old = a.net.kk[b.keys.mbox].sid
        a.net.send(b.keys.id, {"t": "pmsg", "mid": "d" * 16, "thread": "d" * 16, "irt": None, "text": "还在吗", "ts": 8})
        await until(lambda: a.net.outbox_rows() == [])
        self.assertEqual([o["text"] for _, o in b.cb.apps if o["t"] == "pmsg"], ["还在吗"])
        self.assertNotEqual(a.net.kk[b.keys.mbox].sid, old)

    async def test_rerequest_forgets_stale_session(self):
        """P71 R2 (two real hosts): A removed B — its `bye` went over a KK session that stays live on A's side only (B dropped
        everything of A on the bye). B asks again, A's owner says yes: the facc must go over a fresh KK at once, not over the
        dead session first and then again after MSG_BACKOFF (5 s in production). A request from a mailbox means that side
        holds no KK session with us."""
        a = await self.node("a").start(MSG_BACKOFF=(5.0, 600.0))
        b = await self.node("b").start()
        b.cb.on_req = accept_with_facc(b)
        await a.net.add(b.keys.id, CARD, "我是 A")
        await until(lambda: b.keys.id in a.cb.friends and not b.net.outbox_rows())
        a.net.send(b.keys.id, {"t": "bye"})                     # A's own session to B is now live
        await until(lambda: any(o["t"] == "bye" for _, o in b.cb.apps))
        self.assertIn(b.keys.mbox, a.net.kk)
        a.cb.friends.pop(b.keys.id)                             # A removed B (store), its KK session stays (as in serve)
        b.cb.friends.pop(a.keys.id)
        b.net.drop_peer(a.keys.id)                              # B on the bye: nothing of A is kept
        a.cb.on_req = accept_with_facc(a)
        mark = len(self.relay.log)
        await b.net.add(a.keys.id, {**CARD, "name": "B"}, "我是 B，再加一次")
        t0 = time.monotonic()
        await until(lambda: a.keys.id in b.cb.friends, timeout=8.0)
        self.assertLess(time.monotonic() - t0, 2.0)
        from_a = [k for f, t, k in self.relay.log[mark:] if f == a.keys.mbox and t == b.keys.mbox]
        self.assertEqual(from_a[:3], [0x32, 0x34, 0x36])        # XX msg2, then a fresh KK msg1, then the facc
        self.assertNotIn(0x36, from_a[:2])

    async def test_stopped_sends_nothing(self):
        a, b = await self.friends()
        a.net.stopped(True)
        mark = len(self.relay.log)
        a.net.send(b.keys.id, {"t": "pmsg", "mid": "e" * 16, "thread": "e" * 16, "irt": None, "text": "急停", "ts": 9})
        await asyncio.sleep(0.4)
        self.assertEqual([x for x in self.relay.log[mark:] if x[0] == a.keys.mbox], [])
        # inbound still reaches cb while stopped
        b.net.send(a.keys.id, {"t": "pmsg", "mid": "f" * 16, "thread": "e" * 16, "irt": None, "text": "收得到", "ts": 9})
        await until(lambda: any(o.get("text") == "收得到" for _, o in a.cb.apps))
        self.assertEqual([x for x in self.relay.log[mark:] if x[0] == a.keys.mbox], [])
        a.net.stopped(False)
        await until(lambda: any(o.get("text") == "急停" for _, o in b.cb.apps))

    async def test_limited_pauses_and_drop_peer(self):
        a, b = await self.friends()
        b.cb.on_msg = lambda p, o: o["t"] == "pmsg" and b.net.send(p, {"t": "limited", "scope": "msg_min", "retry": 30})
        a.net.send(b.keys.id, {"t": "pmsg", "mid": "0" * 16, "thread": "0" * 16, "irt": None, "text": "1", "ts": 1})
        await until(lambda: any(o["t"] == "limited" for _, o in a.cb.apps))
        self.assertGreater(a.net.paused[b.keys.id], time.time() + 20)
        mark = len([1 for _, o in b.cb.apps if o["t"] == "pmsg"])
        a.net.send(b.keys.id, {"t": "pmsg", "mid": "9" * 16, "thread": "0" * 16, "irt": None, "text": "2", "ts": 2})
        await asyncio.sleep(0.4)
        self.assertEqual(len([1 for _, o in b.cb.apps if o["t"] == "pmsg"]), mark)  # held
        a.net.drop_peer(b.keys.id)
        self.assertEqual(a.net.outbox_rows(), [])
        self.assertNotIn(b.keys.mbox, a.net.kk)

    async def test_after_limited_one_message_at_a_time(self):
        """After `limited` the backlog is not burst out when the pause ends: one unacknowledged message at a time (the next
        only after the previous one's pack), then back to normal once nothing is queued (e2e_p71_friends item 4)."""
        a, b = await self.friends()
        got, st = [], {"limited": False, "open": None, "overlap": 0}

        def on_msg(p, o):
            if o["t"] != "pmsg":
                return
            if not st["limited"]:
                st["limited"] = True
                b.net.send(p, {"t": "limited", "scope": "interval", "retry": 1})
                return
            if o["mid"] in got:
                return                                   # a retry of the one in flight
            if st["open"] is not None:
                st["overlap"] += 1
            got.append(o["mid"])
            st["open"] = o["mid"]

            def ack(mid=o["mid"]):
                st["open"] = None
                b.net.send(p, {"t": "pack", "mid": mid, "s": "got"})
            asyncio.get_running_loop().call_later(0.15, ack)
        b.cb.on_msg = on_msg
        mids = [str(i) * 16 for i in range(1, 6)]
        a.net.send(b.keys.id, {"t": "pmsg", "mid": mids[0], "thread": "0" * 16, "irt": None, "text": "1", "ts": 1})
        await until(lambda: any(o["t"] == "limited" for _, o in a.cb.apps))
        self.assertIn(b.keys.id, a.net.slow)
        for i, m in enumerate(mids[1:], 2):
            a.net.send(b.keys.id, {"t": "pmsg", "mid": m, "thread": "0" * 16, "irt": None, "text": str(i), "ts": i})
        await until(lambda: len(got) == 5, 15)
        self.assertEqual(got, mids)                      # in order, the first (limited) one again first
        self.assertEqual(st["overlap"], 0)               # never two unacknowledged messages at once
        await until(lambda: not a.net.outbox_rows())
        self.assertNotIn(b.keys.id, a.net.slow)          # backlog through: normal sending again

    async def test_text_limits(self):
        a, b = await self.friends()
        with self.assertRaises(ValueError):
            a.net.send(b.keys.id, {"t": "pmsg", "mid": "1" * 16, "thread": "1" * 16, "irt": None, "text": "x" * 20001})
        with self.assertRaises(ValueError):
            a.net.send(b.keys.id, {"t": "card", "card": {"intro": "y" * 70000}})
        a.net.send(b.keys.id, {"t": "pmsg", "mid": "2" * 16, "thread": "1" * 16, "irt": None, "text": "😀" * 10000})
        await until(lambda: any(o.get("mid") == "2" * 16 for _, o in b.cb.apps))
        # a receiver closes the session on an over-long text (a sender that skipped the check)
        s = a.net.kk[b.keys.mbox]
        ct = s.send.encrypt(b"", peer._pad({"t": "pmsg", "mid": "3" * 16, "text": "x" * 20001}))
        await a.net._tx(b.keys.mbox, peer.K_DATA, s.sid, ct)
        await until(lambda: (a.keys.mbox, s.sid) not in b.net.sessions)
        self.assertFalse(any(o.get("mid") == "3" * 16 for _, o in b.cb.apps))

    async def test_relay_refuses_bad_certificate(self):
        a = self.node("a")
        a.seed = os.urandom(32)  # not the relay's key
        a.net = PeerNet(a.st, a.keys, self.relay.url, a.cert, a.cb)
        tune(a.net)
        a.task = asyncio.create_task(a.net.run())
        await until(lambda: self.relay.refused >= 2)
        self.assertFalse(any(e == "connected" for e, _ in a.cb.statuses))

    async def test_no_cert_means_no_connection(self):
        a = self.node("a")
        calls = []

        async def none():
            calls.append(1)
            return None
        a.net = PeerNet(a.st, a.keys, self.relay.url, none, a.cb)
        tune(a.net)
        a.task = asyncio.create_task(a.net.run())
        await until(lambda: len(calls) >= 2)
        self.assertEqual(self.relay.socks, {})


class CapWS:
    def __init__(self):
        self.sent = []

    async def send(self, f):
        self.sent.append(f)


def _xx_msg3(hs, keys, freq):
    return hs.write_message(lambda h: peer._pad({"pk": wire.b64u(keys.ed_pub),
                                                 "sig": wire.b64u(keys.ed.sign(b"agentj-peer-xx-v1\n" + h)),
                                                 "freq": freq}, 8192))


class Forgery(unittest.IsolatedAsyncioTestCase):
    """Frames handed straight to a PeerNet, as a lying relay would deliver them."""

    def _net(self, d, name):
        st = FakeSt(pathlib.Path(d) / name)
        k = PeerKeys.load_or_create(st)
        cb = CB()
        net = PeerNet(st, k, "ws://127.0.0.1:1", None, cb)
        net.ws, net.up = CapWS(), True
        return net, k, cb

    async def _request(self, b, from_mbox, c):
        hs = Handshake(XX, True, c.x, prologue=peer.XX_PROLOGUE)
        sid = os.urandom(8)
        await b._frame(bytes([0x21]) + wire.unb64u(from_mbox) + bytes([0x31]) + sid + hs.write_message(b""))
        f = b.ws.sent[-1]
        self.assertEqual((f[0], f[17], f[18:26]), (0x21, 0x32, sid))
        p = peer._unpad(hs.read_message(f[26:]))
        self.assertEqual(agent_id(hs.rs, wire.unb64u(p["pk"])), b.keys.id)  # B's msg2 binds its ID
        freq = {"rid": os.urandom(8).hex(), "card": peer.sign_card(c, CARD), "note": "", "ts": 1}
        await b._frame(bytes([0x21]) + wire.unb64u(from_mbox) + bytes([0x33]) + sid + _xx_msg3(hs, c, freq))

    async def test_forged_from_is_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            b, _, bcb = self._net(d, "b")
            c = PeerKeys(Keypair.generate(), Ed25519PrivateKey.generate())
            other = PeerKeys(Keypair.generate(), Ed25519PrivateKey.generate())
            await self._request(b, other.mbox, c)       # C's keys, someone else's mailbox
            self.assertEqual(bcb.requests, [])
            self.assertTrue(any(ev == "peer_xx_bad_from" for ev, _ in b.st.logs))
            await self._request(b, c.mbox, c)           # control: the honest `from`
            self.assertEqual([r[0] for r in bcb.requests], [c.id])
            b.db.close()

    async def test_bad_signature_in_msg3_is_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            b, _, bcb = self._net(d, "b")
            c = PeerKeys(Keypair.generate(), Ed25519PrivateKey.generate())
            hs = Handshake(XX, True, c.x, prologue=peer.XX_PROLOGUE)
            sid = os.urandom(8)
            await b._frame(bytes([0x21]) + wire.unb64u(c.mbox) + bytes([0x31]) + sid + hs.write_message(b""))
            hs.read_message(b.ws.sent[-1][26:])
            freq = {"rid": "a" * 16, "card": peer.sign_card(c, CARD), "note": "", "ts": 1}
            m3 = hs.write_message(peer._pad({"pk": wire.b64u(c.ed_pub), "sig": wire.b64u(c.ed.sign(b"other")),
                                             "freq": freq}, 8192))
            await b._frame(bytes([0x21]) + wire.unb64u(c.mbox) + bytes([0x33]) + sid + m3)
            self.assertEqual(bcb.requests, [])
            b.db.close()

    async def test_requester_rejects_wrong_responder(self):
        """A adds T; whoever answers at T's mailbox must hash back to T's ID, else A sends no msg3 (no freq leaks)."""
        with tempfile.TemporaryDirectory() as d:
            a, _, _ = self._net(d, "a")
            t = PeerKeys(Keypair.generate(), Ed25519PrivateKey.generate())
            m = PeerKeys(Keypair.generate(), Ed25519PrivateKey.generate())   # the man in the middle
            await a.add(t.id, CARD, "secret note")
            a._wake = None
            await a._pump()
            f = a.ws.sent[-1]
            self.assertEqual((f[17], wire.b64u(f[1:17])), (0x31, t.mbox))
            sid = f[18:26]
            hs = Handshake(XX, False, m.x, prologue=peer.XX_PROLOGUE)
            hs.read_message(f[26:])
            m2 = hs.write_message(lambda h: peer._pad({"pk": wire.b64u(m.ed_pub),
                                                       "sig": wire.b64u(m.ed.sign(b"agentj-peer-xx-v1\n" + h))}))
            n = len(a.ws.sent)
            await a._frame(bytes([0x21]) + wire.unb64u(t.mbox) + bytes([0x32]) + sid + m2)
            self.assertEqual(len(a.ws.sent), n)
            self.assertTrue(any(ev == "peer_xx_bad_responder" for ev, _ in a.st.logs))
            a.db.close()

    async def test_outbox_file_mode(self):
        with tempfile.TemporaryDirectory() as d:
            b, _, _ = self._net(d, "b")
            self.assertEqual(stat.S_IMODE((pathlib.Path(d) / "b/peer/outbox.sqlite").stat().st_mode), 0o600)
            b.db.close()

    async def test_outbox_wal_no_fsync_per_write(self):
        """P71 R1: the pump runs on serve's loop. With the default rollback journal each autocommit write fsyncs several times
        (0.1–0.4 s per pump step on a disk-backed folder: the release gate's TMPDIR under /var/tmp starved the limited-backlog
        test). WAL + synchronous=NORMAL; the -wal / -shm files keep 0600 like the database."""
        with tempfile.TemporaryDirectory() as d:
            b, _, _ = self._net(d, "b")
            self.assertEqual(b.db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(b.db.execute("PRAGMA synchronous").fetchone()[0], 1)    # NORMAL
            b.db.execute("INSERT INTO outbox (kind, peer, mbox, obj, created, expires) VALUES ('pmsg', 'p', 'm', '{}', 1, 2)")
            files = sorted(f.name for f in (pathlib.Path(d) / "b/peer").glob("outbox.sqlite*"))
            self.assertEqual(files, ["outbox.sqlite", "outbox.sqlite-shm", "outbox.sqlite-wal"])
            for name in files:
                self.assertEqual(stat.S_IMODE((pathlib.Path(d) / "b/peer" / name).stat().st_mode), 0o600, name)
            b.db.close()


if __name__ == "__main__":
    unittest.main()
