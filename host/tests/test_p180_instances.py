"""P180 (public since P217): several numbered instances (= seats) on one computer under one OS user. `--instance 1` / no option is the
ordinary install; N ≥ 2 has its own state, preferences, unit, workspace; the fence hides the other instances; an upgrade
restarts every instance; a remote paired with one instance is refused by another."""
from __future__ import annotations

import io
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

HOST = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOST))
sys.path.insert(0, str(HOST / "tests"))

from agentj import instance, wire  # noqa: E402
from agentj.noise import IK, Handshake  # noqa: E402
from agentj.state import State  # noqa: E402

INST_VARS = ("AGENTJ_INSTANCE", "AGENTJ_STATE_DIR", "AGENTJARVIS_STATE_DIR", "AGENTJ_SERVICE_NAME", "AGENTJ_SEATS_ROOT",
             "XDG_CONFIG_HOME", "HOME")


class Env(unittest.TestCase):
    """A throw-away HOME; the instance variables restored after each test."""

    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in INST_VARS}
        self.home = tempfile.mkdtemp(prefix="aj-p180-", dir="/tmp")
        for k in INST_VARS:
            os.environ.pop(k, None)
        os.environ["HOME"] = self.home

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.home, ignore_errors=True)

    def make(self, n, workspace=None):
        """Instance n's identity (keys + config) and, optionally, its Agent folder."""
        st = State(instance.state_root(n))
        st.init(relay="ws://127.0.0.1:9", web="https://m.agentj.app")
        if workspace:
            os.makedirs(workspace, exist_ok=True)
            cfg = st.config()
            cfg["agent"] = {"kind": "claude", "dir": os.path.realpath(workspace), "model": None, "fence": True}
            st.write_private(st.config_path, json.dumps(cfg).encode())
        return st


class Selection(Env):
    def test_split_argv(self):
        self.assertEqual(instance.split_argv(["--instance", "3", "pair", "--no-qr"]), (3, ["pair", "--no-qr"]))
        self.assertEqual(instance.split_argv(["status", "--instance=2"]), (2, ["status"]))
        self.assertEqual(instance.split_argv(["pair"]), (None, ["pair"]))
        self.assertEqual(instance.split_argv(["send", "--", "--instance", "2"]), (None, ["send", "--", "--instance", "2"]))
        for bad in (["--instance"], ["--instance", "0"], ["--instance", "11"], ["--instance", "x"],
                    ["--instance", "2", "--instance", "3"]):
            with self.assertRaises(instance.InstanceError, msg=bad):
                instance.split_argv(bad)

    def test_instance_one_is_the_ordinary_install(self):
        e = {"AGENTJ_INSTANCE": "1", "AGENTJ_SERVICE_NAME": "agentj-3", "AGENTJ_STATE_DIR": "/x"}
        self.assertEqual(instance.apply(1, e), 1)
        self.assertEqual(e, {}, "--instance 1 = no option at all")
        self.assertEqual(instance.apply(None, {}), 1)
        self.assertEqual(instance.state_root(1), pathlib.Path(self.home, ".local/state/agentj"))
        self.assertEqual(instance.service_name(1, "linux"), "agentj")
        self.assertEqual(instance.service_name(1, "macos"), "net.agentj.host")
        self.assertEqual(instance.prefs_dir(1), pathlib.Path(self.home, ".config/agentj"))

    def test_instance_n_has_its_own_everything(self):
        e = {}
        self.assertEqual(instance.apply(4, e), 4)
        self.assertEqual(e["AGENTJ_INSTANCE"], "4")
        self.assertEqual(e["AGENTJ_STATE_DIR"], os.path.join(self.home, ".local/state/agentj-4"))
        self.assertEqual(e["AGENTJ_SERVICE_NAME"], instance.service_name(4))
        self.assertEqual(instance.service_name(4, "linux"), "agentj-4")
        self.assertEqual(instance.service_name(4, "macos"), "net.agentj.host.4")
        self.assertEqual(instance.workspace(4), pathlib.Path(self.home, "agentj-seats/4"))
        e2 = {"AGENTJ_INSTANCE": "5"}          # a unit / job carries only the number
        instance.apply(None, e2)
        self.assertEqual(e2["AGENTJ_STATE_DIR"], os.path.join(self.home, ".local/state/agentj-5"))

    def test_state_preferences_and_unit_follow_the_instance(self):
        from agentj import preferences, service, state
        instance.apply(3)
        self.assertEqual(state.state_dir(), pathlib.Path(self.home, ".local/state/agentj-3"))
        self.assertEqual(preferences.path(), pathlib.Path(self.home, ".config/agentj-3/config.json5"))
        self.assertEqual(service.name(), instance.service_name(3))
        self.assertIsNone(service.legacy_name(), "a seat instance never touches the ≤ 0.9 unit")
        env, _ = service.service_env({"PATH": "/usr/bin", **os.environ})
        self.assertEqual(env.get("AGENTJ_INSTANCE"), "3")
        text = service.unit_text("agentj-3", ["/v/bin/agentj"], env)
        self.assertIn('Environment="AGENTJ_INSTANCE=3"', text)
        instance.apply(1)
        self.assertEqual(preferences.path(), pathlib.Path(self.home, ".config/agentj/config.json5"))
        env, _ = service.service_env({"PATH": "/usr/bin", **os.environ})
        self.assertNotIn("AGENTJ_INSTANCE", env)

    def test_browser_unit_carries_the_instance(self):
        from agentj import browser
        instance.apply(2)
        self.assertIn('Environment="AGENTJ_INSTANCE=2"', browser.unit_text(["/v/bin/agentj"]))
        self.assertTrue(browser.svc_name().startswith(instance.service_name(2)))

    def test_numbers_label_and_describe(self):
        self.make(1)
        self.make(3, os.path.join(self.home, "agentj-seats/3"))
        self.assertEqual(instance.numbers(), [1, 3])
        with self.assertRaises(instance.InstanceError):
            instance.set_label(2, "x")
        self.assertEqual(instance.set_label(3, "  小王\t的 席位 "), "小王 的 席位")
        self.assertEqual(instance.label(3), "小王 的 席位")
        with mock.patch("agentj.service.status", return_value={"active": "inactive"}):
            row = instance.describe(3)
        self.assertEqual((row["instance"], row["label"], row["remotes"], row["running"], row["online"]),
                         (3, "小王 的 席位", 0, False, False))
        self.assertEqual(row["workspace"], os.path.realpath(os.path.join(self.home, "agentj-seats/3")))


class Fence(Env):
    def test_other_instances_and_seat_workspaces_are_hidden(self):
        from agentj import fence
        ws2, ws3 = (os.path.join(self.home, "agentj-seats", n) for n in "23")
        main_ws = os.path.join(self.home, "coding")
        self.make(1, main_ws)
        self.make(2, ws2)
        self.make(3, ws3)
        for n in (1, 2, 3):
            instance.prefs_dir(n).mkdir(parents=True, exist_ok=True)
        real = os.path.realpath
        instance.apply(2)
        st2 = State()
        hidden = fence.other_states(st2)
        for p in (instance.state_root(1), instance.state_root(3), instance.prefs_dir(1), instance.prefs_dir(3), ws3):
            self.assertIn(real(str(p)), hidden, p)
        for p in (instance.state_root(2), ws2, self.home):
            self.assertNotIn(real(str(p)), hidden, p)
        self.assertIn(real(main_ws), hidden, "a seat does not see the owner's (instance 1's) folder either")
        argv = fence.bwrap_argv(st2, ws2, home=self.home, runtime="")
        tmpfs = {argv[i + 1] for i, x in enumerate(argv) if x == "--tmpfs"}
        self.assertTrue({real(ws3), real(str(instance.state_root(3))), real(str(instance.state_root(1)))} <= tmpfs)
        self.assertNotIn(real(ws2), tmpfs)
        prof, params = fence.sbpl_profile(st2, ws2, home=self.home, uid=501)
        self.assertIn(real(ws3), params.values(), "macOS denies the same folders")
        instance.apply(1)
        hidden1 = fence.other_states(State())
        self.assertIn(real(ws2), hidden1)
        self.assertIn(real(str(instance.state_root(2))), hidden1)

    def test_a_folder_holding_agentj_or_a_path_entry_stays_visible(self):
        from agentj import fence
        code = os.path.join(self.home, "src")
        tools = os.path.join(self.home, "tools")
        os.makedirs(os.path.join(tools, "bin"))
        self.make(1, code)
        self.make(3, tools)
        self.make(2, os.path.join(self.home, "agentj-seats/2"))
        instance.apply(2)
        with mock.patch.object(fence, "code_paths", return_value=[os.path.join(code, "agentj")]), \
             mock.patch.dict(os.environ, {"PATH": os.path.join(tools, "bin") + ":/usr/bin"}):
            hidden = fence.other_states(State())
        self.assertNotIn(os.path.realpath(code), hidden, "Agent J's own code must stay runnable inside the fence")
        self.assertNotIn(os.path.realpath(tools), hidden, "a harness binary on PATH must stay runnable")

    def test_home_as_a_workspace_is_never_hidden(self):
        from agentj import fence
        self.make(1, self.home)
        self.make(2, os.path.join(self.home, "agentj-seats/2"))
        instance.apply(2)
        self.assertNotIn(os.path.realpath(self.home), fence.other_states(State()))

    def test_a_folder_holding_the_own_workspace_is_not_hidden(self):
        from agentj import fence
        outer = os.path.join(self.home, "team")
        self.make(2, outer)
        self.make(3, os.path.join(outer, "inner"))
        instance.apply(3)
        self.assertNotIn(os.path.realpath(outer), fence.other_states(State()))

    def test_the_agent_keeps_its_instance_number(self):
        from agentj import fence
        env = {"AGENTJ_INSTANCE": "2", "AGENTJ_STATE_DIR": "/s", "PATH": "/usr/bin", "HOME": self.home}
        out = fence.launch_environment(env, kind="claude")
        self.assertEqual(out.get("AGENTJ_INSTANCE"), "2")
        self.assertNotIn("AGENTJ_STATE_DIR", out)
        self.assertEqual(fence.auxiliary_environment(env).get("AGENTJ_INSTANCE"), "2")


class Upgrade(Env):
    def test_only_instance_one_runs_the_nightly_upgrade(self):
        from agentj import auto_update
        self.make(1)
        st2 = self.make(2)
        instance.apply(2)
        with mock.patch.object(auto_update, "enabled", return_value=True):
            self.assertFalse(auto_update._due(st2, now=time.mktime(time.strptime("2026-10-12 04:59", "%Y-%m-%d %H:%M"))))
        shutil.rmtree(instance.state_root(1))
        with mock.patch.object(auto_update, "enabled", return_value=True), mock.patch.object(auto_update.activity, "record"):
            auto_update._due(st2, now=time.time())
        self.assertTrue((st2.root / auto_update.RECORD).exists(), "without instance 1 the seat schedules its own")

    def test_after_an_upgrade_every_running_instance_restarts(self):
        from agentj import service_recovery
        for n in (1, 2, 3, 5):
            self.make(n)
        instance.apply(2)
        st2 = State()
        launched = []
        active = {1: True, 3: True, 5: False}
        r = service_recovery.restart_siblings(st2, "0.20.0", launch=lambda st, argv, extra_env=None: launched.append(
            (st.root, argv[-6:], extra_env)), active=lambda n, env: active[n])
        self.assertEqual(r, [1, 3])
        self.assertEqual([x[1] for x in launched], [["--instance", "1", "service", "install", "--recovery-worker", "0.20.0"],
                                                   ["--instance", "3", "service", "install", "--recovery-worker", "0.20.0"]])
        self.assertEqual(launched[0][0], instance.state_root(1))
        self.assertEqual(launched[0][2], {service_recovery.SIBLING_ENV: "1"})
        os.environ[service_recovery.SIBLING_ENV] = "1"
        try:
            self.assertEqual(service_recovery.restart_siblings(st2, "0.20.0", launch=lambda *a, **k: self.fail("fan-out"),
                                                               active=lambda n, env: True), [], "a sibling job never fans out")
        finally:
            os.environ.pop(service_recovery.SIBLING_ENV)


class PairingLink(Env):
    def test_the_link_selects_the_instance_by_channel(self):
        from agentj import protocol_handler
        self.make(1)
        st3 = self.make(3)
        url = "agentj://pair?channel=" + st3.config()["channel"]
        self.assertEqual(protocol_handler._instance_for(url).root, st3.root)
        self.assertIsNone(protocol_handler._instance_for("agentj://pair?channel=AAAAAAAAAAAAAAAAAAAAAA"))


class Protocol(Env):
    def test_a_seat_keeps_the_existing_pairing_handler(self):
        from agentj import service
        calls = []
        with mock.patch("agentj.protocol_handler.install", side_effect=lambda: calls.append(1)), \
             mock.patch("agentj.protocol_handler.installed", return_value=True):
            instance.apply(2)
            service._register_pair_protocol([])
            self.assertEqual(calls, [], "instance 2 does not rewrite the Agent J host's one handler")
            instance.apply(1)
            service._register_pair_protocol([])
            self.assertEqual(calls, [1])
        with mock.patch("agentj.protocol_handler.install", side_effect=lambda: calls.append(2)), \
             mock.patch("agentj.protocol_handler.installed", return_value=False):
            instance.apply(3)
            service._register_pair_protocol([])
            self.assertEqual(calls, [1, 2], "none yet: the seat registers it")


class Cli(Env):
    def agentj(self, *args, env=None):
        e = {**os.environ, "PYTHONPATH": str(HOST), **(env or {})}
        return subprocess.run([sys.executable, "-m", "agentj.cli", *args], cwd=HOST, env=e, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=120)

    def test_bad_number_and_status_per_instance(self):
        r = self.agentj("--instance", "11", "status")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("1–10", r.stderr)
        self.make(2)
        r = self.agentj("--instance", "2", "devices", "--json")
        self.assertEqual((r.returncode, json.loads(r.stdout or "null")), (0, {}), r.stderr)
        r = self.agentj("instances", "--json")
        self.assertEqual([x["instance"] for x in json.loads(r.stdout)], [2], r.stderr)
        r = self.agentj("--instance", "2", "label", "前台小李")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(self.agentj("instances", "--json").stdout)[0]["label"], "前台小李")

    def test_a_seat_never_attaches_to_the_desktop_session(self):
        st = self.make(2)
        ws = os.path.join(self.home, "agentj-seats/2")
        os.makedirs(ws)
        r = self.agentj("--instance", "2", "agent", "codex", "--shared", "--dir", ws)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--independent", r.stderr)
        r = self.agentj("--instance", "2", "agent", "codex", "--dir", ws)
        self.assertEqual(r.returncode, 0, r.stderr)
        instance.apply(2)
        self.assertEqual(State(st.root).agent_config()["session_mode"], "independent")

    def test_the_admin_helper_stays_with_instance_one(self):
        self.make(2)
        r = self.agentj("--instance", "2", "sudo-helper", "install")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("实例 1", r.stderr)

    def test_remove_keeps_the_workspace_and_refuses_a_bound_seat(self):
        st = self.make(4, os.path.join(self.home, "agentj-seats/4"))
        (st.root / "cloud.json").write_text("{}")
        r = self.agentj("instances", "remove", "4")
        self.assertIn("unlink", r.stderr)
        (st.root / "cloud.json").unlink()
        from agentj import service
        env_file = service.env_file(instance.service_name(4))
        os.makedirs(os.path.dirname(env_file), exist_ok=True)
        pathlib.Path(env_file).write_text('AGENTJ_CLAUDE_BIN="/x/claude"\n')
        with mock.patch.dict(os.environ, {"AGENTJ_SERVICE_NAME": "x"}):
            r = self.agentj("instances", "remove", "4", env={"PATH": "/nonexistent"})
        self.assertFalse(st.root.exists(), r.stdout + r.stderr)
        self.assertFalse(os.path.exists(env_file), "the seat's service env file goes too")
        self.assertTrue(os.path.isdir(os.path.join(self.home, "agentj-seats/4")))
        self.assertNotEqual(self.agentj("instances", "remove", "1").returncode, 0)


class TwoHosts(Env):
    """Two instances = two `agentj serve`, one relay: a remote paired with instance 2 is refused by instance 3."""

    def setUp(self):
        super().setUp()
        from fakerelay import FakeRelay
        self.relay = FakeRelay().__enter__()
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            p.send_signal(signal.SIGTERM)
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
        self.relay.__exit__()
        super().tearDown()

    def serve(self, n):
        st = State(instance.state_root(n))
        st.init(relay=self.relay.url, web="https://m.agentj.app")
        env = {**os.environ, "PYTHONPATH": str(HOST)}
        self.procs.append(subprocess.Popen([sys.executable, "-m", "agentj.cli", "--instance", str(n), "serve", "--events", "quiet",
                                            "--no-stdin"], cwd=HOST, env=env, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        from agentj import names
        end = time.time() + 30
        while time.time() < end:
            try:
                if (names.ctl_call(st, {"cmd": "status"}) or {}).get("relay_up"):
                    return st
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.1)
        self.fail(f"instance {n} serve did not come up")

    def resume(self, st, kp, host_pub=None):
        """A remote's reconnect (IK) to instance `st`'s channel. → the first app message, or "closed"."""
        from websockets.sync.client import connect
        ch = st.config()["channel"]
        hub = st.host_keypair().pub if host_pub is None else host_pub
        ws = connect(f"{self.relay.url}/v1/dev/{ch}", open_timeout=10).__enter__()
        try:
            while True:
                m = ws.recv(10)
                if isinstance(m, str) and json.loads(m) == {"t": "host", "up": True}:
                    break
            hs = Handshake(IK, True, kp, prologue=wire.resume_prologue(ch), rs=hub)
            ws.send(bytes([wire.RESUME_INIT]) + hs.write_message(b"{}"))
            try:
                while True:
                    m = ws.recv(10)
                    if isinstance(m, bytes):
                        break
            except Exception:  # noqa: BLE001 — the host closed this socket
                return "closed"
            hs.read_message(m[1:])
            send, recv, _ = hs.split()
            ws.send(bytes([wire.DATA]) + send.encrypt(b"", wire.pad_json({"t": "hello"})))
            try:
                while True:
                    m = ws.recv(10)
                    if isinstance(m, bytes):
                        return wire.unpad_json(recv.decrypt(b"", m[1:]))
            except Exception:  # noqa: BLE001
                return "closed"
        finally:
            ws.close()

    def test_a_remote_belongs_to_exactly_one_instance(self):
        from agentj.noise import Keypair
        st2, st3 = self.serve(2), self.serve(3)
        self.assertNotEqual(st2.config()["channel"], st3.config()["channel"], "own identity, own relay channel")
        self.assertNotEqual(st2.host_keypair().pub, st3.host_keypair().pub)
        phone = Keypair.generate()
        st2.pair_device(phone.pub, "小王的手机")
        ok = self.resume(st2, phone)
        self.assertIsInstance(ok, dict)
        self.assertNotEqual(ok.get("t"), "removed", ok)
        other = self.resume(st3, phone)                       # the right key of instance 3: not on its list
        self.assertTrue(other == "closed" or other.get("t") == "removed", other)
        self.assertEqual(self.resume(st3, phone, host_pub=st2.host_keypair().pub), "closed",
                         "instance 2's key on instance 3's channel: the handshake itself fails")
        self.assertEqual(list(st3.devices()), [])
        self.assertIn('"unknown_device"', st3.log_path.read_text())


if __name__ == "__main__":
    unittest.main()
