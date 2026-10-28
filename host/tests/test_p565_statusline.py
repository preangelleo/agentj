"""P565 追加 B (#345): after an upgrade moved Python (3.11 → 3.12) the Claude status-line tap command pointed at an
interpreter / script that no longer existed — `active()` still said yes, doctor stayed quiet, ensure_default did nothing
and the phone kept a day-old weekly number with status ok / reading_valid true.

Every case uses a temporary HOME / CLAUDE_CONFIG_DIR / state (never the real ~/.claude); no service manager is touched."""
import _hermetic  # noqa: F401
from _testtmp import TEST_TMP  # noqa: E402
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentj import claude_inbound as inbound, claude_statusline as tap, doctor, preferences, quota, shared, update  # noqa: E402
from agentj.state import State  # noqa: E402

HOST = Path(__file__).resolve().parents[1]
SID = "aaaaaaaa-1111-2222-3333-444444444444"


class _Home(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p565b-", dir=TEST_TMP)
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
        self.mode("shared")
        for p in (inbound.path(), tap.meta_path(), self.st.root / "claude-statusline-notice.json"):
            if p.exists(): p.unlink()
        inbound.path().parent.mkdir(parents=True, exist_ok=True)
        inbound.path().write_text(json.dumps({"statusLine": {"type": "command", "command": "echo owner-line"}}))

    def mode(self, m):
        preferences.path().write_text(preferences.edit(preferences.path().read_text(), "agent.session_mode", m))

    def old_install(self, explicit=None, state_root=None):
        """The pre-P565B tap as 0.25.17 wrote it: `<uv venv>/bin/python3.11 <…/lib/python3.11/site-packages/agentj/
        claude_statusline.py> tap <meta>` — then the upgrade rebuilds the venv on 3.12 and the 3.11 paths are gone."""
        venv = self.home / "uv-tools" / "agentj"
        exe = venv / "bin" / "python3.11"; exe.parent.mkdir(parents=True, exist_ok=True); exe.write_text("#!/bin/sh\nexit 0\n"); exe.chmod(0o700)
        script = venv / "lib" / "python3.11" / "site-packages" / "agentj" / "claude_statusline.py"
        script.parent.mkdir(parents=True, exist_ok=True); script.write_text("")
        meta = tap.meta_path().resolve()
        installed = {"type": "command", "command": " ".join(shlex.quote(str(x)) for x in (exe, script, "tap", meta))}
        original = {"type": "command", "command": "echo owner-line"}
        tap.private_write(meta, {"enabled": True, "had_original": True, "original": original, "installed": installed,
                                 "state_root": str(Path(state_root or self.st.root).resolve()), "settings": str(inbound.path()),
                                 "dir": None, "created_file": False, "explicit": explicit})
        inbound.path().write_text(json.dumps({"statusLine": installed, "other": 1}))
        return venv

    def upgrade_moves_python(self, venv):
        shutil.rmtree(venv / "lib" / "python3.11"); (venv / "bin" / "python3.11").unlink()


class OutdatedEntry(_Home):
    def test_interpreter_dir_gone_doctor_warns_ensure_default_repairs(self):
        venv = self.old_install()
        self.upgrade_moves_python(venv)
        self.assertFalse(tap.active(None, str(self.work)), "a command whose interpreter is gone is not active")
        self.assertEqual(tap.shadow(str(self.work)), "outdated")
        row = doctor.check_shared_statusline(self.st)
        self.assertEqual((row["status"], row["hint"]), (doctor.WARN, "agentj config claude-statusline on"))
        self.assertIn("Python", row["summary"])

        self.assertTrue(tap.ensure_default(self.st))
        doc = inbound.read()
        cmd = doc["statusLine"]["command"]
        self.assertEqual(cmd, tap.entry(tap.meta_path().resolve()))
        self.assertIn("-P -m agentj.claude_statusline tap", cmd)
        for bad in ("site-packages", "claude_statusline.py", "python3.11"):
            self.assertNotIn(bad, cmd)
        self.assertEqual(doc["other"], 1)
        self.assertEqual(tap.load_meta()["original"]["command"], "echo owner-line", "the owner's line stays chained")
        self.assertTrue(tap.active(None, str(self.work)))
        self.assertEqual(doctor.check_shared_statusline(self.st)["status"], doctor.OK)
        self.assertFalse(tap.ensure_default(self.st), "idempotent")

        # the repaired command really runs: meter fields land, the owner's original output comes through
        blob = {"session_id": SID, "rate_limits": {"seven_day": {"used_percentage": 40, "resets_at": time.time() + 86400}}}
        env = {**os.environ, "PYTHONPATH": str(HOST)}
        r = subprocess.run(cmd, shell=True, input=json.dumps(blob).encode(), capture_output=True, env=env, cwd=self.work,
                           timeout=60)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, b"owner-line"), r.stderr[-400:])
        self.assertEqual(json.loads((self.st.root / "claude-statusline" / f"{SID}.json").read_text())["week"]["pct"], 40)

    def test_old_format_still_present_is_migrated_too(self):
        self.old_install()              # interpreter still there: the pre-P565B format is migrated anyway
        self.assertEqual(tap.shadow(str(self.work)), "outdated")
        self.assertTrue(tap.ensure_default(self.st))
        self.assertEqual(inbound.read()["statusLine"]["command"], tap.entry(tap.meta_path().resolve()))

    def test_versioned_interpreter_name_is_written_unversioned(self):
        d = self.home / "venv" / "bin"; d.mkdir(parents=True)
        real = d / "python3.12"; real.write_text(""); real.chmod(0o700)
        (d / "python3").symlink_to("python3.12")
        with patch.object(tap.sys, "executable", str(real)):
            self.assertEqual(tap.interpreter(), str(d / "python3"))
        with patch.object(tap.sys, "executable", str(d / "python3")):
            self.assertEqual(tap.interpreter(), str(d / "python3"))

    def test_owner_off_and_another_install_are_left_alone(self):
        venv = self.old_install(state_root=self.home / "other-state")
        self.upgrade_moves_python(venv)
        before = inbound.read()
        self.assertFalse(tap.ensure_default(self.st), "another install's tap is that install's to repair")
        self.assertEqual(inbound.read(), before)
        tap.meta_path().unlink()
        venv = self.old_install(explicit="off")
        self.upgrade_moves_python(venv)
        before = inbound.read()
        self.assertFalse(tap.ensure_default(self.st))
        self.assertEqual(inbound.read(), before)

    def test_explicit_on_in_independent_mode_is_migrated(self):
        self.mode("independent")
        venv = self.old_install(explicit="on")
        self.upgrade_moves_python(venv)
        self.assertTrue(tap.ensure_default(self.st))
        self.assertEqual(inbound.read()["statusLine"]["command"], tap.entry(tap.meta_path().resolve()))
        self.assertEqual(tap.load_meta()["explicit"], "on")

    def test_upgrade_flow_repairs_proactively(self):
        venv = self.old_install()
        self.upgrade_moves_python(venv)
        self.assertIn("shared_statusline", update.repair_upgrade(self.st, []))
        self.assertTrue(tap.active(None, str(self.work)))
        self.assertEqual(update.repair_upgrade(self.st, []), [])


class ExpiredReading(_Home):
    def reading(self, age, active_ago):
        d = self.st.root / "claude-statusline"; d.mkdir(parents=True, exist_ok=True)
        f = d / f"{SID}.json"
        now = time.time()
        f.write_text(json.dumps({"session_id": SID, "h5": {"pct": 20, "reset": now + 3600},
                                 "week": {"pct": 37, "reset": now + 3 * 86400}}))
        os.utime(f, (now - age, now - age))
        t = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / "-work" / f"{SID}.jsonl"
        t.parent.mkdir(parents=True, exist_ok=True); t.write_text("{}\n")
        os.utime(t, (now - active_ago, now - active_ago))
        os.utime(t.parent, (now - active_ago, now - active_ago))
        return t

    def claude(self):
        return next(a for a in quota.report(self.st.root)["harnesses"] if a["harness"] == "claude")

    def test_quota_marks_old_reading_stale_while_claude_kept_working(self):
        self.reading(age=3 * 3600, active_ago=60)
        a = self.claude()
        self.assertEqual((a["status"], a["why"], a["reading_valid"], a.get("stale")), ("stale", "stale_reading", False, True))
        self.assertNotIn("h5", a, "a stale 5-hour window is no reading")
        self.assertEqual(a["reminders"], [])
        self.assertIn("额度读数已过期", quota.render({"harnesses": [a]}, "zh"))
        self.assertIn("usage reading expired", quota.render({"harnesses": [a]}, "en"))
        self.assertEqual(quota.pick({"harnesses": [a]})["harness"], None)

    def test_idle_or_recent_reading_stays_valid(self):
        self.reading(age=3 * 3600, active_ago=3 * 3600 + 60)       # nobody used Claude since: just old, not expired
        self.assertEqual((self.claude()["status"], self.claude()["reading_valid"]), ("ok", True))
        self.reading(age=3600, active_ago=60)                       # under the two-hour threshold
        self.assertEqual((self.claude()["status"], self.claude()["reading_valid"]), ("ok", True))

    def test_phone_meter_says_expired(self):
        tap.set_enabled(True, self.st.root, str(self.work))
        t = self.reading(age=3 * 3600, active_ago=60)
        fresh = tap.read(self.st.root, SID)
        self.assertNotIn("stale", fresh["week"])
        m = tap.read(self.st.root, SID, None, t.stat().st_mtime)
        self.assertEqual((m["week"]["pct"], m["week"]["stale"], m["week"]["expired"]), (37, True, True))
        self.assertTrue(m["h5"]["expired"])
        # the shared session passes its own transcript's last write
        agent = SimpleNamespace(host=SimpleNamespace(st=self.st), session={"sessionId": SID}, compacted_at=None, transcript=t)
        self.assertTrue(shared.SharedClaudeAgent._read_native(agent)["week"]["expired"])
        agent.transcript = None
        self.assertNotIn("expired", shared.SharedClaudeAgent._read_native(agent)["week"])


if __name__ == "__main__":
    unittest.main()
