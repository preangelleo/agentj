"""L2 V2: trusted fence configuration and authorization contracts.

Historical attack execution is retained in git history and original red evidence, not replayed.
Synthetic discovery + actual generators cover state/permission mounts, PID and environment
configuration, socket filtering and launch refusal. Kernel enforcement requires separate
positive runtime qualification; these tests make no claim to have executed hostile actions.
Existing gate, signed decisions, package and decline tests retain their ordinary paths.
"""
import asyncio
import json
import os
import pathlib
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _hermetic  # noqa: E402,F401  (never the real ~/.local/state; see _hermetic.py)
import agentj  # noqa: E402
from agentj import cloud, fence, gate, serve, wire  # noqa: E402
from agentj.state import State  # noqa: E402

from test_l1 import Phone, _host, _ready  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
PKG = pathlib.Path(agentj.__file__).resolve().parent   # wherever it is imported from: checkout or an installed wheel (L3)
PASS = "correct horse battery"


def _state(d, passphrase=True) -> State:
    st = State(pathlib.Path(d) / "s")
    st.init(relay="ws://127.0.0.1:1")
    if passphrase:
        gate.set_passphrase(st, PASS)
    return st


class FencedChain(unittest.TestCase):
    """V2 configuration contract, not a replay of the historical hostile command.

    Runtime namespace evidence is a separate qualification obligation. F14 intentionally
    permits self-code/general shell configuration writes; it never promised code immutability.
    """
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)
        self.work = pathlib.Path(self.tmp.name) / "work"
        self.work.mkdir()
        self.st.perm_dir.mkdir(exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_an_approved_hostile_command_cannot_reach_jarvis(self):
        # Historical method ID retained; only trusted configuration is evaluated.
        root, perm = str(self.st.root.resolve()), str(self.st.perm_dir.resolve())
        code = str(PKG.resolve())
        env = {"HOME": self.tmp.name, "HERDR_PANE_ID": "synthetic", "AGENTJ_STATE_DIR": root}
        with mock.patch.object(fence, "control_sockets", return_value=[]), \
             mock.patch.object(fence, "other_states", return_value=[]), \
             mock.patch.object(fence, "service_mounts", return_value=[]):
            argv = fence.bwrap_argv(self.st, str(self.work), home=self.tmp.name, runtime="", environ=env)
        def verify(a):
            for flag in ("--unshare-pid", "--as-pid-1", "--die-with-parent", "--new-session"):
                self.assertIn(flag, a)
            self.assertIn(["--proc", "/proc"], [a[i:i+2] for i in range(len(a))])
            hidden = a.index(root, a.index("--tmpfs"))
            self.assertEqual(a[hidden-1], "--tmpfs")
            restore = a.index(perm)
            self.assertEqual(a[restore-1:restore+2], ["--bind", perm, perm])
            self.assertLess(hidden, restore, "state hiding precedes permission-only restoration")
            binds = [(a[i+1], a[i+2]) for i,x in enumerate(a) if x == "--bind"]
            self.assertEqual([x for x in binds if x[0].startswith(root + "/")], [(perm,perm)])
            self.assertTrue(any(code == a or code.startswith(a + "/") for a,b in binds if a == b), "F14 self-code remains writable")
            for name in ("HERDR_PANE_ID", "AGENTJ_STATE_DIR", "DBUS_SESSION_BUS_ADDRESS", "SSH_AUTH_SOCK"):
                self.assertIn(["--unsetenv",name], [a[i:i+2] for i in range(len(a))])
            self.assertEqual(a[-1], "--")
        verify(argv)
        # Negative controls mutate vectors only; no mutated vector is ever executed.
        for flag in ("--unshare-pid", "--as-pid-1", "--die-with-parent", "--new-session"):
            broken = argv.copy(); broken.remove(flag)
            with self.assertRaises(AssertionError):
                verify(broken)
        broken = argv.copy(); i = broken.index(root, broken.index("--tmpfs")); broken[i-1] = "--bind"
        with self.assertRaises(AssertionError):
            verify(broken)
        # The production one-use claim state machine is exercised with in-memory
        # streams and a synthetic token, never a real endpoint or process environment.
        host = _host(self.st, [])
        before = {p.name:p.read_bytes() for p in (self.st.config_path, self.st.devices_path, self.st.approver_path)}
        async def claim():
            reader = asyncio.StreamReader()
            reader.feed_data(b'{"t":"claim","token":"synthetic-claim"}\n'); reader.feed_eof()
            writer = mock.Mock(); writer.drain = mock.AsyncMock()
            await host.on_perm(reader, writer)
            return writer
        async def claims():
            host.perm_token = "synthetic-claim"
            first = await claim()
            self.assertIsNone(host.perm_token)
            self.assertTrue(host.perm_claimed.is_set())
            self.assertEqual(first.write.call_count, 1)
            second = await claim()
            second.write.assert_not_called()
            second.close.assert_called_once()
        asyncio.run(claims())
        log = self.st.log_path.read_text()
        self.assertEqual(log.count('"ev": "perm_claimed"'), 1)
        self.assertIn('"ev": "perm_refused", "reason": "spent"', log)
        for name, data in before.items():
            self.assertEqual((self.st.root/name).read_bytes(), data, name + " unchanged")
        # Actual launch policy: unchanged normal command routes through fence.wrap;
        # unavailable isolation still refuses launch. Nothing starts a subprocess.
        from agentj import agent as agents
        cfg = {"dir": str(self.work), "fence": True, "_workflow_ceo": True, "kind": "codex"}
        ag = agents.ClaudeAgent.__new__(agents.ClaudeAgent)
        ag.cfg, ag.host = cfg, _host(self.st, [])
        with mock.patch.object(fence, "problem", return_value=None), mock.patch.object(fence, "wrap", return_value=argv+["/usr/bin/true"]) as wrap:
            self.assertEqual(ag.launch_argv(["/usr/bin/true"]), argv+["/usr/bin/true"])
            wrap.assert_called_once_with(self.st, ["/usr/bin/true"], str(self.work), allow_docker=False)
        with mock.patch.object(fence, "problem", return_value="no_bwrap"), mock.patch.object(fence, "wrap") as wrap:
            self.assertIsNone(ag.launch_argv(["/usr/bin/true"]))
            wrap.assert_not_called()


class Gate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name, passphrase=False)

    def tearDown(self):
        self.tmp.cleanup()

    def test_set_verify_store_only_a_hash(self):
        with self.assertRaises(gate.GateError) as e:
            gate.verify(self.st, PASS)
        self.assertEqual(e.exception.reason, "not_set")
        with self.assertRaises(gate.GateError) as e:
            gate.set_passphrase(self.st, "short")
        self.assertEqual(e.exception.reason, "too_short")
        gate.set_passphrase(self.st, PASS)
        self.assertEqual(stat.S_IMODE(self.st.approver_path.stat().st_mode), 0o600)
        raw = self.st.approver_path.read_text()
        self.assertNotIn(PASS, raw)
        self.assertEqual(json.loads(raw)["kdf"], "scrypt")
        self.assertEqual(gate.verify(self.st, PASS), gate.FREE_TRIES)
        with self.assertRaises(gate.GateError) as e:
            gate.set_passphrase(self.st, "another passphrase")
        self.assertEqual(e.exception.reason, "exists", "a second set needs the old one (change)")
        with self.assertRaises(gate.GateError):
            gate.set_passphrase(self.st, "another passphrase", old="not it at all")
        gate.set_passphrase(self.st, "another passphrase", old=PASS)
        self.assertEqual(gate.verify(self.st, "another passphrase"), gate.FREE_TRIES)
        self.assertNotIn(PASS, self.st.log_path.read_text())
        self.st.check_perms()

    def test_wrong_tries_lock_and_the_lock_doubles_and_survives_a_restart(self):
        gate.set_passphrase(self.st, PASS)
        now = 1_000_000.0
        for left in (4, 3, 2, 1):
            with self.assertRaises(gate.GateError) as e:
                gate.verify(self.st, "nope nope", now=now)
            self.assertEqual((e.exception.reason, e.exception.info["left"]), ("wrong", left))
        with self.assertRaises(gate.GateError) as e:
            gate.verify(self.st, "nope nope", now=now)
        self.assertEqual((e.exception.reason, e.exception.info["seconds"]), ("locked", 60))
        with self.assertRaises(gate.GateError) as e:
            gate.verify(State(self.st.root), PASS, now=now + 30)        # a fresh State = a restarted serve
        self.assertEqual(e.exception.reason, "locked", "even the right passphrase waits out the lock")
        for _ in range(5):
            with self.assertRaises(gate.GateError):
                gate.verify(self.st, "nope nope", now=now + 61)
        self.assertEqual(gate.lock_left(self.st, now=now + 61), 120, "second lock = 2 min")
        self.assertEqual(gate.verify(self.st, PASS, now=now + 200), gate.FREE_TRIES)
        self.assertEqual(json.loads(self.st.approver_path.read_text())["locks"], 0, "a right one clears the ledger")


class Decide(unittest.TestCase):
    """What a same-user process outside the fence — or a holder of an admin-page session — gets at the control socket."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _pending(self, host):
        s = serve.Session(cid=7, state="pending", device="NEWNEWNEWNEWNEWN", name="攻击者的手机", pub=os.urandom(32), h=os.urandom(32))
        s.deadline = time.monotonic() + 60
        host.sessions[7] = s
        host.pairing = serve.Pairing(os.urandom(16), os.urandom(32), time.time() + 60, time.monotonic() + 60, ctl=None, cid=7)
        return s

    def test_the_right_code_without_the_passphrase_approves_nothing(self):
        host = _host(self.st, [])

        async def go():
            s = self._pending(host)
            code = wire.safety_code(s.h)
            out = [await host.decide(host.pairing, code, None)]            # nothing typed: asks again, costs no try
            self.assertEqual(out[0], {"ev": "pass_wrong", "left": gate.FREE_TRIES})
            out = [await host.decide(host.pairing, code, "guess guess")]
            for _ in range(3):
                out.append(await host.decide(host.pairing, code, "guess guess"))
            self.assertEqual(self.st.devices(), {})
            self.assertIn(7, host.sessions, "a wrong passphrase keeps the device waiting (the code is not used up)")
            p = host.pairing
            out.append(await host.decide(p, code, "guess guess"))     # 5th wrong → locked → denied
            out.append(await host.decide(p, code, PASS))
            return out
        out = asyncio.run(go())
        self.assertEqual([(o["ev"], o["left"]) for o in out[:4]], [("pass_wrong", n) for n in (4, 3, 2, 1)])
        self.assertEqual(out[4], {"ev": "denied", "reason": "pass_locked"})
        self.assertEqual(out[5], {"ev": "denied", "reason": "no_pending_device"})
        self.assertEqual(self.st.devices(), {})
        log = self.st.log_path.read_text()
        self.assertEqual(log.count('"ev": "pair_pass_wrong"'), 4)
        self.assertIn('"reason": "pass_locked"', log)

    def test_no_passphrase_set_refuses_and_right_passphrase_approves(self):
        host = _host(self.st, [])
        sent = []

        async def send_app(s, obj):
            sent.append(obj)
            return True
        host.send_app = send_app

        async def go():
            s = self._pending(host)
            r1 = await host.decide(host.pairing, wire.safety_code(s.h), PASS)
            return r1
        self.assertEqual(asyncio.run(go())["ev"], "approved")
        self.assertEqual(len(self.st.devices()), 1)
        self.st.approver_path.unlink()
        host2 = _host(self.st, [])

        async def go2():
            s = self._pending(host2)
            s.device = "OTHEROTHEROTHER1"
            return await host2.decide(host2.pairing, wire.safety_code(s.h), PASS)
        self.assertEqual(asyncio.run(go2()), {"ev": "denied", "reason": "pass_not_set"})
        self.assertEqual(len(self.st.devices()), 1)


class ControlSockets(unittest.TestCase):
    """G-A56: control sockets outside the runtime dir and /tmp. A herdr server in a TEMPORARY HOME (never the human's own
    session: no HERDR_* of this shell reaches it, its socket is under the temp HOME), a listening socket in an arbitrary
    folder (tmux -S / emacs / nvim style), and the docker socket."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-ctl-")
        self.home = pathlib.Path(self.tmp.name) / "home"
        (self.home / "proj").mkdir(parents=True)
        self.st = _state(self.tmp.name, passphrase=False)
        self.st.perm_dir.mkdir(exist_ok=True)
        self.work = self.home / "proj"
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(5)
                except Exception:  # noqa: BLE001
                    p.kill()
        self.tmp.cleanup()

    def _env(self, **extra) -> dict:
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.home), "LANG": "C.UTF-8"}
        env.update(extra)
        return env

    def _argv(self, env, sockets=(), allow_docker=False):
        # Discovery input is synthetic; real configuration and filtering functions run.
        with mock.patch.object(fence, "control_sockets", return_value=list(sockets)), \
             mock.patch.object(fence, "service_mounts", return_value=[]), \
             mock.patch.object(fence, "other_states", return_value=[]):
            return fence.bwrap_argv(self.st, str(self.work), home=str(self.home), runtime="", allow_docker=allow_docker, environ=env)

    def test_hide_env_by_prefix(self):
        env = {"HERDR_SOCKET_PATH": "/x", "HERDR_PANE_ID": "1", "HERDR_NEW_THING": "1", "ZELLIJ_SESSION_NAME": "s",
               "WEZTERM_UNIX_SOCKET": "/w", "KITTY_WINDOW_ID": "3", "NVIM": "/n", "VSCODE_IPC_HOOK_CLI": "/v", "DOCKER_HOST": "unix:///d",
               "HOME": "/h", "PATH": "/p"}
        names = fence.hide_env(env)
        for k in env:
            if k not in ("HOME", "PATH"):
                self.assertIn(k, names)
        self.assertNotIn("HOME", names)
        self.assertNotIn("DOCKER_HOST", fence.hide_env(env, allow_docker=True))

    def test_a_herdr_server_is_unreachable_from_inside(self):
        folder = self.home / ".config" / "herdr"
        folder.mkdir(parents=True)
        sock = str(folder / "herdr.sock")
        env = self._env(HERDR_SOCKET_PATH=sock, HERDR_PANE_ID="synthetic")
        a = self._argv(env, [sock])
        self.assertIn(["--tmpfs",str(folder.resolve())], [a[i:i+2] for i in range(len(a))])
        self.assertIn(["--unsetenv","HERDR_SOCKET_PATH"], [a[i:i+2] for i in range(len(a))])
        self.assertIn(["--unsetenv","HERDR_PANE_ID"], [a[i:i+2] for i in range(len(a))])
        # An unrelated ordinary folder is not globally hidden (positive contrast).
        self.assertNotIn(str(self.work), [a[i+1] for i,x in enumerate(a) if x == "--tmpfs"])

    def test_a_listening_socket_in_any_folder_is_hidden(self):
        sock = str(self.work / "synthetic-custom.sock")
        a = self._argv(self._env(), [sock])
        self.assertIn(["--ro-bind","/dev/null",sock], [a[i:i+3] for i in range(len(a))])
        self.assertNotIn(str(self.st.perm_sock_path), a, "permission endpoint is not masked")
        absent = self._argv(self._env(), [])
        self.assertNotIn(sock, absent, "negative discovery control: no invented socket")

    def test_docker_socket_is_hidden_unless_the_human_allowed_it(self):
        # Feed the real discovery parser synthetic metadata, never a daemon or /proc.
        dock, other = "/run/docker.sock", str(self.work / "synthetic-other.sock")
        env = self._env(DOCKER_HOST="unix://" + dock)
        from types import SimpleNamespace
        def discover(allowed):
            table = "Num RefCount Protocol Flags Type St Inode Path\n0: 2 0 00010000 0001 01 1 " + other + "\n"
            with mock.patch("builtins.open", mock.mock_open(read_data=table)), \
                 mock.patch.object(fence.os, "access", side_effect=lambda p,m: p == dock), \
                 mock.patch.object(fence.os.path, "exists", return_value=True), \
                 mock.patch.object(fence.os, "stat", return_value=SimpleNamespace(st_mode=stat.S_IFSOCK)), \
                 mock.patch.object(fence.os, "lstat", return_value=SimpleNamespace(st_uid=os.getuid(), st_mode=stat.S_IFSOCK)):
                return fence.control_sockets(env, allowed)
        for allowed in (False, True):
            sockets = discover(allowed)
            self.assertEqual(dock in sockets, not allowed)
            self.assertIn(other, sockets)
            a = self._argv(env, sockets, allowed)
            masks = [a[i:i+3] for i,x in enumerate(a) if x == "--ro-bind"]
            self.assertEqual(["--ro-bind","/dev/null",dock] in masks, not allowed)
            self.assertIn(["--ro-bind","/dev/null",other], masks)
            self.assertEqual("DOCKER_HOST" in fence.hide_env(env,allowed), not allowed)
            self.assertIn(str(self.st.root.resolve()), [a[i+1] for i,x in enumerate(a) if x == "--tmpfs"])

    @unittest.skipUnless(sys.platform == "linux", "bubblewrap argv")
    def test_control_folders_and_the_runtime_dir_without_xdg(self):
        (self.home / ".config" / "herdr").mkdir(parents=True)
        (self.home / ".screen").mkdir()
        a = fence.bwrap_argv(self.st, str(self.work), home=str(self.home), runtime="", environ=self._env())
        tmpfs = [a[i + 1] for i, x in enumerate(a) if x == "--tmpfs"]
        self.assertIn(str((self.home / ".config" / "herdr").resolve()), tmpfs)
        self.assertIn(str((self.home / ".screen").resolve()), tmpfs)
        if os.path.isdir(f"/run/user/{os.getuid()}"):
            self.assertIn(os.path.realpath(f"/run/user/{os.getuid()}"), tmpfs, "hidden also when XDG_RUNTIME_DIR is unset")
        self.assertLess(tmpfs.index(str((self.home / ".config" / "herdr").resolve())), tmpfs.index(str(self.st.root.resolve())))
        # never the home itself or a folder that holds the Agent's folder
        d = fence.control_dirs(str(self.home), {"HERDR_SOCKET_PATH": str(self.home / "h.sock"),
                                                "SCREENDIR": str(self.work)}, workdir=str(self.work))
        self.assertNotIn(str(self.home.resolve()), d)
        self.assertNotIn(str(self.work.resolve()), d)


class FenceConfig(unittest.TestCase):
    def setUp(self):
        # Preferences are per fixture: another test's working root is not initial setup.
        from tempfile import TemporaryDirectory as _ConfigDirectory
        from unittest.mock import patch as _config_patch
        self._config_tmp = _ConfigDirectory(prefix='aj-0300-config-')
        self.addCleanup(self._config_tmp.cleanup)
        _config_env = _config_patch.dict('os.environ', {'XDG_CONFIG_HOME': self._config_tmp.name})
        _config_env.start()
        self.addCleanup(_config_env.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_agent_folder_cannot_be_jarvis_itself(self):
        for d in (self.st.root, self.st.perm_dir):
            d.mkdir(exist_ok=True)
            with self.assertRaises(ValueError) as e:
                self.st.set_agent_config("claude", str(d))
            self.assertEqual(str(e.exception), "protected_dir")
        # Self-maintenance and a normal first selection are independent fresh
        # installs; switching an existing protected working root is not setup.
        with tempfile.TemporaryDirectory() as first, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": first+"/config"}):
            pkg_state = _state(first)
            pkg_state.set_agent_config("claude", str(PKG))
            self.assertEqual(pkg_state.agent_config()["dir"], str(PKG))
        work = pathlib.Path(self.tmp.name) / "w"
        work.mkdir()
        self.st.set_agent_config("claude", str(work))
        self.assertTrue(self.st.agent_config()["fence"], "fenced by default")
        self.st.set_agent_config("claude", str(work), fence=False)
        self.assertFalse(self.st.agent_config()["fence"])
        cfg = json.loads(self.st.config_path.read_text())
        cfg["agent"]["fence"] = "no"
        self.st.write_private(self.st.config_path, json.dumps(cfg).encode())
        self.assertTrue(self.st.agent_config()["fence"], "anything but an explicit false reads as fenced")
        self.assertFalse(self.st.agent_config()["docker"], "docker hidden by default (G-A56)")
        cfg["agent"]["docker"] = "yes"
        self.st.write_private(self.st.config_path, json.dumps(cfg).encode())
        self.assertFalse(self.st.agent_config()["docker"], "anything but an explicit true keeps docker hidden")
        self.st.set_agent_config("claude", str(work), docker=True)
        self.assertTrue(self.st.agent_config()["docker"])
        from agentj import doctor
        row = doctor.check_agent(self.st)
        self.assertEqual(row["status"], doctor.WARN)
        self.assertIn("--allow-docker", row["summary"])

    def test_fence_unavailable_refuses_launch(self):
        work = pathlib.Path(self.tmp.name) / "w"
        work.mkdir()
        self.st.set_agent_config("codex", str(work))
        host = _host(self.st, [])
        from agentj import agent as agents
        ag = agents.make(host, {**self.st.agent_config(), "session_mode": "independent"})
        with mock.patch.object(fence, "problem", return_value="no_bwrap"):
            self.assertIsNone(ag.launch_argv(["/usr/bin/true", "x"]))
            self.assertIsNone(ag.launch_argv(["/usr/bin/true", "y"]))
        self.assertIn('"status": "refused"', self.st.log_path.read_text())

    def test_bwrap_argv_hides_state_and_puts_back_only_the_permission_folder(self):
        a = fence.bwrap_argv(self.st, "/srv/work", home="/home/u", runtime="")
        self.assertEqual(a[-1], "--")
        self.assertIn("--unshare-pid", a)
        self.assertIn("--die-with-parent", a)
        root, perm = str(self.st.root.resolve()), str(self.st.perm_dir.resolve())
        tmpfs = [a[i + 1] for i, x in enumerate(a) if x == "--tmpfs"]
        self.assertTrue(any(root == t or root.startswith(t + "/") for t in tmpfs), "state dir hidden")
        binds = [(a[i + 1], a[i + 2]) for i, x in enumerate(a) if x == "--bind"]
        self.assertIn((perm, perm), binds)
        self.assertEqual([b for b in binds if b[0].startswith(root) and b[0] != perm], [], "nothing else from the state dir")
        ro = [a[i + 1] for i, x in enumerate(a) if x == "--ro-bind"]
        pkg = str(PKG.resolve())
        self.assertFalse(any(pkg == r or pkg.startswith(r + "/") for r in ro), "F14: self-code is writable")
        for k in ("TMUX", "SSH_AUTH_SOCK", "WAYLAND_DISPLAY", "DISPLAY", "XAUTHORITY", "AGENTJ_STATE_DIR"):
            self.assertIn(k, a[a.index("--unsetenv"):])
        self.assertIn("DBUS_SESSION_BUS_ADDRESS", a[a.index("--unsetenv"):], "P591: do not inherit the session bus")


class Package(unittest.TestCase):
    def test_the_shipped_package_records_no_wire_frames(self):
        for f in PKG.rglob("*"):
            if f.suffix in (".py", ".js", ".html") and f.is_file():
                text = f.read_text()
                self.assertNotIn("WIRE_DUMP", text, f.name)
                self.assertNotIn("import wiredump", text, f.name)
        with tempfile.TemporaryDirectory() as d:
            st = _state(d)
            host = serve.Host(st, events="jsonl", read_stdin=False)
            before = sorted(p.name for p in st.root.iterdir())
            host._tap("out", b"\x01" * 40)
            self.assertIsNone(serve.Host._tap(host, "in", "x"))
            self.assertEqual(sorted(p.name for p in st.root.iterdir()), before, "the shipped _tap writes nothing")


class Decline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = _state(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_at_the_terminal_sends_a_signed_decline(self):
        calls = []
        login_id = "L" * 22
        answers = {"/v1/host/login": (201, {"login_id": login_id, "user_code": "BCDF-GHJK", "verification_uri": "https://x/",
                                            "expires_in": 600, "interval": 5}),
                   "/v1/host/poll": (200, {"status": "bound", "host_id": "h_1", "tenant": {"slug": "evil-co", "name": "Evil"}}),
                   "/v1/host/decline": (200, {"status": "declined"})}

        def post(url, payload):
            path = url.split("agentj.test", 1)[1]
            calls.append((path, payload))
            return answers[path]
        res = cloud.login(self.st, "https://api.agentj.test", show=lambda lg: None, confirm=lambda t, n=None: False,
                          post=post, sleep=lambda s: None)
        self.assertEqual((res["status"], res["undone"]), ("declined", "undone"))
        self.assertFalse(self.st.cloud_path.exists(), "nothing written on N")
        path, env = calls[-1]
        self.assertEqual(path, "/v1/host/decline")
        inner = json.loads(wire.unb64u(env["body"]))
        self.assertEqual(set(inner), {"v", "t", "channel", "ts", "login_id"})
        self.assertEqual((inner["t"], inner["login_id"], inner["channel"]), ("decline", login_id, cloud.channel_of(self.st)))
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        Ed25519PublicKey.from_public_bytes(wire.unb64u(env["pk"])).verify(
            wire.unb64u(env["sig"]), f"{cloud.CTX_DECLINE}\n{env['body']}".encode())
        for status, body, want in ((409, {"error": "already_confirmed"}, "already_confirmed"), (404, {"error": "not_found"}, "not_found"),
                                   (429, {"error": "rate_limited"}, "fail"), (500, {}, "fail")):
            self.assertEqual(cloud.decline(self.st, "https://api.agentj.test", login_id,
                                           post=lambda u, p, s=status, b=body: (s, b)), want)


if __name__ == "__main__":
    unittest.main()
