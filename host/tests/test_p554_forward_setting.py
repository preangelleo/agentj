"""P554 (chairman 10-27 12:23): 转发去向 is one Agent J host setting (`ui.forward_destination`) shared by every remote.

A change is the signed `forward_set` control (no Face ID), written like `agentj config set`, then the `preferences` broadcast
reaches every ready remote. `migrate: true` moves a remote's own pre-P554 local choice only while the host has none (the
first one wins). The format addition is a sparse key whose default "" means "not chosen yet": an existing config.json5
without it keeps validating and reads as "" (no migration script needed), and the key round-trips through the file.
"""
import _hermetic  # noqa: F401,I001  (never the real ~/.config / ~/.local/state)
import asyncio
import json
import os
import pathlib
import subprocess
import shutil
import sys
import tempfile
import time
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import controls, preferences, serve, wire  # noqa: E402
from test_l1 import Phone, _owner_host as _host, _ready, _state  # noqa: E402

KEY = "ui.forward_destination"


def sign(ph, target, *, key=None, r="0123456789abcdef"):
    n, ts = os.urandom(16).hex(), int(time.time() * 1000)
    msg = controls.signed_message(ph.channel, ph.did, "forward_set", n, ts, controls.object_digest("forward_set", target))
    return {"t": "forward_set", "r": r, **target, "n": n, "ts": ts, "sig": wire.b64u((key or ph.sk).sign(msg))}


class ForwardSetting(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st = _state(self.tmp.name)
        self.sent = []
        self.host = _host(self.st, self.sent)
        self.a, self.b = Phone(self.st, "A"), Phone(self.st, "B")
        self.sa, self.sb = _ready(self.host, self.a, cid=11), _ready(self.host, self.b, cid=12)
        self.sa.p33 = self.sb.p33 = True
        self.writes = []

        async def fake_set(key, value, ack=None, language_at=None):   # the file path: test_real_file_round_trip
            self.writes.append((key, value))
            sec, k = key.split(".", 1)
            self.host.preferences = preferences.validate({**self.host.preferences,
                                                          sec: {**self.host.preferences.get(sec, {}), k: value}})
            return {"ok": True}
        self.host.set_pref = fake_set

    def run_app(self, s, obj):
        async def go():
            await self.host._app(s, obj)
            await asyncio.sleep(0.02)
        asyncio.run(go())
        return [o for _, o in self.sent if o.get("t") == "ctl_res" and o.get("r") == obj.get("r")][-1]

    def prefs_to(self, s):
        return [o for x, o in self.sent if o.get("t") == "preferences" and getattr(x, "cid", x) in (s, getattr(s, "cid", s))]

    def test_schema_default_and_capability(self):
        m = preferences.SCHEMA[KEY]
        self.assertEqual((m["type"], m["default"], m["tier"], m["enum"]), ("string", "", "user", ["", "share", "wx", "tg"]))
        self.assertEqual(preferences.get(self.host.preferences, KEY, ""), "", "a config without the key reads as not chosen")
        self.assertNotIn(KEY, serve.PREF_SET_KEYS, "never an unsigned pref_set")
        self.assertIs(self.host.host_info()["forward_set"], True)
        self.assertEqual(self.host.preferences_msg()["value"]["ui"]["forward_destination"], "")
        self.assertEqual(controls.object_text("forward_set", {"dest": "tg", "migrate": False}), "tg")
        self.assertEqual(controls.object_text("forward_set", {"dest": "wx", "migrate": True}), "wx\nmigrate")

    def test_one_remote_changes_it_every_remote_gets_it(self):
        res = self.run_app(self.sa, sign(self.a, {"dest": "tg", "migrate": False}))
        self.assertEqual((res["ok"], res["dest"], res["migrated"]), (True, "tg", False))
        self.assertEqual(preferences.get(self.host.preferences, KEY), "tg")
        got = [o["value"]["ui"]["forward_destination"] for _, o in self.sent if o.get("t") == "preferences"]
        self.assertGreaterEqual(len(got), 2, "both ready remotes get the broadcast")
        self.assertTrue(all(v == "tg" for v in got))
        # B changes it back; A sees B's choice
        self.sent.clear()
        self.assertTrue(self.run_app(self.sb, sign(self.b, {"dest": "share", "migrate": False}, r="1123456789abcdef"))["ok"])
        self.assertEqual([o["value"]["ui"]["forward_destination"] for _, o in self.sent if o.get("t") == "preferences"][-1], "share")
        log = [json.loads(x) for x in self.st.controls_path.read_text().splitlines()]
        self.assertEqual([(x["action"], x["result"]) for x in log[-2:]], [("forward_set", "ok")] * 2)

    def test_migration_first_remote_wins(self):
        res = self.run_app(self.sa, sign(self.a, {"dest": "wx", "migrate": True}))
        self.assertEqual((res["ok"], res["dest"], res["migrated"]), (True, "wx", True))
        res = self.run_app(self.sb, sign(self.b, {"dest": "tg", "migrate": True}, r="2123456789abcdef"))
        self.assertEqual((res["ok"], res["dest"], res["migrated"]), (True, "wx", False), "the host's value stands")
        self.assertEqual(self.writes, [(KEY, "wx")])
        # an explicit change after migration is an ordinary write
        self.assertTrue(self.run_app(self.sb, sign(self.b, {"dest": "tg", "migrate": False}, r="3123456789abcdef"))["ok"])
        self.assertEqual(preferences.get(self.host.preferences, KEY), "tg")

    def test_two_migrations_at_once_first_wins(self):
        real = self.host.set_pref

        async def slow(key, value, ack=None, language_at=None):   # the file lock wait: the second request arrives meanwhile
            await asyncio.sleep(0.05)
            return await real(key, value)
        self.host.set_pref = slow

        async def go():
            await asyncio.gather(self.host._app(self.sa, sign(self.a, {"dest": "wx", "migrate": True}, r="c123456789abcdef")),
                                 self.host._app(self.sb, sign(self.b, {"dest": "tg", "migrate": True}, r="d123456789abcdef")))
            await asyncio.sleep(0.02)
        asyncio.run(go())
        res = {o["r"]: o for _, o in self.sent if o.get("t") == "ctl_res"}
        self.assertEqual(self.writes, [(KEY, "wx")], "one write")
        self.assertEqual((res["d123456789abcdef"]["dest"], res["d123456789abcdef"]["migrated"]), ("wx", False))
        self.assertEqual(preferences.get(self.host.preferences, KEY), "wx")

    def test_refusals(self):
        other = Phone(self.st, "x")
        bad = [
            ({"t": "forward_set", "r": "4123456789abcdef", "dest": "tg", "migrate": False}, "shape"),   # unsigned
            (sign(self.a, {"dest": "tg", "migrate": False}, key=other.sk, r="5123456789abcdef"), "bad_signature"),
            ({**sign(self.a, {"dest": "wx", "migrate": False}, r="6123456789abcdef"), "dest": "tg"}, "bad_signature"),   # re-pointed
            ({**sign(self.a, {"dest": "tg", "migrate": False}, r="7123456789abcdef"), "migrate": True}, "bad_signature"),
            (sign(self.a, {"dest": "mail", "migrate": False}, r="8123456789abcdef"), "shape"),
            ({**sign(self.a, {"dest": "tg", "migrate": False}, r="9123456789abcdef"), "migrate": "yes"}, "shape"),
        ]
        for obj, why in bad:
            res = self.run_app(self.sa, obj)
            self.assertEqual((res["ok"], res["why"]), (False, why), obj.get("r"))
        self.assertEqual(self.writes, [])
        self.sa.p33 = False
        n = len(self.sent)
        asyncio.run(self.host._app(self.sa, sign(self.a, {"dest": "tg", "migrate": False}, r="a123456789abcdef")))
        self.assertEqual(len([o for _, o in self.sent[n:] if o.get("t") == "ctl_res"]), 0, "p33 sessions only")
        asyncio.run(self.host.on_pref_set(self.sb, {"r": "b123456789abcdef", "key": KEY, "value": "tg"}))
        res = [o for _, o in self.sent if o["t"] == "pref_res"][-1]
        self.assertEqual((res["ok"], res["problem"]), (False, "not_allowed"))
        self.assertEqual(preferences.get(self.host.preferences, KEY, ""), "")

    def test_real_file_round_trip(self):
        """The key lands in config.json5 as a sparse override; a config written before P554 still validates."""
        async def real(v):
            return await serve.Host.set_pref(self.host, KEY, v)
        old = preferences.parse(preferences.path().read_text()) if preferences.path().exists() else {}
        self.assertIsNone(preferences.get(old, KEY), "the shared test config starts without the key")
        try:
            self.assertTrue(asyncio.run(real("wx"))["ok"])
            raw = preferences.path().read_text()
            self.assertEqual(preferences.get(preferences.parse(raw), KEY), "wx")
            self.assertEqual(preferences.get(self.host.preferences, KEY), "wx")
            with self.assertRaises(preferences.ConfigError):
                preferences.validate({"ui": {"forward_destination": "mail"}})
            self.assertEqual(preferences.get(preferences.validate({"ui": {}}), KEY, ""), "")
        finally:
            asyncio.run(real(""))

    def test_js_signs_the_same_bytes(self):
        if not shutil.which("node"):
            self.skipTest("node")
        for o in ({"dest": "tg", "migrate": False}, {"dest": "wx", "migrate": True}):
            code = (f"import {{ controlMessage }} from {json.dumps((HERE.parents[1] / 'protocol' / 'wire.js').as_uri())};\n"
                    f"console.log(Buffer.from(await controlMessage('CH', 'DEV', 'forward_set', '{'c' * 32}', 1790000000123, "
                    f"{json.dumps(o)})).toString('hex'));")
            r = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            want = controls.signed_message("CH", "DEV", "forward_set", "c" * 32, 1790000000123,
                                           controls.object_digest("forward_set", o))
            self.assertEqual(r.stdout.strip(), want.hex())


if __name__ == "__main__":
    unittest.main()
