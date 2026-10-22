"""P340 (#45): the four buttons above the phone's message box (file / image / camera / mic) can be reordered from the
phone's Settings. One user-tier setting `ui.composer_buttons`, written through `pref_set` (the same schema check as every
phone setting) and by the Agent / owner with `agentj config set`.
- default ["file","image","camera","mic"] (today's order);
- the value must be a permutation of those four ids — duplicates, missing / extra / unknown ids, non-strings, a string
  instead of a list are refused by both paths and nothing is written (the phone then keeps what it showed / the default);
- the phone's broadcast lists it as settable and carries it in the effective preferences (every remote repaints from it).
"""
import _hermetic  # noqa: F401,I001
import json
import os
import subprocess
import sys
import unittest

from test_p44 import HOST, _AsyncEnv, _Env  # noqa: E402
from agentj import preferences, serve  # noqa: E402

KEY = "ui.composer_buttons"
DEFAULT = ["file", "image", "camera", "mic"]
GOOD = (["mic", "file", "image", "camera"], ["camera", "mic", "image", "file"], DEFAULT)
BAD = (["mic", "mic", "image", "camera"], ["file", "image", "camera"], ["file", "image", "camera", "mic", "mic"],
       ["file", "image", "camera", "voice"], ["file", "image", "camera", 1], [], "mic,file,image,camera", None, 5, True,
       {"0": "mic"}, ["File", "image", "camera", "mic"])


class Schema(unittest.TestCase):
    def test_default_and_permutation(self):
        self.assertEqual(preferences.get(preferences.defaults(), KEY), DEFAULT)
        self.assertEqual(preferences.SCHEMA[KEY]["tier"], "user")
        self.assertEqual(list(preferences.COMPOSER_BUTTONS), DEFAULT)
        for v in GOOD:
            self.assertEqual(preferences.get(preferences.validate({"ui": {"composer_buttons": v}}), KEY), v, v)
        for v in BAD:
            with self.assertRaises(preferences.ConfigError, msg=repr(v)):
                preferences.validate({"ui": {"composer_buttons": v}})


class PhoneSet(_AsyncEnv):
    async def pref(self, value):
        self.sent.clear()
        await self.host._app(self.s, {"t": "pref_set", "r": "r1", "key": KEY, "value": value})
        return [o for _, o in self.sent if o.get("t") == "pref_res"]

    async def test_whitelisted_checked_and_broadcast(self):
        self.assertIn(KEY, serve.PREF_SET_KEYS)
        res = await self.pref(["mic", "file", "image", "camera"])
        self.assertEqual((res[0]["ok"], res[0].get("problem")), (True, None))
        self.assertEqual(preferences.get(preferences.effective(self.st), KEY), ["mic", "file", "image", "camera"])
        b = [o for _, o in self.sent if o.get("t") == "preferences"][-1]
        self.assertEqual(b["value"]["ui"]["composer_buttons"], ["mic", "file", "image", "camera"])
        self.assertIn(KEY, b["host"]["settable"])
        res = await self.pref(["camera", "mic", "image", "file"])   # a second change rewrites the list in place
        self.assertTrue(res[0]["ok"], res)
        self.assertEqual(preferences.get(preferences.effective(self.st), KEY), ["camera", "mic", "image", "file"])
        for bad in BAD:
            res = await self.pref(bad)
            self.assertEqual(res[0]["problem"], "bad_value", repr(bad))
        self.assertEqual(preferences.get(preferences.effective(self.st), KEY), ["camera", "mic", "image", "file"],
                         "a refused value wrote nothing")
        res = await self.pref(DEFAULT)   # 「恢复默认」 is the default order written back
        self.assertTrue(res[0]["ok"])
        self.assertEqual(preferences.get(preferences.effective(self.st), KEY), DEFAULT)

    async def test_other_lists_still_refused(self):
        """Only this key may carry a list from the phone; other array keys stay off the whitelist."""
        self.sent.clear()
        await self.host._app(self.s, {"t": "pref_set", "r": "r2", "key": "menu.items", "value": []})
        res = [o for _, o in self.sent if o.get("t") == "pref_res"]
        self.assertEqual(res[0]["problem"], "not_allowed")


class AgentCli(_Env):
    def _cli(self, *args):
        return subprocess.run([sys.executable, "-m", "agentj.cli", "config", *args, "--json"], cwd=HOST,
                              env={**os.environ, **self.env}, capture_output=True, text=True, timeout=60)

    def test_config_set_get_unset(self):
        r = self._cli("set", KEY, '["mic","file","image","camera"]')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads(self._cli("get", KEY).stdout)["value"], ["mic", "file", "image", "camera"])
        r = self._cli("set", KEY, '["mic","mic","image","camera"]')
        self.assertNotEqual(r.returncode, 0, "the validator refuses a non-permutation")
        self.assertEqual(json.loads(self._cli("get", KEY).stdout)["value"], ["mic", "file", "image", "camera"])
        self.assertEqual(self._cli("unset", KEY).returncode, 0)
        self.assertEqual(json.loads(self._cli("get", KEY).stdout)["value"], DEFAULT)
        found = json.loads(self._cli("keys", "按钮顺序").stdout)["keys"]
        self.assertIn(KEY, found, "the Chinese alias finds it")


if __name__ == "__main__":
    unittest.main()
