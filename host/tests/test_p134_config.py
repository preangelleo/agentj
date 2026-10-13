"""CLI shell and host service have different environments; no real Bot API calls."""
import _hermetic  # noqa: F401,I001
from _testtmp import TEST_TMP  # noqa: E402  P202: fixtures on tmpfs, not the system disk
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from agentj import preferences as p
from agentj.state import State


class ServiceEnvironmentConfig(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='aj-p134-', dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        env = {'HOME': str(self.home), 'XDG_CONFIG_HOME': str(self.home / '.config'),
               'AGENTJ_STATE_DIR': str(self.home / 'state'), 'PATH': '/usr/bin:/bin'}
        self.env = patch.dict(os.environ, env, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.st = State()
        self.st.init()
        # Synthetic EnvironmentFile, loaded only by the service fixture.
        service_env = self.home / '.config/systemd/user/agentj.env'
        service_env.parent.mkdir(parents=True)
        service_env.write_text('AGENTJ_TELEGRAM_BOT_TOKEN=offline-fixture-only\n')
        name, value = service_env.read_text().strip().split('=', 1)
        os.environ[name] = value
        cfg = self.st.config()
        cfg['telegram'] = {'owner_id': 123, 'key_env': name}
        self.st.write_private(self.st.config_path, json.dumps(cfg).encode())
        p.ensure()
        p.path().write_text(p.edit(p.read()[0], 'channels.items', [{'id': 'tg', 'type': 'telegram'}]))
        from agentj.serve import Host
        self.host = Host(self.st, read_stdin=False)
        self.assertIsNone(self.host.config_problem)
        self.server = await asyncio.start_unix_server(self.host.on_ctl, path=str(self.st.sock_path))

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()

    async def cli(self):
        env = dict(os.environ)
        env.pop('AGENTJ_TELEGRAM_BOT_TOKEN', None)
        env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
        proc = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'agentj.cli', 'config', 'set', 'appearance.theme', 'dark', '--json',
            env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(), 15)
        self.assertNotIn(b'offline-fixture-only', out + err)
        return proc.returncode, json.loads(out)

    async def test_shell_without_key_uses_service_env_and_real_socket(self):
        code, result = await self.cli()
        self.assertEqual(code, 0, result)
        self.assertTrue(result['applied'])
        self.assertEqual(p.get(self.host.preferences, 'appearance.theme'), 'dark')
        self.assertEqual(p.get(p.read()[1], 'appearance.theme'), 'dark')

    async def test_service_rejection_preserves_file_and_history(self):
        os.environ.pop('AGENTJ_TELEGRAM_BOT_TOKEN')
        old = p.path().read_bytes()
        code, result = await self.cli()
        self.assertEqual(code, 3, result)
        self.assertEqual(result['key'], 'channels.items')
        self.assertEqual(p.path().read_bytes(), old)
        self.assertFalse(list((self.st.root / 'config-history').glob('*')))

    async def test_offline_without_key_still_refuses(self):
        self.server.close()
        await self.server.wait_closed()
        self.st.sock_path.unlink(missing_ok=True)
        old = p.path().read_bytes()
        code, result = await self.cli()
        self.assertEqual(code, 2, result)
        self.assertEqual(p.path().read_bytes(), old)


if __name__ == '__main__':
    unittest.main()
