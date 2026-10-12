"""P193 (B7): the resident bot host renews its relay ticket in place instead of reconnecting every 240 s.

Against a relay that advertises `renew`: a fresh host ticket goes up as a text frame every RENEW_EVERY, and op 3 keeps only
visitors who spoke within VISITOR_IDLE (the visitor's own session follows the renewed admission, so it keeps talking past
its first ticket). Against an older relay (no `renew`): no text frame and no op 3 ever (it would close the socket); the
lease is still refreshed and the visitor's first admission still ends. Event-driven with a fake idle clock: no wall-clock
thresholds, so a loaded machine only makes it slower.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import contextlib
import json
import struct
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from websockets.asyncio.server import serve as ws_serve
from websockets.exceptions import ConnectionClosed

from agentj import wire
from agentj.bots import runtime
from agentj.bots.runtime import Runtime
from agentj.bots.store import Store
from agentj.bots.visitor import client_finish, issue_claim, public

TALKER, IDLE = 7, 8
TICKET_S = 2          # the visitors' first admission (seconds of real time)


class Clock:
    """runtime.time stand-in: monotonic() is the test's idle clock; wall time stays real (tickets, request ids)."""
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    time, time_ns = staticmethod(time.time), staticmethod(time.time_ns)


class FakeRelay:
    def __init__(self, bid, host_pub, issuer, renew, clock):
        self.bid, self.host_pub, self.issuer, self.renew, self.clock = bid, host_pub, issuer, renew, clock
        self.texts, self.rounds, self.replies, self.drops = [], [], [], []
        self.event = asyncio.Event()
        self.finished = asyncio.Event()

    async def wait(self, pred, what, timeout=30):
        async def loop():
            while not pred():
                self.event.clear()
                await self.event.wait()
        try:
            await asyncio.wait_for(loop(), timeout)
        except TimeoutError:
            raise AssertionError("timeout: " + what) from None

    async def handler(self, ws):
        await ws.send(json.dumps({"challenge": "n" * 43, **({"renew": 1} if self.renew else {})}))
        json.loads(await ws.recv())
        clients = {}
        for cid in (TALKER, IDLE):
            v = X25519PrivateKey.generate()
            ticket = issue_claim(self.issuer, self.bid, wire.b64u(self.host_pub), wire.b64u(public(v)), 1,
                                 int(time.time()) + TICKET_S)
            await ws.send(bytes([0]) + struct.pack(">I", cid) + json.dumps(ticket).encode())
            clients[cid] = v
        self.admitted_at = time.time()

        async def reader():
            keeps = None
            async for raw in ws:
                if isinstance(raw, str):
                    self.texts.append(json.loads(raw))
                    keeps = []
                    self.rounds.append(keeps)     # the op 3 frames that follow belong to this renewal
                else:
                    cid = struct.unpack(">I", raw[1:5])[0]
                    if raw[0] == 3:
                        if keeps is not None:
                            keeps.append(cid)
                    elif raw[0] == 2:
                        self.drops.append(cid)
                    elif raw[0] == 1 and isinstance(clients.get(cid), X25519PrivateKey):
                        clients[cid] = client_finish(clients[cid], json.loads(raw[5:]), self.host_pub)  # admission answer
                    elif raw[0] == 1:
                        self.replies.append((time.time(), cid))
                self.event.set()

        async def say():
            await ws.send(bytes([1]) + struct.pack(">I", TALKER) + clients[TALKER].seal({"t": "human", "r": "b" * 32}))

        task = asyncio.create_task(reader())
        try:
            with contextlib.suppress(ConnectionClosed):   # the host task is cancelled at the end of the test
                await self.wait(lambda: not any(isinstance(c, X25519PrivateKey) for c in clients.values()), "admission answers")
                await self.script(say)
        finally:
            self.finished.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


class RenewTest(unittest.IsolatedAsyncioTestCase):
    async def run_relay(self, renew, script):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = Store(Path(tmp.name) / "bots")
        self.addCleanup(store.close)
        bid = store.create({"slug": "renew-shop", "enabled": True, "terms_accepted": True})["id"]
        sk, issuer, clock = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate(), Clock()
        relay = FakeRelay(bid, public(sk), issuer, renew, clock)
        relay.script = lambda say: script(relay, say)
        server = await ws_serve(relay.handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        host = SimpleNamespace(sk=sk, cfg={"relay": f"ws://127.0.0.1:{port}"}, agent=None, agent_cfg={"kind": "codex"},
                               st=SimpleNamespace(log=lambda *a, **k: None))

        async def nothing(*a):
            pass
        r = Runtime(SimpleNamespace(host=host, state=lambda: store, approve=nothing, handoff=nothing))
        relay.registered = []

        async def register(b):
            relay.registered.append(time.time())
            relay.event.set()
            return {"n": len(relay.registered), "claim": {"expires": int(time.time()) + 300}}, 1
        r.register = register
        with patch.object(runtime, "ISSUER", public(issuer)), patch.object(runtime, "RENEW_EVERY", 0.2), \
                patch.object(runtime, "VISITOR_IDLE", 100), patch.object(runtime, "time", clock):
            task = asyncio.create_task(r.serve_bot(bid))
            try:
                await asyncio.wait_for(relay.finished.wait(), 90)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        server.close()
        await server.wait_closed()
        return relay

    async def test_renews_in_place_and_keeps_only_talking_visitors(self):
        async def script(relay, say):
            await relay.wait(lambda: len(relay.texts) >= 2, "two renewals")
            self.assertIn(IDLE, relay.rounds[1], "a fresh visitor is vouched for")
            relay.clock.now = 1000                       # both visitors idle past VISITOR_IDLE …
            await say()                                  # … then the talker speaks again
            await relay.wait(lambda: relay.replies, "reply")
            n = len(relay.texts)
            await relay.wait(lambda: len(relay.texts) >= n + 2, "two more renewals")
            self.assertEqual(relay.rounds[-1], [TALKER], "only the talking visitor is still vouched for")
            # past the first admission, the talker still gets answers and is never dropped for expiry
            while time.time() < relay.admitted_at + TICKET_S + 1:
                await asyncio.sleep(0.1)
            seen = len(relay.replies)
            await say()
            await relay.wait(lambda: len(relay.replies) > seen, "reply after the first admission ended")
        relay = await self.run_relay(True, script)
        self.assertTrue(all(set(t) == {"renew"} for t in relay.texts), "only the renewal shape goes up as text")
        ns = [t["renew"]["n"] for t in relay.texts]
        self.assertEqual(ns, sorted(set(ns)), "each renewal carries a freshly registered ticket")
        self.assertNotIn(TALKER, relay.drops)

    async def test_legacy_relay_gets_no_renewal_frames(self):
        async def script(relay, say):
            await relay.wait(lambda: len(relay.registered) >= 4, "lease refreshed")
            while time.time() < relay.admitted_at + TICKET_S + 1:
                await asyncio.sleep(0.1)
            await say()                                  # the first admission has ended: the host drops the visitor
            await relay.wait(lambda: TALKER in relay.drops, "expired visitor dropped")
        relay = await self.run_relay(False, script)
        self.assertEqual(relay.texts, [])
        self.assertEqual(relay.rounds, [])


if __name__ == "__main__":
    unittest.main()
