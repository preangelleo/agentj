"""P565 追加 A (华为 Pad 10-28 13:16): the system's fingerprint sheet sends the page to the background, the socket drops
(dev_gone), the page reconnects (resume_ok 1006) and hands in create()'s result — the host used to answer `no_offer`
because the offer lived on the old socket. Now an offer is bound to the device + its nonce, good for OFFER_TTL.

- a pk_reg handed in on the NEW socket for the OLD socket's offer is taken (challenge still checked);
- a new offer issued on the reconnect does not void the earlier one (the page answers whichever create() used);
- an expired offer, another device's offer, a used offer, a wrong challenge: still refused.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
import pathlib
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import passkey, serve, wire  # noqa: E402

from test_l1 import Phone, _ready  # noqa: E402
from test_p551_confirm_levels import Base  # noqa: E402


def cd_for(nonce: bytes) -> str:
    return wire.b64u(json.dumps({"type": "webauthn.create", "challenge": wire.b64u(nonce), "origin": "https://m.agentj.app"}).encode())


def fake_verify(obj, nonce):
    """verify_registration's challenge rule (the rest of the ceremony is covered by test_p57_passkey)."""
    passkey.client_data(passkey.b64(obj.get("cd"), passkey.MAX_CD, "cd"), "webauthn.create", nonce)
    return {"id": obj.get("id") or wire.b64u(os.urandom(16)), "pub": wire.b64u(os.urandom(77)), "alg": -7, "count": 0,
            "rp": "m.agentj.app", "at": int(time.time())}


class Resubmit(Base):
    def setUp(self):
        super().setUp()
        self.s.p33 = True
        p = mock.patch.object(passkey, "origin_allowed", return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def offer(self, s) -> bytes:
        asyncio.run(self.host._pk_offer(s))
        return wire.unb64u([x for _, x in self.sent if x["t"] == "pk_offer"][-1]["n"])

    def reconnect(self, cid=12):
        """dev_gone + resume: the same device on a new Session; the old one is gone from host.sessions."""
        self.host.sessions.pop(self.s.cid, None)
        s2 = _ready(self.host, self.phone, cid=cid)
        s2.p33 = True
        return s2

    def reg(self, s, nonce: bytes, cid: str | None = None) -> dict:
        n = len(self.sent)
        with mock.patch.object(passkey, "verify_registration", side_effect=fake_verify):
            asyncio.run(self.host._pk_reg(s, "pk_reg", {"t": "pk_reg", "cd": cd_for(nonce), **({"id": cid} if cid else {})}))
        return [x for _, x in self.sent[n:] if x["t"] == "pk_reg_res"][-1]

    def test_result_after_a_16_s_drop_is_taken_on_the_new_socket(self):
        n = self.offer(self.s)
        s2 = self.reconnect()
        with mock.patch.object(time, "monotonic", return_value=time.monotonic() + 16):
            res = self.reg(s2, n)
        self.assertTrue(res["ok"], res)
        self.assertIsNotNone(self.st.passkey_of_device(self.phone.did))
        self.assertNotIn(self.phone.did, self.host.pk_offers)        # the device's other offers end with the key

    def test_a_new_offer_on_the_reconnect_does_not_void_the_first(self):
        n1 = self.offer(self.s)
        s2 = self.reconnect()
        n2 = self.offer(s2)
        self.assertNotEqual(n1, n2)
        self.assertTrue(self.reg(s2, n1)["ok"])

    def test_an_expired_offer_is_refused(self):
        n = self.offer(self.s)
        s2 = self.reconnect()
        with mock.patch.object(time, "monotonic", return_value=time.monotonic() + passkey.OFFER_TTL + 5):
            res = self.reg(s2, n)
        self.assertEqual((res["ok"], res["why"]), (False, "expired"))
        self.assertIsNone(self.st.passkey_of_device(self.phone.did))

    def test_another_device_cannot_hand_in_this_devices_offer(self):
        n = self.offer(self.s)
        other = Phone(self.st)
        so = _ready(self.host, other, cid=31)
        so.p33 = True
        res = self.reg(so, n)
        self.assertFalse(res["ok"])
        self.assertIsNone(self.st.passkey_of_device(other.did))
        self.assertIn(self.phone.did, self.host.pk_offers)          # this device's offer is untouched

    def test_an_offer_is_used_once(self):
        n = self.offer(self.s)
        s2 = self.reconnect()
        with mock.patch.object(passkey, "verify_registration", side_effect=passkey.PasskeyError("bad", "att")):
            asyncio.run(self.host._pk_reg(s2, "pk_reg", {"t": "pk_reg", "cd": cd_for(n)}))
        res = self.reg(s2, n)
        self.assertEqual((res["ok"], res["why"]), (False, "expired"))

    def test_a_wrong_challenge_is_still_refused(self):
        self.offer(self.s)
        s2 = self.reconnect()
        res = self.reg(s2, os.urandom(32))
        self.assertFalse(res["ok"])
        self.assertIsNone(self.st.passkey_of_device(self.phone.did))

    def test_the_same_result_handed_in_twice_is_ok_and_changes_nothing(self):
        cid = wire.b64u(b"\x05" * 16)
        n = self.offer(self.s)
        self.assertTrue(self.reg(self.s, n, cid)["ok"])            # the answer was lost with the socket
        rec = dict(self.st.passkey_of_device(self.phone.did))
        s2 = self.reconnect()
        self.assertTrue(self.reg(s2, n, cid)["ok"])                # the page hands it in again
        self.assertEqual(self.st.passkey_of_device(self.phone.did), rec)
        res = self.reg(s2, n, wire.b64u(b"\x06" * 16))           # another key on a used offer: refused as before
        self.assertEqual((res["ok"], res["why"]), (False, "expired"))

    def test_the_old_socket_cannot_use_the_offer_again(self):
        n = self.offer(self.s)
        old = self.s
        s2 = _ready(self.host, self.phone, cid=12)          # the half-open earlier socket is still listed
        s2.p33 = True
        self.assertTrue(self.reg(s2, n)["ok"])
        self.assertIsNone(old.pk_offer, "review S3: the same nonce ends on every socket of the device")

    def test_a_direct_offer_ends_when_another_remote_got_a_key_meanwhile(self):
        n = self.offer(self.s)
        s2 = self.reconnect()
        with mock.patch.object(self.host.pkpeer, "direct_ok", return_value=False):
            res = self.reg(s2, n)
        self.assertEqual((res["ok"], res["why"]), (False, "peer_required"))
        self.assertIsNone(self.st.passkey_of_device(self.phone.did))

    def test_offers_per_device_are_bounded(self):
        for _ in range(serve.PK_OFFERS + 3):
            self.offer(self.s)
        self.assertEqual(len(self.host.pk_offers[self.phone.did]), serve.PK_OFFERS)


if __name__ == "__main__":
    unittest.main()
