"""P142 item 3 (B14): Telegram enroll / doctor / the setup checklist ask the RUNNING serve whether the bot key NAME is set in
its service environment (yes/no over the control socket — never the value); a terminal's environment counts only when serve is
not running. P134 did the same for `config set`. No real Bot API."""
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

from agentj.state import State

KEY = 'AGENTJ_TEST_P142_BOT'
PROBE = r'''
import json, sys
from agentj import telegram, doctor
from agentj.state import State
st = State()
print(json.dumps({"enroll": telegram.enroll(123, sys.argv[1]),
                  "configured": bool(telegram.configured(st))}))
'''


class ServeEnvDecides(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='aj-p142tg-', dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        env = {'HOME': str(self.home), 'XDG_CONFIG_HOME': str(self.home / '.config'),
               'AGENTJ_STATE_DIR': str(self.home / 'state'), 'PATH': '/usr/bin:/bin'}
        self.env = patch.dict(os.environ, env, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.st = State()
        self.st.init()
        self.server = None

    async def serve(self):
        from agentj.serve import Host
        self.host = Host(self.st, read_stdin=False)
        self.server = await asyncio.start_unix_server(self.host.on_ctl, path=str(self.st.sock_path))

    async def asyncTearDown(self):
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    async def probe(self, terminal_has_key: bool):
        env = dict(os.environ)
        env.pop(KEY, None)
        if terminal_has_key:
            env[KEY] = 'terminal-fixture-only'
        env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
        proc = await asyncio.create_subprocess_exec(sys.executable, '-c', PROBE, KEY, env=env,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(), 20)
        self.assertNotIn(b'fixture-only', out + err)
        return json.loads(out.decode().strip().splitlines()[-1])

    async def test_terminal_without_key_serve_with_key_enrolls(self):
        os.environ[KEY] = 'service-fixture-only'          # serve's (this process's) environment
        await self.serve()
        r = await self.probe(terminal_has_key=False)
        self.assertTrue(r['enroll']['ok'], r)
        self.assertTrue(r['configured'])                   # doctor / setup checklist see it too

    async def test_terminal_with_key_serve_without_refuses(self):
        os.environ.pop(KEY, None)
        await self.serve()
        r = await self.probe(terminal_has_key=True)
        self.assertFalse(r['enroll']['ok'])
        self.assertIn('service environment', r['enroll']['error'])
        self.assertFalse(r['configured'])

    async def test_serve_not_running_terminal_env_decides(self):
        r = await self.probe(terminal_has_key=True)
        self.assertTrue(r['enroll']['ok'])
        r = await self.probe(terminal_has_key=False)
        self.assertFalse(r['enroll']['ok'])
        self.assertFalse(r['configured'])

    async def test_env_present_answers_yes_no_only_and_rejects_bad_names(self):
        from agentj.names import ctl_call
        os.environ[KEY] = 'service-fixture-only'
        await self.serve()
        good = await asyncio.to_thread(ctl_call, self.st, {'cmd': 'env_present', 'name': KEY}, 5)
        self.assertEqual(good, {'ok': True, 'present': True})
        none = await asyncio.to_thread(ctl_call, self.st, {'cmd': 'env_present', 'name': 'AGENTJ_TEST_P142_ABSENT'}, 5)
        self.assertEqual(none, {'ok': True, 'present': False})
        bad = await asyncio.to_thread(ctl_call, self.st, {'cmd': 'env_present', 'name': 'path; rm'}, 5)
        self.assertEqual(bad, {'ok': False, 'error': 'bad_name'})


class DoctorServeDecides(unittest.TestCase):
    """doctor's config row: a terminal that has the key cannot vouch for a running serve that lacks it (the B14 bug), and a
    running serve that has it vouches for a terminal that lacks it."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='aj-p142doc-')
        self.addCleanup(self.tmp.cleanup)
        home = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {'HOME': str(home), 'XDG_CONFIG_HOME': str(home / '.config'),
                                           'AGENTJ_STATE_DIR': str(home / 'state'), 'PATH': '/usr/bin:/bin'}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.st = State()
        self.st.init()
        cfg = self.st.config()
        cfg['telegram'] = {'owner_id': 123, 'key_env': KEY}
        self.st.write_private(self.st.config_path, json.dumps(cfg).encode())

    def row(self, live, terminal_key):
        from agentj import doctor, preferences
        cfg = preferences.defaults()
        cfg['channels']['items'] = [{'id': 'telegram', 'type': 'telegram'}]
        with patch.dict(os.environ, {KEY: terminal_key}), patch('agentj.preferences.effective', return_value=cfg), \
                patch('agentj.voice.hardware', return_value={}), patch('agentj.doctor._serve_status', return_value=live):
            return next(r for r in doctor.check_preferences(self.st) if r['id'] == 'config')['status']

    def test_running_serve_decides(self):
        from agentj import doctor
        self.assertEqual(self.row({'ok': True, 'telegram': {'enrolled': False}}, 'terminal-fixture-only'), doctor.FAIL)
        self.assertEqual(self.row({'ok': True, 'telegram': {'enrolled': True}}, ''), doctor.OK)

    def test_serve_not_running_terminal_decides(self):
        from agentj import doctor
        self.assertEqual(self.row(None, 'terminal-fixture-only'), doctor.OK)
        self.assertEqual(self.row(None, ''), doctor.FAIL)


if __name__ == '__main__':
    unittest.main()
