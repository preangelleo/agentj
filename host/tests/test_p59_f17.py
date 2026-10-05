"""P59 / F17 收尾 (ADR-A163, PROTOCOL §16.1): Face ID before 「同意」 on a sudo / secret card — when, and only when, the
approving device's record holds a passkey (F20).

Over the real F17 flow harness (test_f17: a real `serve` Host, the Agent's unix socket, a fake sudo) and the P57
authenticator stand-in (real P-256 / Ed25519 keys, the byte layouts a browser produces):
- the card tells a passkey device its own credential id (`fa`), never another device's;
- a valid assertion over (card, nonce, shown digest) approves; signCount is stored;
- missing / invalid (UV off, other rp, other origin, other credential, bad signature) / other card / replayed after a
  re-arm / a cloned counter → refused, the card stays open, the phone is told `elev_refused`, nothing runs;
- 拒绝 needs no assertion; a device without a passkey behaves exactly as before;
- a secret card with Face ID saves the value, which never shows up in a log / message / result;
- after a F20 restore (new device id + approval key, same credential) the same passkey still approves.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
import pathlib
import sys
import time
import types
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from agentj import elevate, passkey, wire  # noqa: E402

import test_f17 as f17  # noqa: E402  (module import: its TestCases must not be collected here)
from test_f17 import KEY, PW, answer  # noqa: E402
from test_l1 import Phone, _raw  # noqa: E402
from test_p57_passkey import Authenticator, pk_record  # noqa: E402



def fa(a: Authenticator, ph, card: dict, *, rid=None, n=None, digest=None, kind=None, **kw) -> dict:
    """The phone's `fa`: navigator.credentials.get over elevate_challenge(this card) → {id, cd, ad, sig}."""
    digest = digest or elevate.shown_digest(card["kind"], elevate.shown_fields(card))
    ch = passkey.elevate_challenge(ph.channel, ph.did, rid or card["id"], kind or card["kind"], n or card["n"], digest)
    x = a.assert_(ch, b"\0", nonce=b"\0", **kw)
    return {k: x[k] for k in ("id", "cd", "ad", "sig")}


def with_fa(m: dict, f: dict | None) -> dict:
    return {**m, "fa": f} if f is not None else m


class Challenge(unittest.TestCase):
    def test_binds_every_part(self):
        base = ("ch", "dev", "a" * 32, "sudo", "n" * 32, "d" * 64)
        c = passkey.elevate_challenge(*base)
        self.assertEqual(len(c), 32)
        for i in range(len(base)):
            other = list(base)
            other[i] = base[i] + "x"
            self.assertNotEqual(c, passkey.elevate_challenge(*other), i)
        with self.assertRaises(ValueError):
            passkey.elevate_challenge("ch", "", "a", "sudo", "n", "d")
        with self.assertRaises(ValueError):
            passkey.elevate_challenge("ch\nx", "dev", "a", "sudo", "n", "d")

    def test_vector(self):
        """Shared with web/test/p59_f17.test.mjs: the page and the host must derive the same challenge."""
        got = passkey.elevate_challenge("AAECAwQFBgcICQoLDA0ODw", "dev-1", "0" * 32, "secret", "f" * 32, "e" * 64).hex()
        self.assertEqual(got, VEC)


VEC = "d09334b6a4712179d9f5d26193e55039f115fbe3714d497906fc4a3c0165b44b"


class FaceId(unittest.TestCase):
    """The test_f17 flow harness, with a passkey on the device record."""
    REQ = f17.Flows.REQ
    tearDown, cards, done, run_flow, wait_card, assert_no_leak = (f17.Flows.tearDown, f17.Flows.cards, f17.Flows.done,
                                                                  f17.Flows.run_flow, f17.Flows.wait_card, f17.Flows.assert_no_leak)

    def setUp(self):
        f17.Flows.setUp(self)
        self.a = Authenticator()
        os.environ.pop(passkey.TEST_ENV, None)

    def give_passkey(self, ph, a=None):
        self.assertTrue(self.st.set_passkey(ph.pub, pk_record(a or self.a)))

    def refused(self):
        return [o for _, o in self.sent if o["t"] == "elev_refused"]

    def run_pk(self, req, script, a=None):
        """Like run_flow, but the phone's record gets a passkey before the card is shown."""
        orig = Phone.__init__
        test = self

        def init(ph, st, *args, **kw):
            orig(ph, st, *args, **kw)
            test.give_passkey(ph, a)
        Phone.__init__ = init
        try:
            return self.run_flow(req, script)
        finally:
            Phone.__init__ = orig

    # ------------------------------------------------------------------ approve
    def test_valid_assertion_approves_and_the_card_names_only_this_credential(self):
        a = Authenticator(count=5)

        async def script(host, s, ph):
            c = await self.wait_card()
            self.assertEqual(c["fa"], wire.b64u(a.id), "the card carries this device's credential id")
            await host._app(s, with_fa(answer(ph, c, True, PW), fa(a, ph, c)))
        res, host, ph = self.run_pk({**self.REQ}, script, a)
        self.assertEqual((res["result"], res["code"], res["stdout"]), ("done", 0, "installed\n"))
        rec = elevate.read_log(self.st)[-1]
        self.assertEqual((rec["result"], rec["passkey"]), ("done", "uv"))
        self.assertEqual(elevate.check_record(self.st, rec), "ok")
        self.assertEqual(self.st.passkey_of_device(ph.did)["sc"], 6, "signCount remembered")
        log = self.st.log_path.read_text() + elevate.log_path(self.st).read_text()
        self.assertNotIn(wire.b64u(a.id), log, "no credential id in any log")
        self.assert_no_leak(PW, result=res)

    def test_no_passkey_device_unchanged_and_other_devices_get_no_fa(self):
        async def script(host, s, ph):
            c = await self.wait_card()
            self.assertNotIn("fa", c)
            await host._app(s, answer(ph, c, True, PW))             # no assertion: approved as before
        res, _, _ = self.run_flow({**self.REQ}, script)
        self.assertEqual(res["result"], "done")
        self.assertNotIn("passkey", elevate.read_log(self.st)[-1])

        # two phones, only one with a passkey: each card message is per device
        self.sent.clear()

        async def script2(host, s, ph):
            other = Phone(self.st)
            self.give_passkey(other)
            from test_l1 import _ready
            s2 = _ready(host, other, cid=12)
            await host.elevate.on_ready(s2)
            await self.wait_card()
            await asyncio.sleep(0.05)
            by = {cid: o for cid, o in self.sent if o["t"] == "elev"}
            self.assertNotIn("fa", by[s.cid])
            self.assertEqual(by[12]["fa"], wire.b64u(self.a.id))
            await host._app(s, answer(ph, by[s.cid], True, PW))
        res, _, _ = self.run_flow({**self.REQ}, script2)
        self.assertEqual(res["result"], "done")

    def test_deny_needs_no_assertion(self):
        async def script(host, s, ph):
            c = await self.wait_card()
            await host._app(s, answer(ph, c, False))
        res, _, _ = self.run_pk({**self.REQ}, script)
        self.assertEqual(res["result"], "denied")
        self.assertEqual(self.refused(), [])

    # ------------------------------------------------------------------ refusals
    def test_missing_and_invalid_assertions_refused_card_stays_open(self):
        other_auth = Authenticator()

        async def script(host, s, ph):
            c = await self.wait_card()
            base = answer(ph, c, True, PW)
            bad = [
                base,                                                         # no fa at all
                with_fa(base, "x"),                                           # not an object
                with_fa(base, fa(self.a, ph, c, flags=0x01)),                 # UP only: no user verification
                with_fa(base, fa(self.a, ph, c, rp="evil.example")),          # another rp id
                with_fa(base, fa(self.a, ph, c, origin="https://evil.example")),
                with_fa(base, fa(self.a, ph, c, typ="webauthn.create")),
                with_fa(base, fa(other_auth, ph, c)),                         # a credential this record does not hold
                with_fa(base, {**fa(self.a, ph, c), "id": wire.b64u(other_auth.id)}),
                with_fa(base, {**fa(self.a, ph, c), "sig": fa(other_auth, ph, c)["sig"]}),   # bad signature
                with_fa(base, fa(self.a, ph, c, kind="secret")),              # other kind
                with_fa(base, fa(self.a, ph, c, digest="0" * 64)),            # another shown command
                with_fa(base, fa(self.a, ph, c, n="0" * 32)),                 # another arming
            ]
            for m in bad:
                await host._app(s, m)
                self.assertFalse(host.elevate.cards[c["id"]]["fut"].done())
                self.assertIsNotNone(host.elevate.cards[c["id"]]["nonce"], "a refused answer does not spend the nonce")
            self.assertEqual(len(self.refused()), len(bad))
            self.assertEqual({r["why"] for r in self.refused()}, {"passkey"})
            await host._app(s, with_fa(answer(ph, c, True, PW), fa(self.a, ph, c)))   # the real one still works
        res, _, _ = self.run_pk({**self.REQ}, script)
        self.assertEqual(res["result"], "done")
        reasons = [r.get("reason") for r in elevate.read_log(self.st) if r.get("result") == "refused"]
        self.assertEqual(reasons.count("passkey_missing"), 1)
        self.assertEqual(reasons.count("passkey_bad"), 11)
        recs = [json.loads(ln) for ln in self.st.log_path.read_text().splitlines()]
        self.assertTrue(any(r.get("ev") == "elev_passkey" and r.get("reason") == "flags" for r in recs))
        self.assertEqual(len((self.d / "sudo-record.jsonl").read_text().splitlines()), 1, "sudo ran once")

    def test_other_card_assertion_refused(self):
        async def script(host, s, ph):
            c1 = await self.wait_card(1)
            t2 = asyncio.create_task(asyncio.to_thread(elevate.client_request, self.st, {**self.REQ, "why": "第二张"}, 30))
            c2 = await self.wait_card(2)
            self.assertNotEqual(c1["id"], c2["id"])
            # c1's assertion on an answer for c2 (and vice versa): refused
            await host._app(s, with_fa(answer(ph, c2, True, PW), fa(self.a, ph, c1)))
            await host._app(s, with_fa(answer(ph, c1, True, PW), fa(self.a, ph, c2)))
            # an assertion over c1's id but c2's nonce and digest
            await host._app(s, with_fa(answer(ph, c2, True, PW), fa(self.a, ph, c2, rid=c1["id"])))
            self.assertEqual(len(self.refused()), 3)
            await host._app(s, answer(ph, c2, False))
            self.assertEqual((await t2)["result"], "denied")
            await host._app(s, with_fa(answer(ph, c1, True, PW), fa(self.a, ph, c1)))
        res, _, _ = self.run_pk({**self.REQ}, script)
        self.assertEqual(res["result"], "done")

    def test_replayed_assertion_refused(self):
        """A wrong password re-arms the card (new nonce): the first Face ID cannot approve the second attempt; a cloned
        counter (≤ the stored one) is refused too; a captured answer replayed after acting finds no nonce."""
        a = Authenticator(count=10)

        async def script(host, s, ph):
            c = await self.wait_card(1)
            first = fa(a, ph, c)
            await host._app(s, with_fa(answer(ph, c, True, "wrong"), first))   # Face ID ok, password wrong → re-armed
            c2 = await self.wait_card(2)
            self.assertNotEqual(c2["n"], c["n"])
            await host._app(s, with_fa(answer(ph, c2, True, PW), first))       # the old assertion: refused
            await host._app(s, with_fa(answer(ph, c2, True, PW), fa(a, ph, c2, count=5)))   # counter went back: clone
            self.assertEqual(len(self.refused()), 2)
            good = with_fa(answer(ph, c2, True, PW), fa(a, ph, c2))
            await host._app(s, good)
            await asyncio.sleep(0.05)
            await host._app(s, good)                                            # replay after acting: nothing
        res, _, ph = self.run_pk({**self.REQ}, script, a)
        self.assertEqual(res["result"], "done")
        recs = [json.loads(ln) for ln in self.st.log_path.read_text().splitlines()]
        self.assertTrue(any(r.get("ev") == "elev_passkey" and r.get("reason") == "sign_count" for r in recs))
        self.assertEqual(self.st.passkey_of_device(ph.did)["sc"], 12)

    # ------------------------------------------------------------------ secret card
    def test_secret_card_with_face_id(self):
        work = self.d / "proj"
        work.mkdir()
        req = {"t": "secret", "name": "TOK", "purpose": "p", "dest": "file:tok.txt", "cwd": str(work)}

        async def script(host, s, ph):
            c = await self.wait_card()
            self.assertEqual(c["fa"], wire.b64u(self.a.id))
            await host._app(s, answer(ph, c, True, KEY))                        # no Face ID: not saved
            self.assertFalse((work / "tok.txt").exists())
            await host._app(s, with_fa(answer(ph, c, True, KEY), fa(self.a, ph, c)))
        res, _, _ = self.run_pk(req, script)
        self.assertEqual(res["result"], "saved")
        self.assertEqual((work / "tok.txt").read_text(), KEY)
        self.assert_no_leak(KEY, result=res)

    # ------------------------------------------------------------------ admin helper (approve-only, no password)
    def test_admin_helper_approve_only_needs_face_id_too(self):
        from test_l1 import _host, _ready
        host = _host(self.st, self.sent)
        ph = Phone(self.st)
        self.give_passkey(ph)
        s = _ready(host, ph)
        card = {"kind": "sudo", "id": "a" * 32, "n": "b" * 32, "cmd": "true", "why": "w", "effect": ""}
        c = {**card, "nonce": card["n"], "deadline": time.monotonic() + 60, "helper": [ph.did],
             "digest": elevate.shown_digest("sudo", elevate.shown_fields(card))}
        m = f17.approve_only(ph, card)
        self.assertEqual(host.elevate._check(s, c, m), "passkey_missing")
        self.assertIsNone(host.elevate._check(s, c, with_fa(m, fa(self.a, ph, card))))

    # ------------------------------------------------------------------ after a F20 restore
    def test_still_works_after_a_passkey_restore(self):
        async def script(host, s, ph):
            c = await self.wait_card()
            # F20 restore: the record takes a new X25519 key (→ new device id) and a new approval key; same credential
            new_pub, new_sk = os.urandom(32), Ed25519PrivateKey.generate()
            new = self.st.passkey_restore(ph.did, wire.b64u(self.a.id), new_pub, _raw(new_sk.public_key()))
            self.assertIsNotNone(new)
            ph2 = types.SimpleNamespace(did=new, sk=new_sk, pub=new_pub, channel=ph.channel)
            from test_l1 import _ready
            s2 = _ready(host, ph2, cid=21)
            self.sent.clear()
            await host.elevate.on_ready(s2)
            c2 = [o for _, o in self.sent if o["t"] == "elev"][-1]
            self.assertEqual((c2["id"], c2["fa"]), (c["id"], wire.b64u(self.a.id)))
            # the old device id can no longer approve (no longer listed); its assertion is bound to the old id anyway
            await host._app(s2, with_fa(answer(ph2, c2, True, PW), fa(self.a, ph, c2)))
            self.assertEqual(len(self.refused()), 1)
            await host._app(s2, with_fa(answer(ph2, c2, True, PW), fa(self.a, ph2, c2)))
        res, _, _ = self.run_pk({**self.REQ}, script)
        self.assertEqual(res["result"], "done")
        self.assertEqual(elevate.read_log(self.st)[-1]["passkey"], "uv")


if __name__ == "__main__":
    unittest.main()
