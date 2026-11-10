"""0304 A4 root cause: a phone mail settings write (host_write) signed by the REAL page code must verify on the host.

Field event (0.30.3): Settings → Mail 「只认这台服务器写的校验结果」 + Face ID → controls.log `host_write refused:bad_signature`.
The host's policy target carries `before` = mail.read_config(), whose `triage` block holds floats (`usd_per_day` 0.0 since
the 11-07 triage feature: mail_triage.validate stores round(float(v), 6)). Python json.dumps writes 0.0 as "0.0"; the page
receives the target as JSON (0.0 → 0) and wire.js canonicalJson writes "0". Different SHA-256 → every mailuv1 mail_set /
mail_test from a phone failed. The older tests signed with Python object_digest on both sides (test_p503_policy
MailPhoneUV) or verified the page with the JS fake host (web/test/p526.mjs), so nothing crossed Python ↔ JS.
"""
import _hermetic  # noqa: F401,I001
import json
import math
import pathlib
import random
import shutil
import struct
import subprocess
import time
import unittest

from agentj import action_policy, controls, mail, passkey, wire
from test_p503_policy import HostExecution

AJ = pathlib.Path(__file__).resolve().parents[1].parent
WIRE_JS = (AJ / "web" / "public" / "proto" / "wire.js", AJ / "protocol" / "wire.js")


def js_texts(objs, wire_js=WIRE_JS[0]):
    """The page's own bytes: wire.js canonicalJson over each object after a JSON round trip (as the phone receives it)."""
    js = (f"import {{ canonicalJson }} from {json.dumps(wire_js.as_uri())};\n"
          "let s='';process.stdin.on('data',d=>s+=d);process.stdin.on('end',()=>{"
          "console.log(JSON.stringify(JSON.parse(s).map(canonicalJson)));});")
    r = subprocess.run(["node", "--input-type=module", "-e", js], input=json.dumps(objs, ensure_ascii=False),
                       capture_output=True, text=True, timeout=30)
    if r.returncode:
        raise AssertionError(r.stderr)
    return json.loads(r.stdout)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class CanonicalParity(unittest.TestCase):
    def test_host_write_text_equals_wire_js(self):
        objs = [{"kind": "mail_set", "cfg": {"auth_servid": "mx.google.com"}, "before": mail.blank_config()},
                {"kind": "mail_test", "before": mail.blank_config()},
                {"n": [0.0, -0.0, 1.0, 0.01, 0.0001, 1e-05, 1.5e-07, 2.5e-06, 1e16, 1e21, 1.5e300, 123.456, 7, -3, 2 ** 53]},
                {"名字": "王 · 主人", "z": None, "a": True, "é": "x", "\U0001f600": 1, "～": 2, "s": "q\"\\\n\t\x01\x7f "}]
        for wire_js in WIRE_JS:
            self.assertEqual(js_texts(objs, wire_js), [controls.object_text("host_write", o) for o in objs], wire_js)

    def test_seeded_double_fuzz_matches_json_stringify(self):
        # security r1 L5: committed, seeded, fast — the hand-run 80 011 / 20 003 double fuzz in small.
        rnd = random.Random(30405)
        xs = []
        while len(xs) < 4800:
            x = struct.unpack("<d", struct.pack("<Q", rnd.getrandbits(64)))[0]   # any bit pattern
            if math.isfinite(x):
                xs.append(x)
        for _ in range(100):
            e = rnd.randint(-8, 23)
            xs += [rnd.random() * 10 ** e, -float(rnd.randint(0, 10 ** 6)) * 10 ** rnd.randint(-7, 22), round(rnd.random(), 6)]
        xs += [1e21, 1e-7, 1e-6, 9.999999999999999e20, 5e-324, 1.7976931348623157e308, float(2 ** 53), float(2 ** 53 + 2)]
        js = ("let s='';process.stdin.on('data',d=>s+=d);process.stdin.on('end',()=>{"
              "console.log(JSON.stringify(JSON.parse(s).map(x=>JSON.stringify(x))));});")
        r = subprocess.run(["node", "-e", js], input=json.dumps(xs), capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        want = json.loads(r.stdout)
        bad = [(x, w, controls._js_number(x)) for x, w in zip(xs, want) if controls._js_number(x) != w]
        self.assertEqual(bad[:5], [], f"{len(bad)} of {len(xs)} differ")

    def test_other_canonical_actions_follow(self):
        for action in ("heartbeat_write", "event_write", "owner_hosts_set"):
            o = {"x": 1.0, "y": [0.5, 0.0]}
            self.assertEqual(controls.object_text(action, o), js_texts([o])[0])


@unittest.skipUnless(shutil.which("node"), "node not installed")
class PhoneSignedLikeThePage(HostExecution):
    async def _prepare(self, kind, cfg=None):
        target = {"kind": kind, **({"cfg": cfg} if kind == "mail_set" else {})}
        await self.h._app(self.session, {"t": "policy_prepare", "r": "c" * 16, "action": "host_write", "target": target})
        m = self.sent[-1][1]
        self.assertEqual(m["t"], "policy_ready")
        return json.loads(json.dumps(m)), m["n"]      # what the phone holds: the message after the wire's JSON

    def _sign_as_page(self, prepared, n):
        digest = __import__("hashlib").sha256(js_texts([prepared["target"]])[0].encode()).hexdigest()
        ts = int(time.time() * 1000)
        auth = {"n": n, "ts": ts, "sig": wire.b64u(self.ph.sk.sign(
            controls.signed_message(self.h.channel, self.ph.did, "host_write", n, ts, digest)))}
        auth["fa"] = self.auth.assert_(passkey.elevate_challenge(self.h.channel, self.ph.did, n, "host_write", n, digest),
                                       b"0", nonce=b"0", flags=5)
        return auth

    def _seed(self, **kw):
        cfg = mail.blank_config()
        cfg.update(preset="custom", address="owner@example.com", username="owner@example.com",
                   imap={"host": "imap.example.com", "port": 993, "security": "ssl"},
                   smtp={"host": "smtp.example.com", "port": 465, "security": "ssl"},
                   allow=[{"addr": "boss@example.com", "role": "owner", "name": "老板"}], **kw)
        mail.save_config(self.st, mail.validate(cfg))

    def _refusals(self):
        p = self.st.controls_path
        rows = [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
        return [r["result"] for r in rows if r["action"] == "host_write" and r["result"] != "applied"]

    async def test_pin_servid_only_change_verifies(self):
        self._seed()
        cfg = {"auth_servid": "mx.example.com"}
        prepared, n = await self._prepare("mail_set", cfg)
        await self.h.on_mail(self.session, "mail_set", {"r": "d" * 16, "cfg": cfg, "authorization": self._sign_as_page(prepared, n)})
        self.assertTrue(self.sent[-1][1]["ok"], self.sent[-1][1].get("why"))
        self.assertEqual(mail.read_config(self.st)["auth_servid"], "mx.example.com")
        self.assertEqual(self._refusals(), [])

    async def test_every_settings_write_shape_verifies(self):
        for cfg in ({"poll_minutes": 11}, {"enabled": True}, {"auth": "strict"},
                    {"allow": [{"addr": "boss@example.com", "role": "guest"}]},
                    {"triage": {**mail.blank_config()["triage"], "usd_per_day": 1}},
                    {"triage": {**mail.blank_config()["triage"], "usd_per_day": 2.5}},
                    {"auth_servid": ""}):
            with self.subTest(cfg=cfg):
                self._seed(auth_servid="mx.example.com")
                prepared, n = await self._prepare("mail_set", cfg)
                await self.h.on_mail(self.session, "mail_set", {"r": "e" * 16, "cfg": cfg, "authorization": self._sign_as_page(prepared, n)})
                self.assertTrue(self.sent[-1][1]["ok"], self.sent[-1][1].get("why"))
        self.assertEqual(self._refusals(), [])

    async def test_mail_test_signature_verifies(self):
        from unittest.mock import patch
        self._seed()
        prepared, n = await self._prepare("mail_test")
        with patch.object(mail, "load_secret", return_value=None):
            await self.h.on_mail(self.session, "mail_test", {"r": "a" * 16, "authorization": self._sign_as_page(prepared, n)})
        self.assertEqual(self._refusals(), [])

    async def test_gmail_setup_preview_target_and_execution_agree(self):
        # security r1 M1: the known-provider pin is part of the single merge — the phone summary shows it, the signed
        # target {before, cfg} fixes it (mail_config is a function of those two only), and on_mail saves exactly that.
        cfg = {"preset": "gmail", "address": "owner@example.com", "allow": [{"addr": "boss@example.com", "role": "owner"}]}
        prepared, n = await self._prepare("mail_set", cfg)
        self.assertIn("mx.google.com", prepared["summary"])
        self.assertIn("可信邮件服务器", prepared["summary"])
        preview = action_policy.mail_config(prepared["target"]["before"], prepared["target"]["cfg"])
        self.assertEqual(preview["auth_servid"], "mx.google.com")
        await self.h.on_mail(self.session, "mail_set", {"r": "b" * 16, "cfg": cfg, "authorization": self._sign_as_page(prepared, n)})
        self.assertTrue(self.sent[-1][1]["ok"], self.sent[-1][1].get("why"))
        saved = mail.read_config(self.st)
        self.assertEqual({k: v for k, v in saved.items() if k != "generation"}, {k: v for k, v in preview.items() if k != "generation"})
        self.assertEqual(self.h.mail.view()["servid_status"]["source"], "auto")
        self.assertEqual(self._refusals(), [])
        # Clearing it on the phone records the opt-out in the same signed merge.
        prepared, n = await self._prepare("mail_set", {"auth_servid": ""})
        await self.h.on_mail(self.session, "mail_set", {"r": "c" * 16, "cfg": {"auth_servid": ""}, "authorization": self._sign_as_page(prepared, n)})
        self.assertEqual((mail.read_config(self.st)["auth_servid"], mail.read_config(self.st)["servid_optout"]), ("", ["imap.gmail.com"]))
        self.assertEqual(self._refusals(), [])

    def _auth_over(self, text):
        n = self.h.nonces.issue_mail(self.ph.did, __import__("hashlib").sha256(text.encode()).hexdigest())
        digest = __import__("hashlib").sha256(text.encode()).hexdigest()
        ts = int(time.time() * 1000)
        return {"n": n, "ts": ts, "sig": wire.b64u(self.ph.sk.sign(
            controls.signed_message(self.h.channel, self.ph.did, "host_write", n, ts, digest)))}

    async def test_unrepresentable_numbers_fail_closed(self):
        # security r1 L5 / L4: NaN / Infinity / a non-string key → shape; an int above 2^53 whose JS spelling differs
        # is also rejected as shape before hashing (ADR-0305).
        for odd in ({"x": float("nan")}, {"x": float("inf")}, {"x": float("-inf")}, {1: "x"}):
            target = {"kind": "mail_set", "cfg": odd, "before": mail.blank_config()}
            why = controls.check(self.st, self.h.nonces, self.h.channel, self.ph.did, self._auth_over("x"), "host_write", target)
            self.assertEqual(why, "shape", odd)
        with self.assertRaises(TypeError):
            controls.canonical_json({1: "x"})
        target = {"kind": "mail_set", "cfg": {"poll_minutes": 2 ** 53 + 1}, "before": mail.blank_config()}
        page = js_texts([target])[0]
        self.assertIn("9007199254740992", page)
        with self.assertRaises(ValueError):
            controls.object_text("host_write", target)
        why = controls.check(self.st, self.h.nonces, self.h.channel, self.ph.did, self._auth_over(page), "host_write", target)
        self.assertEqual(why, "shape")

    async def test_tampered_after_signing_still_refused(self):
        self._seed()
        prepared, n = await self._prepare("mail_set", {"auth_servid": "mx.example.com"})
        auth = self._sign_as_page(prepared, n)
        await self.h.on_mail(self.session, "mail_set", {"r": "f" * 16, "cfg": {"auth_servid": "evil.example.com"}, "authorization": auth})
        self.assertFalse(self.sent[-1][1]["ok"])
        self.assertEqual(mail.read_config(self.st)["auth_servid"], "")


if __name__ == "__main__":
    unittest.main()
