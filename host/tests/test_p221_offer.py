"""P221 item 5: `agentj passphrase offer` — the one optional question at the end of an installation. y → set it (twice,
≥ 8 characters, never echoed); Enter → skipped with how to set it later; no terminal → the sentence only, never a prompt."""
import _hermetic  # noqa: F401,I001
import argparse
import contextlib
import io
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from agentj import cli, gate
from agentj.state import State
from test_p176_terminal_pair import TTYIn

PASS = 'offer-password-221'


class Offer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='aj-p221o-', dir='/tmp')
        self.st = State(pathlib.Path(self.tmp.name) / 's'); self.st.init()

    def tearDown(self):
        self.tmp.cleanup()

    def offer(self, stdin, secrets=()):
        out = io.StringIO(); pw = list(secrets)
        a = argparse.Namespace(mode='offer', json=True)
        with patch.object(cli, 'State', return_value=self.st), patch('sys.stdin', stdin), \
                patch('getpass.getpass', side_effect=lambda prompt='': pw.pop(0) if pw else ''), contextlib.redirect_stdout(out):
            try:
                cli.cmd_passphrase(a); code = 0
            except SystemExit as e:
                code = e.code
        text = out.getvalue()
        return code, text, json.loads(text.strip().splitlines()[-1]) if code in (0, None) else None

    def test_yes_sets_it(self):
        code, text, res = self.offer(TTYIn(['y']), [PASS, PASS])
        self.assertEqual(res, {'result': 'set', 'set': True}); self.assertTrue(gate.is_set(self.st))
        self.assertIn('要不要现在设一个遥控器配对密码', text); self.assertIn('Optional, recommended', text)
        self.assertNotIn(PASS, text)

    def test_enter_skips_and_says_how_later(self):
        code, text, res = self.offer(TTYIn(['']))
        self.assertEqual(res, {'result': 'skipped', 'set': False}); self.assertFalse(gate.is_set(self.st))
        self.assertIn('agentj passphrase set', text)

    def test_no_terminal_never_prompts(self):
        code, text, res = self.offer(io.StringIO('y\n'), [PASS, PASS])
        self.assertEqual(res, {'result': 'no_terminal', 'set': False}); self.assertFalse(gate.is_set(self.st))

    def test_already_set_is_left_alone(self):
        gate.set_passphrase(self.st, PASS)
        code, text, res = self.offer(TTYIn(['y']), ['new-password-x', 'new-password-x'])
        self.assertEqual(res, {'result': 'already', 'set': True})
        gate.verify(self.st, PASS)

    def test_too_short_is_refused(self):
        code, text, res = self.offer(TTYIn(['y']), ['short', 'short'])
        self.assertNotIn(code, (0, None)); self.assertFalse(gate.is_set(self.st))


if __name__ == '__main__':
    unittest.main()
