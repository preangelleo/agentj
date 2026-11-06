"""#520: a host system follow-up reaches mail's normal authorization path; external sources never do."""
import _hermetic  # noqa: F401
import asyncio
from types import SimpleNamespace
from unittest import mock

from agentj import contacts_serve, provenance
from test_p551_confirm_levels import Base


class GateAudit520Contacts(Base):
    def service(self):
        cs = contacts_serve.ContactService.__new__(contacts_serve.ContactService)
        cs.host, cs.st = self.host, self.st
        cs.mail_state = lambda: ({'address': 'me@example.com'}, '', None)
        cs.resolve = mock.Mock(return_value=('bob@example.com', None))
        cs.build = mock.Mock(return_value=object())
        return cs

    def send_once(self, source, *, foreign=False, request=None):
        self.host.agent.cur_send = SimpleNamespace(source=source)
        self.host.agent.untrusted_foreign = foreign
        self.host.agent.untrusted = lambda: foreign
        cs = self.service()
        self.host.ask = mock.AsyncMock(return_value={'behavior': 'deny'})
        async def go():
            with mock.patch('agentj.mail.send_target', return_value={}):
                if request is not None:
                    with provenance.request_source(request):
                        return await cs.agent_send({'to': ['bob@example.com'], 'subject': 'report'})
                return await cs.agent_send({'to': ['bob@example.com'], 'subject': 'report'})
        return asyncio.run(go())

    def test_signed_full_system_without_chain_reaches_mail_action_guard(self):
        self.harness()
        got = self.send_once(provenance.system())
        self.assertEqual(got['why'], 'denied', got)  # the normal action guard, not source rejection
        self.host.ask.assert_awaited_once()

    def test_standard_system_without_chain_keeps_original_scope(self):
        got = self.send_once(provenance.system())
        self.assertEqual(got['why'], 'group_source')
        self.host.ask.assert_not_called()

    def test_full_external_unknown_group_and_foreign_system_cannot_send(self):
        self.harness()
        cases = [(provenance.normalize(None), False, None),
                 (provenance.telegram_group(5, 6, 'proxy', 1), False, None),
                 (provenance.email_guest('guest@example.com'), False, None),
                 (provenance.system(), True, None),
                 (provenance.system(), False, provenance.email_guest('guest@example.com'))]
        for src, foreign, request in cases:
            with self.subTest(src=src, foreign=foreign, request=request):
                got = self.send_once(src, foreign=foreign, request=request)
                self.assertEqual(got['why'], 'group_source', got)
                self.host.ask.assert_not_called()

    def test_full_system_secret_and_spend_keep_uv(self):
        self.harness()
        self.host.agent.cur_send = SimpleNamespace(source=provenance.system())
        for cmd in ('echo "$API_TOKEN"', 'stripe charges create --amount 500'):
            with self.subTest(cmd=cmd):
                got = self.ask(cmd)
                self.assertEqual(got['behavior'], 'deny')
                self.assertTrue(self.cards()[-1]['passkey_required'])
