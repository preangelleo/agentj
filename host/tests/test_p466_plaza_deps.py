"""#466 (chairman ruling 2026-11-04): a missing dependency never blocks a plaza install; only signature / digest / static
audit do. 127 / a declared cli missing on the sandbox PATH → 「已安装·依赖待补」 + a system task for the main Agent;
`agentj plaza verify <name>` reruns the self-test for the same bundle sha256 + installed files, no new signature.
Offline, synthetic packages and state only (market_fixtures.Env + _hermetic); the sandbox itself is unchanged."""
import _hermetic  # noqa: F401,I001
import asyncio
import io
import os
import shutil
import sys
import pathlib
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import test_market as tm  # noqa: E402
from agentj import bundle, market, plaza_deps  # noqa: E402
import market_fixtures as fx  # noqa: E402

FAKE = "zz-aj466-tool"
_which = shutil.which


def which_with(*names):
    """shutil.which, plus `names` resolving to /bin/true — 'the owner installed it system-wide' (no real system change)."""
    def w(cmd, mode=os.F_OK | os.X_OK, path=None):
        return _which("true", path=path) if cmd in names else _which(cmd, mode=mode, path=path)
    return w


def need_sandbox(t):
    if not market.sandbox_kind():
        t.skipTest("no sandbox on this machine")


class Pending(tm.Base):
    def target(self):
        return self.e.home / ".claude" / "skills" / "demo-skill"

    def rec(self):
        return market.read_installed(self.e.st)[0]

    def test_self_test_program_missing_installs_as_pending(self):
        need_sandbox(self)
        self.add_skill(manifest={"install": {"skill_dir_name": "demo-skill", "post_install": [], "verify": f"{FAKE} --self-test"}})
        rc, out = self.install(accept_unverified=True)
        self.assertEqual(rc, 0, out)
        self.assertIn(FAKE, out.split("digest:")[0], "the preview names the missing dependency before the owner signs")
        self.assertIn("依赖", out.split("digest:")[0])
        rc, out = self.preview_and_confirm(accept_unverified=True)
        self.assertEqual(rc, market.EXIT_OK, out)
        self.assertTrue(self.target().is_dir())
        self.assertIn(plaza_deps.PENDING[0], out)
        self.assertIn(plaza_deps.PENDING[1], out)
        self.assertIn(f"agentj plaza verify demo-skill", out)
        v = self.rec()["verify"]
        self.assertEqual((v["state"], v["missing"]), ("pending", [FAKE]))

    def test_script_exit_127_names_the_dependency(self):
        need_sandbox(self)
        files = {**tm.fx.SKILL_FILES, "scripts/demo.py":
                 "import sys\nprint(\"env: 'zz466node': No such file or directory\")\nsys.exit(127)\n"}
        self.add_skill(files=files)
        rc, out = self.preview_and_confirm(accept_unverified=True)
        self.assertEqual(rc, market.EXIT_OK, out)
        self.assertEqual(self.rec()["verify"]["missing"], ["zz466node"])

    def test_declared_cli_missing_is_pending_even_when_the_self_test_passes(self):
        need_sandbox(self)
        self.add_skill(manifest={"requires": {"harness": ["claude_code"], "os": ["linux"],
                                              "cli": [{"name": "python3"}, {"name": FAKE}], "skills": []}})
        rc, out = self.preview_and_confirm(accept_unverified=True)
        self.assertEqual(rc, market.EXIT_OK, out)
        self.assertIn("install.verify: OK", out)
        self.assertEqual(self.rec()["verify"]["state"], "pending")

    def add_legacy_host_skill(self, verify="python3 scripts/demo.py --self-test", files=None, extra_cli=()):
        # Construct pre-fix package bytes directly; the new publisher refuses to build these.
        d = fx.make_skill(self.work / f"legacy-{next(self.n)}", files=files,
                          requires={"cli": [{"name": "python3"}, {"name": "agentj"}, *extra_cli]},
                          install={"skill_dir_name": "demo-skill", "post_install": [], "verify": verify})
        m = __import__("json").loads((d / "manifest.json").read_text())
        payload = {p: (d / p).read_bytes() for p in (files or fx.SKILL_FILES)}
        m["files"] = bundle.files_list(payload)
        data = bundle.pack(m, payload)
        self.srv.add(data)
        return data

    def test_legacy_host_declaration_install_and_recheck_are_ok(self):
        need_sandbox(self)
        data = self.add_legacy_host_skill()
        rc, out = self.preview_and_confirm(accept_unverified=True)
        self.assertEqual(rc, market.EXIT_OK, out)
        self.assertIn("install.verify: OK", out)
        self.assertNotIn("依赖待补", out)
        self.assertEqual((self.rec()["verify"]["state"], self.rec()["verify"]["missing"]), ("ok", []))
        self.assertEqual(self.rec()["sha256"], bundle.sha256(data))
        rc, out = self.reverify()
        self.assertEqual(rc, market.EXIT_OK, out)
        self.assertEqual((self.rec()["verify"]["state"], self.rec()["verify"]["missing"]), ("ok", []))

    def test_legacy_host_with_real_missing_cli_remains_pending(self):
        need_sandbox(self)
        self.add_legacy_host_skill(extra_cli=({"name": FAKE},))
        rc, out = self.preview_and_confirm(accept_unverified=True)
        self.assertEqual(rc, market.EXIT_OK, out)
        self.assertEqual(self.rec()["verify"]["missing"], [FAKE])
        rc, out = self.reverify()
        self.assertEqual(rc, market.EXIT_ERROR, out)
        self.assertEqual(self.rec()["verify"]["missing"], [FAKE])

    def test_legacy_host_verify_command_is_not_made_available_in_sandbox(self):
        need_sandbox(self)
        files = {**fx.SKILL_FILES, "scripts/demo.py":
                 "import subprocess\nsubprocess.run(['agentj', '--version'], check=True)\n"}
        self.add_legacy_host_skill(files=files)
        rc, out = self.preview_and_confirm(accept_unverified=True)
        self.assertEqual(rc, market.EXIT_OK, out)
        self.assertNotEqual(self.rec()["verify"]["state"], "ok")
        self.assertIn("agentj", out)
        rc, out = self.reverify()
        self.assertEqual(rc, market.EXIT_ERROR, out)
        self.assertEqual(self.rec()["verify"]["state"], "failed")
        self.assertIn("agentj", out)

    def test_a_real_failure_installs_as_pending(self):
        """ADR-0269-w4-plaza (#479, superseding the a2 rule): only signature / digest / audit block; a failing self-test is
        「已安装·依赖待补」 with a system task, like a missing dependency."""
        need_sandbox(self)
        files = {**tm.fx.SKILL_FILES, "scripts/demo.py": "import sys\nsys.exit(3)\n"}
        self.add_skill(files=files)
        rc, out = self.preview_and_confirm(accept_unverified=True)
        self.assertEqual(rc, market.EXIT_OK, out)
        self.assertEqual(self.rec()["verify"]["state"], "pending")

    def test_passing_install_shows_self_check_apart_from_acceptance(self):
        need_sandbox(self)
        self.add_skill()
        rc, out = self.preview_and_confirm(accept_unverified=True)
        self.assertEqual(rc, 0, out)
        self.assertIn(plaza_deps.PASSED[0], out)
        self.assertIn(plaza_deps.ACCEPT[0], out)
        self.assertEqual(self.rec()["verify"]["state"], "ok")

    def reverify(self, **kw):
        out = io.StringIO()
        rc = market.run_reverify("demo-skill", st=self.e.st, out=out, local=True, post=self.srv, get=self.srv.get, **kw)
        return rc, out.getvalue()

    def pending_install(self):
        need_sandbox(self)
        self.add_skill(manifest={"install": {"skill_dir_name": "demo-skill", "post_install": [], "verify": f"{FAKE} --self-test"}})
        rc, out = self.preview_and_confirm(accept_unverified=True)
        self.assertEqual((rc, self.rec()["verify"]["state"]), (0, "pending"), out)

    def test_rerun_after_the_fix_passes_without_a_new_signature(self):
        self.pending_install()
        rc, out = self.reverify()
        self.assertEqual(rc, market.EXIT_ERROR, out)
        self.assertIn(plaza_deps.PENDING[0], out)
        with mock.patch("shutil.which", new=which_with(FAKE)):
            rc, out = self.reverify()
        self.assertEqual(rc, 0, out)
        self.assertIn(plaza_deps.PASSED[0], out)
        self.assertEqual(self.rec()["verify"]["state"], "ok")

    def test_rerun_refuses_changed_installed_files(self):
        self.pending_install()
        (self.target() / "SKILL.md").write_text("changed by someone\n")
        with mock.patch("shutil.which", new=which_with(FAKE)):
            rc, out = self.reverify()
        self.assertEqual(rc, market.EXIT_DIGEST, out)
        self.assertIn("agentj plaza request", out)
        self.assertEqual(self.rec()["verify"]["state"], "pending")

    def test_rerun_refuses_a_different_bundle(self):
        self.pending_install()
        for p in (self.e.st.root / "plaza" / "cache").glob("*.ajpkg"):
            p.unlink()
        self.srv.items.clear() if hasattr(self.srv, "items") else None
        self.add_skill(manifest={"install": {"skill_dir_name": "demo-skill", "post_install": [], "verify": "python3 scripts/demo.py"}},
                       files={**tm.fx.SKILL_FILES, "README.md": "# other\n"})
        with mock.patch("shutil.which", new=which_with(FAKE)):
            rc, out = self.reverify()
        self.assertEqual(rc, market.EXIT_DIGEST, out)
        self.assertIn("sha256", out)

    def test_user_copies_are_reported_not_mounted(self):
        d = self.e.home / ".nvm" / "versions" / "node" / "v20.0.0" / "bin"
        d.mkdir(parents=True)
        (d / FAKE).write_text("#!/bin/sh\n")
        (d / FAKE).chmod(0o755)
        with mock.patch.dict(os.environ, {"HOME": str(self.e.home)}):
            got = plaza_deps.missing([FAKE, "sh"], market.VERIFY_PATH)
            text = plaza_deps.task_text("demo-skill", "1.0.0", got, False, market.VERIFY_PATH)
        self.assertEqual(got, [{"name": FAKE, "user": ["~/.nvm/versions/node/v20.0.0/bin"]}])
        self.assertIn("用户目录里有", text)
        self.assertNotIn(str(self.e.home), text)
        self.assertNotIn(str(self.e.home), market.verify_env(self.e.root)["PATH"])
        self.assertTrue(market.VERIFY_PATH.startswith("/usr/local/bin:/usr/bin:/bin"))     # + Homebrew bins (ADR-0269-w4-plaza)


class HostTask(unittest.TestCase):
    def test_pending_install_sends_one_system_task(self):
        import test_plaza_host_install as hi
        t = hi.HostRequest("test_allow_installs_with_one_card_and_receipt")
        t.setUp()
        self.addCleanup(t.doCleanups)
        # This positive install request originates in an actual host-held owner turn.
        from agentj import approval_review, provenance
        t.host.desktop_turn = 1
        approval_review.remember(t.host, {'id': 1, 'ts': int(__import__('time').time()*1000),
                                         'src': {'k': 'terminal', 'text': 'Install the synthetic package'}},
                                 provenance.terminal())
        need_sandbox(self)
        t.add_skill(manifest={"install": {"skill_dir_name": "demo-skill", "post_install": [], "verify": f"{FAKE} --x"},
                              "permissions": {}})
        sent = []
        with mock.patch.object(t.host.agent, "submit", new=sent.append, create=True), \
                mock.patch.object(market.plaza_perms, "allowed", return_value=(market.plaza_perms.ALL, "test")):
            res, _ = t.run_host({"name": "demo-skill", "harness": "claude_code"}, "allow")
        self.assertEqual(res["rc"], 0, res)
        self.assertIn("依赖", t.cards()[0]["summary"], "the card says what is missing before the owner allows")
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].source["kind"], "system")
        self.assertIn(FAKE, sent[0].text)
        self.assertIn("agentj plaza verify demo-skill", sent[0].text)
        self.assertTrue(any(plaza_deps.PENDING[0] in n for n in t.notices), t.notices)
        from agentj import plaza_host
        with mock.patch("shutil.which", new=which_with(FAKE)):
            res = asyncio.run(plaza_host.verify(t.host, {"name": "demo-skill", "harness": None}))
        self.assertEqual(res["rc"], 0, res)
        self.assertIn("demo-skill 自检通过", t.notices[-1])     # no install line recorded (fake notice): a new line says so
        self.assertEqual(asyncio.run(plaza_host.verify(t.host, {"name": "../x"}))["why"], "bad_request")

    def test_cli_asks_the_host_and_prints_its_receipt(self):
        seen, out = [], io.StringIO()
        rc = market.run_reverify("demo-skill", st=object(), out=out,
                                 call=lambda st, req, t: seen.append(req) or {"ok": True, "rc": 0, "out": "receipt-466\n"})
        self.assertEqual((rc, seen[0]["cmd"], out.getvalue()), (0, "plaza_verify", "receipt-466\n"))
        with mock.patch.object(market.fence, "inside", return_value=True):
            rc = market.run_reverify("demo-skill", st=object(), out=out, call=lambda st, req, t: None)
        self.assertEqual(rc, market.EXIT_UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()
