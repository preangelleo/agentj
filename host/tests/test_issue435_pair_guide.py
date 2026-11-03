"""#435: the first-remote pairing hint is a one-time nudge, never a resident line and never an Agent message.

Owner 11-03 17:33 / 21:37: with three, then four remotes paired the hint 「首台遥控器配好了。建议再配主力手机…」 stayed under
the chat header and survived refreshes. Rules here (host side):
- the host suggests the missing kind only while exactly ONE remote is paired; with two or more remotes it always sends
  `kind: null`, whatever the remotes are called;
- the one remote's kind comes from the label the device gave itself, not from a name the owner may have changed;
- a phone / computer word right after Chinese text (「张伟的iPhone」) still counts;
- once two or more remotes are paired, no onboarding note appended to the owner's message mentions a missing remote.
"""
import _hermetic  # noqa: F401
import asyncio
import os
import tempfile
import unittest

from agentj import onboarding
import test_p525_pair_state as p525
from test_p72_onboarding import PC, PHONE, _seat, _state


class Hint(unittest.TestCase):
    PASS = p525.PairState.PASS
    setUp, tearDown, pair = p525.PairState.setUp, p525.PairState.tearDown, p525.PairState.pair   # not its tests

    def kinds(self, cid):
        return [m["kind"] for c, m in self.sent if c == cid and m.get("t") == "pair_guide"]

    def test_two_computers_never_get_the_hint(self):
        async def go():
            self.host.agent = None
            await self.pair(71, PC)
            self.assertEqual(self.kinds(71)[-1], "computer")       # positive control: one remote → one suggestion
            await self.pair(72, "网页 · macOS Safari")
            for cid in (71, 72):
                self.assertIsNone(self.kinds(cid)[-1], "two remotes: the hint is gone for good")
            await self.pair(73, "网页 · Windows Edge")
            self.sent.clear()
            for cid in (71, 72, 73):
                await self.host.on_ready(self.host.sessions[cid], None)    # a refresh / reconnect
                self.assertEqual(self.kinds(cid), [None])
        asyncio.run(asyncio.wait_for(go(), 6))

    def test_four_remotes_with_owner_names_never_get_the_hint(self):
        async def go():
            self.host.agent = None
            ss = [await self.pair(80 + i, n) for i, n in enumerate([PC, "网页 · macOS Chrome", PHONE, "网页 · Android Chrome"])]
            for s in ss:                                                    # the owner names them all without OS words
                self.assertTrue(self.st.rename_device(s.device, "无敌小可爱 " + str(s.cid), via="phone"))
            await self.host.remotes.push_list()
            for s in ss:
                self.assertIsNone(self.kinds(s.cid)[-1])
        asyncio.run(asyncio.wait_for(go(), 6))

    def test_last_remote_left_after_unbind_gets_the_suggestion_back(self):
        async def go():
            self.host.agent = None
            await self.pair(71, PC)
            ph = await self.pair(72, PHONE)
            self.assertIsNone(self.kinds(71)[-1])
            await self.host.revoke(ph.device)
            self.assertEqual(self.kinds(71)[-1], "computer")
        asyncio.run(asyncio.wait_for(go(), 6))

    def test_single_remote_kind_comes_from_its_own_label_not_the_owner_name(self):
        async def go():
            self.host.agent = None
            s = await self.pair(71, PHONE)
            self.assertTrue(self.st.rename_device(s.device, "Leo 的 Mac", via="phone"))   # owner name says computer
            await self.host.remotes.push_list()
            self.assertEqual(self.kinds(71)[-1], "phone", "the device's own label (iOS Safari) decides, not the rename")
        asyncio.run(asyncio.wait_for(go(), 6))


class Kinds(unittest.TestCase):
    def test_os_word_right_after_chinese_text_counts(self):
        for label, kind in [("张伟的iPhone", "phone"), ("李娜的iPad", "phone"), ("我的Android", "phone"),
                            ("张伟的Mac", "computer"), ("办公室Windows", "computer"),
                            ("Machine", "other"), ("iPhoneX12", "other")]:
            self.assertEqual(onboarding.kind_of(label), kind, label)


class Notes(unittest.TestCase):
    """The hidden note appended to the owner's next message must not keep asking for a remote once two are paired."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)
        _seat(self.st)

    def tearDown(self):
        self.tmp.cleanup()

    def test_two_paired_remotes_end_the_remote_reminder(self):
        t0 = 1_800_000_000
        onboarding.read(self.st)                                   # fresh install
        a = self.st.add_device(os.urandom(32), "我的电脑")           # names the classifier cannot read
        onboarding.paired(self.st, a, "我的电脑", now=t0)
        self.assertIn("还差", onboarding.turn_note(self.st, "zh", now=t0 + 1), "positive control: one remote → tour note")
        b = self.st.add_device(os.urandom(32), "我的平板")
        onboarding.paired(self.st, b, "我的平板", now=t0 + 2)
        for lang, words in (("zh", ("还差", "遥控器管理", "添加遥控器")), ("en", ("still missing", "Setup still needs", "Remotes →"))):
            for i in range(onboarding.TOUR_TURNS + 3):
                n = onboarding.turn_note(self.st, lang, now=t0 + 10 + i * onboarding.REMIND_SECS)
                for w in words:
                    self.assertNotIn(w, n, (lang, i, n))


if __name__ == "__main__":
    unittest.main()
