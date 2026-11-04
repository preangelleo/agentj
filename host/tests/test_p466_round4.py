"""#466 security round 4 (0.26.8a2): N4-5 the fix advice never links a user path / shim onto root's PATH (package manager,
else `sudo install -o root -m 0755 <real binary>`); N4-6 dependency names reach the system task / chat bare only when the
manifest declares them under `cli` — anything else is marked 「包自检报告的」/"reported by the package", charset kept.
Pure function checks with a stand-in host; no real state, no sandbox."""
import _hermetic  # noqa: F401,I001
import pathlib
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import market, plaza_deps, plaza_host  # noqa: E402


class N45Howto(unittest.TestCase):
    def test_no_symlink_into_root_path_on_any_platform(self):
        for plat in ("darwin", "linux"):
            with mock.patch.object(plaza_deps.sys, "platform", plat):
                for en in (True, False):
                    t = plaza_deps._howto(en)
                    self.assertIn("sudo install -o root -m 0755", t, (plat, en))
                    self.assertNotIn("sudo ln", t)
                    self.assertNotIn("/opt/homebrew/bin/", t)
                    self.assertIn("shim", t)
                    self.assertIn("ln -s", t, "named only as the thing not to do")
                    full = plaza_deps.task_text("demo-skill", "1.0.0", [{"name": "jq", "user": []}], en, market.VERIFY_PATH,
                                                ["jq"])
                    self.assertIn(t, full)
        with mock.patch.object(plaza_deps.sys, "platform", "linux"):
            self.assertIn("apt install", plaza_deps._howto(True))
            self.assertIn("包管理器", plaza_deps._howto(False))


class N46Names(unittest.TestCase):
    def test_label(self):
        self.assertEqual(plaza_deps.label("jq", ["jq"], True), "jq")
        self.assertEqual(plaza_deps.label("zz466node", ["jq"], True), "zz466node (reported by the package)")
        self.assertEqual(plaza_deps.label("zz466node", None, False), "zz466node（包自检报告的）")
        self.assertEqual(plaza_deps.label("rm -rf ~; x", ["rm -rf ~; x"], True), "? (reported by the package)",
                         "the charset limit holds even for a declared value")

    def test_task_text_marks_undeclared_names(self):
        deps = [{"name": "jq", "user": []}, {"name": "zzagent", "user": []}]
        for en, mark in ((True, "(reported by the package)"), (False, "（包自检报告的）")):
            t = plaza_deps.task_text("demo-skill", "1.0.0", deps, en, market.VERIFY_PATH, ["jq"])
            self.assertIn("zzagent" + (" " if en else "") + mark, t)
            self.assertNotIn("jq" + (" " if en else "") + mark, t)
        self.assertIn("jq（包自检报告的）", plaza_deps.task_text("d", "1", deps, False, market.VERIFY_PATH),
                      "no declared list (the CLI receipt): every name is marked")

    def test_deps_task_and_chat_names(self):
        rec = {"version": "1.0.0", "requires": {"cli": [{"name": "jq", "version": None, "optional": False}]},
               "verify": {"state": "pending", "missing": ["jq", "zzagent"]}}
        sent = []
        host = types.SimpleNamespace(st=types.SimpleNamespace(log=lambda *a, **k: None),
                                     hist_add=lambda *a, **k: {"id": "p1"}, agent=types.SimpleNamespace(submit=sent.append))
        with mock.patch.object(market, "pending_of", return_value=rec):
            got = plaza_host._deps_task(host, ["demo-skill"], False)
        self.assertEqual(got, {"demo-skill": ["jq", "zzagent（包自检报告的）"]})
        self.assertEqual(len(sent), 1)
        self.assertIn("- jq：", sent[0].text)
        self.assertIn("- zzagent（包自检报告的）：", sent[0].text)


if __name__ == "__main__":
    unittest.main()
