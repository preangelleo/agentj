"""P204 (0.21, PROTOCOL §9): empty-signal Web Push by category — per-device kinds (审批 ask / 完成 reply / 告警 security),
kept across a re-subscribe; a silent 〔不回群〕 turn never pushes."""
import _hermetic  # noqa: F401
import asyncio
import json
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from cryptography.hazmat.primitives.asymmetric import ec

from agentj import serve, webpush
from agentj.serve import Host
from agentj.silent import MARKER


def a_sub():
    pub = ec.generate_private_key(ec.SECP256R1()).public_key()
    return {"t": "push_sub", "endpoint": "https://fcm.googleapis.com/fcm/send/abc",
            "p256dh": webpush.b64u(webpush._pub_bytes(pub)), "auth": webpush.b64u(b"0123456789abcdef")}


class St:
    def __init__(self, root):
        self.root = pathlib.Path(root)
        self.push_path = self.root / "push.json"
        self.logs = []

    def write_private(self, p, b):
        pathlib.Path(p).write_bytes(b)

    def log(self, ev, **kw):
        self.logs.append((ev, kw))

    def devices(self):
        return {"d1": {}, "d2": {}}

    def is_allowed(self, pub):
        return True


class Kinds(unittest.TestCase):
    def test_parse_and_wants(self):
        self.assertEqual(webpush.parse_kinds({"kinds": ["security", "ask"]}), ["ask", "security"])
        self.assertEqual(webpush.parse_kinds({"kinds": []}), [])
        for bad in ({"kinds": ["text"]}, {"kinds": "ask"}, {"kinds": ["ask"] * 4}, {}, None):
            self.assertIsNone(webpush.parse_kinds(bad), bad)
        self.assertTrue(webpush.wants({"endpoint": "x"}, "reply"))      # no choice yet = everything (as before P204)
        self.assertFalse(webpush.wants({"kinds": ["ask"]}, "reply"))
        self.assertTrue(webpush.wants({"kinds": ["ask"]}, "ask"))

    def test_payload_is_still_the_kind_only(self):
        self.assertEqual(json.loads(webpush.payload("reply")), {"k": "reply"})


class Messages(unittest.TestCase):
    def host(self, d):
        h = SimpleNamespace(st=St(d), push_generation={}, send_app=mock.AsyncMock())
        return h

    def app(self, h, dev, obj):
        s = SimpleNamespace(state="ready", device=dev, p33=True, pub=b"k", cid=1)
        asyncio.run(Host._app_frame(h, s, obj))

    def test_pref_is_stored_and_survives_a_resubscribe(self):
        with tempfile.TemporaryDirectory() as d:
            h = self.host(d)
            sub = a_sub()
            self.app(h, "d1", sub)
            self.app(h, "d1", {"t": "push_pref", "kinds": ["ask"]})
            self.assertEqual(webpush.load_subs(h.st)["d1"]["kinds"], ["ask"])
            self.app(h, "d1", sub)                                       # every connect re-sends the subscription
            self.assertEqual(webpush.load_subs(h.st)["d1"]["kinds"], ["ask"])
            self.app(h, "d1", {"t": "push_pref", "kinds": ["nonsense"]})
            self.assertEqual(webpush.load_subs(h.st)["d1"]["kinds"], ["ask"])
            self.assertIn("push_pref_refused", [e for e, _ in h.st.logs])
            self.app(h, "d2", {"t": "push_pref", "kinds": ["ask"]})    # no subscription: nothing to attach it to
            self.assertNotIn("d2", webpush.load_subs(h.st))
            self.app(h, "d1", {"t": "push_off"})
            self.assertEqual(webpush.load_subs(h.st), {})


class Notify(unittest.TestCase):
    def test_only_the_kinds_a_device_chose(self):
        with tempfile.TemporaryDirectory() as d:
            st = St(d)
            sub = webpush.parse_sub(a_sub())
            webpush.save_subs(st, {"d1": dict(sub, kinds=["ask", "security"]), "d2": dict(sub)})
            sent = []

            def fake(fn, key, s, kind, urgency):
                sent.append(kind)
                f = mock.Mock()
                f.add_done_callback = lambda cb: None
                return f
            h = SimpleNamespace(st=st, sessions={}, push_last={}, push_generation={}, push_key=object())
            h._push_key = lambda: h.push_key
            with mock.patch.object(serve, "in_daemon_thread", fake):
                Host.push_notify(h, "reply")
                self.assertEqual(sent, ["reply"])                         # d2 (no choice) only
                sent.clear()
                Host.push_notify(h, "ask")
                self.assertEqual(sorted(sent), ["ask", "ask"])


class Silent(unittest.TestCase):
    def test_a_silent_turn_never_pushes(self):
        h = SimpleNamespace(st=SimpleNamespace(log=mock.Mock()), cur_turn=3, hist=SimpleNamespace(get=lambda t: {"id": t}), turn_text=False,
                            hist_update=mock.Mock(return_value=[{"id": 3}]), emit=mock.Mock(), _remember=mock.Mock(),
                            _post=mock.Mock(), push_notify=mock.Mock())
        Host.agent_text(h, MARKER)
        self.assertFalse(h.turn_text, "the marker alone never marks the turn as one that pushes")
        h.push_notify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
