"""Offline checks for the data/runtime import boundary and shared validators."""
import ast
from pathlib import Path
import unittest

from agentj import preference_checks as checks, preference_values as values, proxy


class PreferenceChecksTest(unittest.TestCase):
    def test_notify_rows_shape_limits_and_duplicates(self):
        self.assertIsNone(checks.rows_problem([{'id': 'backup.daily', 'allow': False}]))
        for rows in (None, [{}], [{'id': 'bad name', 'allow': True}],
                     [{'id': 'job', 'allow': 1}],
                     [{'id': 'job', 'allow': True}] * 2,
                     [{'id': str(i), 'allow': True} for i in range(101)]):
            with self.subTest(rows=rows):
                self.assertIsNotNone(checks.rows_problem(rows))

    def test_asr_endpoint_normalizes_only_supported_routes(self):
        for base in ('https://example.com', 'https://example.com/v1/',
                     'https://example.com/v1/audio/transcriptions'):
            self.assertEqual(checks.asr_endpoint(base),
                             'https://example.com/v1/audio/transcriptions')
        self.assertEqual(checks.asr_endpoint('http://127.0.0.1:8080/custom'),
                         'http://127.0.0.1:8080/custom/v1/audio/transcriptions')

    def test_asr_endpoint_refuses_credentials_and_malformed_destinations(self):
        for base in ('https://user:secret@example.com', 'https://example.com?key=secret',
                     'https://example.com/#secret', 'file:///tmp/audio',
                     'https://example.com:bad', 'https://example.com/\nsecret', None):
            with self.subTest(base=base), self.assertRaises(values.ConfigError) as caught:
                checks.asr_endpoint(base)
            self.assertNotIn('secret', str(caught.exception))

    def test_proxy_reads_sparse_values_without_loading_preferences(self):
        env = proxy.environment({}, {'proxy': {'https': 'http://127.0.0.1:7890'}})
        self.assertEqual(env['HTTPS_PROXY'], 'http://127.0.0.1:7890')
        self.assertEqual(env['HTTP_PROXY'], env['HTTPS_PROXY'])
        self.assertEqual(proxy.environment({}, {}), {})

    def test_data_layer_has_no_state_or_runtime_imports_at_any_depth(self):
        root = Path(__file__).resolve().parents[1] / 'agentj'
        allowed = {'__future__', 'copy', 'json', 'pathlib', 're', 'json5',
                   'urllib.parse', 'preference_values'}
        for name in ('preference_data', 'preference_values', 'preference_checks'):
            tree = ast.parse((root / (name + '.py')).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertIn(node.module, allowed, (name, node.lineno))
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertIn(alias.name, allowed, (name, node.lineno))
        for name in ('voice', 'proxy'):
            tree = ast.parse((root / (name + '.py')).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotEqual(node.module, 'preferences', (name, node.lineno))
                    self.assertNotIn('preferences', [a.name for a in node.names],
                                     (name, node.lineno))
