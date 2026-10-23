"""P492: host-generated authorization metadata is bound to the sealed card."""
import _hermetic  # noqa: F401
import unittest
from agentj import elevate
from test_p288_auth_renew import Manager, _SeqElev


class Cards(Manager):
    async def test_authorize_round_is_in_card_and_digest(self):
        self.host.elevate = _SeqElev([None, None, None])
        await self.r.start('claude', 'setup_token')
        await self.drain()
        cards = self.host.elevate.cards
        self.assertEqual(len(cards), 3)
        for n, card in enumerate(cards, 1):
            auth = card['auth']
            self.assertTrue(auth['url'].startswith('https://'))
            self.assertEqual((auth['kind'], auth['round'], auth['rounds']), ('claude', n, 3))
            self.assertGreater(auth['expires_at'], 0)
            e = object.__new__(elevate.Elevator); e.st = self.st
            delivered = e.card_msg(dict(card, id='a'*32, nonce='b'*32, epk=bytes(32),
                                        deadline=__import__('time').monotonic()+600, fails=0))
            self.assertEqual(delivered['auth'],auth)
            self.assertNotIn('sink',delivered)
            original = elevate.shown_digest('secret', elevate.shown_fields(card))
            for key, value in [('url', auth['url'] + '&other=1'), ('round', 9), ('expires_at', 1)]:
                changed = dict(card, auth=dict(auth, **{key: value}))
                self.assertNotEqual(original, elevate.shown_digest('secret', elevate.shown_fields(changed)))

    def test_agent_secret_request_cannot_supply_authorization_metadata(self):
        card = elevate.norm_secret({'name':'EXAMPLE','purpose':'fixture','dest':f'env:{self.tmp}/app.env#EXAMPLE',
                                    'auth':{'url':'https://claude.com/cai/oauth/authorize?state=agent'}}, self.st.root)
        self.assertNotIn('auth', card)

    def test_ordinary_card_digest_unchanged(self):
        c = dict(kind='secret', name='EXAMPLE', purpose='Example', dest='local', verify='')
        self.assertEqual(elevate.shown_fields(c), ['EXAMPLE', 'Example', 'local', ''])

from unittest import mock
import asyncio
import os
import pathlib
import subprocess
from agentj import auth_rescue as ar, service, keys_found
from test_p258_auth_rescue import GOOD


class Health(Manager):
    async def test_external_replacement_discards_old_failure_and_rechecks(self):
        ar.write_meta(self.st, {'health': {'claude': {'at': 1, 'ok': False, 'cls': 'login'}}})
        self.r.state['claude'] = {'cls': 'login', 'at': 1}
        entered, release = __import__('threading').Event(), __import__('threading').Event()
        def check(kind):
            entered.set(); release.wait(3)
            return {'ok': True, 'cls': None, 'why': 'ok'}
        with mock.patch.object(ar, 'health_targets', return_value=['claude']), mock.patch.object(ar, 'health_check', side_effect=check):
            await self.r.refresh_health()
            await asyncio.to_thread(entered.wait, 2)
            row = self.r.view()['harnesses'][0]
            self.assertEqual(row['state'], 'checking')
            self.assertNotIn('health', row)
            # A second phone joins the same flight, never spawns a second model call.
            old_task = self.r.health_task
            await self.r.refresh_health()
            self.assertIs(self.r.health_task, old_task)
            release.set(); await self.r.health_task
        row = self.r.view()['harnesses'][0]
        self.assertEqual(row['state'], 'ok')
        self.assertTrue(row['health']['ok'])
        self.assertGreater(row['health']['at'], 1)
        # External replacement: no daily-cache suppression, even after a pass today.
        with mock.patch.object(ar, 'health_targets', return_value=['claude']), mock.patch.object(ar, 'health_check', return_value={'ok':True,'cls':None,'why':'ok'}) as hc:
            await self.r.refresh_health(force=False)
            self.assertEqual(hc.call_count, 0)
            os.environ['CLAUDE_CODE_OAUTH_TOKEN'] = GOOD
            await self.r.refresh_health(force=False)
            await self.r.health_task
            self.assertGreater(hc.call_count, 0)

    async def test_change_during_check_does_not_publish_obsolete_failure(self):
        ar.write_meta(self.st, {'health':{'claude':{'at':1,'ok':False,'cls':'login'}}})
        calls = []
        def check(kind):
            calls.append(kind)
            if len(calls) == 1:
                os.environ['CLAUDE_CODE_OAUTH_TOKEN'] = GOOD
                return {'ok':False,'cls':'login','why':'login'}
            return {'ok':True,'cls':None,'why':'ok'}
        with mock.patch.object(ar, 'health_targets', return_value=['claude']), mock.patch.object(ar, 'binary', side_effect=lambda k: '/fixture' if k == 'claude' else None), mock.patch.object(ar, 'health_check', side_effect=check):
            await self.r.refresh_health()
            await self.r.health_task
        self.assertIsNotNone(self.r.credential_stamp)
        self.assertEqual(len(calls),2)
        self.assertTrue(ar.read_meta(self.st)['health']['claude']['ok'])

    def test_live_check_reads_new_service_token_not_inherited_expired_token(self):
        p = pathlib.Path(ar.service_env_path()); p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('CLAUDE_CODE_OAUTH_TOKEN=' + GOOD + '\n'); p.chmod(0o600)
        os.environ['CLAUDE_CODE_OAUTH_TOKEN'] = 'expired-fixture'
        self.assertTrue(ar.health_check('claude', timeout=10)['ok'])
        self.assertNotIn(GOOD, str(self.r.view()))

    def test_effective_manager_environment_and_file_precedence(self):
        with mock.patch.dict(os.environ, {'CLAUDE_CODE_OAUTH_TOKEN':'old-fixture'}), \
             mock.patch.object(keys_found, 'manager_env', return_value={'CLAUDE_CODE_OAUTH_TOKEN':'manager-fixture','UNRELATED':'ignored'}):
            self.assertEqual(ar.effective_auth_env()['CLAUDE_CODE_OAUTH_TOKEN'], 'manager-fixture')
            self.assertNotIn('UNRELATED', ar.effective_auth_env())
            p = pathlib.Path(ar.service_env_path()); p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('CLAUDE_CODE_OAUTH_TOKEN=file-fixture\nANTHROPIC_AUTH_TOKEN=priority-fixture\n'); p.chmod(0o600)
            self.assertEqual(ar.effective_auth_env()['CLAUDE_CODE_OAUTH_TOKEN'], 'file-fixture')
            self.assertIn('ANTHROPIC_AUTH_TOKEN', ar.conflicts())

    def test_environment_d_replaces_stale_inherited_token(self):
        folder = pathlib.Path(keys_found.envd_dir()); folder.mkdir(parents=True, exist_ok=True)
        p = folder / '50-claude-oauth.conf'
        p.write_text('CLAUDE_CODE_OAUTH_TOKEN=' + GOOD + '\n'); p.chmod(0o600)
        os.environ['CLAUDE_CODE_OAUTH_TOKEN'] = 'expired-fixture'
        with mock.patch.object(keys_found, 'manager_env', return_value={}):
            self.assertTrue(ar.health_check('claude', timeout=10)['ok'])
        # A symlink is not a new trusted token source.
        p.rename(folder / 'original'); p.symlink_to(folder / 'original')
        with mock.patch.object(keys_found, 'manager_env', return_value={}):
            self.assertEqual(ar.effective_auth_env()['CLAUDE_CODE_OAUTH_TOKEN'], 'expired-fixture')

    async def test_daily_old_failure_cannot_replace_new_token_result(self):
        ar.write_meta(self.st, {'health':{'claude':{'at':1,'ok':True}}})
        def late(kind):
            os.environ['CLAUDE_CODE_OAUTH_TOKEN'] = GOOD
            return {'ok':False,'cls':'login','why':'login'}
        with mock.patch.object(ar,'health_targets',return_value=['claude']), mock.patch.object(ar,'health_check',side_effect=late):
            await self.r.health_daily(force=True)
        self.assertTrue(ar.read_meta(self.st)['health']['claude']['ok'])
        self.assertNotIn('claude',self.r.state)

    async def test_claude_login_recovery_refreshes_old_health(self):
        ar.write_meta(self.st, {'health':{'claude':{'at':1,'ok':False,'cls':'login'}}})
        os.environ['FAKE258_SCREENS']='ready'
        with self.probe():
            res=await self.r.recover('claude',route='login')
        self.assertTrue(res['ok'])
        self.assertTrue(ar.read_meta(self.st)['health']['claude']['ok'])

    def test_macos_keychain_replacement_changes_revision_without_reading_values(self):
        folder=self.tmp/'Library/Keychains';folder.mkdir(parents=True)
        path=folder/'login.keychain-db';path.write_bytes(b'fixture-one')
        with mock.patch.object(ar.sys,'platform','darwin'), mock.patch.object(pathlib.Path,'home',return_value=self.tmp):
            before=self.r._credential_stamp()
            path.write_bytes(b'fixture-two-longer')
            self.assertNotEqual(self.r._credential_stamp(),before)
