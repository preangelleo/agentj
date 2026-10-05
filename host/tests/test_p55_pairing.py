"""P55 (0.15.1): a full host no longer blocks a new phone — approving it unbinds the stalest remote (offline first, then the
longest unseen; online last). The same browser paired again under a lost key replaces its old record (`iid`). A removed
device that resumes is told why (`removed`: replaced | revoked) before the close, so its page never guesses."""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import asyncio
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import gate, serve, wire  # noqa: E402
from agentj.noise import IK, Handshake, Keypair  # noqa: E402
from agentj.state import MAX_DEVICES, State, evict_order  # noqa: E402

PASS = "test-passphrase-P55"
IID = wire.b64u(b"\x01" * 16)


def _state(d) -> State:
    st = State(pathlib.Path(d) / "s")
    st.init(relay="ws://127.0.0.1:1")
    gate.set_passphrase(st, PASS)
    return st


def _fill(st, n=MAX_DEVICES):
    """n remotes paired one after another, oldest first (paired_at strictly increasing)."""
    ids = []
    for i in range(n):
        ids.append(st.add_device(os.urandom(32), f"iOS Safari {i}"))
        d = st.devices()
        d[ids[-1]]["paired_at"] = 1_790_000_000 + i * 3600
        st.write_private(st.devices_path, json.dumps(d).encode())
    return ids


class Order(unittest.TestCase):
    def test_offline_first_then_longest_unseen_online_last(self):
        devs = {"A": {"paired_at": 10}, "B": {"paired_at": 20, "seen": 50}, "C": {"paired_at": 30}, "D": {"paired_at": 5}}
        self.assertEqual(evict_order(devs), ["D", "A", "C", "B"], "B was paired early but resumed most recently")
        self.assertEqual(evict_order(devs, {"D", "A"}), ["C", "B", "D", "A"], "online ones go last, oldest first")


class Host(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)
        self.host = serve.Host(self.st, events="jsonl", read_stdin=False)
        self.sent, self.ops = [], []

        async def send_app(s, obj):
            self.sent.append((s.cid, obj))
            return True

        async def op(o, cid, payload=b""):
            self.ops.append((o, cid, payload))
        self.host.send_app = send_app
        self.host._op = op

    def tearDown(self):
        self.tmp.cleanup()

    def _pending(self, cid=7, pub=None, iid=""):
        pub = pub or os.urandom(32)
        s = serve.Session(cid=cid, state="pending", device=wire.device_id(pub), name="网页 · iOS Safari", pub=pub,
                          h=os.urandom(32), iid=iid)
        s.deadline = time.monotonic() + 60
        self.host.sessions[cid] = s
        p = serve.Pairing(os.urandom(16), os.urandom(32), time.time() + 60, time.monotonic() + 60, ctl=None, cid=cid)
        self.host.pairing = p
        return s, p

    def _ready(self, cid, did):
        pub = wire.unb64u(self.st.devices()[did]["pub"])
        self.host.sessions[cid] = serve.Session(cid=cid, state="ready", device=did, pub=pub, name="x")

    def test_full_host_approves_and_unbinds_the_oldest_offline_remote(self):
        """The customer's case (2026-10-05): five remotes, four of them the same iPhone's lost pairings, one Mac online."""
        ids = _fill(self.st)
        self._ready(30, ids[0])                    # the oldest is online (the Mac): it must not be the one to go

        async def go():
            s, p = self._pending()
            ev = self.host._pending_event(s)
            self.assertNotIn("full", ev, "a full list no longer blocks the code prompt")
            self.assertEqual((ev["evict"]["id"], ev["evict"]["online"]), (ids[1], False))
            return s, await self.host.decide(p, wire.safety_code(s.h), PASS)
        s, res = asyncio.run(go())
        self.assertEqual(res["ev"], "approved")
        self.assertEqual(res["evicted"]["id"], ids[1])
        self.assertEqual(res["evicted"]["name"], "iOS Safari 1")
        self.assertEqual(len(self.st.devices()), MAX_DEVICES)
        self.assertNotIn(ids[1], self.st.devices())
        self.assertIn(s.device, self.st.devices())
        self.assertIn(ids[0], self.st.devices(), "the online remote stays")
        self.assertEqual(self.st.removed_why(ids[1]), "replaced")
        log = self.st.log_path.read_text()
        self.assertIn('"ev": "auto_unbind"', log)
        self.assertNotIn("device_limit", log)

    def test_all_online_the_longest_unseen_goes_and_is_disconnected(self):
        ids = _fill(self.st)
        for i, did in enumerate(ids):
            self._ready(40 + i, did)
        d = self.st.devices()
        d[ids[0]]["seen"] = int(time.time())         # the oldest pairing was used a moment ago
        self.st.write_private(self.st.devices_path, json.dumps(d).encode())

        async def go():
            s, p = self._pending()
            return await self.host.decide(p, wire.safety_code(s.h), PASS)
        res = asyncio.run(go())
        self.assertEqual(res["evicted"]["id"], ids[1])
        self.assertTrue(res["evicted"]["online"])
        self.assertNotIn(41, self.host.sessions, "its live session ends at once")
        self.assertIn((wire.OP_CLOSE, 41, b""), self.ops)

    def test_same_browser_replaces_its_old_record_without_a_full_list(self):
        old = self.st.pair_device(os.urandom(32), "网页 · iOS Safari", iid=IID)["device"]
        other = self.st.add_device(os.urandom(32), "macOS Chrome")

        async def go():
            s, p = self._pending(iid=IID)
            ev = self.host._pending_event(s)
            self.assertEqual(ev["replaces"]["id"], old)
            self.assertNotIn("evict", ev)
            return await self.host.decide(p, wire.safety_code(s.h), PASS)
        res = asyncio.run(go())
        self.assertEqual(res["replaced"]["id"], old)
        self.assertNotIn("evicted", res)
        self.assertEqual(set(self.st.devices()), {other, wire.device_id(self.host.sessions[7].pub)})
        self.assertEqual(self.st.removed_why(old), "replaced")

    def test_same_key_repairs_in_place(self):
        pub = os.urandom(32)
        did = self.st.add_device(pub, "phone")
        _fill(self.st, MAX_DEVICES - 1)
        got = self.st.pair_device(pub, "phone again", evict=True)
        self.assertEqual((got["device"], got["evicted"], got["replaced"]), (did, None, None))
        self.assertEqual(len(self.st.devices()), MAX_DEVICES)

    def test_seen_is_recorded_on_resume_at_most_once_a_minute(self):
        did = self.st.add_device(os.urandom(32), "phone")
        self.st.touch_seen(did)
        first = self.st.devices()[did]["seen"]
        self.st.touch_seen(did)
        self.assertEqual(self.st.devices()[did]["seen"], first)

    def _resume(self, kp: Keypair) -> tuple:
        """A device resuming with an IK msg1 (like the web client): → (its recv CipherState, the host's answer frames)."""
        hs = Handshake(IK, True, kp, prologue=wire.resume_prologue(self.host.channel), rs=self.host.kp.pub)
        msg1 = hs.write_message(b'{"v":1}')
        s = serve.Session(cid=9)
        self.host.sessions[9] = s

        async def go():
            await self.host._resume_init(s, msg1)
            hs_resp = self.ops[-1]
            self.assertEqual(hs_resp[2][0], wire.HS_RESP)
            hs.read_message(hs_resp[2][1:])
            await self.host._app(s, {"t": "hello", "caps": ["p33"]})
        asyncio.run(go())
        return [o for c, o in self.sent if c == 9]

    def test_a_replaced_device_that_resumes_is_told_so_then_closed(self):
        kp = Keypair.generate()
        _fill(self.st, MAX_DEVICES - 1)
        did = self.st.add_device(kp.pub, "old iPhone")
        d = self.st.devices()
        d[did]["paired_at"] = 1_700_000_000           # paired long before the others: the one to go
        self.st.write_private(self.st.devices_path, json.dumps(d).encode())

        async def pair_new():
            s, p = self._pending()
            return await self.host.decide(p, wire.safety_code(s.h), PASS)
        self.assertEqual(asyncio.run(pair_new())["evicted"]["id"], did)
        got = self._resume(kp)
        self.assertEqual(got, [{"t": "removed", "why": "replaced"}])
        self.assertNotIn(9, self.host.sessions)
        self.assertEqual(self.ops[-1][:2], (wire.OP_CLOSE, 9))

    def test_a_revoked_or_unknown_device_is_told_revoked(self):
        kp = Keypair.generate()
        did = self.st.add_device(kp.pub, "phone")
        asyncio.run(self.host.revoke(did))
        self.assertEqual(self._resume(kp), [{"t": "removed", "why": "revoked"}])
        self.sent.clear()
        self.assertEqual(self._resume(Keypair.generate()), [{"t": "removed", "why": "revoked"}])

    def test_a_removed_device_gets_nothing_else(self):
        kp = Keypair.generate()
        hs = Handshake(IK, True, kp, prologue=wire.resume_prologue(self.host.channel), rs=self.host.kp.pub)
        s = serve.Session(cid=11)
        self.host.sessions[11] = s

        async def go():
            await self.host._resume_init(s, hs.write_message(b"{}"))
            await self.host._app(s, {"t": "msg", "text": "hi"})
        asyncio.run(go())
        self.assertEqual([o for c, o in self.sent if c == 11], [])
        self.assertNotIn(11, self.host.sessions)

    def test_iid_is_validated(self):
        self.assertEqual(serve._iid({"iid": IID}), IID)
        for bad in ({}, {"iid": 5}, {"iid": "short"}, {"iid": IID + "x"}, {"iid": "!" * 22}):
            self.assertEqual(serve._iid(bad), "")


if __name__ == "__main__":
    unittest.main()
