"""Offline regression: hostile service logs and test-mode network diagnostics."""
import _hermetic  # noqa: F401
import os
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import rescue_kit as kit


class RescuePrivacy(unittest.TestCase):
    def test_traceback_never_exposes_customer_filenames_or_functions(self):
        hostile = 'customer_invoice_very_private.py'
        text = ('Traceback (most recent call last):\n'
                f'  File "/home/user/projects/{hostile}", line 9999999999, in customerSecret\n'
                f'  File "/home/user/site-packages/{hostile}", line 2, in leaked\n'
                'CustomerError: private message\n')
        result = '\n'.join(kit.last_traceback(text))
        for secret in (hostile, 'customerSecret', 'leaked', 'CustomerError', 'private message', '/home/user'):
            self.assertNotIn(secret, result)
        self.assertTrue(result)
        self.assertLess(len(result), 256)

    def test_test_mode_network_checks_never_probe_public_hosts(self):
        with mock.patch.dict(os.environ, {'AGENTJ_TEST': '1', 'AGENTJ_RESCUE_RELAY_HOST': 'example.org'}), \
             mock.patch.object(kit, 'RELAY_HOST', 'example.org'), \
             mock.patch.object(kit.socket, 'getaddrinfo', side_effect=AssertionError('external DNS')) as dns, \
             mock.patch.object(kit.socket, 'create_connection', side_effect=AssertionError('external TLS')) as tls, \
             mock.patch.object(kit.urllib.request, 'urlopen', side_effect=AssertionError('external HTTP')) as http:
            report = kit.Report()
            kit.check_network(report)
            dns.assert_not_called()
            tls.assert_not_called()
            http.assert_not_called()
            self.assertFalse(report.facts.get('https'))

    def test_hostlog_serve_crash_where_is_category_only(self):
        line = kit.hostlog_line({"ev": "serve_crash", "exception_type": "acme_secret_project.ClientError",
                                 "where": "agentj/serve.py:12 < /home/u/acme-merger-plan/run.py:7"})
        self.assertNotIn("acme", line)
        self.assertNotIn("serve.py", line)
        self.assertNotIn(":12", line)
        self.assertIn('"where": "agentj < other"', line)
        self.assertIn('"exception_type": "other_error"', line)

    def test_test_mode_resolve_never_calls_the_real_resolver(self):
        with mock.patch.dict(os.environ, {'AGENTJ_TEST': '1'}), \
             mock.patch.object(kit.socket, 'getaddrinfo', side_effect=AssertionError('external DNS')) as dns:
            self.assertEqual(kit.resolve('example.org'), 'disabled_in_test')
            # An injected stand-in is not the network: P332 tests keep driving resolve() through it.
            lookup = mock.Mock(return_value=[])
            self.assertIsNone(kit.resolve('example.org', lookup=lookup))
            lookup.assert_called_once()
        dns.assert_not_called()
