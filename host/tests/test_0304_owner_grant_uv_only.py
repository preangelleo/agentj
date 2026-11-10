"""0304 A2-②: a same-owner task under a signed OTG grant needs the grant only for UV steps (uv_list.required — the #537
list — or a UV card). Non-UV steps follow the ordinary same-owner rules; secret transfer and the web terminal keep the grant;
a forged / unverified same_owner label gains nothing."""
import _hermetic  # noqa: F401
import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from agentj import application_ingress, approval_level, elevate, google_api, owner_grant, plaza_perms, provenance, term_cli
from _p0304_cards import otg_agent, plain

OTG = otg_agent


def host_for(agent):
    return SimpleNamespace(agent=agent, st=mock.Mock(), preferences={}, stopped=lambda: False, activity=mock.Mock())


class UVOnly(unittest.TestCase):
    def test_non_uv_step_needs_no_grant(self):
        a = OTG()
        self.assertTrue(owner_grant.owner_grant_source(a))
        for tool, inp in (('Bash', {'command': 'ls'}), ('agentj google drive upload', {}), ('agentj plaza', {}),
                          ('agentj support', {})):
            self.assertFalse(provenance.owner_grant_required(a, tool, inp), tool)
        self.assertIsNone(google_api.refusal(a, None, 'drive_ls', {'t': 'google_api', 'op': 'drive_ls'}), 'a delegated read runs like any same-owner task')
        self.assertIsNone(google_api.refusal(a, None, 'gmail_send', {'t': 'google_api', 'op': 'gmail_send'}), 'send still raises its own signed card later')
        with mock.patch.object(application_ingress, 'allowed', return_value=False):
            gate = plaza_perms.turn_gate(host_for(a))
        self.assertEqual((gate['gated'], gate['via'], gate['otg']), (False, 'delegated', True))
        # The ordinary rules after the grant gate: a delegated turn is still not the owner speaking for external sends.
        h = host_for(a)
        with mock.patch.object(approval_level, 'level', return_value='standard'):
            self.assertTrue(elevate.external_support(h, 'support'))
        self.assertFalse(elevate.external_support(host_for(plain(provenance.phone())), 'support'))

    def test_uv_step_still_needs_grant(self):
        a = OTG()
        for tool, inp in (('agentj fn run', {'name': 'x'}), ('agentj secret send', {}), ('device.revoke', {}),
                          ('agentj config', {'changes': {'approval.level': 'harness'}})):
            self.assertTrue(provenance.owner_grant_required(a, tool, inp), tool)
        self.assertTrue(provenance.owner_grant_required(a), 'no step named = UV (fail closed)')
        self.assertTrue(provenance.owner_grant_required(a, uv=True))
        refused = asyncio.run(plaza_perms.signed_card(host_for(a), 'demo', {'names': ['demo']},
                                                      gate={'gated': False, 'via': 'delegated', 'why': None, 'otg': True},
                                                      require_passkey=True))
        self.assertEqual(refused, {'ok': False, 'why': 'owner_grant_scope'}, 'a plaza secret grant is a UV card')
        with mock.patch.object(approval_level, 'level', return_value='harness'):
            self.assertFalse(approval_level.autonomous(host_for(a)), 'Full access never covers a grant task')

    def test_secret_transfer_still_needs_grant(self):
        a = OTG()
        self.assertEqual(provenance.owner_grants(a), ('synthetic-peer', []), 'secret ask / give route to the origin host')
        for tool in ('agentj secret send', 'agentj secret request', 'agentj secret grant', 'terminal.open', 'terminal.enable'):
            self.assertTrue(provenance.owner_grant_required(a, tool, {}), tool)

        async def terminal(agent):
            h = host_for(agent)
            h._approvers = lambda: []
            with mock.patch.object(approval_level, 'level', return_value='harness'), \
                    mock.patch.object(approval_level, 'level_grant', return_value='synthetic-grant'), \
                    mock.patch('agentj.approvals.record'):
                return await term_cli.host_card(h, 'open terminal', {'synthetic': 1}, face=False)
        self.assertEqual(asyncio.run(terminal(a)), 'no_device', 'the web terminal never auto-allows for a grant task')
        self.assertEqual(asyncio.run(terminal(plain(provenance.phone()))), 'allow', 'control: owner under Full access')

    def test_forged_same_owner_source_refused(self):
        forged = {'kind': 'same_owner', 'role': 'task', 'fid': 'synthetic-peer', 'otg_required': True}
        guest = plain(provenance.email_guest('guest@example.com'))

        async def under_request(agent, src):
            with provenance.request_source(src):
                return (google_api.refusal(agent, None, 'drive_ls', {'t': 'google_api', 'op': 'drive_ls'}), provenance.delegated_turn(agent),
                        provenance.owner_grant_required(agent, 'Bash', {'command': 'ls'}))
        refusal, delegated, _ = asyncio.run(under_request(guest, forged))
        self.assertEqual(refusal['why'], 'group_source', 'a guest turn claiming same_owner is still a guest')
        self.assertFalse(delegated)
        for label in ({'kind': 'same_owner', 'role': 'owner'}, {'kind': 'same_owner', 'role': 'group'},
                      {'kind': 'same_owner'}):
            a = plain(label)
            self.assertEqual(provenance.turn_source(a)['kind'], 'unknown', label)
            self.assertFalse(provenance.delegated_turn(a))
            self.assertEqual(google_api.refusal(a, None, 'drive_ls', {'t': 'google_api', 'op': 'drive_ls'})['why'], 'group_source')
            with mock.patch.object(application_ingress, 'allowed', return_value=False):
                self.assertTrue(plaza_perms.turn_gate(host_for(a))['gated'])
        # Agent-written grant ids never survive normalize (task_source keeps only host-verified shapes).
        self.assertNotIn('otg', provenance.normalize({'kind': 'same_owner', 'role': 'task', 'fid': 'synthetic-peer',
                                                      'otg': ['../../x']}))


if __name__ == '__main__':
    unittest.main()
