"""#520: repeat terminal access skips only non-UV confirmation in signed Full access."""
import _hermetic
import asyncio
import contextlib
import io
from types import SimpleNamespace
from unittest import mock
from agentj import term_cli, provenance
from agentj.compose import Send
from test_p551_confirm_levels import Base

class TerminalGates(Base):
    def run_card(self, face=False):
        with mock.patch.object(self.host, '_approvers', return_value=[]):
            return asyncio.run(term_cli.host_card(self.host, 'terminal', {'op':'allow','devices':[self.phone.did]}, face=face))
    def test_repeat_terminal_full_works_offline(self):
        self.harness()
        self.assertEqual(self.run_card(), 'allow')
        self.assertFalse(self.cards())
    def test_first_terminal_still_needs_uv_device(self):
        self.harness()
        self.assertEqual(self.run_card(True), 'no_device')
    def test_standard_keeps_confirmation(self):
        self.assertEqual(self.run_card(), 'no_device')
    def test_external_source_cannot_widen_terminal(self):
        self.harness()
        for source in (provenance.normalize(None), provenance.email_guest('guest@example.com')):
            self.host.agent.cur_send=Send('outside','outside',1,source=source)
            self.assertEqual(self.run_card(), 'no_device')
    def test_estop_precedes_automation(self):
        self.harness()
        with mock.patch.object(self.host, 'stopped', return_value=True):
            self.assertEqual(self.run_card(), 'estop')

    def test_non_tty_cli_reaches_host_admission_without_owner_claim(self):
        self.harness()
        from agentj import ctl_auth, names, state, terminal
        args=SimpleNamespace(action='allow',target=self.phone.did,device=[],allow_web=False,
                             hours=1,minutes=None,json=True)
        with mock.patch.object(state,'State',return_value=self.st), \
             mock.patch.object(terminal,'platform_ok',return_value=True), \
             mock.patch.object(term_cli,'_password_sudo',return_value=None), \
             mock.patch.object(self.st,'passkey_of_device',return_value={'id':'synthetic'}), \
             mock.patch.object(ctl_auth,'has_tty',return_value=False), \
             mock.patch.object(names,'ctl_call',return_value={'ok':True}) as call, \
             contextlib.redirect_stdout(io.StringIO()):
            term_cli.cmd_terminal(args)
        req=call.call_args.args[1]
        self.assertEqual(req['cmd'],'terminal_cfg')
        self.assertEqual(req['op'],'allow')
        self.assertEqual(req['device'],self.phone.did)
        self.assertNotIn('claim',req)
        self.assertRegex(req['code'],r'^\d{6}$')

    def test_control_socket_full_system_routes_to_real_terminal_uv_gate(self):
        self.harness()
        self.host.agent.cur_send=Send('system','system',1,source=provenance.system())
        self.host._ctl_send=mock.AsyncMock()
        from agentj import ctl_auth
        with mock.patch.object(ctl_auth,'has_tty',return_value=False):
            ok=asyncio.run(self.host._ctl_gate(object(),{'cmd':'terminal_cfg','op':'enable'},12345))
        self.assertTrue(ok)
        self.host._ctl_send.assert_not_awaited()

    def test_control_socket_standard_and_full_unknown_keep_claim_boundary(self):
        self.host._ctl_send=mock.AsyncMock()
        for full in (False,True):
            if full:
                self.harness()
                self.host.agent.cur_send=Send('outside','outside',1,source=provenance.normalize(None))
            self.assertFalse(asyncio.run(self.host._ctl_gate(object(),{'cmd':'terminal_cfg','op':'enable'},12345)))
        self.assertEqual(self.host._ctl_send.await_count,2)

    def test_first_use_online_card_still_requires_uv(self):
        self.harness()
        async def run():
            task=asyncio.create_task(term_cli.host_card(self.host,'terminal',{'op':'enable','devices':[self.phone.did]},face=True))
            await asyncio.sleep(0.01)
            self.assertTrue(self.cards()[-1]['passkey_required'])
            pending=next(iter(self.host.asks.values()))
            pending.fut.set_result(('deny',self.phone.did,self.st.sign_key(self.phone.did),None))
            self.assertEqual(await task,'deny')
        asyncio.run(run())
