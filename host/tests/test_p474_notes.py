"""P474: isolated owner notebooks, lossless backup and exact three-field round trips."""
import _hermetic  # noqa
import datetime
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch
from agentj import jcards, jcards_data, jcards_notes


class Notes(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='p474-')
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        jcards.set_on(self.root, 'notes', True)
        self.path = jcards.data_dir(self.root, 'notes') / 'notes.md'

    def test_legacy_migration_exact_backup_once(self):
        raw = '# Notebook\r\n\r\n- 2026-10-21 · 香港地址\r\n  second line\r\n- 2026-10-22 Plain content\r\n- undated\r\n'
        self.path.write_bytes(raw.encode())
        snap = jcards.phone_get(self.root, 'notes')
        self.assertTrue(snap['ok'], snap)
        self.assertEqual([(x['title'], x['text'], x['date']) for x in snap['entries']],
                         [('', '香港地址\nsecond line', '2026-10-21'), ('', 'Plain content', '2026-10-22'), ('', 'undated', '')])
        backups = list(self.path.parent.glob('notes.md.pre-v2-*.bak'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), raw.encode())
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
        now = self.path.read_bytes()
        self.assertEqual(jcards.phone_get(self.root, 'notes')['revision'], snap['revision'])
        self.assertEqual(self.path.read_bytes(), now)
        self.assertEqual(len(list(self.path.parent.glob('*.bak'))), 1)

    def test_exact_content_and_optional_title(self):
        for text in ['  香港 Address\n\n## Note\nTitle: fake\nDate: fake\n尾行  \n', 'line\r\nnext\t\u2028尾']:
            r = jcards_data.note_change(self.root, 'notes', 'add', text=text, title='香港地址', date='2026-10-20')
            self.assertTrue(r['ok'], r)
            row = r['entries'][0]
            self.assertEqual((row['title'], row['text'], row['date']), ('香港地址', text, '2026-10-20'))
            r = jcards_data.note_change(self.root, 'notes', 'edit', row['id'], r['revision'], text, title='')
            self.assertTrue(r['ok'], r)
            self.assertEqual(r['entries'][0]['title'], '')
            self.assertEqual(r['entries'][0]['text'], text)
            self.assertEqual(r['entries'][0]['date'], '2026-10-20')
        r = jcards_data.note_change(self.root, 'notes', 'add', text='body')
        self.assertEqual(r['entries'][0]['date'], datetime.date.today().isoformat())
        self.assertEqual(r['entries'][0]['title'], '')

    def test_invalid_and_conflict_never_write(self):
        snap = jcards.phone_get(self.root, 'notes')
        raw = self.path.read_bytes()
        for fields in [{'date':'2026-02-30'}, {'date':42}, {'title':'a\nb'}, {'title':42}]:
            self.assertFalse(jcards_data.note_change(self.root, 'notes', 'add', text='body', **fields)['ok'])
            self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(jcards_data.note_change(self.root, 'notes', 'edit', 'x', 'old', 'body')['why'], 'conflict')
        self.assertEqual(self.path.read_bytes(), raw)

    def test_backup_failure_prevents_migration(self):
        raw = b'- 2026-10-22 body\n'
        self.path.write_bytes(raw)
        with patch.object(jcards, '_write_at', side_effect=OSError('disk full')):
            self.assertFalse(jcards.phone_get(self.root, 'notes')['ok'])
        self.assertEqual(self.path.read_bytes(), raw)

    def test_malformed_v2_and_non_utf8_are_not_overwritten(self):
        for raw in [jcards_notes.MAGIC+b'broken', b'- invalid \xff\n']:
            self.path.write_bytes(raw)
            self.assertFalse(jcards_data.note_change(self.root, 'notes', 'add', text='body')['ok'])
            self.assertEqual(self.path.read_bytes(), raw)

    def test_phone_title_secret_refused(self):
        raw = self.path.read_bytes()
        r = jcards_data.phone_change(self.root, 'notes', 'add', text='safe', title='sk-'+'syntheticonly'*4)
        self.assertEqual(r['why'], 'looks_like_a_secret')
        self.assertEqual(self.path.read_bytes(), raw)

    def test_cli_fields_and_json_listing(self):
        import argparse
        from contextlib import redirect_stdout
        import io
        with patch('agentj.state.state_dir', return_value=self.root), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(jcards.command(['note','add','--title','Address','--date','2026-10-19','--text','Body','--json']), 0)
        row = json.loads(out.getvalue())['entries'][0]
        self.assertEqual((row['title'],row['text'],row['date']), ('Address','Body','2026-10-19'))
        with patch('agentj.state.state_dir', return_value=self.root), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(jcards.command(['note','list','--json']), 0)
        self.assertEqual(json.loads(out.getvalue())['entries'][0], row)

    def test_concurrent_first_reads_share_one_backup(self):
        from concurrent.futures import ThreadPoolExecutor
        raw = b'# original heading\n\n- 2026-10-22 one\n'
        self.path.write_bytes(raw)
        with ThreadPoolExecutor(max_workers=6) as pool:
            snapshots = list(pool.map(lambda _: jcards.phone_get(self.root, 'notes'), range(6)))
        self.assertTrue(all(x['ok'] for x in snapshots))
        self.assertEqual(len({x['revision'] for x in snapshots}), 1)
        self.assertEqual(len(list(self.path.parent.glob('*.bak'))), 1)
        self.assertEqual(next(self.path.parent.glob('*.bak')).read_bytes(), raw)

    def test_paired_transport_passes_fields_without_content_logs(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, Mock
        from agentj.serve import Host
        h = Host.__new__(Host)
        h.st = SimpleNamespace(root=self.root, log=Mock())
        h.lang, h.agent_cfg, h.send_app = 'en', None, AsyncMock()
        sender = SimpleNamespace(device='p474-test')
        asyncio.run(h.on_jcard(sender, 'jcard_note', {'r':'fields','id':'notes','op':'add','title':'Address title','text':'Private body','date':'2026-10-19'}))
        self.assertIs(h.send_app.call_args.args[0], sender)
        row = h.send_app.call_args.args[1]['entries'][0]
        self.assertEqual((row['title'],row['text'],row['date']), ('Address title','Private body','2026-10-19'))
        self.assertNotIn('Private body', str(h.st.log.call_args_list))
        self.assertNotIn('Address title', str(h.st.log.call_args_list))
