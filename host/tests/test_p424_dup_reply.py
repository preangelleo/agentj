"""P424 (#87, owner 10-17 「我经常会连续收到你两条一模一样的消息」): the same reply on two pages — the second one an input-less
「后台任务完成」 page (src agent, via task). Host history evidence (10-19 17:27, 17:33; 10-20 11:51, 16:03): turn N (phone)
done with reply X, turn N+1 (agent · task) seconds later with the very same X, and `shared_turn_end_no_stop` logged between.

The race: the transcript's turn_duration is read first (the grace timer is armed with X) → the native Stop delivers X →
a background-task notification starts the next turn (stop_since_end reset) → the grace timer fired anyway and wrote X again
on a fresh agent page. A turn a committed Stop answered never ends twice; the record sequence mirrors the real transcript."""
import _hermetic  # noqa: F401,I001
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from test_p151_wakeup_stop import SID, WakeupStop, asst, rec

from agentj import shared
from agentj.history import History
from agentj.serve import Host


def end():
    return rec("system", subtype="turn_duration", durationMs=1000)   # a fresh uuid: each turn its own end record


class P424DupReply(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = WakeupStop.asyncSetUp
    write = WakeupStop.write
    phone_turn = WakeupStop.phone_turn
    submit = WakeupStop.submit
    settle = WakeupStop.settle

    def task_notification(self):
        return rec("user", origin={"kind": "task-notification"}, message={"role": "user", "content":
            "<task-notification>\n<task-id>bg1</task-id>\n<status>completed</status>\n</task-notification>"})

    async def test_stop_between_turn_end_and_task_notification_is_not_a_second_page(self):
        task = await self.phone_turn("旧函数清理了吗")
        await self.submit("旧函数清理了吗")
        self.write(asst("m1", {"type": "text", "text": "旧函数清理完了。"}), end())
        self.a.read_transcript()                    # turn_duration first: the grace timer holds 「旧函数清理完了。」
        await self.a.hook_event({"hook_event_name": "Stop", "session_id": SID, "last_assistant_message": "旧函数清理完了。"})
        self.write(self.task_notification())        # the background task's notification opens the next turn
        self.a.read_transcript()
        await self.settle(task)
        await asyncio.sleep(shared.TURN_END_GRACE * 3)
        self.assertEqual(self.host.phone, ["旧函数清理完了。"])
        self.assertEqual(self.host.desktop, [], "the same reply never lands again on a 「后台任务完成」 page")

    async def test_silent_task_turn_after_the_reply_adds_nothing(self):
        """贾维斯's pattern: reply → task notification → the Agent answers only 〔不回群〕 — no page carries X twice."""
        task = await self.phone_turn("进度？")
        await self.submit("进度？")
        self.write(asst("m2", {"type": "text", "text": "第一步做完了。"}), end())
        self.a.read_transcript()
        await self.a.hook_event({"hook_event_name": "Stop", "session_id": SID, "last_assistant_message": "第一步做完了。"})
        self.write(self.task_notification(), asst("m3", {"type": "text", "text": "〔不回群〕"}))
        self.a.read_transcript()
        await self.settle(task)
        await self.a.hook_event({"hook_event_name": "Stop", "session_id": SID, "last_assistant_message": "〔不回群〕"})
        self.write(end())
        self.a.read_transcript()
        await asyncio.sleep(shared.TURN_END_GRACE * 3)
        self.assertEqual(self.host.phone, ["第一步做完了。"])
        self.assertNotIn("第一步做完了。", self.host.desktop)

    async def test_a_task_turn_without_stop_still_reaches_the_phone(self):
        """The guard is per turn: a later background-task turn that ends without Stop still gets its own page."""
        task = await self.phone_turn("好了吗")
        await self.submit("好了吗")
        self.write(asst("m4", {"type": "text", "text": "好了。"}), end())
        self.a.read_transcript()
        await self.a.hook_event({"hook_event_name": "Stop", "session_id": SID, "last_assistant_message": "好了。"})
        await self.settle(task)
        await asyncio.sleep(shared.TURN_END_GRACE * 3)
        self.write(self.task_notification(), asst("m5", {"type": "text", "text": "后台测试也通过了。"}),
                   asst("m5", {"type": "tool_use", "id": "toolu_w", "name": "ScheduleWakeup", "input": {}}), end())
        self.a.read_transcript()
        await asyncio.sleep(shared.TURN_END_GRACE * 3)
        self.assertEqual(self.host.phone, ["好了。"])
        self.assertEqual(self.host.desktop, ["后台测试也通过了。"])



class EchoPageGuard(unittest.TestCase):
    """The host's push side: whatever harness ends a turn twice, an input-less page that repeats the newest finished page
    word for word is not stored or pushed (one id per reply); a different reply, or an open page, still gets its page."""
    def host(self):
        tmp = tempfile.TemporaryDirectory(prefix="p424-")
        self.addCleanup(tmp.cleanup)
        st = SimpleNamespace(root=Path(tmp.name), config=lambda: {"history": "off"}, log=Mock())
        h = SimpleNamespace(st=st, hist=History(st, on=False), desktop_turn=None, lang="zh", added=[],
                            media_later=lambda tid: None)
        h.hist_add = lambda src, text, end, card=None: (h.added.append(text), h.hist.add(src, text, end))[1]
        for name in ("proactive_turn", "_agent_src", "desktop_text", "desktop_end"):
            setattr(h, name, getattr(Host, name).__get__(h))
        h.hist_update = lambda tid, **kw: h.hist.update(tid, **kw)
        return h

    def test_repeat_of_the_newest_finished_reply_is_dropped(self):
        h = self.host()
        h.hist.add({"k": "phone", "text": "旧函数呢？"}, "旧函数清理完了。", "done")
        h.proactive_turn("task")
        h.desktop_text("旧函数清理完了。")
        self.assertEqual(h.added, [])
        self.assertEqual(len(h.hist.turns), 1)
        h.st.log.assert_any_call("hist_echo_dropped", turn=1)

    def test_a_new_reply_or_an_open_page_still_gets_its_page(self):
        h = self.host()
        h.hist.add({"k": "phone", "text": "a"}, "答复一", "done")
        h.proactive_turn("task")
        h.desktop_text("后台任务完成：报告已生成。")
        self.assertEqual(h.added, ["后台任务完成：报告已生成。"])
        h.hist.add({"k": "phone", "text": "b"}, "同一句", "open")
        h.desktop_end()
        h.desktop_text("同一句")                # the newest page is still open: not a finished repeat
        self.assertEqual(h.added[-1], "同一句")


if __name__ == "__main__":
    unittest.main()
