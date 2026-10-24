"""P506 #270: synthetic mailbox credentials, local fake IMAP/SMTP only."""
import _hermetic  # noqa: F401
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from test_p170_mail import _Base, AGENT, CODE
from agentj import mail as M, elevate, preferences

class Recovery(_Base):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.host.preferences = preferences.defaults()
        self.env = self.root/'registered-keys'; self.env.write_text('OWNER_GMAIL_EMAIL='+AGENT+'\nOWNER_GMAIL_APP_PASSWORD='+CODE+'\nOTHER_EMAIL=other@example.test\nOTHER_APP_PASSWORD=unrelated-synthetic\n')
        self.env.chmod(0o600)
        self.managed = self.root/'managed-keys'; self.managed.write_text(''); self.managed.chmod(0o600)
        p = patch('agentj.keys.env_path', return_value=str(self.managed)); p.start(); self.addCleanup(p.stop)
        p = patch('agentj.keys_found.manager_env', return_value={}); p.start(); self.addCleanup(p.stop)
        M.store_secret(self.st, b'expired-synthetic', AGENT)

    def registered(self):
        preferences.put(self.host.preferences, 'api_keys.extra_env_files', [str(self.env)])

    async def test_imap_recovers_registered_same_account_without_asking(self):
        self.registered(); self.host.elevate = SimpleNamespace(request=AsyncMock())
        res = await self.m.check()
        self.assertTrue(res['ok'], res)
        self.assertEqual(M.load_secret(self.st, AGENT), CODE)
        self.host.elevate.request.assert_not_called()
        self.assertEqual(self.smtp.sent, [], 'verification must not send a message')
        self.assertNotIn(CODE, self.st.log_path.read_text())

    async def test_secret_card_env_destination_is_a_registered_source(self):
        elevate.remember(self.st, 'a'*32, {'kind':'secret', 'name':'OWNER_GMAIL_APP_PASSWORD', 'result':'saved',
            'where':{'mode':'env','path':str(self.env),'key':'OWNER_GMAIL_APP_PASSWORD'}})
        res = await self.m.check()
        self.assertTrue(res['ok'], res)
        self.assertEqual(M.load_secret(self.st, AGENT), CODE)

    async def test_account_hint_is_not_required_when_both_servers_accept(self):
        self.registered(); self.env.write_text('OWNER_GMAIL_EMAIL=other@example.test\nOWNER_GMAIL_APP_PASSWORD='+CODE+'\n')
        res = await self.m.check()
        self.assertTrue(res['ok'], res)
        self.assertEqual(M.load_secret(self.st, AGENT), CODE)

    async def test_smtp_recovers_before_data_and_sends_once(self):
        from agentj.contacts_serve import ContactService
        self.registered()
        cfg = M.read_config(self.st)
        msg = M.build_notes(cfg, ['boss@example.test'], 'Test', 'Synthetic body', '')
        contacts = ContactService(SimpleNamespace(host=self.host, st=self.st))
        await asyncio.to_thread(contacts.smtp_send, cfg, 'expired-synthetic', msg, ['boss@example.test'])
        self.assertEqual(len(self.smtp.sent), 1)
        self.assertEqual(M.load_secret(self.st, AGENT), CODE)

    async def test_password_card_first_reuses_a_tested_existing_candidate(self):
        self.registered(); self.host.elevate = SimpleNamespace(request=AsyncMock(side_effect=AssertionError('must reuse first')))
        res = await self.m.secret_card()
        self.assertEqual(res.get('result'), 'saved', res)
        self.host.elevate.request.assert_not_called()

    async def test_saved_card_account_hint_needs_no_sibling_address_variable(self):
        self.env.write_text('OWNER_GMAIL_APP_PASSWORD='+CODE+'\n')
        elevate.remember(self.st, 'b'*32, {'kind':'secret', 'name':'OWNER_GMAIL_APP_PASSWORD', 'result':'saved',
            'mail_account':AGENT, 'where':{'mode':'env','path':str(self.env),'key':'OWNER_GMAIL_APP_PASSWORD'}})
        self.assertTrue((await self.m.check())['ok'])

    async def test_candidate_must_pass_smtp_too(self):
        self.registered(); self.smtp.password = 'different-synthetic'
        res = await self.m.check()
        self.assertFalse(res['ok'])
        self.assertEqual(M.load_secret(self.st, AGENT), 'expired-synthetic')
        self.assertEqual(self.smtp.sent, [])

    async def test_symlink_source_never_gets_used(self):
        link = self.root/'link'; link.symlink_to(self.env)
        preferences.put(self.host.preferences, 'api_keys.extra_env_files', [str(link)])
        with patch.object(M, 'test_connection', side_effect=AssertionError('unsafe source must not be tried')):
            self.assertFalse((await self.m.check())['ok'])

    async def test_configuration_changed_during_verification_aborts_takeover(self):
        from agentj import mail_recovery as R
        self.registered(); cfg = M.read_config(self.st)
        def verify(*args):
            changed = dict(cfg, generation='changed'); M.save_config(self.st, changed)
            return {'ok':True}
        with patch.object(M, 'test_connection', side_effect=verify):
            self.assertIsNone(R.recover(self.host, cfg, 'expired-synthetic'))
        self.assertEqual(M.load_secret(self.st, AGENT), 'expired-synthetic')

    async def test_network_failure_does_not_try_credentials(self):
        self.registered()
        with patch.object(M, '_imap', side_effect=OSError('synthetic network')), \
             patch('agentj.mail_recovery.recover', side_effect=AssertionError('not an auth failure')):
            self.assertEqual((await self.m.check())['why'], 'imap_network')

    async def test_explicit_app_password_is_verified_even_without_a_mail_name(self):
        self.registered()
        self.env.write_text('KIMI_EMAIL='+AGENT+'\nKIMI_APP_PASSWORD='+CODE+'\n')
        self.assertTrue((await self.m.check())['ok'])
        self.assertEqual(M.load_secret(self.st, AGENT), CODE)

    async def test_card_account_is_derived_only_from_mail_purpose(self):
        from agentj.mail_recovery import card_account
        self.assertIsNone(card_account({'name':'KIMI_APP_PASSWORD','purpose':'Kimi account owner@gmail.com'}))
        self.assertEqual(card_account({'name':'OWNER_APP_PASSWORD','purpose':'Mailbox app password for '+AGENT}), AGENT)
        self.assertIsNone(card_account({'name':'OWNER_APP_PASSWORD','purpose':'Mail accounts a@example.test and b@example.test'}))

    async def test_legacy_card_without_account_metadata_reuses_before_asking(self):
        self.env.write_text('OWNER_APP_PASSWORD='+CODE+'\n')
        elevate.remember(self.st, 'c'*32, {'kind':'secret', 'result':'saved',
            'where':{'mode':'env','path':str(self.env),'key':'OWNER_APP_PASSWORD'}})
        self.host.elevate = SimpleNamespace(request=AsyncMock(side_effect=AssertionError('already given')))
        before = self.env.read_bytes()
        res = await self.m.secret_card()
        self.assertEqual(res.get('result'), 'saved', res)
        self.host.elevate.request.assert_not_called()
        self.assertEqual(M.load_secret(self.st, AGENT), CODE)
        self.assertEqual(self.env.read_bytes(), before)
        self.assertEqual(self.smtp.sent, [])
        self.assertNotIn(CODE, self.st.log_path.read_text())

    async def test_unbound_registered_password_must_pass_both_servers(self):
        self.registered(); self.env.write_text('OWNER_APP_PASSWORD='+CODE+'\n')
        self.smtp.password = 'different-synthetic'
        self.host.elevate = SimpleNamespace(request=AsyncMock(return_value={'result':'requested'}))
        await self.m.secret_card()
        self.host.elevate.request.assert_awaited_once()
        self.assertEqual(M.load_secret(self.st, AGENT), 'expired-synthetic')
        self.assertEqual(self.smtp.sent, [])

    async def test_unrelated_generic_password_is_not_an_app_password(self):
        self.registered(); self.env.write_text('KIMI_PASSWORD='+CODE+'\n')
        elevate.remember(self.st, 'd'*32, {'kind':'secret', 'result':'saved',
            'where':{'mode':'env','path':str(self.env),'key':'KIMI_PASSWORD'}})
        with patch.object(M, 'test_connection', side_effect=AssertionError('not an app password')):
            self.assertFalse((await self.m.check())['ok'])

    async def test_all_registered_app_passwords_are_tried_before_asking(self):
        self.registered()
        self.env.write_text(''.join('MAIL%d_APP_PASSWORD=wrong-synthetic-%d\n' % (i,i) for i in range(9))+
                            'LAST_APP_PASSWORD='+CODE+'\n')
        self.host.elevate = SimpleNamespace(request=AsyncMock(side_effect=AssertionError('try all candidates')))
        self.assertEqual((await self.m.secret_card()).get('result'), 'saved')
        self.assertEqual(M.load_secret(self.st, AGENT), CODE)
        self.host.elevate.request.assert_not_called()
