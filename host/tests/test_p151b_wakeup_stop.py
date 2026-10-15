"""P151b: 0.18.4a1 still showed 「共享 Claude 未确认回合完成」 (owner, 10-12 11:02 → 11:07:23, turn_end secs 300.3).
Root cause in the real transcript + logs: that turn (phone message → Bash → Edit → answer only in the visible thinking →
ScheduleWakeup) DID get a native Stop hook (relay went idle at 11:02:45) — but with no last_assistant_message. AgentState
commits nothing for such a Stop, while P151 took any Stop as 「this turn has its committed end」 and so swallowed the
turn_duration path. Now: only a Stop that carries the reply counts; an empty Stop, the ScheduleWakeup result and
turn_duration each close the turn — exactly once. Plus 追加 4: the wait line, the stall card, the reason on the page."""
import _hermetic  # noqa: F401,I001
import asyncio
import unittest
from unittest.mock import patch

from agentj import shared
import test_p151_wakeup_stop as base
from test_p151_wakeup_stop import SID, asst, phone_msg, rec


def tool_use(name, ident, **inp):
    return {"type": "tool_use", "id": ident, "name": name, "input": inp}


def result(ident):
    return rec("user", message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": ident, "content": "ok"}]})


def end():
    return rec("system", subtype="turn_duration", durationMs=1000)


THOUGHT = "今天的想法都已整理成任务书并排队，有进展再汇报。"


class NotedHost:
    """The page-level hooks of serve.Host the agent calls (recorded)."""

    def __init__(self, host):
        self.h = host
        self.notes = []
        host.turn_wait_note = self.note

    def note(self, text, stall=False, final=False):
        self.notes.append((text, stall, final))


class Rig(unittest.IsolatedAsyncioTestCase):
    """base.WakeupStop's rig without its test methods."""
    asyncSetUp = base.WakeupStop.asyncSetUp
    write = base.WakeupStop.write
    phone_turn = base.WakeupStop.phone_turn
    submit = base.WakeupStop.submit
    settle = base.WakeupStop.settle


class EmptyStop(Rig):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        p = patch.object(shared, "EMPTY_STOP_WAIT", 0.1)
        p.start()
        self.addCleanup(p.stop)

    async def stop(self, message=""):
        await self.a.hook_event({"hook_event_name": "Stop", "session_id": SID, "last_assistant_message": message})

    async def test_leo_1102_empty_stop_then_schedulewakeup_then_turn_duration(self):
        """The real 11:02 order. Before: Stop (empty) set stop_since_end, turn_duration was swallowed, 300 s → 「未确认」."""
        task = await self.phone_turn("好的，暂时就这些想法了，你跟进吧。")
        await self.submit("好的，暂时就这些想法了，你跟进吧。")
        self.write(rec("user", isMeta=True, message={"role": "user", "content": phone_msg("好的，暂时就这些想法了，你跟进吧。")}),
                   asst("m1", tool_use("Bash", "t1", description="restart p154")), result("t1"),
                   asst("m2", tool_use("Edit", "t2", file_path="/x/PROMPT-154.md")), result("t2"),
                   asst("m3", {"type": "thinking", "thinking": THOUGHT, "signature": "s"}),
                   asst("m3", tool_use("ScheduleWakeup", "t3", reason="20 分钟心跳")))
        self.a.read_transcript()
        await self.stop("")                 # Claude Code's Stop with no last_assistant_message, before the tool result is read
        self.write(result("t3"), end())
        await self.settle(task)
        await asyncio.sleep(0.3)            # past every grace: nothing more may arrive
        self.a.read_transcript()
        self.assertEqual(self.host.phone, [THOUGHT])
        self.assertEqual(self.host.notices, [])
        self.assertFalse(self.host.failed)
        self.assertEqual(self.host.desktop, [], "delivered once")

    async def test_empty_stop_alone_closes_the_turn(self):
        """An empty Stop and no recorded end at all (the transcript stops there): EMPTY_STOP_WAIT later it is the end."""
        task = await self.phone_turn("x")
        await self.submit("x")
        self.write(asst("m4", {"type": "text", "text": "答复 A"}), asst("m4", tool_use("Monitor", "t4")))
        self.a.read_transcript()
        await self.stop("")
        await asyncio.wait_for(task, 2)
        self.assertEqual(self.host.phone, ["答复 A"])
        self.assertEqual(self.host.notices, [])

    async def test_schedulewakeup_result_closes_the_turn_without_stop_or_turn_duration(self):
        """The process stays alive waiting for its wakeup and writes nothing more: the tool result is the end."""
        task = await self.phone_turn("y")
        await self.submit("y")
        self.write(asst("m5", {"type": "text", "text": "正文 B"}), asst("m5", tool_use("ScheduleWakeup", "t5")), result("t5"))
        await self.settle(task)
        self.assertEqual(self.host.phone, ["正文 B"])
        self.assertEqual(self.host.notices, [])

    async def test_committed_stop_then_schedulewakeup_result_then_turn_duration_once(self):
        task = await self.phone_turn("z")
        await self.submit("z")
        self.write(asst("m6", {"type": "text", "text": "正文 C"}), asst("m6", tool_use("ScheduleWakeup", "t6")))
        self.a.read_transcript()
        await self.stop("正文 C")
        self.write(result("t6"), end())
        await self.settle(task)
        await asyncio.sleep(0.3)
        self.a.read_transcript()
        self.assertEqual(self.host.phone, ["正文 C"])
        self.assertEqual(self.host.desktop, [])

    async def test_tool_result_first_then_committed_stop_once(self):
        task = await self.phone_turn("w")
        await self.submit("w")
        self.write(asst("m7", {"type": "text", "text": "正文 D"}), asst("m7", tool_use("ScheduleWakeup", "t7")), result("t7"))
        self.a.read_transcript()
        await self.stop("正文 D")
        self.write(end())
        await self.settle(task)
        await asyncio.sleep(0.3)
        self.a.read_transcript()
        self.assertEqual(self.host.phone, ["正文 D"])
        self.assertEqual(self.host.desktop, [])

    async def test_fake_stop_held_for_a_foreground_agent_does_not_swallow_the_end(self):
        self.a.state._agents["fg-agent"] = None         # a foreground sub-agent AgentState still thinks is running
        task = await self.phone_turn("v")
        await self.submit("v")
        self.write(asst("m8", {"type": "text", "text": "正文 E"}), asst("m8", tool_use("Monitor", "t8")))
        self.a.read_transcript()
        await self.stop("正文 E")
        self.assertFalse(self.a.stop_since_end, "a held Stop is not the committed end")
        self.write(end())
        await self.settle(task)
        self.assertEqual(self.host.phone, ["正文 E"])

    async def test_next_turn_after_an_end_tool_still_ends(self):
        """turn_closed never leaks into the next turn."""
        task = await self.phone_turn("1")
        await self.submit("1")
        self.write(asst("m9", {"type": "text", "text": "一"}), asst("m9", tool_use("ScheduleWakeup", "t9")), result("t9"), end())
        await self.settle(task)
        task = await self.phone_turn("2")
        await self.submit("2")
        self.write(asst("m10", {"type": "text", "text": "二"}), asst("m10", tool_use("Monitor", "t10")), end())
        await self.settle(task)
        self.assertEqual(self.host.phone, ["一", "二"])

    async def test_text_after_the_end_tool_reaches_the_desktop_not_lost(self):
        task = await self.phone_turn("3")
        await self.submit("3")
        self.write(asst("m11", {"type": "text", "text": "先说"}), asst("m11", tool_use("ScheduleWakeup", "t11")), result("t11"))
        await self.settle(task)
        self.write(asst("m12", {"type": "text", "text": "补一句"}), end())
        await self.settle()
        await asyncio.sleep(0.2)
        self.assertEqual(self.host.phone, ["先说"])
        self.assertEqual(self.host.desktop, ["补一句"])


def peer_msg(body):
    """How Claude Code 2.1.296 writes the phone message (10-12 11:32): an isMeta user record, turnOrigin 'peer'."""
    return rec("user", isMeta=True, turnOrigin="peer", origin={"kind": "peer"},
               message={"role": "user", "content": "Another Claude session sent a message:\n" + phone_msg(body) + "\n\nThis came from…"})


class StreamedText(Rig):
    """追加 5: the turn's text reaches the phone the moment the session moves on to a tool call — how the turn ends no
    longer matters. The end sends only what is still new (no duplicate)."""
    stop = EmptyStop.stop

    async def asyncSetUp(self):
        await super().asyncSetUp()
        p = patch.object(shared, "EMPTY_STOP_WAIT", 0.1)
        p.start()
        self.addCleanup(p.stop)

    async def test_leo_1132_text_then_schedulewakeup_text_arrives_before_any_end(self):
        """The real 11:32 order (isMeta peer record → text → ScheduleWakeup → result → turn_duration; Stop without
        last_assistant_message). Before: 「（无文字回复）」 + 「未确认」 at 300 s."""
        task = await self.phone_turn("我扫码了")
        await self.submit("我扫码了")
        self.write(peer_msg("我扫码了"), asst("m1", {"type": "text", "text": "好的，谢谢。公众号登录态已恢复。"}),
                   asst("m1", tool_use("ScheduleWakeup", "t1", reason="心跳")))
        self.a.read_transcript()
        self.assertEqual(self.host.phone, ["好的，谢谢。公众号登录态已恢复。"], "on the phone before any end signal")
        self.assertFalse(task.done())
        await self.stop("")
        self.write(result("t1"), end())
        await self.settle(task)
        await asyncio.sleep(0.3)
        self.a.read_transcript()
        self.assertEqual(self.host.phone, ["好的，谢谢。公众号登录态已恢复。"], "once")
        self.assertEqual(self.host.notices, [])
        self.assertFalse(self.host.failed)

    async def test_no_end_signal_at_all_the_text_is_still_there_and_no_unconfirmed_card(self):
        with patch.object(shared, "TURN_WAIT", 0.2):
            task = await self.phone_turn("q")
            await self.submit("q")
            self.write(peer_msg("q"), asst("m2", {"type": "text", "text": "正文 Q"}), asst("m2", tool_use("Monitor", "t2")))
            self.a.read_transcript()
            await asyncio.wait_for(task, 3)
        self.assertEqual(self.host.phone, ["正文 Q"])
        self.assertFalse(self.host.failed)
        self.assertEqual(self.host.notices, [])

    async def test_segments_stack_and_the_final_stop_adds_only_the_new_one(self):
        task = await self.phone_turn("r")
        await self.submit("r")
        self.write(peer_msg("r"), asst("m3", {"type": "text", "text": "先看一下"}), asst("m3", tool_use("Bash", "t3")),
                   result("t3"), asst("m4", {"type": "text", "text": "查完了：一切正常"}))
        self.a.read_transcript()
        self.assertEqual(self.host.phone, ["先看一下"])
        await self.stop("查完了：一切正常")
        self.write(rec("system", subtype="stop_hook_summary"), end())
        await self.settle(task)
        await asyncio.sleep(0.2)
        self.a.read_transcript()
        self.assertEqual(self.host.phone, ["先看一下", "查完了：一切正常"])

    async def test_text_before_the_phone_input_is_never_streamed_to_the_phone(self):
        self.write(rec("user", turnOrigin="scheduled", message={"role": "user", "content": "# Autonomous loop tick"}),
                   asst("m5", {"type": "text", "text": "巡检旧文字"}))
        self.a.read_transcript()
        task = await self.phone_turn("s")
        await self.submit("s")
        self.write(asst("m5", tool_use("Bash", "t5")), result("t5"))     # the loop tick's tool after its text
        self.a.read_transcript()
        self.assertEqual(self.host.phone, [])
        self.write(peer_msg("s"), asst("m6", {"type": "text", "text": "回复 S"}), asst("m6", tool_use("ScheduleWakeup", "t6")),
                   result("t6"), end())
        await self.settle(task)
        self.assertEqual(self.host.phone, ["回复 S"])

    async def test_secret_in_a_streamed_segment_is_redacted(self):
        task = await self.phone_turn("t")
        await self.submit("t")
        key = "sk-" + "A1b2C3d4" * 6
        self.write(peer_msg("t"), asst("m7", {"type": "text", "text": "key 是 " + key}), asst("m7", tool_use("Bash", "t7")),
                   result("t7"), end())
        await self.settle(task)
        self.assertTrue(self.host.phone)
        self.assertNotIn(key, " ".join(self.host.phone))


class WaitLine(Rig):
    """追加 4: no reply for WAIT_NOTE_AFTER → the page says what the host sees; TURN_WAIT → stall card; reason at the end."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.noted = NotedHost(self.host)

    async def test_wait_line_names_the_tool_then_stall_then_reason_on_the_page(self):
        with patch.object(shared, "WAIT_NOTE_AFTER", 0.05), patch.object(shared, "TURN_WAIT", 0.4), \
                patch.object(shared.asyncio, "wait", _fast_wait):
            task = await self.phone_turn("等一下")
            await self.submit("等一下")
            self.write(asst("m1", tool_use("Bash", "t1", description="channels-login-guard",
                                           command="curl -H 'Authorization: Bearer sk-SECRET' https://x")))
            self.a.read_transcript()
            await asyncio.wait_for(task, 3)
        lines = [n for n in self.noted.notes if n[0]]
        self.assertIn("正在等待 Bash：channels-login-guard（已 1 分钟）", lines[0][0])
        self.assertTrue(any(n[1] for n in lines), "the stall card came")
        self.assertTrue(lines[-1][2], "the closing reason stays on the page")
        self.assertIn("没有确认这一回合结束", lines[-1][0])
        self.assertIn("Bash", lines[-1][0])
        self.assertTrue(self.host.failed)
        self.assertEqual(self.host.notices, [], "no separate notice page (「这一页没有你的输入」)")
        self.assertFalse(any("SECRET" in n[0] or "curl" in n[0] for n in lines))

    async def test_keep_waiting_extends_and_the_reply_still_lands(self):
        with patch.object(shared, "WAIT_NOTE_AFTER", 0.05), patch.object(shared, "TURN_WAIT", 0.3), \
                patch.object(shared.asyncio, "wait", _fast_wait):
            task = await self.phone_turn("长任务")
            await self.submit("长任务")
            self.write(asst("m2", tool_use("Bash", "t2", description="build")))
            self.a.read_transcript()
            for _ in range(60):
                await asyncio.sleep(0.02)
                if any(n[1] for n in self.noted.notes):
                    break
            self.assertIsNotNone(self.a.wait_more)
            self.a.wait_more.set()                 # 「继续等」
            await asyncio.sleep(0.15)
            self.assertFalse(task.done(), "still waiting after 继续等")
            self.write(result("t2"), asst("m3", {"type": "text", "text": "好了"}), end())
            await self.settle(task)
        self.assertEqual(self.host.phone, ["好了"])
        self.assertFalse(self.host.failed)
        self.assertIsNone(self.noted.notes[-1][0], "the wait line is withdrawn when the turn ends")

    async def test_empty_reply_notice_goes_on_the_page(self):
        task = await self.phone_turn("嗯")
        await self.submit("嗯")
        self.write(asst("m4", tool_use("ScheduleWakeup", "t4")), result("t4"), end())
        await self.settle(task)
        self.assertEqual(self.host.notices, [])
        self.assertIn(("共享 Claude 这一回合已结束，但没有写文字回复。", False, True), self.noted.notes)


async def _fast_wait(fs, timeout=None, return_when=None):
    return await _real_wait(fs, timeout=min(timeout or 0.05, 0.05), return_when=return_when)


_real_wait = asyncio.wait


class ToolWhat(unittest.TestCase):
    def test_never_a_command_line(self):
        self.assertEqual(shared.tool_what("Bash", {"command": "curl -H 'Authorization: Bearer sk-abc' https://x"}), "curl")
        self.assertEqual(shared.tool_what("Bash", {"command": "FOO=sk-abc ./run.sh"}), "")
        self.assertEqual(shared.tool_what("Bash", {"command": "x", "description": "channels-login-guard"}), "channels-login-guard")
        self.assertEqual(shared.tool_what("Edit", {"file_path": "/home/u/p/PROMPT-154.md"}), "PROMPT-154.md")
        self.assertEqual(shared.tool_what("WebFetch", {"url": "https://user:pw@example.com/a?k=1"}), "example.com")
        self.assertEqual(shared.tool_what("WebFetch", {"url": "https://example.com/a?k=1"}), "example.com")
        self.assertEqual(shared.tool_what("X", None), "")


class HostWaitNote(unittest.TestCase):
    """serve.Host.turn_wait_note on a real History page."""

    def host(self, root):
        from pathlib import Path
        from agentj.history import History
        from agentj.serve import Host
        from agentj.state import State

        class H:
            turn_wait_note = Host.turn_wait_note
            BUSY_NOTE = Host.BUSY_NOTE

            def __init__(self):
                self.lang, self.wait_note = "zh", None
                self.hist = History(State(Path(root)), on=True)

            def hist_update(self, tid, **kw):
                return self.hist.update(tid, **kw)
        h = H()
        h.cur_turn = h.hist.add({"k": "phone", "text": "hi"}, "", "open", None)["id"]
        return h

    def test_line_stall_withdraw_and_final(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            h = self.host(root)
            h.turn_wait_note("正在等待 Bash（已 1 分钟）")
            page = h.hist.get(h.cur_turn)
            self.assertEqual(page["reply"]["text"], "正在等待 Bash（已 1 分钟）")
            self.assertEqual(page["card"], {"wait": True})
            h.turn_wait_note("共享 Claude 还没结束这一回合：…", stall=True)
            self.assertEqual(h.hist.get(h.cur_turn)["card"], {"wait": True, "stall": True})
            h.turn_wait_note(None)
            self.assertEqual(h.hist.get(h.cur_turn)["reply"]["text"], "", "a wait line is withdrawn")
            h.turn_wait_note("这一回合已结束，但没有写文字回复。", final=True)
            h.turn_wait_note(None)
            self.assertEqual(h.hist.get(h.cur_turn)["reply"]["text"], "这一回合已结束，但没有写文字回复。", "a final line stays")

    def test_never_overwrites_a_reply(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            h = self.host(root)
            h.hist.update(h.cur_turn, append="真正的答复")
            h.turn_wait_note("正在等待 Bash（已 1 分钟）")
            self.assertEqual(h.hist.get(h.cur_turn)["reply"]["text"], "真正的答复")


if __name__ == "__main__":
    unittest.main()
