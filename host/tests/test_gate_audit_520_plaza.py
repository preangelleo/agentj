"""#520 independent QA: signed Full access uses parent authority; credential scope keeps UV."""
import _hermetic  # noqa: F401
import asyncio
from unittest import mock

from agentj import approval_level, market, plaza_host, plaza_perms, preferences, provenance
from agentj.compose import Send
from test_plaza_install_card import CardFixture


class PlazaFullAccess520(CardFixture):
    def test_full_install_has_no_card_and_no_new_signature_claim(self):
        self.harness()
        self.package()
        rc, text = self.install_flow()
        self.assertEqual(rc, 0, text)
        self.assertEqual(self.cards(), [])
        self.assertTrue(self.replies[-1]['inherited'])
        self.assertEqual(self.replies[-1]['parent_grant'], 'synthetic-switch')
        self.assertIn('no new signature requested', text)
        self.assertNotIn('on the phone (signed)', text)
        saved = market.read_installed(self.st)[-1]['perm_grant']
        self.assertTrue(saved['inherited'])
        self.assertEqual(saved['parent_grant'], 'synthetic-switch')

    def test_standard_keeps_signed_card(self):
        self.host.preferences = preferences.validate({'approval': {'level': 'standard'}})
        self.package()
        rc, text = self.install_flow()
        self.assertEqual(rc, 0, text)
        self.assertEqual(len(self.cards()), 1)
        self.assertNotIn('inherited', self.replies[-1])

    def test_standard_wrong_signature_cannot_install(self):
        self.host.preferences = preferences.validate({'approval': {'level': 'standard'}})
        self.package()
        rc, text = self.install_flow('wrong_digest')
        self.assertEqual(rc, market.EXIT_PERMISSION, text)
        self.assertEqual(market.read_installed(self.st), [])
        self.assertEqual(len(self.cards()), 1)

    def test_new_secret_scope_requires_uv_under_full(self):
        self.harness()
        self.package(requires={'harness': ['codex'], 'env': [
            {'name': 'OPTIONAL_TOKEN', 'required': False, 'secret': True,
             'description_zh': '可选', 'how_to_get': 'example.com'}]},
            permissions={'tools': ['WebFetch'], 'secrets': ['OPTIONAL_TOKEN']})
        rc, text = self.install_flow('deny')
        self.assertEqual(rc, market.EXIT_PERMISSION, text)
        self.assertEqual(market.read_installed(self.st), [])
        self.assertEqual(len(self.cards()), 1)
        self.assertTrue(self.cards()[-1]['passkey_required'])

    def test_inherited_result_does_not_survive_revoke(self):
        self.harness()
        async def stale_allow(*args, **kw):
            approval_level.drop_signer(self.st, approval_level.KEY)
            return {'behavior': 'allow', 'grant_info': {'rid': 'a'*32,
                'device': self.phone.did, 'inherited': True, 'parent_grant': 'synthetic-switch'}}
        with mock.patch.object(self.host, 'ask', new=stale_allow):
            result = asyncio.run(plaza_perms.signed_card(self.host, 'flow', {'items': [], 'summary': 'flow'}))
        self.assertFalse(result['ok'])
        self.assertEqual(result['why'], 'no_signature')

    def test_external_mail_cannot_install_under_full(self):
        self.harness()
        self.host.agent.cur_send = Send('outside','outside',1,source=provenance.email_guest('guest@example.com'))
        self.package()
        rc, text = self.install_flow()
        self.assertEqual(rc, market.EXIT_PERMISSION, text)
        self.assertEqual(self.cards(), [])
        self.assertEqual(market.read_installed(self.st), [])
        self.assertEqual(self.replies[-1]['why'], 'group_source')

    def test_group_secret_after_display_limit_still_requires_uv(self):
        self.harness()
        infos = [dict(name='synthetic', version='1.0.0', sha256='a'*64, digest='b'*64,
                      target='/tmp/synthetic', certified=False, publisher={'fp':None},
                      extra=[{'kind':'tool', 'name':'WebFetch'}]*40 + [{'kind':'secret','name':'EXTRA_TOKEN'}])]
        signed = mock.AsyncMock(return_value={'ok':False,'why':'timeout'})
        with mock.patch.object(plaza_perms,'signed_card',new=signed), \
             mock.patch.object(plaza_perms,'group_card_text',return_value='synthetic group'):
            asyncio.run(plaza_host._card(self.host,infos,True,[],{'gated':False},None,None,False))
        self.assertEqual(len(signed.await_args.args[2]['items']), 40)
        self.assertTrue(signed.await_args.kwargs['require_passkey'])
