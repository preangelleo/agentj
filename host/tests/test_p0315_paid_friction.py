"""Paid install: an obsolete OpenCode pin and a signed-out Codex candidate."""
import _hermetic  # noqa: F401
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from agentj import binaries, harness, service


class PaidFriction(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='p0315-paid-')
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        (self.home / '.config').mkdir()
        (self.home / '.local/share').mkdir(parents=True)
        (self.home / 'bin').mkdir()
        self.env = {'HOME': str(self.home), 'XDG_CONFIG_HOME': str(self.home / '.config'),
                    'XDG_CACHE_HOME': str(self.home / '.cache'),
                    'XDG_DATA_HOME': str(self.home / '.local/share'), 'CODEX_HOME': str(self.home / '.codex'),
                    'AGENTJ_STATE_DIR': str(self.home / 'state'), 'PATH': str(self.home / 'bin')}
        env_patch = mock.patch.dict(os.environ, self.env, clear=True)
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def binary(self, path, version):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('#!/bin/sh\nprintf "%s\\n" ' + version + '\n')
        path.chmod(0o700)
        return str(path)

    def test_open_code_v1_saved_pin_follows_v2_path_and_heals_saved_line(self):
        old = self.binary(self.home / '.local/share/mise/installs/opencode/1.18.35/bin/opencode', '1.18.35')
        new = self.binary(self.home / 'bin/opencode', '2.2.7')
        env = {**self.env, 'AGENTJ_OPENCODE_BIN': old}
        with mock.patch.dict(os.environ, env, clear=True):
            result = binaries.resolve('opencode')
            self.assertEqual((result['path'], result['reason']), (new, 'pin_stale'))
            f = self.home / 'service.env'
            f.write_text('AGENTJ_OPENCODE_BIN="' + old + '"\nCUSTOM=unchanged\n')
            f.chmod(0o600)
            with mock.patch.object(service, 'env_file', return_value=str(f)):
                self.assertEqual(service.heal_binary_pins('agentj', env)[0]['new'], new)
            self.assertIn('CUSTOM=unchanged\n', f.read_text())
            self.assertIn('AGENTJ_OPENCODE_BIN="' + new + '"', f.read_text())

    def test_explicit_v2_or_no_verified_upgrade_keeps_owner_pin(self):
        old = self.binary(self.home / 'other/opencode', '2.0.0')
        path = self.binary(self.home / 'bin/opencode', '2.2.7')
        env = {**self.env, 'AGENTJ_OPENCODE_BIN': old}
        self.assertEqual(binaries.resolve('opencode', env)['path'], old)
        old = self.binary(self.home / '.local/share/mise/installs/opencode/1.18.35/bin/opencode', '1.18.35')
        self.binary(Path(path), '1.19.0')
        self.assertEqual(binaries.resolve('opencode', {**env, 'AGENTJ_OPENCODE_BIN': old})['path'], old)

    def test_codex_signed_out_at_selection_explains_next_step(self):
        self.binary(self.home / 'bin/codex', 'codex-cli 0.162.1')
        with mock.patch.dict(os.environ, self.env, clear=True):
            d = harness.detect()
            codex = next(h for h in d['harnesses'] if h['name'] == 'codex')
            self.assertTrue(codex['installed'])
            self.assertFalse(codex['logged_in'])
            self.assertNotIn('codex', d['usable'])
            self.assertIn('codex login', codex['note'])
            with mock.patch.object(harness, 'reach', side_effect=lambda d: d), mock.patch('builtins.print') as printed:
                harness.main()
            self.assertIn('codex login', '\n'.join(str(c) for c in printed.call_args_list))
            (self.home / '.codex').mkdir()
            (self.home / '.codex/auth.json').touch()
            self.assertIn('codex', harness.detect()['usable'])
