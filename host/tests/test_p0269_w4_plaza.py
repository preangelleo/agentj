"""0.26.9 lane w4-plaza (ADR-0269-w4-plaza): #465 every file a manifest names must ship (signing / publishing gate, and a
self-test naming a missing file is skipped at install, never a failure); #478/#466 the self-check PATH adds Homebrew's bin
folders read-only, a declared minimum Python picks a matching interpreter, Google Chrome counts for chromium; #479 a failing
first self-check installs as pending and the owner's ONE chat line becomes 「已装好」 when the rerun passes; #480 a successful
admin command is silent, a failed one says which thing failed. Offline, synthetic packages and state (market_fixtures.Env)."""
import _hermetic  # noqa: F401,I001
import asyncio
import os
import pathlib
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import test_market as tm  # noqa: E402
import test_p466_plaza_deps as t466  # noqa: E402
from agentj import bundle, market, plaza_deps, report_privacy  # noqa: E402

FAKE = "zz-aj0269-tool"
TMP = os.environ.get("TEST_TMP") or None


def wf_files(**extra):
    return {**tm.fx.WORKFLOW_FILES, "scaffold/bin/entry": "import sys\nprint('doctor ok')\n", **extra}


class EntryGate(tm.Base):
    """#465: build() (tools/plaza import, agentj plaza publish) refuses a manifest naming files the package lacks."""

    def test_verify_relative_to_scaffold_is_refused_with_the_hint(self):
        d = tm.fx.make_workflow(self.work / "w1", files=wf_files(), install={"post_install": [], "verify": "python3 bin/entry doctor"})
        with self.assertRaisesRegex(bundle.BundleError, r"install.verify 'bin/entry'.*scaffold/bin/entry"):
            bundle.build(d)

    def test_fixed_path_builds(self):
        d = tm.fx.make_workflow(self.work / "w2", files=wf_files(), install={"post_install": [], "verify": "python3 scaffold/bin/entry doctor"})
        _, m = bundle.build(d)
        self.assertEqual(bundle.missing_entry_files(m, [f["path"] for f in m["files"]]), [])

    def test_every_named_file_is_checked(self):
        m = {"entry": "README.md", "install": {"verify": "python3 a.py && ./b/run --x >/dev/null && bash t/x.sh"},
             "roles": [{"file": "scaffold/.claude/agents/w.md"}], "params_schema": "params.schema.json",
             "requires": {"python_requirements": "scaffold/requirements.txt"}}
        self.assertEqual(bundle.missing_entry_files(m, ["README.md"]),
                         [("install.verify", "a.py"), ("install.verify", "b/run"), ("install.verify", "t/x.sh"),
                          ("roles[].file", "scaffold/.claude/agents/w.md"), ("params_schema", "params.schema.json"),
                          ("requires.python_requirements", "scaffold/requirements.txt")])

    def test_what_it_cannot_name_is_left_to_parse_verify(self):
        for v in ("test -f SKILL.md", "python3 -m unittest discover -s tests", "python3 x.py; touch y", 'disk "$HOME"',
                  "node -e 1", "python3 -c 1"):
            self.assertEqual(bundle._verify_files(v), [], v)

    def test_official_catalog_still_builds(self):
        if not tm.fx.CATALOG.is_dir():
            self.skipTest("no catalog next to the repo")
        bad = []
        for mf in sorted(tm.fx.CATALOG.glob("*/*/manifest.json")):
            m = __import__("json").loads(mf.read_text())
            if bundle.missing_entry_files(m, [f["path"] for f in m.get("files", [])]):
                bad.append(m["name"])
        self.assertEqual(bad, [])


class InstallSkipsMissingVerifyFile(tm.Base):
    def test_published_package_with_a_missing_verify_file_installs_and_skips_the_self_test(self):
        with mock.patch.object(bundle, "check_entry_files"):      # an already-published package (built before the gate)
            self.add_skill(manifest={"install": {"skill_dir_name": "demo-skill", "post_install": [], "verify": "python3 bin/nope doctor"}})
        rc, out = self.install(accept_unverified=True)
        self.assertEqual(rc, 0, out)
        self.assertIn("指向包里没有的文件", out.split("digest:")[0])
        rc, out = self.preview_and_confirm(accept_unverified=True)
        self.assertEqual(rc, market.EXIT_OK, out)
        self.assertIn("安装继续", out)
        self.assertTrue((self.e.home / ".claude" / "skills" / "demo-skill").is_dir())


class VerifyPath(unittest.TestCase):
    def folder(self, mode=0o755):
        d = tempfile.mkdtemp(prefix="aj-w4-brew-", dir=TMP)
        self.addCleanup(shutil.rmtree, d, True)
        os.chmod(d, mode)
        return d

    def test_homebrew_bins_join_after_the_system_folders(self):
        ok, gw = self.folder(), self.folder(0o775)
        with mock.patch.object(market, "BREW_BINS", (ok, gw, "/nonexistent-aj-w4")), mock.patch.object(market, "_homes", return_value=[]):
            self.assertEqual(market.verify_path(), f"{market.VERIFY_BASE}:{ok}")

    def test_a_folder_inside_a_home_never_joins(self):
        ok = self.folder()
        with mock.patch.object(market, "BREW_BINS", (ok,)), mock.patch.object(market, "_homes", return_value=[os.path.dirname(ok)]):
            self.assertEqual(market.verify_path(), market.VERIFY_BASE)

    def test_default_path_has_no_home(self):
        self.assertTrue(market.VERIFY_PATH.startswith(market.VERIFY_BASE))
        for h in market._homes():
            self.assertNotIn(h + "/", market.VERIFY_PATH + "/")

    def test_sandbox_profiles_stay_read_only_for_homebrew(self):
        prof, _ = market.verify_sbpl_profile("/tmp/aj-w4-scratch", homes=["/Users/x"])
        self.assertIn('(deny file-write* (subpath "/"))', prof)
        self.assertIn('"/opt/homebrew"', prof)
        a = market.verify_bwrap_argv("/tmp/aj-w4-scratch", "/tmp/aj-w4-scratch/pkg", homes=[], bwrap="bwrap")
        self.assertEqual(a[1:4], ["--ro-bind", "/", "/"])


class Python(unittest.TestCase):
    def test_declared_minimum(self):
        for v, want in ((">=3.12", (3, 12)), ("3.11+", (3, 11)), ("3.10", (3, 10)), ("", None), (">=2.7", None)):
            m = {"requires": {"cli": [{"name": "python3", "version": v}]}}
            self.assertEqual(market.min_python(m), want, v)

    def test_picks_the_first_interpreter_new_enough(self):
        d = tempfile.mkdtemp(prefix="aj-w4-py-", dir=TMP)
        self.addCleanup(shutil.rmtree, d, True)
        for n, ver in (("python3", "3 9"), ("python3.12", "3 12"), ("python3.13", "3 13")):
            p = pathlib.Path(d, n)
            p.write_text(f"#!/bin/sh\necho {ver}\n")
            p.chmod(0o755)
        with mock.patch.object(market, "VERIFY_PATH", d):
            self.assertEqual(market.pick_python((3, 12), [], {"PATH": d}, d), os.path.join(d, "python3.13"))
            self.assertEqual(market.pick_python((3, 9), [], {"PATH": d}, d), os.path.join(d, "python3"))
            self.assertIsNone(market.pick_python((3, 14), [], {"PATH": d}, d))


class PythonInstall(tm.Base):
    def test_too_old_python_is_a_pending_dependency_not_a_failure(self):
        t466.need_sandbox(self)
        self.add_skill(manifest={"requires": {"harness": ["claude_code"], "os": ["linux"], "cli": [{"name": "python3", "version": ">=3.99"}],
                                              "skills": []}})
        rc, out = self.preview_and_confirm(accept_unverified=True)
        self.assertEqual(rc, market.EXIT_OK, out)
        v = market.read_installed(self.e.st)[0]["verify"]
        self.assertEqual((v["state"], v["missing"]), ("pending", ["Python ≥ 3.99"]))   # ADR-0305: named as a Python version


class Chrome(unittest.TestCase):
    def test_google_chrome_counts_for_chromium(self):
        real = shutil.which
        with mock.patch("shutil.which", side_effect=lambda c, mode=os.F_OK | os.X_OK, path=None:
                        "/x/google-chrome" if c == "google-chrome" else None if "chrom" in c else real(c, mode=mode, path=path)):
            self.assertEqual(plaza_deps.missing(["chromium"], market.VERIFY_PATH), [])
        with mock.patch("shutil.which", return_value=None), mock.patch("os.access", return_value=False):
            self.assertEqual([d["name"] for d in plaza_deps.missing(["chromium"], market.VERIFY_PATH)], ["chromium"])


class FailedFirstSelfCheck(tm.Base):
    def test_a_failing_self_test_installs_as_pending_with_a_task(self):
        t466.need_sandbox(self)
        self.add_skill(files={**tm.fx.SKILL_FILES, "scripts/demo.py": "import sys\nsys.exit(3)\n"})
        rc, out = self.preview_and_confirm(accept_unverified=True)
        self.assertEqual(rc, market.EXIT_OK, out)
        self.assertNotIn("什么都没装", out)
        v = market.read_installed(self.e.st)[0]["verify"]
        self.assertEqual((v["state"], v["missing"]), ("pending", ["? (exit 3)"]))
        text = plaza_deps.task_text("demo-skill", "1.0.0", [{"name": "? (exit 3)", "user": []}], False, market.VERIFY_PATH)
        self.assertIn("自检没通过（exit 3）", text)
        self.assertIn("你不用再说", text)


class OneLine(unittest.TestCase):
    """#479 acceptance: first self-check fails, the dependency is added, the rerun passes → the owner saw ONE line, 「已装好」."""

    def test_owner_sees_one_line_that_ends_installed(self):
        import test_plaza_host_install as hi
        from agentj import plaza_host
        t = hi.HostRequest("test_allow_installs_with_one_card_and_receipt")
        t.setUp()
        self.addCleanup(t.doCleanups)
        # A remembered owner label alone is not a live application request.
        # Enter the same authenticated paired request/frame guards as
        # serve._op(DATA) -> Host._app, keeping the frame live through install.
        # No card is expected for this already-allowed permission set.
        from agentj import application_ingress, provenance
        t466.need_sandbox(self)
        rows, updates = [], []
        t.host.agent_notice = lambda text, **kw: rows.append(text) or len(rows)
        t.host.hist_update = lambda tid, **kw: updates.append((tid, kw)) or []
        t.add_skill(manifest={"install": {"skill_dir_name": "demo-skill", "post_install": [], "verify": f"{FAKE} --x"}, "permissions": {}})
        refused = asyncio.run(plaza_host.handle(t.host, {"name": "demo-skill", "harness": "claude_code"},
                                                post=t.srv, get=t.srv.get))
        self.assertEqual((refused["rc"], refused["why"]), (market.EXIT_PERMISSION, "application_ingress_unverified"))
        self.assertFalse(t.cards(), "an unverified caller never opens an approval card")
        self.assertFalse((t.e.home / ".claude" / "skills" / "demo-skill").exists())
        async def install():
            start = len(t.cards())
            with provenance.paired_request(lambda: t.host._paired_live(t.s)):
                with application_ingress.paired(t.host, t.s, {"t": "plaza_install"}):
                    res = await asyncio.wait_for(plaza_host.handle(
                        t.host, {"name": "demo-skill", "harness": "claude_code"},
                        post=t.srv, get=t.srv.get), 10)
            self.assertTrue(res["ok"], res)
            self.assertEqual(len(t.cards()), start, "this owner install needs no approval card")
            return res

        with mock.patch.object(t.host.agent, "submit", new=lambda s: None, create=True), \
                mock.patch.object(market.plaza_perms, "allowed", return_value=(market.plaza_perms.ALL, "test")):
            res = asyncio.run(install())
        self.assertEqual(res["rc"], 0, res)
        self.assertEqual(len(rows), 1, rows)
        self.assertNotIn("没", rows[0].split("：")[0])
        with mock.patch("shutil.which", new=t466.which_with(FAKE)):
            res = asyncio.run(plaza_host.verify(t.host, {"name": "demo-skill", "harness": None}))
        self.assertEqual(res["rc"], 0, res)
        self.assertEqual(len(rows), 1, f"no second chat line: {rows}")
        self.assertEqual(updates[-1][0], 1)
        self.assertTrue(updates[-1][1]["text"].startswith("已装好 demo-skill"), updates)

    def test_group_line_marks_and_then_clears_pending(self):
        from agentj import plaza_host
        host = type("H", (), {"lang": "zh"})()
        row = {"tid": 1, "names": ["a", "b"], "bad": [], "pending": {"b"}}
        self.assertEqual(plaza_host._group_text(host, row), "Agent J 主机装好 2 个：a、b（补依赖中）")
        row["pending"].clear()
        row["bad"] = ["c（签名不对）"]
        self.assertEqual(plaza_host._group_text(host, row), "Agent J 主机装好 2 个：a、b；没装 1 个：c（签名不对）")


class AdminNotice(unittest.TestCase):
    """#480: the chat line after an admin command."""

    def test_failure_names_the_thing(self):
        n = report_privacy.sudo_notice({"result": "done", "code": 1, "summary": "permission denied"}, "zh", "放行 8080 端口")
        self.assertTrue(n.startswith("没做成（“放行 8080 端口”）"), n)     # security r1 L1: the Agent's words quoted
        self.assertIn("permission denied", n)
        self.assertIsNone(report_privacy.sudo_notice({"result": "done", "code": 0}, "zh", "x"))

    def test_success_is_silent_in_the_finish_path(self):
        src = pathlib.Path(market.__file__).with_name("elevate.py").read_text()
        self.assertIn('not (res["result"] == "done" and res.get("code") == 0):     # #480: success is silent', src)
        self.assertNotIn('("已完成", "completed") if res.get("code") == 0 else ("执行失败", "failed"),\n                      "timeout"', src)


class SecurityRound1(unittest.TestCase):
    """reports/qa/0269-w4-plaza/security.md: L1 Agent words quoted, L2 not_run never reads 「自检通过」, L3 probe deadline, I1 -ec."""

    def test_l1_agent_why_is_quoted_in_the_host_line(self):
        n = report_privacy.sudo_notice({"result": "done", "code": 1}, "zh", "已全部完成")
        self.assertTrue(n.startswith("没做成（“已全部完成”）"), n)
        e = report_privacy.sudo_notice({"result": "done", "code": 1}, "en", "all done")
        self.assertTrue(e.startswith('Not done ("all done")'), e)
        src = pathlib.Path(market.__file__).with_name("elevate.py").read_text()
        self.assertIn('f"管理员命令{zh}（“{what}”）"', src)

    def run_verify_host(self, state, row):
        from agentj import plaza_host
        rows, updates = [], []
        host = type("H", (), {"lang": "zh", "st": None})()
        host.agent_notice = lambda text, **kw: rows.append(text) or len(rows)
        host.hist_update = lambda tid, **kw: updates.append(kw["text"]) or []
        if row:
            host._plaza_rows = {"demo-skill": {"tid": 7, "names": ["demo-skill"], "bad": [], "pending": {"demo-skill"}}}
        rec = [{"name": "demo-skill", "type": "skill", "harness": "claude_code", "verify": {"state": state}}]
        with mock.patch.object(market, "run_reverify", return_value=0), mock.patch.object(market, "read_installed", return_value=rec):
            res = asyncio.run(plaza_host.verify(host, {"name": "demo-skill", "harness": None}))
        self.assertEqual(res["rc"], 0)
        return rows + updates

    def test_l2_not_run_is_not_self_check_passed(self):
        for row in (True, False):
            got = self.run_verify_host("not_run", row)
            self.assertEqual(len(got), 1, got)
            self.assertIn("自检没有运行", got[0])
            self.assertNotIn("自检通过", got[0])
        self.assertIn("自检通过", self.run_verify_host("ok", True)[0])

    def test_l3_python_probe_is_bound_by_the_deadline(self):
        import time
        d = tempfile.mkdtemp(prefix="aj-w4-py-", dir=TMP)
        self.addCleanup(shutil.rmtree, d, True)
        for n in ("python3", "python3.12", "python3.13"):
            p = pathlib.Path(d, n)
            # absolute: the probe env PATH is the stub dir; `exec` keeps the stub one process, so the probe's timeout kill leaves no
            # grandchild behind (0.26.9 parity: "fixture descendants remained" → exit 97 with every case passing)
            p.write_text(f"#!/bin/sh\nexec {shutil.which('sleep')} 5\n")
            p.chmod(0o755)
        with mock.patch.object(market, "VERIFY_PATH", d):
            t0 = time.monotonic()
            self.assertIsNone(market.pick_python((3, 12), [], {"PATH": d}, d, deadline=t0 - 1))
            self.assertLess(time.monotonic() - t0, 0.5, "a spent deadline probes nothing")
            t0 = time.monotonic()
            self.assertIsNone(market.pick_python((3, 12), [], {"PATH": d}, d, deadline=t0 + 1))
            self.assertLess(time.monotonic() - t0, 3, "every probe capped by what is left")

    def test_i1_combined_code_flags_are_not_judged(self):
        for v in ("bash -ec tests/x.sh", 'python3 -Bc "import a/b"', "sh -xc run/it"):
            self.assertEqual(bundle._verify_files(v), [], v)
        self.assertEqual(bundle._verify_files("python3 -B scripts/a.py"), ["scripts/a.py"])


if __name__ == "__main__":
    unittest.main()
