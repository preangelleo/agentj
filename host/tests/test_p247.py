"""Owner read receipts: real host handlers, paired remotes and durable restart."""
import _hermetic
import asyncio
import pathlib
import tempfile
import unittest
from unittest.mock import patch
from test_l1 import Phone, _host, _ready, _state
from agentj.history import History
from agentj.readmark import ReadMark


class OwnerRead(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)
        self.sent = []
        self.h = _host(self.st, self.sent)
        self.a = _ready(self.h, Phone(self.st), 11)
        self.b = _ready(self.h, Phone(self.st, "B"), 12)
        self.a.p33 = self.b.p33 = True
        self.one = self.h.hist.add({"k":"agent"}, "first")
        self.two = self.h.hist.add({"k":"agent"}, "second")

    def tearDown(self):
        self.tmp.cleanup()

    def test_broadcast_max_reconnect_and_restart(self):
        async def go():
            pending = []
            self.h._post = lambda f, *args: pending.append((f, args))
            await self.h.on_p33(self.a, "hist_read", {"epoch":self.h.hist.epoch, "id":self.two["id"]})
            for f, args in pending:
                await f(*args)
            self.assertEqual({cid for cid, m in self.sent if m["t"] == "hist_read"}, {11,12})
            await self.h.on_p33(self.b, "hist_read", {"epoch":self.h.hist.epoch, "id":self.one["id"]})
            self.assertEqual(self.h.hist_metadata()["read"], self.two["id"])
            self.sent.clear()
            await self.h.on_p33(self.b, "hist_get", {"r":"abcdefghijklmnopqrstuv"})
            self.assertEqual(self.sent[-1][1]["read"], self.two["id"])
        asyncio.run(go())
        self.assertEqual(ReadMark(self.st).value(History(self.st)), self.two["id"])
        self.assertEqual((self.st.root / "history-read.json").stat().st_mode & 0o777, 0o600)

    def test_invalid_epoch_id_silent_and_revoked(self):
        silent = self.h.hist.add({"k":"agent"}, "〔不回群〕")
        async def go():
            for ep, tid in [(999, self.two["id"]), (self.h.hist.epoch, True), (self.h.hist.epoch, 9999), (self.h.hist.epoch, silent["id"])]:
                await self.h.on_p33(self.a, "hist_read", {"epoch":ep, "id":tid})
            self.a.state = "pending"
            await self.h.on_p33(self.a, "hist_read", {"epoch":self.h.hist.epoch, "id":self.two["id"]})
        asyncio.run(go())
        self.assertEqual(self.h.hist_metadata()["read"], 0)
        self.a.state = "ready"
        self.st.remove_device(self.a.device)
        asyncio.run(self.h.on_p33(self.a, "hist_read", {"epoch":self.h.hist.epoch, "id":self.two["id"]}))
        self.assertEqual(self.h.hist_metadata()["read"], 0)

    def test_clear_and_write_failure(self):
        r = self.h.readmark
        with patch.object(self.st, "write_private", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                r.see(self.h.hist, self.h.hist.epoch, self.two["id"])
        self.assertEqual(r.value(self.h.hist), 0)
        self.assertTrue(r.see(self.h.hist, self.h.hist.epoch, self.two["id"]))
        old = self.h.hist.epoch
        self.h.hist.reset("test")
        self.assertEqual(r.value(self.h.hist), 0)
        self.assertFalse(r.see(self.h.hist, old, self.two["id"]))
