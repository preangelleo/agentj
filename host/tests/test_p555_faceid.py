"""P555: after the 0.25.13 upgrade every remote said 「本机 Face ID：未确认」 and asked another remote to approve a key it
already had (owner, 10-27 11:47). Keys saved before P539 carry no `local`, so passkey.pk_state said "unknown".

- a key registered under the device's own id (no `local`, `dev` missing or = the id) is this device's own: zero taps;
- a key a restore moved here (`dev` names another id) stays unknown and gets a one-tap self-check (`pk_check`) — success
  notes `local` and never opens a pk_peer request;
- nobody to approve (the only keyed remote lost its key) → the request still opens and the owner approves it at the Agent J
  host's terminal with the 对照码 (`pk_peer_host`, the same claim bar as `resume`);
- a device without a key still needs an approval (another remote, or the terminal)."""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import asyncio
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ed25519  # noqa: E402

import test_p57_passkey as p57  # noqa: E402
from agentj import cli, ctl_auth, passkey, pkpeer, remotes, serve, state as state_mod, wire  # noqa: E402
from agentj.noise import Keypair  # noqa: E402
from agentj.state import State  # noqa: E402


class FaceIdRecognize(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = State(pathlib.Path(self.tmp.name) / "s")
        self.st.init(relay="ws://127.0.0.1:1")
        self.host = serve.Host(self.st, events="jsonl", read_stdin=False)
        self.sent, self.notices = [], []

        async def send_app(s, obj):
            if self.host.sessions.get(s.cid) is not s:
                return False
            self.sent.append((s.cid, obj))
            return True

        async def broadcast(text, frm="notice", **kw):
            self.notices.append(text)
        self.host.send_app = send_app
        self.host.broadcast = broadcast
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
        pub = ed25519.Ed25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        did = self.st.pair_device(kp.pub, name, pub)["device"]
        if auth:
            self.assertTrue(self.st.set_passkey(kp.pub, p57.pk_record(auth)))
        s = None
        if online:
            self.cid += 1
            s = serve.Session(cid=self.cid, state="ready", device=did, pub=kp.pub, p33=True, name=name)
            self.host.sessions[self.cid] = s
        return kp, did, s

    def edit_pk(self, did, **kw):
        """Rewrite this device's pk record as an older host left it (None = drop the field)."""
        with self.st.devices_lock():
            d = self.st.devices()
            for k, v in kw.items():
                if v is None:
                    d[did]["pk"].pop(k, None)
                else:
                    d[did]["pk"][k] = v
            state_mod._write_private(self.st.devices_path, json.dumps(d).encode())

    @staticmethod
    def fa(auth, challenge):
        a = auth.assert_(challenge, b"u", ts=0, nonce=b"\0" * 16)
        return {k: a[k] for k in ("id", "cd", "ad", "sig")}

    def state_msg(self, s):
        self.run_(self.host.remotes.send_state(s))
        return self.to(s, "pk_state")[-1]

    # ------------------------------------------------------------------ 1. zero taps for a key saved before P539
    def test_legacy_own_key_is_recognised_with_no_tap(self):
        a = p57.Authenticator()
        _, did, s = self.device("iPhone Air", a)
        self.edit_pk(did, local=None, dev=None, plat=None)          # what 0.25.12 and older left in devices.json
        self.assertEqual(passkey.pk_state(json.loads(self.st.devices_path.read_text())[did]["pk"]), "unknown",
                         "the raw record alone is what said 「未确认」")
        self.assertEqual(self.state_msg(s), {"t": "pk_state", "s": "local"}, "no self-check fields: nothing to tap")
        row = [r for r in remotes.rows(self.st, set(), did) if r["id"] == did][0]
        self.assertEqual(row["pk"], "local", "the remotes list agrees")
        self.assertNotIn("local", json.loads(self.st.devices_path.read_text())[did]["pk"], "a view, not a rewrite")
        # the same record with its registering id written down (P547) is the device's own too
        self.edit_pk(did, dev=did)
        self.assertEqual(self.st.passkey_state(did), "local")
        self.assertEqual([o for _, o in self.sent if o["t"].startswith("pk_peer")], [], "no peer request anywhere")

    def test_explicit_states_are_kept(self):
        a = p57.Authenticator()
        _, did, _ = self.device("Mac Chrome", a)
        self.edit_pk(did, local=False)
        self.assertEqual(self.st.passkey_state(did), "borrowed")
        _, did2, _ = self.device("Pixel")
        self.assertEqual(self.st.passkey_state(did2), "none")

    # ------------------------------------------------------------------ 2. a moved key: one Face ID tap, no other remote
    def moved(self):
        a = p57.Authenticator()
        _, did, s = self.device("iPhone Air", a)
        self.edit_pk(did, local=None, dev="d" * 22)                  # a restore moved this record here from another id
        return a, did, s

    def test_moved_key_self_check_success_needs_no_peer(self):
        a, did, s = self.moved()
        self.device("Pixel", p57.Authenticator())                    # another keyed remote exists — still not asked
        m = self.state_msg(s)
        self.assertEqual((m["s"], m["fa"]), ("unknown", wire.b64u(a.id)))
        self.run_(self.host._app(s, {"t": "pk_check", "r": "c1", "n": m["n"], "fa": self.fa(a, wire.unb64u(m["fa_ch"])), "aa": "platform"}))
        self.assertEqual(self.to(s, "pk_check_res")[-1], {"t": "pk_check_res", "r": "c1", "ok": True, "s": "local"},
                         "the page's ask1 waits on r")
        self.assertIs(self.st.devices()[did]["pk"]["local"], True)
        self.assertEqual(self.to(s, "pk_state")[-1], {"t": "pk_state", "s": "local"}, "the row repaints 已设好")
        self.assertEqual([o for _, o in self.sent if o["t"].startswith("pk_peer")], [], "never a pk_peer request / card")
        self.assertIn('"result": "ok"', self.st.log_path.read_text().split("pk_check")[-1][:200])

    def test_a_card_opened_with_face_id_means_set_up(self):
        """Owner 10-27 11:53: the remote said 「未确认」 yet opened a key pickup card with Face ID — what works is what shows."""
        a, did, s = self.moved()
        self.assertEqual(self.st.passkey_state(did), "unknown")
        out = self.host.elevate.out
        c = {"id": "c" * 32, "nonce": "n" * 32, "digest": "0" * 64}
        ch = passkey.elevate_challenge(self.host.channel, did, c["id"], "secret_out", c["nonce"], c["digest"])
        async def pick(s_, fa):                                       # as serve's handler: inside the running loop
            r = out._check(s_, c, {"n": c["nonce"], "fa": fa})
            await asyncio.sleep(0.05)
            return r
        self.assertIsNone(self.run_(pick(s, self.fa(a, ch))), "the pickup verifies")
        self.assertEqual(self.st.passkey_state(did), "local", "→ 已设好")
        self.assertIs(self.st.devices()[did]["pk"]["local"], True)
        self.assertEqual(self.to(s, "pk_state")[-1], {"t": "pk_state", "s": "local"}, "every remote repaints at once")
        self.assertEqual([o for _, o in self.sent if o["t"].startswith("pk_peer")], [], "no peer request")
        # a refused assertion notes nothing
        b, did2, s2 = self.moved()
        self.assertEqual(self.run_(pick(s2, self.fa(p57.Authenticator(), ch))), "passkey_bad")
        self.assertEqual(self.st.passkey_state(did2), "unknown")

    def test_passkey_count_notes_only_an_unknown_record(self):
        seen = []
        self.st.on_passkey_noted = seen.append
        a, did, _ = self.moved()
        self.assertFalse(self.st.passkey_count(did, wire.b64u(b"x" * 16), 0), "another credential id: nothing")
        self.assertEqual(self.st.passkey_state(did), "unknown")
        self.assertFalse(self.st.passkey_count(did, wire.b64u(a.id), 0), "iOS signCount 0: still False (nothing stored)")
        self.assertEqual((self.st.passkey_state(did), seen), ("local", [did]))
        self.assertTrue(self.st.passkey_count(did, wire.b64u(a.id), 7))
        self.assertEqual((self.st.devices()[did]["pk"]["sc"], seen), (7, [did]), "noted once")
        _, did2, _ = self.device("Mac Chrome", b := p57.Authenticator())
        self.edit_pk(did2, local=False, dev="e" * 22)                # a key borrowed over a phone scan stays borrowed
        self.st.passkey_count(did2, wire.b64u(b.id), 0)
        self.assertEqual((self.st.passkey_state(did2), seen), ("borrowed", [did]))
        before = self.st.devices_path.stat().st_mtime_ns
        self.st.passkey_count(did2, wire.b64u(b.id), 0)
        self.assertEqual(self.st.devices_path.stat().st_mtime_ns, before, "a known record with signCount 0 is not rewritten")
        self.st.on_passkey_noted = lambda d: (_ for _ in ()).throw(RuntimeError("no running event loop"))
        _, did3, _ = self.device("iPad", c := p57.Authenticator())
        self.edit_pk(did3, local=None, dev="f" * 22)
        self.st.passkey_count(did3, wire.b64u(c.id), 0)               # a caller without a loop: noted, repaint on resume
        self.assertEqual(self.st.passkey_state(did3), "local")

    def test_moved_key_self_check_over_a_phone_scan_says_borrowed(self):
        a, did, s = self.moved()
        m = self.state_msg(s)
        self.run_(self.host._app(s, {"t": "pk_check", "n": m["n"], "fa": self.fa(a, wire.unb64u(m["fa_ch"])),
                                     "aa": "cross-platform"}))
        self.assertEqual(self.to(s, "pk_check_res")[-1]["s"], "borrowed")

    def test_self_check_refuses_a_wrong_key_a_replay_and_a_stale_nonce(self):
        a, did, s = self.moved()
        m = self.state_msg(s)
        other = p57.Authenticator()
        self.run_(self.host._app(s, {"t": "pk_check", "n": m["n"], "fa": self.fa(other, wire.unb64u(m["fa_ch"]))}))
        self.assertEqual(self.to(s, "pk_check_res")[-1], {"t": "pk_check_res", "ok": False})
        good = self.fa(a, wire.unb64u(m["fa_ch"]))
        self.run_(self.host._app(s, {"t": "pk_check", "n": m["n"], "fa": good}))
        self.assertEqual(self.to(s, "pk_check_res")[-1]["ok"], False, "one challenge, one try")
        m2 = self.state_msg(s)
        self.run_(self.host._app(s, {"t": "pk_check", "n": m["n"], "fa": self.fa(a, wire.unb64u(m2["fa_ch"]))}))
        self.assertEqual(self.to(s, "pk_check_res")[-1]["ok"], False, "an old n never matches")
        self.assertNotIn("local", self.st.devices()[did]["pk"])
        self.assertEqual(self.st.passkey_state(did), "unknown")

    # ------------------------------------------------------------------ 3. nobody can approve → the Agent J host terminal
    def test_only_keyed_remote_lost_its_key_terminal_approves(self):
        old = p57.Authenticator()
        _, did, s = self.device("iPhone Air", old)                  # the owner's only remote with a key; the key is gone
        self.device("iPad")                                          # another remote, no key
        self.run_(self.host._app(s, {"t": "pk_peer_req", "r": "r1"}))
        res = self.to(s, "pk_peer_res")[-1]
        self.assertEqual((res["ok"], res["mode"], res["online"], res["host"]), (True, "peer", 0, 1))
        wrong = "000000" if res["code"] != "000000" else "111111"
        self.assertEqual(self.run_(self.host.pkpeer.host_approve(wrong)), {"ok": False, "error": "no_request"})
        self.assertEqual(self.run_(self.host.pkpeer.host_approve(None)), {"ok": False, "error": "no_request"})
        ok = self.run_(self.host.pkpeer.host_approve(res["code"]))
        self.assertEqual(ok, {"ok": True, "name": "iPhone Air"})
        self.assertEqual(self.to(s, "pk_peer_done")[-1], {"t": "pk_peer_done", "id": res["id"], "ok": True, "by": pkpeer.HOST_BY})
        offer = self.to(s, "pk_offer")[-1]
        self.assertEqual(offer["peer"], res["id"])
        new = p57.Authenticator()
        self.run_(self.host._app(s, new.register(wire.unb64u(offer["n"]))))
        self.assertEqual(self.to(s, "pk_reg_res")[-1], {"t": "pk_reg_res", "ok": True})
        self.assertEqual(self.st.devices()[did]["pk"]["id"], wire.b64u(new.id), "the lost key is replaced")
        line = json.loads(self.st.approvals_path.read_text().strip().splitlines()[-1])
        self.assertEqual((line["reason"], line["device"], line["decision"]), ("pk_peer", "host-terminal", "allow"))
        self.assertTrue(any("Agent J 主机终端" in n for n in self.notices), "every remote is told")
        self.assertEqual(self.run_(self.host.pkpeer.host_approve(res["code"])), {"ok": False, "error": "no_request"},
                         "one approval, one registration")

    def test_device_without_key_still_needs_an_approval(self):
        bkey = p57.Authenticator()
        _, _, sB = self.device("iPhone", bkey)
        _, D, sD = self.device("Mac Chrome")
        self.run_(self.host._app(sD, {"t": "pk_peer_req", "r": "r1"}))
        res = self.to(sD, "pk_peer_res")[-1]
        self.assertEqual((res["mode"], res["online"]), ("peer", 1))
        self.assertEqual(self.to(sD, "pk_offer"), [], "no offer before an approval")
        self.assertEqual(len(self.to(sB, "pk_peer_ask")), 1, "the keyed remote gets the card")
        self.assertNotIn("host", res, "another remote can approve: the terminal way is not offered")
        # security review B1: while a remote could approve, the terminal cannot (a same-uid Agent must not mint a key)
        self.assertEqual(self.run_(self.host.pkpeer.host_approve(res["code"])), {"ok": False, "error": "approver_exists"})
        self.assertEqual(self.to(sD, "pk_offer"), [])

    def test_wrong_codes_at_the_terminal_pause_it_and_tell_the_owner(self):
        _, _, s = self.device("iPhone Air", p57.Authenticator())
        self.run_(self.host._app(s, {"t": "pk_peer_req", "r": "r1"}))
        res = self.to(s, "pk_peer_res")[-1]
        wrong = [c for c in ("000001", "000002", "000003", "000004", "000005") if c != res["code"]][:pkpeer.HOST_MISS_N]
        for c in wrong:
            self.assertEqual(self.run_(self.host.pkpeer.host_approve(c))["error"], "no_request")
        self.assertEqual(self.run_(self.host.pkpeer.host_approve(res["code"])), {"ok": False, "error": "rate"}, "paused, even right")
        self.assertTrue(any("连续输错" in n for n in self.notices), "every remote is told")

    # ------------------------------------------------------------------ the terminal path is the owner's, never an Agent's
    def test_terminal_command_is_sensitive_and_has_resume_bar(self):
        self.assertIn("pk_peer_host", ctl_auth.SENSITIVE)
        src = pathlib.Path(serve.__file__).read_text()
        self.assertIn('if claimed and cmd in ("resume", "pk_peer_host"):', src)
        sent = []

        class W:
            pass

        async def ctl_send(w, obj):
            sent.append(obj)
        self.host._ctl_send = ctl_send
        self.assertFalse(self.run_(self.host._ctl_gate(W(), {"cmd": "pk_peer_host", "code": "123456"}, 1)))
        self.assertEqual(sent[-1], {"ok": False, "error": "claim_required"})
        import time as _t
        from agentj import gate

        def claimed(**kw):
            self.host.ctl_claims = {"tok": (4242, _t.monotonic() + 30)}
            return self.run_(self.host._ctl_gate(W(), {"cmd": "pk_peer_host", "code": "123456", "claim": "tok", **kw}, 4242))
        # review B1: a claim + a terminal is not enough — no remote pairing password set → refused
        self.assertFalse(claimed())
        self.assertEqual(sent[-1], {"ok": False, "error": "no_passphrase"})
        gate.set_passphrase(self.st, "correct horse battery")
        self.assertFalse(claimed(**{"pass": "wrong password!"}))
        self.assertEqual(sent[-1]["error"], "passphrase")
        self.assertTrue(claimed(**{"pass": "correct horse battery"}), "the owner's password passes")
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as e:
            cli.main(["faceid", "--help"])
        self.assertEqual(e.exception.code, 0)
        self.assertIn("approve", out.getvalue())


if __name__ == "__main__":
    unittest.main()
