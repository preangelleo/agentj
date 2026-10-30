"""P566 (#364 / #354 / #357 / #346, 华为 Pad 10-28 21:02 and Sammi's Mac 17:50): the Face ID sheet sends the page to the
background, the socket drops (dev_gone 6–20 s) and the page hands the get() result in on the NEXT socket of the same device.

- a key-card answer (signature + assertion over card id, nonce and what was shown) is taken on another socket of the same
  device — every check unchanged: a stale time, a foreign signature and a card that is gone are refused, and the phone is now
  TOLD why (`elev_refused.why`; elevate.py:1240-1248 used to write only the log);
- the same answer handed in again after the card ended gets the card's end (elev_done) — never a second run;
- an expired key card leaves one chat line 「这张卡已过期，请让 Agent 重发」;
- the P555 self-check / Settings 「测试一下」 challenge lives with the device (≤ CHECKS, CHECK_TTL), answers once, and is
  issued for a `local` key too.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import pathlib
import sys
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from agentj import elevate, pkpeer, serve, wire  # noqa: E402
import test_p555_faceid as p555  # noqa: E402  (module import: its TestCases are not collected here)
import test_p57_passkey as p57  # noqa: E402
import test_p59_f17 as p59  # noqa: E402
from test_f17 import PW, answer  # noqa: E402
from test_l1 import _ready  # noqa: E402

B = p59.FaceId


class KeyCardResume(unittest.TestCase):
    USE_PASSKEY = False
    REQ = B.REQ
    setUp, tearDown, give_passkey, refused, run_pk = B.setUp, B.tearDown, B.give_passkey, B.refused, B.run_pk
    cards, done, run_flow, wait_card = B.cards, B.done, B.run_flow, B.wait_card

    def to(self, cid, t):
        return [o for c, o in self.sent if c == cid and o["t"] == t]

    def test_the_answer_on_the_next_socket_of_the_same_device_is_taken_and_a_second_copy_gets_the_end(self):
        async def script(host, s, ph):
            c = await self.wait_card()
            host.sessions.pop(s.cid, None)                   # dev_gone while the system sheet was up
            s2 = _ready(host, ph, cid=12)                     # resume_ok: the same device on a new socket
            good = p59.with_fa(answer(ph, c, True, PW), p59.fa(self.a, ph, c))
            await host._app(s2, good)
            for _ in range(300):
                if self.done():
                    break
                await asyncio.sleep(0.02)
            await host._app(s2, good)                         # handed in again (its reply was lost): the end, nothing else
            self.assertEqual(self.to(12, "elev_done")[-1]["result"], "done")
        res, _, _ = self.run_pk({**self.REQ}, script)
        self.assertEqual(res["result"], "done")
        self.assertEqual(self.refused(), [])
        self.assertEqual(len((self.d / "sudo-record.jsonl").read_text().splitlines()), 1, "ran once")

    def test_every_refusal_says_why_and_the_card_stays(self):
        async def script(host, s, ph):
            c = await self.wait_card()
            f = p59.fa(self.a, ph, c)
            await host._app(s, p59.with_fa(answer(ph, c, True, PW, ts=int(time.time() * 1000) - 3_600_000), f))
            await host._app(s, p59.with_fa(answer(ph, c, True, PW, sk=Ed25519PrivateKey.generate()), f))
            await host._app(s, p59.with_fa(answer(ph, c, True, PW, nonce="0" * 32), f))
            await host._app(s, answer(ph, c, True, PW))                       # no assertion from a passkey remote
            await host._app(s, answer(ph, c, True, PW, rid="e" * 32))         # a card that is not there
            self.assertEqual([r["why"] for r in self.refused()], ["stale", "bad_signature", "replay", "passkey", "gone"])
            self.assertFalse(host.elevate.cards[c["id"]]["fut"].done(), "the card stays open")
            await host._app(s, p59.with_fa(answer(ph, c, True, PW), p59.fa(self.a, ph, c)))
        res, _, _ = self.run_pk({**self.REQ}, script)
        self.assertEqual(res["result"], "done")
        for r in self.refused():
            self.assertEqual(set(r), {"t", "id", "why"}, "one coarse word, never the card's contents")

    def test_refused_words(self):
        for why, word in (("late", "late"), ("stale", "stale"), ("replay", "replay"), ("bad_signature", "bad_signature"),
                          ("no_key", "no_key"), ("passkey_bad", "passkey"), ("passkey_missing", "passkey"),
                          ("passkey_required", "passkey"), ("shape", "shape"), ("anything", "shape")):
            self.assertEqual(elevate.refused_word(why), word)

    def test_an_expired_key_card_leaves_one_chat_line(self):
        notes = []

        async def script(host, s, ph):
            host.agent_notice = lambda text, **kw: notes.append(text)
            c = await self.wait_card()
            host.elevate.cards[c["id"]]["deadline"] = time.monotonic() - 1
            for _ in range(300):
                if self.done():
                    break
                await asyncio.sleep(0.02)
        req = {"t": "secret", "name": "OPENAI_API_KEY", "purpose": "p566", "dest": str(self.d / "k.env"), "cwd": str(self.d)}
        res, _, _ = self.run_flow(req, script)
        self.assertEqual(res["result"], "timeout")
        self.assertEqual(len(notes), 1)
        self.assertIn("这张卡已过期，请让 Agent 重发", notes[0])
        self.assertIn("ask the Agent to send it again", notes[0])


class CheckChallenge(unittest.TestCase):
    setUp, tearDown, run_, to, device, edit_pk, state_msg = (p555.FaceIdRecognize.setUp, p555.FaceIdRecognize.tearDown,
        p555.FaceIdRecognize.run_, p555.FaceIdRecognize.to, p555.FaceIdRecognize.device, p555.FaceIdRecognize.edit_pk,
        p555.FaceIdRecognize.state_msg)
    fa = staticmethod(p555.FaceIdRecognize.fa)

    def check(self, s, m, a, r="c1"):
        self.run_(self.host._app(s, {"t": "pk_check", "r": r, "n": m["n"], "fa": self.fa(a, wire.unb64u(m["fa_ch"])), "aa": "platform"}))
        return self.to(s, "pk_check_res")[-1]

    def test_a_local_key_gets_a_test_challenge_that_passes_and_changes_nothing(self):
        a = p57.Authenticator()
        _, did, s = self.device("Mac Chrome", a)
        self.edit_pk(did, local=True)
        m = self.state_msg(s)
        self.assertEqual(m["s"], "local")
        self.assertEqual(self.check(s, m, a), {"t": "pk_check_res", "r": "c1", "ok": True, "s": "local"})
        self.assertEqual(self.st.passkey_state(did), "local")
        self.assertEqual(self.check(s, m, a)["ok"], False, "each challenge answers once")
        self.assertIn("fa_ch", self.to(s, "pk_state")[-1], "a failure brings a fresh one for the next tap")

    def test_a_test_reported_over_a_phone_scan_does_not_downgrade_a_local_key(self):
        """Security review S2: `aa` is the browser's unsigned word — a pass on a `local` key never rewrites it."""
        a = p57.Authenticator()
        _, did, s = self.device("Mac Chrome", a)
        self.edit_pk(did, local=True)
        m = self.state_msg(s)
        self.run_(self.host._app(s, {"t": "pk_check", "n": m["n"], "fa": self.fa(a, wire.unb64u(m["fa_ch"])), "aa": "cross-platform"}))
        self.assertIs(self.to(s, "pk_check_res")[-1]["ok"], True)
        self.assertEqual(self.st.passkey_state(did), "local")

    def test_the_challenge_of_the_old_socket_is_answered_on_the_new_one(self):
        a = p57.Authenticator()
        _, did, s = self.device("华为 Pad", a)
        self.edit_pk(did, local=True)
        m = self.state_msg(s)
        self.host.sessions.pop(s.cid, None)                   # dev_gone behind the fingerprint sheet
        s2 = serve.Session(cid=99, state="ready", device=did, pub=s.pub, p33=True, name="华为 Pad")
        self.host.sessions[99] = s2
        self.state_msg(s2)                                    # the reconnect's own pk_state brings another challenge
        self.assertEqual(self.check(s2, m, a)["ok"], True, "the first one still answers, once")

    def test_another_device_cannot_use_it_and_at_most_CHECKS_stay_open(self):
        a, b = p57.Authenticator(), p57.Authenticator()
        _, did, s = self.device("iPhone", a)
        _, did2, s2 = self.device("iPad", b)
        self.edit_pk(did, local=True)
        self.edit_pk(did2, local=True)
        m = self.state_msg(s)
        self.run_(self.host._app(s2, {"t": "pk_check", "n": m["n"], "fa": self.fa(b, wire.unb64u(m["fa_ch"]))}))
        self.assertEqual(self.to(s2, "pk_check_res")[-1]["ok"], False, "another device's challenge")
        for _ in range(pkpeer.CHECKS + 2):
            self.state_msg(s)
        self.assertLessEqual(len(self.host.pkpeer.checks[did]), pkpeer.CHECKS)
        self.assertFalse(self.check(s, m, a)["ok"], "the oldest one went")

    def test_an_expired_challenge_is_refused(self):
        a = p57.Authenticator()
        _, did, s = self.device("iPhone", a)
        self.edit_pk(did, local=True)
        m = self.state_msg(s)
        self.host.pkpeer.checks[did] = [(n, 0.0) for n, _ in self.host.pkpeer.checks[did]]
        self.assertFalse(self.check(s, m, a)["ok"])


del B   # borrowed methods only: p59's FaceId cases are not collected again here


if __name__ == "__main__":
    unittest.main()
