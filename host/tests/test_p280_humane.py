"""P280: the Agent's own words after a state change live in humane.json (zh/en), are short enough for one or two phone
lines, call the owner by the chosen title, rotate the /clear greeting by time of day; 「撤销清空」 ends when the new
conversation starts (the host refuses a late undo instead of half-doing it)."""
import _hermetic  # noqa: F401,I001
import asyncio
import datetime as dt
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agentj import agent as agents, auto_update, handoff, humane, menu, preferences, slash
from agentj.state import State

FIELD = re.compile(r"\{(\w+)\}")


def measured(text, lang):
    """The length a phone shows: {title} at its default, every other placeholder empty."""
    return len(FIELD.sub(lambda m: humane.DATA["default_title"][lang] if m.group(1) == "title" else "", text))


class Dictionary(unittest.TestCase):
    def test_every_line_both_languages_same_fields_and_short(self):
        for key in humane.LINES:
            zh, en = humane.variants(key, "zh"), humane.variants(key, "en")
            self.assertTrue(zh and en, key)
            fields = {frozenset(FIELD.findall(x)) for x in zh + en}
            self.assertEqual(len(fields), 1, f"{key}: zh/en and every variant take the same fields")
            for lang, items in (("zh", zh), ("en", en)):
                for x in items:
                    self.assertTrue(x.strip(), key)
                    self.assertNotIn("\n", x, f"{key}: one line")
                    self.assertLessEqual(measured(x, lang), humane.DATA["limits"][lang], f"{key} [{lang}] too long: {x}")
                    if lang == "en":
                        self.assertIsNone(re.search(r"[㐀-鿿]", x), f"{key}: no Chinese in English")

    def test_greeting_rotates_by_time_of_day(self):
        days = {h: humane.say("clear.greet", "zh", now=dt.datetime(2026, 10, 14, h, 0)) for h in (8, 14, 20, 2)}
        for h, line in days.items():
            self.assertTrue(line.startswith("董事长，"), line)
        self.assertIn("夜", days[2])
        self.assertEqual(humane.period(dt.datetime(2026, 1, 1, 4, 59)), "night")
        self.assertEqual(humane.period(dt.datetime(2026, 1, 1, 5, 0)), "morning")
        seen = {humane.say("clear.greet", "zh", now=dt.datetime(2026, 10, 14, 9, m)) for m in range(4)}
        self.assertEqual(len(seen), 2, "two morning lines take turns")
        self.assertIn("董事长，又是美好的一天，今天我们做点啥？", seen, "Leo's own words are one of them")

    def test_title_setting(self):
        self.assertEqual(humane.title(None, "zh"), "董事长")
        self.assertEqual(humane.title(None, "en"), "Chairman")
        prefs = {"owner": {"address": "  Leo　总 "}}
        self.assertEqual(humane.title(prefs, "zh"), "Leo 总")
        self.assertTrue(humane.say("clear.greet", "en", prefs).count("Leo"))
        self.assertEqual(len(humane.title({"owner": {"address": "x" * 40}}, "zh")), humane.TITLE_MAX)
        # P290: the one form-of-address setting is P205's owner.address (hot user setting, ≤ 16, default empty) — no second one
        meta = preferences.SCHEMA["owner.address"]
        self.assertEqual((meta["tier"], meta["apply"], meta["default"], meta["maxLength"]), ("user", "hot", "", 16))
        self.assertNotIn("appearance.owner_title", preferences.SCHEMA)
        self.assertEqual(preferences.get(preferences.validate({"owner": {"address": "老板"}}), "owner.address"), "老板")
        with self.assertRaises(preferences.ConfigError):
            preferences.validate({"owner": {"address": "x" * 17}})
        self.assertEqual(preferences.get(preferences.defaults(), "owner.address"), "")

    def test_never_a_lie(self):
        """Failure lines still say it failed; the recovery lines say what is still going on."""
        self.assertIn("没", humane.say("compact.handover_fail", "zh"))
        rb = auto_update.notice({"result": "rolled_back", "from": "0.18.4a1", "reason": "install_failed"})
        self.assertIn("没成功", rb); self.assertIn("回滚到 0.18.4a1", rb); self.assertIn("install_failed", rb)
        self.assertIn("failed", auto_update.notice({"result": "rolled_back", "from": "1", "reason": "r"}, "en"))
        self.assertIn("undo", humane.say("undo.late", "en").lower())
        ok = auto_update.notice({"result": "ok", "to": "0.19.0a1", "summary": "修好了 X。"}, "zh", {"owner": {"address": "老板"}})
        self.assertEqual(ok, "老板，我夜里自己升级到 0.19.0a1 了：修好了 X。")
        self.assertEqual(handoff.phone_line("en", "clear"), humane.say("recover.clear", "en"))


class DoneLines(unittest.TestCase):
    def test_clear_greets_first_compact_after_its_figures(self):
        now = dt.datetime(2026, 10, 14, 21, 43)
        kept = humane.say("clear.kept", "zh")
        out = slash.with_done("clear", kept, "zh", None, now)
        first, second = out.split("\n")
        self.assertEqual(first, humane.say("clear.greet", "zh", now=now))
        self.assertEqual(second, kept)
        self.assertEqual(slash.with_done("clear", out, "zh", None, now), out, "never twice")
        self.assertNotIn("✓ 已开新对话", out)
        comp = slash.with_done("compact", "已压缩：21k → 1k tokens", "en")
        self.assertEqual(comp, "已压缩：21k → 1k tokens\n" + humane.say("compact.done", "en"))

    def test_no_undo_promise_in_the_command_lists(self):
        for lang in ("zh", "en"):
            self.assertNotIn("可撤销", menu.DEFAULT_DESC[lang]["clear"])
            self.assertNotIn("undoable", menu.DEFAULT_DESC[lang]["clear"])


class _St:
    def __init__(self):
        self.s = {}

    def agent_session(self, k):
        return self.s.get(k)

    def set_agent_session(self, k, v):
        self.s[k] = v


class UndoWindow(unittest.TestCase):
    """The host's own /clear (independent sessions, all three harnesses share Agent.cmd_clear / cmd_undo_clear)."""

    def agent(self, lang="zh"):
        a = agents.Agent.__new__(agents.Agent)
        a.kind = "claude"
        a.host = SimpleNamespace(st=_St(), lang=lang, preferences={"owner": {"address": "Leo"}})

        async def drop():
            a.dropped = getattr(a, "dropped", 0) + 1
        a.drop_conversation = drop
        return a

    def test_undo_before_the_next_message(self):
        a = self.agent()
        a.host.st.s["claude"] = "old"
        res = asyncio.run(a.cmd_clear(""))
        self.assertTrue(res.undo and res.sep)
        self.assertEqual(res.text, humane.say("clear.kept", "zh"))
        un = asyncio.run(a.cmd_undo_clear(""))
        self.assertEqual((un.kind, un.text), ("ok", humane.say("undo.done", "zh")))
        self.assertEqual(a.host.st.s["claude"], "old")

    def test_late_undo_is_refused_and_closes_the_window(self):
        a = self.agent("en")
        a.host.st.s["claude"] = "old"
        asyncio.run(a.cmd_clear(""))
        a.host.st.s["claude"] = "new-conversation"          # the next message opened it
        late = asyncio.run(a.cmd_undo_clear(""))
        self.assertEqual((late.kind, late.text), ("error", humane.say("undo.late", "en")))
        self.assertEqual(a.host.st.s["claude"], "new-conversation", "nothing swapped back")
        self.assertIsNone(a.host.st.s["claude.prev"])
        again = asyncio.run(a.cmd_undo_clear(""))
        self.assertEqual((again.kind, again.text), ("error", humane.say("undo.none", "en")))

    def test_nothing_to_clear(self):
        a = self.agent()
        res = asyncio.run(a.cmd_clear(""))
        self.assertEqual((res.kind, res.undo, res.text), ("info", False, humane.say("clear.noop", "zh")))


if __name__ == "__main__":
    unittest.main()
