"""P332 (PROTOCOL §33 "Rescue watchdog"): the tiny process that takes the relay host socket when serve stops answering.

Counter / hand-back / 4001 back-off, refusal of unsigned / replayed / unpaired requests, the action whitelist, the unit
files and their idempotent registration (install and the upgrade's recovery worker), `agentj rescue status` without
secrets, "never imports serve", and one end-to-end run: fake relay + a paired device doing the real RESUME handshake."""
import _hermetic  # noqa: F401
from _testtmp import TEST_TMP  # noqa: E402

import asyncio
import contextlib
import io
import json
import os
import pathlib
import plistlib
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from websockets.sync.client import connect as ws_connect

from agentj import controls, rescue_watchdog as rw, service, wire
from agentj.noise import IK, IKPSK2, Handshake, Keypair
from agentj.state import State
from fakerelay import FakeRelay

HOST = pathlib.Path(__file__).resolve().parents[1]


def raw(pub) -> bytes:
    return pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


class Phone:
    """A paired remote: X25519 device key (allowlisted) + Ed25519 approval key; resumes over the relay like session.js."""

    def __init__(self, st: State, relay_url: str, paired: bool = True):
        self.st, self.url = st, relay_url
        self.kp, self.sign = Keypair.generate(), Ed25519PrivateKey.generate()
        self.device = wire.device_id(self.kp.pub)
        if paired:
            st.add_device(self.kp.pub, "测试手机", raw(self.sign.public_key()))
        self.ch = st.config()["channel"]

    def connect(self, t=10):
        self.ws = ws_connect(f"{self.url}/v1/dev/{self.ch}", open_timeout=t).__enter__()
        end = time.monotonic() + t
        while time.monotonic() < end:
            m = self.ws.recv(t)
            if isinstance(m, str) and json.loads(m) == {"t": "host", "up": True}:
                return
        raise AssertionError("host never up")

    def resume(self, caps=("p33",), t=10):
        hs = Handshake(IK, True, self.kp, prologue=wire.resume_prologue(self.ch), rs=self.st.host_keypair().pub)
        self.ws.send(bytes([wire.RESUME_INIT]) + hs.write_message(b"{}"))
        m = self._bin(t)
        assert m[0] == wire.HS_RESP
        hs.read_message(m[1:])
        self.send, self.recv, _ = hs.split()
        self.app_send({"t": "hello", "caps": list(caps)})

    def _bin(self, t):
        while True:
            m = self.ws.recv(t)
            if isinstance(m, bytes):
                return m

    def app_send(self, obj):
        self.ws.send(bytes([wire.DATA]) + self.send.encrypt(b"", wire.pad_json(obj, wire.MAX_JSON_P33)))

    def app(self, t=10) -> dict:
        b = self._bin(t)
        assert b[0] == wire.DATA
        return wire.unpad_json(self.recv.decrypt(b"", b[1:]), wire.MAX_JSON_P33)

    def until(self, pred, t=10) -> dict:
        end = time.monotonic() + t
        while True:
            m = self.app(max(0.1, end - time.monotonic()))
            if pred(m):
                return m

    def signed(self, t, act, target, **extra) -> dict:
        n, ts = secrets.token_hex(16), int(time.time() * 1000)
        msg = f"{controls.CONTEXT}\n{self.ch}\n{self.device}\n{act}\n{n}\n{ts}\n{rw.object_digest(act, target)}"
        sig = wire.b64u(self.sign.sign(msg.encode()))
        return {"t": t, "r": secrets.token_hex(8), "n": n, "ts": ts, "sig": sig, **extra}

    def closed(self, t=5) -> bool:
        from websockets.exceptions import ConnectionClosed
        try:
            while True:
                if isinstance(self.ws.recv(t), bytes):
                    return False
        except ConnectionClosed:
            return True
        except TimeoutError:
            return False

    def close(self):
        with contextlib.suppress(Exception):
            self.ws.close()


class _Hosts(dict):
    """The real relay's rule: a newer authenticated host socket replaces the old one (close 4001)."""

    def __setitem__(self, k, v):
        prev = self.get(k)
        super().__setitem__(k, v)
        if prev is not None and prev is not v:
            asyncio.ensure_future(prev.close(4001))


class Relay4001(FakeRelay):
    def __init__(self):
        super().__init__()
        self.hosts = _Hosts()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="p332-", dir=TEST_TMP)
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", self.tmp]))
        self.st = State(pathlib.Path(self.tmp) / "st")


class Counter(Base):
    def setUp(self):
        super().setUp()
        self.st.init(relay="ws://127.0.0.1:9", web="https://m.agentj.app")
        self.wd = rw.Watchdog(self.st, probe_fn=lambda: "down")

    def test_three_failures_take_over_two_answers_hand_back(self):
        wd = self.wd
        self.assertIsNone(wd.observe("down"))
        self.assertIsNone(wd.observe("stall"))
        self.assertEqual(wd.observe("stall"), "takeover")
        self.assertEqual(wd.why, "serve_stall")
        self.assertGreater(wd.since, 0)
        wd.active = True
        self.assertIsNone(wd.observe("down"))         # already holding: no second takeover
        self.assertIsNone(wd.observe("ok"))           # one answer is not enough
        self.assertIsNone(wd.observe("down"))         # … and a failure in between resets it
        self.assertIsNone(wd.observe("ok"))
        self.assertEqual(wd.observe("ok"), "handback")

    def test_a_good_answer_resets_the_failure_streak(self):
        wd = self.wd
        wd.observe("down"); wd.observe("down"); wd.observe("ok")
        self.assertIsNone(wd.observe("down"))
        self.assertIsNone(wd.observe("down"))
        self.assertEqual(wd.observe("down"), "takeover")
        self.assertEqual(wd.why, "serve_down")

    def test_after_4001_three_fresh_failures(self):
        wd = self.wd
        for _ in range(5):
            wd.observe("down")
        wd.active = True
        wd.replaced()
        self.assertFalse(wd.active)
        self.assertIsNone(wd.observe("down"))
        self.assertIsNone(wd.observe("down"))
        self.assertEqual(wd.observe("down"), "takeover")

    def test_intentional_stop_is_passive(self):
        wd = self.wd
        for _ in range(10):
            self.assertIsNone(wd.observe("stopped"), "a unit stopped on purpose is never taken over")
        wd.observe("down"); wd.observe("down")
        self.assertIsNone(wd.observe("stopped"), "… and it resets the failure streak")
        wd.active = True
        self.assertEqual(wd.observe("stopped"), "handback")

    def test_stopped_probe_reads_the_unit(self):
        def ctl(out, rc=0):
            return lambda *a, **k: subprocess.CompletedProcess(a, rc, out, "")
        with patch.object(rw.sys, "platform", "linux"):
            with patch.object(service, "_systemctl", ctl("LoadState=loaded\nActiveState=inactive\nResult=success\n")):
                self.assertTrue(rw.serve_stopped_on_purpose())
            for out in ("LoadState=loaded\nActiveState=failed\nResult=exit-code\n",
                        "LoadState=loaded\nActiveState=inactive\nResult=signal\n",
                        "LoadState=loaded\nActiveState=activating\nResult=success\n",
                        "LoadState=not-found\nActiveState=inactive\nResult=success\n", ""):
                with patch.object(service, "_systemctl", ctl(out)):
                    self.assertFalse(rw.serve_stopped_on_purpose(), out)
            with patch.object(service, "_systemctl", side_effect=service.ServiceError("no_systemd")):
                self.assertFalse(rw.serve_stopped_on_purpose())
        with patch.object(rw.sys, "platform", "darwin"):
            self.assertFalse(rw.serve_stopped_on_purpose())

    def test_run_loop_stays_passive_after_a_stop(self):
        wd = rw.Watchdog(self.st, poll=0.01, probe_fn=lambda: "down", stopped_fn=lambda: True)

        async def go():
            t = asyncio.create_task(wd.run())
            await asyncio.sleep(0.3)
            wd.stopping.set()
            await asyncio.wait_for(t, 5)
        asyncio.run(go())
        self.assertFalse(wd.active)
        self.assertIsNone(wd.relay_task)
        evs = [json.loads(x)["ev"] for x in (self.st.root / "rescue.log").read_text().splitlines()]
        self.assertIn("passive", evs)
        self.assertNotIn("takeover", evs)

    def test_flapping_takeover_backs_off(self):
        now = {"t": 1000.0}
        wd = rw.Watchdog(self.st, probe_fn=lambda: "down", clock=lambda: now["t"])

        def take_over():
            for i in range(wd.need):
                r = wd.observe("down")
            self.assertEqual(r, "takeover")
            wd.active, wd.taken_at = True, now["t"]
        needs = []
        for _ in range(8):
            take_over()
            needs.append(wd.need)
            now["t"] += 30                                   # 4001 within 60 s: serve is alive
            wd.replaced()
            fails_before = [wd.observe("down") for _ in range(wd.need - 1)]
            self.assertEqual(set(fails_before), {None}, "no takeover before the doubled threshold")
            wd.fails = 0
        self.assertEqual(needs, [3, 6, 12, 24, 40, 40, 40, 40], "×2 each time, capped at 10 min of 15 s polls")
        take_over()
        now["t"] += 120                                      # a 4001 long after the takeover: no extra back-off
        wd.replaced()
        self.assertEqual(wd.need, 40)
        wd.observe("ok")
        self.assertEqual(wd.need, 40, "one answer is not enough")
        wd.observe("ok")
        self.assertEqual(wd.need, rw.TAKEOVER, "two answers in a row reset it")
        self.assertIn('"backoff"', (self.st.root / "rescue.log").read_text())

    def test_probe_kinds(self):
        st = self.st
        self.assertEqual(rw.probe(st, 0.5), "down")     # no control socket at all
        import socket
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(st.sock_path)); sock.listen(1)     # there, but never answers → stall
        try:
            t0 = time.monotonic()
            self.assertEqual(rw.probe(st, 0.5), "stall")
            self.assertLess(time.monotonic() - t0, 3)
        finally:
            sock.close()


class Signed(Base):
    def setUp(self):
        super().setUp()
        self.st.init(relay="ws://127.0.0.1:9", web="https://m.agentj.app")
        self.ph = Phone(self.st, "ws://127.0.0.1:9")
        self.nonces = controls.Nonces()

    def chk(self, obj, action="reset", target=None, device=None):
        return rw.check(self.st, self.nonces, self.ph.ch, device or self.ph.device, obj, action, target or {"what": "connection"})

    def test_valid_then_replay(self):
        obj = self.ph.signed("reset", "reset", {"what": "connection"})
        self.assertIsNone(self.chk(obj))
        self.assertEqual(self.chk(obj), "replay")

    def test_unsigned_stale_wrong_object_no_key(self):
        self.assertEqual(self.chk({"t": "reset", "r": "0" * 16}), "shape")
        obj = self.ph.signed("reset", "reset", {"what": "connection"})
        self.assertEqual(self.chk({**obj, "ts": obj["ts"] - 10 * 60_000}), "stale")
        self.assertEqual(self.chk(obj, "rescue_do", {"action": "help"}), "bad_signature")   # re-pointed at another action
        bad = dict(obj, sig=wire.b64u(b"\0" * 64))
        self.assertEqual(self.chk(bad), "bad_signature")
        other = Phone(self.st, "ws://127.0.0.1:9", paired=False)
        self.assertEqual(self.chk(other.signed("reset", "reset", {"what": "connection"}), device=other.device), "no_key")

    def test_object_texts_are_serves(self):
        self.assertEqual(controls.object_text("reset", {"what": "connection"}), "connection")
        self.assertEqual(controls.object_text("rescue_do", {"action": "help"}), "help")


class Whitelist(Base):
    def setUp(self):
        super().setUp()
        self.st.init(relay="ws://127.0.0.1:9", web="https://m.agentj.app")
        self.calls = []
        self.wd = rw.Watchdog(self.st, probe_fn=lambda: "ok", restart_fn=lambda: self.calls.append("restart") or True,
                              linger_fn=lambda: self.calls.append("linger") or True, restart_wait=0.5)

    def test_unknown_and_serve_only_actions(self):
        steps = []
        emit = lambda *a: steps.append(a)  # noqa: E731
        self.assertEqual(self.wd.do("rm -rf /", "zh", emit)["why"], "shape")
        for a in ("reconnect", "new_session"):
            r = self.wd.do(a, "zh", emit)
            self.assertEqual((r["ok"], r["why"]), (False, "unavailable"))
            self.assertNotRegex(r["text"], "(?i)relay|serve|harness|session")
        with patch.object(rw, "_reset_fn", lambda name: None):
            self.assertEqual(self.wd.do("rollback", "en", emit)["why"], "unavailable")
        self.assertEqual(self.calls, [])

    def test_restart_texts_are_plain(self):
        steps = []
        res = self.wd.do("restart", "zh", lambda *a: steps.append(a))
        self.assertTrue(res["ok"])
        self.assertEqual(self.calls, ["restart"])
        self.assertEqual(steps[0], (1, "run", "Agent J 主机上的 Agent J 没响应，正在重启它…"))
        for _step, _state, text in steps:
            self.assertNotRegex(text, "(?i)relay|serve|harness|session|中继")

    def test_help_flow_runs_only_whitelisted_actions_from_support(self):
        seen = {}

        class Kit:
            @staticmethod
            def help_flow(lang, emit, run_action, *, with_doctor=False, tag="help", **kw):
                seen["with_doctor"] = with_doctor
                seen["estop"] = kw.get("estop")
                emit(1, "run", "正在检查这台Agent J 主机…")
                seen["bash"] = run_action("bash")
                seen["shutdown"] = run_action("help")             # not something support may ask for
                seen["restart"] = run_action("restart")
                return {"ok": True, "step": 3, "ticket": "st_" + "a" * 22, "category": "service_down", "text": "好了"}
        self.wd.kit = Kit
        steps = []
        res = self.wd.do("help", "zh", lambda *a: steps.append(a))
        self.assertEqual(seen["bash"], (False, "not allowed"))
        self.assertEqual(seen["shutdown"], (False, "not allowed"))
        self.assertFalse(seen["estop"])
        self.assertTrue(seen["restart"][0])
        self.assertFalse(seen["with_doctor"])
        self.assertEqual(self.calls, ["restart"])
        self.assertEqual((res["ok"], res["ticket"], res["category"]), (True, "st_" + "a" * 22, "service_down"))

    def test_help_offers_persistent_actions_and_honours_the_estop(self):
        seen = {}

        class Kit:
            @staticmethod
            def help_flow(lang, emit, run_action, *, with_doctor=False, tag="help", estop=False, **kw):
                seen["estop"] = estop
                seen["runs"] = [run_action(a) for a in ("linger", "rollback", "new_session")]
                emit(4, "skip", "客服建议：…（点下面的按钮确认后才会做）", offer=["linger", "rollback", "help", "sh"])
                return {"ok": False, "step": 5, "ticket": "st_" + "a" * 22, "text": "t", "offer": ["linger", "rollback", "sh"]}
        self.wd.kit = Kit
        from agentj import controls as c
        c.set_estop(self.st, True, "terminal")
        sent = []
        res = self.wd.do("help", "zh", lambda *a, **k: sent.append((a, k)))
        self.assertTrue(seen["estop"])
        self.assertEqual(seen["runs"], [(False, "not allowed")] * 3, "support's word never runs a persistent change")
        self.assertEqual(self.calls, [])
        self.assertEqual(res["offer"], ["linger", "rollback"])
        self.assertEqual(sent[0][1]["offer"], ["linger", "rollback", "help", "sh"], "the kit's emit gets the offer …")
        self.assertEqual(rw._offer(sent[0][1]["offer"]), ["linger", "rollback"], "… and the wire keeps only action names")

    def test_unknown_action_is_logged_as_unknown(self):
        class S:
            device, p33 = "dev1", True
        sent = []

        async def send_app(s, obj):
            sent.append(obj)
        self.wd.send_app = send_app
        evil = "rm -rf /\x1b[2J\nfake"
        asyncio.run(self.wd.on_request(S(), "rescue_do", {"t": "rescue_do", "r": "0" * 16, "action": evil}))
        self.assertEqual(sent[0]["why"], "shape")
        log = (self.st.root / "rescue.log").read_text()
        self.assertNotIn("rm -rf", log)
        self.assertIn('"action": "unknown"', log)

    def test_help_without_help_flow_degrades_to_report(self):
        class Kit:
            @staticmethod
            def report_only(lang="zh", tag="watchdog", quiet=True):
                return "st_" + "b" * 22, False, ""
        self.wd.kit = Kit
        res = self.wd.do("help", "en", lambda *a: None)
        self.assertEqual((res["ok"], res["ticket"]), (True, "st_" + "b" * 22))


class Units(Base):
    def setUp(self):
        super().setUp()
        self.st.init(relay="ws://127.0.0.1:9", web="https://m.agentj.app")
        home = pathlib.Path(self.tmp) / "home"
        home.mkdir()
        self.env = patch.dict(os.environ, {"HOME": str(home), "XDG_CONFIG_HOME": str(home / "cfg"), "PATH": "/usr/bin:/bin",
                                           "AGENTJ_SERVICE_NAME": "p332t", "AGENTJ_STATE_DIR": str(self.st.root)}, clear=True)
        self.env.start(); self.addCleanup(self.env.stop)
        self.home = home

    def test_names(self):
        with patch.object(service, "platform", return_value="linux"):
            self.assertEqual(service.rescue_name("agentj"), "agentj-rescue")
            self.assertEqual(service.rescue_name("agentj-3"), "agentj-3-rescue")
            self.assertEqual(service.rescue_name(), "p332t-rescue")
        with patch.object(service, "platform", return_value="macos"):
            self.assertEqual(service.rescue_name("net.agentj.host"), "net.agentj.rescue")
            self.assertEqual(service.rescue_name("net.agentj.host.3"), "net.agentj.rescue.3")
            self.assertEqual(service.rescue_name("p332t"), "p332t.rescue")

    def test_systemd_unit_is_independent_and_small(self):
        with patch.object(service, "platform", return_value="linux"):
            env = service.rescue_env()
            text = service.rescue_unit_text("agentj-rescue", service.rescue_argv(), env)
        self.assertIn("Restart=always", text)
        self.assertIn("WantedBy=default.target", text)
        self.assertRegex(text, r"MemoryMax=\d+M")
        self.assertIn("Nice=10", text)
        for bad in ("PartOf", "BindsTo", "Requires", "Requisite", "StopWhenUnneeded"):
            self.assertNotIn(bad, text)
        self.assertIn('"-P" "-m" "agentj.rescue_watchdog"', text)
        self.assertIn(f'Environment="AGENTJ_SERVICE_NAME=p332t"', text)
        self.assertIn(f'Environment="AGENTJ_STATE_DIR={self.st.root}"', text)
        self.assertNotRegex(text, "(?i)token|api_key|password")

    def test_launchd_plist(self):
        with patch.object(service, "platform", return_value="macos"):
            data = plistlib.loads(service.rescue_plist_bytes("net.agentj.rescue", service.rescue_argv(), service.rescue_env(),
                                                             "/x/rescue-service.log"))
        self.assertEqual(data["Label"], "net.agentj.rescue")
        self.assertIs(data["KeepAlive"], True)
        self.assertIs(data["RunAtLoad"], True)
        self.assertEqual(data["ProcessType"], "Background")
        self.assertEqual(data["ProgramArguments"][1:], ["-P", "-m", "agentj.rescue_watchdog"])

    def _fake_systemctl(self):
        calls = []

        def ctl(*args, check=False, timeout=30):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, "active\n" if args[0] == "is-active" else "", "")
        return calls, ctl

    def _install_patches(self, ctl):
        return [patch.object(service, "platform", return_value="linux"), patch.object(service, "_systemctl", ctl),
                patch.object(service, "_register_pair_protocol", lambda notes: None),
                patch.object(service, "_linger", lambda: "yes"),
                patch.object(service, "prepare_binary_env", lambda n, previous=None: ({"PATH": "/usr/bin:/bin"}, [])),
                patch.object(service, "remember_binary_selection", lambda st, n: None)]

    def test_install_registers_it_idempotently_and_uninstall_removes_it(self):
        calls, ctl = self._fake_systemctl()
        with contextlib.ExitStack() as es:
            for p in self._install_patches(ctl):
                es.enter_context(p)
            service.install(self.st)
            unit = pathlib.Path(service.unit_dir()) / "p332t-rescue.service"
            first = unit.read_text()
            service.install(self.st)
            self.assertEqual(unit.read_text(), first)
            self.assertEqual(sorted(os.listdir(service.unit_dir())), ["p332t-rescue.service", "p332t.service"])
            self.assertEqual(calls.count(("enable", "p332t-rescue.service")), 2)
            self.assertEqual(calls.count(("restart", "p332t-rescue.service")), 2)
            calls.clear()
            service.stop()                                    # an intentional stop silences the watchdog too (MEDIUM-2)
            self.assertEqual(calls[:2], [("stop", "p332t-rescue.service"), ("stop", "p332t.service")])
            calls.clear()
            with patch.object(service, "status", lambda: {"installed": True, "active": "inactive"}), \
                    patch.object(service, "_acknowledge_recovery", lambda st: None):
                service.start(self.st)                        # … and start brings it back
            self.assertEqual(calls, [("start", "p332t.service"), ("start", "p332t-rescue.service")])
            calls.clear()
            service.restart()
            self.assertEqual(calls, [("restart", "p332t.service"), ("start", "p332t-rescue.service")])
            calls.clear()
            unit.unlink()                                     # `agentj rescue uninstall`ed: start does not re-register it
            service.stop()
            with patch.object(service, "status", lambda: {"installed": True, "active": "active"}), \
                    patch.object(service, "_acknowledge_recovery", lambda st: None):
                service.start(self.st)
            self.assertEqual(calls, [("stop", "p332t.service")])
            service.install(self.st)
            self.assertTrue(unit.exists(), "install registers it again")
            service.uninstall(self.st)
            self.assertFalse(unit.exists())
            self.assertIn(("disable", "--now", "p332t-rescue.service"), calls)

    def test_a_watchdog_failure_never_fails_the_serve_install(self):
        calls, ctl = self._fake_systemctl()
        with contextlib.ExitStack() as es:
            for p in self._install_patches(ctl):
                es.enter_context(p)
            es.enter_context(patch.object(service, "install_rescue", side_effect=service.ServiceError("systemctl_failed")))
            r = service.install(self.st)
        self.assertTrue(any("agentj rescue install" in n for n in r["notes"]))

    def test_macos_first_registration_explains_the_login_item(self):
        notes = []
        with patch.object(service, "platform", return_value="macos"), \
                patch.object(service, "launchd_disabled", return_value=None), \
                patch.object(service, "_gui", return_value="gui/501"), \
                patch.object(service, "_bootstrap", return_value=subprocess.CompletedProcess([], 0, "", "")):
            service._rescue_note(self.st, notes)
            self.assertEqual(notes, [service.MAC_LOGIN_ITEM_NOTE])
            service._rescue_note(self.st, notes)              # an upgrade rewrites the plist: no second note
            self.assertTrue(service.rescue_path("p332t.rescue").startswith(str(self.home)))
        self.assertEqual(len(notes), 1)
        self.assertIn("Agent J 救援守护", notes[0])
        self.assertIn("rescue watchdog", notes[0])

    def test_upgrade_recovery_worker_registers_it(self):
        from agentj import service_recovery
        calls, ctl = self._fake_systemctl()
        with contextlib.ExitStack() as es:
            for p in self._install_patches(ctl):
                es.enter_context(p)
            es.enter_context(patch.object(service_recovery, "restart_siblings", lambda st, target: []))
            from agentj import __version__
            rc = service_recovery.run(self.st, __version__, ready=lambda st: "idle", status=lambda: {"active": "active"})
        self.assertEqual(rc, 0)
        self.assertTrue((pathlib.Path(service.unit_dir()) / "p332t-rescue.service").exists())
        self.assertIn(("restart", "p332t-rescue.service"), calls)

    def test_rescue_status_prints_no_secrets(self):
        from agentj import cli
        log = self.st.root / "rescue.log"
        log.write_text(json.dumps({"ts": 1, "ev": "request", "action": "restart", "result": "refused", "why": "replay",
                                   "sig": "SIGSECRETVALUE", "text": "message text secret", "key": "sk-ant-SECRET"}) + "\nnot json\n")
        with patch.object(service, "rescue_status", lambda: {"kind": "systemd", "name": "p332t-rescue", "path": "/x",
                                                             "installed": True, "active": "active"}):
            for js in (False, True):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    cli.cmd_rescue(type("A", (), {"mode": "status", "json": js})())
                o = out.getvalue()
                self.assertIn("p332t-rescue", o)
                self.assertIn("replay", o)
                self.assertNotIn("SECRET", o)
                self.assertNotIn("message text", o)
        # review P332 LOW-6: control characters never reach the terminal (raw or after JSON decoding)
        log.write_text(json.dumps({"ts": "x", "ev": "request\x1b[2J", "why": "a\u0007b\u007f\u0000c", "action": "\nfake line"}) + "\n")
        out = io.StringIO()
        with patch.object(service, "rescue_status", lambda: {"kind": "systemd", "name": "p332t-rescue", "path": "/x",
                                                             "installed": True, "active": "active"}), \
                contextlib.redirect_stdout(out):
            cli.cmd_rescue(type("A", (), {"mode": "status", "json": False})())
        o = out.getvalue()
        self.assertNotRegex(o, r"[\x00-\x08\x0b-\x1f\x7f]")
        self.assertIn("why=abc", o)
        self.assertIn("action=fake line", o)
        self.assertEqual(len(o.strip().splitlines()), 2, "one status line + one log line")


class NoServeImport(unittest.TestCase):
    def test_module_never_imports_serve_and_stays_small(self):
        code = ("import sys, resource; import agentj.rescue_watchdog as w; import agentj.names, agentj.seatcert, agentj.service;"
                "import websockets.asyncio.client;"
                "bad = sorted(m for m in sys.modules if m in ('agentj.serve', 'agentj.agent', 'agentj.shared', 'agentj.cli'));"
                "print(bad, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)")
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(HOST), timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        bad, rss = r.stdout.strip().rsplit(" ", 1)
        self.assertEqual(bad, "[]")
        self.assertLess(int(rss), 80 * 1024, "the watchdog's imports grew past 80 MB RSS")


def _run(wd):
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_until_complete, args=(wd.run(),), daemon=True)
    t.start()
    return loop, t


def _stop(wd, loop, t):
    loop.call_soon_threadsafe(wd.stopping.set)
    t.join(15)
    if not t.is_alive():
        loop.close()


def _wait(pred, t=10):
    end = time.monotonic() + t
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


class EndToEnd(Base):
    def setUp(self):
        super().setUp()
        import logging
        lg = logging.getLogger("websockets.server")     # the fake relay's handler ends with the 4001 it was sent: expected
        level, lg.level = lg.level, logging.CRITICAL
        self.addCleanup(setattr, lg, "level", level)

    def test_takeover_resume_reset_handback(self):
        with Relay4001() as relay:
            self.st.init(relay=relay.url, web="https://m.agentj.app")
            probe = {"v": "down"}
            restarted = []

            def restart():
                restarted.append(1)
                threading.Timer(0.2, lambda: probe.update(v="ok")).start()
                return True
            wd = rw.Watchdog(self.st, poll=0.1, probe_fn=lambda: probe["v"], restart_fn=restart, restart_wait=5,
                             lang_fn=lambda: "zh", stopped_fn=lambda: False)
            loop, t = _run(wd)
            ph, stranger, pairer = Phone(self.st, relay.url), Phone(self.st, relay.url, paired=False), None
            try:
                self.assertTrue(_wait(lambda: self.st.config()["channel"] in relay.hosts))
                ph.connect()
                ph.resume()
                ready = ph.app()
                self.assertEqual((ready["t"], ready["mode"]), ("ready", "rescue"))
                on = ph.app()
                self.assertEqual((on["t"], on["on"], on["why"]), ("rescue", True, "serve_down"))
                self.assertIn("restart", on["actions"])
                ph.app_send({"t": "ping", "r": "abc"})
                self.assertEqual(ph.until(lambda m: m["t"] == "pong"), {"t": "pong", "r": "abc"})
                ph.app_send({"t": "msg", "text": "hi"})          # anything else → rescue on again
                self.assertEqual(ph.until(lambda m: True)["t"], "rescue")
                # unsigned → refused, nothing runs
                ph.app_send({"t": "reset", "r": "1" * 16})
                ref = ph.until(lambda m: m["t"] == "reset_done")
                self.assertEqual((ref["ok"], ref["why"]), (False, "shape"))
                bad = ph.signed("rescue_do", "rescue_do", {"action": "sh"}, action="sh")
                ph.app_send(bad)
                self.assertEqual(ph.until(lambda m: m["t"] == "reset_done")["why"], "shape")
                self.assertEqual(restarted, [])
                # a stranger finishes no handshake; a pairing is refused
                stranger.connect()
                hs = Handshake(IK, True, stranger.kp, prologue=wire.resume_prologue(stranger.ch), rs=self.st.host_keypair().pub)
                stranger.ws.send(bytes([wire.RESUME_INIT]) + hs.write_message(b"{}"))
                self.assertTrue(stranger.closed())
                pairer = Phone(self.st, relay.url, paired=False)
                pairer.connect()
                ph_hs = Handshake(IKPSK2, True, pairer.kp, prologue=wire.pair_prologue(pairer.ch, b"\0" * 16),
                                  rs=self.st.host_keypair().pub, psk=b"\1" * 32)
                pairer.ws.send(bytes([wire.PAIR_INIT]) + b"\0" * 16 + ph_hs.write_message(b"{}"))
                self.assertTrue(pairer.closed())
                # the signed reset: restart, wait for status, done; replaying it is refused
                req = ph.signed("reset", "reset", {"what": "connection"})
                ph.app_send(req)
                first = ph.until(lambda m: m["t"] == "reset_step")
                self.assertEqual((first["r"], first["flow"], first["step"], first["state"]), (req["r"], "reset", 1, "run"))
                done = ph.until(lambda m: m["t"] == "reset_done", 15)
                self.assertEqual((done["r"], done["ok"], done["step"]), (req["r"], True, 2))
                self.assertEqual(restarted, [1])
                # serve answers twice → rescue off, socket handed back
                off = ph.until(lambda m: m["t"] == "rescue", 10)
                self.assertFalse(off["on"])
                self.assertTrue(_wait(lambda: not wd.active and self.st.config()["channel"] not in relay.hosts))
            finally:
                for p in (ph, stranger, pairer):
                    if p:
                        p.close()
                _stop(wd, loop, t)
            log = (self.st.root / "rescue.log").read_text()
            for ev in ('"takeover"', '"resume_ok"', '"accepted"', '"handback"', '"shape"', '"not_paired"', '"pairing"'):
                self.assertIn(ev, log)
            self.assertNotIn(req["sig"], log)
            self.assertNotIn("hi", [json.loads(x).get("text") for x in log.splitlines()])
            self.assertEqual(oct((self.st.root / "rescue.log").stat().st_mode & 0o777), "0o600")
            ctl = (self.st.root / "controls.log").read_text()
            self.assertIn('"action": "reset"', ctl)

    def test_replay_and_4001(self):
        with Relay4001() as relay:
            self.st.init(relay=relay.url, web="https://m.agentj.app")
            wd = rw.Watchdog(self.st, poll=0.1, probe_fn=lambda: "down", restart_fn=lambda: False, lang_fn=lambda: "en",
                             stopped_fn=lambda: False)
            loop, t = _run(wd)
            ph = Phone(self.st, relay.url)
            try:
                ch = self.st.config()["channel"]
                self.assertTrue(_wait(lambda: ch in relay.hosts))
                ph.connect(); ph.resume()
                ph.until(lambda m: m["t"] == "rescue")
                req = ph.signed("rescue_do", "rescue_do", {"action": "restart"}, action="restart")
                ph.app_send(req)
                done = ph.until(lambda m: m["t"] == "reset_done", 10)
                self.assertEqual((done["ok"], done["step"]), (False, 1))
                self.assertNotRegex(done["text"], "(?i)relay|serve|harness")
                ph.app_send(req)                                  # the same nonce again
                self.assertEqual(ph.until(lambda m: m["t"] == "reset_done")["why"], "replay")
                # serve's own link comes back: the relay replaces the watchdog's socket (4001)
                from websockets.sync.client import connect
                serve_ws = connect(f"{relay.url}/v1/host/{ch}").__enter__()
                n = json.loads(serve_ws.recv(5))["n"]
                sk = self.st.signing_key()
                serve_ws.send(json.dumps({"t": "auth", "pk": wire.b64u(self.st._ed_pub(sk)),
                                          "sig": wire.b64u(sk.sign(f"agentjarvis-relay-auth-v1\n{ch}\n{n}".encode()))}))
                self.assertEqual(json.loads(serve_ws.recv(5)), {"t": "ok"})
                self.assertTrue(_wait(lambda: '"code": 4001' in (self.st.root / "rescue.log").read_text()))
                # three fresh failures later it takes over again (probe still says down)
                self.assertTrue(_wait(lambda: relay.hosts.get(ch) is not serve_ws and wd.active and wd.ws is not None, 10))
                with contextlib.suppress(Exception):
                    serve_ws.close()
            finally:
                ph.close()
                _stop(wd, loop, t)
            lines = [json.loads(x) for x in (self.st.root / "rescue.log").read_text().splitlines()]
            evs = [x["ev"] for x in lines]
            i = next(i for i, x in enumerate(lines) if x.get("code") == 4001)
            self.assertIn("takeover", evs[i:])


if __name__ == "__main__":
    unittest.main()
