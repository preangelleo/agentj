"""P547 §A (pkpeer.py, PROTOCOL "P547"): a remote's Face ID key set up with ANOTHER remote's approval.

Host level with the P57 harness (send_app captured, real WebAuthn byte layouts from test_p57_passkey.Authenticator): the first
device gets a direct offer; a device whose account already has a keyed remote needs that remote's assertion (its own key, over
the pk_peer challenge) — then exactly one peer offer replaces D's record without the old key; wrong / missing assertions,
denial, expiry, cancellation, the rate and open limits, the approver set (never D itself, never D's own credential), cards for
an approver that connects later and the approved offer for D after a reconnect. Review M1: every offer path (pk_offer_req,
after a pairing, after a restore) follows the one rule — direct only while no other device holds a key. Review minor 4: only
a device whose key it registered itself approves (a passkey-restored record does not)."""
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
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ed25519  # noqa: E402

import test_p57_passkey as p57  # noqa: E402
from agentj import passkey, pkpeer, serve, wire  # noqa: E402
from agentj.noise import Keypair  # noqa: E402
from agentj.state import State  # noqa: E402


class Peer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = State(pathlib.Path(self.tmp.name) / "s")
        self.st.init(relay="ws://127.0.0.1:1")
        self.host = serve.Host(self.st, events="jsonl", read_stdin=False)
        self.sent = []

        async def send_app(s, obj):
            if self.host.sessions.get(s.cid) is not s:
                return False
            self.sent.append((s.cid, obj))
            return True
        self.host.send_app = send_app
        self.loop = asyncio.new_event_loop()
        self.cid = 0

    def tearDown(self):
        for t in asyncio.all_tasks(self.loop):
            t.cancel()
        pending = asyncio.all_tasks(self.loop)
        if pending:
            self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        self.loop.run_until_complete(self.loop.shutdown_default_executor())
        self.loop.close()
        self.tmp.cleanup()

    def run_(self, coro):
        return self.loop.run_until_complete(coro)

    def to(self, s, t=None):
        return [o for c, o in self.sent if c == s.cid and (t is None or o["t"] == t)]

    def device(self, name, auth=None, online=True):
        kp = Keypair.generate()
        sk = ed25519.Ed25519PrivateKey.generate()
        pub = sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        did = self.st.pair_device(kp.pub, name, pub)["device"]
        if auth:
            self.assertTrue(self.st.set_passkey(kp.pub, p57.pk_record(auth)))
        s = None
        if online:
            s = self.connect(kp, did)
        return kp, did, s

    def connect(self, kp, did):
        self.cid += 1
        s = serve.Session(cid=self.cid, state="ready", device=did, pub=kp.pub, p33=True, name=self.st.devices()[did]["name"])
        self.host.sessions[self.cid] = s
        return s

    def req(self, s):
        self.run_(self.host._app(s, {"t": "pk_peer_req", "r": "r1"}))
        return self.to(s, "pk_peer_res")[-1]

    @staticmethod
    def fa(auth, challenge):
        a = auth.assert_(challenge, b"u", ts=0, nonce=b"\0" * 16)
        return {k: a[k] for k in ("id", "cd", "ad", "sig")}

    def answer(self, s, auth, ask, ok=True, challenge=None):
        ch = challenge or wire.unb64u(ask["fa_ch"])
        self.run_(self.host._app(s, {"t": "pk_peer_answer", "id": ask["id"], "ok": ok,
                                     **({"fa": self.fa(auth, ch)} if ok and auth else {})}))
        return self.to(s, "pk_peer_answer_res")[-1]

    # ------------------------------------------------------------------ the rule
    def test_first_device_gets_a_direct_offer(self):
        kp, did, s = self.device("Mac Chrome")
        res = self.req(s)
        self.assertEqual(res, {"t": "pk_peer_res", "r": "r1", "ok": True, "mode": "direct"})
        offer = self.to(s, "pk_offer")[-1]
        self.assertNotIn("peer", offer)
        self.assertNotIn("replace", offer)
        a = p57.Authenticator()
        self.run_(self.host._app(s, a.register(wire.unb64u(offer["n"]))))
        self.assertEqual(self.to(s, "pk_reg_res")[-1], {"t": "pk_reg_res", "ok": True})

    def test_lone_keyed_device_has_no_approver(self):
        _, _, s = self.device("Mac Chrome", p57.Authenticator())
        self.device("iPad", None)                        # another remote, but without a key
        # P555: never a dead end — the request opens with nobody online; the Agent J host's terminal can approve it
        res = self.req(s)
        self.assertEqual((res["ok"], res["mode"], res["online"], res["host"]), (True, "peer", 0, 1))

    def test_replace_lost_key_with_peer_approval(self):
        old, phone_key, other_key = p57.Authenticator(), p57.Authenticator(), p57.Authenticator()
        kpD, D, sD = self.device("Mac Chrome", old)       # #317: D's recorded key is gone with a reinstall
        _, B, sB = self.device("iPhone", phone_key)
        _, C, sC = self.device("iPad", other_key)
        res = self.req(sD)
        self.assertEqual((res["ok"], res["mode"], res["online"]), (True, "peer", 2))
        self.assertEqual(res["code"], pkpeer.code(res["id"], D, wire.b64u(kpD.pub)))
        self.assertRegex(res["code"], r"^\d{6}$")
        self.assertEqual(self.to(sD, "pk_offer"), [], "no offer before the approval")
        ask = self.to(sB, "pk_peer_ask")[-1]
        digest = pkpeer.digest(D, "Mac Chrome", res["code"], ask["sign_pub"])
        self.assertEqual(ask, {"t": "pk_peer_ask", "id": res["id"], "device": D, "name": "Mac Chrome", "at": ask["at"],
                               "exp": res["exp"], "code": res["code"], "sign_pub": wire.b64u(self.st.sign_key(D)), "fa": wire.b64u(phone_key.id),
                               "fa_ch": wire.b64u(passkey.elevate_challenge(self.host.channel, B, res["id"], "pk_peer", res["id"], digest))})
        self.assertEqual(self.to(sD, "pk_peer_ask"), [], "D never gets its own card")
        self.assertEqual(self.answer(sB, phone_key, ask), {"t": "pk_peer_answer_res", "id": res["id"], "ok": True})
        self.assertEqual(self.to(sC, "pk_peer_close"), [{"t": "pk_peer_close", "id": res["id"]}])
        self.assertEqual(self.to(sD, "pk_peer_done")[-1], {"t": "pk_peer_done", "id": res["id"], "ok": True, "by": "iPhone"})
        offer = self.to(sD, "pk_offer")[-1]
        self.assertEqual(offer["peer"], res["id"])
        self.assertNotIn("replace", offer, "the page must not ask for the old key")
        new = p57.Authenticator()
        self.run_(self.host._app(sD, new.register(wire.unb64u(offer["n"]))))     # no replace_fa
        self.assertEqual(self.to(sD, "pk_reg_res")[-1], {"t": "pk_reg_res", "ok": True})
        rec = self.st.devices()[D]
        self.assertEqual(rec["pk"]["id"], wire.b64u(new.id))
        self.assertTrue(rec.get("pk_ancestors"), "the old key stays in the lineage (credential_revoke)")
        # one approval, one registration: the same request cannot be used again
        self.assertFalse(self.host.pkpeer.approved(D, res["id"]))
        before = len(self.to(sD, "pk_offer"))
        self.run_(self.host._pk_offer(sD, peer=res["id"]))
        self.assertEqual(len(self.to(sD, "pk_offer")), before, "a used approval makes no offer")
        n2 = os.urandom(32)                                 # … and even an offer the page kept is refused at pk_reg
        sD.pk_offer = (n2, time.monotonic(), res["id"])
        self.run_(self.host._app(sD, p57.Authenticator().register(n2)))
        self.assertEqual(self.to(sD, "pk_reg_res")[-1], {"t": "pk_reg_res", "ok": False, "why": "expired"})
        self.assertEqual(self.st.devices()[D]["pk"]["id"], wire.b64u(new.id))
        # the record: approvals.log (passkey proof as hashes), notice to everyone, no credential id in any log
        line = [json.loads(x) for x in self.st.approvals_path.read_text().splitlines()][-1]
        self.assertEqual((line["tool"], line["decision"], line["reason"], line["device"]), ("pk_peer", "allow", "pk_peer", B))
        self.assertEqual(set(line["fa"]), {"id", "cd", "ad", "sig"})
        for path in (self.st.approvals_path, self.st.log_path):
            text = path.read_text()
            self.assertNotIn(wire.b64u(phone_key.id), text)
            self.assertNotIn(wire.b64u(new.id), text)
        from agentj import approvals
        self.assertEqual(approvals.check_record(self.st, line), "unsigned", "a passkey proof, never shown as a bad signature")
        self.assertTrue(any(o["t"] == "hist_turn" for o in self.to(sC)), "every remote gets the notice")

    def test_wrong_or_missing_assertion_is_refused(self):
        kpD, D, sD = self.device("Mac Chrome", p57.Authenticator())
        bkey = p57.Authenticator()
        _, B, sB = self.device("iPhone", bkey)
        res = self.req(sD)
        ask = self.to(sB, "pk_peer_ask")[-1]
        self.assertEqual(self.answer(sB, None, ask)["why"], "passkey_failed", "ok without fa")
        self.assertEqual(self.answer(sB, p57.Authenticator(), ask)["why"], "passkey_failed", "another key")
        self.assertEqual(self.answer(sB, bkey, ask, challenge=os.urandom(32))["why"], "passkey_failed", "another challenge")
        self.assertEqual(self.answer(sD, bkey, ask)["why"], "not_approver", "D cannot approve itself")
        self.assertEqual(self.to(sD, "pk_offer"), [])
        self.assertFalse(self.host.pkpeer.approved(D, res["id"]))
        # a forged peer id in an offer is worthless: nothing this host approved
        self.host.pkpeer.reqs[res["id"]].state = "open"
        self.run_(self.host._pk_offer(sD, peer=res["id"]))
        self.assertEqual(self.to(sD, "pk_offer"), [], "no offer for a request nobody approved")
        n = os.urandom(32)
        sD.pk_offer = (n, time.monotonic(), res["id"])
        self.run_(self.host._app(sD, p57.Authenticator().register(n)))
        self.assertEqual(self.to(sD, "pk_reg_res")[-1]["ok"], False)
        # M1: another device holds a key, so a plain offer is not made at all (the page starts the peer flow)
        self.run_(self.host._app(sD, {"t": "pk_offer_req"}))
        self.assertEqual(self.to(sD, "pk_offer"), [])
        self.assertEqual(self.to(sD, "pk_reg_res")[-1], {"t": "pk_reg_res", "ok": False, "why": "peer_required"})

    def test_no_direct_offer_while_another_device_has_a_key(self):
        """Review M1 (model §2): B (no key) next to keyed A — pk_offer_req, the post-pairing and post-restore offers are
        refused; after A's approval B gets exactly the peer offer; A alone (no key) gets a direct one."""
        akey = p57.Authenticator()
        _, A, sA = self.device("iPhone", akey)
        _, B, sB = self.device("Mac Chrome")
        self.run_(self.host._app(sB, {"t": "pk_offer_req"}))
        self.assertEqual(self.to(sB, "pk_reg_res")[-1], {"t": "pk_reg_res", "ok": False, "why": "peer_required"})
        self.run_(self.host._pk_offer(sB))                     # what pairing / a ticket or passkey restore call
        self.assertEqual(self.to(sB, "pk_offer"), [], "never a direct offer")
        self.assertEqual(len(self.to(sB, "pk_reg_res")), 1, "an automatic offer is silently not made")
        res = self.req(sB)
        self.assertEqual((res["ok"], res["mode"], res["online"]), (True, "peer", 1))
        self.answer(sA, akey, self.to(sA, "pk_peer_ask")[-1])
        offer = self.to(sB, "pk_offer")[-1]
        self.assertEqual(offer["peer"], res["id"])
        bkey = p57.Authenticator()
        self.run_(self.host._app(sB, bkey.register(wire.unb64u(offer["n"]))))
        self.assertEqual(self.to(sB, "pk_reg_res")[-1], {"t": "pk_reg_res", "ok": True})
        self.assertEqual(self.st.devices()[B]["pk"]["id"], wire.b64u(bkey.id))

    def test_lone_device_without_a_key_gets_the_direct_offer_request(self):
        _, A, sA = self.device("iPhone")
        self.device("iPad")                                     # another remote, no key either
        self.run_(self.host._app(sA, {"t": "pk_offer_req"}))
        offer = self.to(sA, "pk_offer")[-1]
        self.assertNotIn("peer", offer)
        self.run_(self.host._app(sA, p57.Authenticator().register(wire.unb64u(offer["n"]))))
        self.assertEqual(self.to(sA, "pk_reg_res")[-1], {"t": "pk_reg_res", "ok": True})
        self.assertEqual(self.st.devices()[A]["pk"]["dev"], A, "the record notes the device that registered it")

    def test_restored_record_is_not_an_approver(self):
        """Review minor 4: a passkey restore moves A's record to a new device id; its key lives on the old device, so the
        new id is not asked (it would pop the browser's QR sheet). A legacy record without `dev` still approves."""
        akey = p57.Authenticator()
        kpA, A, _ = self.device("iPhone", akey, online=False)
        _, D, sD = self.device("Mac Chrome")
        kpA2, sk2 = Keypair.generate(), ed25519.Ed25519PrivateKey.generate()
        A2 = self.st.passkey_restore(A, wire.b64u(akey.id), kpA2.pub,
                                     sk2.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
        self.assertTrue(A2 and A2 != A)
        self.assertEqual(self.st.devices()[A2]["pk"]["dev"], A)
        sA2 = self.connect(kpA2, A2)
        self.assertFalse(self.host.pkpeer.direct_ok(D), "a key is still listed: no direct offer for D")
        res = self.req(sD)                                # P555: opens for the terminal; A2 is still not asked
        self.assertEqual((res["ok"], res["mode"], res["online"]), (True, "peer", 0))
        self.assertEqual(self.to(sA2, "pk_peer_ask"), [])
        self.run_(self.host.pkpeer.on_phone(sD, "pk_peer_cancel", {"id": res["id"]}))
        # a legacy record (no `dev`) counts as its own
        d = self.st.devices()
        d[A2]["pk"].pop("dev")
        from agentj import state as state_mod
        with self.st.devices_lock():
            state_mod._write_private(self.st.devices_path, json.dumps(d).encode())
        res = self.req(sD)
        self.assertEqual((res["ok"], res["mode"], res["online"]), (True, "peer", 1))
        self.assertEqual(self.to(sA2, "pk_peer_ask")[-1]["id"], res["id"])

    def test_a_device_holding_ds_own_credential_is_not_an_approver(self):
        shared = p57.Authenticator()                      # #300: D borrowed this remote's key
        _, D, sD = self.device("Mac Chrome", shared)
        _, B, sB = self.device("iPhone", shared)
        res = self.req(sD)                                # P555: opens for the terminal only
        self.assertEqual((res["ok"], res["online"]), (True, 0))
        self.assertEqual(self.to(sB, "pk_peer_ask"), [])

    def test_deny_cancel_and_expiry(self):
        _, D, sD = self.device("Mac Chrome", p57.Authenticator())
        bkey = p57.Authenticator()
        _, B, sB = self.device("iPhone", bkey)
        _, C, sC = self.device("iPad", p57.Authenticator())
        res = self.req(sD)
        self.answer(sB, None, self.to(sB, "pk_peer_ask")[-1], ok=False)
        self.assertEqual(self.to(sD, "pk_peer_done")[-1], {"t": "pk_peer_done", "id": res["id"], "ok": False, "why": "denied"})
        self.assertEqual(self.to(sC, "pk_peer_close")[-1]["id"], res["id"])
        line = json.loads(self.st.approvals_path.read_text().splitlines()[-1])
        self.assertEqual((line["decision"], line["device"]), ("deny", B))
        # cancel by D
        res = self.req(sD)
        self.run_(self.host._app(sD, {"t": "pk_peer_cancel", "id": res["id"]}))
        self.assertEqual(self.to(sD, "pk_peer_done")[-1]["why"], "cancelled")
        self.assertEqual(self.to(sB, "pk_peer_close")[-1]["id"], res["id"])
        # expiry: the timer tells D and closes the cards; a late answer is refused
        pkpeer.PEER_TTL, ttl = 0, pkpeer.PEER_TTL
        try:
            res = self.req(sD)
        finally:
            pkpeer.PEER_TTL = ttl
        self.run_(asyncio.sleep(0.05))
        self.assertEqual(self.to(sD, "pk_peer_done")[-1], {"t": "pk_peer_done", "id": res["id"], "ok": False, "why": "expired"})
        self.assertEqual(self.answer(sB, bkey, {"id": res["id"], "fa_ch": wire.b64u(os.urandom(32))})["why"], "expired")

    def test_rate_and_open_limits(self):
        _, D, sD = self.device("Mac Chrome", p57.Authenticator())
        self.device("iPhone", p57.Authenticator())
        for _ in range(pkpeer.RATE_N):
            res = self.req(sD)
            self.assertTrue(res["ok"])
            self.assertEqual(self.req(sD)["id"], res["id"], "one request per device: asking again returns the same one")
            self.run_(self.host._app(sD, {"t": "pk_peer_cancel", "id": res["id"]}))
        self.assertEqual(self.req(sD)["why"], "rate")
        others = [self.device(f"Phone {i}", p57.Authenticator())[2] for i in range(pkpeer.MAX_OPEN + 1)]
        for s in others[:pkpeer.MAX_OPEN]:
            self.assertTrue(self.req(s)["ok"])
        self.assertEqual(self.req(others[-1])["why"], "busy")

    def test_late_approver_and_reconnecting_device(self):
        kpD, D, sD = self.device("Mac Chrome", p57.Authenticator())
        bkey = p57.Authenticator()
        kpB, B, _ = self.device("iPhone", bkey, online=False)
        res = self.req(sD)
        self.assertEqual(res["online"], 0, "the request waits for an approver to come online")
        sB = self.connect(kpB, B)
        self.run_(self.host.pkpeer.on_ready(sB))
        ask = self.to(sB, "pk_peer_ask")[-1]
        self.assertEqual(ask["id"], res["id"])
        del self.host.sessions[sD.cid]                     # D went away; the approval is not void
        self.answer(sB, bkey, ask)
        sD2 = self.connect(kpD, D)
        self.run_(self.host.pkpeer.on_ready(sD2))
        self.assertEqual(self.to(sD2, "pk_peer_done")[-1]["ok"], True)
        offer = self.to(sD2, "pk_offer")[-1]
        self.assertEqual(offer["peer"], res["id"])
        new = p57.Authenticator()
        self.run_(self.host._app(sD2, new.register(wire.unb64u(offer["n"]))))
        self.assertEqual(self.to(sD2, "pk_reg_res")[-1]["ok"], True)
        self.assertEqual(self.st.devices()[D]["pk"]["id"], wire.b64u(new.id))


if __name__ == "__main__":
    unittest.main()
