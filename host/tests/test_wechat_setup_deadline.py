"""Setup metadata is the host's total monotonic window, never a QR TTL."""
import unittest
from unittest.mock import patch
from agentj.wechat import WeChat


class SetupDeadline(unittest.TestCase):
    def test_view_reports_remaining_window_without_qr_secrets(self):
        w = WeChat.__new__(WeChat)
        w.status = lambda: {'bound': False}
        w.qr = {'id': 'first', 'img': 'fixture', 'status': 'wait', 'content': 'private', 'value': 'private'}
        w.setup_deadline, w.setup_id = 700, 'setup-session'
        with patch('agentj.wechat.time.monotonic', return_value=105):
            first = w.view()
        self.assertEqual(first['setup'], {'id': 'setup-session', 'remaining_seconds': 595})
        self.assertEqual(set(first['qr']), {'id', 'img', 'status'})
        w.qr['id'] = 'refreshed'
        with patch('agentj.wechat.time.monotonic', return_value=225):
            self.assertEqual(w.view()['setup']['remaining_seconds'], 475)
        with patch('agentj.wechat.time.monotonic', return_value=800):
            self.assertEqual(w.view()['setup']['remaining_seconds'], 0)
        w.qr = None
        self.assertNotIn('setup', w.view())
