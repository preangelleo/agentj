"""F14 (P45b): the remaining self-imposed locks are gone; the data-safety floor stays. All I/O hermetic (temporary HOME and
state, the CLI on a pipe — never a terminal)."""
import _hermetic  # noqa: F401
import io
import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agentj import controls, fence, preferences, serve, service, telegram
from agentj.state import State

HERE = pathlib.Path(__file__).resolve().parent
AGENTJ = str(HERE.parent / "bin" / "agentj")


class Fence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-p45c-", dir="/var/tmp")
        self.addCleanup(self.tmp.cleanup)
        d = pathlib.Path(self.tmp.name)
        self.home, self.rt = d / "home", d / "run"
        for folder in (".config/autostart", ".config/systemd/user", ".ssh"):
            (self.home / folder).mkdir(parents=True)
        for f in (".bashrc", ".zshrc"):
            (self.home / f).write_text("")
        (self.home / ".ssh/authorized_keys").write_text("")
        (self.rt / "systemd").mkdir(parents=True)
        (self.rt / "bus").write_text("")
        (self.rt / "keyring").mkdir()
        self.st = State(d / "state")
        self.st.init()

    def argv(self, **kw):
        env = {"DBUS_SESSION_BUS_ADDRESS": f"unix:path={self.rt}/bus", "SSH_AUTH_SOCK": f"{self.rt}/keyring/ssh",
               "XDG_RUNTIME_DIR": str(self.rt)}
        return fence.bwrap_argv(self.st, str(self.home), home=str(self.home), runtime=str(self.rt), environ=env, **kw)

    def test_general_config_writable_credentials_read_only(self):
        a = self.argv()
        ro = {a[i + 1] for i, x in enumerate(a) if x == "--ro-bind" and a[i + 1] != "/dev/null"}
        for rel in (".bashrc", ".zshrc", ".config/autostart", ".config/systemd"):
            self.assertNotIn(os.path.realpath(self.home / rel), ro, f"F14: {rel} is the owner's general config")
        self.assertIn(os.path.realpath(self.home / ".ssh/authorized_keys"), ro, "who may log in: read-only")

    def test_service_manager_and_bus_come_back_keyring_stays_hidden(self):
        a = self.argv()
        rt = os.path.realpath(self.rt)
        self.assertIn(rt, [a[i + 1] for i, x in enumerate(a) if x == "--tmpfs"], "runtime dir still private")
        binds = [a[i + 1] for i, x in enumerate(a) if x == "--bind"]
        self.assertIn(os.path.join(rt, "systemd"), binds)
        self.assertIn(os.path.join(rt, "bus"), binds)
        self.assertNotIn(os.path.join(rt, "keyring"), binds, "credential sockets stay hidden")
        unset = a[a.index("--unsetenv"):]
        self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", unset)
        for k in ("SSH_AUTH_SOCK", "XAUTHORITY", "AGENTJ_STATE_DIR", "GNOME_KEYRING_CONTROL"):
            self.assertIn(k, unset)

    def test_state_and_preferences_still_hidden(self):
        a = self.argv()
        tmpfs = [a[i + 1] for i, x in enumerate(a) if x == "--tmpfs"]
        self.assertIn(os.path.realpath(self.st.root), tmpfs)
        perm = os.path.realpath(self.st.perm_dir)
        self.assertEqual(a[a.index(os.path.realpath(self.st.root)) + 1:a.index(os.path.realpath(self.st.root)) + 4],
                         ["--bind", perm, perm], "only the permission folder comes back")

    def test_docker_stays_off_unless_allowed(self):
        self.assertIn("DOCKER_HOST", self.argv())
        self.assertNotIn("DOCKER_HOST", self.argv(allow_docker=True))


class ServiceOverBus(unittest.TestCase):
    """Inside the fence's PID namespace systemctl refuses the manager's private socket (peer PID 0 → ENODATA); agentj's
    own service verbs then go over the user bus."""
    def test_fallback_maps_the_verbs(self):
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)
            if argv[0].endswith("systemctl"):
                return subprocess.CompletedProcess(argv, 1, "", "Failed to connect to user scope bus via local transport: No data available")
            out = {"get-property": 's "active"', "call": 's "enabled"'}.get(argv[2], "")
            return subprocess.CompletedProcess(argv, 0, out + "\n", "")
        with patch.object(service.shutil, "which", side_effect=lambda x: "/usr/bin/" + x), \
                patch.object(service.subprocess, "run", side_effect=fake_run):
            self.assertEqual(service._systemctl("restart", "agentj.service", check=True).returncode, 0)
            self.assertEqual(calls[-1][1:3], ["--user", "call"])
            self.assertEqual(calls[-1][-4:], ["RestartUnit", "ss", "agentj.service", "replace"])
            self.assertEqual(service._systemctl("is-active", "agentj.service").stdout.strip(), "active")
            self.assertEqual(calls[-1][4], "/org/freedesktop/systemd1/unit/agentj_2eservice")
            self.assertEqual(service._systemctl("is-enabled", "agentj.service").returncode, 0)
            service._systemctl("enable", "agentj.service", check=True)
            self.assertEqual(calls[-1][-6:], ["EnableUnitFiles", "asbb", "1", "agentj.service", "false", "true"])
            service._systemctl("daemon-reload", check=True)
            self.assertEqual(calls[-1][-1], "Reload")

    def test_other_failures_are_not_rerouted(self):
        def fake_run(argv, **kw):
            return subprocess.CompletedProcess(argv, 1, "", "Unit agentj.service not found.")
        with patch.object(service.shutil, "which", side_effect=lambda x: "/usr/bin/" + x), \
                patch.object(service.subprocess, "run", side_effect=fake_run) as run:
            with self.assertRaises(service.ServiceError):
                service._systemctl("restart", "agentj.service", check=True)
            self.assertEqual(run.call_count, 1)


class Cli(unittest.TestCase):
    """Every command on a pipe, the way an Agent runs it."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-p45c-cli-", dir="/var/tmp")
        self.addCleanup(self.tmp.cleanup)
        d = self.d = pathlib.Path(self.tmp.name)
        self.work = d / "work"
        self.work.mkdir()
        self.env = {**os.environ, "HOME": str(d / "home"), "XDG_CONFIG_HOME": str(d / "home/.config"),
                    "AGENTJ_STATE_DIR": str(d / "state"), "CLAUDE_CONFIG_DIR": str(d / "cfg")}
        self.run_("init", "--relay", "ws://127.0.0.1:1")

    def run_(self, *a, inp="", ok=True):
        r = subprocess.run([AGENTJ, *a], env=self.env, input=inp, capture_output=True, text=True, timeout=60)
        if ok:
            self.assertEqual(r.returncode, 0, (a, r.stdout, r.stderr))
        return r

    def test_unfenced_and_docker_need_no_passphrase(self):
        self.run_("passphrase", "set", inp="pass-phrase-1\npass-phrase-1\n")
        self.run_("agent", "claude", "--dir", str(self.work), "--allow-docker")
        st = State(self.d / "state")
        self.assertEqual((st.agent_config()["fence"], st.agent_config()["docker"]), (True, True))
        self.run_("agent", "claude", "--dir", str(self.work), "--unfenced")
        self.assertFalse(State(self.d / "state").agent_config()["fence"])

    def test_isolation_and_docker_preferences(self):
        self.run_("agent", "claude", "--dir", str(self.work))
        self.run_("config", "set", "agent.isolation", "false")
        self.run_("config", "set", "agent.allow_docker", "true")
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": self.env["XDG_CONFIG_HOME"]}):
            c = State(self.d / "state").agent_config()
            self.assertEqual((c["fence"], c["docker"]), (False, True))
            self.run_("config", "unset", "agent.isolation")
            self.assertTrue(State(self.d / "state").agent_config()["fence"])

    def test_reset_all_with_yes_on_a_pipe(self):
        self.run_("config", "set", "appearance.theme", "dark")
        r = self.run_("config", "reset", "--all", ok=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("--yes", r.stdout)
        self.assertNotIn('"needs": ["human"]', r.stdout)
        self.run_("config", "reset", "--all", "--yes")
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": self.env["XDG_CONFIG_HOME"]}):
            self.assertIsNone(preferences.get(preferences.read()[1], "appearance.theme"))

    def test_human_tier_keeps_only_devices_and_account(self):
        human = sorted(k for k, m in preferences.SCHEMA.items() if m["tier"] == "human")
        self.assertEqual(human, ["human.devices", "human.relay", "human.remote_unbind", "human.web"])
        for k in ("human.danger_extra", "human.report_hostname", "human.fence"):
            self.run_("config", "set", k, "x")
        r = self.run_("config", "set", "human.devices", "x", ok=False)
        self.assertEqual(r.returncode, 2)

    def test_tasks_enable_without_passphrase(self):
        self.run_("passphrase", "set", inp="pass-phrase-1\npass-phrase-1\n")
        self.run_("agent", "claude", "--dir", str(self.work))
        t = self.work / "workflows" / "daily"
        t.mkdir(parents=True)
        (t / "RUN.md").write_text("SAY: VERDICT: ok — 好\n")
        (t / "DRYRUN.md").write_text("dry\n")
        (t / "task.json").write_text(json.dumps({"v": 1, "id": "daily", "title": {"zh": "日报", "en": "Daily"},
                                                 "schedule": "0 8 * * *", "tz": "local", "prompt_file": "RUN.md",
                                                 "dry_run_prompt_file": "DRYRUN.md", "mode": "research", "enabled": False,
                                                 "needs": ["x"], "outputs": ["reports/x.md"]}))
        r = self.run_("tasks", "enable", "daily", ok=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("✓ 已启用", r.stdout)

    def test_the_agent_cannot_lift_the_owners_stop(self):
        st = State(self.d / "state")
        self.run_("stop")
        r = self.run_("resume", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("已配对手机上点「恢复」", r.stderr)
        self.assertTrue(controls.estop_state(st)["on"], "a pipe without the passphrase does not resume")
        self.run_("passphrase", "set", inp="pass-phrase-1\npass-phrase-1\n")
        self.assertNotEqual(self.run_("resume", inp="wrong-pass\n", ok=False).returncode, 0)
        self.assertTrue(controls.estop_state(st)["on"])
        self.run_("resume", inp="pass-phrase-1\n")
        self.assertFalse(controls.estop_state(st)["on"])

    def test_docs_rule_and_skill_install_on_a_pipe(self):
        self.run_("docs-rule", "--write", "--harness", "claude", "--lang", "zh")
        self.assertIn("自己搜一下广场", (self.d / "home/.claude/CLAUDE.md").read_text())
        r = self.run_("skill", "install")
        self.assertTrue(json.loads(r.stdout)["ok"])
        self.assertTrue((self.d / "home/.claude/skills/agentj-config").is_symlink())


class Telegram(unittest.TestCase):
    def test_enroll_without_a_terminal_only_the_owner_id(self):
        with tempfile.TemporaryDirectory(prefix="aj-p45c-tg-", dir="/var/tmp") as d, \
                patch.dict(os.environ, {"AGENTJ_STATE_DIR": d + "/state", "AJ_TEST_BOT": "x"}), \
                patch("os.isatty", return_value=False), patch("builtins.input", side_effect=AssertionError("no question")):
            State().init()
            self.assertFalse(telegram.enroll(0, "AJ_TEST_BOT")["ok"])
            self.assertFalse(telegram.enroll("123", "AJ_TEST_BOT")["ok"])
            self.assertFalse(telegram.enroll(123, "AJ_ABSENT_BOT")["ok"])
            r = telegram.enroll(123, "AJ_TEST_BOT")
            self.assertTrue(r["ok"])
            self.assertIn("not end-to-end encrypted", r["notice"])
            cfg = telegram.configuration(State())
            self.assertEqual((cfg["owner_id"], cfg["key_env"]), (123, "AJ_TEST_BOT"))
            self.assertNotIn("x", json.dumps({k: v for k, v in cfg.items() if k != "generation"}).replace("AJ_TEST_BOT", ""))


class Phone(unittest.TestCase):
    def test_phone_may_switch_isolation_and_docker(self):
        for k in ("agent.isolation", "agent.allow_docker", "agent.session_mode", "agent.high_risk_warnings"):
            self.assertIn(k, serve.PREF_SET_KEYS)
            self.assertEqual(preferences.SCHEMA[k]["tier"], "user")
        for k in serve.PREF_SET_KEYS:
            self.assertFalse(k.startswith(("human.", "security.")), k)

    def test_phone_resume_is_a_signed_control_without_passphrase(self):
        self.assertIn("resume", serve.PHONE_CONTROLS)
        import inspect
        src = inspect.getsource(serve.Host.do_resume)
        self.assertNotIn("gate.", src)


if __name__ == "__main__":
    unittest.main()
