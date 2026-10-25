"""P373 (Leo, 10-17): `agentj report` — the template publishing protocol for automations.

The engine is the plaza package web-page-report (≥ 1.3); the host only finds it and runs it, so these tests use a stub
engine that records its argv and answers like the real one (exit 2 + field paths for bad data):
- validate / render / templates / new-template map to the engine's CLI (positional or --data; no --template = engine default);
- send: render --email first; invalid data → exit 2 and mail is never called; valid → mail.cmd(action=send, format=html,
  body_file = the rendered mail, subject = the data's title unless given); --dry-run renders only;
- no package → exit 3 with the install hint; a pre-1.3 package (no wr_email.py) → exit 3; a symlinked engine is ignored;
- `report` is registered once: alone it stays the dashboard status report; with an action it skips the state migration.
"""
import _hermetic  # noqa: F401,I001
import argparse
import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import mail as _mail, market as _market, report as R  # noqa: E402,F401
from _testtmp import TEST_TMP  # noqa: E402

STUB = textwrap.dedent('''
    import json, os, pathlib, sys
    log = pathlib.Path(os.environ["P373_LOG"])
    log.write_text(log.read_text() + json.dumps(sys.argv[1:]) + "\\n" if log.exists() else json.dumps(sys.argv[1:]) + "\\n")
    a = sys.argv[1:]
    if a and a[0] in ("validate", "render"):
        data = json.loads(pathlib.Path(a[1]).read_text())
        if not data.get("title"):
            print("错误：数据不符合模板 schema：\\n  $.title: 不能为空", file=sys.stderr)
            sys.exit(2)
        if a[0] == "render":
            out = pathlib.Path(a[a.index("--out") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("<!doctype html><table><tr><td>" + data["title"] + "</td></tr></table>")
    print("ok")
''')


class _Engine:
    def __init__(self, root: pathlib.Path, new=True):
        self.dir = root / "web-page-report"
        (self.dir / "scripts").mkdir(parents=True)
        (self.dir / "scripts" / "web_report.py").write_text(STUB)
        if new:
            (self.dir / "scripts" / "wr_email.py").write_text("")
        (self.dir / "templates").mkdir()
        (self.dir / "templates" / "registry.json").write_text(json.dumps({"templates": [
            {"id": tid, "ref": tid, "slug": tid.lower()} for tid in ("R02", "R15")]}))
        self.log = root / "argv.log"

    def calls(self):
        return [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []


def _parse(argv):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd")
    R.add_parser(sub)
    return p.parse_args(argv)


class ReportCLI(unittest.TestCase):
    def setUp(self):
        authorization = mock.patch('agentj.action_policy.confirm_cli', return_value=True)
        authorization.start(); self.addCleanup(authorization.stop)
        self.tmp = pathlib.Path(tempfile.mkdtemp(dir=TEST_TMP))
        self.eng = _Engine(self.tmp)
        self.env = mock.patch.dict(os.environ, {"AGENTJ_REPORT_ENGINE": str(self.eng.dir), "P373_LOG": str(self.eng.log)})
        self.env.start()
        self.good = self.tmp / "good.json"
        self.good.write_text(json.dumps({"title": "每日简报", "sections": [{"heading": "x"}]}, ensure_ascii=False))
        self.bad = self.tmp / "bad.json"
        self.bad.write_text(json.dumps({"title": ""}))

    def tearDown(self):
        self.env.stop()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = R.cmd(_parse(["report", *argv]))
        return code, out.getvalue(), err.getvalue()

    def test_argument_mapping(self):
        self.assertEqual(self.run_cli("validate", str(self.good))[0], 0)
        self.assertEqual(self.run_cli("validate", "--data", str(self.good), "-t", "R02", "--json")[0], 0)
        self.assertEqual(self.run_cli("render", str(self.good), "--email", "-o", str(self.tmp / "m.html"))[0], 0)
        self.assertEqual(self.run_cli("templates", "--json")[0], 0)
        self.assertEqual(self.run_cli("schema", "R15")[0], 0)
        self.assertEqual(self.run_cli("new-template", str(self.tmp / "mine"), "--id", "my-daily")[0], 0)
        c = self.eng.calls()
        for row in c:
            for i, value in enumerate(row):
                if isinstance(value, str) and '/agentj-report-exec-' in value and '/input-' in value:
                    row[i] = str(self.tmp / pathlib.Path(value).name)
        self.assertEqual(c[0], ["validate", str(self.good)])                       # no --template → the engine's default
        self.assertEqual(c[1], ["validate", str(self.good), "--template", "R02", "--json"])
        self.assertEqual(c[2], ["render", str(self.good), "--out", str(self.tmp / "m.html"), "--email"])
        self.assertEqual(c[3], ["templates", "--json"])
        self.assertEqual(c[4], ["schema", "R15"])
        self.assertEqual(c[5], ["new-template", str(self.tmp / "mine"), "--id", "my-daily"])

    def test_bad_data_exit_2(self):
        code, _, _ = self.run_cli("validate", str(self.bad))
        self.assertEqual(code, 2)

    def test_send_invalid_sends_nothing(self):
        with mock.patch("agentj.mail.cmd") as mail_cmd:
            code, _, err = self.run_cli("send", str(self.bad), "--to-owner")
        self.assertEqual(code, 2)
        mail_cmd.assert_not_called()
        self.assertIn("$.title", err)
        self.assertIn("没有发送", err)

    def test_send_valid_goes_through_mail_as_html(self):
        seen = {}

        def fake(ns):
            seen.update(vars(ns), body=pathlib.Path(ns.body_file).read_text())
            return 0
        with mock.patch("agentj.mail.cmd", side_effect=fake):
            code, _, _ = self.run_cli("send", str(self.good), "--to-owner", "-t", "R15")
        self.assertEqual(code, 0)
        self.assertEqual(seen["action"], "send")
        self.assertEqual(seen["format"], "html")
        self.assertTrue(seen["to_owner"])
        self.assertEqual(seen["subject"], "每日简报")
        self.assertIn("<table>", seen["body"])
        render = self.eng.calls()[0]
        self.assertEqual((render[0], pathlib.Path(render[1]).name, render[2]), ("render", self.good.name, "--email"))
        self.assertNotEqual(render[1], str(self.good), "the engine reads a frozen input copy")
        self.assertEqual(render[-2:], ["--template", "R15"])
        self.assertFalse(pathlib.Path(seen["body_file"]).exists())                # the temp mail is cleaned up

    def test_send_subject_and_recipients(self):
        with mock.patch("agentj.mail.cmd", return_value=0) as mail_cmd:
            self.run_cli("send", str(self.good), "--to", "小王", "--cc", "a@example.com", "--subject", "周报")
        ns = mail_cmd.call_args[0][0]
        self.assertEqual((ns.to, ns.cc, ns.subject, ns.to_owner), (["小王"], ["a@example.com"], "周报", False))

    def test_send_needs_recipient_and_data(self):
        with mock.patch("agentj.mail.cmd") as mail_cmd:
            self.assertEqual(self.run_cli("send", str(self.good))[0], 2)
            self.assertEqual(self.run_cli("send", "--to-owner")[0], 2)
        mail_cmd.assert_not_called()

    def test_send_dry_run(self):
        keep = self.tmp / "keep" / "mail.html"
        with mock.patch("agentj.mail.cmd") as mail_cmd:
            code, out, _ = self.run_cli("send", str(self.good), "--to-owner", "--dry-run", "-o", str(keep))
        self.assertEqual(code, 0)
        mail_cmd.assert_not_called()
        self.assertTrue(json.loads(out)["dry_run"])
        self.assertIn("每日简报", keep.read_text())


class EngineDiscovery(unittest.TestCase):
    def setUp(self):
        authorization = mock.patch('agentj.action_policy.confirm_cli', return_value=True)
        authorization.start(); self.addCleanup(authorization.stop)
        self.tmp = pathlib.Path(tempfile.mkdtemp(dir=TEST_TMP))

    def test_missing_package(self):
        with mock.patch.dict(os.environ, {"AGENTJ_REPORT_ENGINE": str(self.tmp / "nope")}):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(R.cmd(_parse(["report", "templates"])), 3)
        self.assertIn("agentj plaza install web-page-report", err.getvalue())

    def test_old_package(self):
        eng = _Engine(self.tmp, new=False)
        with mock.patch.dict(os.environ, {"AGENTJ_REPORT_ENGINE": str(eng.dir), "P373_LOG": str(eng.log)}):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(R.cmd(_parse(["report", "templates"])), 3)
        self.assertEqual(eng.calls(), [])

    def test_symlinked_engine_ignored(self):
        real = _Engine(self.tmp)
        link = self.tmp / "linked" / "scripts"
        link.mkdir(parents=True)
        (link / "web_report.py").symlink_to(real.dir / "scripts" / "web_report.py")
        with mock.patch.dict(os.environ, {"AGENTJ_REPORT_ENGINE": str(self.tmp / "linked")}):
            self.assertIsNone(R.find_engine())

    def test_installed_record_and_skills_roots(self):
        home = self.tmp / "home"
        eng = _Engine(home / ".agents" / "skills")
        env = {k: v for k, v in os.environ.items() if k != "AGENTJ_REPORT_ENGINE"}
        env["HOME"] = str(home)
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("agentj.market.read_installed", return_value=[]):
            self.assertEqual(R.find_engine(), eng.dir)


class Registered(unittest.TestCase):
    """The real root parser (built the way site/generate-cli.py does): `report` is registered once; alone it is still the
    dashboard status report, with an action it is the protocol (and skips the state migration — it runs in the fence)."""

    def root(self):
        import ast
        from agentj import cli
        tree = ast.parse(pathlib.Path(cli.__file__).read_text(encoding="utf-8"))
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
        start = next(i for i, n in enumerate(main.body) if isinstance(n, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == "p" for t in n.targets))
        end = next(i for i, n in enumerate(main.body) if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
                   and isinstance(n.value.func, ast.Attribute) and n.value.func.attr == "parse_args")
        ns = dict(vars(cli))
        exec(compile(ast.Module(body=main.body[start:end], type_ignores=[]), str(cli.__file__), "exec"), ns)
        return ns["p"], cli

    def test_both_meanings(self):
        p, cli = self.root()                        # argparse.ArgumentError here = `report` registered twice
        bare = p.parse_args(["report"])
        self.assertIsNone(bare.action)
        with self.assertRaises(SystemExit) as cm:      # the old status report: this hermetic state is not initialised
            bare.fn(bare)
        self.assertIn("agentj init", str(cm.exception.code))
        a = p.parse_args(["report", "validate", "x.json", "-t", "R14"])
        self.assertEqual((a.action, a.data, a.template), ("validate", "x.json", "R14"))
        src = (HERE.parent / "agentj" / "cli.py").read_text(encoding="utf-8")
        self.assertIn('not (a.cmd == "report" and getattr(a, "action", None))', src)
        self.assertNotIn('"report"', src[src.index("NO_MIGRATE ="):src.index("\n", src.index("NO_MIGRATE ="))])


if __name__ == "__main__":
    unittest.main()
