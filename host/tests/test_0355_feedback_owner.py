"""Shared phone feedback through Host ingress, native transcript and real CLI socket."""
import _hermetic  # noqa: F401
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch

from _testtmp import TEST_TMP
from test_feedback import DRAFT
from test_inline_media_stream import PNG
from test_l1 import Phone, _host, _ready, _state

from agentj import approval_material, compose, privacy, provenance, shared, shared_turn_origin, support
from agentj.browser_login import RequestPhone
from agentj.peer_origin import _stat


NATIVE = """
import json
import subprocess
import sys
for line in sys.stdin:
    call = json.loads(line)
    argv = ['-c', call['python'], call['arg']] if isinstance(call, dict) else ['-m', 'agentj.cli', *call]
    result = subprocess.run([sys.executable, *argv],
                            capture_output=True, text=True)
    print(json.dumps([result.returncode, result.stdout, result.stderr]), flush=True)
"""


@unittest.skipUnless(sys.platform == "linux", "Linux native process lineage")
class SharedFeedback(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory(dir=TEST_TMP, prefix="fb55-")
        self.addCleanup(tmp.cleanup)
        self.root = root = Path(tmp.name)
        self.st = _state(root)
        self.events = []
        self.host = host = _host(self.st, self.events)
        self.enterContext(patch.object(host, "seat_refusal", return_value=None))
        self.phone = Phone(self.st)
        self.remote = _ready(host, self.phone)
        self.remote.p33 = True
        self.remote.sign_pub = self.st.sign_key(self.remote.device)
        self.remote.login_phone = RequestPhone.from_session(host, self.remote)
        self.assertIsNotNone(self.remote.login_phone)
        self.draft = root / "draft.json"
        self.draft.write_text(json.dumps(DRAFT))
        cfg = {"dir": str(root), "kind": "claude", "session_mode": "shared", "fence": False}
        self.agent = host.agent = shared.SharedClaudeAgent(host, cfg)
        host.agent_cfg = cfg
        self.native_env = env = {
            "HOME": str(root),
            "PATH": os.environ["PATH"],
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
            "AGENTJ_TEST": "1",
            "AGENTJ_STATE_DIR": str(self.st.root),
            "AGENTJ_ELEVATE_SOCK": str(host.elevate.sock_path),
        }
        await host.elevate.start()
        self.addAsyncCleanup(host.elevate.stop)
        self.native = await asyncio.create_subprocess_exec(
            sys.executable, "-c", NATIVE, env=env, cwd=root, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        self.addAsyncCleanup(self.end_native)
        row = _stat(Path("/proc"), self.native.pid)
        self.agent.session = {"pid": self.native.pid, "procStart": row[2], "sessionId": "fixture-session",
                              "messagingSocketPath": str(root / "native.sock")}
        self.enterContext(patch.object(shared, "claude_sessions", side_effect=lambda *args: [self.agent.session]))
        self.agent.transcript = root / "transcript.jsonl"
        self.agent.transcript.touch()
        # Native's empty Stop was observed during attach. It has no retained work.
        self.agent.bg_known = (self.native.pid, row[2])
        self.host._nonowner_floor = approval_material._continuous() - 3
        self.reviews = []
        self.model_drafts = []
        self.posts = []
        self.enterContext(patch.object(privacy, "layer2", side_effect=self.review))
        self.enterContext(patch.object(support, "channel_for", return_value=self))

    signed = True
    ticket = "st_" + "a" * 22

    def review(self, draft, **kwargs):
        self.model_drafts.append(draft)
        self.reviews.append(kwargs)
        return privacy.Verdict("ok")

    def send(self, fields):
        self.posts.append(fields)
        return 201, {"thread": self.ticket, "seq": 1}

    async def end_native(self):
        if self.native.returncode is None:
            self.native.stdin.close()
            await asyncio.wait_for(self.native.wait(), 5)

    async def begin(self, source=None):
        agent = self.agent
        if source is None:
            await self.queue_owner()
            self.assertFalse(agent.q.empty(), self.events)
            send = agent.q.get_nowait()
        else:
            send = compose.Send("fixture-peer", secrets.token_urlsafe(16), 999, text="Same request from elsewhere", source=source)
        self.host.cur_turn = send.turn
        agent.cur_send = send
        self.host.agent_turn_start(send.text, send)
        agent.phone_turn = True
        agent.phone_seen = False
        agent.pending_source = agent.native_source = provenance.normalize(send.source)
        agent.pending_text = provenance.body(send.text, agent.pending_source)
        agent.native_request = shared_turn_origin.mint(agent, send, agent.pending_text)
        # Keep the CLI child's birth comfortably past the host monotonic stamp.
        await asyncio.sleep(.07)

    async def queue_owner(self):
        with patch.object(self.host, "_setup_round_trip", AsyncMock()), patch.object(self.host, "_model_set_flush", AsyncMock()):
            await self.host._app(self.remote, {"t": "say", "sid": secrets.token_urlsafe(16),
                                             "text": "Please send my diagnostic feedback.", "ts": 0})

    def native_record(self, *, record_id=True):
        att = self.agent.native_request
        item = {"type": "user", "sessionId": att.session_id, "isMeta": True, "uuid": secrets.token_hex(16),
                "timestamp": datetime.now(timezone.utc).isoformat(), "origin": {"kind": "peer"},
                "message": {"role": "user", "content": provenance.envelope(self.agent.session["messagingSocketPath"],
                                                                           att.body, att.source)}}
        if record_id:
            item["origin"]["msg_id"] = att.record_id
        with self.agent.transcript.open("a") as out:
            out.write(json.dumps(item) + "\n")

    async def native_exec(self, call):
        self.native.stdin.write((json.dumps(call) + "\n").encode())
        await self.native.stdin.drain()
        return json.loads(await asyncio.wait_for(self.native.stdout.readline(), 10))

    async def command(self, *argv):
        return await self.native_exec(argv)

    async def test_paired_shared_feedback_waits_for_exact_native_delivery(self):
        await self.begin()
        call = asyncio.create_task(self.command("support", "feedback", "Synthetic owner report", "--wait", "0"))
        await asyncio.sleep(.25)
        self.assertFalse(self.agent.phone_seen)
        self.native_record()
        exit_code, output, _ = await call
        self.assertEqual(exit_code, support.EXIT_PENDING, output)
        self.assertTrue(self.agent.phone_seen)
        self.assertIn(self.ticket, output)
        self.assertEqual(len(self.reviews), 1)
        self.assertEqual(len(self.posts), 1)

    async def test_missing_native_nonce_preserves_draft_without_sending(self):
        await self.begin()
        self.native_record(record_id=False)
        exit_code, output, _ = await self.command("support", "feedback", "Synthetic owner report", "--wait", "0")
        self.assertNotEqual(exit_code, 0)
        self.assertIn("peer_unverified", output)
        self.assertIn("原配对手机", output)
        self.assertEqual(self.reviews, [])
        self.assertEqual(self.posts, [])

    async def test_legacy_feedback_check_and_send_use_the_same_source_proof(self):
        await self.begin()
        self.native_record()
        exit_code, output, _ = await self.command("feedback", "check", str(self.draft))
        self.assertEqual(exit_code, 0, output)
        self.assertEqual(len(self.reviews), 1)
        self.assertEqual(self.posts, [])
        with patch.object(support.cloud, "read_cloud", return_value={"fixture": True}):
            exit_code, output, _ = await self.command("feedback", "send", str(self.root / "draft.checked.json"))
        self.assertEqual(exit_code, support.EXIT_PENDING, output)
        self.assertIn(self.ticket, output)
        self.assertEqual(len(self.posts), 1)

    async def test_revocation_during_privacy_review_cannot_send(self):
        await self.review_across_source_change("revoked")

    async def test_new_owner_turn_during_privacy_review_cannot_send_old_draft(self):
        await self.review_across_source_change("new_turn")

    async def review_across_source_change(self, scenario):
        await self.begin()
        self.native_record()
        ready, proceed = threading.Event(), threading.Event()
        def hold(draft, **kwargs):
            ready.set()
            if not proceed.wait(5):
                raise TimeoutError
            return self.review(draft, **kwargs)
        with patch.object(privacy, "layer2", side_effect=hold):
            call = asyncio.create_task(self.command("support", "feedback", "Synthetic owner report", "--wait", "0"))
            try:
                self.assertTrue(await asyncio.to_thread(ready.wait, 5))
                if scenario == "revoked":
                    revoked, _ = await self.host.revoke(self.remote.device)
                    self.assertTrue(revoked)
                else:
                    await self.begin()
            finally:
                proceed.set()
            exit_code, output, _ = await call
        self.assertEqual(exit_code, support.EXIT_UNAVAILABLE, output)
        self.assertIn("session_changed", output)
        self.assertEqual(self.posts, [])

    async def test_model_receives_only_host_redacted_draft(self):
        await self.begin()
        self.native_record()
        request = {"t": "privacy_check", "gate": "feedback", "draft": {"body": "contact@example.com"}}
        # Read from the real socket as a native CLI descendant. The raw
        # request deliberately skips the CLI's own deterministic report rules.
        worker = (
            "import json,sys; from agentj import elevate; from agentj.state import State; "
            "print(json.dumps(elevate.client_request(State(), json.loads(sys.argv[1]))))"
        )
        # Reuse the native actor's controlled exec without a second Host.
        exit_code, output, _ = await self.native_exec({"python": worker, "arg": json.dumps(request)})
        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(output)["status"], "ok")
        self.assertEqual(self.model_drafts, [{"body": "<email>"}])
        self.assertEqual(self.posts, [])

    async def test_file_show_retains_exact_native_admission_across_late_delivery(self):
        from agentj.peer_service import PeerService
        self.host.peers = PeerService(self.host)
        await self.begin()
        source = self.root / "fixture.png"
        source.write_bytes(PNG)
        call, tool, inp = "file-fixture-call", "Bash", {"command": f"agentj file show {source} --json"}
        pending = asyncio.create_task(self.agent._await_native_call(call))
        await asyncio.sleep(.1)
        self.native_record()
        with self.agent.transcript.open("a") as out:
            out.write(json.dumps({"type": "assistant", "uuid": "fixture-tool-record", "cwd": str(self.root),
                                  "message": {"role": "assistant", "id": "fixture-command", "content": [
                                      {"type": "tool_use", "id": call, "name": tool, "input": inp}
                                  ]}}) + "\n")
        await pending
        broker = self.host.peers.xfer.broker
        self.assertEqual((await broker.execute(tool, inp, call))["behavior"], "allow")
        exit_code, output, _ = await self.command("file", "show", str(source), "--json")
        self.assertEqual(exit_code, 0, output)
        self.assertEqual(json.loads(output)["result"], "queued")
        broker.complete(call, True, output)
        await broker.drain()
        page = self.host.hist.get(self.host.cur_turn)
        self.assertEqual(len(page.get("media") or []), 1)

    async def test_friend_group_unknown_cannot_send(self):
        for source in (provenance.HostDelegated({"kind": "friend_task", "role": "task"}), provenance.telegram_group(-42, 7, "proxy", 7),
                       {"kind": "unknown", "role": "unknown"}):
            with self.subTest(source=source):
                await self.begin(source)
                for args in (("support", "feedback", "Synthetic nonowner report", "--wait", "0"),
                             ("feedback", "send", str(self.draft))):
                    exit_code, output, _ = await self.command(*args)
                    self.assertNotEqual(exit_code, 0, output)
        self.assertEqual(self.reviews, [])
        self.assertEqual(self.posts, [])

    async def test_secret_pickup_card_keeps_original_phone_attribution(self):
        await self.begin()
        attachment = self.root / "synthetic-fixture.txt"
        attachment.write_text("Synthetic attachment")
        call = asyncio.create_task(self.command("secret", "send", "--name", "Fixture", "--file", str(attachment), "--json"))
        await asyncio.sleep(.25)
        self.native_record()
        exit_code, output, _ = await call
        self.assertEqual(exit_code, 0, output)
        card = self.host.elevate.out.cards[json.loads(output)["id"]]
        source = card["review_adapter"].envelope["source"]
        self.assertEqual((source["channel"], source["message_id"]), ("phone", self.agent.cur_send.turn))
        self.assertEqual(card["pickup_keys"][self.phone.did], self.st.sign_key(self.phone.did))

    async def test_merged_send_cannot_borrow_the_preceding_native_receipt(self):
        await self.begin()
        self.native_record()
        self.agent.read_transcript()
        self.assertTrue(self.agent.phone_seen)
        await self.queue_owner()
        with patch.object(self.agent, "deliver", AsyncMock()):
            await self.agent._merge_queued()
        self.assertFalse(self.agent.phone_seen)
        self.assertIs(self.agent._pending_phone_record().send, self.agent.cur_send)

    @unittest.skipUnless(shutil.which("bwrap"), "requires Linux bubblewrap")
    async def test_host_owned_feedback_has_exact_phone_proof_before_busy_board(self):
        await self.begin()
        await self.end_native()
        code = "import json,sys; print('ready',flush=True); " + NATIVE.strip()
        self.native = p = await asyncio.create_subprocess_exec(
            shutil.which("bwrap"), "--dev-bind", "/", "/", "--unshare-pid", "--as-pid-1", "--proc", "/proc",
            sys.executable, "-c", code, env=self.native_env, cwd=self.root,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        self.addAsyncCleanup(self.end_native)
        self.assertEqual(await asyncio.wait_for(p.stdout.readline(), 5), b"ready\n")
        self.agent.session = None
        self.agent.proc = p
        self.host.agent_turn_start(self.agent.cur_send.text, self.agent.cur_send)
        await asyncio.sleep(.07)
        exit_code, output, _ = await self.command("support", "feedback", "Synthetic owner report", "--wait", "0")
        self.assertEqual(exit_code, support.EXIT_PENDING, output)
        self.assertIn(self.ticket, output)
        self.assertEqual(len(self.posts), 1)
