"""0306 A5 (chairman 11-09 22:5x, ledger code:0306-sameowner-binding-flow): a remote's first Same owner start binds
it to the roster (Touch ID once) and then continues by itself — the continuation ticket saves a tap, never a UV.

Contract: reports/qa/0304-frozen/contract-a5.md; threat model THREAT-MODEL.md §A5. Fixture: test_0304_any_signer
(two real serve.Host + PeerService over FakeNet, real Ed25519 controls, real WebAuthn assertions).
"""
import _hermetic  # noqa: F401
import unittest
import unittest.mock

from agentj import same_owner as so
from test_0304_any_signer import TwoHosts


class BindingFlow(TwoHosts):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.events = []
        original = self.A.st.log
        def log(ev, **kw):
            self.events.append((ev, kw))
            return original(ev, **kw)
        self.A.st.log = log

    def start_obj(self, name='safari', cont=None, friend=None):
        obj = self.signed(self.A, name, {'friend': friend or self.B.id, 'op': 'group', 'value': 'same-owner'}, uv=False)
        if cont is not None:
            obj['cont'] = cont
        return obj

    def continued(self):
        return [kw.get('result') for ev, kw in self.events if ev == 'same_owner_continue']

    async def first_tap(self, name='safari'):
        res = await self.write(self.A, name, self.start_obj(name))
        self.assertEqual((res['ok'], res.get('why'), res.get('next')), (False, 'roster_binding_required', 'bind'))
        self.assertRegex(res.get('cont', ''), r'^[0-9a-f]{32}$')
        self.assertTrue(any(o.get('t') == 'roster_bind_offer' for o in self.A.broadcasts), 'the bind offer is still broadcast')
        self.assertFalse(self.A.svc.same_owner.row(self.B.id).get('nonce'))
        return res['cont']

    async def test_continue_after_bind_still_needs_confirm_uv(self):
        ticket = await self.first_tap()
        await self.bind(self.A, 'safari')                      # the one Touch ID (rosterbind)
        res = await self.write(self.A, 'safari', self.start_obj(cont=ticket))
        self.assertTrue(res['ok'], res)
        self.assertEqual(self.continued(), ['ok'])
        await self.pump()
        row = self.A.svc.same_owner.row(self.B.id)
        self.assertTrue(row.get('nonce') and not row.get('token'), 'started; nothing consented yet')
        self.assertTrue(self.A.svc.same_owner.v4(self.B.id))
        self.assertTrue(self.pairing_cards(self.A), 'the six-digit pairing card is up')
        self.assertEqual(self.A.svc.same_owner.code(self.B.id), self.B.svc.same_owner.code(self.A.id))
        # the confirm still needs its own fresh UV (separate challenge from the binding)
        res = await self.confirm(self.A, 'safari', uv=False)
        self.assertFalse(res['ok'])
        self.assertFalse(self.A.svc.same_owner.row(self.B.id).get('token'))
        self.assertTrue((await self.confirm(self.A, 'safari'))['ok'])
        self.assertTrue((await self.confirm(self.B, 'pixel'))['ok'])
        self.assertTrue(self.A.svc.same_owner.active(self.B.id))
        self.assertTrue(self.B.svc.same_owner.active(self.A.id))

    async def test_continue_ticket_single_use(self):
        ticket = await self.first_tap()
        await self.bind(self.A, 'safari')
        self.assertTrue((await self.write(self.A, 'safari', self.start_obj(cont=ticket)))['ok'])
        res = await self.write(self.A, 'safari', self.start_obj(cont=ticket))
        self.assertTrue(res['ok'], 'a bound remote\'s own signed start is a normal start')
        self.assertEqual(self.continued(), ['ok', 'unknown'], 'the second use of the ticket is refused')
        sow = self.A.svc.same_owner
        t = sow.cont_issue('dev', 1, self.B.id)
        with unittest.mock.patch.object(self.A.svc.xfer, 'bound', lambda d: True):
            self.assertIsNone(sow.cont_take(t, 'dev', 1, self.B.id))
            self.assertEqual(sow.cont_take(t, 'dev', 1, self.B.id), 'unknown')

    async def test_continue_ticket_other_device_or_session_refused(self):
        ticket = await self.first_tap()
        await self.bind(self.A, 'safari')
        res = await self.write(self.A, 'oppo', self.start_obj('oppo', cont=ticket))   # another bound remote
        self.assertTrue(res['ok'], 'oppo\'s own signed start is a normal start')
        self.assertEqual(self.continued(), ['other_remote'])
        sow = self.A.svc.same_owner
        safari, s = self.A.phones['safari']
        with unittest.mock.patch.object(self.A.svc.xfer, 'bound', lambda d: True):
            t = sow.cont_issue(safari.did, s.cid, self.B.id)
            self.assertEqual(sow.cont_take(t, safari.did, s.cid + 100, self.B.id), 'other_remote', 'another session')
            t = sow.cont_issue(safari.did, s.cid, self.B.id)
            self.assertEqual(sow.cont_take(t, self.A.phones['oppo'][0].did, s.cid, self.B.id), 'other_remote')
            self.assertEqual(sow.cont_take(t, safari.did, s.cid, self.B.id), 'unknown', 'a refused presentation burns it')

    async def test_continue_unbound_gets_fresh_ticket(self):
        ticket = await self.first_tap()
        res = await self.write(self.A, 'safari', self.start_obj(cont=ticket))        # binding never happened
        self.assertEqual((res['ok'], res.get('why'), res.get('next')), (False, 'roster_binding_required', 'bind'))
        self.assertNotEqual(res.get('cont'), ticket)
        self.assertEqual(self.continued(), ['unbound'])
        self.assertFalse(self.A.svc.same_owner.row(self.B.id).get('nonce'), 'never skips the binding')

    async def test_continue_ticket_expired_refused(self):
        ticket = await self.first_tap()
        await self.bind(self.A, 'safari')
        now = self.A.svc.clock()
        self.A.svc.clock = lambda: now + so.CONT_TTL + 1
        res = await self.write(self.A, 'safari', self.start_obj(cont=ticket))
        self.assertTrue(res['ok'])                            # bound now → the normal path starts it anyway
        self.assertEqual(self.continued(), ['expired'])

    async def test_continue_ticket_bound_to_friend(self):
        sow = self.A.svc.same_owner
        safari, s = self.A.phones['safari']
        with unittest.mock.patch.object(self.A.svc.xfer, 'bound', lambda d: True):
            t = sow.cont_issue(safari.did, s.cid, self.B.id)
            self.assertEqual(sow.cont_take(t, safari.did, s.cid, 'AJ-other-friend'), 'other_friend')
        ticket = await self.first_tap()
        await self.bind(self.A, 'safari')
        res = await self.write(self.A, 'safari', self.start_obj(cont=ticket, friend='f' * 32))
        self.assertFalse(res['ok'])                           # no such friend → the normal path refuses it
        self.assertEqual(self.continued(), ['other_friend'])
        self.assertFalse(self.A.svc.same_owner.row(self.B.id).get('nonce'), 'the ticket did not start the ticket\'s friend')


if __name__ == '__main__':
    unittest.main()
