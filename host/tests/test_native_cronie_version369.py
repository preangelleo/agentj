"""Cronie's PACKAGE_STRING output is data; never invoke a real crontab."""
import unittest

from agentj import native_scheduler_control as native


class CronieVersion(unittest.TestCase):
    def test_upstream_package_string(self):
        calls = []

        def run(argv, stdin=None):
            calls.append((argv, stdin))
            self.assertIsNone(stdin)
            if argv == ['crontab', '-V']:
                return b'cronie 1.7.2\n'
            self.assertEqual(argv, ['crontab', '-l'])
            return b'0 1 * * * /bin/true\n'

        backend = native.Backend(platform='linux', crontab_run=run)
        target = dict(platform='linux', source='cron', scope='user', id='crontab:1',
                      op='disable', snapshot='0' * 64)
        self.assertTrue(backend.snapshot(target)['enabled'])
        self.assertEqual([argv for argv, _ in calls], [['crontab', '-V'], ['crontab', '-l']])
