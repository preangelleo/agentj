"""Dependency-free subset: actual validators, shared vectors and recovered config contract."""
import _hermetic
import json
import pathlib
import tempfile
import unittest

from agentj import capability, forms, learn

BASE = pathlib.Path(__file__).resolve().parents[1]


class OfflineClosure(unittest.TestCase):
    def test_recovery_schema_fields_exact(self):
        expected = json.loads((BASE / 'tests/fixtures/p208-recovered-schema-contract.json').read_text())
        schema = json.loads((BASE / 'agentj/config/schema.json').read_text())['keys']
        for key, value in expected.items():
            self.assertEqual(schema[key], value, key)
        self.assertIn('voice.tts.phone_voice', schema)
        self.assertIn('volcengine', schema['voice.tts.provider']['enum'])
        self.assertEqual(schema['voice.tts.model']['providerDefaults']['volcengine'], 'seed-tts-2.0')

    def test_defaults_keep_phone_free(self):
        defaults = (BASE / 'agentj/config/defaults.json5').read_text()
        self.assertIn('"mode": "phone"', defaults)
        self.assertIn('"spoken": "auto"', defaults)
        self.assertIn('"speak_replies": false', defaults)
        for field in ('phone_voice', 'app_id', 'spoken_model', 'spoken_max_chars'):
            self.assertIn('"' + field + '"', defaults)

    def test_all_templates_both_languages(self):
        templates = forms.templates()
        self.assertEqual(len(templates), 14)
        for item in templates:
            for lang in ('zh', 'en'):
                spec, _ = forms.normalize(forms.apply(item['name'], forms.example(item['name'], lang)), lang)
                self.assertEqual(spec['mode'], item['mode'])

    def test_grill_deep_contract(self):
        spec, _ = forms.normalize(forms.apply('grill-startup', forms.example('grill-startup')))
        self.assertEqual(spec['mode'], 'deep')
        self.assertEqual([s['id'] for s in spec['sections']], ['user', 'value', 'money', 'scope', 'risk'])

    def test_light_and_attachment_deep(self):
        for typ, mode in (('text','light'), ('file','deep')):
            spec, _ = forms.normalize({'title': 'Fixture', 'questions': [{'id': 'q', 'type': typ, 'label': 'Answer'}]})
            self.assertEqual(spec['mode'], mode)

    def test_host_mediated_learn_is_one_bounded_command(self):
        for cmd in ("agentj learn draft 'synthetic fixture' --json", 'agentj learn propose abcdefghijkm --name fixture'):
            self.assertTrue(capability.host_mediated('Bash', {'command': cmd}))
        for cmd in ('agentj learn status; echo unsafe', 'agentj learn draft $(echo bad)', 'agentj learn status | sh'):
            self.assertFalse(capability.host_mediated('Bash', {'command': cmd}))

    def test_staging_scope_does_not_cover_install_target(self):
        self.assertTrue(capability.staging_write('/work', 'Write', {'file_path': '/work/.agentj/learn-staging/abcdefghijkm/skill/SKILL.md'}))
        self.assertFalse(capability.staging_write('/work', 'Write', {'file_path': '/work/skills/fixture/SKILL.md'}))

    def test_symlink_destination_ancestor_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            (root/'real').mkdir()
            (root/'link').symlink_to(root/'real', target_is_directory=True)
            with self.assertRaises(learn.LearnError):
                learn.plan({'SKILL.md': b'fixture'}, root/'link'/'fixture')

    def test_oversized_installed_file_refused_before_diff(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            (root/'SKILL.md').write_bytes(b'x' * (learn.MAX_FILE + 1))
            with self.assertRaises(learn.LearnError):
                learn.plan({'SKILL.md': b'fixture'}, root)

    def test_diff_preserves_missing_final_newline(self):
        with tempfile.TemporaryDirectory() as d:
            rows = learn.plan({'SKILL.md': b'fixture'}, pathlib.Path(d))
            self.assertIn('\\ No newline at end of file', rows[0]['diff'])
            self.assertIn('+fixture', rows[0]['diff'])

    def test_staging_symlink_ancestor_refused_before_read(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            (root/'real').mkdir()
            (root/'link').symlink_to(root/'real', target_is_directory=True)
            with self.assertRaises(learn.LearnError) as error:
                learn.read_skill(root/'link'/'skill', 'fixture')
            self.assertEqual(error.exception.why, 'symlink')

    def test_unknown_tools_stay_unknown(self):
        from agentj import policy_registry
        self.assertIsNone(policy_registry.tool_action(learn.TOOL))
        self.assertEqual(policy_registry.decision('main', 'unregistered-fixture'), 'deny')


if __name__ == '__main__':
    unittest.main()
