"""P0325: approval cards are accepted only after transport acceptance and queue per remote."""
import _hermetic  # noqa: F401
import asyncio
import json
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import approval_delivery, elevate


class _State:
    def __init__(self, root):
        self.root = pathlib.Path(root)

    def is_allowed(self, pub):
        return pub.startswith(b"allowed")


class Delivery(unittest.TestCase):
    def test_partial_delivery_copy_is_explicit(self):
        zh, en = elevate._secret_card_delivery_copy("partial")
        self.assertIn("部分设备已送达", zh)
        self.assertIn("其余排队/失败", zh)
        self.assertIn("Partially delivered", en)
        self.assertIn("queued or failed", en)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.host = SimpleNamespace(st=_State(self.tmp.name), sessions={}, _approvers=lambda: {"phone", "web"})
        self.host.st.devices = lambda: {d: {"device": d, "pub": __import__('agentj.wire', fromlist=['b64u']).b64u(s.pub),
                                            "sk": __import__('agentj.wire', fromlist=['b64u']).b64u(s.sign_pub)}
                                        for d, s in self.host.sessions.items()}
        self.host.st.sign_key = lambda d: self.host.sessions[d].sign_pub
        self.sent = []

        async def send(session, obj):
            self.sent.append((session.device, obj))
            return True

        self.host.send_app = send

    def tearDown(self):
        self.tmp.cleanup()

    def session(self, device, online=True):
        s = SimpleNamespace(device=device, state="ready" if online else "gone",
                            pub=b"allowed-noise-" + device.encode(), sign_pub=b"sign-" + device.encode())
        self.host.sessions[device] = s
        return s

    def test_fans_out_to_two_online_remotes(self):
        self.session("phone")
        self.session("web")
        result = asyncio.run(approval_delivery.deliver(self.host, lambda s: {"t": "ask", "id": "r1"}, rid="r1"))
        self.assertEqual(result["accepted"], {"phone", "web"})
        self.assertEqual(result["queued"], set())
        self.assertTrue(result["complete"])
        self.assertEqual({d for d, _ in self.sent}, {"phone", "web"})

    def test_offline_is_durable_and_replayed_on_reconnect(self):
        self.session("phone", online=False)
        result = asyncio.run(approval_delivery.deliver(self.host, lambda s: {"t": "ask", "id": "r2"}, rid="r2"))
        self.assertEqual(result["accepted"], set())
        self.assertEqual(result["queued"], {"phone", "web"})
        path = pathlib.Path(self.tmp.name) / "approval-card-delivery.json"
        self.assertEqual({r["device"] for r in json.loads(path.read_text())}, {"phone", "web"})
        self.host.sessions["phone"].state = "ready"
        self.host.sessions["web"] = SimpleNamespace(device="web", state="ready", pub=b"allowed-web", sign_pub=b"sign-web")
        self.host.asks = {}
        # A real open ask is not needed here: replay's stale-row cleanup must not
        # claim delivery. The queue remains until its card can be reconstructed.
        self.assertEqual(asyncio.run(approval_delivery.replay(self.host, self.host.sessions["phone"])), 0)
        self.assertEqual(json.loads(path.read_text()), [{"id": "r2", "device": "web"}])

    def test_transport_failure_is_not_success_and_is_queued(self):
        self.session("phone")
        self.session("web")

        async def broken(session, obj):
            if session.device == "phone":
                raise ConnectionError("transport")
            return None

        self.host.send_app = broken
        result = asyncio.run(approval_delivery.deliver(self.host, lambda s: {"t": "ask", "id": "r3"}, rid="r3"))
        self.assertEqual(result["accepted"], set())
        self.assertEqual(result["failed"], {"phone", "web"})
        self.assertFalse(result["complete"])
        self.assertEqual({r["device"] for r in json.loads((pathlib.Path(self.tmp.name) / "approval-card-delivery.json").read_text())}, {"phone", "web"})


if __name__ == "__main__":
    unittest.main()

class ReplayGuards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.host = SimpleNamespace(st=_State(self.tmp.name), sessions={}, asks={}, elevate=SimpleNamespace(cards={}),
                                    _ask_msg=lambda ask, s: {'t': 'ask', 'id': ask.rid})
        self.session = SimpleNamespace(device='phone', state='ready', pub=b'allowed-phone')
        self.host.sessions['phone'] = self.session
        self.host.st.devices = lambda: {'phone': {'sign_pub': b'other-key', 'device': 'phone'}}
        self.host.send_app = AsyncMock(return_value=True)
    def tearDown(self): self.tmp.cleanup()
    def test_replay_rejects_sign_key_mismatch(self):
        pathlib.Path(self.tmp.name, 'approval-card-delivery.json').write_text(json.dumps([{'id':'r','device':'phone'}]))
        async def run():
            self.host.elevate.cards['r'] = {'fut': asyncio.get_running_loop().create_future(), 'deadline': 10**30}
            self.assertEqual(await approval_delivery.replay(self.host, self.session), 0)
        asyncio.run(run())
        self.host.send_app.assert_not_awaited()

    def test_replay_single_frame_revoke_between_checks_does_not_send(self):
        """A revoke observed by the final, send-lock check blocks one-frame replay."""
        pathlib.Path(self.tmp.name, 'approval-card-delivery.json').write_text(
            json.dumps([{'id': 'r', 'device': 'phone'}]))
        self.session.sign_pub = b'sign-phone'
        self.host.st.sign_key = lambda d: self.session.sign_pub
        calls = 0
        valid = {'device': 'phone', 'pub': __import__('agentj.wire', fromlist=['b64u']).b64u(self.session.pub),
                 'sk': __import__('agentj.wire', fromlist=['b64u']).b64u(self.session.sign_pub)}
        def devices():
            nonlocal calls
            calls += 1
            return {'phone': valid} if calls == 1 else {}
        self.host.st.devices = devices
        self.host.elevate.card_msg = lambda card, session: {'t': 'elev', 'id': 'r'}
        async def run():
            fut = asyncio.get_running_loop().create_future()
            self.host.elevate.cards['r'] = {'fut': fut, 'deadline': 10**30}
            return await approval_delivery.replay(self.host, self.session)
        self.assertEqual(asyncio.run(run()), 0)
        self.host.send_app.assert_not_awaited()
