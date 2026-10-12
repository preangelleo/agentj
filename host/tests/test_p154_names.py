"""P154 追加 6: a NEW Agent name is short — CJK (East Asian wide) counts 1.6 units, anything else 1, at most 13 units
(8 CJK characters, or 13 Latin letters / digits / hyphens / spaces). Applies to `agentj name`, the admin page rename and
`agentj login --seat … --name`; a name already stored, synced from the account or bound is never re-checked."""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import json
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
HOST = HERE.parent
sys.path.insert(0, str(HOST))
sys.path.insert(0, str(HERE))
from agentj import cloud, names, text  # noqa: E402
from agentj.state import State  # noqa: E402


class NewNameRule(unittest.TestCase):
    def test_counts(self):
        ok = ["Wren", "AgentJ-Father", "a" * 13, "名" * 8, "市场部 Agent", "内容部 Agent", "AJ 2", "助理一号"]
        bad = ["a" * 14, "AgentJ-Fathers", "名" * 9, "内容制作部 Agent", "Content Team Agent", "Marketing Agent"]
        for n in ok:
            self.assertIsNone(text.new_agent_name_problem(n), n)
        for n in bad:
            self.assertEqual(text.new_agent_name_problem(n), "name_too_long", n)
        self.assertEqual(text.new_agent_name_problem("  Wren  "), None)          # trimmed first
        self.assertEqual(text.new_agent_name_problem(""), "name_required")      # the old rules still come first
        self.assertEqual(text.new_agent_name_problem("a\nb"), "bad_name")
        self.assertAlmostEqual(text.agent_name_units("市场部 Agent"), 10.8)

    def test_starters_fit(self):
        for n in text.STARTER_NAMES:
            self.assertIsNone(text.new_agent_name_problem(n), n)
        for lang in ("en", "zh", "zh.src"):
            d = json.loads((HOST / "agentj" / "admin" / "i18n" / f"admin.{lang}.json").read_text(encoding="utf-8"))
            for i in range(1, 5):
                self.assertIsNone(text.new_agent_name_problem(d[f"name.starter_{i}"]), (lang, i))
            self.assertIn("name.err.name_too_long", d)

    def test_rename_refuses_long_but_keeps_a_stored_long_name(self):
        with tempfile.TemporaryDirectory() as d:
            st = State(pathlib.Path(d))
            st.init()
            long_old = "内容制作部的老 Agent 名字"   # stored before 0.18.5: valid under the 1–32 rule, too long now
            self.assertIsNone(text.agent_name_problem(long_old))
            st.set_agent_name(long_old)
            self.assertEqual(st.agent_name(), long_old, "an upgrade never forces a rename")
            res = names.rename(st, "名" * 9)
            self.assertEqual((res["ok"], res["error"]), (False, "name_too_long"))
            self.assertIn("8", names.RENAME_MESSAGES["name_too_long"])
            self.assertEqual(st.agent_name(), long_old)
            orig = names.announce
            names.announce = lambda st: None
            try:
                res = names.rename(st, "名" * 8)
            finally:
                names.announce = orig
            self.assertTrue(res["ok"], res)
            self.assertEqual(st.agent_name(), "名" * 8)

    def test_a_synced_long_name_is_still_accepted(self):
        # the account (Dashboard) may hold a longer name; the host adopts it as before (only NEW names are held short)
        self.assertEqual(cloud.parse_agent_name("内容制作部的老 Agent 名字"), "内容制作部的老 Agent 名字")


if __name__ == "__main__":
    unittest.main()
