"""Windows installation failure regressions, simulated on Linux; no Windows machine."""
import importlib.util
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stderr
from agentj import opencode_free
from agentj.agent_opencode import startup_help

SOURCE = Path(__file__).resolve().parents[2] / 'support/installation/installer.py'
if not SOURCE.is_file():
    # The public tree carries the same published installer as an exact test fixture.
    SOURCE = Path(__file__).resolve().parent / 'fixtures/p385/installer.py'
spec = importlib.util.spec_from_file_location('p385_installer', SOURCE)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class WindowsStartup(unittest.TestCase):
    def test_offline_catalog_for_every_free_probe(self):
        env = opencode_free._env({'OPENCODE_DISABLE_MODELS_FETCH': '0'})
        self.assertEqual(env['OPENCODE_DISABLE_MODELS_FETCH'], '1')

    def test_home_root_and_alias_refused_before_external_calls(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            alias = home / 'alias'
            alias.symlink_to(home, target_is_directory=True)
            for root in (home, alias, Path('/')):
                with self.subTest(root=root), self.assertRaisesRegex(installer.InstallError, 'working_root'):
                    installer.install('AJI-' + 'a' * 32, 'uv', root, home=home,
                                      approve=lambda _: self.fail('consent reached'),
                                      post=lambda *a, **k: self.fail('network reached'))

    def test_chosen_project_accepted_and_file_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            self.assertEqual(installer.validate_working_root(home / 'project', home), home / 'project')
            f = home / 'file'; f.touch()
            with self.assertRaisesRegex(installer.InstallError, 'working_root'):
                installer.validate_working_root(f, home)

    def test_installer_timeout_has_safe_actionable_diagnosis(self):
        out = io.StringIO()
        with patch.object(installer.subprocess, 'run', side_effect=subprocess.TimeoutExpired('opencode PRIVATE_OUTPUT', 45)), redirect_stderr(out):
            with self.assertRaisesRegex(installer.InstallError, 'opencode_startup_timeout'):
                installer.explain_installation(Path('/fake/opencode'), Path('/tmp'), {})
        text = out.getvalue()
        self.assertIn('45', text)
        self.assertIn('models.dev', text)
        self.assertIn('工作目录', text)
        self.assertIn('OPENCODE_DISABLE_MODELS_FETCH', text)
        self.assertNotIn('PRIVATE_OUTPUT', text)

    def test_explanation_disables_fetch_and_bounds_wait(self):
        calls = []
        def run(argv, **kwargs):
            calls.append((argv, kwargs)); return b''
        installer.explain_installation(Path('/fake/opencode'), Path('/tmp'), {}, run=run)
        self.assertEqual(calls[0][1]['timeout'], 45)
        self.assertEqual(calls[0][1]['env']['OPENCODE_DISABLE_MODELS_FETCH'], '1')

    def test_slow_start_hint_classifies_without_leaking_output(self):
        for raw, expected in [('models.dev PRIVATE', '模型目录'), ('watcher PRIVATE', '扫描'),
                              ('ENOTFOUND PRIVATE', '网络连接'), ('unknown PRIVATE', '尚未确定')]:
            text = startup_help(raw, slow=True)
            self.assertIn(expected, text)
            self.assertIn('30', text)
            self.assertIn('```sh', text)
            self.assertNotIn('PRIVATE', text)

    def test_phone_hint_uses_selected_language(self):
        english = startup_help('models.dev', lang='en')
        self.assertIn('model catalog', english)
        self.assertNotIn('目录', english)
        self.assertNotIn('Check on the host', startup_help('models.dev', lang='zh'))


class StartupThreshold(unittest.IsolatedAsyncioTestCase):
    async def test_spawn_warns_once_at_threshold_then_reports_failure(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, Mock
        from agentj import agent_opencode as oc
        now = [0.0]
        notices, failures = [], []
        async def readline():
            await asyncio.Event().wait()
        process = SimpleNamespace(returncode=None, stdout=SimpleNamespace(readline=readline))
        async def wait(*args, **kwargs):
            now[0] += 15
            return set(), set()
        async def kill(p):
            p.returncode = -9
        def discard(coro):
            coro.close()
        agent = SimpleNamespace(cfg={'dir': '/tmp', 'fence': False}, kind='opencode',
                                host=SimpleNamespace(lang='en', preferences={}, st=SimpleNamespace(log=Mock()),
                                                     agent_notice=notices.append),
                                _own_protocol=lambda: False, argv=lambda p: ['fake-opencode'],
                                authorized_launch_argv=AsyncMock(return_value=['fake-opencode']),
                                _drain_err=lambda p: asyncio.sleep(0), _bg=discard,
                                _kill=kill, fail_notice=failures.append)
        with patch.object(oc, '_bin', return_value='/fake/opencode'), \
             patch('agentj.harness.version_of', return_value='1.18.34'), \
             patch('agentj.provider_runtime.fresh_environment', return_value={}), \
             patch.object(oc, 'auth_fingerprint', return_value=()), \
             patch.object(oc, 'time', SimpleNamespace(monotonic=lambda: now[0])), \
             patch.object(oc.asyncio, 'wait', side_effect=wait), \
             patch.object(oc.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process)):
            self.assertFalse(await oc.OpenCodeAgent._spawn(agent))
        self.assertEqual(len(notices), 1)
        self.assertIn('after 30 seconds', notices[0])
        self.assertEqual(len(failures), 1)
        self.assertIn('not delivered', failures[0])
        self.assertTrue(agent.failed_start)
