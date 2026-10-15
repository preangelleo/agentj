"""P220 (Leo 10-13, a Windows owner in Hong Kong installing from zero): the host side of the one-command install.

- `agentj agent detect <harness> --probe --model M` makes ONE real call, to that harness, with that model — the installer's
  fallback when Claude Code's own default is a model the owner's relay does not serve (400 "Claude Fable 5.1 is not supported").
- `agentj agent switch <harness>` with no Agent connected yet is a first connection, not 「Agent J 还没接任何 Agent」: it connects
  like `agentj agent <harness>` and restarts a running background service. A real switch (an Agent already connected), a dry
  run, --scan / --result and the worker are unchanged.
Every test uses a throw-away HOME / XDG_CONFIG_HOME / CLAUDE_CONFIG_DIR / state dir and stand-in harness executables.
"""
import _hermetic  # noqa: F401,I001
import contextlib
import io
import json
import os
import pathlib
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import cli, harness, service, switch_harness  # noqa: E402
from agentj.state import State  # noqa: E402
from test_p155_switch_harness import FAKE  # noqa: E402


class _Env(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aj-p220-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        env = {"HOME": str(self.tmp), "XDG_CONFIG_HOME": str(self.tmp / "config"), "CLAUDE_CONFIG_DIR": str(self.tmp / "cc"),
               "AGENTJ_STATE_DIR": str(self.tmp / "st"), "AGENTJ_SKILL_LINK": "off", "CODEX_HOME": str(self.tmp / "codex-home")}
        (self.tmp / "codex-home").mkdir()
        for k in harness.NAMES:
            p = self.bin / k
            p.write_text(FAKE)
            p.chmod(p.stat().st_mode | stat.S_IXUSR)
            (self.bin / (k + ".mode")).write_text("ok")
            env[harness.BIN_ENV[k]] = str(p)
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.st = State()
        self.st.init(relay="ws://127.0.0.1:1")

    def argv(self, kind):
        p = self.bin / (kind + ".argv")
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def run_cli(self, *args):
        out = io.StringIO()
        code = 0
        with mock.patch("agentj.claude_auth.available", return_value=True), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(out):
            try:
                cli.main(list(args))
            except SystemExit as e:
                code = e.code or 0
        return code, out.getvalue()


class DetectWithModel(_Env):
    def test_probe_only_the_named_harness_with_the_model(self):
        code, out = self.run_cli("agent", "detect", "claude", "--probe", "--model", "claude-opus-5-5", "--json")
        self.assertEqual(code, 0, out)
        d = json.loads(out)
        by = {h["name"]: h for h in d["harnesses"]}
        self.assertTrue(by["claude"]["callable"])
        self.assertEqual(by["codex"]["probe_reason"], "not_probed")
        self.assertEqual(self.argv("codex"), [])
        self.assertIn("claude-opus-5-5", self.argv("claude")[0])

    def test_plain_probe_is_unchanged(self):
        code, out = self.run_cli("agent", "detect", "--probe", "--json")
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(out)["usable"], ["claude", "codex", "opencode", "kimi", "qwen"])   # P305 / P310: kimi, qwen = fakeacp.py
        self.assertFalse(any("claude-opus-5-5" in a for a in self.argv("claude")[0]))


class FirstConnect(_Env):
    def test_switch_with_no_agent_connects_and_restarts_the_service(self):
        restarted = []
        with mock.patch.object(service, "status", return_value={"installed": True, "active": "active", "kind": "systemd"}), \
                mock.patch.object(service, "restart", side_effect=lambda: restarted.append(1) or {}), \
                mock.patch.object(switch_harness, "cli", side_effect=AssertionError("no switch job for a first connection")):
            code, out = self.run_cli("agent", "switch", "codex", "--dir", str(self.work))
        self.assertEqual(code, 0, out)
        self.assertIn("还没接任何 Agent，不用切换", out)
        self.assertEqual(self.st.agent_config()["kind"], "codex")
        self.assertEqual(restarted, [1])
        self.assertIn("后台服务已重启", out)

    def test_no_service_says_the_one_next_step(self):
        with mock.patch.object(service, "status", return_value={"installed": False, "active": None, "kind": "systemd"}):
            code, out = self.run_cli("agent", "switch", "claude", "--dir", str(self.work))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.st.agent_config()["kind"], "claude")
        self.assertIn("agentj service install", out)

    def test_a_real_switch_still_goes_through_the_gate(self):
        self.st.set_agent_config("claude", str(self.work))
        with mock.patch.object(switch_harness, "cli", return_value=0) as sw:
            code, out = self.run_cli("agent", "switch", "codex")
        self.assertEqual(code, 0, out)
        sw.assert_called_once()
        self.assertEqual(self.st.agent_config()["kind"], "claude", "the job switches, not the CLI")

    def test_dry_run_scan_and_result_are_untouched(self):
        for extra in (["--dry-run"], ["--result"], ["--scan"]):
            with mock.patch.object(switch_harness, "cli", return_value=0) as sw:
                self.run_cli("agent", "switch", "codex", *extra)
            sw.assert_called_once()
            self.assertIsNone(self.st.agent_config(), extra)


if __name__ == "__main__":
    unittest.main()
