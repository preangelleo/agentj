"""Offline recovery contracts; synthetic inputs only, no State, transport or engines."""
import argparse
import pathlib
import tempfile
import unittest
from unittest import mock
from agentj import usage, forms, learn


class MeasuredUsage(unittest.TestCase):
    def test_missing_cost_never_uses_price_table(self):
        t = usage.Turn('claude')
        t.put('a', {'i': 100, 'o': 50}, model='claude-sonnet-4-6')
        with mock.patch.object(usage, 'estimate', side_effect=AssertionError('estimated')):
            self.assertNotIn('usd', t.finish())

    def test_zero_is_reported_and_partial_is_unknown(self):
        t = usage.Turn('claude')
        t.put('a', {'o': 20}, 0)
        self.assertEqual(t.finish()['usd'], 0)
        t.put('b', {'o': 10})
        self.assertNotIn('usd', t.finish())

    def test_cumulative_key_replaces_without_double_counting(self):
        t = usage.Turn('codex')
        t.put('turn1', {'o': 10}, .1)
        t.put('turn1', {'o': 30}, .3)
        t.put('turn2', {'o': 5}, .05)
        r = t.finish()
        self.assertEqual((r['o'], r['usd']), (35, .35))

    def test_missing_token_fields_stay_unknown(self):
        t = usage.Turn("fixture")
        t.put("a", {"i": 3}, .1)
        self.assertNotIn("o", t.finish())

    def test_invalid_costs_are_unknown(self):
        for cost in (True, -1, float('nan'), float('inf'), '0.1'):
            self.assertIsNone(usage.usd_of(cost))


class FormContracts(unittest.TestCase):
    def test_all_fourteen_templates_in_both_languages(self):
        ts = forms.templates()
        self.assertEqual(len(ts), 14)
        for row in ts:
            for lang in ('zh', 'en'):
                with self.subTest(template=row['name'], lang=lang):
                    raw = forms.apply(row['name'], forms.example(row['name'], lang))
                    spec, _ = forms.normalize(raw, lang)
                    self.assertIn(spec['mode'], ('light', 'deep'))
                    self.assertGreater(spec['n'], 0)

    def test_existing_ask_parser_can_coexist(self):
        p = argparse.ArgumentParser()
        sub = p.add_subparsers(dest='cmd')
        sub.add_parser('ask').add_argument('question')
        forms.add_parser(sub, include_ask=False)
        self.assertEqual(p.parse_args(['ask', 'hello']).question, 'hello')
        self.assertEqual(p.parse_args(['form', 'templates']).action, 'templates')

    def test_shared_host_vectors(self):
        import json
        v = json.loads((pathlib.Path(__file__).parents[2] / 'protocol/vectors/forms.json').read_text())
        for cond, ans, wanted in v['holds']:
            self.assertEqual(forms.holds(cond, ans), wanted)
        spec, _ = forms.normalize(v['form'], 'en')
        for ans, other, wanted, clean in v['check']:
            if wanted == 'ok':
                actual, _ = forms.check(spec, ans, other)
                if clean:
                    self.assertEqual(actual, clean)
            else:
                with self.assertRaises(forms.FormError) as caught:
                    forms.check(spec, ans, other)
                self.assertEqual(caught.exception.why, wanted)


class LearnPreview(unittest.TestCase):
    def test_preview_and_write_refuse_destination_symlinks_before_effects(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            target, other = root / 'skill', root / 'other'
            target.mkdir()
            other.mkdir()
            (target / 'scripts').symlink_to(other, target_is_directory=True)
            files = {'scripts/new/run.py': b'fixture'}
            for action in (learn.plan, learn.write_skill):
                with self.assertRaises(learn.LearnError):
                    action(files, target)
            self.assertEqual(list(other.iterdir()), [])

    def test_invisible_filename_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            (root / 'misleading\u202ename.md').write_text('fixture')
            with self.assertRaises(learn.LearnError) as e:
                learn.read_skill(root, 'demo')
            self.assertEqual(e.exception.why, 'path')

    def test_hidden_text_and_oversized_preview_refused(self):
        with tempfile.TemporaryDirectory() as d:
            src = pathlib.Path(d)
            (src / 'SKILL.md').write_text('---\nname: demo\ndescription: fixture\n---\nhello\u202e')
            with self.assertRaises(learn.LearnError) as e:
                learn.read_skill(src, 'demo')
            self.assertEqual(e.exception.why, 'hidden_characters')
            content = b'---\nname: demo\ndescription: fixture\n---\n' + b'fixture line\n' * 2000
            rows = learn.plan({'SKILL.md': content}, src / 'target')
            with self.assertRaises(learn.LearnError) as e:
                learn.card_text('demo', 'doc', rows, src / 'target', '', False, 'en')
            self.assertEqual(e.exception.why, 'too_long')
