"""535: native login owns Kimi credentials; rescue and foreground checks terminate."""
import _hermetic  # noqa: F401
import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from agentj import auth_rescue as ar
from test_p288_auth_renew import Manager


class Kimi(unittest.TestCase):
    def test_legacy_python_verification_url(self):
        url, code = ar.kimi_authorization('Verification URL: https://auth.kimi.com/authorize?user_code=ABCD-1234\n')
        self.assertEqual(code, 'ABCD-1234')
        self.assertIn('auth.kimi.com', url)

    def test_current_node_positive_and_untrusted_negative(self):
        text = 'Opening browser for Kimi device login: https://www.kimi.com/code/authorize_device?user_code=ABCD-1234\nenter code: ABCD-1234'
        self.assertEqual(ar.kimi_authorization(text)[1], 'ABCD-1234')
        for url in ['https://auth.kimi.com@evil.invalid/auth', 'https://evil.kimi.com/auth', 'http://auth.kimi.com/auth']:
            self.assertEqual(ar.kimi_authorization(url + '\nenter code: ABCD-1234'), ('', ''))

    def test_external_native_login_survives_failed_rescue(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {'KIMI_CODE_HOME': d}):
            cfg = Path(d) / 'config.toml'; cfg.write_text('old=1')
            flow = ar.KimiFlow(); flow._snapshot()
            cfg.write_text('new=2')
            cred = Path(d) / 'credentials/new.json'; cred.parent.mkdir(); cred.write_text('{"fixture":true}')
            flow.restore()
            self.assertEqual(cfg.read_text(), 'new=2')
            self.assertTrue(cred.exists())

class Health(Manager):
    async def test_foreground_deadline_includes_discovery(self):
        def slow():
            time.sleep(.15)
            return ['kimi']
        with mock.patch.object(ar, 'FOREGROUND_TIMEOUT', .03), mock.patch.object(ar, 'health_targets', side_effect=slow):
            await self.r.refresh_health()
            await asyncio.wait_for(self.r.health_task, .3)
        self.assertFalse(self.r.checking)
        self.assertTrue(all(r['state'] != 'checking' for r in self.r.view()['harnesses']))

    async def test_fast_provider_finishes_while_other_checks(self):
        release = __import__('threading').Event()
        def check(k):
            if k == 'kimi': release.wait(.3)
            return {'ok': True, 'cls': None, 'why': 'ok'}
        with mock.patch.object(ar, 'health_targets', return_value=['claude','kimi']), mock.patch.object(ar, 'binary', return_value='/fixture'), mock.patch.object(ar, 'health_check', side_effect=check):
            await self.r.refresh_health()
            for _ in range(20):
                await asyncio.sleep(.005)
                rows = {r['kind']:r for r in self.r.view()['harnesses']}
                if rows['claude']['state'] == 'ok': break
            self.assertEqual(rows['claude']['state'], 'ok')
            self.assertEqual(rows['kimi']['state'], 'checking')
            release.set(); await self.r.health_task

    async def test_continuous_replacement_never_claims_ok(self):
        stamps = iter(bytes([n]) for n in range(40))
        with mock.patch.object(self.r, '_credential_stamp', side_effect=lambda: next(stamps)), mock.patch.object(ar, 'health_targets', return_value=['kimi']), mock.patch.object(ar, 'binary', return_value=None), mock.patch.object(ar, 'health_check', return_value={'ok': True, 'cls': None, 'why':'ok'}):
            await self.r.refresh_health()
            await self.r.health_task
        row = next(r for r in self.r.view()['harnesses'] if r['kind'] == 'kimi')
        self.assertEqual(row['state'], 'unknown')

    async def test_late_thread_result_after_deadline_not_published(self):
        def slow(k):
            time.sleep(.08)
            return {'ok':True, 'cls':None, 'why':'ok'}
        with mock.patch.object(ar, 'FOREGROUND_TIMEOUT', .02), mock.patch.object(ar, 'health_targets', return_value=['kimi']), mock.patch.object(ar, 'binary', return_value=None), mock.patch.object(ar, 'health_check', side_effect=slow):
            await self.r.refresh_health(); await self.r.health_task
            before = ar.read_meta(self.st)['health']['kimi'].copy()
            await asyncio.sleep(.1)
            self.assertEqual(ar.read_meta(self.st)['health']['kimi'], before)
            self.assertEqual(before['why'], 'timeout')

class PrimaryKey(Manager):
    async def test_kimi_primary_overrides_stale_oauth_without_changing_native_files(self):
        from agentj import agent_kimi, keys
        native = self.tmp / 'kimi'
        (native / 'credentials').mkdir(parents=True)
        cfg = native / 'config.toml'; cfg.write_text('old=1')
        cred = native / 'credentials/native.json'; cred.write_text('{"fixture":true}')
        before = {p.name:p.read_bytes() for p in (cfg, cred)}
        with mock.patch.object(keys, 'read_env', return_value={'KIMI_API_KEY': 'managed-fixture'}), mock.patch.dict(os.environ, {'KIMI_CODE_HOME':str(native)}):
            env = agent_kimi.key_environment(os.environ, 'global', None)
            self.assertEqual(env['KIMI_MODEL_API_KEY'], 'managed-fixture')
            self.assertEqual(agent_kimi.login()[0], 'env')
        self.assertEqual({p.name:p.read_bytes() for p in (cfg, cred)}, before)

    async def test_codex_and_opencode_launch_observe_fresh_primary_keys(self):
        from agentj import agent_codex, agent_opencode, keys, peer_session, provider_runtime
        with mock.patch.object(keys, 'read_env', return_value={'CUSTOM_CODEX_API_KEY':'new','UNRELATED_SECRET':'private'}), mock.patch.object(peer_session, 'codex_user_model', return_value={'provider_table': {'env_key':'CUSTOM_CODEX_API_KEY'}}):
            env = agent_codex.primary_environment({'CUSTOM_CODEX_API_KEY':'stale'})
            self.assertEqual(env['CUSTOM_CODEX_API_KEY'], 'new')
            self.assertNotIn('UNRELATED_SECRET', env)
        with mock.patch.object(provider_runtime, 'env_path', return_value=str(self.tmp / 'primary.env')):
            (self.tmp / 'primary.env').write_text('KIMI_API_KEY=updated-fixture\n')
            env = agent_opencode.opencode_env({'KIMI_API_KEY':'stale'}, 'fixture')
            self.assertEqual(env['KIMI_API_KEY'], 'updated-fixture')

    async def test_existing_phone_contract_has_no_primary_key_button(self):
        rows = self.r.view()['harnesses']
        for row in rows:
            self.assertNotIn('api_key', row['routes'])
            self.assertNotIn('key_name', row)
            self.assertNotIn('key_set', row)
        self.assertEqual(await self.r.start('kimi','api_key'), {'ok':False,'why':'unsupported'})


class LegacyPhoneCompatibility(Manager):
    async def test_failed_kimi_verification_uses_existing_nonrollback_reason(self):
        flow = mock.Mock(region='global')
        flow.start.return_value = ('https://www.kimi.com/code/authorize_device','ABCD-1234',600)
        flow.wait.return_value = 'ok'
        self.r.flow = {'kind':'kimi','route':'device','stage':'starting'}
        with mock.patch.object(ar, 'KimiFlow', return_value=flow), mock.patch.object(ar, 'health_check', return_value={'ok':False,'cls':None,'why':'failed'}):
            await self.r._kimi()
        self.assertEqual(self.r.last['kimi']['why'], 'probe')
        self.assertNotIn('恢复', self.r.why(self.r.last['kimi']['why']))

    async def test_timeout_reuses_existing_unknown_state_and_text(self):
        with mock.patch.object(ar, 'FOREGROUND_TIMEOUT', .01), mock.patch.object(ar, 'health_targets', side_effect=lambda: time.sleep(.1)), mock.patch.object(ar, 'binary', return_value='/fixture'):
            await self.r.refresh_health()
            await self.r.health_task
        for row in self.r.view()['harnesses']:
            self.assertEqual(row['state'], 'unknown')
            self.assertEqual(row['health']['why'], 'timeout')
            self.assertEqual(row['health']['detail'], ar.WHY['zh']['timeout'])
            self.assertNotIn('api_key', row['routes'])
