"""P65 regressions: native-sandbox recall fallback never widens state/network visibility."""
import _hermetic  # noqa: F401
import asyncio
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from agentj import history, main_identity, recall
from test_l1 import _state


class Index(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.work = self.root / 'work'; self.work.mkdir()
        self.st = _state(self.root)
        self.hist = history.History(self.st)
        self.host = SimpleNamespace(st=self.st, hist=self.hist, agent_cfg={'dir': str(self.work)})
        self.srv = recall.Server(self.host)
        self.env = patch.dict(os.environ, {}, clear=False); self.env.start()
        self.srv.start_index()
        self.args = recall.check({'t': 'recall', 'limit': 50})

    def tearDown(self):
        asyncio.run(self.srv.stop())
        self.env.stop(); self.tmp.cleanup()

    def read(self, cwd=None, **args):
        with patch('pathlib.Path.cwd', return_value=cwd or self.work):
            return recall.from_index({**self.args, **args})

    def test_native_socket_denial_keeps_bounded_redacted_history(self):
        key = 'sk-' + 'A' * 45
        for i in range(55):
            self.hist.add({'k': 'phone', 'text': f'任务 {i}: ' + key}, '换 open-meteo，停在未测试', 'done')
        row = self.read()
        self.assertEqual(len(row['hits']), 50)
        self.assertTrue(row['bounded'])
        self.assertNotIn(key, self.srv.index_path.read_text())
        self.assertIn('<redacted>', self.srv.index_path.read_text())
        self.assertLessEqual(self.srv.index_path.stat().st_size, recall.INDEX_MAX)
        self.assertEqual(self.srv.index_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.srv.index_dir.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.srv.index_dir.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.srv.index_dir.parent / '.gitignore').read_text(), '*\n')
        self.assertEqual(len(self.read(query='open-meteo')['hits']), 50)

    def test_device_label_redacted_before_clipping_in_socket_and_index(self):
        key = 'sk-' + 'B' * 45
        for prefix in ('phone ', 'x' * 59 + ' '):
            self.hist.add({'k': 'phone', 'name': prefix + key, 'text': 'label-test'}, '', 'done')
        hits = recall.search(self.srv.turns())
        for hit in hits:
            self.assertNotIn('sk-', hit['who'], 'a truncated key prefix must not escape label redaction')
        exported = self.srv.index_path.read_text()
        self.assertNotIn('sk-', exported)
        self.assertIn('<redacted>', exported)
        self.assertNotIn('sk-', json.dumps(self.read()))

    def test_cli_socket_denial_falls_back_without_opening_hidden_state(self):
        self.hist.add({'k': 'phone', 'text': 'open-meteo'}, 'stopped at validation', 'done')
        env = {**os.environ, recall.ENV: str(self.root / 'absent.sock'),
               'AGENTJ_STATE_DIR': str(self.root / 'hidden-state'),
               'PYTHONPATH': str(pathlib.Path(__file__).resolve().parents[1])}
        result = subprocess.run([sys.executable, '-m', 'agentj.cli', 'recall', 'open-meteo', '--json'],
                                env=env, cwd=self.work, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)
        self.assertEqual(row['via'], 'index')
        self.assertEqual(row['hits'][0]['reply'], 'stopped at validation')
        self.assertFalse((self.root / 'hidden-state').exists())

    def test_epoch_clear_undo_purge_and_off_refresh_synchronously(self):
        self.hist.add({'k': 'phone', 'text': 'yesterday'}, 'stopped at tests', 'done')
        before = json.loads(self.srv.index_path.read_text())['epoch']
        self.hist.reset()
        self.assertGreater(json.loads(self.srv.index_path.read_text())['epoch'], before)
        self.assertTrue(self.read()['hits'][0]['archived'])
        self.hist.undo_reset()
        self.assertFalse(self.read()['hits'][0]['archived'])
        self.hist.purge()
        self.assertEqual(self.read()['hits'], [])
        self.hist.add({'k': 'phone', 'text': 'new'}, '', 'done')
        self.host.hist = history.History(self.st, on=False)
        self.srv.bind_history()
        self.assertFalse(self.srv.index_path.exists())
        self.host.hist.add({'k': 'phone', 'text': 'memory only'}, '', 'done')
        self.assertIsNone(self.read())

    def test_no_cross_root_discovery_and_symlink_reader_refused(self):
        self.hist.add({'k': 'phone', 'text': 'private'}, '', 'done')
        other = self.root / 'other'; other.mkdir()
        self.assertIsNone(self.read(cwd=other))
        with patch.dict(os.environ, {recall.INDEX_ENV: ''}):
            self.assertIsNone(self.read())
        self.srv.index_path.unlink()
        private = self.root / 'private.json'; private.write_text('{}')
        self.srv.index_path.symlink_to(private)
        self.assertIsNone(self.read())
        self.hist.add({'k': 'phone', 'text': 'safe'}, '', 'done')
        self.assertEqual(private.read_text(), '{}', 'atomic write must not follow index symlink')

    def test_symlink_directory_cannot_redirect_producer(self):
        original = self.srv.index_dir
        saved = original.with_name('saved'); original.rename(saved)
        outside = self.root / 'outside'; outside.mkdir()
        (outside / 'index.json').write_text('do not unlink')
        original.symlink_to(outside, target_is_directory=True)
        self.hist.add({'k': 'phone', 'text': 'should not export'}, '', 'done')
        self.assertEqual((outside / 'index.json').read_text(), 'do not unlink')
        original.unlink(); saved.rename(original)

    def test_shutdown_cleanup_and_observer_failure_is_nonfatal(self):
        self.hist.changed = lambda: (_ for _ in ()).throw(OSError('failed'))
        self.hist.add({'k': 'phone', 'text': 'still saved'}, '', 'done')
        self.assertTrue(self.hist.turns)
        self.srv.bind_history()
        dest = self.srv.index_dir
        asyncio.run(self.srv.stop())
        self.assertFalse(dest.exists())
        self.assertNotIn(recall.INDEX_ENV, os.environ)

    def test_stale_cleanup_only_same_state_dead_pid(self):
        dest = self.srv.index_dir.parent
        dead = dest / f'serve-{self.srv.state_tag}-99999999-abcdef'; dead.mkdir()
        (dead / 'index.json').write_text('private')
        foreign = dest / 'serve-other-99999999-abcdef'; foreign.mkdir()
        (foreign / 'index.json').write_text('keep')
        fd = os.open(dest, os.O_RDONLY | os.O_DIRECTORY)
        try: self.srv.clean_stale(fd)
        finally: os.close(fd)
        self.assertFalse(dead.exists())
        self.assertTrue(foreign.exists())
        self.assertTrue(self.srv.index_dir.exists(), 'live serve must survive cleanup')


class Guidance(unittest.TestCase):
    def test_bilingual_operations_are_audited_without_changing_role(self):
        for lang in ('zh', 'en'):
            prompt = main_identity.prompt({'language': lang})
            for needle in ('agentj recall --days 2 --json', 'VERDICT', 'agentj-manual', '/model'):
                self.assertIn(needle, prompt)
            self.assertIn(main_identity.OPERATIONS_LINE[lang], prompt)
            self.assertEqual(main_identity.verify_core()['version'], 5)


if __name__ == '__main__': unittest.main()
