"""Native trash never falls back to permanent deletion or exposes backend output."""
import _hermetic  # noqa
import argparse
import contextlib
import io
import os
import pathlib
import subprocess
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

    def test_linux_exact_argv_and_suppressed_output(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TEST_TMP")) as d:
            p = pathlib.Path(d) / '-rf $(touch innocent)'; p.touch()
            with mock.patch.object(trash.sys, 'platform', 'linux'), mock.patch.object(trash.subprocess, 'run') as run:
                run.return_value.returncode = 0
                self.assertEqual(trash.move(str(p))['status'], 'trashed')
                args, kw = run.call_args
                self.assertEqual(args[0], ['/usr/bin/gio', 'trash', '--', str(p)])
                self.assertEqual(kw['stdout'], subprocess.DEVNULL)
                self.assertEqual(kw['stderr'], subprocess.DEVNULL)
                self.assertFalse(kw.get('shell', False))

    def test_failure_preserves_file_and_fixed_error(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TEST_TMP")) as d:
            p = pathlib.Path(d) / 'sample'; p.write_text('retained')
            for err in (FileNotFoundError('private backend message'), subprocess.TimeoutExpired('private', 60)):
                with mock.patch.object(trash.subprocess, 'run', side_effect=err):
                    result = trash.move(str(p))
                self.assertNotEqual(result['status'], 'trashed')
                self.assertNotIn('private', str(result))
                self.assertEqual(p.read_text(), 'retained')
            with mock.patch.object(trash.subprocess, 'run') as run:
                run.return_value.returncode = 1
                self.assertEqual(trash.move(str(p))['status'], 'failed')
            self.assertEqual(p.read_text(), 'retained')

    def test_no_unsupported_or_root_or_home_fallback(self):
        with mock.patch.object(trash.subprocess, 'run') as run:
            for raw in ('/', str(pathlib.Path.home())):
                self.assertEqual(trash.move(raw)['status'], 'refused')
            with mock.patch.object(trash.sys, 'platform', 'win32'):
                self.assertEqual(trash.move('/tmp/sample')['status'], 'unsupported')
            run.assert_not_called()

    def test_macos_script_is_constant_paths_only_argv(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TEST_TMP")) as d:
            p = pathlib.Path(d) / 'quote"; do shell script "bad'; p.touch()
            with mock.patch.object(trash.sys, 'platform', 'darwin'), mock.patch.object(trash.subprocess, 'run') as run:
                run.return_value.returncode = 0
                self.assertEqual(trash.move(str(p))['status'], 'trashed')
                argv = run.call_args.args[0]
                self.assertEqual(argv[:3], ['/usr/bin/osascript', '-e', trash.FINDER_SCRIPT])
                self.assertNotIn(str(p), trash.FINDER_SCRIPT)
                self.assertEqual(argv[-1], str(p))
            link = pathlib.Path(d) / 'link'; link.symlink_to(p)
            with mock.patch.object(trash.sys, 'platform', 'darwin'), mock.patch.object(trash.subprocess, 'run') as run:
                self.assertEqual(trash.move(str(link))['status'], 'refused')
                run.assert_not_called()

    def test_parser_and_partial_exit_status(self):
        parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest='cmd'); trash.add_parser(sub)
        args = parser.parse_args(['trash', '--json', '--', 'one', 'two'])
        with mock.patch.object(trash, 'move', side_effect=[{'status': 'trashed'}, {'status': 'failed'}]), contextlib.redirect_stdout(io.StringIO()) as out:
            with self.assertRaises(SystemExit) as caught:
                args.fn(args)
        self.assertEqual(caught.exception.code, 1)
        self.assertIn('"trashed"', out.getvalue())
        self.assertIn('"failed"', out.getvalue())

    def test_native_linux_real_recoverable_payload(self):
        if not os.path.isfile('/usr/bin/gio') or trash.sys.platform != 'linux':
            self.skipTest('native GLib trash unavailable')
        with tempfile.TemporaryDirectory(dir=os.environ.get("TEST_TMP")) as d:
            root = pathlib.Path(d); data = root / 'xdg'; data.mkdir(mode=0o700)
            original = root / 'sample.txt'; original.write_bytes(b'recoverable')
            with mock.patch.dict(os.environ, {'XDG_DATA_HOME': str(data)}):
                result = trash.move(str(original))
                if result['status'] != 'trashed':
                    self.assertEqual(original.read_bytes(), b'recoverable')
                    self.skipTest('native filesystem does not support trash; refusal preserved source')
            self.assertFalse(original.exists())
            saved = list((data / 'Trash/files').iterdir())
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0].read_bytes(), b'recoverable')
            info = list((data / 'Trash/info').iterdir())
            self.assertEqual(len(info), 1)
            self.assertIn('sample.txt', info[0].read_text())
            saved[0].rename(original)
            self.assertEqual(original.read_bytes(), b'recoverable')
