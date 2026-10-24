"""P308: a workflow CEO's staff — the scanner counts only real employees (front-matter / TOML `name`), shows their own
title, flags 「只有 CEO」; the wizard writes staff files (+ the Codex twin) and doctor warns; dispatch mentions it once."""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import asyncio
import io
import json
import os
import pathlib
import shutil
import sys
import tempfile
import tomllib
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import capability  # noqa: E402
from agentj import workflows as wf  # noqa: E402
from agentj import wizard  # noqa: E402
from agentj.state import State  # noqa: E402

T0 = 1791900000


def _w(p: pathlib.Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def company(root: pathlib.Path, name: str) -> pathlib.Path:
    d = root / name
    _w(d / "documentation/CONSTITUTION.md", f"# CONSTITUTION — {name}\n")
    _w(d / "documentation/ROLES.md", "# ROLES\n")
    _w(d / "documentation/IDENTITY.md", f"# IDENTITY\n\n- **Name**：{name}-ceo。\n")
    _w(d / "CLAUDE.md", "# entry\n")
    return d


STAFF_MD = "---\nname: {n}\ndescription: {d}\n{extra}---\n\n你是{n}。\n"


class Scanner(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="p308-")
        self.addCleanup(t.cleanup)
        self.base = pathlib.Path(t.name)
        self.root = self.base / "root"
        self.root.mkdir()
        _w(self.root / "documentation/ROLES.md", "# ROLES\n\n| 工作流 | CEO 入口 |\n|---|---|\n| team | team/CLAUDE.md |\n"
                                                  "| solo | solo/CLAUDE.md |\n| codexy | codexy/CLAUDE.md |\n")
        d = company(self.root, "team")
        ag = d / ".claude/agents"
        _w(ag / "README.md", "# 员工说明\n\n这里放员工文件。\n")                               # no front matter: not staff
        _w(ag / "notes.md", "---\ndescription: 没有 name 的笔记\n---\nx\n")                    # no name: not staff
        _w(ag / "web-engineer.md", STAFF_MD.format(n="web-engineer", d="前端/UI 工程师，负责手机网页。",
                                                   extra="title: 前端/UI 工程师\n"))
        _w(ag / "qa-tester.md", STAFF_MD.format(n="qa-tester", d="QA tester. Second sentence.", extra=""))
        _w(d / ".codex/agents/web-engineer.toml", 'name = "web-engineer"\ndescription = "dup"\n')  # twin: counted once
        _w(d / ".codex/agents/critic.toml", 'name = "critic"\ndescription = "Reads cold."\nname_zh = "审稿人"\n')
        _w(d / ".codex/agents/broken.toml", 'description = "no name"\n')                    # no name: not staff
        company(self.root, "solo")                                                           # only a CEO
        s = company(self.root, "readme-only")
        _w(s / ".claude/agents/README.md", "# README\n")                                     # only a README: still no staff
        (self.base / "state").mkdir()
        self.sc = wf.Scanner(State(self.base / "state"))

    def cards(self):
        pic = self.sc.scan(self.root, "claude", "zh", now=T0)
        return pic, {w["folder"]: w for w in pic["items"]}

    def test_only_named_files_are_staff(self):
        _, c = self.cards()
        names = [a["name"] for a in c["team"]["agents"]]
        self.assertEqual(sorted(names), ["critic", "qa-tester", "web-engineer"])
        self.assertNotIn("README", names)
        self.assertEqual(c["team"]["staff"], 3)
        self.assertEqual(c["solo"]["staff"], 0)
        self.assertEqual(c["readme-only"]["staff"], 0)

    def test_title_shown_and_not_repeated_in_desc(self):
        _, c = self.cards()
        a = {x["name"]: x for x in c["team"]["agents"]}
        self.assertEqual(a["web-engineer"]["title"], "前端/UI 工程师")
        self.assertEqual(a["web-engineer"]["desc"], "负责手机网页。")
        self.assertEqual(a["critic"]["title"], "审稿人", "Codex name_zh")
        self.assertNotIn("title", a["qa-tester"], "no title → the page shows the slug")
        self.assertEqual(a["qa-tester"]["desc"], "QA tester.")

    def test_tail_org_counts_nostaff(self):
        pic, _ = self.cards()
        self.assertEqual(pic["tail"]["org"]["nostaff"], 2)
        self.assertEqual(set(pic["tail"]["org"]), {"registry", "ready", "unreg", "nostaff"})

    def test_staff_counted_before_the_list_is_cut(self):
        ag = self.root / "team/.claude/agents"
        for i in range(wf.MAX_AGENTS + 5):
            _w(ag / f"r{i:03d}.md", STAFF_MD.format(n=f"r{i:03d}", d="x", extra=""))
        _, c = self.cards()
        self.assertEqual(c["team"]["staff"], wf.MAX_AGENTS + 5 + 3)
        self.assertLessEqual(len(c["team"]["agents"]), wf.MAX_AGENTS)

    def test_hint_once_per_company(self):
        st = State(self.base / "state")
        h = wf.staff_hint(st, self.root, "solo", T0)
        self.assertEqual(h["staff"], 0)
        self.assertIn("solo", h["staff_hint"])
        self.assertIn("复查组织结构", h["staff_hint"])
        self.assertNotIn("staff_hint", wf.staff_hint(st, self.root, "solo", T0), "mentioned once only")
        self.assertIn("staff_hint", wf.staff_hint(st, self.root, "readme-only", T0), "each company once")
        self.assertEqual(wf.staff_hint(st, self.root, "team", T0), {"folder": "team", "staff": 3})
        for bad in ("../x", "a/b", "secrets", "missing"):
            self.assertIsNone(wf.staff_hint(st, self.root, bad, T0)["staff"], bad)
        data = json.loads((st.root / "workflows.json").read_text())
        self.assertEqual(len(data["staffhint"]), 2)
        self.sc.scan(self.root, "claude", "zh", now=T0)          # the colour table keeps the hint memory
        self.assertEqual(len(json.loads((st.root / "workflows.json").read_text())["staffhint"]), 2)

    def test_cli_staff_hint(self):
        st = State(self.base / "state")
        with mock.patch("agentj.state.State", return_value=st), \
                mock.patch.object(State, "agent_config", return_value={"kind": "claude"}), \
                mock.patch("agentj.main_identity.working_root", return_value=self.root):
            buf = io.StringIO()
            with redirect_stdout(buf):
                wf.cmd_workflows(SimpleNamespace(json=True, staff_hint="solo"))
            self.assertIn("staff_hint", json.loads(buf.getvalue()))
            buf = io.StringIO()
            with redirect_stdout(buf):
                wf.cmd_workflows(SimpleNamespace(json=True, staff_hint=None))
            rows = {w["folder"]: w for w in json.loads(buf.getvalue())["workflows"]}
            self.assertEqual((rows["team"]["staff"], rows["solo"]["staff"]), (3, 0))


class Wizard(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="p308w-")
        self.addCleanup(t.cleanup)
        self.root = pathlib.Path(t.name)
        company(self.root, "shop")
        self.stage = self.root / wizard.STAGING_REL

    def stage_file(self, rel, text):
        _w(self.stage / rel, text)

    def test_apply_writes_staff_and_codex_twin(self):
        md = STAFF_MD.format(n="copywriter", d="文案员，写 Listing 草稿。", extra="title: 文案员\n").replace(
            "你是copywriter。", '你是文案员。路径 C:\\data\\x，引号 """ 也要原样。')
        self.stage_file("shop/.claude/agents/copywriter.md", md)
        res = {r["path"]: r["status"] for r in wizard.apply(self.root, self.stage)}
        self.assertEqual(res, {"shop/.claude/agents/copywriter.md": "created", "shop/.codex/agents/copywriter.toml": "created"})
        t = tomllib.loads((self.root / "shop/.codex/agents/copywriter.toml").read_text())
        self.assertEqual((t["name"], t["description"], t["title"]), ("copywriter", "文案员，写 Listing 草稿。", "文案员"))
        self.assertEqual(t["developer_instructions"], wf.front(md)[1].strip("\n") + "\n")
        self.assertEqual(wf.staff_count(str(self.root / "shop")), 1)

    def test_apply_never_overwrites_owner_edits(self):
        self.stage_file("shop/.claude/agents/copywriter.md", STAFF_MD.format(n="copywriter", d="v1", extra=""))
        wizard.apply(self.root, self.stage)
        own = self.root / "shop/.claude/agents/copywriter.md"
        own.write_text(own.read_text() + "\n主人加的一行。\n", encoding="utf-8")
        twin = self.root / "shop/.codex/agents/copywriter.toml"
        twin_before = twin.read_bytes()
        self.stage_file("shop/.claude/agents/copywriter.md", STAFF_MD.format(n="copywriter", d="v2", extra=""))
        res = {r["path"]: r["status"] for r in wizard.apply(self.root, self.stage)}
        self.assertEqual(res["shop/.claude/agents/copywriter.md"], "kept")
        self.assertIn("主人加的一行", own.read_text())
        self.assertEqual(res["shop/.codex/agents/copywriter.toml"], "updated", "the untouched twin is ours")
        self.assertNotEqual(twin.read_bytes(), twin_before)
        # an owner's own staff file that the wizard never wrote is never replaced
        mine = self.root / "shop/.claude/agents/analyst.md"
        _w(mine, STAFF_MD.format(n="analyst", d="主人自己写的", extra=""))
        self.stage_file("shop/.claude/agents/analyst.md", STAFF_MD.format(n="analyst", d="向导版", extra=""))
        res = {r["path"]: r["status"] for r in wizard.apply(self.root, self.stage)}
        self.assertEqual(res["shop/.claude/agents/analyst.md"], "kept")
        self.assertIn("主人自己写的", mine.read_text())

    def test_staged_staff_is_checked(self):
        bad = {"shop/.claude/agents/README.md": "# readme\n",                                   # not a role file name
               "shop/.claude/agents/editor.md": "no front matter\n",
               "shop/.claude/agents/cutter.md": STAFF_MD.format(n="editor", d="x", extra=""),   # name ≠ file
               "shop/.claude/agents/mixer.md": "---\nname: mixer\n---\nbody\n",                 # no description
               "shop/.codex/agents/critic.toml": 'name = "critic"\ndescription = "x"\n',       # no instructions
               "shop/.codex/agents/judge.toml": 'name = "judge\n',                               # not TOML
               "shop/.claude/skills/x.md": "x\n",
               "shop/sub/.claude/agents/a.md": STAFF_MD.format(n="a", d="x", extra="")}
        for rel, text in bad.items():
            shutil.rmtree(self.stage, ignore_errors=True)
            self.stage_file(rel, text)
            with self.assertRaises(wizard.WizardError, msg=rel):
                wizard.apply(self.root, self.stage)
        shutil.rmtree(self.stage, ignore_errors=True)
        self.stage_file(".claude/agents/editor.md", STAFF_MD.format(n="editor", d="剪辑", extra=""))   # a one-project root
        self.stage_file(".codex/agents/editor.toml", 'name = "editor"\ndescription = "剪辑"\ndeveloper_instructions = "x"\n')
        res = {r["path"]: r["status"] for r in wizard.apply(self.root, self.stage)}
        self.assertEqual(set(res), {".claude/agents/editor.md", ".codex/agents/editor.toml"}, "a staged twin is used as is")

    def test_staged_staff_secret_refused(self):
        self.stage_file("shop/.claude/agents/ops.md", STAFF_MD.format(n="ops", d="运维", extra="").replace(
            "你是ops。", "key sk-" + "live-0123456789abcdefghijklmnopqrstuv"))
        with self.assertRaises(wizard.WizardError):
            wizard.apply(self.root, self.stage)

    def test_doctor_staff_warns_never_fails(self):
        _w(self.root / "documentation/ROLES.md", "# ROLES\n")
        chk = {c["name"]: c for c in wizard.doctor(self.root)}
        self.assertEqual(chk["staff"]["status"], "warn")
        self.assertIn("shop", chk["staff"]["detail"])
        self.stage_file("shop/.claude/agents/copywriter.md", STAFF_MD.format(n="copywriter", d="文案", extra=""))
        wizard.apply(self.root, self.stage)
        chk = {c["name"]: c for c in wizard.doctor(self.root)}
        self.assertEqual(chk["staff"]["status"], "ok")
        # goal-mode workflow folders (task.json, not a WDB card) are checked too
        _w(self.root / "weekly-brief/task.json", "{}")
        chk = {c["name"]: c for c in wizard.doctor(self.root)}
        self.assertEqual(chk["staff"]["status"], "warn")
        self.assertIn("weekly-brief", chk["staff"]["detail"])

    def test_doctor_no_workflow_no_staff_line(self):
        empty = self.root / "empty"
        empty.mkdir()
        self.assertNotIn("staff", {c["name"] for c in wizard.doctor(empty)})


class Dispatch(unittest.TestCase):
    """capability dispatch carries the one-time hint for a CEO without staff."""

    def test_queued_dispatch_hint_once(self):
        t = tempfile.TemporaryDirectory(prefix="p308d-")
        self.addCleanup(t.cleanup)
        base = pathlib.Path(t.name)
        root = base / "root"
        company(root, "weekly")
        st = State(base / "state")
        st.root.mkdir(parents=True, exist_ok=True)
        sch = SimpleNamespace(current_id=None, waiting=[], request=lambda wf_, why: "run-1")
        host = SimpleNamespace(agent=SimpleNamespace(cur_send=SimpleNamespace(source={"kind": "phone", "role": "owner"})), stopped=lambda: False, scheduler=sch, st=st, activity=lambda *a, **k: None)
        mgr = capability.Capabilities.__new__(capability.Capabilities)
        mgr.host, mgr.cards, mgr.adapters = host, {}, {}
        plan = {"brief": {"full": {"confirmation": {"mode": "requested", "request_id": "r"}, "revision": 1}, "confirm": False},
                "missing": [], "preflight": {"brief_digest": "d"}}

        async def flush(*_a):
            return True
        with mock.patch.object(capability, "plan_card", return_value=plan), \
                mock.patch.object(capability, "existing_workflow", return_value=True), \
                mock.patch.object(type(mgr), "st", property(lambda s: st)), \
                mock.patch.object(type(mgr), "cfg", lambda s: {"working_root": str(root)}), \
                mock.patch.object(capability, "working_root", return_value=root), \
                mock.patch("agentj.diskq.aflush", flush):
            first = asyncio.run(mgr.dispatch("weekly"))
            second = asyncio.run(mgr.dispatch("weekly"))
        self.assertEqual(first["result"], "queued")
        self.assertIn("weekly", first["staff_hint"])
        self.assertNotIn("staff_hint", second)


if __name__ == "__main__":
    unittest.main()
