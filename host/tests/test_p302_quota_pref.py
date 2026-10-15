"""P302 (owner 2026-10-15 10:20: 「额度告警时把新任务交给其他 AI 账号」 点了没反应): the phone's switch writes
quota.auto_switch through pref_set — P254 drew the switch but never whitelisted the key, so the host refused it silently."""
import _hermetic  # noqa: F401,I001
import unittest

from test_p44 import PrefSet as _P44PrefSet  # noqa: E402
from agentj import preferences, quota, serve  # noqa: E402


class P302QuotaSwitch(_P44PrefSet):
    async def test_quota_auto_switch_round_trip(self):
        self.assertIn("quota.auto_switch", serve.PREF_SET_KEYS)
        self.assertIn("quota.auto_switch", self.host.preferences_msg()["host"]["settable"], "the phone sees it settable")
        for value in (True, False, True):
            res = await self.pref("quota.auto_switch", value)
            self.assertEqual(res, [{"t": "pref_res", "r": "r1", "ok": True, "key": "quota.auto_switch"}], value)
            self.assertEqual(preferences.get(preferences.effective(self.st), "quota.auto_switch"), value)
            self.assertIs(quota.auto_switch_on(preferences.effective(self.st)), value)
        for bad in ("true", 1, None):
            res = await self.pref("quota.auto_switch", bad)
            self.assertEqual(res[0]["problem"], "bad_value", bad)


# the imported PrefSet would run twice under discovery; only the subclass belongs to this file
del _P44PrefSet

if __name__ == "__main__":
    unittest.main()
