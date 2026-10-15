"""P276: the Workflow page's first scan fills the CEO roster (root documentation/ROLES.md) — append-only, once per folder."""
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import workflows as wf  # noqa: E402
from agentj import wizard  # noqa: E402
from agentj.state import State  # noqa: E402

T0 = 1791900000
TEMPLATE = (pathlib.Path(wizard.__file__).with_name("rootdocs") / "zh" / "ROLES.md").read_text(encoding="utf-8")


def _w(p: pathlib.Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def wdb(root: pathlib.Path, name: str, ceo: str, thin: bool = False) -> None:
    d = root / name / "documentation"
    _w(d / "CONSTITUTION.md", f"# CONSTITUTION — {name}\n")
    if thin:                                       # entry + CONSTITUTION + IDENTITY (no ROLES): the P276 rule
        _w(root / name / "CLAUDE.md", "# entry\n\n读 documentation/CONSTITUTION.md\n")
    else:
        _w(d / "ROLES.md", "# ROLES\n")
        _w(root / name / "AGENTS.md", "# entry\n")
    _w(d / "IDENTITY.md", f"# IDENTITY — {ceo}\n\n- **Name**：{ceo}。\n")


class Roster(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="p276-")
        self.addCleanup(t.cleanup)
        self.base = pathlib.Path(t.name)
        self.root = self.base / "root"
        self.root.mkdir()
        self.st = State(self.base / "state")
        self.st.root.mkdir(parents=True, exist_ok=True)
        self.roles = self.root / "documentation/ROLES.md"
        _w(self.roles, TEMPLATE)
        wdb(self.root, "video-studio", "导演阿影")              # WDB, not registered
        wdb(self.root, "music-studio", "乐师阿律", thin=True)   # WDB (thin entry rule), not registered
        wdb(self.root, "old-shop", "店长")                      # WDB, already registered by hand
        with open(self.roles, "a", encoding="utf-8") as f:
            f.write("| old-shop | old-shop/AGENTS.md | 手动 | old-shop/reports/ | 在用 |\n")
        _w(self.root / "notes/readme.md", "plain\n")                                   # not WDB
        _w(self.root / "half/documentation/CONSTITUTION.md", "# only a constitution\n")  # not WDB
        self.sc = wf.Scanner(self.st)

    def sweep(self):
        return self.sc.sweep(self.root, "claude", "zh", now=T0)

    def cards(self, pic):
        return {w["folder"]: w for w in pic["items"]}

    def test_scan_registers_missing_append_only(self):
        before = self.roles.read_text(encoding="utf-8")
        pic = self.sweep()
        after = self.roles.read_text(encoding="utf-8")
        self.assertTrue(after.startswith(before.rstrip("\n")), "nothing already in ROLES.md is changed")
        self.assertEqual(after.count("old-shop"), 3, "a registered folder is not added twice")
        self.assertIn("| video-studio | video-studio/AGENTS.md | 手动 | video-studio/reports/ | 扫描补登 |", after)
        self.assertIn("| music-studio | music-studio/CLAUDE.md |", after)
        c = self.cards(pic)
        self.assertEqual(sorted(w for w in c if c[w]["wdb"]), ["music-studio", "old-shop", "video-studio"])
        self.assertFalse(c["notes"]["wdb"])
        self.assertFalse(c["half"]["wdb"])
        self.assertTrue(all(c[w]["reg"] for w in ("music-studio", "old-shop", "video-studio")))
        self.assertNotIn("reg", c["notes"])
        self.assertEqual(pic["tail"]["org"], {"registry": True, "ready": True, "unreg": 0, "nostaff": 3})
        self.assertEqual(c["video-studio"]["ceo"], "导演阿影", "the code-name row is not mistaken for a CEO name")
        self.assertEqual(sorted(self.sc.added), ["music-studio", "video-studio"])

    def test_idempotent_and_deleted_row_stays_deleted(self):
        self.sweep()
        once = self.roles.read_bytes()
        self.sweep()
        self.assertEqual(self.roles.read_bytes(), once, "a second scan writes nothing")
        text = once.decode().replace("| video-studio | video-studio/AGENTS.md | 手动 | video-studio/reports/ | 扫描补登 |\n", "")
        self.roles.write_text(text, encoding="utf-8")
        pic = self.sweep()
        self.assertNotIn("video-studio/AGENTS.md", self.roles.read_text(encoding="utf-8"), "the owner's deletion wins")
        self.assertFalse(self.cards(pic)["video-studio"]["reg"])
        self.assertEqual(pic["tail"]["org"]["unreg"], 1)
        wdb(self.root, "new-flow", "新 CEO")                  # a NEW folder is still registered (incremental)
        self.sweep()
        self.assertIn("| new-flow | new-flow/AGENTS.md |", self.roles.read_text(encoding="utf-8"))

    def test_roster_not_last_gets_its_own_section(self):
        _w(self.roles, "# 我的角色表\n\n| CEO | 项目 |\n|---|---|\n| 店长 | old-shop |\n\n后记：表格后面还有字。\n")
        before = self.roles.read_text(encoding="utf-8")
        self.sweep()
        after = self.roles.read_text(encoding="utf-8")
        self.assertTrue(after.startswith(before.rstrip("\n")))
        self.assertIn("## 扫描补登的工作流", after)
        self.assertIn("| 工作流 | CEO 入口 | 执行方式 | 报告位置 | 状态 |", after)

    def test_other_column_count_and_structure_json(self):
        _w(self.roles, "# ROLES\n\n| CEO | 项目 | 路径 |\n|---|---|---|\n| 乐师阿律 | 律动音乐 | `music-studio` |\n")
        _w(self.root / "documentation/STRUCTURE.json", json.dumps({"workflows": [{"id": "old-shop", "dir": "old-shop"}]}))
        self.sweep()
        text = self.roles.read_text(encoding="utf-8")
        self.assertIn("| video-studio | video-studio/AGENTS.md | 扫描补登 |", text)
        self.assertNotIn("| old-shop |", text, "STRUCTURE.json workflows count as registered")
        self.assertEqual(text.count("music-studio"), 1)

    def test_no_roster_symlink_and_empty_root(self):
        self.roles.unlink()
        pic = self.sweep()
        self.assertFalse(self.roles.exists(), "the scan never creates ROLES.md")
        self.assertEqual(pic["tail"]["org"], {"registry": False, "ready": False, "unreg": 3, "nostaff": 3})
        outside = self.base / "outside.md"
        outside.write_text(TEMPLATE, encoding="utf-8")
        self.roles.symlink_to(outside)
        self.sweep()
        self.assertEqual(outside.read_text(encoding="utf-8"), TEMPLATE, "a symlinked roster is never written")
        empty = self.base / "empty"
        _w(empty / "documentation/ROLES.md", TEMPLATE)
        pic = wf.Scanner(self.st).sweep(empty, "claude", "zh", now=T0)
        self.assertEqual([w for w in pic["items"] if w["wdb"]], [])
        self.assertEqual(pic["tail"]["counts"]["wdb"], 0)
        self.assertEqual(pic["tail"]["org"], {"registry": True, "ready": False, "unreg": 0, "nostaff": 0})
        self.assertEqual((empty / "documentation/ROLES.md").read_text(encoding="utf-8"), TEMPLATE)

    def test_english_roster_stays_english(self):
        en = (pathlib.Path(wizard.__file__).with_name("rootdocs") / "en" / "ROLES.md").read_text(encoding="utf-8")
        _w(self.roles, en)
        self.sc.sweep(self.root, "claude", "zh", now=T0)          # phone in Chinese, document in English
        self.assertIn("| video-studio | video-studio/AGENTS.md | manual | video-studio/reports/ | registered by scan |",
                      self.roles.read_text(encoding="utf-8"))

    def test_refused_names_and_secret_check(self):
        res = wf.register(self.root, ["bad|name", "a/b", "x" * 200])
        self.assertEqual(res["added"], [])
        self.assertEqual(len(res["refused"]), 3)
        with mock.patch.object(wizard, "secret_hits", return_value=["x"]):
            res = wf.register(self.root, ["video-studio"])
        self.assertEqual(res["added"], [])
        self.assertNotIn("video-studio", self.roles.read_text(encoding="utf-8"))

    def test_colour_table_keeps_memory(self):
        self.sweep()
        data = json.loads((self.st.root / "workflows.json").read_text(encoding="utf-8"))
        self.assertIn("video-studio", data["folders"])
        self.assertEqual(len(data["reg"]), 2)
        wdb(self.root, "third", "三号")
        self.sweep()
        data = json.loads((self.st.root / "workflows.json").read_text(encoding="utf-8"))
        self.assertEqual(len(data["reg"]), 3, "the colour rewrite keeps the registration memory")

    def test_budget_and_peek(self):
        p = self.sweep()
        with mock.patch.object(wf, "SCAN_BUDGET", -1):
            q = wf.Scanner(self.st).scan(self.root, "claude", "zh", now=T0)
        self.assertTrue(q["tail"]["partial"])
        self.assertEqual(q["items"], [], "a fresh scanner over budget sends nothing it has not read")
        with mock.patch.object(wf, "SCAN_BUDGET", -1):
            r = self.sc.scan(self.root, "claude", "zh", now=T0)
        self.assertEqual(len(r["items"]), len(p["items"]), "over budget, cached cards are kept")
        big = self.root / "big"
        big.mkdir()
        for i in range(wf.PEEK + 5):
            (big / f".env{i}").write_text("")
        names = wf._folder_entries(str(self.root), [])
        self.assertIn("big", names, "a huge folder is only peeked at, not judged by a full listing")

    def test_cli_register_and_doctor(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = wizard.cmd_register(SimpleNamespace(dir=str(self.root), json=True))
        res = json.loads(out.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(all(r["registered"] for r in res["workflows"]))
        self.assertEqual(sorted(r["folder"] for r in res["workflows"] if r.get("added")), ["music-studio", "video-studio"])
        self.assertEqual(next(c for c in wizard.doctor(self.root) if c["name"] == "roster")["status"], "ok")
        wdb(self.root, "later", "后来")
        chk = next(c for c in wizard.doctor(self.root) if c["name"] == "roster")
        self.assertEqual(chk["status"], "warn")
        self.assertIn("later", chk["detail"])

    def test_serve_start_and_wf_list_use_the_sweep(self):
        import asyncio
        from agentj import serve
        host = SimpleNamespace(wf=self.sc, agent_cfg={"working_root": str(self.root), "kind": "claude"}, lang="zh", st=self.st)
        asyncio.run(serve.Host._wf_first_sweep(host))
        self.assertIn("| video-studio |", self.roles.read_text(encoding="utf-8"), "the start sweep registers")
        boom = SimpleNamespace(wf=SimpleNamespace(sweep=mock.Mock(side_effect=OSError)), agent_cfg={}, lang="zh", st=mock.Mock())
        asyncio.run(serve.Host._wf_first_sweep(boom))          # never raises into serve
        boom.st.log.assert_called_with("wf_sweep_failed", reason="OSError")
        src = pathlib.Path(serve.__file__).read_text(encoding="utf-8")
        self.assertIn('scan = self.wf.sweep if t == "wf_list" else self.wf.scan', src)


class WebPrompt(unittest.TestCase):
    """The button's prompt (web dictionary) names the wizard skill, the roster and the self-check commands."""

    def test_prompt_mentions_skill_and_commands(self):
        web = pathlib.Path(__file__).resolve().parents[2] / "web/i18n"
        if not web.is_dir():
            self.skipTest("no web/ in this tree")
        for name in ("web.zh.src.json", "web.en.json"):
            d = json.loads((web / name).read_text(encoding="utf-8"))
            for k in ("p276.setupPrompt", "p276.reviewPrompt"):
                for want in ("agentj-workflow-wizard", "documentation/ROLES.md", "agentj wizard register --dir . --json",
                             "agentj wizard doctor --dir . --json"):
                    self.assertIn(want, d[k], (name, k, want))


if __name__ == "__main__":
    unittest.main()
