"""P339 (backlog #44): a fenced Agent (OpenCode / Kimi / Qwen …) switches the harness through serve's own socket.

QA Mac 10-16: OpenCode inside the fence could not `launchctl bootstrap gui/<uid>` the switch job, could not see launchd at all,
concluded "the service is not loaded", asked three times for the admin password and told the owner to open a terminal.
- `agentj agent switch` / `--scan` / `--undo` ask serve (elevate.sock — visible inside the fence — then control.sock) to launch
  the job; serve checks every semantic field and builds the argv itself (never an argv / env from the caller). No serve, an
  older serve, a busy one → the caller launches it itself as before; a refusal → launch_failed.
- `agentj service status` / doctor: serve's answer counts when launchd / systemd cannot see the service.
- `agentj sudo` refuses Agent J's own service management and a `--why` that says nothing.
Never touches the real systemctl / launchctl: launch_job is stubbed everywhere; the fenced run has an empty PATH.
Run (in host): python -m unittest tests/test_p339_switch_via_serve.py
"""
import _hermetic  # noqa: F401,I001
import asyncio
import contextlib
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from agentj import cli, ctl_auth, doctor, elevate, fence, names, service, service_recovery, switch_harness as sw  # noqa: E402
from agentj.state import State  # noqa: E402
from test_p155_switch_harness import Clock, FakeServe, fake_probe, mk_state  # noqa: E402

SERVE_SRC = (HERE.parent / "agentj" / "serve.py").read_text()
NOT_FENCED = {k: "" for k in ("AGENTJ_FENCED", elevate.ENV, "AGENTJ_PERM_SOCK", "AGENTJ_CAPABILITY_SOCK", "AGENTJ_RECALL_SOCK")}


def unfenced():
    p = mock.patch.dict(os.environ, NOT_FENCED)
    p.start()
    for k in NOT_FENCED:
        os.environ.pop(k, None)
    return p


class ServeThread:
    """serve's Agent-facing socket (elevate.Elevator.on_client) on its own event loop in a thread."""

    def __init__(self, st):
        self.st, self.loop = st, asyncio.new_event_loop()
        self.el = elevate.Elevator(types.SimpleNamespace(st=st))
        self.t = threading.Thread(target=self.loop.run_forever, daemon=True)

    def __enter__(self):
        self.t.start()
        asyncio.run_coroutine_threadsafe(self.el.start(), self.loop).result(10)
        return self

    def __exit__(self, *exc):
        asyncio.run_coroutine_threadsafe(self.el.stop(), self.loop).result(10)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.t.join(10)
        self.loop.close()


class Spec(unittest.TestCase):
    """serve's side: semantic fields only, each one checked; serve builds the argv itself."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p339-spec-")
        self.addCleanup(self.tmp.cleanup)
        self.st = mk_state(self.tmp.name, "opencode", model="zhipuai/glm-5.3")
        self.job = "0123456789abcdef"
        unit = pathlib.Path(self.tmp.name) / "units"
        for p in (mock.patch.object(sw, "receipt_path", side_effect=lambda name=sw.RECEIPT: unit / ("agentj." + name)),
                  mock.patch.object(sw, "_LAST_SCAN", [])):
            p.start()
            self.addCleanup(p.stop)

    def test_valid_requests_and_the_argv_serve_builds(self):
        for req, tail in (
                ({"kind": "switch", "target": "codex"}, ["codex", "--worker", "--job", self.job]),
                ({"kind": "switch", "target": "opencode", "model": "zhipuai/glm-5.3", "dry_run": True,
                  "at": int(time.time())}, ["opencode", "--worker", "--job", self.job, "--model", "zhipuai/glm-5.3", "--dry-run"]),
                ({"kind": "switch", "undo": True, "force": False}, ["--undo", "--worker", "--job", self.job]),
                ({"kind": "switch", "target": "claude", "force": True}, ["claude", "--worker", "--job", self.job, "--force"])):
            spec, err = sw.check_spec({"cmd": "harness_job", **req})
            self.assertIsNone(err, req)
            self.assertEqual(sw.job_argv({**spec, "job": self.job}), service.agentj_argv() + ["agent", "switch"] + tail)
        spec, err = sw.check_spec({"t": "harness_job", "kind": "scan"})
        self.assertEqual(sw.job_argv({**spec, "job": self.job}),
                         service.agentj_argv() + ["agent", "switch", "--worker", "--scan", "--job", self.job])

    def test_junk_is_refused(self):
        ok = {"kind": "switch", "target": "codex"}
        for bad, code in (
                ({**ok, "argv": ["/bin/sh", "-c", "id"]}, "bad_field"), ({**ok, "env": {"LD_PRELOAD": "x"}}, "bad_field"),
                ({**ok, "job": self.job}, "bad_field"),             # P339 review: the caller never picks the job id
                ({**ok, "dry_run": "true"}, "bad_dry_run"), ({**ok, "force": 1}, "bad_force"), ({**ok, "undo": None}, "bad_undo"),
                ({**ok, "target": "gemini"}, "bad_target"), ({**ok, "target": "codex; rm -rf ~"}, "bad_target"),
                ({**ok, "target": "opencode", "model": "glm-5.3"}, "bad_model"), ({**ok, "model": "--worker"}, "bad_model"),
                ({**ok, "model": ["a"]}, "bad_model"), ({**ok, "at": time.time() - 3600}, "bad_at"), ({**ok, "at": "now"}, "bad_at"),
                ({**ok, "kind": "upgrade"}, "bad_kind"), ({"kind": "scan", "target": "codex"}, "bad_field"),
                ({"kind": "scan", "dry_run": True}, "bad_field"),
                ({"kind": "switch", "undo": True, "target": "codex"}, "bad_field"), ("x", "bad_request")):
            self.assertEqual(sw.check_spec(bad), (None, code), bad)
        launch = mock.Mock()
        self.assertEqual(sw.host_job(self.st, {**ok, "argv": ["x"]}, launch_job=launch), {"ok": False, "error": "bad_field"})
        launch.assert_not_called()

    def test_host_job_launches_with_its_own_argv_and_no_caller_env(self):
        launch = mock.Mock()
        r = sw.host_job(self.st, {"kind": "switch", "target": "codex"}, launch_job=launch)
        self.assertTrue(r["ok"] and sw._JOB.fullmatch(r["job"]), r)
        (st, argv), kw = launch.call_args
        self.assertEqual(argv, service.agentj_argv() + ["agent", "switch", "codex", "--worker", "--job", r["job"]])
        self.assertEqual(sw.read_receipt(self.st)["job"], r["job"])        # serve writes "probing" under its own id
        sw.write_receipt(self.st, {"status": "failed", "job": r["job"]})
        self.assertEqual((kw["purpose"], list(kw["extra_env"])), ("switch", ["AGENTJ_JOB_AT"]))
        self.assertLessEqual(abs(int(kw["extra_env"]["AGENTJ_JOB_AT"]) - time.time()), 5)    # serve's own clock
        boom = mock.Mock(side_effect=service.ServiceError("recovery_launch_failed"))
        self.assertEqual(sw.host_job(self.st, {"kind": "scan"}, launch_job=boom), {"ok": False, "error": "launch_failed"})
        boom = mock.Mock(side_effect=RuntimeError("secret-ish detail"))
        self.assertEqual(sw.host_job(self.st, {"kind": "scan"}, launch_job=boom), {"ok": False, "error": "launch_failed"})

    def test_one_job_at_a_time_and_a_scan_a_minute(self):
        """P339 review: no second switch / scan while one is in flight (busy); scans ≤ 1 per minute (fresh result reused, or
        too_soon + seconds) — each scan is five real model calls, each switch restarts the service."""
        launch, now = mock.Mock(), [1_000_000.0]
        with mock.patch.object(sw.time, "time", lambda: now[0]):
            a = sw.host_job(self.st, {"kind": "switch", "target": "codex"}, launch_job=launch)
            self.assertEqual(sw.host_job(self.st, {"kind": "switch", "target": "claude"}, launch_job=launch), {"ok": False, "error": "busy"})
            self.assertEqual(sw.host_job(self.st, {"kind": "scan"}, launch_job=launch), {"ok": False, "error": "busy"})
            for status in ("waiting", "switching"):
                sw.write_receipt(self.st, {"status": status, "job": a["job"]})
                self.assertEqual(sw.host_job(self.st, {"kind": "scan"}, launch_job=launch)["error"], "busy")
            now[0] += sw.JOB_MAX_AGE + 1                      # a job that died mid-way does not block for ever
            s1 = sw.host_job(self.st, {"kind": "scan"}, launch_job=launch)
            self.assertTrue(s1["ok"])
            now[0] += 20
            self.assertEqual(sw.host_job(self.st, {"kind": "scan"}, launch_job=launch), {"ok": False, "error": "too_soon", "wait": 41})
            sw.write_receipt(self.st, {"status": "scan", "job": s1["job"], "harnesses": []}, sw.SCAN)
            self.assertEqual(sw.host_job(self.st, {"kind": "scan"}, launch_job=launch), {"ok": True, "job": s1["job"]})   # reused
            now[0] += 61
            sw.write_receipt(self.st, {"status": "scan", "job": s1["job"], "harnesses": []}, sw.SCAN)
            now[0] += 61
            s2 = sw.host_job(self.st, {"kind": "scan"}, launch_job=launch)
            self.assertNotEqual(s2["job"], s1["job"])
            now[0] += 90                                      # still running after a minute: a switch waits for it
            self.assertEqual(sw.host_job(self.st, {"kind": "switch", "target": "codex"}, launch_job=launch)["error"], "busy")
            self.assertEqual(sw.host_job(self.st, {"kind": "scan"}, launch_job=launch)["error"], "busy")
        self.assertEqual(launch.call_count, 3)
        for code, zh, en in (("busy", "还没结束", "has not finished"), ("too_soon", "41 秒后再试", "in 41 s")):
            self.assertIn(zh, sw.reason_text(code, "41"))
            self.assertIn(en, sw.reason_text(code, "41", "en"))
        self.assertIn("41 秒后", sw._say_scan({"ok": False, "reason": "too_soon", "wait": 41}, "zh"))

    def test_caller_waits_on_serves_job_and_busy_keeps_the_receipt(self):
        sw.write_receipt(self.st, {"status": "waiting", "target": "codex", "from": "opencode", "job": self.job})
        with mock.patch.object(sw, "_via_serve", side_effect=sw.HostRefused("busy", None)):
            r = sw.request(self.st, "claude", sleep=lambda s: None, clock=Clock())
        self.assertEqual((r["reason"], sw.read_receipt(self.st)["status"]), ("busy", "waiting"))
        self.assertIn("还没结束", sw._say(r, "zh"))
        with mock.patch.object(sw, "_via_serve", side_effect=sw.HostRefused("too_soon", 30)):
            self.assertEqual(sw.scan(self.st, sleep=lambda s: None, clock=Clock()), {"ok": False, "reason": "too_soon", "wait": 30})
        sw.write_receipt(self.st, {"status": "scan", "job": "f" * 16, "harnesses": [{"name": "codex"}]}, sw.SCAN)
        with mock.patch.object(sw, "_via_serve", return_value="f" * 16):
            self.assertEqual(sw.scan(self.st, sleep=lambda s: None, clock=Clock())["harnesses"], [{"name": "codex"}])

    def test_request_builds_the_same_argv_as_before(self):
        seen = []
        sw.request(self.st, "codex", None, True, launch=lambda st, argv: seen.append(argv), sleep=lambda s: None,
                   clock=iter(range(0, 10_000, 200)).__next__)
        job = seen[0][seen[0].index("--job") + 1]
        self.assertEqual(seen[0], service.agentj_argv() + ["agent", "switch", "codex", "--worker", "--job", job, "--dry-run"])

    def test_serve_dispatches_it_and_it_is_not_sensitive(self):
        self.assertIn('elif cmd == "harness_job":', SERVE_SRC)
        self.assertIn("asyncio.to_thread(switch_harness.host_job, self.st, req)", SERVE_SRC)
        self.assertNotIn("harness_job", ctl_auth.SENSITIVE)


class ViaServe(unittest.TestCase):
    """The caller: serve first; without one (or an older one) the direct launch as before; a refusal = launch_failed."""

    def setUp(self):
        self.addCleanup(unfenced().stop)
        self.tmp = tempfile.TemporaryDirectory(prefix="p339-via-")
        self.addCleanup(self.tmp.cleanup)
        unit = pathlib.Path(self.tmp.name) / "units"
        p = mock.patch.object(sw, "receipt_path", side_effect=lambda name=sw.RECEIPT: unit / ("agentj." + name))
        p.start()
        self.addCleanup(p.stop)
        self.spec = {"kind": "scan"}

    def test_no_serve_falls_back_to_the_direct_launch(self):
        st = mk_state(self.tmp.name)
        with mock.patch.object(service_recovery, "launch_job") as direct:
            sw._launch(st, self.spec, None)
        direct.assert_called_once()
        self.assertEqual(direct.call_args[0][1][-3:-1], ["--scan", "--job"])     # the direct launch picks its own id

    def test_older_serve_unknown_command_and_busy_fall_back(self):
        st = mk_state(self.tmp.name)
        old_elevate = {"result": "refused", "why": "shape", "detail": "t: sudo | secret | secret_out | secret_result"}
        for ctl in (mock.Mock(return_value=None), mock.Mock(return_value={"ok": False, "error": "unknown command"}),
                    mock.Mock(side_effect=names.ServeBusy("timeout"))):
            with mock.patch.object(elevate, "client_request", return_value=old_elevate), \
                    mock.patch.object(names, "ctl_call", ctl), mock.patch.object(service_recovery, "launch_job") as direct:
                sw._launch(st, self.spec, None)
            ctl.assert_called_once()
            self.assertEqual(ctl.call_args[0][1]["cmd"], "harness_job")
            direct.assert_called_once()

    def test_control_socket_launches_when_elevate_sock_is_missing(self):
        st = mk_state(self.tmp.name)
        with mock.patch.object(elevate, "client_request", return_value={"result": "unavailable"}), \
                mock.patch.object(names, "ctl_call", return_value={"ok": True, "job": "a" * 16}) as ctl, \
                mock.patch.object(service_recovery, "launch_job") as direct:
            self.assertEqual(sw._launch(st, self.spec, None), "a" * 16)
        direct.assert_not_called()
        req = ctl.call_args[0][1]
        self.assertEqual(set(req) - {"cmd", "at"}, set(self.spec))     # semantic fields only
        self.assertNotIn("argv", req)

    def test_refusal_is_launch_failed_and_nothing_launched(self):
        st = mk_state(self.tmp.name, "opencode")
        with mock.patch.object(elevate, "client_request", return_value={"ok": False, "error": "bad_target"}), \
                mock.patch.object(service_recovery, "launch_job") as direct:
            r = sw.request(st, "codex", sleep=lambda s: None, clock=Clock())
        self.assertEqual(r["reason"], "launch_failed")
        direct.assert_not_called()
        self.assertEqual(sw.read_receipt(st)["reason"], "launch_failed")

    def test_fenced_caller_switches_back_through_serve(self):
        """OpenCode / Kimi / Qwen, fenced (the caller's own launch_job cannot reach launchd / systemd), switch back to Codex
        and to Claude Code — serve's socket launches the job; the job (here in-process) switches and leaves its receipt."""
        for frm in ("opencode", "kimi", "qwen"):
            for to in ("codex", "claude"):
                with self.subTest(frm=frm, to=to), tempfile.TemporaryDirectory(prefix="p339-fx-") as d:
                    st = mk_state(d, frm, **({"model": "zhipuai/glm-5.3"} if frm == "opencode" else {}))
                    clock, served = Clock(), []

                    def launch_job(st_, argv, purpose="recovery", extra_env=None, restart=False):
                        if threading.current_thread() is threading.main_thread():
                            raise service.ServiceError("recovery_launch_failed")      # the fenced caller: no launchd
                        served.append(argv)
                        i = argv.index("switch")
                        fake = FakeServe(st_, st_.agent_config()["kind"])
                        sw.worker(st_, argv[i + 1], None, job=argv[argv.index("--job") + 1], probe=fake_probe(),
                                  ctl=fake, restart=fake.restart, status=lambda: {"installed": True, "active": "active"},
                                  ready=lambda s, limit: "idle", sleep=clock.sleep, clock=clock)

                    with mock.patch.object(service_recovery, "launch_job", launch_job), ServeThread(st):
                        r = sw.request(st, to, sleep=clock.sleep, clock=clock)
                    self.assertTrue(r["ok"] and r.get("scheduled"), r)
                    self.assertEqual(len(served), 1)
                    self.assertEqual(served[0][served[0].index("switch") + 1], to)
                    self.assertEqual(st.agent_config()["kind"], to)


class ServiceSeen(unittest.TestCase):
    """`agentj service status|start|restart` and doctor trust serve's answer when the manager cannot see the service."""

    def setUp(self):
        self.addCleanup(unfenced().stop)
        self.tmp = tempfile.TemporaryDirectory(prefix="p339-svc-")
        self.addCleanup(self.tmp.cleanup)
        self.st = mk_state(self.tmp.name)
        p = mock.patch.dict(os.environ, {"AGENTJ_STATE_DIR": str(self.st.root)})
        p.start()
        self.addCleanup(p.stop)
        self.launchd = {"kind": "launchd", "name": "net.agentj.host", "path": "/x/net.agentj.host.plist", "installed": True,
                        "active": "not_loaded"}

    def run_cli(self, mode, json_=False):
        out, err, code = io.StringIO(), io.StringIO(), None
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                cli.cmd_service(types.SimpleNamespace(mode=mode, json=json_, recovery_worker=None, deferred=None))
            except SystemExit as e:
                code = e.code
        return out.getvalue(), err.getvalue(), code

    def test_status_running_via_socket_when_launchctl_says_not_loaded(self):
        with mock.patch.object(service, "status", return_value=dict(self.launchd)), \
                mock.patch.object(names, "ctl_call", return_value={"ok": True}):
            out, _, _ = self.run_cli("status", True)
            d = json.loads(out)
            self.assertEqual((d["active"], d["seen_by"], d["serve"], d["manager_active"]), ("active", "socket", "answering", "not_loaded"))
            out, _, _ = self.run_cli("status")
            self.assertIn("后台服务在运行（serve 在应答）", out)
            self.assertIn("launchd / systemd", out)
        with mock.patch.object(service, "status", return_value=dict(self.launchd)), \
                mock.patch.object(names, "ctl_call", return_value=None):          # nothing answers: the manager's word
            d = json.loads(self.run_cli("status", True)[0])
            self.assertEqual((d["active"], d["serve"]), ("not_loaded", "silent"))
            self.assertNotIn("seen_by", d)

    def test_fenced_status_asks_elevate_sock_when_control_sock_is_hidden(self):
        with ServeThread(self.st) as s, mock.patch.dict(os.environ, {"AGENTJ_FENCED": "1", elevate.ENV: str(s.el.sock_path)}), \
                mock.patch.object(service, "status", return_value={**self.launchd, "kind": "systemd", "active": "inactive"}), \
                mock.patch.object(names, "ctl_call", side_effect=names.ServeBusy("PermissionError")):   # macOS sandbox
            d = json.loads(self.run_cli("status", True)[0])
        self.assertEqual((d["active"], d["seen_by"]), ("active", "socket"))
        self.assertEqual(names.elevate_sock(self.st), self.st.perm_dir / elevate.SOCK_NAME)   # outside the fence: the state's

    def test_start_is_a_no_op_and_fenced_restart_says_why(self):
        with mock.patch.object(names, "ctl_call", return_value={"ok": True}), \
                mock.patch.object(service, "start") as start, mock.patch.object(service, "restart") as restart:
            out, _, code = self.run_cli("start")
            self.assertIn("不用再启动", out)
            self.assertIsNone(code)
            with mock.patch.dict(os.environ, {"AGENTJ_FENCED": "1"}):
                _, _, code = self.run_cli("restart")
            self.assertIn("agentj agent switch", str(code))
            self.assertIn("never sudo", str(code))
            start.assert_not_called()
            restart.assert_not_called()

    def test_doctor_service_row(self):
        with mock.patch.object(names, "ctl_call", return_value={"ok": True}), \
                mock.patch.object(service, "binary_env_mismatches", return_value=[]):
            row = doctor.check_service(doctor._seen(self.st, dict(self.launchd)))
        self.assertEqual(row["status"], doctor.OK, row)
        with mock.patch.object(names, "ctl_call", return_value=None), \
                mock.patch.object(service, "binary_env_mismatches", return_value=[]):
            row = doctor.check_service(doctor._seen(self.st, dict(self.launchd)))
        self.assertEqual(row["status"], doctor.FAIL)


class SudoCard(unittest.TestCase):
    """`agentj sudo` never for Agent J's own service; `--why` says what the command is for."""

    WHY = "安装视频转码要用的 ffmpeg"

    def test_own_service_refused_with_the_detail(self):
        for argv in (["agentj", "service", "start"], ["/opt/aj/bin/agentj", "--instance", "2", "service", "restart"],
                     ["sh", "-c", "agentj service install"], ["launchctl", "kickstart", "-k", "gui/501/net.agentj.host"],
                     ["launchctl", "bootstrap", "gui/501", "Library/LaunchAgents/net.agentj.host.plist"],
                     ["systemctl", "--user", "restart", "agentj"], ["systemctl", "restart", "agentj.service"]):
            with self.assertRaises(elevate.Refused) as c:
                elevate.norm_sudo({"argv": argv, "why": self.WHY})
            self.assertEqual((c.exception.why, c.exception.detail), ("agentj_service", elevate.SERVICE_DETAIL), argv)
        for argv in (["apt-get", "install", "-y", "ffmpeg"], ["systemctl", "restart", "nginx"], ["launchctl", "list"]):
            self.assertEqual(elevate.norm_sudo({"argv": argv, "why": self.WHY})["why"], self.WHY)

    def test_why_must_say_something(self):
        argv = ["apt-get", "install", "-y", "ffmpeg"]
        for why in ("x", "test", "转码要用", "apt-get install -y ffmpeg", "  APT-GET install  ", "install -y ffmpeg"):
            with self.assertRaises(elevate.Refused) as c:
                elevate.norm_sudo({"argv": argv, "why": why})
            self.assertEqual((c.exception.why, c.exception.detail), ("why", elevate.WHY_DETAIL), why)
        for why in ("装 ffmpeg 转码", "install ffmpeg for video transcoding"):
            self.assertEqual(elevate.norm_sudo({"argv": argv, "why": why})["why"], why)

    def test_the_agent_sees_the_detail(self):
        res = {"result": "refused", "why": "agentj_service", "detail": elevate.SERVICE_DETAIL}
        err = io.StringIO()
        with mock.patch.object(elevate, "client_request", return_value=res), contextlib.redirect_stderr(err):
            code = elevate.cmd_sudo(types.SimpleNamespace(command=["agentj", "service", "start"], why=self.WHY, effect="",
                                                          timeout=60, json=False))
        self.assertEqual(code, elevate.EXIT_CARD)
        self.assertIn("agentj agent switch", err.getvalue())
        self.assertIn("agentj service status", err.getvalue())

    def test_served_refusal_on_the_socket(self):
        with tempfile.TemporaryDirectory(prefix="p339-el-") as d:
            st = State(pathlib.Path(d) / "state")
            st.init(relay="ws://127.0.0.1:9")
            with ServeThread(st) as s:
                r = elevate.client_request(st, {"t": "sudo", "argv": ["agentj", "service", "start"], "why": self.WHY}, 10,
                                           path=s.el.sock_path)
                self.assertEqual((r["result"], r["why"]), ("refused", "agentj_service"))
                r = elevate.client_request(st, {"t": "serve_ping"}, 10, path=s.el.sock_path)
                self.assertEqual(r, {"ok": True, "serve": "answering"})
                r = elevate.client_request(st, {"t": "harness_job", "kind": "switch", "target": "codex", "job": "0" * 16}, 10,
                                           path=s.el.sock_path)
                self.assertEqual(r, {"ok": False, "error": "bad_field"})


class Skill(unittest.TestCase):
    def test_skill_rule(self):
        text = (HERE.parent / "agentj" / "skills" / "agentj-switch-harness" / "SKILL.md").read_text()
        for s in ("never need sudo or the owner's terminal", "Never `agentj sudo`", "the service IS running",
                  "不需要管理员权限", "绝不为此用 `agentj sudo`", "agentj service status"):
            self.assertIn(s, text)


@unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("bwrap"), "Linux fence only")
class RealFence(unittest.TestCase):
    """A real bwrap fence (control.sock hidden): `agentj agent switch codex --dry-run` inside it reaches serve's elevate.sock,
    and the receipt beside the unit is readable there. The fenced CLI has an empty PATH: no systemd-run / busctl to reach."""

    def test_fenced_switch_dry_run_and_service_status(self):
        with tempfile.TemporaryDirectory(prefix="p339-fence-") as d:
            d = pathlib.Path(d)
            env_patch = mock.patch.dict(os.environ, {"AGENTJ_STATE_DIR": str(d / "state"), "XDG_CONFIG_HOME": str(d / "work" / "cfg"),
                                                     "AGENTJ_SERVICE_NAME": "agentj-p339test"})
            env_patch.start()
            self.addCleanup(env_patch.stop)
            for k in NOT_FENCED:
                os.environ.pop(k, None)
            (d / "work").mkdir()      # the unit dir lives under it: the gate runner's temp root (/var/tmp) is hidden in a fence
            st = State()
            st.init(relay="ws://127.0.0.1:9")
            (d / "bin").mkdir()
            st.set_agent_config("opencode", str(d / "work"), "zhipuai/glm-5.3", fence=True, docker=False)
            served = []

            def launch_job(st_, argv, purpose="recovery", extra_env=None, restart=False):
                served.append(argv)
                sw.write_receipt(st_, {"status": "probe_ok", "target": argv[argv.index("switch") + 1], "from": "opencode",
                                       "job": argv[argv.index("--job") + 1], "version": "0.160.0"})

            argv = fence.wrap(st, [sys.executable, "-m", "agentj.cli", "agent", "switch", "codex", "--dry-run", "--json"], str(d / "work"))
            with mock.patch.object(service_recovery, "launch_job", launch_job), ServeThread(st):
                # serve exports AGENTJ_ELEVATE_SOCK to its Agent; the fence unsets AGENTJ_STATE_DIR (State() differs inside)
                env = dict(os.environ, PYTHONPATH=str(HERE.parent), PATH=str(d / "bin"))
                run = subprocess.run(argv, cwd=d / "work", env=env, capture_output=True, text=True, timeout=120)
                if run.returncode and "bwrap" in run.stderr and not served:
                    self.skipTest("bubblewrap cannot start here")
                status = subprocess.run(fence.wrap(st, [sys.executable, "-m", "agentj.cli", "service", "status", "--json"],
                                                   str(d / "work")), cwd=d / "work", env=env, capture_output=True, text=True,
                                        timeout=60)
            self.assertEqual(run.returncode, 0, run.stderr[-2000:] + run.stdout[-2000:])
            r = json.loads(run.stdout)
            self.assertTrue(r["ok"] and r["dry_run"], r)
            self.assertEqual(len(served), 1)
            self.assertIn("--dry-run", served[0])
            s = json.loads(status.stdout)
            self.assertEqual((s["serve"], s["active"]), ("answering", "active"), status.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
