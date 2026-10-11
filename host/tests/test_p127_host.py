"""P127 host cut-over fixes: (1) the shared-Claude crossSessionInbound value really in effect (managed > local > project >
user) with specific phone reasons; (3) the Claude status-line tap on by default, chained at the layer really in effect.
Every case uses a temporary HOME / CLAUDE_CONFIG_DIR (never the real ~/.claude)."""
import _hermetic
import asyncio
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentj import claude_inbound as inbound, claude_statusline as tap, cli, doctor, preferences, shared  # noqa: E402
from agentj.state import State  # noqa: E402


class _Home(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p127-host-", dir="/var/tmp" if os.path.isdir("/var/tmp") else None)
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        env = patch.dict(os.environ, {"HOME": str(self.home), "CLAUDE_CONFIG_DIR": str(self.home / "cc"),
                                      "XDG_CONFIG_HOME": str(self.home / "config"), "AGENTJ_STATE_DIR": str(self.home / "st")})
        env.start(); self.addCleanup(env.stop)
        managed = patch.object(inbound, "MANAGED", (str(self.home / "managed" / "managed-settings.json"),))
        managed.start(); self.addCleanup(managed.stop)
        self.work = self.home / "work"; self.work.mkdir()
        self.st = State(); self.st.init(); self.st.set_agent_config("claude", str(self.work))
        preferences.ensure()
        # Start from no native setting and no Agent J record (setup itself may already apply the shared defaults).
        for p in (inbound.path(), inbound.meta_path(), tap.meta_path(), self.st.root / "claude-inbound-notice.json",
                  self.st.root / "claude-statusline-notice.json"):
            if p.exists(): p.unlink()
        for b in inbound.path().parent.glob("settings.json.agentj-backup-*"):
            b.unlink()

    def write(self, p, doc):
        p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(doc))

    @property
    def local(self): return self.work / ".claude" / "settings.local.json"
    @property
    def project(self): return self.work / ".claude" / "settings.json"
    @property
    def managed(self): return Path(inbound.MANAGED[0])

    def live_session(self, sid="aaaaaaaa-1111-2222-3333-444444444444", started=None, updated=None, name="s"):
        """A registry entry Claude Code writes for a live interactive session (pid = this process, a real socket)."""
        reg = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "sessions"; reg.mkdir(parents=True, exist_ok=True)
        sock_path = str(self.home / f"{name}.sock")
        s = socket.socket(socket.AF_UNIX); s.bind(sock_path); self.addCleanup(s.close)
        started = int((time.time() - 3600) * 1000) if started is None else started
        d = {"pid": os.getpid(), "sessionId": sid, "cwd": str(self.work), "startedAt": started, "kind": "interactive",
             "messagingSocketPath": sock_path, "updatedAt": updated or started}
        (reg / f"{os.getpid()}-{name}.json").write_text(json.dumps(d))
        return d


class InboundEffective(_Home):
    """Item 1: the value Claude Code really uses for the shared folder (managed > local > project > user)."""

    def test_user_hold_without_owner_off_is_repaired(self):
        self.write(inbound.path(), {"crossSessionInbound": "hold", "other": 1})
        self.assertTrue(inbound.ensure_shared_default(self.st))
        self.assertEqual(inbound.effective(str(self.work))[2], "accept")
        self.assertEqual(inbound.read()["other"], 1)
        self.assertEqual(len(list(inbound.path().parent.glob("settings.json.agentj-backup-*"))), 1)
        self.assertIsNotNone(inbound.default_notice(self.st))

    def test_project_hold_is_overridden_in_private_local_layer(self):
        self.write(self.project, {"crossSessionInbound": "hold", "team": True})
        before = self.project.read_bytes()
        self.assertTrue(inbound.ensure_shared_default(self.st))
        self.assertEqual(self.project.read_bytes(), before, "the shared project file is never edited")
        self.assertEqual(json.loads(self.local.read_text())["crossSessionInbound"], "accept")
        self.assertEqual(inbound.effective(str(self.work))[:1] + inbound.effective(str(self.work))[2:], ("local", "accept"))

    def test_local_hold_is_repaired_where_it_is(self):
        self.write(inbound.path(), {"crossSessionInbound": "accept"})
        self.write(self.local, {"crossSessionInbound": "hold", "statusLine": {"type": "command", "command": "x"}})
        self.assertTrue(inbound.ensure_shared_default(self.st))
        doc = json.loads(self.local.read_text())
        self.assertEqual((doc["crossSessionInbound"], doc["statusLine"]["command"]), ("accept", "x"))

    def test_managed_policy_is_never_changed_only_reported(self):
        self.write(self.managed, {"crossSessionInbound": "hold"})
        before = self.managed.read_bytes()
        self.assertFalse(inbound.ensure_shared_default(self.st))
        self.assertEqual(self.managed.read_bytes(), before)
        self.assertEqual(inbound.hold_reason(str(self.work)), "managed")
        row = doctor.check_shared_inbound(self.st)
        self.assertEqual(row["status"], doctor.FAIL)
        self.assertIn("组织策略", row["summary"])

    def test_explicit_owner_off_is_kept(self):
        inbound.set_enabled(False)
        self.assertFalse(inbound.ensure_shared_default(self.st))
        self.assertEqual(inbound.value(), "hold")
        self.assertEqual(inbound.hold_reason(str(self.work)), "owner_off")

    def test_off_recorded_by_an_older_version_is_kept(self):
        # pre-P127 `off`: hold now, and our own backup shows it used to be accept
        p = inbound.path(); self.write(p, {"crossSessionInbound": "accept"})
        inbound.update(lambda d: d.update(crossSessionInbound="hold"))
        inbound.meta_path().unlink(missing_ok=True)
        self.assertFalse(inbound.ensure_shared_default(self.st))
        self.assertEqual(inbound.value(), "hold")

    def test_refuse_and_project_refuse_are_reported_not_changed(self):
        self.write(self.project, {"crossSessionInbound": "refuse"})
        self.assertFalse(inbound.ensure_shared_default(self.st))
        self.assertFalse(self.local.exists())
        self.assertEqual(inbound.hold_reason(str(self.work)), "project")
        self.assertIn("项目设置", inbound.reason_text("project", "zh"))

    def test_on_command_repairs_effective_layer_and_clears_off(self):
        inbound.set_enabled(False)
        self.write(self.project, {"crossSessionInbound": "hold"})
        with patch("builtins.print"):
            self.assertEqual(inbound.command(["on"]), 0)
        self.assertEqual(inbound.effective(str(self.work))[2], "accept")
        self.assertIsNone(inbound.hold_reason(str(self.work)))

    def test_session_started_before_the_change_needs_clear(self):
        self.live_session()
        self.write(inbound.path(), {"crossSessionInbound": "hold"})
        self.assertTrue(inbound.ensure_shared_default(self.st))
        self.assertEqual(inbound.hold_reason(str(self.work)), "stale")
        self.assertIn("/clear", inbound.default_notice(self.st))
        self.assertIn("/clear", inbound.reason_text("stale", "zh"))
        self.assertIn("/clear", inbound.reason_text("stale", "en"))
        row = doctor.check_shared_inbound(self.st)
        self.assertEqual(row["status"], doctor.WARN)
        self.assertIn("/clear", row["summary"] + row.get("hint", ""))

    def test_session_after_change_is_fine(self):
        self.write(inbound.path(), {"crossSessionInbound": "hold"})
        self.assertTrue(inbound.ensure_shared_default(self.st))
        self.live_session(started=int((time.time() + 5) * 1000))
        self.assertIsNone(inbound.hold_reason(str(self.work)))
        self.assertEqual(doctor.check_shared_inbound(self.st)["status"], doctor.OK)

    def test_doctor_names_the_layer(self):
        self.write(self.project, {"crossSessionInbound": "refuse"})
        row = doctor.check_shared_inbound(self.st)
        self.assertEqual(row["status"], doctor.FAIL)
        self.assertIn("项目设置", row["summary"])


class PhoneHoldNotice(_Home, unittest.IsolatedAsyncioTestCase):
    async def notice_for(self, lang="zh"):
        host = Mock()
        a = shared.SharedClaudeAgent(host, {"kind": "claude", "dir": str(self.work), "_workflow_ceo": True, "language": lang})
        a.session = {"pid": os.getpid(), "sessionId": "aaaaaaaa-1111-2222-3333-444444444444", "messagingSocketPath": "/nonexistent"}
        a.attach = AsyncMock(return_value=True)
        with patch.object(shared, "claude_send"), patch.object(shared, "CLAUDE_INPUT_WAIT", 0.01):
            await a.turn("hello")
        host.turn_failed.assert_called_once()
        return host.agent_notice.call_args.args[0]

    async def test_specific_reasons_replace_maybe(self):
        self.write(self.managed, {"crossSessionInbound": "hold"})
        text = await self.notice_for()
        self.assertIn("组织策略", text); self.assertNotIn("可能拦下", text)
        self.managed.unlink()
        self.write(self.project, {"crossSessionInbound": "refuse"})
        self.assertIn("项目设置", await self.notice_for())
        self.assertIn("project settings", await self.notice_for("en"))

    async def test_default_hold_keeps_the_fix_command(self):
        text = await self.notice_for()
        self.assertIn("claude-inbound on", text)
        self.assertNotIn("可能拦下", text)

    async def test_accept_everywhere_says_busy_not_held(self):
        self.write(inbound.path(), {"crossSessionInbound": "accept"})
        text = await self.notice_for()
        self.assertNotIn("拦下", text)
        self.assertIn("别重复发送", text)


class StatuslineDefault(_Home):
    """Item 3: shared Claude turns the status-line tap on by default, at the layer Claude Code really uses."""

    def blob(self):
        return {"session_id": "sess-p127", "model": {"display_name": "Opus"}, "context_window": {"used_percentage": 12},
                "rate_limits": {"five_hour": {"used_percentage": 5}, "seven_day": {"used_percentage": 40}}}

    def run_line(self, settings):
        import subprocess
        cmd = json.loads(settings.read_text())["statusLine"]["command"]
        return subprocess.run(cmd, shell=True, input=json.dumps(self.blob()).encode(), capture_output=True)

    def test_shared_default_on_with_backup_notice_and_off_is_final(self):
        self.write(inbound.path(), {"other": 1})
        self.assertTrue(tap.ensure_default(self.st))
        self.assertIn("claude_statusline.py", inbound.read()["statusLine"]["command"])
        self.assertEqual(inbound.read()["other"], 1)
        self.assertEqual(len(list(inbound.path().parent.glob("settings.json.agentj-backup-*"))), 1)
        self.assertTrue(tap.active(None, str(self.work)))
        notice = inbound.default_notice(self.st, "zh")
        self.assertIn("claude-statusline off", notice)
        self.assertIn("claude-statusline off", inbound.default_notice(self.st, "en"))
        inbound.notice_delivered(self.st)
        self.assertIsNone(inbound.default_notice(self.st))
        self.assertFalse(tap.ensure_default(self.st), "already on: idempotent")
        with patch("builtins.print"):
            self.assertEqual(tap.command(["off"]), 0)
        self.assertEqual(inbound.read(), {"other": 1})
        self.assertFalse(tap.ensure_default(self.st), "owner off is never auto re-enabled")
        self.assertNotIn("statusLine", inbound.read())
        self.assertEqual(doctor.check_shared_statusline(self.st)["status"], doctor.OK)

    def test_owner_off_before_any_default_is_respected(self):
        with patch("builtins.print"):
            self.assertEqual(tap.command(["off"]), 0)
        self.assertFalse(tap.ensure_default(self.st))
        self.assertFalse(inbound.path().exists() and "statusLine" in inbound.read())

    def test_local_statusline_is_chained_where_it_wins_and_restored_exactly(self):
        import shlex
        old = {"type": "command", "command": shlex.quote(sys.executable) + " -c " + shlex.quote("print('relay-line')"), "padding": 1}
        self.write(self.local, {"statusLine": old, "hooks": {"x": 1}})
        self.write(inbound.path(), {"statusLine": {"type": "command", "command": "user-line"}})
        self.assertTrue(tap.ensure_default(self.st))
        self.assertEqual(inbound.read()["statusLine"]["command"], "user-line", "the shadowed user layer is not edited")
        r = self.run_line(self.local)
        self.assertEqual(r.stdout.strip(), b"relay-line", "the original status line still shows")
        stored = json.loads((self.st.root / "claude-statusline" / "sess-p127.json").read_text())
        self.assertEqual((stored["week"]["pct"], stored["ctx"]["pct"]), (40, 12), "weekly quota and water level arrive")
        self.assertEqual(doctor.check_shared_statusline(self.st)["status"], doctor.OK)
        tap.set_enabled(False, self.st.root, str(self.work), explicit="off")
        self.assertEqual(json.loads(self.local.read_text()), {"statusLine": old, "hooks": {"x": 1}})

    def test_project_statusline_chained_from_local_and_local_file_removed_on_off(self):
        self.write(self.project, {"statusLine": {"type": "command", "command": "printf team"}, "team": True})
        before = self.project.read_bytes()
        self.assertTrue(tap.ensure_default(self.st))
        self.assertEqual(self.project.read_bytes(), before, "the shared project file is never edited")
        self.assertEqual(self.run_line(self.local).stdout, b"team")
        self.assertEqual(tap.shadow(str(self.work)), None)
        tap.set_enabled(False, self.st.root, str(self.work), explicit="off")
        self.assertFalse(self.local.exists(), "the local file created only for the tap is removed again")
        self.assertEqual(self.project.read_bytes(), before)

    def test_user_install_shadowed_by_local_is_reported_and_rechained(self):
        tap.set_enabled(True, self.st.root)                       # an older install: user layer only
        self.write(self.local, {"statusLine": {"type": "command", "command": "printf relay"}})
        self.assertFalse(tap.active(None, str(self.work)))
        row = doctor.check_shared_statusline(self.st)
        self.assertEqual(row["status"], doctor.WARN)
        self.assertIn("settings.local.json", row["summary"])
        self.assertIn("agentj config claude-statusline on", row["hint"])
        self.assertTrue(tap.ensure_default(self.st))
        self.assertTrue(tap.active(None, str(self.work)))
        self.assertNotIn("statusLine", inbound.read(), "the user-layer install was restored when re-chaining")
        self.assertEqual(self.run_line(self.local).stdout, b"relay")
        self.assertIsNone(inbound.default_notice(self.st), "re-chaining is not a new default: no phone notice")

    def test_on_command_rechains_when_shadowed(self):
        with patch("builtins.print"):
            self.assertEqual(tap.command(["on"]), 0)
            self.write(self.local, {"statusLine": {"type": "command", "command": "printf relay"}})
            self.assertEqual(tap.command(["on"]), 0)
        self.assertTrue(tap.active(None, str(self.work)))

    def test_managed_statusline_is_reported_never_changed(self):
        self.write(self.managed, {"statusLine": {"type": "command", "command": "corp"}})
        before = self.managed.read_bytes()
        self.assertFalse(tap.ensure_default(self.st))
        self.assertEqual(self.managed.read_bytes(), before)
        self.assertFalse(inbound.path().exists() and "statusLine" in inbound.read())
        row = doctor.check_shared_statusline(self.st)
        self.assertEqual(row["status"], doctor.WARN)
        self.assertIn("组织策略", row["summary"])

    def test_only_shared_claude(self):
        for cfg in ({"kind": "codex", "dir": str(self.work)}, {"kind": "claude", "dir": str(self.work), "session_mode": "independent"}, None):
            with patch.object(self.st, "agent_config", return_value=cfg):
                self.assertFalse(tap.ensure_default(self.st))
                self.assertIsNone(doctor.check_shared_statusline(self.st))
        self.assertFalse(inbound.path().exists())

    def test_cli_selection_turns_both_defaults_on(self):
        args = SimpleNamespace(mode="claude", dir=str(self.work), model=None, unfenced=False, allow_docker=False)
        with patch("agentj.wizard.bootstrap_root"), patch("agentj.fence.problem", return_value=None), patch("builtins.print"):
            cli.cmd_agent(args)
        self.assertEqual(inbound.effective(str(self.work))[2], "accept")
        self.assertTrue(tap.active(None, str(self.work)))
        notice = inbound.default_notice(self.st)
        self.assertIn("claude-inbound off", notice); self.assertIn("claude-statusline off", notice)


if __name__ == "__main__":
    unittest.main()
