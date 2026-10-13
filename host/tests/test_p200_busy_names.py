"""P200 追加 1: a dispatch unit started by `systemd-run` without --description is named after its command line
("[systemd-run] /usr/bin/bash …/run-px.sh 165 …"), which the phone's busy card showed as the task name. Names now:
① a written Description (who · what, who: what, or any title — a leading repeat of who is cut); ② systemd-run's default
(prefixed, or the bare command line of systemd < 256): a known launcher `run-<name>.sh|py` → its PROMPT file's first
"# " heading (outside ``` fences), PROMPT-<arg>.md, PROMPT-<launcher name>.md or PROMPT-<unit number>.md next to it;
③ the unit name. The command line itself is only in the detail (`busy_get` → item.cmd), redacted.
"""
import _hermetic  # noqa: F401,I001
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from agentj import busy  # noqa: E402


class Names(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.jar = pathlib.Path(self.tmp.name) / ".jarvis"
        self.jar.mkdir()
        for n in ("run-px.sh", "run-shotfable.sh", "run-other.sh"):
            (self.jar / n).write_text("#!/bin/sh\n")
        (self.jar / "PROMPT-165.md").write_text(
            "【执行环境】headless\n\n```bash\n# not a title\n```\n# P165 — 0.20.0 集成与发布候选\n## sub\n", encoding="utf-8")
        (self.jar / "PROMPT-shotfable.md").write_text("intro\n# 分镜寓言 · 第二集\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def sr(self, rest):
        return f"[systemd-run] /usr/bin/bash {self.jar}/{rest}"

    def test_written_description(self):
        self.assertEqual(busy.unit_names("agentj-p160.service", "掌枢 · harness 对等"), ("掌枢", "harness 对等", None))
        self.assertEqual(busy.unit_names("agentj-p160.service", "掌枢: 发布"), ("掌枢", "发布", None))
        self.assertEqual(busy.unit_names("agentj-p200.service", "P200 输入框滚动条 + 在办任务名"),
                         ("P200", "输入框滚动条 + 在办任务名", None), "who is not repeated in the label")
        self.assertEqual(busy.unit_names("agentj-p20.service", "P200 别的事"), ("P20", "P200 别的事", None))

    def test_launcher_prompt_title(self):
        who, label, cmd = busy.unit_names("agentj-p165.service", self.sr("run-px.sh 165 p165-integration-020 claude"))
        self.assertEqual((who, label), ("P165", "0.20.0 集成与发布候选"), "the H1 outside the fence, P165 — cut")
        self.assertEqual(cmd, f"/usr/bin/bash {self.jar}/run-px.sh 165 p165-integration-020 claude")
        self.assertNotIn("systemd-run", label)

    def test_launcher_by_its_own_name_and_old_systemd(self):
        # run-shotfable.sh with no argument reads PROMPT-shotfable.md; systemd < 256: no "[systemd-run]" prefix
        who, label, cmd = busy.unit_names("agentj-p77.service", f"/usr/bin/bash {self.jar}/run-shotfable.sh")
        self.assertEqual((who, label), ("P77", "分镜寓言 · 第二集"))
        self.assertTrue(cmd.endswith("run-shotfable.sh"))

    def test_unit_name_fallback(self):
        for desc in (self.sr("run-other.sh 9"), "[systemd-run] /usr/bin/sleep 600", "/usr/bin/python3 -m x"):
            who, label, cmd = busy.unit_names("agentj-p9-x.service", desc)
            self.assertEqual((who, label), ("P9", "agentj-p9-x"), desc)
            self.assertTrue(cmd.startswith("/usr/bin/"))
        self.assertEqual(busy.unit_names("agentj-p9.service", ""), ("P9", "agentj-p9", None))

    def test_symlink_prompt_not_followed(self):
        (self.jar / "PROMPT-166.md").symlink_to(self.jar / "PROMPT-165.md")
        self.assertEqual(busy.unit_names("agentj-p166.service", self.sr("run-px.sh 166 wt claude"))[1], "agentj-p166")

    def test_scan_and_detail(self):
        line = f"agentj-p165.service loaded active running {self.sr('run-px.sh 165 wt claude')}\n"
        sk = "sk-ant-api03-" + "A" * 90

        def run(argv, **kw):
            if argv[2] == "list-units":
                self.assertIn("--full", argv, "a long Description is never ellipsised")
                return SimpleNamespace(stdout=line.replace("claude", "claude " + sk))
            return SimpleNamespace(stdout="Environment=\n")
        b = busy.Board(None)
        b.refresh_outside(run=run)
        items = b.items()
        self.assertEqual((items[0]["who"], items[0]["label"]), ("P165", "0.20.0 集成与发布候选"))
        self.assertNotIn("cmd", items[0], "the command line is never on the list")
        d = b.detail(items[0]["id"], run=run)
        self.assertIn("run-px.sh 165 wt claude", d["cmd"])
        self.assertNotIn(sk, d["cmd"], "redacted")


if __name__ == "__main__":
    unittest.main()
