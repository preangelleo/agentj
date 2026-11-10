"""0.30.6: Settings → Mail trusted-server checkbox is tickable through the EXISTING signed mail_set (host_write) path.

Page contract (web/public/js/mail.js `trustPatch`, pinned by web/test/p0306-mail-servid-toggle.mjs):
  untick (pinned)              → mail_set cfg {"auth_servid": ""}            servid_merge records the known host in servid_optout
  tick (servid_hint unpinned_known) → mail_set cfg {"servid_optout": [list without this IMAP host]}
                                                                              servid_merge pins KNOWN_SERVID (host table, never mail)
No new field, endpoint or refusal code: the target is {kind, cfg, before}, signed over wire.js canonicalJson, with the
offer nonce, ±120 s and Face ID (neither shape is an owner→guest downgrade).
"""
import _hermetic  # noqa: F401,I001
import hashlib
import json
import shutil
import time
import unittest

from agentj import action_policy, controls, mail, wire
import test_0304_mail_sign_js as base

HostExecution = base.HostExecution
UNTICK = {"auth_servid": ""}
TICK = {"servid_optout": []}          # what the page sends for a Gmail-only opt-out list


@unittest.skipUnless(shutil.which("node"), "node not installed")
class TrustedServerToggle(HostExecution):
    _prepare = base.PhoneSignedLikeThePage._prepare
    _sign_as_page = base.PhoneSignedLikeThePage._sign_as_page
    _refusals = base.PhoneSignedLikeThePage._refusals

    def _gmail(self, **kw):
        cfg = mail.apply_preset(mail.blank_config(), "gmail", "owner@example.com")
        cfg.update(allow=[{"addr": "boss@example.com", "role": "owner"}], **kw)
        mail.save_config(self.st, mail.validate(cfg))

    async def _send(self, cfg, auth, r="d"):
        await self.h.on_mail(self.session, "mail_set", {"r": r * 16, "cfg": cfg, **({"authorization": auth} if auth else {})})
        return self.sent[-1][1]

    async def _signed(self, cfg, r="d"):
        prepared, n = await self._prepare("mail_set", cfg)
        return prepared, await self._send(cfg, self._sign_as_page(prepared, n), r)

    def _pin(self):
        c = mail.read_config(self.st)
        return c["auth_servid"], c["servid_optout"]

    async def test_signed_untick_then_tick_round_trip(self):
        self._gmail(auth_servid="mx.google.com")
        prepared, res = await self._signed(UNTICK)
        self.assertTrue(res["ok"], res.get("why"))
        self.assertIn("不自动固定校验服务器", prepared["summary"])       # the Face ID sheet names the opt-out
        self.assertEqual(self._pin(), ("", ["imap.gmail.com"]))
        v = res["view"]
        self.assertEqual((v["servid_hint"], v["servid_status"]["pinned"], v["config"]["servid_optout"]),
                         ("unpinned_known", False, ["imap.gmail.com"]))
        prepared, res = await self._signed(TICK, "e")
        self.assertTrue(res["ok"], res.get("why"))
        self.assertIn("mx.google.com", prepared["summary"])               # the pinned value is shown before Face ID
        self.assertEqual(self._pin(), ("mx.google.com", []))
        self.assertEqual(res["view"]["servid_status"]["source"], "auto")   # the 「Gmail 已自动固定」 hint stays truthful
        self.assertEqual(self._refusals(), [])

    async def test_tick_keeps_other_hosts_opt_outs(self):
        self._gmail(servid_optout=["imap.gmail.com", "imap.example.org"])
        _, res = await self._signed({"servid_optout": ["imap.example.org"]})
        self.assertTrue(res["ok"], res.get("why"))
        self.assertEqual(self._pin(), ("mx.google.com", ["imap.example.org"]))

    async def test_opt_out_persists_across_validate_merge_saves_and_restart(self):
        self._gmail(auth_servid="mx.google.com")
        await self._signed(UNTICK)
        old = mail.read_config(self.st)
        self.assertEqual(mail.validate(old), old)
        self.assertEqual(mail.servid_merge(old, mail.validate(old)), old)
        self.assertEqual(action_policy.mail_config(old, {"poll_minutes": 7})["auth_servid"], "")
        _, res = await self._signed({"poll_minutes": 7}, "f")                 # an unrelated signed save never re-pins
        self.assertTrue(res["ok"], res.get("why"))
        self.assertEqual(self._pin(), ("", ["imap.gmail.com"]))
        s = mail.read_state(self.st)
        s["servid_migrated"] = False                                       # even a fresh upgrade migration honours it
        mail.save_state(self.st, s)
        mail.Mail(self.h)._migrate()
        self.assertEqual(self._pin(), ("", ["imap.gmail.com"]))
        self.assertEqual(mail.Mail(self.h).view()["servid_hint"], "unpinned_known")

    async def test_tick_on_custom_server_never_pins_from_mail(self):
        cfg = mail.blank_config()
        cfg.update(preset="custom", address="owner@example.com", username="owner@example.com",
                   imap={"host": "imap.example.com", "port": 993, "security": "ssl"},
                   smtp={"host": "smtp.example.com", "port": 465, "security": "ssl"})
        mail.save_config(self.st, mail.validate(cfg))
        s = mail.read_state(self.st)
        s["auth_seen"] = {"checked": 5, "with_results": 5, "servid": "sender.example.net"}
        mail.save_state(self.st, s)
        self.assertEqual(mail.Mail(self.h).view()["servid_hint"], "unpinned_custom")   # the page offers no tick here
        _, res = await self._signed(TICK)
        self.assertTrue(res["ok"], res.get("why"))
        self.assertEqual(self._pin(), ("", []))

    async def test_unsigned_forged_tampered_replayed_and_no_face_id_are_refused(self):
        self._gmail(auth_servid="mx.google.com")
        before = self._pin()
        res = await self._send(UNTICK, None)                                   # unsigned
        self.assertEqual((res["ok"], res["why"]), (False, "shape"))
        prepared, n = await self._prepare("mail_set", UNTICK)                 # forged: signature bytes changed
        auth = self._sign_as_page(prepared, n)
        bad = bytearray(wire.unb64u(auth["sig"])); bad[0] ^= 1                 # noqa: E702
        res = await self._send(UNTICK, {**auth, "sig": wire.b64u(bytes(bad))})
        self.assertEqual((res["ok"], res["why"]), (False, "bad_signature"))
        prepared, n = await self._prepare("mail_set", UNTICK)                 # tampered: signed untick, sent with a new pin
        res = await self._send({"auth_servid": "evil.example.com"}, self._sign_as_page(prepared, n))
        self.assertEqual((res["ok"], res["why"]), (False, "bad_signature"))
        prepared, n = await self._prepare("mail_set", UNTICK)                 # signature only, no Face ID
        auth = self._sign_as_page(prepared, n)
        res = await self._send(UNTICK, {k: v for k, v in auth.items() if k != "fa"})
        self.assertEqual((res["ok"], res["why"]), (False, "passkey_required"))
        # an owner→guest downgrade (the only no-Face-ID shape) cannot carry the toggle along
        mixed = {"allow": [{"addr": "boss@example.com", "role": "guest"}], "auth_servid": ""}
        prepared, n = await self._prepare("mail_set", mixed)
        self.assertNotEqual(prepared.get("level"), "signed")
        auth = self._sign_as_page(prepared, n)
        res = await self._send(mixed, {k: v for k, v in auth.items() if k != "fa"})
        self.assertEqual((res["ok"], res["why"]), (False, "passkey_required"))
        self.assertEqual(self._pin(), before)
        # replay: a valid untick applies once; the same authorization later cannot undo a re-tick
        prepared, n = await self._prepare("mail_set", UNTICK)
        auth = self._sign_as_page(prepared, n)
        self.assertTrue((await self._send(UNTICK, auth))["ok"])
        _, res = await self._signed(TICK, "e")
        self.assertTrue(res["ok"], res.get("why"))
        res = await self._send(UNTICK, auth, "f")
        self.assertFalse(res["ok"])
        self.assertIn(res["why"], ("stale", "replay"))
        self.assertEqual(self._pin(), ("mx.google.com", []))
        # a ticket signed for one `before` does not verify once the config moved on
        prepared, n = await self._prepare("mail_set", UNTICK)
        auth = self._sign_as_page(prepared, n)
        _, res = await self._signed({"poll_minutes": 9}, "a")
        self.assertTrue(res["ok"], res.get("why"))
        res = await self._send(UNTICK, auth, "b")
        self.assertEqual((res["ok"], res["why"]), (False, "bad_signature"))
        self.assertEqual(self._pin(), ("mx.google.com", []))
        rows = self._refusals()
        self.assertTrue(rows and all(r.startswith("refused:") for r in rows), rows)

    def test_toggle_targets_canonical_bytes_equal_wire_js(self):
        before = mail.validate({**mail.apply_preset(mail.blank_config(), "gmail", "owner@example.com"),
                                "auth_servid": "", "servid_optout": ["imap.gmail.com", "imap.example.org"]})
        before["triage"] = {**before["triage"], "usd_per_day": 0.0, "usd_per_call": 0.25}
        targets = [{"kind": "mail_set", "cfg": cfg, "before": before}
                   for cfg in (UNTICK, TICK, {"servid_optout": ["imap.example.org"]}, {"auth_servid": "mx.google.com"})]
        for wire_js in base.WIRE_JS:
            page = base.js_texts(targets, wire_js)
            self.assertEqual(page, [controls.object_text("host_write", o) for o in targets], wire_js)
            self.assertEqual([hashlib.sha256(p.encode()).hexdigest() for p in page],
                             [controls.object_digest("host_write", o) for o in targets])


if __name__ == "__main__":
    unittest.main()
