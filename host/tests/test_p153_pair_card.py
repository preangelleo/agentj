"""P153: add a remote from a remote. A paired remote asks for a QR (signed `pair_new`); the new device that completes the
handshake waits; every paired remote that renders it (`pair1`) gets ONE `pair_device` card showing the 6-digit code; the first
signed answer decides (+ the remote's passkey when it has one); the others fold it away (`ask_done`); the opening remote hears
the outcome (`pair_ev`); the new device gets `approved`. Nothing is typed; the terminal / account routes keep working."""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import asyncio
import json
import os
import pathlib
import secrets
import sys
import tempfile
import time
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import approvals, controls, gate, pair_card, passkey, serve, wire  # noqa: E402

PASS = "test-passphrase-P153"
NEW = "网页 · Android Chrome"


def _raw(sk):
    return sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


class Flow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        st = serve.State(pathlib.Path(self.tmp.name) / "s")
        st.init(relay="ws://127.0.0.1:1")
        gate.set_passphrase(st, PASS)
        self.st = st
        self.host = serve.Host(st, events="jsonl", read_stdin=False)
        self.host.cfg = {**getattr(self.host, "cfg", {}), "web": "https://m.agentj.app", "relay": "wss://relay.agentj.app"}
        self.host.relay_up = True
        self.sent, self.ops = [], []

        async def send_app(s, obj):
            if self.host.sessions.get(s.cid) is not s:
                return False
            self.sent.append((s.cid, obj))
            return True

        async def op(o, cid, payload=b""):
            self.ops.append((o, cid, payload))
        self.host.send_app = send_app
        self.host._op = op
        self.keys = {}
        # two paired remotes that render the card (A = this computer's browser, B = the phone) and one old page (C)
        self.A = self._remote(10, "网页 · Linux Chrome", pair1=True)
        self.B = self._remote(11, "网页 · iOS Safari", pair1=True)
        self.C = self._remote(12, "网页 · iPad Safari", pair1=False)

    def tearDown(self):
        self.tmp.cleanup()

    def _remote(self, cid, name, pair1):
        pub, sk = os.urandom(32), Ed25519PrivateKey.generate()
        did = self.st.pair_device(pub, name, _raw(sk))["device"]
        self.keys[did] = sk
        self.host.sessions[cid] = serve.Session(cid=cid, state="ready", device=did, pub=pub, name=name, p33=True, pair1=pair1)
        return did

    def to(self, cid, t=None):
        return [o for c, o in self.sent if c == cid and (t is None or o.get("t") == t)]

    def _sign_control(self, did):
        n, ts = secrets.token_hex(16), int(time.time() * 1000)
        msg = controls.signed_message(self.host.channel, did, "pair_new", n, ts, controls.object_digest("pair_new", {}))
        return {"t": "pair_new", "r": "r1", "n": n, "ts": ts, "sig": wire.b64u(self.keys[did].sign(msg))}

    def _answer(self, did, card, ok=True, **extra):
        msg = approvals.signed_message(self.host.channel, did, card["id"], "allow" if ok else "deny",
                                       approvals.shown_digest(card["tool"], card["summary"]))
        return {"t": "answer", "id": card["id"], "ok": ok, "sig": wire.b64u(self.keys[did].sign(msg)), **extra}

    async def _open(self, by=None):
        by = by or self.A
        cid = next(c for c, s in self.host.sessions.items() if s.device == by)
        await self.host._app(self.host.sessions[cid], self._sign_control(by))
        return self.to(cid, "pair_link")[-1]

    async def _arrive(self, cid=20, name=NEW):
        """A new device that completed IKpsk2 on the open pairing, then sends its hello."""
        p = self.host.pairing
        pub = os.urandom(32)
        s = serve.Session(cid=cid, state="hello", mode="pair", device=wire.device_id(pub), pub=pub, name=name, h=os.urandom(32))
        self.host.sessions[cid] = s
        p.cid = cid
        await self.host._app(s, {"t": "hello", "caps": ["p33", "lt1", "pair1"]})
        await asyncio.sleep(0)
        return s

    async def _settle(self):
        for _ in range(20):
            await asyncio.sleep(0)

    # ------------------------------------------------------------------ the target flow
    def test_scan_card_on_every_remote_any_one_confirms(self):
        async def go():
            link = await self._open()
            self.assertTrue(link["ok"])
            self.assertTrue(link["qr"].startswith("data:image/png;base64,"))
            self.assertTrue(link["link"].startswith("https://m.agentj.app/#p="))
            self.assertEqual(self.host.pairing.phone, self.A)
            s = await self._arrive()
            code = wire.safety_code(s.h)
            cards = {cid: self.to(cid, "ask") for cid in (10, 11, 12)}
            self.assertEqual(len(cards[10]), 1)
            self.assertEqual(len(cards[11]), 1)
            self.assertEqual(cards[12], [], "an old page that cannot render the card never gets it")
            card = cards[11][0]
            self.assertEqual(card["tool"], pair_card.TOOL)
            self.assertIn(f"{code[:3]} {code[3:]}", card["summary"], "the code is shown on the card, to compare")
            self.assertIn(NEW, card["summary"])
            self.assertGreater(card["ttl"], 200, "five minutes, not the old 120 s to type a code")
            self.assertEqual(self.to(20, "pair_wait")[0]["t"], "pair_wait", "the new device is told to wait, no code to type")
            self.assertEqual(self.to(10, "pair_ev")[0], {"t": "pair_ev", "ev": "pending", "name": NEW})
            await self.host._app(self.host.sessions[11], self._answer(self.B, card))
            # P525 state fanout checks the existing journal in a thread: await completion, not 20 scheduler spins.
            waiting = [t for t in self.host._bg_tasks if t.get_coro().__name__ == '_pair_card_wait']
            await asyncio.wait_for(asyncio.gather(*waiting), 3)
            await self._settle()
            return s
        s = asyncio.run(go())
        self.assertIn(s.device, self.st.devices())
        self.assertEqual(s.state, "ready")
        self.assertEqual(self.to(20, "approved")[0]["t"], "approved")
        for cid in (10, 11, 12):
            self.assertIn({"t": "ask_done", "id": self.to(11, "ask")[0]["id"], "result": "allow"}, self.to(cid),
                          "every remote folds the card (the old page ignores an unknown id)")
        self.assertEqual(self.to(10, "pair_ev")[-1], {"t": "pair_ev", "ev": "approved", "name": NEW})
        self.assertIsNone(self.host.pairing)
        log = self.st.root.joinpath("approvals.log").read_text() if self.st.root.joinpath("approvals.log").exists() else ""
        self.assertIn(pair_card.TOOL, log)
        self.assertIn(self.B, log, "the approving remote and its signature are on record")
        self.assertNotIn(wire.safety_code(s.h), log, "the code itself is never logged")

    def test_cancel_on_a_remote_refuses_the_new_device(self):
        async def go():
            await self._open()
            s = await self._arrive()
            card = self.to(10, "ask")[0]
            await self.host._app(self.host.sessions[10], self._answer(self.A, card, ok=False))
            await self._settle()
            return s
        s = asyncio.run(go())
        self.assertNotIn(s.device, self.st.devices())
        self.assertNotIn(20, self.host.sessions)
        self.assertIn((wire.OP_CLOSE, 20, b""), self.ops)
        self.assertEqual(self.to(10, "pair_ev")[-1]["ev"], "denied")
        self.assertIn("deny", [o.get("result") for o in self.to(11, "ask_done")])

    # ------------------------------------------------------------------ the bar is not lowered
    def test_bad_or_missing_signature_and_old_pages_decide_nothing(self):
        async def go():
            await self._open()
            s = await self._arrive()
            card = self.to(11, "ask")[0]
            bad = self._answer(self.B, card)
            bad["sig"] = wire.b64u(os.urandom(64))
            await self.host._app(self.host.sessions[11], bad)
            await self.host._app(self.host.sessions[12], self._answer(self.C, card))   # signed, but never shown the card
            await self._settle()
            self.assertEqual(s.state, "pending")
            self.assertNotIn(s.device, self.st.devices())
            self.host._pair_card_end(s)
            await self._settle()
        asyncio.run(go())
        log = self.st.log_path.read_text()
        self.assertIn('"reason": "bad_signature"', log)
        self.assertIn('"reason": "no_pair1"', log)

    def test_a_remote_with_a_passkey_must_use_it(self):
        self.st.set_passkey(self.host.sessions[11].pub,
                            {"id": wire.b64u(b"\x07" * 16), "pub": wire.b64u(os.urandom(77)), "alg": -7, "count": 0,
                             "rp": "m.agentj.app", "at": int(time.time())})
        self.assertIsNotNone(self.st.passkey_of_device(self.B))

        async def go():
            await self._open()
            s = await self._arrive()
            card = self.to(11, "ask")[0]
            self.assertTrue(card.get("passkey_required"))
            want = passkey.elevate_challenge(self.host.channel, self.B, card["id"], pair_card.PK_KIND, card["id"],
                                             approvals.shown_digest(card["tool"], card["summary"]))
            self.assertEqual(card["fa_ch"], wire.b64u(want))
            self.assertFalse(self.to(10, "ask")[0].get("passkey_required"), "a remote without a passkey signs only")
            await self.host._app(self.host.sessions[11], self._answer(self.B, card))     # no Face ID
            await self._settle()
            self.assertEqual(s.state, "pending")
            self.host._pair_card_end(s)
            await self._settle()
        asyncio.run(go())
        self.assertIn('"reason": "passkey_required"', self.st.log_path.read_text())

    def test_pair_new_needs_a_valid_signature_and_a_pair1_page(self):
        async def go():
            obj = self._sign_control(self.A)
            obj["sig"] = wire.b64u(os.urandom(64))
            await self.host._app(self.host.sessions[10], obj)
            self.assertEqual(self.to(10, "pair_link")[-1], {"t": "pair_link", "r": "r1", "ok": False, "why": "bad_signature"})
            await self.host._app(self.host.sessions[12], self._sign_control(self.C))
            self.assertEqual(self.to(12, "pair_link"), [])
            self.assertIsNone(self.host.pairing)
        asyncio.run(go())

    def test_one_pairing_at_a_time(self):
        async def go():
            await self._open(self.A)
            link = await self._open(self.B)
            self.assertEqual((link["ok"], link["why"]), (False, "busy"))
            again = await self._open(self.A)                 # the same remote may simply open a fresh QR
            self.assertTrue(again["ok"])
            await self._settle()
            self.assertEqual(self.to(10, "pair_ev")[-1]["ev"], "replaced")
        asyncio.run(go())

    def test_closing_the_qr_sheet_ends_the_pairing(self):
        async def go():
            await self._open()
            s = await self._arrive()
            await self.host._app(self.host.sessions[10], {"t": "pair_stop"})
            await self._settle()
            return s
        s = asyncio.run(go())
        self.assertIsNone(self.host.pairing)
        self.assertNotIn(20, self.host.sessions)
        self.assertIn("gone", [o.get("result") for o in self.to(11, "ask_done")])

    def test_the_new_device_leaving_is_logged_and_folds_the_card(self):
        """10-12 09:52 Leo's Mac Chrome was dropped by the relay (4029) while pending: `agentj pair` said 「手机断开了」 but
        host.log had no line for the newcomer itself. Now `pair_gone` names it."""
        async def go():
            await self._open()
            s = await self._arrive()
            await self.host.on_frame(bytes([wire.OP_GONE]) + (20).to_bytes(4, "big"))
            await self._settle()
            return s
        s = asyncio.run(go())
        self.assertIsNone(self.host.pairing)
        self.assertIn("gone", [o.get("result") for o in self.to(11, "ask_done")])
        evs = [json.loads(x) for x in self.st.log_path.read_text().splitlines() if x.strip()]
        gone = [e for e in evs if e.get("ev") == "pair_gone"]
        self.assertEqual([(e["cid"], e["device"], e["phase"]) for e in gone], [(20, s.device, "pending")])
        self.assertNotIn("dev_gone", [e.get("ev") for e in evs], "dev_gone stays for ready remotes only")

    # ------------------------------------------------------------------ the other routes still work, and fold the card
    def test_terminal_code_still_works_and_folds_the_card(self):
        async def go():
            p = serve.Pairing(os.urandom(16), os.urandom(32), time.time() + 60, time.monotonic() + 60, ctl=None)
            self.host.pairing = p
            s = await self._arrive()
            self.assertEqual(len(self.to(11, "ask")), 1, "a terminal `agentj pair` pairing also reaches the remotes")
            res = await self.host.decide(p, wire.safety_code(s.h), PASS)
            await self._settle()
            return res
        res = asyncio.run(go())
        self.assertEqual(res["ev"], "approved")
        self.assertIn("gone", [o.get("result") for o in self.to(11, "ask_done")])

    def test_no_remote_yet_no_card(self):
        for did in list(self.st.devices()):
            asyncio.run(self.host.revoke(did))

        async def go():
            p = serve.Pairing(os.urandom(16), os.urandom(32), time.time() + 60, time.monotonic() + 60, ctl=None)
            self.host.pairing = p
            s = await self._arrive()
            self.assertEqual(s.card, "")
            self.assertEqual(self.to(20, "pair_wait"), [], "the first remote still uses the terminal / account page")
            self.assertLessEqual(s.deadline - time.monotonic(), serve.APPROVE_TTL + 1)
        asyncio.run(go())

    def test_card_text_is_one_language(self):
        zh = pair_card.summary("zh", NEW, "123456", {"evict": {"name": "旧手机"}, "limit": 5}, when=0)
        en = pair_card.summary("en", "Web · Android Chrome", "123456", {"replaces": {"name": "old"}}, when=0)
        self.assertIn("对照码：123 456", zh)
        self.assertIn("旧手机", zh)
        self.assertNotRegex(en, "[一-鿿]")
        self.assertIn("Match code: 123 456", en)


if __name__ == "__main__":
    unittest.main()
