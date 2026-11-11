"""0.30.7 (preview-frset): a record taken over by the SAME phone's other identity (passkey / renewal-ticket restore — iOS
Safari vs the Home Screen app) is remembered as `replaced` + `moved`, so that page can say "two copies on this phone".
A full computer evicting its stalest remote, or the same browser re-pairing, stays plain `replaced` (no hint)."""
import _hermetic  # noqa: F401,I001
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import wire  # noqa: E402
from agentj.state import MAX_DEVICES, State  # noqa: E402


class RemovedMoved(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = State(pathlib.Path(self.tmp.name) / "s")
        self.st.init(relay="ws://127.0.0.1:1")

    def tearDown(self):
        self.tmp.cleanup()

    def test_passkey_restore_marks_moved(self):
        pub = os.urandom(32)
        self.st.pair_device(pub, "iPhone Safari", os.urandom(32))
        old = wire.device_id(pub)
        d = self.st.devices()
        d[old]["pk"] = {"id": "cred-1"}
        self.st.write_private(self.st.devices_path, __import__("json").dumps(d).encode())
        new = self.st.passkey_restore(old, "cred-1", os.urandom(32), os.urandom(32))
        self.assertIsNotNone(new)
        self.assertEqual(self.st.removed_why(old), "replaced")
        self.assertTrue(self.st.removed_moved(old))

    def test_ticket_restore_marks_moved(self):
        pub = os.urandom(32)
        self.st.pair_device(pub, "iPhone Home Screen", os.urandom(32))
        old = wire.device_id(pub)
        self.assertTrue(self.st.set_ticket(pub, {"i": "tid-1"}))
        new = self.st.ticket_restore(old, "tid-1", os.urandom(32), os.urandom(32), {"i": "tid-2"})
        self.assertIsNotNone(new)
        self.assertTrue(self.st.removed_moved(old))

    def test_eviction_and_same_browser_are_not_moved(self):
        for i in range(MAX_DEVICES):
            self.st.pair_device(os.urandom(32), f"r{i}")
        got = self.st.pair_device(os.urandom(32), "new", evict=True)
        self.assertEqual(self.st.removed_why(got["evicted"]["id"]), "replaced")
        self.assertFalse(self.st.removed_moved(got["evicted"]["id"]))
        iid = wire.b64u(b"\x02" * 16)
        st2 = State(pathlib.Path(self.tmp.name) / "s2"); st2.init(relay="ws://127.0.0.1:1")
        a = st2.pair_device(os.urandom(32), "x", iid=iid)["device"]
        st2.pair_device(os.urandom(32), "x", iid=iid)
        self.assertEqual(st2.removed_why(a), "replaced")
        self.assertFalse(st2.removed_moved(a))
        self.assertFalse(self.st.removed_moved("nobody"))

    def test_serve_sends_moved_only_when_set(self):
        src = (pathlib.Path(__file__).resolve().parents[1] / "agentj" / "serve.py").read_text()
        self.assertIn('s.gone_moved = s.gone == "replaced" and self.st.removed_moved(did)', src)
        self.assertIn('{"t": "removed", "why": s.gone, **({"moved": True} if s.gone_moved else {})}', src)


if __name__ == "__main__":
    unittest.main()
