"""Native trash never falls back to permanent deletion or exposes backend output."""
import _hermetic  # noqa
import argparse
import contextlib
import io
import os
import pathlib
import tempfile
import unittest
from unittest import mock
from agentj import trash


class Trash(unittest.TestCase):
    def test_preserves_delete_classification(self):
        from agentj import danger
        for cmd in ('agentj trash -- sample.txt', 'aj --instance 2 trash sample.txt',
                    'jarvis --fresh trash sample.txt', 'agentj --instance=2 trash sample.txt',
                    'python3 -m agentj trash sample.txt', 'python3 -I -S -m agentj.cli --instance 2 trash sample.txt'):
            self.assertIn('delete', danger.classify('Bash', {'command': cmd}).cats, cmd)
        for target in ('~/.ssh/id_ed25519', '~/.claude/settings.json'):
            for cmd in (f'agentj --instance 2 trash -- {target}', f'python3 -m agentj trash -- {target}'):
                self.assertIn('credentials', danger.classify_shared('Bash', {'command': cmd}).cats, cmd)

    def test_reversible_trash_keeps_permanent_and_secret_floors(self):
        from agentj import confirm_tier
        for command in ('agentj trash -- sample.txt', 'gio trash -- sample.txt', 'trash-put sample.txt'):
            self.assertNotEqual(confirm_tier.agent_requirement('Bash', {'command': command}), 'uv', command)
        for command in ('trash --empty', 'gio trash --empty', 'rm ~/owner-notes.txt', 'agentj trash ~/.ssh/id_ed25519'):
            self.assertEqual(confirm_tier.agent_requirement('Bash', {'command': command}), 'uv', command)

    # 0.30.8 #1 (chairman 11-11): `agentj trash <path>` now moves into Agent J's own trashbin (24 h, `agentj trash
    # restore <id>`) instead of the OS trash; the zero-tail guarantees below carry over (test_p0308_trashbin has the rest).
    def _env(self, d):
        home = pathlib.Path(d) / 'home'
        (home / 'tmp').mkdir(parents=True)
        env = mock.patch.dict(os.environ, {'HOME': str(home), 'AGENTJ_STATE_DIR': str(home / '.local/state/agentj'),
                                           'TMPDIR': str(home / 'tmp')})
        # security r2: the trashbin follows the passwd home (state_paths.account_home), never an environment variable
        acct = mock.patch('agentj.state_paths.account_home', return_value=str(home))
        stack = contextlib.ExitStack()
        stack.enter_context(env)
        stack.enter_context(acct)
        return stack

    def test_failure_preserves_file_and_never_permanent(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TEST_TMP")) as d, self._env(d):
            p = pathlib.Path(d) / 'sample'; p.write_text('retained')
            with mock.patch.object(trash, '_rename', side_effect=PermissionError('private backend message')):
                result = trash.put(str(p))
            self.assertEqual(result['status'], 'failed')
            self.assertNotIn('private', str(result))
            self.assertEqual(p.read_text(), 'retained')
            self.assertEqual(trash.entries(), [])

    def test_no_unsupported_or_root_or_home_fallback(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TEST_TMP")) as d, self._env(d):
            for raw in ('/', str(pathlib.Path.home())):
                self.assertEqual(trash.put(raw)['status'], 'refused')
            with mock.patch.object(trash.sys, 'platform', 'win32'):
                self.assertEqual(trash.put('/tmp/sample')['status'], 'unsupported')

    def test_parser_and_partial_exit_status(self):
        parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest='cmd'); trash.add_parser(sub)
        args = parser.parse_args(['trash', '--json', '--', 'one', 'two'])
        with mock.patch.object(trash, 'put', side_effect=[{'status': 'trashed', 'id': 'x'}, {'status': 'failed'}]), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            with self.assertRaises(SystemExit) as caught:
                args.fn(args)
        self.assertEqual(caught.exception.code, 1)
        self.assertIn('"trashed"', out.getvalue())
        self.assertIn('"failed"', out.getvalue())

    def test_real_recoverable_payload(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TEST_TMP")) as d, self._env(d):
            original = pathlib.Path(d) / 'sample.txt'; original.write_bytes(b'recoverable')
            result = trash.put(str(original))
            self.assertEqual(result['status'], 'trashed')
            self.assertFalse(original.exists())
            self.assertEqual(trash.restore(result['id'])['status'], 'restored')
            self.assertEqual(original.read_bytes(), b'recoverable')
