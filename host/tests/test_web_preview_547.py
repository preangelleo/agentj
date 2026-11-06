import json
import unittest
import asyncio
from types import SimpleNamespace
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from agentj import web_preview, wire
from agentj.seaturl import seat_id


class State:
    def __init__(self):
        self.key = Ed25519PrivateKey.generate()
        self.device = bytes(range(32))
        self.public = wire.b64u(bytes(range(32, 64)))
        self.active = True
    def devices(self):
        return {wire.device_id(self.device): {"sk": self.public}} if self.active else {}
    def is_allowed(self, key):
        return self.active and key == self.device
    def signing_key(self):
        return self.key


class PreviewAttestation(unittest.TestCase):
    def setUp(self):
        self.st = State()
        self.channel = wire.b64u(bytes(range(16)))
        self.now = 1780000000
        self.m = dict(v=1, origin="https://m.agentj.app", seat=seat_id(self.channel),
                      manifestSha256="a" * 64, devicePublicKey=self.st.public,
                      nonce=wire.b64u(bytes(range(32))), expires=self.now + 60)
    def proof(self, obj=None, device=None, channel=None):
        return web_preview.attest(self.st, channel or self.channel, device or self.st.device,
                                  json.dumps(self.m if obj is None else obj, separators=(",", ":")), now=self.now)
    def test_current_paired_device_gets_domain_separated_metadata_only_signature(self):
        signature = self.proof()
        self.assertIsNotNone(signature)
        self.st.key.public_key().verify(wire.unb64u(signature), web_preview.LABEL + json.dumps(self.m, separators=(",", ":")).encode())
        with self.assertRaises(Exception):
            self.st.key.public_key().verify(wire.unb64u(signature), b"approval\n" + json.dumps(self.m, separators=(",", ":")).encode())
    def test_revoked_and_never_paired_devices_cannot_get_or_renew_proof(self):
        self.assertIsNotNone(self.proof())
        self.st.active = False
        self.assertIsNone(self.proof())
        self.st.active = True
        self.assertIsNone(self.proof(device=bytes(reversed(range(32)))))
    def test_wrong_seat_origin_device_expiry_and_metadata_rejected(self):
        for field, value in [("v", True), ("origin", "https://evil.invalid"), ("seat", "jqwauuda"),
                             ("devicePublicKey", wire.b64u(bytes(32))), ("manifestSha256", "x" * 64),
                             ("nonce", "short"), ("expires", self.now), ("expires", self.now + 61), ("expires", True)]:
            with self.subTest(field=field, value=value):
                self.assertIsNone(self.proof({**self.m, field: value}))
        self.assertIsNone(self.proof({**self.m, "body": "do not sign arbitrary text"}))
    def test_malformed_and_noncanonical_messages_rejected_without_writes(self):
        for message in [None, 1, "x" * 1025, "{}", "{", json.dumps(self.m)]:
            with self.subTest(message_type=type(message).__name__):
                self.assertIsNone(web_preview.attest(self.st, self.channel, self.st.device, message, now=self.now))


if __name__ == "__main__":
    unittest.main()

class PreviewDispatch(unittest.IsolatedAsyncioTestCase):
    async def test_actual_app_dispatch_ready_pending_revoked_and_stale(self):
        from agentj import serve
        st = State()
        channel = wire.b64u(bytes(range(16)))
        m = dict(v=1, origin="https://m.agentj.app", seat=seat_id(channel),
                 manifestSha256="a" * 64, devicePublicKey=st.public,
                 nonce=wire.b64u(bytes(range(32))), expires=int(__import__("time").time()) + 60)
        request = {"t": "web_preview_attest", "r": "1234567890abcdef", "message": json.dumps(m, separators=(",", ":"))}
        sent, closed = [], []
        async def send(s, obj): sent.append(obj)
        async def close(cid, why): closed.append(why)
        host = SimpleNamespace(st=st, channel=channel, sessions={}, send_app=send, close_cid=close)
        host.on_p33 = lambda s,t,obj: serve.Host.on_p33(host,s,t,obj)
        s = SimpleNamespace(cid=1, state="ready", pub=st.device, p33=True)
        host.sessions[1] = s
        await serve.Host._app(host, s, request)
        self.assertEqual(len(sent), 1)
        self.assertTrue(sent[0]["ok"])
        self.assertEqual(sent[0]["t"], "web_preview_proof")
        sent.clear()
        s.state = "pending"
        await serve.Host._app(host, s, request)
        self.assertEqual(sent, [])
        s.state = "ready"
        st.active = False
        await serve.Host._app(host, s, request)
        self.assertEqual(sent, [])
        self.assertEqual(closed, ["revoked"])
        st.active = True
        host.sessions.clear()
        await serve.Host._app(host, s, request)
        self.assertEqual(sent, [])
