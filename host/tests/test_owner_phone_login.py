"""Paired phone ingress, native execution identity and the browser login CLI."""
import _hermetic  # noqa: F401

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import secrets
import shlex
import shutil
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from agentj import (browser, codex_npm, compose, privacy, provenance, shared, shared_turn_origin,
                    switch_harness, update)
from agentj.agent import ClaudeAgent
from agentj.agent_codex import CodexAgent
from agentj.agent_opencode2 import OpenCodeV2Agent
from agentj.browser_login import RequestPhone
from agentj.core.process_evidence import row
from agentj.shared import SharedClaudeAgent, SharedOpenCodeAgent
from agentj.shared_codex import SharedCodexAgent
from agentj.shared_opencode2 import switch_shared
from test_codex_npm_launch import NpmTree
from test_l1 import Phone, _host, _ready, _state


COMMAND = "agentj browser login channels-weixin --wait --json"
REFUSED = {"result": "refused", "why": "owner_phone_required"}
PASSED_GATE = {"result": "no_browser", "reason": "browser_unavailable"}

# The worker represents the runtime boundary. Real subprocesses below it invoke
# the installed CLI entry point against the Host's actual Unix socket.
NATIVE = """
import json
import subprocess
import sys

old = None
print('ready', flush=True)
try:
    for line in sys.stdin:
        args = json.loads(line)
        if isinstance(args, dict) and "start_old" in args:
            old = subprocess.Popen([sys.executable, "-c", args["start_old"]],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            print(old.stdout.readline().strip(), flush=True)
        elif isinstance(args, dict):
            old.stdin.write(json.dumps(args["old"]) + "\\n")
            old.stdin.flush()
            print(old.stdout.readline().strip(), flush=True)
        else:
            result = subprocess.run(args, capture_output=True, text=True)
            print(json.dumps([result.returncode, result.stdout, result.stderr]), flush=True)
finally:
    if old is not None:
        old.stdin.close()
        old.wait()
"""


# Minimal stdio app-server adapter for the launcher regression. Its CLI child
# is real and never reads account config. It accepts only the fixture's RPCs.
APP_SERVER = """
import json
import subprocess
import sys

for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    method = request["method"]
    if method == "initialize":
        result = {"userAgent": "codex/fixture"}
    elif method == "config/read":
        result = {"config": {}}
    elif method == "fixture/run":
        call = subprocess.run(request["params"]["argv"], capture_output=True, text=True)
        result = {"exit": call.returncode, "out": call.stdout}
    else:
        raise ValueError(method)
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
"""


@unittest.skipUnless(sys.platform == "linux" and shutil.which("bwrap"), "Linux harness process and bwrap")
class PhoneLogin(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="owner-login-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.events = []
        self.st = _state(self.root)
        self.host = _host(self.st, self.events)
        self.host.send_app = self.send_app
        self.host.emit = lambda *args, **kwargs: None
        self.phone = self.pair()
        self.pair(cid=12)
        self.enterContext(patch.object(self.host, "seat_refusal", return_value=None))
        self.enterContext(patch.object(self.host, "_setup_round_trip", AsyncMock()))
        self.enterContext(patch.object(self.host, "_model_set_flush", AsyncMock()))
        self.enterContext(patch.object(browser, "settings", return_value={"enabled": True}))
        self.runtime = self.enterContext(patch.object(browser, "runtime", return_value=None))
        await self.host.elevate.start()
        self.addAsyncCleanup(self.host.elevate.stop)

    async def send_app(self, session, frame, **kwargs):
        self.events.append((session.cid, frame))
        return True

    def pair(self, cid=11):
        phone = Phone(self.st)
        session = _ready(self.host, phone, cid)
        session.p33 = True
        session.sign_pub = self.st.sign_key(session.device)
        session.login_phone = RequestPhone.from_session(self.host, session)
        self.assertIsNotNone(session.login_phone)
        return session

    def cli_env(self):
        shim = self.root / "agentj"
        shim.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} -m agentj.cli \"$@\"\n")
        shim.chmod(0o700)
        return {
            "HOME": str(self.root),
            "PATH": str(self.root) + os.pathsep + os.environ["PATH"],
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
            "AGENTJ_TEST": "1",
            "AGENTJ_STATE_DIR": str(self.st.root),
            "AGENTJ_ELEVATE_SOCK": str(self.host.elevate.sock_path),
        }

    async def native(self, *, fenced=False, launcher=False):
        argv = [sys.executable, "-c", NATIVE]
        if launcher:
            argv = ["/bin/sh", "-c", '"$@"; ret=$?; exit "$ret"', "fixture-wrapper", *argv]
        if fenced:
            argv = ["bwrap", "--dev-bind", "/", "/", "--unshare-pid", "--as-pid-1", "--proc", "/proc", *argv]
        worker = await asyncio.create_subprocess_exec(
            *argv, env=self.cli_env(), cwd=self.root, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        self.addAsyncCleanup(self.end_native, worker)
        self.assertEqual(await asyncio.wait_for(worker.stdout.readline(), 4), b"ready\n")
        return worker

    async def end_native(self, worker):
        if worker.returncode is None:
            worker.stdin.close()
            try:
                await asyncio.wait_for(worker.wait(), 4)
            except TimeoutError:
                worker.kill()
                await worker.wait()
        self.assertEqual(worker.returncode, 0, (await worker.stderr.read()).decode())

    async def choose(self, harness, mode, *, launcher=False):
        fenced = mode == "isolated"
        cfg = dict(dir=str(self.root), kind=harness, fence=fenced,
                   session_mode="shared" if mode == "shared" else "independent")
        pair = {
            "codex": (CodexAgent, SharedCodexAgent),
            "claude": (ClaudeAgent, SharedClaudeAgent),
            "opencode": (OpenCodeV2Agent, SharedOpenCodeAgent),
        }[harness]
        agent = self.host.agent = pair[mode == "shared"](self.host, cfg)
        self.host.agent_cfg = cfg
        worker = await self.native(fenced=fenced, launcher=launcher)
        if harness == "codex":
            agent.proc = agent.turn_proc = worker
            agent.tid, agent.turn_id = "native-main", "native-turn"
            if mode == "shared":
                agent.cfg["shared_session_id"] = agent.tid
            agent.thread = {"sandbox": {"type": "dangerFullAccess"}}
            agent.quiet = True
            agent._busy("thread/status", {})
        elif harness == "claude" and mode == "shared":
            process = row(worker.pid)
            self.assertIsNotNone(process)
            agent.session = {"pid": worker.pid, "procStart": process[2], "sessionId": "fixture-session",
                             "messagingSocketPath": str(self.root / "native.sock")}
            agent.transcript = self.root / "fixture-transcript.jsonl"
            agent.transcript.write_text("")
            agent.offset = 0
            agent.bg_known = (worker.pid, process[2])
            self.enterContext(patch.object(shared, "claude_sessions", return_value=[agent.session]))
        else:
            agent.proc = worker
            if harness == "opencode":
                if mode == "shared":
                    switch_shared(agent, "2.0.23")
                    agent.owned_server = True
                agent.sid = "ses_fixture"
                agent._busy({})
        self.worker = worker
        return agent

    async def begin(self, agent, source=None, *, flush_native=True):
        if source is None:
            await self.host._app(self.phone, {"t": "say", "sid": secrets.token_urlsafe(16),
                                             "text": "恢复视频号登录", "ts": 0})
            self.assertFalse(agent.q.empty())
            send = agent.q.get_nowait()
            self.assertIs(send.phone_binding, self.phone.login_phone)
        else:
            send = compose.Send("fixture-system", secrets.token_urlsafe(16), 999, text="Synthetic completion", source=source)
        agent.cur_send = send
        self.host.agent_turn_start(send.text, send)
        send.state = "delivered"
        if isinstance(agent, SharedClaudeAgent) and source is None:
            agent.phone_turn = True
            agent.phone_seen = False
            agent.pending_source = agent.native_source = provenance.normalize(send.source)
            agent.pending_text = provenance.body(send.text, agent.pending_source)
            agent.native_request = shared_turn_origin.mint(agent, send, agent.pending_text)
            if flush_native:
                self.native_claude_record(agent)
                agent.read_transcript()
                self.assertTrue(agent.phone_seen)
        # Linux reports process birth in clock ticks. Do not collapse the test
        # child birth and the Host's executing-turn timestamp into one tick.
        await asyncio.sleep(.06)
        return send

    def native_claude_record(self, agent):
        att = agent.native_request
        record = {
            "type": "user", "sessionId": att.session_id, "isMeta": True,
            "uuid": secrets.token_hex(16), "timestamp": datetime.now(timezone.utc).isoformat(),
            "origin": {"kind": "peer", "msg_id": att.record_id},
            "message": {"role": "user", "content": provenance.envelope(
                agent.session["messagingSocketPath"], att.body, att.source)},
        }
        with agent.transcript.open("a") as out:
            out.write(json.dumps(record) + "\n")

    def assert_refused(self, result):
        self.assertEqual(result, {**REFUSED, "detail": browser.CHANNELS_OWNER_HINT})
        detail = result["detail"]
        self.assertIn("已配对手机", detail)
        self.assertIn("主对话", detail)
        self.assertIn("恢复视频号登录", detail)

    async def call(self, worker, argv=None):
        return await self.run_cli(worker, argv or shlex.split(COMMAND), 125)

    async def worker_request(self, worker, payload):
        worker.stdin.write((json.dumps(payload) + "\n").encode())
        await worker.stdin.drain()
        return await asyncio.wait_for(worker.stdout.readline(), 8)

    async def run_cli(self, worker, argv, expect_exit=0, *, as_json=True):
        exit_code, output, error = json.loads(await self.worker_request(worker, argv))
        self.assertEqual(exit_code, expect_exit, (output, error))
        return json.loads(output) if as_json else output.rstrip()

    async def record_codex(self, agent, *, thread=None, turn=None, cid="native-call",
                           command=COMMAND, actual=None, authenticated=True):
        from agentj import shared_risk
        thread = thread or agent.tid
        turn = turn or agent.turn_id
        guard = agent.risk if isinstance(agent, SharedCodexAgent) else shared_risk.NativePathGuard(agent)
        hook = {
            "_agentj_chain": [agent.proc.pid] if authenticated else [],
            "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command},
            "session_id": thread, "turn_id": turn, "tool_use_id": cid, "cwd": str(self.root),
        }
        guard.authentic(hook)
        await guard.hook_event(hook)
        agent._on_note("item/started", {"threadId": thread, "turnId": turn, "item": {
            "type": "commandExecution", "id": cid, "status": "inProgress",
            "command": "/bin/bash -lc " + shlex.quote(actual if actual is not None else command),
            "cwd": str(self.root),
        }})

    async def test_codex_owner_direct_and_child(self):
        for mode in ("main", "shared", "isolated"):
            with self.subTest(mode=mode):
                self.events.clear()
                agent = await self.choose("codex", mode)
                await self.begin(agent)
                await self.record_codex(agent, cid="first-call")
                self.assertEqual(await self.call(agent.proc), PASSED_GATE)
                agent._on_note("item/completed", {"threadId": agent.tid, "turnId": agent.turn_id, "item": {
                    "type": "commandExecution", "id": "first-call", "status": "failed", "exitCode": 125,
                }})
                self.events.clear()
                agent.turn_id = "second-native-turn"
                await self.begin(agent)
                await self.record_codex(agent)
                self.assertEqual(await self.call(agent.proc), PASSED_GATE)
                self.assertEqual([cid for cid, msg in self.events if msg.get("t") == "login_qr_err"], [self.phone.cid])
                self.events.clear()
                self.runtime.reset_mock()
                agent._on_note("item/completed", {"threadId": agent.tid, "turnId": agent.turn_id, "item": {
                    "type": "commandExecution", "id": "native-call", "status": "failed", "exitCode": 125,
                }})
                agent._on_note("thread/started", {"thread": {"id": "native-child", "source": {
                    "subAgent": {"thread_spawn": {"parent_thread_id": agent.tid, "depth": 1}},
                }}})
                agent._on_note("turn/started", {"threadId": "native-child", "turn": {"id": "child-turn"}})
                await self.record_codex(agent, thread="native-child", turn="child-turn", cid="child")
                self.assert_refused(await self.call(agent.proc))
                self.runtime.assert_not_called()
                self.assertEqual([msg["t"] for _, msg in self.events if msg.get("t") == "login_qr_err"], [])
                agent._on_note("turn/completed", {"threadId": "native-child",
                                                   "turn": {"id": "child-turn", "status": "completed"}})
                await self.record_codex(agent, cid="new-root-call")
                self.assertEqual(await self.call(agent.proc), PASSED_GATE)
                self.runtime.assert_called_once()

    @unittest.skipUnless(platform.machine() in ("x86_64", "amd64") and shutil.which("node"),
                         "x64 and Node for npm launcher regression")
    async def test_codex_npm_launcher_reaches_second_paired_main_turn(self):
        npm = NpmTree(self.root)
        _, native = npm.install_target(npm.base.parent)
        npm.js.write_text(
            "#!/usr/bin/env node\n"
            "const {spawn}=require('node:child_process');\n"
            f"const child=spawn({json.dumps(str(native))},process.argv.slice(2),{{stdio:'inherit'}});\n"
            "child.on('exit', code => process.exit(code === null ? 1 : code));\n"
        )
        (self.root / "app-server").write_text(APP_SERVER)
        cfg = dict(dir=str(self.root), kind="codex", fence=False, session_mode="independent")
        agent = self.host.agent = CodexAgent(self.host, cfg)
        self.host.agent_cfg = cfg
        env = {**self.cli_env(), "HOME": str(self.root / "home"), "AGENTJ_CODEX_BIN": str(npm.wrapper),
               "PYTHONHOME": sys.base_prefix}
        Path(env["HOME"]).mkdir()
        with patch.dict(os.environ, env, clear=True), patch.object(codex_npm, "LAUNCHERS", {npm.launcher_hash()}):
            self.assertTrue(await agent._spawn())
        self.addAsyncCleanup(agent._stop_control_paths)
        self.addAsyncCleanup(agent._kill, agent.proc)
        post_spawn_pid = agent.proc.pid
        agent.turn_proc = agent.proc
        agent.tid = "native-main"
        agent.thread = {"sandbox": {"type": "dangerFullAccess"}}
        agent.quiet = True
        for turn in ("first", "second"):
            with self.subTest(turn=turn):
                agent.turn_id = turn
                await self.begin(agent)
                await self.record_codex(agent, cid=turn)
                result = await agent.call("fixture/run", {"argv": shlex.split(COMMAND)}, timeout=8)
                self.assertEqual(result["exit"], 125, result)
                self.assertEqual(json.loads(result["out"]), PASSED_GATE)
                self.assertEqual(agent.proc.pid, post_spawn_pid)
                agent._on_note("item/completed", {"threadId": agent.tid, "turnId": turn, "item": {
                    "type": "commandExecution", "id": turn, "status": "failed", "exitCode": 125,
                }})

    async def test_codex_same_turn_background_shell_has_no_main_witness(self):
        agent = await self.choose("codex", "main")
        await self.begin(agent)
        self.assert_refused(await self.call(agent.proc))
        text = await self.run_cli(agent.proc, shlex.split(COMMAND)[:-1], 125, as_json=False)
        self.assertEqual(text.partition(" — ")[2], browser.CHANNELS_OWNER_HINT)
        self.runtime.assert_not_called()

    async def test_codex_existing_background_shell_cannot_borrow_a_new_main_witness(self):
        agent = await self.choose("codex", "main")
        await self.begin(agent)
        self.assertEqual(await self.worker_request(agent.proc, {"start_old": NATIVE}), b"ready\n")
        await self.record_codex(agent)
        self.assert_refused(await self.call(agent.proc, {"old": shlex.split(COMMAND)}))
        self.runtime.assert_not_called()
        self.assertEqual(await self.call(agent.proc), PASSED_GATE)
        self.assert_refused(await self.call(agent.proc))
        self.runtime.assert_called_once()

    async def test_codex_main_witness_requires_exact_authenticated_standalone_call(self):
        agent = await self.choose("codex", "main")
        await self.begin(agent)
        for cid, options in (
            ("compound", {"command": COMMAND + " &"}),
            ("wrong-item", {"actual": COMMAND.replace("channels-weixin", "other-site")}),
            ("unverified-hook", {"authenticated": False}),
        ):
            with self.subTest(case=cid):
                await self.record_codex(agent, cid=cid, **options)
                self.assert_refused(await self.call(agent.proc))
        self.runtime.assert_not_called()

    async def test_codex_unrecognized_persistent_wrapper_stays_refused(self):
        # An arbitrary persistent wrapper is not the native process pin.
        # No relaxation may let an old process borrow this newer phone turn.
        agent = await self.choose("codex", "main", launcher=True)
        await self.begin(agent)
        await self.record_codex(agent)
        self.assert_refused(await self.call(agent.proc))
        self.runtime.assert_not_called()

    async def test_codex_system_completion_refused(self):
        agent = await self.choose("codex", "main")
        previous = await self.begin(agent)
        callback = await self.begin(agent, provenance.system())
        # A Host callback can carry the old root for display. It never owns its authority.
        callback.approval_origin = previous.approval_origin.copy()
        await self.record_codex(agent)
        self.assert_refused(await self.call(agent.proc))
        self.runtime.assert_not_called()


    async def call_req(self, worker, req):
        code = (
            "from agentj import elevate; import json, os, sys; "
            "print(json.dumps(elevate.client_request(None, json.loads(sys.argv[1]), "
            "5, path=os.environ['AGENTJ_ELEVATE_SOCK'])))"
        )
        return await self.run_cli(worker, [sys.executable, "-c", code, json.dumps(req)])

    async def assert_browser_owner(self, harness):
        for mode in ("main", "shared", "isolated"):
            with self.subTest(harness=harness, mode=mode):
                self.events.clear()
                agent = await self.choose(harness, mode)
                self.runtime.reset_mock()
                await self.begin(agent)
                self.assertEqual(await self.call(self.worker), PASSED_GATE)
                self.runtime.assert_called_once()
                self.assertEqual([cid for cid, msg in self.events if msg.get("t") == "login_qr_err"], [self.phone.cid])
                self.runtime.reset_mock()
                await self.begin(agent, provenance.system())
                self.assert_refused(await self.call(self.worker))
                self.runtime.assert_not_called()

    async def test_claude_owner_direct_and_callback(self):
        await self.assert_browser_owner("claude")

    async def test_claude_shared_first_command_waits_for_native_record(self):
        agent = await self.choose("claude", "shared")
        await self.begin(agent, flush_native=False)
        pending = asyncio.create_task(self.call(self.worker))
        try:
            await asyncio.sleep(.25)
            self.assertFalse(agent.phone_seen)
            self.native_claude_record(agent)
            self.assertEqual(await pending, PASSED_GATE)
            self.assertTrue(agent.phone_seen)
            self.runtime.assert_called_once()
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)

    async def test_revoked_phone_cannot_reuse_the_original_request(self):
        agent = await self.choose("codex", "main")
        await self.begin(agent)
        await self.record_codex(agent)
        self.st.remove_device(self.phone.device)
        self.assert_refused(await self.call(agent.proc))
        self.runtime.assert_not_called()
        self.assertFalse([msg for _, msg in self.events if msg.get("t") == "login_qr_err"])

    async def test_opencode_owner_direct_and_callback(self):
        await self.assert_browser_owner("opencode")

    async def test_verified_phone_consumer_matrix(self):
        async def key_request(*args, _chat=None, **kwargs):
            return {"result": "fixture", "chat": _chat is not None}

        def record_switch_origin(*args, origin=None):
            return {"ok": False, "phone": origin is not None}

        with patch.object(self.host.elevate, "_request", side_effect=key_request), \
                patch.object(switch_harness, "host_job", side_effect=record_switch_origin), \
                patch.object(update, "check", return_value={"status": "newer", "latest": "1.2.3"}), \
                patch.object(privacy, "layer2", return_value=privacy.Verdict("ok")):
            for harness in ("codex", "opencode", "claude"):
                for mode in ("main", "shared", "isolated"):
                    with self.subTest(harness=harness, mode=mode):
                        agent = await self.choose(harness, mode)
                        for owner in (True, False):
                            with self.subTest(owner=owner):
                                await self.begin(agent, None if owner else provenance.system())
                                await self.assert_consumer_routes(owner)
                        # Each cell must own its own offer rather than reuse an
                        # offer opened by another harness in this test.
                        if self.host.upg:
                            self.host.upg["deadline"] = 0

    async def assert_consumer_routes(self, owner):
        key = await self.call_req(self.worker, {"t": "secret", "name": "FIXTURE_ONLY", "purpose": "QA",
                                              "dest": "file:" + str(self.root / "unused-secret-target"), "wait": 2})
        self.assertEqual(key, {"result": "fixture", "chat": owner})
        switch = await self.call_req(self.worker, {"t": "harness_job", "kind": "switch", "target": "opencode"})
        self.assertEqual(switch, {"ok": False, "phone": owner})
        feedback = await self.call_req(self.worker, {"t": "privacy_check", "gate": "feedback",
                                                   "draft": {"problem": "Synthetic feedback"}})
        self.assertEqual(feedback.get("status") == "ok", owner, feedback)
        upgrade = await self.call_req(self.worker, {"t": "upgrade", "op": "phone_offer", "version": "1.2.3"})
        self.assertIs(upgrade.get("ok"), owner, upgrade)
