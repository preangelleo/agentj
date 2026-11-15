import asyncio
import inspect
import unittest
from unittest import mock

from agentj import auto_update, ctl_auth, diskq, serve, agent


class P1AuditHostTests(unittest.TestCase):
    def test_history_clear_all_is_sensitive(self):
        self.assertIn("history_clear", ctl_auth.SENSITIVE)

    def test_update_loop_due_is_called_off_loop(self):
        source = inspect.getsource(serve.Host.update_loop)
        self.assertIn("to_thread(auto_update.due", source)

    def test_busy_loop_reports_periodic_failures(self):
        source = inspect.getsource(serve.Host.busy_loop)
        self.assertIn("bg.swallowed", source)

    def test_harness_read_kills_process_when_event_handler_dies(self):
        source = inspect.getsource(agent.ClaudeAgent._read)
        self.assertIn("await self._kill(proc)", source)

    def test_log_rotation_keeps_readable_previous_file(self):
        self.assertTrue(hasattr(diskq, "LOG_ROTATE_BYTES"))
        self.assertIn(".1", inspect.getsource(diskq._append))
