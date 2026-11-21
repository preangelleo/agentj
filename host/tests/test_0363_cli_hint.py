"""Invalid root commands offer a localized suggestion without invoking it."""
import _hermetic  # noqa: F401
import contextlib
import io
import os
import unittest
from unittest.mock import patch

from agentj import cli


class CliHint(unittest.TestCase):
    def invoke(self, args, locale='en_US.UTF-8'):
        err = io.StringIO()
        with patch.dict(os.environ, {'LC_ALL': locale}), patch.object(cli.State, 'exists', return_value=False), \
                contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as exited:
            cli.main(args)
        self.assertEqual(exited.exception.code, 2)
        return err.getvalue()

    def test_restart_guidance_is_one_line(self):
        for args in (['restart'], ['restart', 'apply'], ['serve', 'restart'], ['reboot'], ['重启'],
                     ['--instance', '2', 'serve', '重启']):
            with self.subTest(args=args):
                self.assertEqual(self.invoke(args), 'Did you mean: agentj service restart\n')
        self.assertEqual(self.invoke(['serve', 'restart'], 'zh_CN.UTF-8'),
                         '你是不是想用：agentj service restart\n')

    def test_update_guidance_and_unknown_command(self):
        for args in (['upgrade'], ['更新'], ['update'], ['update', 'install']):
            with self.subTest(args=args):
                self.assertEqual(self.invoke(args), 'Did you mean: agentj update apply\n')
        err = self.invoke(['misspelled-command'])
        self.assertEqual(len(err.splitlines()), 1)
        self.assertIn('agentj --help', err)
        self.assertNotIn('misspelled-command', err)

    def test_installed_owner_language_takes_priority(self):
        with patch.object(cli, '_lang', return_value='zh'), patch.object(cli.State, 'exists', return_value=True):
            err = io.StringIO()
            with patch.dict(os.environ, {'LC_ALL': 'en_US.UTF-8'}), contextlib.redirect_stderr(err), \
                    self.assertRaises(SystemExit) as exited:
                cli.main(['restart'])
            self.assertEqual(exited.exception.code, 2)
            self.assertEqual(err.getvalue(), '你是不是想用：agentj service restart\n')

    def test_supported_commands_keep_normal_dispatch(self):
        with patch('agentj.migrate.auto', return_value='none'), patch.object(cli, 'cmd_service') as service, \
                patch.object(cli, 'cmd_update') as update:
            cli.main(['service', 'restart'])
            cli.main(['update', 'apply'])
        self.assertEqual(service.call_args.args[0].mode, 'restart')
        self.assertEqual(update.call_args.args[0].mode, 'apply')
        for args in (['--help'], ['serve', '--help'], ['update', '--help']):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as exited:
                cli.main(args)
            self.assertEqual(exited.exception.code, 0)
            self.assertIn('usage:', out.getvalue())
