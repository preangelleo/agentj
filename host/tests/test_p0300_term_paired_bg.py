"""0.30.0 hotfix (董事长 11-09): the web terminal failed on every phone (iPhone / Android / 华为 Pad) with 「已取消」.

Production wraps every phone frame in `provenance.paired_request(...)` (serve.on_data). 0.30.0's `elevate.request` only
accepts that frame's own asyncio task, but the terminal asks its `login` card from a background task (`_open`) spawned
by the signed + UV-checked term_open → refused (authority_changed) → the phone saw 「没能打开网页终端：已取消」 right after
Face ID / the device PIN. The other terminal tests call `desk.on_phone` with no paired context, so they never saw it.

Here every phone frame goes through the same paired wrapper as serve.on_data: enable + open (login card) must reach
term_ready, and a session that is gone by the time the card is asked must still be refused.
"""
import _hermetic  # noqa: F401,I001
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import provenance, term_store  # noqa: E402

from test_p408_terminal import GOOD  # noqa: E402
from test_p417_term_enable import IPHONE, ANDROID, Kind  # noqa: E402


class PairedFrames(Kind):
    """Phone frames exactly as serve.on_data delivers them: inside paired_request(lambda: host._paired_live(s))."""

    def setUp(self):
        super().setUp()
        desk, host = self.desk, self.host
        raw = desk.on_phone

        async def framed(s, obj):
            with provenance.paired_request(lambda: host._paired_live(s)):
                return await raw(s, obj)
        desk.on_phone = framed

    def _enable_open(self, label):
        self.label(label)
        self.passkey()
        self.assertTrue(self.host._paired_live(self.s))

        async def go():
            c1 = await self.enable_step1()
            self.assertEqual(c1["why"], "passkey_required", c1)
            out = await self.enable_step2(c1)
            self.assertEqual(out[-1]["t"], "ctl_pending", out)
            return await self.finish(c1, GOOD)
        return self.run_async(go)

    def test_iphone_enable_and_open_through_the_paired_frame(self):
        ready = self._enable_open(IPHONE)
        self.assertEqual(ready["t"], "term_ready", ready)          # 0.30.0: ctl_res why=cancelled
        self.assertTrue(term_store.read_config(self.st)["on"])

    def test_android_enable_and_open_through_the_paired_frame(self):
        ready = self._enable_open(ANDROID)
        self.assertEqual(ready["t"], "term_ready", ready)

    def test_listed_open_with_login_card_through_the_paired_frame(self):
        self.label(IPHONE)
        self.passkey()
        self.enable()

        async def go():
            return await self.open()
        ready = self.run_async(go)
        self.assertEqual(ready["t"], "term_ready", ready)

    def test_session_gone_before_the_card_is_still_refused(self):
        self.label(IPHONE)
        self.passkey()
        live = self.host._paired_live
        state = {"gone": False}
        self.host._paired_live = lambda s: (not state["gone"]) and live(s)

        async def go():
            c1 = await self.enable_step1()
            state["gone"] = True                                     # the phone's session drops after its signed step 2
            out = await self.enable_step2(c1)
            self.assertEqual(out[-1]["t"], "ctl_pending", out)
            end = await self.until(lambda: next((o for o in self.sent_after(c1) if o.get("t") in ("term_ready", "elev")
                                                 or (o.get("t") == "ctl_res" and o.get("why") not in (None, "passkey_required"))), None), 30)
            return end
        end = self.run_async(go)
        self.assertEqual(end.get("t"), "ctl_res", end)               # no login card, no terminal
        self.assertFalse(term_store.read_config(self.st)["on"])


if __name__ == "__main__":
    unittest.main()
