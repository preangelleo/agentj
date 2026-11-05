"""P571 (0.26.0): `agentj decide` writes the decision log in the kernel's own format through ONE
shared module (agentj/decisions.py); identity v24 names only commands that exist; the skills carry the dispatch rules."""
import _hermetic  # noqa: F401
import contextlib
import io
import json
import os
import re
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from agentj import cli, decisions, main_identity
from agentj.core import orders

HOST = Path(__file__).resolve().parents[1]
SKILLS = HOST / "agentj/skills"
BRIEF = {"goal": "fixture", "scope": {"write": ["*.txt"]}, "verify": ["true"], "timebox": 5, "report": "head"}


def run_cli(*argv, cwd=None):
    out, err = io.StringIO(), io.StringIO()
    old = os.getcwd()
    try:
        if cwd:
            os.chdir(cwd)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(decisions, "working_root", lambda: os.getcwd()):
            try:
                cli.main(["decide", *argv])
                code = 0
            except SystemExit as e:
                code = e.code or 0
    finally:
        os.chdir(old)
    return code, out.getvalue(), err.getvalue()


class SharedFormat(unittest.TestCase):
    def test_kernel_and_cli_write_the_same_fields(self):
        with tempfile.TemporaryDirectory() as td:
            o = orders.create(project=td, prompt_text="fixture", brief=BRIEF, title="fixture", st_root=Path(td) / "state")
            o.decide("build", "use JSONL", "same as the kernel", "agentj/core/orders.py:12", "ok")
            kernel = o.decisions()[0]
            mine = decisions.add("use JSONL", "same as the kernel", "agentj/core/orders.py:12", "build", "ok", root=td,
                                 environ={})
        self.assertEqual(tuple(kernel), decisions.FIELDS)
        self.assertEqual(set(mine) - {"who"}, set(decisions.FIELDS))
        self.assertEqual({k: v for k, v in mine.items() if k not in ("ts", "who")},
                         {k: v for k, v in kernel.items() if k != "ts"})

    def test_kernel_keeps_its_names_from_the_shared_module(self):
        for name in ("RETRY", "failure_kind", "root_cause_hash", "places_of", "retry_advice"):
            self.assertIs(getattr(orders, name), getattr(decisions, name), name)
        self.assertIs(orders._append, decisions.append)
        self.assertIs(orders._jsonl, decisions.read)
        self.assertEqual(decisions.RETRY["network"], "retry_same")
        self.assertEqual(decisions.retry_advice([{"verdict": "failed", "root_cause_hash": "a"},
                                                 {"verdict": "failed", "root_cause_hash": "a"}]), "escalate")

    def test_kernel_still_cuts_its_own_fields(self):
        r = decisions.record("p" * 99, "d" * 999, "w" * 999, "e" * 999, "r" * 999)
        self.assertEqual([len(r[k]) for k in ("phase", "decision", "why", "evidence", "result")], [40, 300, 500, 300, 200])


class WhereItWrites(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name) / "work"
        self.root.mkdir()

    def tearDown(self):
        self.td.cleanup()

    def test_main_conversation_goes_to_a_dated_plain_file_under_dot_agentj(self):
        code, out, _ = run_cli("先派一单再铺开", "--why", "同类活 50 件", "--evidence", "reports/one.md", "--phase", "派活",
                               cwd=self.root)
        self.assertEqual(code, 0, out)
        files = list((self.root / ".agentj/decisions").iterdir())
        self.assertEqual([f.name for f in files], [time.strftime("%Y-%m-%d") + ".jsonl"])
        self.assertRegex(files[0].name, r"^\d{4}-\d{2}-\d{2}\.jsonl$")      # no hash / session id in what the owner sees
        self.assertEqual(stat.S_IMODE(files[0].stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.root / ".agentj").stat().st_mode), 0o700)
        self.assertEqual((self.root / ".agentj/.gitignore").read_text(), "*\n")
        line = json.loads(files[0].read_text().splitlines()[0])
        self.assertEqual((line["decision"], line["why"], line["evidence"], line["phase"]),
                         ("先派一单再铺开", "同类活 50 件", "reports/one.md", "派活"))
        self.assertIn("harness", line["who"])

    def test_workflow_run_goes_to_reports(self):
        wf = self.root / "weekly-report"
        wf.mkdir()
        code, out, _ = run_cli("skip the archive", "--why", "out of scope", "--dir", str(wf), "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["path"], str(wf.resolve() / "reports/decisions.jsonl"))
        self.assertEqual(len(decisions.read(wf / "reports/decisions.jsonl")), 1)

    def test_workflow_log_is_gitignored_without_hiding_reports(self):
        wf = self.root / "wf"
        wf.mkdir()
        self.assertEqual(run_cli("a", "--why", "b", "--dir", str(wf))[0], 0)
        self.assertEqual((wf / "reports/.gitignore").read_text(), "decisions.jsonl\n")
        run_cli("c", "--why", "d", "--dir", str(wf))
        self.assertEqual((wf / "reports/.gitignore").read_text(), "decisions.jsonl\n")     # once, not once per decision
        own = self.root / "own"
        (own / "reports").mkdir(parents=True)
        (own / "reports/.gitignore").write_text("*.tmp")
        run_cli("a", "--why", "b", "--dir", str(own))
        self.assertEqual((own / "reports/.gitignore").read_text(), "*.tmp\ndecisions.jsonl\n")   # the owner's rules stay
        import shutil, subprocess
        if shutil.which("git"):
            env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
            subprocess.run(["git", "init", "-q", str(wf)], check=True, env=env)
            (wf / "reports/REPORT.md").write_text("done")
            got = subprocess.run(["git", "-C", str(wf), "status", "--porcelain", "-uall"], capture_output=True, text=True,
                                 env=env).stdout
            self.assertIn("reports/REPORT.md", got)
            self.assertNotIn("decisions.jsonl", got)

    def test_gitignore_link_is_refused(self):
        wf = self.root / "wf"
        (wf / "reports").mkdir(parents=True)
        (self.root / "target").write_text("")
        os.symlink(self.root / "target", wf / "reports/.gitignore")
        code, out, _ = run_cli("a", "--why", "b", "--dir", str(wf), "--json")
        self.assertEqual((code, json.loads(out)["why"]), (1, "unsafe_log"))
        self.assertEqual((self.root / "target").read_text(), "")
        self.assertFalse((wf / "reports/decisions.jsonl").exists())

    def test_email_and_phone_are_masked_before_writing(self):
        code, out, _ = run_cli("回信给 li.lei@example.org", "--why", "主人说打 +86 138 1234 5678 或 13912345678",
                               "--evidence", "mail from ops@corp.example.net", "--json", cwd=self.root)
        self.assertEqual(code, 0, out)
        raw = (self.root / ".agentj/decisions" / decisions.day_name()).read_text()
        for leak in ("li.lei", "ops@", "1234 5678", "13912345678"):
            self.assertNotIn(leak, raw)
        line = decisions.read(self.root / ".agentj/decisions" / decisions.day_name())[0]
        self.assertEqual(line["decision"], "回信给 ***@example.org")
        self.assertEqual(line["why"], "主人说打 ***5678 或 ***5678")
        self.assertEqual(line["evidence"], "mail from ***@corp.example.net")
        self.assertEqual(decisions.record("p", "x@y.example.com", "w")["decision"], "***@y.example.com")

    def test_list_reads_back_in_order_and_since_filters(self):
        now = time.time()
        decisions.add("first", "a", root=str(self.root), now=now - 7200, environ={})
        decisions.add("second", "b", root=str(self.root), now=now - 60, environ={})
        code, out, _ = run_cli("list", "--json", cwd=self.root)
        got = json.loads(out)
        same_day = time.strftime("%Y-%m-%d", time.localtime(now - 7200)) == time.strftime("%Y-%m-%d")
        self.assertEqual((code, got["schema"], got["count"]), (0, "agentj.decision.v1", 2 if same_day else 1))   # default: today
        self.assertEqual([d["decision"] for d in decisions.entries(root=str(self.root), since=now - 600)], ["second"])
        self.assertEqual([d["decision"] for d in decisions.entries(root=str(self.root), since=now - 3 * 86400)], ["first", "second"])
        code, out, _ = run_cli("list", cwd=self.root)
        self.assertIn("second", out)
        self.assertEqual(run_cli("list", "--since", "soon", cwd=self.root)[0], 1)

    def test_who_is_the_hosts_not_an_argument(self):
        self.assertEqual(decisions.who_am_i({"CLAUDECODE": "1"})["harness"], "claude")
        self.assertEqual(decisions.who_am_i({"CODEX_THREAD_ID": "x"})["harness"], "codex")
        self.assertEqual(decisions.who_am_i({})["harness"], "terminal")
        self.assertEqual(decisions.who_am_i({"AGENTJ_REPORT": "/w/reports/weekly/REPORT.md"})["run"], "weekly")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as e:
            cli.main(["decide", "x", "--why", "y", "--who", "owner"])
        self.assertEqual(e.exception.code, 2)


class Refusals(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def refused(self, *argv):
        code, out, _ = run_cli(*argv, "--json", cwd=self.root)
        self.assertEqual(code, 1, out)
        return json.loads(out)["why"]

    def test_secret_like_text_is_refused_and_nothing_is_written(self):
        fake = "Authorization: Bearer " + "Q7" * 14           # built at run time: not a key, only key-shaped
        self.assertEqual(self.refused("use the token", "--why", "works", "--evidence", fake), "looks_like_a_secret")
        self.assertFalse((self.root / ".agentj/decisions").exists() and any((self.root / ".agentj/decisions").iterdir()))

    def test_missing_why_empty_long_and_control_characters(self):
        self.assertEqual(self.refused("x"), "why_empty")
        self.assertEqual(self.refused("--why", "y"), "decision_empty")
        self.assertEqual(self.refused("d" * 301, "--why", "y"), "decision_too_long")
        self.assertEqual(self.refused("a\nb", "--why", "y"), "control_characters")
        self.assertEqual(self.refused("决" * 290, "--why", "理" * 200), "line_too_long")    # 1 KB per line, bytes
        self.assertEqual(self.refused("x", "--why", "y", "--since", "12h"), "bad_since")

    def test_links_are_never_followed(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (self.root / ".agentj").mkdir(mode=0o700)
        os.symlink(elsewhere, self.root / ".agentj/decisions")
        self.assertEqual(self.refused("x", "--why", "y"), "unsafe_log")
        wf = self.root / "wf"
        wf.mkdir()
        os.symlink(elsewhere, wf / "reports")
        self.assertEqual(self.refused("x", "--why", "y", "--dir", str(wf)), "unsafe_log")
        (wf / "reports").unlink()
        (wf / "reports").mkdir()
        os.symlink(elsewhere / "log", wf / "reports/decisions.jsonl")
        self.assertEqual(self.refused("x", "--why", "y", "--dir", str(wf)), "unsafe_log")
        self.assertFalse((elsewhere / "log").exists())
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_evidence_pointers_are_not_secrets_and_since_is_bounded(self):
        for ev in ("~/work/wf/reports/run.log:12", os.path.expanduser("~/work/wf/reports/run.log"), "c5afbcb96",
                   "a" * 40, "10.0.0.8:8787", "agentj/serve.py:5095"):
            decisions.add("x", "y", ev, root=str(self.root), environ={})
        t = time.time()
        self.assertEqual(len(decisions.entries(root=str(self.root), since=decisions.parse_since("99999999d"))), 6)
        self.assertLess(time.time() - t, 2)

    def test_parallel_writers_never_mix_lines(self):
        def w(i):
            for j in range(20):
                decisions.add(f"writer {i} step {j}", "parallel", root=str(self.root), environ={})
        ts = [threading.Thread(target=w, args=(i,)) for i in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        rows = decisions.entries(root=str(self.root), since=time.time() - 600)
        self.assertEqual(len(rows), 80)


class RootParser(unittest.TestCase):
    def test_one_decide_subcommand_in_the_real_tree(self):
        names = []
        for f in (HOST / "agentj").glob("*.py"):
            for m in re.finditer(r'sub\.add_parser\(\s*"([\w-]+)"', f.read_text(encoding="utf-8")):
                names.append(m.group(1))
        self.assertEqual(names.count("decide"), 1)
        with contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit):
            cli.main(["decide", "--help"])
        self.assertIn("--why", out.getvalue())


class IdentityV24(unittest.TestCase):
    NEW = {"zh": ("派单写清完成证据", "同类活先跑通一单再铺开", "范围变了另派新单", "后台活超时算卡住", "按动静判卡、按原因重试",
                  "同因两次重新规划再报", "关键决定边做边记（`agentj decide`）", "能亲跑验收就亲跑", "路由靠证据，对不上说不明",
                  "拍板附推荐、依据、风险", "停在安全点、存档、写续跑说明"),
           "en": ("Every dispatch names its completion evidence", "finish one first, then fan out", "a changed scope gets a new dispatch",
                  "Background work past its time is stuck", "judge by activity", "retry by cause", "replan and report",
                  "Log key decisions as you go (`agentj decide`)", "rerun acceptance when you can", "Route by evidence",
                  "recommendation, basis and risk", "stop at a safe point, save, write how to resume")}

    def core(self, lang):
        return (main_identity.DATA / f"core.{lang}.md").read_text(encoding="utf-8")

    def test_v24_manifest_and_new_rules_in_both_languages(self):
        self.assertEqual((main_identity.VERSION, main_identity.verify_core()["version"]), (24, 24))
        for lang, needles in self.NEW.items():
            text = self.core(lang)
            self.assertTrue(text.splitlines()[0].endswith("v24"))
            for n in needles:
                self.assertIn(n, text, (lang, n))

    def test_every_agentj_command_the_core_names_exists(self):
        tree = set()
        for f in (HOST / "agentj").rglob("*.py"):
            tree.update(re.findall(r'add_parser\(\s*"([\w-]+)"', f.read_text(encoding="utf-8", errors="replace")))
        for lang in ("zh", "en"):
            for cmd in re.findall(r"`agentj ([a-z][\w-]*)", self.core(lang)):
                self.assertIn(cmd, tree, (lang, cmd))

    def test_moved_detail_lives_in_the_skills(self):
        cap = (SKILLS / "agentj-capability/SKILL.md").read_text(encoding="utf-8")
        father = (SKILLS / "feedback-to-agentj-father/SKILL.md").read_text(encoding="utf-8")
        for n in ("目标暂时缺能力", "您给我", "auth_required", "agentj browser login"):
            self.assertTrue(n in cap or n in father, n)
        for n in ("agentj update check", "agentj support ask", "--attach-doctor"):
            self.assertIn(n, father)


class Skills(unittest.TestCase):
    def test_capability_dispatch_rules(self):
        t = (SKILLS / "agentj-capability/SKILL.md").read_text(encoding="utf-8")
        s = t[t.index("## Dispatching well / 派活规矩"):]
        s = s[:s.index("A recurring schedule defaults")]
        for n in ("Completion evidence", "evidence_expected", "One first, then fan out", "refill each free slot",
                  "New scope = new dispatch", "Background work always through `agentj busy run`", "shows no robot",
                  "agentj decide \"<decision>\" --why", "agentj decide --dir <workflow>", "agentj decide list",
                  "one line per stage, not per command", "Retry by cause", "the same cause twice", "never kill by PID alone",
                  "rerun the acceptance check", "Route by evidence", "recommendation, its basis and its risk",
                  "unanswered is not consent", "Owner says stop for now", "never pushed", "全部停下"):
            self.assertIn(n, s, n)
        self.assertIn("appends one decision line to `reports/decisions.jsonl` with the edit tool", t)

    def test_goal_handoff_heartbeat_read_or_write_the_log(self):
        for skill, needles in (("agentj-goal", ("agentj decide \"<decision>\"", "agentj decide list --since 12h", "agentj busy run")),
                               ("agentj-handoff", ("agentj decide list --json", "do not copy", "stop for now", "全部停下")),
                               ("agentj-heartbeat", ("agentj decide list --since 12h", "--dir <workflow>"))):
            t = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
            for n in needles:
                self.assertIn(n, t, (skill, n))

    def test_attribution_in_notice_and_upstream(self):
        notice = (HOST / "NOTICE").read_text(encoding="utf-8")
        self.assertIn("pstack (https://github.com/cursor/plugins/tree/main/pstack), MIT, (c) 2026 Lauren Tan", notice)
        self.assertIn("../decisions.py", (HOST / "agentj/core/UPSTREAM.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
