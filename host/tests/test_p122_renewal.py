"""P122 (0.17.4, PROTOCOL §12.1): the renewal ticket — "pair once, stay signed in". A phone whose browser deleted the page's
storage (Safari's 7-day rule) proves "the same phone" with the HMAC proof the web origin computes from its HttpOnly cookie.

Unit: the challenge / proof vectors shared with web/test/p122.test.mjs (page + worker.ts), the shape checks. Host level (the
P57 harness): a pairing issues a ticket; a ticket restore replaces the SAME record, rotates the ticket in the same write and
sends the new one after `ready`; the old ticket, a revoked record, a proof for another key, a replayed nonce, a stale ts are
refused; a passkey restore replaces the ticket; rt_req is rate-limited; the secret is never logged or reported."""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import json
import os
import pathlib
import sys
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import cloud, passkey, renewal, serve, wire  # noqa: E402
from agentj.noise import Keypair  # noqa: E402
from test_p57_passkey import Authenticator, HostLevel as P57HostLevel  # noqa: E402

# Shared with web/test/p122.test.mjs: page (renew.js restoreChallenge) + Worker (worker.ts rtProof) + host must agree.
VEC_X, VEC_ED, VEC_TS, VEC_NONCE = bytes(range(32)), bytes(range(32, 64)), 1790000000000, bytes(range(64, 80))
VEC_KEY = bytes(range(200, 232))
VEC_CHALLENGE = "6ede7e3f42f0d64a07069969be654eaf19016e570df81a044a93d1090bb10e4d"
VEC_PROOF = "07517785563f55b0902df406e79d098f15b341e9bb47b23b4cf36a485a76cda1"


class Vectors(unittest.TestCase):
    def test_challenge_and_proof(self):
        c = renewal.restore_challenge(VEC_X, VEC_ED, VEC_TS, VEC_NONCE)
        self.assertEqual(c.hex(), VEC_CHALLENGE)
        self.assertNotEqual(c, passkey.restore_challenge(VEC_X, VEC_ED, VEC_TS, VEC_NONCE), "own label: never a passkey challenge")
        self.assertEqual(renewal.proof(VEC_KEY, c).hex(), VEC_PROOF)

    def test_new_ticket_and_shape(self):
        t = renewal.new_ticket()
        self.assertEqual((len(wire.unb64u(t["i"])), len(wire.unb64u(t["k"]))), (16, 32))
        self.assertNotEqual(t["k"], renewal.new_ticket()["k"])
        good = {"i": t["i"], "p": wire.b64u(os.urandom(32)), "uh": wire.b64u(os.urandom(50)), "ts": 1, "nonce": wire.b64u(os.urandom(16))}
        self.assertEqual(renewal.restore_fields(good)["i"], t["i"])
        for bad in ({"ts": "1"}, {"p": wire.b64u(os.urandom(31))}, {"i": wire.b64u(os.urandom(15))}, {"nonce": "x"}, {"uh": ""}):
            with self.assertRaises(passkey.PasskeyError):
                renewal.restore_fields({**good, **bad})


class HostLevel(P57HostLevel):
    def ticketed(self):
        """Phone A, paired, holding a ticket (as after a pairing)."""
        kp, skpub, did = self.paired()
        rt = renewal.new_ticket()
        self.assertTrue(self.st.set_ticket(kp.pub, rt))
        return kp, did, rt

    def rt_restore(self, cid, rt, kp=None, *, chal_kp=None, ts=None, nonce=None, uh=None, key=None, flag="rt"):
        """A context with no stored keys: new key + approval key, `rt:1` RESUME, hello, then one rt_restore."""
        kp = kp or Keypair.generate()
        sk = os.urandom(32)
        s = self.handshake(cid, kp, {"v": 1, "sk": wire.b64u(sk), flag: 1})
        self.assertEqual((s.mode, s.state), ("pk", "hello"))
        self.run_(self.host._app(s, {"t": "hello", "caps": ["p33"]}))
        self.assertEqual(self.to(cid), [], "nothing before the rt_restore")
        ts = int(time.time() * 1000) if ts is None else ts
        nonce = nonce or os.urandom(16)
        ch = renewal.restore_challenge((chal_kp or kp).pub, sk, ts, nonce)
        p = renewal.proof(key or wire.unb64u(rt["k"]), ch)
        uh = uh if uh is not None else passkey.user_handle(0, self.host.channel, self.host.kp.pub)
        self.run_(self.host._app(s, {"t": "rt_restore", "i": rt["i"], "p": wire.b64u(p), "uh": wire.b64u(uh), "ts": ts,
                                     "nonce": wire.b64u(nonce)}))
        return kp, sk, self.to(cid)

    # -------------------------------------------------------------- issue
    def test_pairing_issues_a_ticket_inside_the_record(self):
        pub = os.urandom(32)
        s = serve.Session(cid=7, state="pending", device=wire.device_id(pub), name="phone", pub=pub, h=os.urandom(32), p33=True)
        s.deadline = time.monotonic() + 60
        self.host.sessions[7] = s

        async def go():
            self.host.pairing = serve.Pairing(os.urandom(16), os.urandom(32), time.time() + 60, time.monotonic() + 60, ctl=None, cid=7)
            return await self.host.decide(self.host.pairing, wire.safety_code(s.h), "test-passphrase-P57")
        self.run_(go())
        msgs = self.to(7)
        self.assertEqual([o["t"] for o in msgs], ["approved", "pk_offer", "rt"])
        rt = self.st.devices()[s.device]["rt"]
        self.assertEqual((msgs[-1]["i"], msgs[-1]["k"]), (rt["i"], rt["k"]))
        self.assertNotIn(rt["k"], self.st.log_path.read_text(), "host.log never holds the secret")
        self.assertNotIn(rt["i"], self.st.log_path.read_text(), "…nor the ticket id")
        reported = json.dumps(cloud.report_devices(self.st, set()))
        self.assertNotIn(rt["k"], reported, "the Dashboard report carries no ticket")

    def test_rt_req_reissues_at_most_once_a_minute(self):
        kp, did, rt = self.ticketed()
        s = serve.Session(cid=3, state="ready", device=did, pub=kp.pub, p33=True)
        self.host.sessions[3] = s
        self.run_(self.host._app(s, {"t": "rt_req"}))
        new = self.st.devices()[did]["rt"]
        self.assertNotEqual(new["k"], rt["k"], "a new ticket replaces the old one")
        self.assertEqual(self.to(3)[-1], {"t": "rt", "i": new["i"], "k": new["k"]})
        self.run_(self.host._app(s, {"t": "rt_req"}))
        self.assertEqual(len(self.to(3)), 1, "a second rt_req within a minute changes nothing")
        self.assertEqual(self.st.devices()[did]["rt"], new)

    # -------------------------------------------------------------- restore
    def test_restore_replaces_the_same_record_and_rotates(self):
        a = Authenticator()
        kpA, did, rt = self.ticketed()
        self.assertTrue(self.st.set_passkey(kpA.pub, {"id": wire.b64u(a.id), "alg": -7, "spki": wire.b64u(a.spki()), "rp": "m.agentj.app", "at": 1}))
        self.host.sessions[5] = serve.Session(cid=5, state="ready", device=did, pub=kpA.pub, p33=True)
        kpB, skB, got = self.rt_restore(9, rt)
        self.assertEqual([m["t"] for m in got], ["pk_ok", "ready", "rt"], "a passkey already exists: no new offer")
        idB = wire.device_id(kpB.pub)
        self.assertEqual(list(self.st.devices()), [idB], "still exactly one remote")
        rec = self.st.devices()[idB]
        self.assertEqual((rec["pub"], rec["sk"], rec["name"], rec["pk"]["id"]), (wire.b64u(kpB.pub), wire.b64u(skB), "网页 · iOS Safari", wire.b64u(a.id)))
        self.assertNotEqual(rec["rt"]["i"], rt["i"], "rotated in the same write")
        self.assertEqual((got[-1]["i"], got[-1]["k"]), (rec["rt"]["i"], rec["rt"]["k"]))
        self.assertNotIn(5, self.host.sessions, "A's live session ended")
        self.assertEqual(self.st.removed_why(did), "replaced")
        log = self.st.log_path.read_text()
        self.assertIn('"ev": "rt_restore"', log)
        for secret in (rt["k"], rt["i"], rec["rt"]["k"], rec["rt"]["i"]):
            self.assertNotIn(secret, log)
        # the used ticket is dead (a copied cookie); nothing changes
        self.sent.clear()
        _, _, got = self.rt_restore(11, rt)
        self.assertEqual(got, [{"t": "pk_fail", "why": "unknown"}])
        self.assertEqual(list(self.st.devices()), [idB])
        # B resumes normally afterwards (plain RESUME)
        self.assertEqual(self.resume_plain(12, kpB)[0]["t"], "ready")

    def test_restore_without_passkey_offers_one(self):
        _, _, rt = self.ticketed()
        _, _, got = self.rt_restore(9, rt)
        self.assertEqual([m["t"] for m in got], ["pk_ok", "ready", "rt", "pk_offer"])

    def test_revoked_record_kills_the_ticket(self):
        _, did, rt = self.ticketed()
        self.run_(self.host.revoke(did))
        self.assertIsNone(self.st.ticket_of(rt["i"]))
        _, _, got = self.rt_restore(9, rt)
        self.assertEqual(got, [{"t": "pk_fail", "why": "unknown"}])
        self.assertEqual(self.st.devices(), {})
        self.assertNotIn(rt["i"], self.st.removed_path.read_text())

    def test_eviction_iid_replace_and_unbind_take_the_ticket(self):
        kp, did, rt = self.ticketed()
        self.st.pair_device(os.urandom(32), "again", iid=wire.b64u(b"\x01" * 16))   # same browser re-paired
        self.assertIsNone(self.st.ticket_of(rt["i"]))

    def test_refusals(self):
        kp, did, rt = self.ticketed()
        cases = [
            ({"chal_kp": Keypair.generate()}, "bad"),                 # a proof made for another key (stolen / relayed)
            ({"key": os.urandom(32)}, "bad"),                         # not this ticket's secret
            ({"ts": int(time.time() * 1000) - 6 * 60 * 1000}, "expired"),
            ({"uh": passkey.user_handle(0, wire.b64u(os.urandom(16)), self.host.kp.pub)}, "bad"),   # another computer's handle
        ]
        for i, (kw, why) in enumerate(cases):
            _, _, got = self.rt_restore(20 + i, rt, **kw)
            self.assertEqual(got, [{"t": "pk_fail", "why": why}], kw)
        nonce = os.urandom(16)
        self.rt_restore(30, rt, nonce=nonce, key=os.urandom(32))
        _, _, got = self.rt_restore(31, rt, nonce=nonce)
        self.assertEqual(got, [{"t": "pk_fail", "why": "bad"}], "a nonce is used once")
        self.assertEqual(list(self.st.devices()), [did], "nothing changed")
        self.assertEqual(self.st.devices()[did]["rt"], rt)

    def test_passkey_restore_replaces_the_ticket(self):
        a = Authenticator()
        kp, did, rt = self.ticketed()
        self.assertTrue(self.st.set_passkey(kp.pub, {"id": wire.b64u(a.id), "alg": -7, "spki": wire.b64u(a.spki()), "rp": "m.agentj.app", "at": 1}))
        kpB, _, got = self.restore(9, a)
        self.assertEqual([m["t"] for m in got], ["pk_ok", "ready", "rt"])
        rec = self.st.devices()[wire.device_id(kpB.pub)]
        self.assertNotEqual(rec["rt"]["i"], rt["i"], "the old context's ticket died with it")
        self.assertIsNone(self.st.ticket_of(rt["i"]))

    def test_listed_key_with_rt_flag_just_resumes(self):
        kp, did, rt = self.ticketed()
        s = self.handshake(9, kp, {"v": 1, "sk": wire.b64u(os.urandom(32)), "rt": 1})
        self.assertEqual(s.mode, "resume")


if __name__ == "__main__":
    unittest.main()
