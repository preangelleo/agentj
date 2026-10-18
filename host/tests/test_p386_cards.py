"""P386 note byte preservation, optimistic edits, colors, native schedule projection. Isolated state only."""
import _hermetic  # noqa
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch
from agentj import jcards, jcards_data


class Cards(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix='p386-')
        self.addCleanup(t.cleanup)
        self.root = pathlib.Path(t.name)
        jcards.set_on(self.root, 'notes', True)
        self.path = jcards.data_dir(self.root, 'notes') / 'notes.md'

    def test_notes_preserve_header_and_other_entries(self):
        raw = '# Notebook\r\n\r\n- Founder code: KEEP\r\n\r\n- Phone +1 415 555 1234\r\n  Restaurant address\r\n'
        self.path.write_bytes(raw.encode())
        snap = jcards.phone_get(self.root, 'notes')
        self.assertEqual(len(snap['entries']), 2)
        self.assertIn('415 555 1234', snap['entries'][1]['text'])
        r = jcards_data.note_change(self.root, 'notes', 'edit', snap['entries'][1]['id'], snap['revision'], 'New address')
        self.assertTrue(r['ok'])
        self.assertTrue(self.path.read_bytes().startswith(b'# Notebook\r\n\r\n- Founder code: KEEP\r\n\r\n'))
        unchanged = self.path.read_bytes()
        self.assertEqual(jcards_data.note_change(self.root, 'notes', 'delete', snap['entries'][0]['id'], snap['revision'])['why'], 'conflict')
        self.assertEqual(self.path.read_bytes(), unchanged)
        r = jcards_data.note_change(self.root, 'notes', 'add', text='Most recent')
        self.assertTrue(r['ok'])
        self.assertEqual(r['entries'][0]['text'], 'Most recent')
        self.assertIn(b'Founder code: KEEP', self.path.read_bytes())
        r = jcards_data.note_change(self.root, 'notes', 'delete', r['entries'][0]['id'], r['revision'])
        self.assertTrue(r['ok'])
        self.assertEqual(self.path.read_bytes(), unchanged)

    def test_secret_redacted_edit_refused_delete_explicit(self):
        fake = 'AKIA' + 'Q3EGRKX7' + 'PLM2VN4B'
        self.path.write_text('- ' + fake + '\n')
        snap = jcards.phone_get(self.root, 'notes')
        self.assertNotIn(fake, json.dumps(snap))
        self.assertFalse(snap['entries'][0]['editable'])
        self.assertEqual(jcards_data.note_change(self.root, 'notes', 'edit', snap['entries'][0]['id'], snap['revision'], 'x')['why'], 'redacted')
        self.assertTrue(jcards_data.note_change(self.root, 'notes', 'delete', snap['entries'][0]['id'], snap['revision'])['ok'])

    def test_symlink_refused(self):
        other = self.root / 'outside.md'
        other.write_text('outside')
        self.path.unlink()
        self.path.symlink_to(other)
        self.assertFalse(jcards_data.note_change(self.root, 'notes', 'add', text='x')['ok'])
        self.assertEqual(other.read_text(), 'outside')

    def test_duplicates_boundaries_and_no_background_writes(self):
        self.path.write_text('# Title\n\n- same\n- same\n\nplain paragraph\nsecond line\n')
        before = self.path.read_bytes()
        r = jcards.phone_get(self.root, 'notes')
        self.assertEqual(len(r['entries']), 3)
        self.assertNotEqual(r['entries'][0]['id'], r['entries'][1]['id'])
        self.assertEqual(before, self.path.read_bytes())

    def test_colors_persist_reset_and_no_switch_clobber(self):
        self.assertTrue(jcards_data.set_color(self.root, 'chat', '#AaBbCc')['ok'])
        jcards.set_on(self.root, 'todo', True)
        self.assertEqual(jcards.phone_list(self.root)['colors']['chat'], '#aabbcc')
        self.assertFalse(jcards_data.set_color(self.root, '../escape', '#112233')['ok'])
        self.assertFalse(jcards_data.set_color(self.root, 'chat', 'red')['ok'])
        self.assertTrue(jcards_data.set_color(self.root, 'chat', None)['ok'])
        self.assertNotIn('chat', jcards_data.colors(self.root))

    def test_official_slash_actions_and_automation_default_off(self):
        for cid in ('notes', 'todo'):
            jcards.set_on(self.root, cid, True)
            doc = jcards.phone_get(self.root, cid)
            for act in doc['actions']:
                self.assertTrue(act['prompt'].startswith('/agentj-add-'))
                self.assertNotIn(str(self.root), act['prompt'])
        self.assertFalse(jcards.find(self.root, 'automation')['on'])
        self.assertTrue(jcards.set_on(self.root, 'automation', True))
        with patch('agentj.systasks.scan', return_value=[]):
            self.assertEqual(jcards.phone_get(self.root, 'automation')['template'], 'automation')

    def test_automation_unique_and_unknowns(self):
        rows = [{'id':'job','name':'job','src':'cron','when':[{'k':'cron','v':'0 1 * * *'}]}] * 2
        with patch('agentj.systasks.scan', return_value=rows):
            r = jcards_data.automations()
        self.assertEqual(len({x['name'] for x in r}), 2)
        self.assertIsNone(r[0]['model'])
        self.assertIsNone(r[0]['harness'])
        self.assertTrue(r[0]['prompt'].endswith('@'+r[0]['name']))

    def test_missing_tool_and_bounded_stdout(self):
        self.assertEqual(jcards_data._run(['/p386-missing-tool']), '')
        import sys
        self.assertEqual(jcards_data._run([sys.executable,'-c','print("x"*300000)']), '')
        self.assertEqual(jcards_data._run([sys.executable,'-c','import time; time.sleep(10)'], .02), '')

    def test_agentj_task_catalogue(self):
        row = {'id':'daily','title':{'en':'Daily summary'},'schedule':'0 9 * * *','enabled':True,'next':123}
        with patch('agentj.systasks.scan', return_value=[]), patch('agentj.tasks.rows', return_value=[row]):
            r = jcards_data.automations(self.root, {'kind':'codex','model':'test-model','dir':str(self.root)}, 'en')
        self.assertEqual(r[0]['name'], 'agentj:daily')
        self.assertEqual(r[0]['model'], 'test-model')

    def test_transport_routes_owner_mutations_and_returns_only_to_sender(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, Mock
        from agentj.serve import Host
        h = Host.__new__(Host)
        h.st = SimpleNamespace(root=self.root, log=Mock())
        h.lang, h.agent_cfg = 'en', None
        h.send_app = AsyncMock()
        sender = SimpleNamespace(device='test-device')
        original = self.path.read_bytes()
        with patch('agentj.jcards_data.note_change') as mutate:
            for op in ('add', 'unknown', None):
                asyncio.run(h.on_jcard(sender, 'jcard_note', {'r':'p386bad','id':'notes','op':op,'text':'Do not add'}))
                self.assertEqual(h.send_app.call_args.args[1]['why'], 'bad_op')
                self.assertFalse(h.send_app.call_args.args[1]['ok'])
            mutate.assert_not_called()
        self.assertEqual(self.path.read_bytes(), original)
        snap = jcards.phone_get(self.root, 'notes')
        payload = {'r':'p386note', 'id':'notes', 'entry':snap['entries'][0]['id'], 'revision':snap['revision'], 'op':'edit', 'text':'Private memo address'}
        asyncio.run(h.on_jcard(sender, 'jcard_note', payload))
        self.assertIs(h.send_app.call_args.args[0], sender)
        self.assertTrue(h.send_app.call_args.args[1]['ok'])
        self.assertNotIn('Private memo address', str(h.st.log.call_args_list))
        asyncio.run(h.on_jcard(sender, 'jcard_note', payload))
        self.assertEqual(h.send_app.call_args.args[1]['why'], 'conflict')
        asyncio.run(h.on_jcard(sender, 'jcard_color', {'r':'p386color','id':'chat','color':'#123456'}))
        self.assertTrue(h.send_app.call_args.args[1]['ok'])
        self.assertEqual(jcards_data.colors(self.root)['chat'], '#123456')
        jcards.set_on(self.root, 'automation', True)
        h.agent_cfg = {'kind':'codex', 'dir':str(self.root)}
        with patch('agentj.jcards_data.automations', return_value=[]) as scan:
            asyncio.run(h.on_jcard(sender, 'jcard_get', {'r':'p386auto','id':'automation'}))
        scan.assert_called_once_with(self.root, h.agent_cfg, 'en')

    def test_automation_cli_stable_projection(self):
        from contextlib import redirect_stdout
        import io
        rows = [{'name':'systemd:test.timer','id':'systemd:test.timer','harness':None,'model':None}]
        out = io.StringIO()
        with patch('agentj.state.state_dir', return_value=self.root), patch('agentj.jcards_data.automations', return_value=rows), redirect_stdout(out):
            self.assertEqual(jcards.command(['automation','list','--json']), 0)
        self.assertEqual(json.loads(out.getvalue())['items'], rows)

    def test_cut_secret_tail_not_sent_or_mutable(self):
        prefix = b'ghp_'
        full = b'- memo ' + prefix + b'Q' * 35
        c = jcards.find(self.root, 'notes')
        c['doc']['data']['maxBytes'] = 22
        self.path.write_bytes(full)
        with patch('agentj.jcards.find', return_value=c):
            snap = jcards.phone_get(self.root, 'notes')
            self.assertTrue(snap['cut'])
            self.assertNotIn(prefix.decode(), json.dumps(snap))
            self.assertFalse(any(x['editable'] for x in snap['entries']))
            before = self.path.read_bytes()
            for op in ('edit', 'delete'):
                self.assertFalse(jcards_data.note_change(self.root, 'notes', op, 'any', snap['revision'], 'replacement')['ok'])
            self.assertEqual(before, self.path.read_bytes())
        # No whitespace at all must drop the entire cut token too.
        self.path.write_bytes(prefix + b'Q' * 60)
        with patch('agentj.jcards.find', return_value=c):
            self.assertEqual(jcards.phone_get(self.root, 'notes')['entries'], [])

    def test_automation_secret_identifiers_are_opaque_stable_handles(self):
        secret = 'AKIA' + 'Q3EGRKX7' + 'PLM2VN4B'
        native = [{'id':secret+'.timer','name':'safe description','src':'systemd','when':[]}]
        with patch('agentj.systasks.scan', return_value=native):
            first = jcards_data.automations()
            second = jcards_data.automations()
        self.assertNotIn(secret, json.dumps(first))
        self.assertEqual(first, second)
        self.assertTrue(first[0]['name'].startswith('job-'))
        self.assertTrue(first[0]['prompt'].endswith('@'+first[0]['name']))


    def test_automation_all_outbound_metadata_secret_filtered(self):
        secret = 'AKIA' + 'Q3EGRKX7' + 'PLM2VN4B'
        row = {'id':secret,'title':{'en':secret},'schedule':secret,'tz':secret,'enabled':True,'next':None}
        native = [{'id':'normal.timer','src':'systemd','name':secret,'when':[{'k':'cal','v':secret}],'next_s':secret}]
        with patch('agentj.systasks.scan', return_value=native), patch('agentj.tasks.rows', return_value=[row]):
            output = jcards_data.automations(self.root, {'kind':secret,'model':secret,'dir':str(self.root)}, 'en')
        self.assertNotIn(secret, json.dumps(output))
        self.assertEqual(len(output), 2)


    def test_colors_fifo_refused_without_blocking(self):
        import os
        if not hasattr(os, 'mkfifo'):
            self.skipTest('POSIX FIFO only')
        jcards.home(self.root).mkdir(parents=True, exist_ok=True)
        os.mkfifo(jcards.home(self.root) / 'colors.json')
        self.assertEqual(jcards_data.colors(self.root), {})


    def test_agentj_task_fifo_rejected_before_read(self):
        import os
        from agentj import tasks
        if not hasattr(os, 'mkfifo'):
            self.skipTest('POSIX FIFO only')
        fifo = self.root / 'task.json'
        os.mkfifo(fifo)
        self.assertIsNone(tasks._regular(str(fifo)))
