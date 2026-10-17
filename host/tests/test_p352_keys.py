"""P352: 「模型和 API Key」 — keys_get / keys_op / keys_set (agentj/keys.py).

A fake host key file in a temp dir (never the real service env file); harness login probes stubbed. Listing: catalog rows set /
unset, a tail only for a value ≥ 24 chars, third-party names filtered (secret-looking, not AGENTJ_*), no value in the page.
Copy / delete: refused without a passkey, with a bad / reused nonce, an unknown page token or a wrong assertion; a verified
assertion over (token, "key_"+op, nonce, digest(op, name)) copies the value to THAT session only / deletes the line keeping
every other line and 0600. Every reply carries a fresh nonce. The audit line never holds the value or its tail. keys_set builds
the ordinary secret card with dest env:<key file>#NAME.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
import pathlib
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from agentj import elevate, harness, keys, passkey, serve, wire  # noqa: E402

from test_l1 import Phone, _host, _ready, _state  # noqa: E402
from test_p57_passkey import Authenticator, pk_record  # noqa: E402

LONG = "p352-fake-anthropic-value-DO-NOT-LEAK-wxyz"     # ≥ 24 chars: tail "wxyz"
SHORT = "p352short"                                       # < 24: no tail
THIRD = "p352-eleven-value-DO-NOT-LEAK-0123456789"
WOLFRAM = "p352-" + "wolfram"   # a fake value, assembled: no secret-shaped literal for the public scan
ENV = ("# Agent J host env\n"
       f"ANTHROPIC_API_KEY={LONG}\n"
       f"export DEEPSEEK_API_KEY='{SHORT}'\n"
       "\n"
       f"ELEVENLABS_API_KEY=\"{THIRD}\"\n"
       "AGENTJ_SERVICE_TOKEN=p352-internal\n"
       "PATH_EXTRA=/opt/bin\n"
       f"WOLFRAM_APP_SECRET={WOLFRAM}\n"
       "EMPTY_TOKEN=\n"
       "OTHER=1\n")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = pathlib.Path(self.tmp.name)
        self.patches = [mock.patch.dict(os.environ, {"HOME": str(self.d / "home"), "XDG_CONFIG_HOME": str(self.d / "cfg")})]
        (self.d / "home").mkdir()
        self.envf = self.d / "unit" / "agentj.env"
        self.envf.parent.mkdir()
        self.envf.write_text(ENV)
        os.chmod(self.envf, 0o600)
        self.patches += [mock.patch.object(keys, "env_path", lambda: str(self.envf)),
                         mock.patch.object(harness, "claude_login", lambda environ=None: ("ok", "login")),
                         mock.patch.object(harness, "codex_login", lambda: ("warn", "no login found")),
                         mock.patch("shutil.which", lambda *_a, **_k: None)]   # never a real systemctl
        for p in self.patches:
            p.start()
        self.st = _state(self.tmp.name)
        self.sent = []
        self.host = _host(self.st, self.sent)
        self.ph = Phone(self.st)
        self.s = _ready(self.host, self.ph)
        self.s.p33 = True
        self.a = Authenticator()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def of(self, t):
        return [o for _, o in self.sent if o["t"] == t]

    def app(self, obj):
        asyncio.run(self.host._app(self.s, obj))

    def get(self):
        self.app({"t": "keys_get", "r": "r1"})
        return self.of("keys")[-1]

    def fa(self, page, op, name, n=None, tid=None):
        ch = passkey.elevate_challenge(self.ph.channel, self.ph.did, tid or page["id"], "key_" + op, n or page["n"],
                                       keys.digest(op, name))
        x = self.a.assert_(ch, b"\0", nonce=b"\0")
        return {k: x[k] for k in ("id", "cd", "ad", "sig")}

    def op(self, page, op, name, **kw):
        self.app({"t": "keys_op", "r": "r2", "op": op, "name": name, "id": kw.get("id", page["id"]),
                  "n": kw.get("n", page["n"]), **({"fa": kw["fa"]} if "fa" in kw else {})})
        return self.of("keys_res")[-1]

    def audit_text(self):
        p = elevate.log_path(self.st)
        return p.read_text() if p.exists() else ""

    def no_leak(self):
        blob = self.audit_text() + (self.st.log_path.read_text() if self.st.log_path.exists() else "")
        for v in (LONG, SHORT, THIRD, LONG[-4:]):
            self.assertNotIn(v, blob)


class Listing(Base):
    def test_catalog_and_third_party(self):
        page = self.get()
        self.assertTrue(wire.is_rid(page["r"]) and len(page["id"]) == 32 and len(page["n"]) == 32)
        self.assertNotIn("fa", page, "no passkey on this device")
        base = {v["id"]: v for v in page["base"]}
        self.assertEqual(list(base)[:2], ["anthropic", "openai"])
        self.assertEqual(len(base), 12)
        self.assertEqual(base["ark"]["name"], {"zh": "火山方舟", "en": "Volcano Ark"})
        a = base["anthropic"]["rows"]
        self.assertEqual({k: a[0][k] for k in ("type", "name", "set", "state")},
                         {"type": "oauth", "name": "claude", "set": True, "state": "in"})
        self.assertEqual((a[0]["use"], a[0]["use_name"], a[0]["use_src"]), ("key", "ANTHROPIC_API_KEY", "agentj"), "P362")
        self.assertEqual(a[1], {"type": "sub", "name": "CLAUDE_CODE_OAUTH_TOKEN", "set": False})
        self.assertEqual(a[2]["name"], "ANTHROPIC_API_KEY")
        self.assertEqual((a[2]["set"], a[2]["tail"]), (True, LONG[-4:]))
        self.assertEqual(base["openai"]["rows"][0]["state"], "out")
        ds = base["deepseek"]["rows"][0]
        self.assertEqual(ds, {"type": "key", "name": "DEEPSEEK_API_KEY", "set": True}, "short value: no tail")
        names = [t["name"] for t in page["third"]]
        self.assertEqual(names, ["ELEVENLABS_API_KEY", "WOLFRAM_APP_SECRET"])
        self.assertEqual(page["third"][0]["tail"], THIRD[-4:])
        blob = json.dumps(page)
        for v in (LONG, SHORT, THIRD):
            self.assertNotIn(v, blob)

    def test_fa_and_in_use(self):
        self.st.set_passkey(self.ph.pub, pk_record(self.a))
        self.host.agent_cfg = {"kind": "claude", "dir": str(self.d)}
        page = self.get()
        self.assertEqual(page["fa"], wire.b64u(self.a.id))
        row = [r for r in page["base"][0]["rows"] if r["name"] == "ANTHROPIC_API_KEY"][0]
        self.assertTrue(row.get("in_use"))
        self.assertNotIn("in_use", [r for r in page["base"][0]["rows"] if r["name"] == "CLAUDE_CODE_OAUTH_TOKEN"][0])

    def test_missing_file_lists_nothing_set(self):
        self.envf.unlink()
        page = self.get()
        self.assertEqual(page["third"], [])
        self.assertFalse(any(r["set"] for v in page["base"] for r in v["rows"] if r["type"] != "oauth"))


class Ops(Base):
    def setUp(self):
        super().setUp()
        self.st.set_passkey(self.ph.pub, pk_record(self.a))

    def test_copy_with_face_id(self):
        page = self.get()
        res = self.op(page, "copy", "ANTHROPIC_API_KEY", fa=self.fa(page, "copy", "ANTHROPIC_API_KEY"))
        self.assertEqual((res["ok"], res["op"], res["name"], res["value"]), (True, "copy", "ANTHROPIC_API_KEY", LONG))
        self.assertEqual([c for c, o in self.sent if o["t"] == "keys_res"], [11], "only this session")
        self.assertTrue(len(res["n"]) == 32 and res["n"] != page["n"], "a fresh nonce")
        # the fresh nonce re-arms the same page token
        res2 = self.op(page, "copy", "DEEPSEEK_API_KEY", n=res["n"], fa=self.fa(page, "copy", "DEEPSEEK_API_KEY", n=res["n"]))
        self.assertEqual((res2["ok"], res2["value"]), (True, SHORT))
        rec = [json.loads(x) for x in self.audit_text().splitlines()]
        self.assertEqual([(r["kind"], r["op"], r["name"], r["result"], r["device"]) for r in rec],
                         [("keys", "copy", "ANTHROPIC_API_KEY", "copied", self.ph.did),
                          ("keys", "copy", "DEEPSEEK_API_KEY", "copied", self.ph.did)])
        self.assertEqual(elevate.check_record(self.st, rec[0]), "passkey")
        self.no_leak()

    def test_nonce_reuse_refused(self):
        page = self.get()
        fa = self.fa(page, "copy", "ANTHROPIC_API_KEY")
        self.assertTrue(self.op(page, "copy", "ANTHROPIC_API_KEY", fa=fa)["ok"])
        res = self.op(page, "copy", "ANTHROPIC_API_KEY", fa=fa)          # the same nonce + assertion again
        self.assertEqual((res["ok"], res["why"]), (False, "passkey"))
        self.assertNotIn("value", res)
        self.assertEqual(len(res["n"]), 32)
        self.no_leak()

    def test_bad_nonce_spends_the_real_one(self):
        page = self.get()
        res = self.op(page, "copy", "ANTHROPIC_API_KEY", n="0" * 32, fa=self.fa(page, "copy", "ANTHROPIC_API_KEY", n="0" * 32))
        self.assertEqual((res["ok"], res["why"]), (False, "passkey"))
        res = self.op(page, "copy", "ANTHROPIC_API_KEY", fa=self.fa(page, "copy", "ANTHROPIC_API_KEY"))
        self.assertEqual((res["ok"], res["why"]), (False, "passkey"), "one attempt per nonce")

    def test_wrong_assertion_refused(self):
        other = Authenticator()
        for make in (lambda p: self.fa(p, "delete", "ANTHROPIC_API_KEY"),      # another op
                     lambda p: self.fa(p, "copy", "DEEPSEEK_API_KEY"),         # another name
                     lambda p: {k: v for k, v in other.assert_(b"x" * 32, b"\0", nonce=b"\0").items()
                                if k in ("id", "cd", "ad", "sig")}):             # another credential
            page = self.get()
            res = self.op(page, "copy", "ANTHROPIC_API_KEY", fa=make(page))
            self.assertEqual((res["ok"], res["why"]), (False, "passkey"))
            self.assertNotIn("value", res)
        res = self.op(self.get(), "copy", "ANTHROPIC_API_KEY")               # no fa at all
        self.assertEqual((res["ok"], res["why"]), (False, "passkey"))
        self.assertIn("ANTHROPIC_API_KEY=", self.envf.read_text())
        self.no_leak()

    def test_unknown_token_and_shape(self):
        page = self.get()
        res = self.op(page, "copy", "ANTHROPIC_API_KEY", id="f" * 32, fa=self.fa(page, "copy", "ANTHROPIC_API_KEY", tid="f" * 32))
        self.assertEqual((res["ok"], res["why"]), (False, "gone"))
        res = self.op(page, "steal", "ANTHROPIC_API_KEY")
        self.assertEqual((res["ok"], res["why"]), (False, "shape"))
        res = self.op(page, "copy", "BAD NAME")
        self.assertEqual((res["ok"], res["why"]), (False, "shape"))

    def test_tokens_kept_per_device(self):
        pages = [self.get() for _ in range(keys.TOKENS_PER_DEVICE + 1)]
        res = self.op(pages[0], "copy", "ANTHROPIC_API_KEY", fa=self.fa(pages[0], "copy", "ANTHROPIC_API_KEY"))
        self.assertEqual(res["why"], "gone", "the oldest page token is dropped")
        res = self.op(pages[1], "copy", "ANTHROPIC_API_KEY", fa=self.fa(pages[1], "copy", "ANTHROPIC_API_KEY"))
        self.assertTrue(res["ok"])

    def test_delete_keeps_other_lines(self):
        page = self.get()
        res = self.op(page, "delete", "DEEPSEEK_API_KEY", fa=self.fa(page, "delete", "DEEPSEEK_API_KEY"))
        self.assertEqual((res["ok"], res["op"], res["name"]), (True, "delete", "DEEPSEEK_API_KEY"))
        self.assertNotIn("value", res)
        self.assertEqual(self.envf.read_text(), ENV.replace(f"export DEEPSEEK_API_KEY='{SHORT}'\n", ""))
        self.assertEqual(stat.S_IMODE(self.envf.stat().st_mode), 0o600)
        res = self.op(page, "delete", "DEEPSEEK_API_KEY", n=res["n"],
                      fa=self.fa(page, "delete", "DEEPSEEK_API_KEY", n=res["n"]))
        self.assertEqual((res["ok"], res["why"]), (False, "missing"))
        rec = [json.loads(x) for x in self.audit_text().splitlines()]
        self.assertEqual([r["result"] for r in rec], ["deleted", "missing"])
        self.no_leak()

    def test_copy_missing(self):
        page = self.get()
        res = self.op(page, "copy", "ZHIPU_API_KEY", fa=self.fa(page, "copy", "ZHIPU_API_KEY"))
        self.assertEqual((res["ok"], res["why"]), (False, "missing"))


class NoPasskey(Base):
    def test_refused_without_passkey(self):
        page = self.get()
        for op in ("copy", "delete"):
            res = self.op(page, op, "ANTHROPIC_API_KEY", n=self.of("keys_res")[-1]["n"] if self.of("keys_res") else page["n"])
            self.assertEqual((res["ok"], res["why"]), (False, "no_passkey"))
        self.assertEqual(self.envf.read_text(), ENV)
        self.no_leak()

    def test_unapproved_device(self):
        other = Phone(self.st, name="平板", with_key=False)
        s2 = _ready(self.host, other, cid=12)
        s2.p33 = True
        asyncio.run(self.host._app(s2, {"t": "keys_get", "r": "r1"}))
        page = self.of("keys")[-1]
        asyncio.run(self.host._app(s2, {"t": "keys_op", "r": "r2", "op": "copy", "name": "ANTHROPIC_API_KEY",
                                        "id": page["id"], "n": page["n"]}))
        res = self.of("keys_res")[-1]
        self.assertEqual((res["ok"], res["why"]), (False, "passkey"))


class SetCard(Base):
    def test_set_builds_secret_card(self):
        cards = []

        async def request(card, gone=None, on_card=None):
            cards.append(card)
            return {"result": "saved"}
        self.host.elevate.request = request

        async def go():
            await self.host._app(self.s, {"t": "keys_set", "r": "r3", "name": "ARK_API_KEY"})
            await self.host._app(self.s, {"t": "keys_set", "r": "r4", "name": "1BAD"})
            await self.host._app(self.s, {"t": "keys_set", "r": "r5", "name": "MY_TOOL_TOKEN"})
            for _ in range(100):
                if len(cards) >= 2:
                    break
                await asyncio.sleep(0.01)
        asyncio.run(go())
        res = self.of("keys_res")
        self.assertEqual([(r["ok"], r["op"], r["name"], r.get("why")) for r in res],
                         [(True, "set", "ARK_API_KEY", None), (False, "set", "", "shape"), (True, "set", "MY_TOOL_TOKEN", None)])
        self.assertEqual(len(cards), 2)
        c = cards[0]
        self.assertEqual((c["kind"], c["name"], c["dest_spec"]["mode"], c["dest_spec"]["key"]),
                         ("secret", "ARK_API_KEY", "env", "ARK_API_KEY"))
        self.assertEqual(c["dest_spec"]["path"], str(self.envf.resolve()))
        self.assertEqual(c["verify"], "")
        self.assertIn("API Key", c["purpose"])
        self.assertEqual(cards[1]["purpose"], "MY_TOOL_TOKEN API Key")
        rec = [json.loads(x) for x in self.audit_text().splitlines()]
        self.assertEqual([(r["op"], r["name"], r["result"]) for r in rec],
                         [("set", "ARK_API_KEY", "card"), ("set", "MY_TOOL_TOKEN", "card")])

    def test_allowed_in_serve(self):
        for t in keys.PHONE_TYPES:
            self.assertIn(t, serve.P33_ONLY)


if __name__ == "__main__":
    unittest.main()
