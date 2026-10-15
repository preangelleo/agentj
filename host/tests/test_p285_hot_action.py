"""P285: the reply card's bottom-left hot key is `ui.card_hot_action` — one user-tier setting the phone writes through
`pref_set` (the same schema check as every phone setting) and the Agent / owner writes with `agentj config set`.
- default web-page-report (the share icon keeps doing what P216 did); a command or skill name, a leading / allowed;
- anything that is not a plain name (spaces, shell, control characters, too long) is refused by both paths, nothing written;
- the phone's broadcast lists it as settable, and the effective preferences carry it (the phone repaints the button from it).
"""
import _hermetic  # noqa: F401,I001
import json
import os
import subprocess
import sys
import unittest

from test_p44 import HOST, _AsyncEnv, _Env  # noqa: E402
from agentj import preferences, serve  # noqa: E402

KEY = "ui.card_hot_action"
GOOD = ("status", "/compact", "clear", "web-page-report", "agentj-recall", "plugin:skill.v2", "a" * 63)
BAD = ("rm -rf ~", "", "/", "-x", "status\n", "状态", "a" * 65, 5, None, True, "/status; ls")


class Schema(unittest.TestCase):
    def test_default_and_pattern(self):
        self.assertEqual(preferences.get(preferences.defaults(), KEY), "web-page-report")
        self.assertEqual(preferences.SCHEMA[KEY]["tier"], "user")
        for v in GOOD:
            self.assertEqual(preferences.get(preferences.validate({"ui": {"card_hot_action": v}}), KEY), v, v)
        for v in BAD:
            with self.assertRaises(preferences.ConfigError, msg=repr(v)):
                preferences.validate({"ui": {"card_hot_action": v}})


class PhoneSet(_AsyncEnv):
    async def pref(self, value):
        self.sent.clear()
        await self.host._app(self.s, {"t": "pref_set", "r": "r1", "key": KEY, "value": value})
        return [o for _, o in self.sent if o.get("t") == "pref_res"]

    async def test_whitelisted_checked_and_broadcast(self):
        self.assertIn(KEY, serve.PREF_SET_KEYS)
        res = await self.pref("status")
        self.assertEqual((res[0]["ok"], res[0].get("problem")), (True, None))
        self.assertEqual(preferences.get(preferences.effective(self.st), KEY), "status")
        b = [o for _, o in self.sent if o.get("t") == "preferences"][-1]
        self.assertEqual(b["value"]["ui"]["card_hot_action"], "status")
        self.assertIn(KEY, b["host"]["settable"])
        for bad in BAD:
            res = await self.pref(bad)
            self.assertEqual(res[0]["problem"], "bad_value", repr(bad))
        self.assertEqual(preferences.get(preferences.effective(self.st), KEY), "status", "a refused value wrote nothing")
        res = await self.pref("web-page-report")   # 「恢复默认」 is the default value written back
        self.assertTrue(res[0]["ok"])


class AgentCli(_Env):
    def _cli(self, *args):
        return subprocess.run([sys.executable, "-m", "agentj.cli", "config", *args, "--json"], cwd=HOST,
                              env={**os.environ, **self.env}, capture_output=True, text=True, timeout=60)

    def test_config_set_get_unset(self):
        r = self._cli("set", KEY, "compact")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads(self._cli("get", KEY).stdout)["value"], "compact")
        r = self._cli("set", KEY, "rm -rf ~")
        self.assertNotEqual(r.returncode, 0, "the validator refuses a non-name")
        self.assertEqual(json.loads(self._cli("get", KEY).stdout)["value"], "compact")
        self.assertEqual(self._cli("unset", KEY).returncode, 0)
        self.assertEqual(json.loads(self._cli("get", KEY).stdout)["value"], "web-page-report")
        found = json.loads(self._cli("keys", "快捷技能").stdout)["keys"]
        self.assertIn(KEY, found, "the Chinese alias finds it")


if __name__ == "__main__":
    unittest.main()
