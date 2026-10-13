"""P205: `owner.address` — how Agent J addresses the owner; the phone's working line uses it.

- schema + defaults: a user-tier string, default "" (= 董事长 / Chairman on the phone), ≤ 16 characters, no control characters,
  bilingual aliases (称呼); not a security key (the main Agent sets it at the owner's word, no card).
- the existing `preferences` broadcast (§10.15) carries it to the phones with the rest of the pure-data value: the default ""
  as well as a set value, after `apply_preferences` (the path `agentj config set` and `pref_set` share).
- the agentj-config skill tells the Agent to set it when the owner says 「以后叫我 X」.
"""
import _hermetic  # noqa: F401,I001
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import ctl_auth, preferences as prefs  # noqa: E402
from agentj.serve import Host  # noqa: E402
from test_l1 import _state  # noqa: E402

AGENTJ = pathlib.Path(__file__).resolve().parents[1] / "agentj"


class Schema(unittest.TestCase):
    def test_key(self):
        m = prefs.SCHEMA["owner.address"]
        self.assertEqual((m["type"], m["default"], m["tier"], m["maxLength"]), ("string", "", "user", 16))
        self.assertEqual(prefs.defaults()["owner"], {"address": ""})
        self.assertEqual(prefs.resolve_key("称呼"), "owner.address")
        self.assertEqual(prefs.resolve_key("owner address"), "owner.address")
        self.assertFalse(any("owner.address".startswith(p) for p in ctl_auth.SECURITY_PREFIXES),
                         "the main Agent may set it at the owner's word without a card")

    def test_validate(self):
        for ok in ("老王", "王总", "Boss", "x" * 16, ""):
            prefs.validate({"owner": {"address": ok}})
        for bad in ("x" * 17, "老王\n", "a\u0007b", 3, None):
            with self.assertRaises(prefs.ConfigError, msg=repr(bad)):
                prefs.validate({"owner": {"address": bad}})

    def test_skill_mentions_it(self):
        skill = (AGENTJ / "skills/agentj-config/SKILL.md").read_text(encoding="utf-8")
        head = skill.split("---", 2)[1]
        self.assertIn("称呼", head, "the description triggers on the address")
        self.assertIn("以后叫我", skill)
        self.assertIn("agentj config set owner.address", skill)


class Broadcast(unittest.IsolatedAsyncioTestCase):
    async def test_preferences_message_carries_the_address(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": d + "/cfg"}):
            st = _state(d)
            prefs.ensure()
            host = Host(st, read_stdin=False)
            host._send_ready = mock.AsyncMock()
            self.assertEqual(host.preferences_msg()["value"]["owner"], {"address": ""}, "default reaches the phone")
            res = await host.apply_preferences(prefs.edit(prefs.read()[0], "owner.address", "老王"))
            self.assertTrue(res["ok"] and res["applied"], res)
            self.assertEqual(host.preferences_msg()["value"]["owner"]["address"], "老王")
            res = await host.apply_preferences(prefs.edit(prefs.read()[0], "owner.address", "x" * 17))
            self.assertFalse(res["ok"])
            self.assertEqual(host.preferences_msg()["value"]["owner"]["address"], "老王", "a bad value keeps the last good")


if __name__ == "__main__":
    unittest.main()
