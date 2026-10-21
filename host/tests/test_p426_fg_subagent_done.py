"""P426 拍板 2: a foreground sub-agent's end does not wake the main Agent again (Claude and OpenCode alike); background
sub-agents / shells still end with 「后台活已结束」."""
import _hermetic  # noqa: F401
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from agentj import background_done as done, busy


class FgSubagent(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.a = SimpleNamespace(status="idle", q=asyncio.Queue(), is_down=lambda: False,
                                 native_busy=lambda: False, cfg={"session_mode": "shared"})
        self.a.submit = self.a.q.put_nowait
        self.b = busy.Board(root)
        self.h = SimpleNamespace(st=SimpleNamespace(root=root, log=Mock()), lang="zh", agent=self.a, preferences={},
                                 busy=self.b, stopped=lambda: False, _open_question=lambda: False,
                                 hist_add=Mock(return_value={"id": 1}))
        self.n = done.Notifier(self.h)

    def woke(self):
        send = self.n.tick(now=10_000)
        return send is not None and "已结束" in send.text and send.by == "background_done"

    # -- Claude, the owned stream
    def tool_use(self, tid, bg=False):
        inp = {"subagent_type": "Explore", "description": "找文件", **({"run_in_background": True} if bg else {})}
        self.b.stream_event({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": tid, "name": "Agent", "input": inp}]}})

    def tool_result(self, tid, text):
        self.b.stream_event({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": tid, "content": text}]}})

    def test_claude_own_foreground_does_not_wake(self):
        self.tool_use("tu1")
        self.assertEqual(len(self.b.items()), 1)
        self.tool_result("tu1", "found 3 files")
        self.assertEqual(self.b.items(), [])
        self.assertFalse(self.woke())
        self.assertEqual(self.n.pending, {})

    def test_claude_own_background_still_wakes(self):
        self.tool_use("tu2", bg=True)
        self.tool_result("tu2", "Async agent launched successfully. agentId: abcd1234")
        self.b.stream_event({"type": "system", "subtype": "task_notification", "task_id": "abcd1234", "status": "completed"})
        self.assertTrue(self.woke())

    # -- Claude, shared sessions' hooks
    def hook(self, **ev):
        self.b.hook_event({"session_id": "s1", **ev}, scope="shared")

    def test_claude_hooks_foreground_does_not_wake(self):
        self.hook(hook_event_name="PreToolUse", tool_name="Agent", tool_input={"subagent_type": "Explore", "description": "x"})
        self.hook(hook_event_name="SubagentStart", agent_id="a1", agent_type="Explore")
        self.hook(hook_event_name="SubagentStop", agent_id="a1")
        self.assertFalse(self.woke())

    def test_claude_hooks_background_still_wakes(self):
        self.hook(hook_event_name="PreToolUse", tool_name="Agent",
                  tool_input={"subagent_type": "Explore", "description": "x", "run_in_background": True})
        self.hook(hook_event_name="SubagentStart", agent_id="a2", agent_type="Explore")
        self.hook(hook_event_name="SubagentStop", agent_id="a2")
        self.assertTrue(self.woke())

    def test_claude_hooks_async_launch_marks_background(self):
        self.hook(hook_event_name="PreToolUse", tool_name="Agent", tool_input={"subagent_type": "Explore", "description": "x"})
        self.hook(hook_event_name="SubagentStart", agent_id="a3", agent_type="Explore")
        self.hook(hook_event_name="PostToolUse", tool_name="Agent", tool_input={"subagent_type": "Explore"},
                  tool_response={"agentId": "a3", "isAsync": True})
        self.hook(hook_event_name="Stop", background_tasks=[{"id": "a3", "type": "subagent", "status": "completed"}])
        self.assertTrue(self.woke())

    def test_claude_hooks_turn_stop_ends_foreground_quietly(self):
        self.hook(hook_event_name="PreToolUse", tool_name="Agent", tool_input={"subagent_type": "Explore", "description": "x"})
        self.hook(hook_event_name="SubagentStart", agent_id="a4", agent_type="Explore")
        self.hook(hook_event_name="Stop", background_tasks=[])
        self.assertEqual(self.b.items(), [])
        self.assertFalse(self.woke())

    def test_background_shell_still_wakes(self):
        self.hook(hook_event_name="PostToolUse", tool_name="Bash", tool_input={"description": "build", "command": "make"},
                  tool_response={"backgroundTaskId": "b1"})
        self.hook(hook_event_name="Stop", background_tasks=[{"id": "b1", "type": "shell", "status": "completed"}])
        self.assertTrue(self.woke())

    # -- OpenCode v1 / v2: ended inside our turn = foreground
    def oc1(self, status, turn_on):
        self.b.opencode_event({"type": "message.part.updated", "properties": {"part": {
            "type": "tool", "tool": "task", "sessionID": "P", "callID": "c1",
            "state": {"status": status, "input": {"subagent_type": "general", "description": "查"}}}}},
            "P", turn_on=turn_on)

    def test_opencode_v1_inside_turn_quiet_outside_wakes(self):
        self.oc1("running", True)
        self.oc1("completed", True)
        self.assertFalse(self.woke())
        self.oc1("running", False)
        self.oc1("completed", False)
        self.assertTrue(self.woke())

    def oc2(self, t, turn_on):
        self.b.opencode2_event({"type": t, "data": {"sessionID": "C", "info": {"id": "C", "parentID": "P",
                                                                             "title": "查 (@explore subagent)"}}},
                               "P", turn_on=turn_on)

    def test_opencode_v2_inside_turn_quiet_outside_wakes(self):
        self.oc2("session.created", True)
        self.oc2("session.execution.succeeded", True)
        self.assertFalse(self.woke())
        self.oc2("session.execution.started", False)
        self.oc2("session.execution.succeeded", False)
        self.assertTrue(self.woke())

    def test_flag_never_reaches_the_phone(self):
        self.tool_use("tu9", bg=True)
        self.b.mark_bg("own:tu:tu9")
        self.assertTrue(all(set(x) & {"fg", "bg"} == set() for x in self.b.items()))


if __name__ == "__main__":
    unittest.main()
