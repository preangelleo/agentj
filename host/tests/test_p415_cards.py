"""P415 paired direct saves, isolated card files only."""
import _hermetic  # noqa
import pathlib
import tempfile
import unittest
from agentj import jcards, jcards_data, jcards_todo

class Direct(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix='p415-')
        self.addCleanup(t.cleanup)
        self.root = pathlib.Path(t.name)
        for cid in ('notes', 'todo'):
            jcards.set_on(self.root, cid, True)

    def test_note_direct_add_dated_edit_delete(self):
        r = jcards_data.phone_change(self.root, 'notes', 'add', text='Tokyo\n\nAddress')
        self.assertTrue(r['ok'], r)
        s = jcards.phone_get(self.root, 'notes')
        self.assertEqual(s['entries'][0]['text'], 'Tokyo\n\nAddress')
        self.assertRegex(s['entries'][0]['date'], r'^\d{4}-\d{2}-\d{2}$')
        self.assertIn('Address', s['entries'][0]['text'])
        it = s['entries'][0]
        self.assertTrue(jcards_data.phone_change(self.root, 'notes', 'edit', it['id'], s['revision'], 'New address')['ok'])
        self.assertEqual(jcards_data.phone_change(self.root, 'notes', 'delete', it['id'], s['revision'])['why'], 'conflict')
        s = jcards.phone_get(self.root, 'notes')
        self.assertTrue(jcards_data.phone_change(self.root, 'notes', 'delete', s['entries'][0]['id'], s['revision'])['ok'])

    def test_todo_direct_add_edit_delete_and_revision(self):
        r = jcards_data.phone_change(self.root, 'todo', 'add', text='Buy milk')
        self.assertTrue(r['ok'], r)
        s = jcards.phone_get(self.root, 'todo')
        it = next(x for x in s['items'] if x['text'] == 'Buy milk')
        self.assertTrue(jcards_data.phone_change(self.root, 'todo', 'edit', it['id'], s['revision'], 'Buy tea')['ok'])
        self.assertEqual(jcards_data.phone_change(self.root, 'todo', 'delete', it['id'], s['revision'])['why'], 'conflict')
        s = jcards.phone_get(self.root, 'todo')
        self.assertTrue(jcards_data.phone_change(self.root, 'todo', 'delete', it['id'], s['revision'])['ok'])

    def test_secret_shapes_refused_without_write(self):
        secret = 'sk-' + 'syntheticonly' * 4
        for cid in ('notes', 'todo'):
            before = jcards._read(self.root, jcards.find(self.root, cid))[0]
            for op in ('add', 'edit'):
                self.assertEqual(jcards_data.phone_change(self.root, cid, op, text=secret)['why'], 'looks_like_a_secret')
            self.assertEqual(jcards._read(self.root, jcards.find(self.root, cid))[0], before)
        self.assertFalse(jcards_data.phone_change(self.root, '../escape', 'add', text='x')['ok'])

    def test_transport_add_routes_to_requester_without_agent_or_text_log(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, Mock
        from agentj.serve import Host
        h = Host.__new__(Host)
        h.st = SimpleNamespace(root=self.root, log=Mock())
        h.lang, h.agent_cfg = 'en', None
        h.send_app = AsyncMock()
        sender = SimpleNamespace(device='p415-test')
        for cid in ('notes', 'todo'):
            asyncio.run(h.on_jcard(sender, 'jcard_note', {'r':'p415add', 'id':cid, 'op':'add', 'text':'Private address'}))
            self.assertIs(h.send_app.call_args.args[0], sender)
            self.assertTrue(h.send_app.call_args.args[1]['ok'])
        self.assertNotIn('Private address', str(h.st.log.call_args_list))
        self.assertFalse(hasattr(h, 'agent'))

    def test_todo_delete_preserves_unknown_rows_and_metadata(self):
        import json
        p = jcards.data_dir(self.root, 'todo') / 'todo.json'
        p.write_text(json.dumps({'custom':'keep', 'items':[{'id':'t1','text':'delete'}, {'strange':42}, {'id':'t2','text':'keep','extra':7}]}))
        snap = jcards.phone_get(self.root, 'todo')
        self.assertTrue(jcards_data.phone_change(self.root, 'todo', 'delete', 't1', snap['revision'])['ok'])
        saved = json.loads(p.read_text())
        self.assertEqual(saved, {'custom':'keep','schema':2,'items':[{'strange':42}, {'id':'t2','text':'keep','extra':7}]})

    def test_custom_notebook_existing_edit_delete_remain_available(self):
        import json
        folder = self.root / 'custom'
        jcards.export('notes', folder)
        p = folder / 'card.json'
        doc = json.loads(p.read_text()); doc['id'] = 'my-notes'; p.write_text(json.dumps(doc))
        self.assertTrue(jcards.apply(self.root, folder)['ok'])
        snap = jcards.phone_get(self.root, 'my-notes')
        it = snap['entries'][0]
        self.assertTrue(jcards_data.phone_change(self.root, 'my-notes', 'edit', it['id'], snap['revision'], 'Custom edit')['ok'])
        snap = jcards.phone_get(self.root, 'my-notes')
        self.assertTrue(jcards_data.phone_change(self.root, 'my-notes', 'delete', snap['entries'][0]['id'], snap['revision'])['ok'])
        self.assertFalse(jcards_data.phone_change(self.root, 'my-notes', 'add', text='No new arbitrary writer')['ok'])
