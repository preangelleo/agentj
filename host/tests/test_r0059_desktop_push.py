"""R-0059: only committed visible shared desktop replies merit a reply notification."""
import _hermetic  # noqa: F401
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from agentj.history import History
from agentj.serve import Host


class DesktopPush(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("TEST_TMP") or None)
        self.addCleanup(self.tmp.cleanup)
        self.old_home = os.environ.get("HOME")
        self.old_xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["HOME"] = self.tmp.name
        os.environ["XDG_CONFIG_HOME"] = str(Path(self.tmp.name) / "config")
        self.addCleanup(self.restore_env)
        st = SimpleNamespace(root=Path(self.tmp.name), log=Mock())
        self.h = SimpleNamespace(st=st, hist=History(st, on=False), desktop_turn=None,
                                 proactive_via=None, proactive_input="", lang="zh", push_notify=Mock(),
                                 media_later=Mock(), agent=None)
        self.h.hist_add = lambda src, text, end: self.h.hist.add(src, text, end)
        self.h.hist_update = lambda tid, **kw: self.h.hist.update(tid, **kw)
        for name in ("_agent_src", "desktop_text", "desktop_end", "desktop_input", "desktop_cmd_done", "proactive_turn"):
            setattr(self.h, name, getattr(Host, name).__get__(self.h))

    def restore_env(self):
        for key, old in (("HOME", self.old_home), ("XDG_CONFIG_HOME", self.old_xdg)):
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old

    def test_unanchored_background_for_each_shared_harness(self):
        for harness in ("claude", "codex", "opencode"):
            with self.subTest(harness=harness):
                self.h.agent = SimpleNamespace(kind=harness)
                self.h.proactive_turn("task")
                self.h.desktop_text("报告已完成 " + harness)
                self.assertEqual(self.h.hist.turns[max(self.h.hist.turns)]["end"], "done")
                self.h.desktop_end()
                self.h.push_notify.assert_called_once_with("reply")
                self.h.push_notify.reset_mock()

    def test_anchored_final_only_not_intermediate_or_duplicate_end(self):
        self.h.desktop_input("desktop request")
        self.h.desktop_text("final reply")
        self.h.push_notify.assert_not_called()
        self.h.desktop_end()
        self.h.desktop_end()
        self.h.push_notify.assert_called_once_with("reply")

    def test_silent_blank_echo_and_tool_only_do_not_push(self):
        self.h.desktop_text(" \n")
        self.h.desktop_text("〔静默消息〕OK")
        self.h.desktop_end()
        self.h.desktop_input("tool-only")
        self.h.desktop_end()
        self.h.push_notify.assert_not_called()
        self.h.desktop_text("fresh result")
        self.h.push_notify.assert_not_called()
        self.h.desktop_end()
        self.h.push_notify.assert_called_once_with("reply")
        self.h.push_notify.reset_mock()
        self.h.desktop_text("fresh result")
        self.h.desktop_end()
        self.h.push_notify.assert_not_called()
        self.assertTrue(any(c.args[0] == "hist_echo_dropped" for c in self.h.st.log.call_args_list))
        for call in self.h.st.log.call_args_list:
            if call.args[0] == "desktop_reply_push":
                self.assertFalse(set(call.kwargs) - {"trigger", "reason"})

    def test_command_completion_is_not_an_agent_reply(self):
        from unittest.mock import patch
        self.h.desktop_input("/compact")
        with patch("agentj.serve.slash.done_line", return_value="Compacted"):
            self.h.desktop_cmd_done("compact")
        self.h.push_notify.assert_not_called()

    def test_unanchored_is_not_sent_before_final_and_logs_source(self):
        self.h.proactive_turn("task")
        self.h.desktop_text("draft")
        self.h.desktop_text("final")
        self.h.push_notify.assert_not_called()
        self.h.desktop_end()
        self.h.push_notify.assert_called_once_with("reply")
        self.h.st.log.assert_any_call("desktop_reply_push", trigger="task", reason="committed")

    def test_failed_history_append_does_not_notify(self):
        self.h.hist.on = True
        self.h.st.append_private = Mock(side_effect=OSError("simulated"))
        self.h.st.write_private = Mock()
        self.h.desktop_text("report")
        self.h.desktop_end()
        self.h.push_notify.assert_not_called()
        self.h.st.log.assert_any_call("history_write_fail")

    def test_silent_final_overwrites_anchored_visible_draft(self):
        self.h.desktop_input("webhook")
        self.h.desktop_text("draft")
        self.h.desktop_text("[silent]OK")
        self.h.desktop_end()
        self.h.push_notify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
