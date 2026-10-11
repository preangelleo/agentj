"""P151: a turn that ends on a tool call (ScheduleWakeup, Monitor, …) sends no native Stop hook — Claude Code 2.1.296 writes
no stop_hook_summary for it, only system/turn_duration (owner report 2026-10-12: phone page got 「共享 Claude 未确认回合完成」 at
01:26, the reply never arrived). The recorded turn end now commits the turn with its last assistant text; a real Stop
keeps priority and is never doubled; a turn with no text gets an honest notice, not 「未确认」. The record sequences below
mirror the real transcript (structure only)."""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from agentj import provenance, shared
from agentj.shared_state import AgentState

SID = "15115115-1511-1511-1511-151151151151"


class Host:
    lang = "zh"

    def __init__(self):
        self.st = Mock()
        self.inputs, self.desktop, self.phone, self.notices, self.ends, self.queued = [], [], [], [], 0, []
        self.failed = False

    def desktop_input(self, text): self.inputs.append(text)
    def desktop_text(self, text): self.desktop.append(text)
    def desktop_end(self): self.ends += 1
    def proactive_turn(self, via): pass
    def agent_text(self, text): self.phone.append(text)
    def agent_notice(self, text): self.notices.append(text)
    def agent_status(self, status): pass
    def turn_failed(self): self.failed = True
    def turn_queued_note(self, on): self.queued.append(on)


def rec(kind, **kw):
    return {"type": kind, "uuid": os.urandom(8).hex(), "timestamp": "2026-10-11T16:21:00Z", **kw}


def asst(mid, *blocks):
    return rec("assistant", message={"id": mid, "role": "assistant", "content": list(blocks)})


def tool_use(name):
    return {"type": "tool_use", "id": "toolu_" + name, "name": name, "input": {}}


def phone_msg(body):
    return ('<cross-session-message from="uds:/run/x.sock" from-name="owner-via-agentj(手机)" source-kind="phone" '
            'source-role="owner">\n' + body + "\n</cross-session-message>")


END = rec("system", subtype="turn_duration", durationMs=1000)
STOP_SUMMARY = rec("system", subtype="stop_hook_summary")


class WakeupStop(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p151-")
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.work = root / "work"
        self.work.mkdir()
        cc = root / "claude"
        (cc / "sessions").mkdir(parents=True)
        env = patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(cc)})
        env.start()
        self.addCleanup(env.stop)
        self.registry = cc / "sessions" / "owner.json"
        self.session = {"pid": os.getpid(), "cwd": str(self.work), "sessionId": SID, "kind": "interactive",
                        "messagingSocketPath": str(root / "peer.sock"), "status": "busy", "updatedAt": 10}
        import socket
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.bind(str(root / "peer.sock"))
        self.addCleanup(self.sock.close)
        self.registry.write_text(json.dumps(self.session))
        slug = re.sub(r"[^A-Za-z0-9]", "-", str(self.work))
        self.transcript = cc / "projects" / slug / (SID + ".jsonl")
        self.transcript.parent.mkdir(parents=True)
        self.transcript.write_text("")
        self.host = Host()
        p = patch.object(shared.SharedClaudeAgent, "prepare_channel", new=AsyncMock())
        p.start()
        self.addCleanup(p.stop)
        g = patch.object(shared, "TURN_END_GRACE", 0.05)
        g.start()
        self.addCleanup(g.stop)
        self.a = shared.SharedClaudeAgent(self.host, {"kind": "claude", "dir": str(self.work), "_workflow_ceo": True,
                                                      "session_mode": "shared", "high_risk_warnings": False})
        self.a.session = dict(self.session)
        self.a.transcript = self.transcript
        self.a.loop = asyncio.get_running_loop()

    def write(self, *records):
        with self.transcript.open("a") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    async def phone_turn(self, body, source=None):
        """Starts a phone turn the way Agent J does; returns the task. The native session is played by the test."""
        from types import SimpleNamespace
        self.a.cur_send = SimpleNamespace(source=source, lock=asyncio.Lock(), state="pending")
        with patch.object(shared, "claude_send"):
            task = asyncio.create_task(self.a.turn(body))
            await asyncio.sleep(0.02)
        return task

    async def submit(self, body):
        await self.a.hook_event({"hook_event_name": "UserPromptSubmit", "session_id": SID, "prompt": phone_msg(body)})

    async def settle(self, task=None):
        for _ in range(40):
            self.a.read_transcript()
            await asyncio.sleep(0.02)
            if task is not None and task.done():
                break
        if task is not None:
            await asyncio.wait_for(task, 2)

    async def test_leo_0121_reply_then_schedulewakeup_inside_a_loop_tick(self):
        """The real 01:21 order: loop tick running → phone message taken as a queued command (UserPromptSubmit fires) →
        the answer → ScheduleWakeup → turn_duration, no Stop. The phone page gets the answer; no 「未确认」 card."""
        self.write(rec("user", turnOrigin="scheduled", message={"role": "user", "content": "# Autonomous loop tick"}),
                   asst("m1", tool_use("Bash")), asst("m1", {"type": "text", "text": "巡检中的旧文字"}))
        self.a.read_transcript()
        task = await self.phone_turn("要多久可以看到设计")
        await self.submit("要多久可以看到设计")
        self.write(rec("attachment", attachment={"type": "queued_command", "prompt": phone_msg("要多久可以看到设计")}),
                   asst("m2", {"type": "thinking", "thinking": "", "signature": "s"}),
                   asst("m2", {"type": "text", "text": "设计稿预计 02:30 前给你 review 网址。"}),
                   asst("m2", tool_use("ScheduleWakeup")),
                   rec("user", message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_ScheduleWakeup",
                                                                   "content": "Next wakeup scheduled"}]}),
                   END)
        await self.settle(task)
        self.assertEqual(self.host.phone, ["设计稿预计 02:30 前给你 review 网址。"])
        self.assertEqual(self.host.notices, [])
        self.assertFalse(self.host.failed)
        self.assertEqual(self.host.desktop, [], "nothing duplicated onto a desktop page")
        self.assertFalse(self.a.native_turn, "the recorded end closes the native turn")

    async def test_answer_only_in_thinking_before_schedulewakeup_owner(self):
        """What the 01:21 transcript actually held: no text block, the answer in the visible thinking of the final message."""
        task = await self.phone_turn("要多久")
        await self.submit("要多久")
        self.write(rec("user", message={"role": "user", "content": phone_msg("要多久")}),
                   asst("m3", {"type": "thinking", "thinking": "预计 1 到 1.5 小时，02:30 左右给 review 网址。", "signature": "s"}),
                   asst("m3", tool_use("ScheduleWakeup")), END)
        await self.settle(task)
        self.assertEqual(self.host.phone, ["预计 1 到 1.5 小时，02:30 左右给 review 网址。"])
        self.assertEqual(self.host.notices, [])

    async def test_thinking_never_reaches_a_group_member(self):
        src = provenance.telegram_group(-100, 42, "family", 7)
        task = await self.phone_turn("hi", source=src)
        self.a.phone_seen = True
        self.a.input_seen.set()
        self.write(asst("m4", {"type": "thinking", "thinking": "内部推理", "signature": "s"}), asst("m4", tool_use("ScheduleWakeup")), END)
        await self.settle(task)
        self.assertEqual(self.host.phone, [])
        self.assertEqual(len(self.host.notices), 1)
        self.assertIn("没有写文字回复", self.host.notices[0])

    async def test_no_text_at_all_is_an_honest_notice_not_unconfirmed(self):
        task = await self.phone_turn("嗯")
        await self.submit("嗯")
        self.write(asst("m5", tool_use("ScheduleWakeup")), END)
        await self.settle(task)
        self.assertEqual(self.host.phone, [])
        self.assertEqual(self.host.notices, ["共享 Claude 这一回合已结束，但没有写文字回复。"])
        self.assertFalse(self.host.failed)

    async def test_committed_stop_keeps_priority_and_is_not_doubled(self):
        task = await self.phone_turn("ok?")
        await self.submit("ok?")
        self.write(asst("m6", {"type": "text", "text": "done"}))
        self.a.read_transcript()
        await self.a.hook_event({"hook_event_name": "Stop", "session_id": SID, "last_assistant_message": "done"})
        self.write(STOP_SUMMARY, END)
        await self.settle(task)
        await asyncio.sleep(0.15)
        self.a.read_transcript()
        self.assertEqual(self.host.phone, ["done"])
        self.assertEqual(self.host.desktop, [])

    async def test_late_stop_after_recorded_end_is_dropped(self):
        task = await self.phone_turn("late?")
        await self.submit("late?")
        self.write(asst("m7", {"type": "text", "text": "answer"}), asst("m7", tool_use("Monitor")), END)
        await self.settle(task)
        self.a._finish_stop("answer")       # a Stop that would arrive after the turn end
        self.assertEqual(self.host.phone, ["answer"])
        self.assertEqual(self.host.desktop, [], "the late Stop does not repeat the reply on a desktop page")

    async def test_desktop_loop_tick_ending_on_schedulewakeup_ends_its_page(self):
        self.write(rec("user", turnOrigin="scheduled", message={"role": "user", "content": "# Autonomous loop tick"}),
                   asst("m8", {"type": "text", "text": "巡检完成，无需处理。"}), asst("m8", tool_use("ScheduleWakeup")), END)
        await self.settle()
        await asyncio.sleep(0.15)
        self.assertEqual(self.host.desktop, ["巡检完成，无需处理。"])
        self.assertEqual(self.host.ends, 1)
        self.assertFalse(self.a.native_turn)

    async def test_text_from_before_the_phone_input_is_not_its_reply(self):
        self.write(rec("user", turnOrigin="scheduled", message={"role": "user", "content": "# tick"}),
                   asst("m9", {"type": "text", "text": "tick 前半段"}))
        self.a.read_transcript()
        task = await self.phone_turn("问题")
        await self.submit("问题")
        self.write(rec("attachment", attachment={"type": "queued_command", "prompt": phone_msg("问题")}),
                   asst("m10", tool_use("ScheduleWakeup")), END)
        await self.settle(task)
        self.assertEqual(self.host.phone, [])
        self.assertIn("没有写文字回复", self.host.notices[0])

    async def test_input_not_taken_still_names_the_reason(self):
        """P127 unchanged: a socket write is not acceptance — no input seen, no turn end claimed."""
        task = await self.phone_turn("never seen")
        self.write(asst("m11", {"type": "text", "text": "unrelated desktop answer"}), END)
        self.a.read_transcript()
        await asyncio.sleep(0.15)
        self.assertEqual(self.host.phone, [], "a recorded end before the phone input was taken answers nothing on the phone")
        task.cancel()
        with __import__("contextlib").suppress(asyncio.CancelledError):
            await task

    async def test_timeout_registry_idle_with_text_delivers_it(self):
        """An older native version without turn_duration: at the 300 s limit, a reply after the input + registry idle
        delivers it instead of 「未确认」."""
        self.session["status"] = "idle"
        self.registry.write_text(json.dumps(self.session))
        task = await self.phone_turn("old")
        await self.submit("old")
        self.write(rec("user", message={"role": "user", "content": phone_msg("old")}),
                   asst("m12", {"type": "text", "text": "old-version reply"}), asst("m12", tool_use("ScheduleWakeup")))
        self.a.read_transcript()
        real_wait = asyncio.wait_for

        async def short(aw, timeout):
            return await real_wait(aw, 0.2 if timeout == 300 else timeout)
        with patch.object(shared.asyncio, "wait_for", short):
            await asyncio.wait_for(task, 3)
        self.assertEqual(self.host.phone, ["old-version reply"])
        self.assertFalse(self.host.failed)

    async def test_timeout_without_text_still_unconfirmed(self):
        task = await self.phone_turn("silent")
        await self.submit("silent")
        real_wait = asyncio.wait_for

        async def short(aw, timeout):
            return await real_wait(aw, 0.2 if timeout == 300 else timeout)
        with patch.object(shared.asyncio, "wait_for", short):
            await asyncio.wait_for(task, 3)
        self.assertTrue(self.host.failed)
        self.assertIn("未确认回合完成", self.host.notices[0])


class StateEndWithoutStop(unittest.TestCase):
    def test_status_goes_idle_with_the_reply_as_last_message(self):
        from agentj import shared_state
        with patch.object(shared_state.config, "IDLE_DEBOUNCE_S", 0.01):
            s = AgentState()
            s.handle({"hook_event_name": "UserPromptSubmit", "session_id": SID})
            s.handle({"hook_event_name": "PreToolUse", "session_id": SID, "tool_name": "ScheduleWakeup"})
            self.assertEqual(s.status, "working")
            s.end_without_stop("答复")
            import time
            time.sleep(0.1)
            self.assertEqual(s.status, "idle")
            self.assertEqual(s.last_message, "答复")


class HookContextAsksForText(unittest.TestCase):
    def test_owner_and_group_contexts_ask_for_text_before_schedulewakeup(self):
        for src in (None, {"kind": "telegram", "role": "group", "profile": "family", "sender": 1}):
            ctx = provenance.hook_context(src)
            self.assertIn("ScheduleWakeup", ctx)
            self.assertIn("not only in thinking", ctx)


if __name__ == "__main__":
    unittest.main()
