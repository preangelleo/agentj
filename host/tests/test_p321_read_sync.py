"""P321: read status follows the host across every remote — a first mark is seeded at the newest reply (no 「99+」 after
an upgrade), a lower / refused receipt answers its sender with the host's mark, three remotes stay in step."""
import _hermetic
import asyncio
import json
import tempfile
import unittest
from test_l1 import Phone, _host, _ready, _state
from agentj.history import History
from agentj.readmark import ReadMark


class Seed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_mark_is_the_newest_visible_reply(self):
        h = History(self.st)
        ids = [h.add({"k": "agent"}, f"reply {i}")["id"] for i in range(150)]
        h.add({"k": "agent"}, "〔不回群〕")
        h.add({"k": "user", "text": "hi"}, "")
        r = ReadMark(self.st, h)
        self.assertEqual(r.value(h), ids[-1])
        self.assertEqual(json.loads((self.st.root / "history-read.json").read_text()), {"epoch": h.epoch, "read": ids[-1]})
        self.assertEqual((self.st.root / "history-read.json").stat().st_mode & 0o777, 0o600)
        nxt = h.add({"k": "agent"}, "new")
        self.assertEqual(ReadMark(self.st, h).value(h), ids[-1], "a restart keeps the stored mark, never reseeds")
        self.assertTrue(r.see(h, h.epoch, nxt["id"]))

    def test_empty_history_and_kept_mark(self):
        h = History(self.st)
        self.assertEqual(ReadMark(self.st, h).value(h), 0)
        a = h.add({"k": "agent"}, "a")
        h.add({"k": "agent"}, "b")
        self.st.write_private(self.st.root / "history-read.json", json.dumps({"epoch": h.epoch, "read": a["id"]}).encode())
        self.assertEqual(ReadMark(self.st, h).value(h), a["id"], "an existing mark is the host's word")

    def test_no_hist_keeps_p247_behaviour(self):
        h = History(self.st)
        h.add({"k": "agent"}, "a")
        self.assertEqual(ReadMark(self.st).value(h), 0)


class ThreeRemotes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)
        self.sent = []
        self.h = _host(self.st, self.sent)
        self.phone = _ready(self.h, Phone(self.st, "iPhone"), 11)
        self.pad = _ready(self.h, Phone(self.st, "Pad"), 12)
        self.mac = _ready(self.h, Phone(self.st, "Mac"), 13)
        for s in (self.phone, self.pad, self.mac):
            s.p33 = True
        self.turns = [self.h.hist.add({"k": "agent"}, f"r{i}") for i in range(5)]

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, s, tid, epoch=None):
        async def go():
            pending = []
            self.h._post = lambda f, *a: pending.append((f, a))
            await self.h.on_p33(s, "hist_read", {"epoch": self.h.hist.epoch if epoch is None else epoch, "id": tid})
            for f, a in pending:
                await f(*a)
        self.sent.clear()
        asyncio.run(go())
        return [(cid, m) for cid, m in self.sent if m["t"] == "hist_read"]

    def test_any_remote_clears_all(self):
        last = self.turns[-1]["id"]
        got = self.read(self.pad, last)
        self.assertEqual({cid for cid, _ in got}, {11, 12, 13})
        self.assertTrue(all(m["read"] == last and m["epoch"] == self.h.hist.epoch for _, m in got))
        self.assertEqual(self.h.hist_metadata()["read"], last)

    def test_lower_or_refused_receipt_answers_only_its_sender(self):
        last = self.turns[-1]["id"]
        self.read(self.mac, last)
        for tid in (self.turns[1]["id"], 99999, True):
            got = self.read(self.phone, tid)
            self.assertEqual(got, [(11, {"t": "hist_read", "epoch": self.h.hist.epoch, "read": last})])
        self.assertEqual(self.read(self.phone, last, epoch=self.h.hist.epoch + 7),
                         [(11, {"t": "hist_read", "epoch": self.h.hist.epoch, "read": last})])
        self.assertEqual(self.h.hist_metadata()["read"], last)

    def test_offline_remote_aligns_on_reconnect(self):
        del self.h.sessions[13]
        last = self.turns[-1]["id"]
        self.assertEqual({cid for cid, _ in self.read(self.pad, last)}, {11, 12})
        mac = _ready(self.h, Phone(self.st, "Mac"), 14)
        mac.p33 = True
        self.sent.clear()
        asyncio.run(self.h.on_p33(mac, "hist_get", {"r": "abcdefghijklmnopqrstuv"}))
        self.assertEqual(self.sent[-1][1]["read"], last)

    def test_unpaired_or_pending_remote_gets_no_answer(self):
        self.phone.state = "pending"
        self.assertEqual(self.read(self.phone, self.turns[0]["id"]), [])
        self.phone.state = "ready"
        self.st.remove_device(self.phone.device)
        self.assertEqual(self.read(self.phone, self.turns[0]["id"]), [])
        self.assertEqual(self.h.hist_metadata()["read"], 0)


if __name__ == "__main__":
    unittest.main()
