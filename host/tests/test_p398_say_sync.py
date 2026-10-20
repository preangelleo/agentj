"""P398 (#155): `say_sync` — after a reconnect the phone asks where its still-queued sends are, and the host answers from its
own record (compose.Sends, then the receipts on disk): delivered / cancelled / failed / done, nothing while one still waits.
Another device's sids read as unknown to this device (never its states); bad ids and shapes are ignored."""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import asyncio
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import compose, serve, wire  # noqa: E402

from test_l1 import Phone, _host, _state  # noqa: E402


def sid():
    return wire.b64u(os.urandom(16))


class SaySyncState(unittest.TestCase):
    def test_mapping(self):
        S = compose.Send
        f = serve.say_sync_state
        self.assertIsNone(f(S("d", "a", 1, state="pending"), None), "still waiting: the phone keeps 取消")
        self.assertIsNone(f(S("d", "a", 1, state="delivering"), "recv"))
        self.assertEqual(f(S("d", "a", 1, state="delivered"), "recv"), "delivered", "memory wins over disk")
        self.assertEqual(f(S("d", "a", 1, state="uncertain"), None), "delivered", "maybe handed over = too late to withdraw")
        self.assertEqual(f(S("d", "a", 1, state="cancelled"), None), "cancelled")
        self.assertEqual(f(S("d", "a", 1, state="failed"), None), "failed")
        self.assertIsNone(f(None, "recv"))
        self.assertEqual(f(None, "deliv"), "delivered")
        self.assertEqual(f(None, "done"), "done")
        self.assertEqual(f(None, "lost"), "failed")
        self.assertEqual(f(None, None), "done", "unknown to this host: nothing left to withdraw")


class SaySync(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_answers_from_the_host_record(self):
        sent = []
        host = _host(self.st, sent)
        ph, other = Phone(self.st), Phone(self.st, "平板")
        s = serve.Session(cid=11, state="ready", device=ph.did, name="手机", pub=ph.pub, p33=True)
        host.sessions[11] = s
        a, b, c, d, e, f = (sid() for _ in range(6))
        host.sends.add(compose.Send(ph.did, a, 3, state="pending"))
        host.sends.add(compose.Send(ph.did, b, 4, state="delivered"))
        host.sends.add(compose.Send(ph.did, c, 5, state="cancelled"))
        host.sends.add(compose.Send(other.did, d, 6, state="pending"))       # another device's send
        host.receipts.mark(ph.did, e, "deliv", 7)
        host.receipts.flush()

        async def go():
            await host._app(s, {"t": "say_sync", "sids": [a, b, c, d, e, f, "bad", 7]})
            await host._app(s, {"t": "say_sync", "sids": "nope"})
        asyncio.run(go())
        got = {o["sid"]: o["s"] for _, o in sent if o["t"] == "say_state"}
        self.assertNotIn(a, got, "a send still waiting gets no answer: its 取消 stays")
        self.assertEqual(got[b], "delivered")
        self.assertEqual(got[c], "cancelled")
        self.assertEqual(got[d], "done", "another device's send: unknown here, its state never leaks")
        self.assertEqual(got[e], "delivered", "from the receipts on disk (a host restart in between)")
        self.assertEqual(got[f], "done")
        self.assertEqual(len(got), 5)
        self.assertTrue(all(c == 11 for c, o in sent if o["t"] == "say_state"), "answered on the asking session only")

    def test_caps_and_p33_only(self):
        self.assertIn("say_sync", serve.P33_ONLY)
        host = _host(self.st, [])
        self.assertIn(compose.CAP_SYNC, host._caps()["caps"])


if __name__ == "__main__":
    unittest.main()
