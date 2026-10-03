"""L3-min: installable package, install-location-aware code paths, `agentj doctor`, `agentj service`, serve without a TTY.

- Version: one source (`agentj.__version__`) → `agentj --version`, the report agent string, pyproject (dynamic).
- Service: the systemd unit / launchd plist text (absolute paths, no secret even when one is in the environment, refused
  names); a REAL `systemctl --user` install under a throwaway name (`agentjarvis-test-<rand>`, temp state dir) on Linux:
  active, serve answers its control socket without any terminal, uninstall leaves nothing behind.
- serve with stdin = /dev/null (EOF at once) keeps running; `--events quiet` never prints message / reply / command text.
- Doctor: the check list and order, ✗ only for blockers, no secret / home path in --json, the relay probe on a local relay.
- Wheel: build the wheel, install it into a fresh venv (Python 3.11 when available — the floor we claim), and run from
  there: `agentj --version`, the code paths the fence protects, the permission tool's interpreter, the admin assets, the
  service ExecStart, and the fenced hostile-command chain (test_l2.FencedChain) against the *installed* package.
"""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import contextlib
import io
import json
import os
import pathlib
import plistlib
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
HOST = HERE.parent
sys.path.insert(0, str(HOST))
sys.path.insert(0, str(HERE))
import agentj  # noqa: E402
from agentj import agent, cloud, doctor, fence, names, serve, service  # noqa: E402
from agentj.state import State  # noqa: E402

SECRET = "sk-" + "ant-oat01-" + "AJL3MARKER" + "x" * 20   # fake token shape, assembled so scanners do not flag the source


def _cli(*args, env=None, timeout=60, stdin=subprocess.DEVNULL):
    return subprocess.run([sys.executable, "-m", "agentj.cli", *args], cwd=HOST, env={**os.environ, **(env or {})},
                          capture_output=True, text=True, timeout=timeout, stdin=stdin)


class Version(unittest.TestCase):
    def test_one_source(self):
        v = agentj.__version__
        self.assertEqual(v, "0.11.0a1")
        self.assertEqual(cloud.VERSION, v)
        self.assertEqual(cloud.AGENT, f"agentj/{v}")
        r = _cli("--version")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, f"agentj {v}"))
        py = (HOST / "pyproject.toml").read_text()
        self.assertIn('dynamic = ["version"]', py)
        self.assertNotIn("\nversion =", py)
        self.assertIn('agentj = "agentj.cli:main"', py)
        self.assertNotIn("\njarvis =", py, "no `jarvis` console script: fresh installs never get that name")

    def test_no_args_prints_the_next_step(self):
        with tempfile.TemporaryDirectory() as d:
            r = _cli(env={"AGENTJ_STATE_DIR": d + "/s"})
            self.assertEqual(r.returncode, 0, r.stderr)
            for step in ("agentj init", "agentj login", "agentj passphrase set", "agentj agent claude", "agentj service install",
                         "agentj pair"):
                self.assertIn(step, r.stdout)
            self.assertIn("next:  agentj init", r.stdout)
            State(pathlib.Path(d) / "s").init(relay="ws://127.0.0.1:1")
            r = _cli(env={"AGENTJ_STATE_DIR": d + "/s"})
            self.assertIn("next:  agentj login", r.stdout)

    def test_new_help_is_bilingual(self):
        for cmd in (["doctor", "--help"], ["service", "--help"]):
            out = _cli(*cmd).stdout
            self.assertRegex(out, r"[一-鿿]")
            self.assertRegex(out, r"[A-Za-z]{4,} [a-z]{2,}")


class ServiceText(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {"PATH": "/opt/x/bin:relative/bin:/usr/bin", "HOME": self.tmp.name, "XDG_CONFIG_HOME": self.tmp.name + "/cfg",
                    "CLAUDE_CODE_OAUTH_TOKEN": SECRET, "ANTHROPIC_API_KEY": SECRET, "AGENTJ_STATE_DIR": self.tmp.name + "/st",
                    "HTTPS_PROXY": f"http://user:{SECRET}@proxy:8080", "NO_PROXY": "localhost"}

    def tearDown(self):
        self.tmp.cleanup()

    def test_systemd_unit(self):
        env, notes = service.service_env(self.env)
        self.assertEqual(env["PATH"], "/opt/x/bin:/usr/bin", "relative PATH entries are dropped")
        self.assertNotIn("HTTPS_PROXY", env, "a proxy with credentials is not copied")
        self.assertEqual(env["NO_PROXY"], "localhost")
        self.assertTrue(any("HTTPS_PROXY" in n for n in notes))
        argv = ["/opt/aj venv/bin/agentj"]
        text = service.unit_text("agentj", argv, env, self.env)
        self.assertNotIn(SECRET, text)
        self.assertNotIn("OAUTH_TOKEN=", text.replace("e.g. CLAUDE_CODE_OAUTH_TOKEN=…", ""))
        self.assertIn('ExecStart="/opt/aj venv/bin/agentj" "serve" "--events" "quiet" "--no-stdin"', text)
        for line in ("Restart=on-failure", "RestartSec=5", "WantedBy=default.target", "StandardInput=null",
                     'Environment="PATH=/opt/x/bin:/usr/bin"', f'Environment="AGENTJ_STATE_DIR={self.tmp.name}/st"',
                     f"EnvironmentFile=-{self.tmp.name}/cfg/systemd/user/agentj.env"):
            self.assertIn(line, text.splitlines())
        for ln in text.splitlines():
            if ln.startswith(("ExecStart=", "EnvironmentFile=")):
                self.assertTrue(ln.split("=", 1)[1].lstrip('-"').startswith("/"), ln)
        self.assertEqual(service._sd_quote('a%b"c'), '"a%%b\\"c"')

    def test_launchd_plist(self):
        env, _ = service.service_env(self.env)
        b = service.plist_bytes("net.agentj.host", ["/opt/v/bin/agentj"], env, "/st/service.log")
        self.assertNotIn(SECRET.encode(), b)
        p = plistlib.loads(b)
        self.assertEqual(p["Label"], "net.agentj.host")
        self.assertEqual(p["ProgramArguments"], ["/opt/v/bin/agentj", "serve", "--events", "quiet", "--no-stdin"])
        self.assertTrue(p["RunAtLoad"] and p["KeepAlive"])
        self.assertEqual((p["StandardOutPath"], p["StandardErrorPath"], p["StandardInPath"]),
                         ("/st/service.log", "/st/service.log", "/dev/null"))
        self.assertEqual(set(p["EnvironmentVariables"]), {"PATH", "AGENTJ_STATE_DIR", "NO_PROXY"})

    def test_names(self):
        for bad in ("vibe-remote-bridge", "Vibe-Remote", "../x", "a b", "x.service", ""):
            with mock.patch.dict(os.environ, {"AGENTJ_SERVICE_NAME": bad}):
                if bad == "":
                    self.assertIn(service.name(), (service.DEFAULT_UNIT, service.DEFAULT_LABEL))
                    continue
                with self.assertRaises(service.ServiceError):
                    service.name()
        with mock.patch.dict(os.environ, {"AGENTJ_SERVICE_NAME": "agentjarvis-test-1"}):
            self.assertEqual(service.name(), "agentjarvis-test-1")

    def test_execstart_is_the_venv_agentj(self):
        argv = service.agentj_argv()
        self.assertTrue(os.path.isabs(argv[0]))
        self.assertTrue(argv[0].startswith(os.path.dirname(os.path.abspath(sys.executable))) or argv[0] == os.path.abspath(sys.executable))

    def test_token_hint_names_the_variable_never_the_value(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": SECRET}):
            h = service.token_hint("agentj")
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN", h)
        self.assertNotIn(SECRET, h)


def _user_systemd() -> bool:
    if not sys.platform.startswith("linux") or not shutil.which("systemctl") or os.environ.get("AJ_SKIP_SYSTEMD"):
        return False
    try:
        return subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@unittest.skipUnless(_user_systemd(), "no systemd user manager here")
class RealSystemdService(unittest.TestCase):
    """A real `systemctl --user` install under a throwaway unit name + temp state dir; always removed afterwards."""

    def setUp(self):
        self.name = f"agentjarvis-test-{secrets.token_hex(4)}"
        self.dir = tempfile.mkdtemp(prefix="aj-svc-", dir="/tmp")
        self.st = State(pathlib.Path(self.dir) / "s")
        self.st.init(relay="ws://127.0.0.1:1")         # a relay that refuses: serve keeps retrying, stays up
        self.env = {"AGENTJ_STATE_DIR": str(self.st.root), "AGENTJ_SERVICE_NAME": self.name,
                    "CLAUDE_CODE_OAUTH_TOKEN": SECRET}
        self.unit = pathlib.Path(service.unit_dir()) / f"{self.name}.service"

    def tearDown(self):
        subprocess.run(["systemctl", "--user", "disable", "--now", f"{self.name}.service"], capture_output=True, timeout=30)
        with contextlib.suppress(FileNotFoundError):
            self.unit.unlink()
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, timeout=30)
        subprocess.run(["systemctl", "--user", "reset-failed", f"{self.name}.service"], capture_output=True, timeout=30)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_install_active_status_uninstall(self):
        self.assertFalse(self.unit.exists())
        r = _cli("service", "install", env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertNotIn(SECRET, r.stdout + r.stderr)
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN", r.stdout, "the human is told the service cannot see the variable")
        self.assertIn("`agentj doctor`", r.stdout.splitlines()[-1], "the last line: run doctor next, report every !")
        text = self.unit.read_text()
        self.assertNotIn(SECRET, text)
        self.assertIn(f'Environment="AGENTJ_STATE_DIR={self.st.root}"', text)
        exe = text.split("ExecStart=", 1)[1].split('"')[1]
        self.assertTrue(os.path.isabs(exe) and os.access(exe, os.X_OK), exe)
        # active, and serve answers its control socket — no terminal, stdin = /dev/null
        deadline = time.time() + 20
        res = None
        while time.time() < deadline:
            with contextlib.suppress(names.ServeBusy):
                res = names.ctl_call(self.st, {"cmd": "status"}, 3)
            if res is not None:
                break
            time.sleep(0.2)
        self.assertIsNotNone(res, subprocess.run(["journalctl", "--user", "-u", self.name, "-n", "30", "--no-pager"],
                                                 capture_output=True, text=True).stdout)
        s = json.loads(_cli("service", "status", "--json", env=self.env).stdout)
        self.assertEqual((s["name"], s["installed"], s["active"], s["enabled"]), (self.name, True, "active", "enabled"))
        d = json.loads(_cli("doctor", "--json", "--offline", env=self.env).stdout)
        by = {c["id"]: c for c in d["checks"]}
        self.assertEqual((by["serve"]["status"], by["service"]["status"]), ("warn", "ok"))   # serve up, relay refused
        self.assertNotIn(SECRET, json.dumps(d))
        # a second install while active is fine (idempotent); a terminal serve would have been refused
        self.assertEqual(_cli("service", "install", env=self.env).returncode, 0)
        r = _cli("service", "uninstall", env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.unit.exists())
        act = subprocess.run(["systemctl", "--user", "is-active", f"{self.name}.service"], capture_output=True, text=True).stdout.strip()
        self.assertNotEqual(act, "active")
        deadline = time.time() + 10
        while self.st.sock_path.exists() and time.time() < deadline:
            time.sleep(0.1)
        self.assertFalse(self.st.sock_path.exists(), "serve stopped cleanly (SIGTERM → control socket removed)")
        s = json.loads(_cli("service", "status", "--json", env=self.env).stdout)
        self.assertFalse(s["installed"])

    @unittest.skipUnless(shutil.which("bwrap") and shutil.which("systemd-run"), "needs bubblewrap + systemd-run")
    def test_the_fence_starts_inside_a_user_service(self):
        """Same context as the installed service (user manager, no TTY): the fence probe and a fenced command both work."""
        work = pathlib.Path(self.dir) / "work"
        work.mkdir()
        code = ("import pathlib, subprocess, sys\nfrom agentj import fence\nfrom agentj.state import State\n"
                f"st = State(pathlib.Path({str(self.st.root)!r})); w = {str(work)!r}\n"
                "why = fence.problem(st, w)\n"
                "r = subprocess.run(fence.wrap(st, ['/bin/sh', '-c', 'ls ' + str(st.root)], w), capture_output=True, text=True)\n"
                "print(why, sorted(r.stdout.split()), r.returncode)\n")
        r = subprocess.run(["systemd-run", "--user", "--quiet", "--wait", "--pipe", "--collect", f"--unit={self.name}-probe",
                            f"--setenv=PYTHONPATH={HOST}", "--property=StandardInput=null", sys.executable, "-P", "-c", code],
                           capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "None ['agentperm'] 0", "fence up; the state dir shows only agentperm/")

    def test_install_refuses_while_a_terminal_serve_runs(self):
        p = subprocess.Popen([sys.executable, "-m", "agentj.cli", "serve", "--events", "jsonl", "--no-stdin"], cwd=HOST,
                             env={**os.environ, **self.env}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.time() + 15
            while not self.st.sock_path.exists() and time.time() < deadline:
                time.sleep(0.05)
            r = _cli("service", "install", env=self.env)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("stop it first", r.stderr)
            self.assertFalse(self.unit.exists())
        finally:
            p.send_signal(signal.SIGTERM)
            p.wait(15)


class ServeWithoutTerminal(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="aj-tty-", dir="/tmp")
        self.st = State(pathlib.Path(self.dir) / "s")
        self.st.init(relay="ws://127.0.0.1:1")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _run(self, *extra):
        p = subprocess.Popen([sys.executable, "-m", "agentj.cli", "serve", *extra], cwd=HOST,
                             env={**os.environ, "AGENTJ_STATE_DIR": str(self.st.root)}, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.time() + 15
            while not self.st.sock_path.exists() and time.time() < deadline:
                self.assertIsNone(p.poll(), p.stderr.read() if p.poll() is not None else "")
                time.sleep(0.05)
            time.sleep(2)                                # stdin hit EOF long ago
            self.assertIsNone(p.poll(), "serve keeps running after stdin EOF")
            self.assertIsNotNone(names.ctl_call(self.st, {"cmd": "status"}, 5))
        finally:
            p.send_signal(signal.SIGTERM)
            out, err = p.communicate(timeout=15)
        self.assertEqual(p.returncode, 0, err)
        return out

    def test_stdin_eof_does_not_stop_serve(self):
        self._run()                                       # text mode, reading stdin (= /dev/null)

    def test_quiet_mode(self):
        out = self._run("--events", "quiet")
        self.assertIn("service mode", out)

    def test_quiet_never_prints_text(self):
        host = serve.Host(self.st, events="quiet", read_stdin=False)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            host.emit("msg", device="d", name="手机", text="AJ-L3-明文-msg")
            host.emit("agent_msg", text="AJ-L3-明文-reply")
            host.emit("agent_notice", text="AJ-L3-明文-notice")
            host.emit("ask", id="a" * 32, tool="Bash", summary="AJ-L3-明文-cmd")
            host.emit("relay", up=True)
            host.emit("agent_status", s="idle")
        out = buf.getvalue()
        self.assertNotIn("AJ-L3-明文", out)
        self.assertIn("中继已连接", out)
        self.assertIn("Bash", out)


class Doctor(unittest.TestCase):
    IDS = ["version", "python", "platform", "state", "relay", "dashboard", "agent", "agent_cli", "harness", "fence", "danger", "passphrase",
           "bound", "serve", "service", "alias", "estop", "tasks", "activity"]

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="aj-doc-", dir="/tmp")
        self.env = {"AGENTJ_STATE_DIR": self.dir + "/s", "CLAUDE_CODE_OAUTH_TOKEN": SECRET,
                    "AGENTJ_SERVICE_NAME": f"agentjarvis-test-{secrets.token_hex(4)}"}

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_checks_and_exit_code(self):
        r = _cli("doctor", "--json", "--offline", env=self.env)
        self.assertEqual(r.returncode, 1, "not initialised = ✗")
        d = json.loads(r.stdout)
        ids = [c["id"] for c in d["checks"]]
        self.assertEqual([i for i in ids if i != "linger"], self.IDS + ["asr", "update"], "linger only where systemd reports it")
        self.assertEqual(d["version"], agentj.__version__)
        by = {c["id"]: c for c in d["checks"]}
        self.assertEqual((by["state"]["status"], by["state"]["hint"]), ("fail", "agentj init"))
        self.assertEqual(_cli("init", env=self.env).returncode, 0)
        r = _cli("doctor", "--json", "--offline", env=self.env)
        d = json.loads(r.stdout)
        self.assertEqual(r.returncode, 0 if d["ok"] else 1)
        self.assertTrue(all(c["status"] in ("ok", "warn", "fail") for c in d["checks"]))
        text = r.stdout + _cli("doctor", "--offline", env=self.env).stdout
        self.assertNotIn(SECRET, text)
        home = os.path.expanduser("~")
        self.assertNotIn(home + "/", text, "paths under HOME are shown with ~")
        plain = _cli("doctor", "--offline", env=self.env).stdout
        self.assertRegex(plain, r"(?m)^[✓!✗] version")

    def test_inside_claude_code_hidden_token_is_a_warning_not_a_failure(self):
        # Claude Code hides CLAUDE_CODE_OAUTH_TOKEN from the commands it runs; doctor run by the agent must say so
        with tempfile.TemporaryDirectory() as d:
            st = State(pathlib.Path(d) / "s")
            st.init(relay="ws://127.0.0.1:1")
            fake = pathlib.Path(d) / "bin"; fake.mkdir()
            (fake / "claude").write_text("#!/bin/sh\necho '9.9.9 (Claude Code)'\n"); (fake / "claude").chmod(0o755)
            env = {"PATH": str(fake), "HOME": d, "CLAUDECODE": "1"}
            with mock.patch.dict(os.environ, env, clear=True):
                st.set_agent("claude", d) if hasattr(st, "set_agent") else None
                c = doctor.check_agent_cli(st, {})
            self.assertEqual(c["status"], "warn")
            self.assertIn("inside Claude Code", c["detail"] if "detail" in c else json.dumps(c))

    def test_fail_only_for_blockers(self):
        with tempfile.TemporaryDirectory() as d:
            st = State(pathlib.Path(d) / "s")
            st.init(relay="ws://127.0.0.1:1")
            checks = {c["id"]: c for c in doctor.run(st, offline=True)}
            self.assertEqual(checks["passphrase"]["status"], "warn")
            self.assertEqual(checks["bound"]["status"], "warn")
            self.assertEqual(checks["agent"]["status"], "warn")
            with mock.patch.object(sys, "platform", "win32"):
                self.assertEqual(doctor.check_platform()["status"], "fail")
                self.assertIn("WSL2", doctor.check_platform()["hint"])
            with mock.patch.object(sys, "platform", "darwin"), mock.patch.object(fence, "SANDBOX_EXEC", "/nonexistent/sandbox-exec"):
                c = doctor.check_fence(st)        # macOS (L3): sandbox-exec, not "--unfenced only"
                self.assertEqual(c["status"], "warn")
                self.assertIn("sandbox-exec", c["summary"])

    def test_relay_probe(self):
        from fakerelay import FakeRelay
        with FakeRelay() as relay:
            ok, why = doctor.probe_relay(f"ws://127.0.0.1:{relay.port}", timeout=3)
            self.assertTrue(ok, why)
            self.assertEqual(relay.hosts, {})
        ok, why = doctor.probe_relay("ws://127.0.0.1:1", timeout=2)
        self.assertFalse(ok)


class InstallAware(unittest.TestCase):
    def test_permission_tool_uses_this_interpreter_and_no_cwd_path(self):
        a = agent.ClaudeAgent.__new__(agent.ClaudeAgent)
        a.cfg = {"model": None}
        argv = a.argv(None)
        mcp = json.loads(argv[argv.index("--mcp-config") + 1])["mcpServers"]["agentj"]
        self.assertEqual(mcp["command"], sys.executable)
        self.assertEqual(mcp["args"], ["-P", "-m", "agentj.permtool"])

    def test_source_checkout_detected(self):
        self.assertEqual(agent.source_root(), os.path.realpath(HOST))
        self.assertIn(os.path.realpath(HOST), fence.code_paths())

    def test_code_under_tmp_is_bound_back_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            st = State(pathlib.Path(d) / "s")
            st.root.mkdir()
            st.perm_dir.mkdir()
            fake = d + "/venv"
            os.mkdir(fake)
            with mock.patch.object(fence, "code_paths", return_value=[fake]):
                a = fence.bwrap_argv(st, d)
            i = a.index("--tmpfs")
            self.assertEqual(a[i + 1], "/tmp")
            j = [k for k in range(len(a) - 2) if a[k] == "--ro-bind" and a[k + 1] == fake]
            self.assertTrue(j and j[0] > i, "the venv under /tmp is put back read-only on top of the private /tmp")


def _uv() -> str | None:
    return None if os.environ.get("AJ_SKIP_WHEEL") else shutil.which("uv")


@unittest.skipUnless(_uv(), "uv not available")
class WheelInstall(unittest.TestCase):
    """Build → install into a fresh venv → run from the installed location only (no checkout on sys.path)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="aj-whl-", dir="/tmp")
        uv = _uv()
        r = subprocess.run([uv, "build", "--wheel", "--out-dir", cls.tmp + "/dist", str(HOST)], capture_output=True,
                           text=True, timeout=300)
        assert r.returncode == 0, r.stderr
        cls.wheel = next(pathlib.Path(cls.tmp, "dist").glob("agentj-*.whl"))
        cls.py = "3.11" if subprocess.run([uv, "python", "find", "3.11"], capture_output=True).returncode == 0 else "3.13"
        venv = cls.tmp + "/venv"
        r = subprocess.run([uv, "venv", "-q", "-p", cls.py, venv], capture_output=True, text=True, timeout=300)
        assert r.returncode == 0, r.stderr
        r = subprocess.run([uv, "pip", "install", "-q", "--python", venv + "/bin/python", str(cls.wheel)], capture_output=True,
                           text=True, timeout=300)
        assert r.returncode == 0, r.stderr
        cls.venv = venv
        cls.env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "VIRTUAL_ENV")}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_wheel_contents(self):
        import zipfile
        names_ = zipfile.ZipFile(self.wheel).namelist()
        for f in ("agentj/__init__.py", "agentj/permtool.py", "agentj/admin/index.html",
                  "agentj/admin/app.js", "agentj/admin/app.css", "agentj/service.py", "agentj/doctor.py",
                  "agentj/admin/i18n/admin.zh.json", "agentj/admin/i18n/admin.en.json", "agentj/admin/favicon.ico",
                  "agentj/admin/brand/lang.js", "agentj/admin/brand/palette.css", "agentj/admin/brand/base.css",
                  "agentj/admin/brand/fonts/ubuntu-400.woff2", "agentj/admin/brand/fonts/UFL-1.0-ubuntu.txt",
                  "agentj/admin/brand/img/shield-64.png"):
            self.assertIn(f, names_)
        self.assertFalse([n for n in names_ if n.endswith(".src.json") or "shield-source" in n], "no copy source, no big logo source")
        self.assertLess(pathlib.Path(self.wheel).stat().st_size, 1_500_000, "wheel stays small")
        self.assertFalse([n for n in names_ if n.startswith("tests/") or "wiredump" in n or "fakeclaude" in n])
        ep = next(n for n in names_ if n.endswith("entry_points.txt"))
        eps = zipfile.ZipFile(self.wheel).read(ep).decode()
        self.assertEqual([x.strip() for x in eps.splitlines() if "=" in x], ["agentj = agentj.cli:main"],
                         "one console script: `agentj` (no `jarvis` — only migrated computers get a symlink)")
        for f in ("jarvis_host/__init__.py", "jarvis_host/cli.py"):
            self.assertIn(f, names_, "the 0.9 compatibility shim ships")

    def test_the_old_module_still_runs_and_no_jarvis_script(self):
        self.assertFalse(os.path.lexists(self.venv + "/bin/jarvis"))
        with tempfile.TemporaryDirectory() as d:      # what alias.py makes on a migrated computer: a symlink named jarvis
            os.symlink(self.venv + "/bin/agentj", d + "/jarvis")
            r = subprocess.run([d + "/jarvis", "--version"], capture_output=True, text=True, env=self.env, cwd="/")
            self.assertEqual(r.stdout.strip(), f"agentj {agentj.__version__}", r.stderr)
            self.assertIn("`jarvis` is now `agentj`", r.stderr)
        r = subprocess.run([self.venv + "/bin/python", "-P", "-m", "jarvis_host.cli", "--version"], capture_output=True, text=True,
                           env=self.env, cwd="/")
        self.assertEqual(r.stdout.strip(), f"agentj {agentj.__version__}", r.stderr)

    def test_runs_from_the_installed_location(self):
        r = subprocess.run([self.venv + "/bin/agentj", "--version"], capture_output=True, text=True, env=self.env, cwd="/")
        self.assertEqual(r.stdout.strip(), f"agentj {agentj.__version__}", r.stderr)
        probe = (
            "import json, os, sys, agentj\n"
            "from agentj import admin, agent, fence, service\n"
            "a = agent.ClaudeAgent.__new__(agent.ClaudeAgent); a.cfg = {'model': None}\n"
            "argv = a.argv(None); mcp = json.loads(argv[argv.index('--mcp-config') + 1])['mcpServers']['agentj']\n"
            "print(json.dumps({'pkg': agentj.__file__, 'prefix': os.path.realpath(sys.prefix), 'code': fence.code_paths(),"
            " 'src': agent.source_root(), 'assets': sorted(os.listdir(admin.ASSET_DIR)), 'exec': service.agentj_argv(),"
            " 'mcp': mcp, 'py': sys.executable, 'ver': list(sys.version_info[:2])}))\n")
        r = subprocess.run([self.venv + "/bin/python", "-P", "-c", probe], capture_output=True, text=True, env=self.env, cwd="/")
        self.assertEqual(r.returncode, 0, r.stderr)
        p = json.loads(r.stdout)
        self.assertTrue(p["pkg"].startswith(p["prefix"] + "/lib/"), p["pkg"])
        self.assertNotIn(str(HOST), r.stdout, "nothing points back into the checkout")
        self.assertIn(p["prefix"], p["code"], "the installed venv is what the fence keeps read-only")
        self.assertIsNone(p["src"], "installed: no PYTHONPATH for the agent")
        for f in ("app.css", "app.js", "index.html", "favicon.ico", "apple-touch-icon.png", "brand", "i18n"):
            self.assertIn(f, p["assets"])
        self.assertEqual([os.path.realpath(x) for x in p["exec"]], [os.path.realpath(self.venv + "/bin/agentj")])  # /tmp → /private/tmp on macOS
        self.assertEqual(p["mcp"], {"type": "stdio", "command": p["py"], "args": ["-P", "-m", "agentj.permtool"]})
        self.assertEqual(p["ver"], [3, int(self.py.split(".")[1])], "the venv runs the Python we claim as the floor")

    def test_doctor_and_init_from_the_wheel(self):
        with tempfile.TemporaryDirectory() as d:
            env = {**self.env, "AGENTJ_STATE_DIR": d + "/s", "AGENTJ_SERVICE_NAME": f"agentjarvis-test-{secrets.token_hex(4)}"}
            self.assertEqual(subprocess.run([self.venv + "/bin/agentj", "init"], env=env, capture_output=True, cwd="/").returncode, 0)
            r = subprocess.run([self.venv + "/bin/agentj", "doctor", "--json", "--offline"], env=env, capture_output=True, text=True, cwd="/")
            d_ = json.loads(r.stdout)
            self.assertEqual([c["id"] for c in d_["checks"] if c["id"] != "linger"], Doctor.IDS + ["asr", "update"])
            self.assertEqual({c["id"]: c["status"] for c in d_["checks"]}["state"], "ok")

    @unittest.skipUnless((sys.platform.startswith("linux") and shutil.which("bwrap")) or
                         (sys.platform == "darwin" and os.access(fence.SANDBOX_EXEC, os.X_OK)),
                         "fence = Linux + bubblewrap or macOS + sandbox-exec (L3)")
    def test_fenced_chain_against_the_installed_package(self):
        # inside the venv (read-only in the fence, so the stand-in claude can be read there); no agentj/ beside it
        t = pathlib.Path(self.venv, "share", "aj-tests")
        shutil.copytree(HERE, t / "tests", ignore=shutil.ignore_patterns("__pycache__"))
        r = subprocess.run([self.venv + "/bin/python", "-m", "unittest", "-v", "tests.test_l2.FencedChain", "tests.test_l2.Package",
                            "tests.test_l2.FenceConfig"],
                           cwd=t, env=self.env, capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        self.assertRegex(r.stderr, r"Ran [3-9] tests")
        self.assertNotIn("skipped", r.stderr, "the fenced chain really ran")
        self.assertNotIn("agentj" + os.sep, "".join(os.listdir(t)), "no package copy next to the tests")


if __name__ == "__main__":
    unittest.main()
