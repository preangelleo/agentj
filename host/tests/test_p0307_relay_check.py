"""Offline, fake-binary qualification for the standalone relay check."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'agentj/skills/agentj-relay-check/check.py'


class RelayCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='p0307-relay-')
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / 'home'
        self.home.mkdir()
        self.bin = Path(self.tmp.name) / 'bin'
        self.bin.mkdir()
        self.env = dict(os.environ, HOME=str(self.home), PATH=str(self.bin),
                        XDG_CONFIG_HOME=str(self.home / '.config'), CODEX_HOME=str(self.home / '.codex'),
                        ANTHROPIC_BASE_URL='https://agentsrelay.net/v1')
        self.env.pop('OPENAI_BASE_URL', None)

    def fake(self, name, response='ok'):
        exe = self.bin / name
        exe.write_text('#!' + sys.executable + '\nimport sys\n'
                       'if "--version" in sys.argv: print("fixture 1.0"); sys.exit(0)\n'
                       'sys.stdin.read()\n'
                       'print(' + repr(response) + ')\n')
        exe.chmod(0o700)

    def run_check(self):
        return subprocess.run([sys.executable, '-I', str(SCRIPT), '--json'],
                              env=self.env, capture_output=True, text=True, timeout=12)

    def test_unconfigured_never_calls_binary(self):
        self.fake('claude', 'secret-should-not-be-reported')
        self.env['ANTHROPIC_BASE_URL'] = 'https://agentsrelay.net.evil.invalid/v1'
        p = self.run_check()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)['claude'], 'not_relay')
        self.assertNotIn('secret-should-not-be-reported', p.stdout + p.stderr)

    def test_one_request_and_no_key_output(self):
        self.fake('claude', '{"result":"hello","is_error":false}')
        self.env['ANTHROPIC_AUTH_TOKEN'] = 'fixture-private-never-print'
        p = self.run_check()
        self.assertEqual(json.loads(p.stdout)['claude'], 'ok')
        self.assertNotIn('fixture-private-never-print', p.stdout + p.stderr)
        self.assertEqual(json.loads(p.stdout)['codex'], 'not_installed')

    def test_codex_config_domain_and_error_class(self):
        self.fake('codex', '{"type":"error","message":"401 fixture-secret"}')
        cfg = self.home / '.codex/config.toml'
        cfg.parent.mkdir()
        cfg.write_text('model_provider = "relay"\n[model_providers.relay]\nbase_url = "https://api.agentsrelay.net/v1"\n')
        p = self.run_check()
        self.assertEqual(json.loads(p.stdout)['codex'], 'auth')
        self.assertNotIn('fixture-secret', p.stdout + p.stderr)

    def test_opencode_config_and_quota(self):
        self.fake('opencode', '{"type":"error","error":{"statusCode":429,"message":"private"}}')
        cfg = self.home / '.config/opencode/opencode.json'
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({'provider': {'relay': {'options': {'baseURL': 'https://agentsrelay.net/v1'}}}}))
        p = self.run_check()
        self.assertEqual(json.loads(p.stdout)['opencode'], 'quota')
        self.assertNotIn('private', p.stdout + p.stderr)

    def test_no_secrets_from_malformed_config_or_url(self):
        self.fake('claude', 'this must not be called')
        self.env['ANTHROPIC_BASE_URL'] = 'https://agentsrelay.net@evil.invalid/v1'
        p = self.run_check()
        self.assertEqual(json.loads(p.stdout)['claude'], 'not_relay')
        self.assertNotIn('this must not be called', p.stdout)


if __name__ == '__main__':
    unittest.main()
