"""P282: /switch-harness misjudged harnesses in the background service's environment (Mac seat 10-14).

- Codex 0.156.0 vs 0.162.1: `agentj service install` saved the versioned folder behind ~/.local/bin/codex
  (…/standalone/releases/0.156.0-…/bin/codex); codex updated itself and the service kept running the old one. A saved path
  that is another version of the same install tree than the one PATH finds now is bypassed (pin_stale / pin_missing), the
  env file line is healed, and new installs save the PATH entry itself.
- Claude Code "not signed in": its token lived only in the owner's shell. The probe says not_logged_in (no login the service
  can see, by Claude Code's own lookup order) with a one-step way (/login → Keychain, or service import-key).
- No one-way switches: the job also checks the way back (the current harness) unless --force; --undo goes back to the entry
  from before the last switch with no gate (restart check + roll-back still apply).
- A launchd job replayed at login (leftover plist) does nothing; switch jobs unload themselves on macOS.
Run (in host/tests): python -m unittest test_p282_switch_probe
"""
import _hermetic  # noqa: F401,I001
import json
import os
import pathlib
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from agentj import binaries, harness, service, service_recovery  # noqa: E402
from agentj import switch_harness as sw  # noqa: E402
from test_p155_switch_harness import FAKE, UP, Clock, FakeServe, fake_probe, mk_state  # noqa: E402


def exe(p: pathlib.Path, body="#!/bin/sh\necho x\n"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    p.chmod(0o755)
    return p


class Pins(unittest.TestCase):
    """The Mac seat's layout: ~/.local/bin/codex → …/standalone/current/bin/codex → releases/<v>/bin/codex."""

    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="p282-pin-")
        self.addCleanup(t.cleanup)
        self.root = pathlib.Path(t.name)
        rel = self.root / "home/.codex/packages/standalone/releases"
        self.old = exe(rel / "0.156.0-aarch64-apple-darwin/bin/codex")
        self.new = exe(rel / "0.162.1-aarch64-apple-darwin/bin/codex")
        cur = self.root / "home/.codex/packages/standalone/current"
        cur.symlink_to(rel / "0.162.1-aarch64-apple-darwin")
        self.bin = self.root / "home/.local/bin"
        self.bin.mkdir(parents=True)
        (self.bin / "codex").symlink_to(cur / "bin/codex")
        self.env = {"HOME": str(self.root / "home"), "PATH": str(self.bin) + ":/usr/bin:/bin"}

    def test_version_sibling(self):
        self.assertTrue(binaries.version_sibling(str(self.old), str(self.new)))
        self.assertTrue(binaries.version_sibling("/u/.local/share/claude/versions/2.1.283", "/u/.local/share/claude/versions/2.1.290"))
        self.assertFalse(binaries.version_sibling("/opt/homebrew/bin/codex", "/u/.local/bin/codex"))      # another install
        self.assertFalse(binaries.version_sibling("/a/b/codex", "/a/c/codex"))                            # no version
        self.assertFalse(binaries.version_sibling(str(self.new), str(self.new)))

    def test_stale_pin_follows_path(self):
        r = binaries.resolve("codex", {**self.env, "AGENTJ_CODEX_BIN": str(self.old)})
        self.assertEqual((r["path"], r["reason"], r["pinned"]), (str(self.new), "pin_stale", str(self.old)))

    def test_deleted_pin_follows_path(self):
        gone = str(self.old).replace("0.156.0", "0.150.0")
        r = binaries.resolve("codex", {**self.env, "AGENTJ_CODEX_BIN": gone})
        self.assertEqual((r["path"], r["reason"]), (str(self.new), "pin_missing"))

    def test_owner_pins_elsewhere_are_kept(self):
        other = exe(self.root / "opt/codex/bin/codex")
        r = binaries.resolve("codex", {**self.env, "AGENTJ_CODEX_BIN": str(other)})
        self.assertEqual((r["path"], r["reason"]), (str(other), "explicit"))
        r = binaries.resolve("codex", {**self.env, "AGENTJ_CODEX_BIN": str(self.root / "nope/codex")})
        self.assertEqual((r["path"], r["reason"]), (None, "missing"))           # unrelated missing override: as before

    def test_service_env_saves_the_path_entry(self):
        env, _ = service.service_env(self.env)
        self.assertEqual(env["AGENTJ_CODEX_BIN"], str(self.bin / "codex"))     # not releases/0.162.1-…

    def test_heal_rewrites_only_the_stale_line(self):
        f = self.root / "agentj.env"
        secret = "ANTHROPIC_API_KEY='sk-p282-canary'\n"
        f.write_text("# owner notes\n" + secret + f'AGENTJ_CODEX_BIN="{self.old}"\nOTHER=1\n')
        f.chmod(0o600)
        with mock.patch.object(service, "env_file", return_value=str(f)):
            healed = service.heal_binary_pins("agentj", self.env)
            self.assertEqual(healed, [{"kind": "codex", "old": str(self.old), "new": str(self.bin / "codex")}])
            self.assertEqual(service.heal_binary_pins("agentj", self.env), [])                        # idempotent
        text = f.read_text()
        self.assertIn(secret, text)
        self.assertIn("# owner notes\n", text)
        self.assertIn("OTHER=1\n", text)
        self.assertIn(f'AGENTJ_CODEX_BIN="{self.bin / "codex"}"', text)
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)

    def test_scan_row_reports_the_bypassed_pin(self):
        with mock.patch.dict(os.environ, {**self.env, "AGENTJ_CODEX_BIN": str(self.old)}):
            h = harness._one("codex")
        w = sw._where(h)
        self.assertEqual(w["pinned"]["why"], "pin_stale")
        self.assertIn("0.156.0", w["pinned"]["path"])
        self.assertNotIn("others", w)                       # the bypassed pin is not listed twice (Mac seat run)
        zh = "\n".join(sw._where_lines({**w, "executable": w["executable"]}, "zh"))
        self.assertIn("0.156.0", zh)
        self.assertIn("记录也已更新", zh)
        en = sw._where_lines({"executable": "~/.local/bin/codex", "others": [{"path": "/opt/homebrew/bin/codex", "version": "codex-cli 0.150"}]}, "en")
        self.assertIn("remove it", en[0])


class Login(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="p282-login-")
        self.addCleanup(t.cleanup)
        self.root = pathlib.Path(t.name)
        env = {"CLAUDE_CONFIG_DIR": str(self.root / "claude"), "CODEX_HOME": str(self.root / "codex")}
        (self.root / "claude").mkdir()
        (self.root / "codex").mkdir()
        for k in harness.NAMES:
            p = exe(self.root / "bin" / k, FAKE)
            env[harness.BIN_ENV[k]] = str(p)
            (self.root / "bin" / (k + ".mode")).write_text("signed_out")
        drop = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CODEX_API_KEY", "OPENAI_API_KEY",
                "AGENTSRELAY_OPENAI_KEY", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        for k in drop:
            os.environ.pop(k, None)
        p = mock.patch.object(harness.sys, "platform", "linux")      # no Keychain lookup from a test
        p.start()
        self.addCleanup(p.stop)

    def test_no_login_in_this_environment_is_said_so(self):
        d = harness.probe(only=("claude",), timeout=10)
        self.assertEqual(next(h for h in d["harnesses"] if h["name"] == "claude")["probe_reason"], "not_logged_in")
        d = harness.probe(only=("codex",), timeout=10)
        self.assertEqual(next(h for h in d["harnesses"] if h["name"] == "codex")["probe_reason"], "not_logged_in")

    def test_a_login_the_service_sees_keeps_the_native_code(self):
        os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = "p282-canary"
        d = harness.probe(only=("claude",), timeout=10)
        self.assertEqual(next(h for h in d["harnesses"] if h["name"] == "claude")["probe_reason"], "native_failed")
        self.assertNotIn("p282-canary", json.dumps(d))

    def test_claude_lookup_order_sources(self):
        self.assertEqual(harness.claude_login()[0], "warn")
        settings = self.root / "claude/settings.json"
        settings.write_text(json.dumps({"apiKeyHelper": "~/bin/key.sh"}))
        settings.chmod(0o600)                     # harness reads native settings only from a private file (secure_read)
        self.assertEqual(harness.claude_login()[0], "ok")
        (self.root / "claude/settings.json").write_text(json.dumps({"env": {"CLAUDE_CODE_OAUTH_TOKEN": "x"}}))
        self.assertEqual(harness.claude_login()[0], "ok")
        (self.root / "claude/settings.json").unlink()
        self.assertEqual(harness.claude_login({"CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CONFIG_DIR": str(self.root / "claude")})[0], "env")

    def test_the_owner_gets_a_one_step_way(self):
        with mock.patch.object(service, "platform", return_value="macos"):
            zh = sw.reason_text("not_logged_in", "claude", "zh")
            en = sw.reason_text("not_logged_in", "claude", "en")
        for t in (zh, en):
            self.assertIn("/login", t)
            self.assertIn("agentj service import-key CLAUDE_CODE_OAUTH_TOKEN", t)
        self.assertIn("钥匙串", zh)
        self.assertIn("Keychain", en)
        self.assertIn("codex login", sw.reason_text("not_logged_in", "codex", "zh"))
        # P298: OpenCode now has its own way too — a key, or one of its free models with no key
        self.assertIn("opencode auth login", sw.reason_text("not_logged_in", "opencode", "zh"))
        self.assertIn("--model opencode/", sw.reason_text("not_logged_in", "opencode", "en"))


def probe_by(ok: dict, seen=None, reason="not_logged_in"):
    """ok: kind → callable."""
    def fn(only=None, model=None, timeout=60):
        if seen is not None:
            seen.append(only[0])
        return {"harnesses": [{"name": n, "installed": True, "version": "1.0", "callable": bool(ok.get(n)) and n in only,
                               **({} if ok.get(n) else {"probe_reason": reason})} for n in harness.NAMES]}
    return fn


class WayBack(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p282-job-")
        self.addCleanup(self.tmp.cleanup)
        unit = pathlib.Path(self.tmp.name) / "units"
        p = mock.patch.object(sw, "receipt_path", side_effect=lambda name=sw.RECEIPT: unit / ("agentj." + name))
        p.start()
        self.addCleanup(p.stop)
        self.clock = Clock()

    def job(self, st, fake, target, probe, **kw):
        return sw.worker(st, target, None, probe=probe, ctl=fake, restart=fake.restart, status=UP,
                         ready=lambda st, limit: "idle", sleep=self.clock.sleep, clock=self.clock, **kw)

    def test_no_way_back_refuses_and_changes_nothing(self):
        """The Mac seat: on Claude Code, which the service cannot call; OpenCode works — switching would be one-way."""
        st = mk_state(self.tmp.name, "claude")
        raw = st.config()["agent"]
        fake = FakeServe(st, "claude")
        seen = []
        self.assertEqual(self.job(st, fake, "opencode", probe_by({"opencode": True}, seen)), 1)
        self.assertEqual(seen, ["opencode", "claude"])
        self.assertEqual((st.config()["agent"], fake.restarts), (raw, 0))
        rec = sw.read_receipt(st)
        self.assertEqual((rec["status"], rec["reason"], rec["back"]), ("failed", "no_way_back", "not_logged_in"))
        self.assertIn({"cmd": "harness_progress", "phase": "probe_back", "to": "opencode", "from": "claude"}, fake.calls)
        r = {"ok": False, "reason": "no_way_back", "back": "not_logged_in", "target": "opencode", "from": "claude"}
        zh = sw._say(r, "zh")
        self.assertIn("切不回 Claude Code", zh)
        self.assertIn("--force", zh)
        self.assertIn("/login", zh)
        self.assertIn("no way back to Claude Code", sw._say(r, "en"))

    def test_force_switches_and_undo_returns_without_a_gate(self):
        st = mk_state(self.tmp.name, "claude", model="claude-opus-5-5", effort="high")
        raw = st.config()["agent"]
        fake = FakeServe(st, "claude")
        self.assertEqual(self.job(st, fake, "opencode", probe_by({"opencode": True}), force=True), 0)
        self.assertEqual(st.agent_config()["kind"], "opencode")
        self.assertEqual(sw._previous(st)["kind"], "claude")

        def no_probe(**_):
            raise AssertionError("--undo must not run the gate")
        self.assertEqual(self.job(st, fake, None, no_probe, undo=True), 0)
        self.assertEqual(st.config()["agent"], raw)                      # exactly as it was (model, effort, fence)
        rec = sw.read_receipt(st)
        self.assertEqual((rec["status"], rec["target"], rec["from"], rec.get("undo")), ("ok", "claude", "opencode", True))
        self.assertEqual(fake.hellos[-1], ("claude", {"cmd": "harness_hello", "outcome": "ok", "from": "opencode", "to": "claude",
                                                      "handoff": "none"}))
        self.assertIn({"cmd": "harness_progress", "phase": "undo", "to": "claude", "from": "opencode"}, fake.calls)
        self.assertEqual(sw._previous(st)["kind"], "opencode")           # undo again toggles

    def test_undo_that_does_not_come_up_rolls_back_here(self):
        st = mk_state(self.tmp.name, "claude")
        fake = FakeServe(st, "claude")
        self.assertEqual(self.job(st, fake, "opencode", probe_by({"opencode": True}), force=True), 0)
        fake.behaviour["claude"] = {"status": "error"}
        self.assertEqual(self.job(st, fake, None, None, undo=True), 2)
        self.assertEqual(st.agent_config()["kind"], "opencode")
        self.assertEqual(sw.read_receipt(st)["status"], "rolled_back")

    def test_nothing_to_undo(self):
        st = mk_state(self.tmp.name, "claude")
        r = sw.request(st, None, undo=True, launch=lambda *a: self.fail("no job"))
        self.assertEqual(r["reason"], "nothing_to_undo")
        self.assertIn("没有可以撤回", sw._say(r, "zh"))
        self.assertEqual(self.job(st, FakeServe(st, "claude"), None, None, undo=True), 1)

    def test_request_undo_runs_the_job_and_a_failed_gate_to_the_previous_one_offers_it(self):
        st = mk_state(self.tmp.name, "claude")
        fake = FakeServe(st, "claude")
        self.assertEqual(self.job(st, fake, "opencode", probe_by({"opencode": True}), force=True), 0)
        launched = []

        def launch(st_, argv):
            launched.append(argv)
            if "--undo" in argv:
                self.job(st_, fake, None, None, undo=True, job=argv[argv.index("--job") + 1])
            else:
                self.job(st_, fake, argv[argv.index("switch") + 1], probe_by({}), job=argv[argv.index("--job") + 1])
        r = sw.request(st, "claude", launch=launch, sleep=self.clock.sleep, clock=self.clock)
        self.assertEqual((r["ok"], r["reason"], r.get("undo_possible")), (False, "not_logged_in", True))
        self.assertIn("agentj agent switch --undo", sw._say(r, "zh"))
        r = sw.request(st, None, undo=True, launch=launch, sleep=self.clock.sleep, clock=self.clock)
        self.assertTrue(r["ok"], r)
        self.assertIn("--undo", launched[-1])
        self.assertNotIn("claude", launched[-1][launched[-1].index("switch"):])   # the job reads the target itself
        self.assertEqual(st.agent_config()["kind"], "claude")


class Jobs(unittest.TestCase):
    def test_stale_job(self):
        st = None
        now = time.time()
        self.assertFalse(sw.stale_job(st, "j", {}))                                  # a caller older than P282
        self.assertFalse(sw.stale_job(st, "j", {"AGENTJ_JOB_AT": str(int(now))}))
        self.assertTrue(sw.stale_job(st, "j", {"AGENTJ_JOB_AT": str(int(now - 3600))}))   # replayed at the next login

    def test_a_replayed_job_does_nothing_and_unloads(self):
        a = mock.Mock(job="j", scan=False, target="codex", model=None, dry_run=False, force=False, undo=False)
        st = mock.Mock()
        with mock.patch.dict(os.environ, {"AGENTJ_JOB_AT": str(int(time.time() - 86400))}), \
                mock.patch.object(sw, "worker") as w, mock.patch.object(service_recovery, "cleanup_job") as c:
            self.assertEqual(sw._job(a, st), 0)
        w.assert_not_called()
        c.assert_called_once_with(st)

    def test_launch_records_when(self):
        got = {}
        with mock.patch.object(service_recovery, "launch_job", side_effect=lambda st, argv, purpose, extra_env: got.update(
                purpose=purpose, **extra_env)):
            sw._launch(None, ["x"], None)
        self.assertEqual(got["purpose"], "switch")
        self.assertLess(abs(float(got["AGENTJ_JOB_AT"]) - time.time()), 5)

    def test_switch_jobs_unload_themselves_on_macos(self):
        label = "net.agentj.switch." + "a" * 32
        with tempfile.TemporaryDirectory() as d:
            plist = pathlib.Path(d) / (label + ".plist")
            plist.write_text("x")
            with mock.patch.object(service, "platform", return_value="macos"), \
                    mock.patch.object(service, "plist_path", return_value=str(plist)), \
                    mock.patch.object(service, "_gui", return_value="gui/501"), \
                    mock.patch.object(service, "_launchctl") as lc, \
                    mock.patch.dict(os.environ, {"AGENTJ_JOB_PLIST": str(plist), "AGENTJ_JOB_LABEL": label}):
                service_recovery.cleanup_job(None)
            self.assertFalse(plist.exists())
            lc.assert_called_once_with("bootout", "gui/501/" + label)


if __name__ == "__main__":
    unittest.main()
