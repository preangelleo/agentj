"""Card name registry, validation and paired projection (P519)."""
import _hermetic  # noqa: F401
import copy
import json
import pathlib
import tempfile
import unittest
from agentj import jcards


class CardNames(unittest.TestCase):
    def test_registry_and_switch_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            data = jcards.phone_list(root)
            self.assertEqual([c['id'] for c in data['system']], list(jcards.SYSTEM))
            for c in data['system'] + data['cards']:
                self.assertEqual(set(c['name']), {'zh', 'en'})
                self.assertTrue(c['aliases'], c['id'])
            self.assertIn('待办卡', next(c for c in data['cards'] if c['id'] == 'todo')['aliases'])
            jcards.set_on(root, 'todo', False)
            self.assertFalse(next(c for c in jcards.phone_list(root)['cards'] if c['id'] == 'todo')['on'])

    def test_optional_bounded_aliases(self):
        d = json.loads((jcards.PKG / 'todo/card.json').read_text())
        for a in [[], ['待办卡', 'To-do card'], ['a+b (c)']]:
            x = copy.deepcopy(d); x['aliases'] = a
            self.assertEqual(jcards.check(x, official=True), [])
        for a in [None, '待办卡', [1], [''], ['x'*41], ['x']*13, ['x','x'], ['a\u202eb']]:
            x = copy.deepcopy(d); x['aliases'] = a
            self.assertTrue(any(e['code'] == 'aliases' for e in jcards.check(x, official=True)), repr(a))
        del d['aliases']
        self.assertEqual(jcards.check(d, official=True), [])
