"""Real JSON5 package tests; missing production dependencies must fail honestly."""
import unittest
from agentj import preference_data as data, preferences
from agentj.preference_values import ConfigError


class PreferenceDataTest(unittest.TestCase):
    def test_decode_remains_sparse_and_ignores_legacy_dotted_keys(self):
        raw = '// keep\n{version:1, appearance:{theme:"dark",}, "agent.allow_docker":true}'
        self.assertEqual(data.decode(raw), {'version': 1, 'appearance': {'theme': 'dark'}})
        self.assertEqual(data.ignored(raw), ['agent.allow_docker'])
        self.assertEqual(preferences.parse(raw), data.decode(raw))

    def test_decode_does_not_replace_semantic_validation(self):
        self.assertEqual(data.decode('{unknown: true}'), {'unknown': True})
        with self.assertRaises(ConfigError):
            preferences.parse('{unknown: true}')
        self.assertIs(preferences.ConfigError, data.ConfigError)

    def test_duplicate_syntax_and_byte_limit_are_preserved(self):
        for raw in ('{version:1,version:1}', '{bad:', ' ' * (data.MAX_BYTES + 1)):
            with self.subTest(raw=raw[:32]), self.assertRaises(ConfigError):
                data.decode(raw)

    def test_defaults_and_merge_are_private_and_preserve_id_semantics(self):
        base = data.defaults()
        original = data.defaults()
        merged = data.merge(base, {'menu': {'items': [{'id': 'custom', 'cmd': '/custom', 'desc': 'Custom'}]}})
        self.assertEqual(base, original)
        self.assertIn('custom', [row['id'] for row in merged['menu']['items']])
        base['appearance']['theme'] = 'changed'
        self.assertEqual(data.defaults(), original)

    def test_editor_preserves_comments_and_unrelated_values(self):
        raw = '// header\n{version:1, appearance:{theme:"dark",language:"en"}}'
        updated = data.edit(raw, 'appearance.theme', 'light')
        self.assertTrue(updated.startswith('// header\n'))
        self.assertEqual(data.decode(updated)['appearance'], {'theme': 'light', 'language': 'en'})
