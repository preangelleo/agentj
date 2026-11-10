"""0304 A1-2 / A1-7 (chairman 12:01 「万一我的主用 iPhone Air 丢了呢？」): any bound remote can (with its own UV)
unbind another one at once; the unbound remote's cards and sessions end immediately, not at TTL.

Real serve.Host + PeerService over FakeNet (fixture from test_0304_any_signer), real Ed25519 controls and real
WebAuthn assertions. Who may unbind is unchanged (remotes.py: another remote needs the caller's UV; itself only its
signature) — these tests pin it.
"""
import _hermetic  # noqa: F401
import asyncio
import secrets
import unittest

from agentj import controls, passkey
from test_controls import sign
from test_0304_any_signer import TwoHosts


class RevokeLost(TwoHosts):
    def unbind(self, side, by, target_name, *, uv=True):
        ph, _ = side.phones[by]
        target = {'device': side.phones[target_name][0].did}
        n = secrets.token_hex(16)
        obj = sign(ph, 'dev_unbind', target, {'t': 'dev_unbind', 'r': secrets.token_hex(8), **target}, n=n)
        if uv and hasattr(ph, 'auth'):
            ch = passkey.elevate_challenge(side.h.channel, ph.did, n, 'dev_unbind', n, controls.object_digest('dev_unbind', target))
            obj['fa'] = ph.auth.assert_(ch, b'', nonce=b'')
        return obj

    async def send_unbind(self, side, by, obj):
        _, s = side.phones[by]
        await side.h.remotes.on_phone(s, 'dev_unbind', obj)
        return next((o for cid, o in reversed(side.sent) if cid == s.cid and o.get('t') == 'ctl_res' and o.get('r') == obj['r']), None)

    async def open_card(self, h, only):
        task = asyncio.create_task(h.ask('agentj secret send', {'name': 'SYNTHETIC_NOT_READ'}, only=only))
        for _ in range(200):
            if any(a.only == only and not a.fut.done() for a in h.asks.values()) or task.done():
                break
            await asyncio.sleep(.005)
        self.assertFalse(task.done(), task.result() if task.done() else '')
        return task, next(a for a in h.asks.values() if a.only == only and not a.fut.done())

    async def test_revoked_device_cards_denied_immediately(self):
        h = self.A.h
        h.ask_ttl = 30
        oppo, _ = self.A.phones['oppo']
        air, air_s = self.A.phones['air']
        task, card = await self.open_card(h, oppo.did)
        shared, shared_card = await self.open_card(h, None)
        removed, closed = await h.revoke(oppo.did)
        self.assertTrue(removed)
        result = await asyncio.wait_for(task, 1)           # not the 30 s TTL
        self.assertEqual(result['behavior'], 'deny')
        self.assertTrue(card.fut.done())
        self.assertFalse(shared_card.fut.done(), 'a card every remote may answer stays open')
        await asyncio.sleep(.05)
        self.assertTrue(any(cid == air_s.cid and o.get('t') == 'ask_done' and o.get('id') == card.rid for cid, o in self.A.sent),
                        'the other remotes are told the card is gone')
        shared_card.fut.set_result(('deny', None, None, None))
        await asyncio.gather(shared, return_exceptions=True)

    async def test_revoked_device_special_cards_end_immediately(self):
        """Q3: review-adapter cards bound to the revoked remote (elevate login / sudo / secret input, key manager,
        mail password: `only=<device>`) end at revoke time like a deny from the phone; others stay open."""
        from types import SimpleNamespace
        h = self.A.h
        loop = asyncio.get_running_loop()
        ended = []
        def adapter(rid, only, tool='TerminalUnlock', busy=False):
            return SimpleNamespace(rid=rid, only=only, tool=tool, terminal=False, fut=loop.create_future(),
                                   card={'busy': busy}, finish=lambda result, rid=rid: ended.append((rid, result)))
        oppo = self.A.phones['oppo'][0].did
        reg = h.__dict__.setdefault('_approval_special', {})
        reg.update({'mine': adapter('mine', oppo), 'sudo': adapter('sudo', oppo, 'AdminCommand'),
                    'running': adapter('running', oppo, busy=True), 'other': adapter('other', self.A.phones['air'][0].did),
                    'shared': adapter('shared', None, 'SecretPickup')})
        await h.revoke(oppo)
        self.assertEqual(sorted(ended), [('mine', 'denied'), ('sudo', 'denied')])

    async def test_revoked_starter_ends_its_pending_same_owner_round(self):
        oppo, _ = self.A.phones['oppo']
        res = await self.write(self.A, 'oppo', self.signed(self.A, 'oppo', {'friend': self.B.id, 'op': 'group', 'value': 'same-owner'}, uv=False))
        self.assertTrue(res['ok'])
        await self.pump()
        cards = {s.name: [a.rid for a in self.pairing_cards(s)] for s in (self.A, self.B)}
        self.assertTrue(cards['A'] and cards['B'])
        await self.A.h.revoke(oppo.did)
        self.assertFalse(self.A.svc.same_owner.row(self.B.id).get('nonce'), 'the round it started ends at once')
        self.assertEqual(self.A.svc.same_owner.row(self.B.id)['end'][0], 'declined')
        self.assertTrue(set(cards['A']) <= set(self.gone(self.A)))
        await self.pump()
        self.assertFalse(self.B.svc.same_owner.row(self.A.id).get('nonce'))
        self.assertTrue(set(cards['B']) <= set(self.gone(self.B)), 'the peer\'s cards fold too')
        self.assertFalse(self.A.svc.same_owner.active(self.B.id))

    async def test_revoked_consenting_remote_ends_unfinished_round(self):
        await self.start(self.A, 'air')
        self.assertTrue((await self.confirm(self.A, 'oppo'))['ok'])        # A's consent signed by oppo, B not yet
        self.assertTrue(self.A.svc.same_owner.row(self.B.id).get('token'))
        await self.A.h.revoke(self.A.phones['oppo'][0].did)
        self.assertFalse(self.A.svc.same_owner.row(self.B.id).get('token'))
        await self.pump()
        res = await self.confirm(self.B, 'mac')
        self.assertFalse(res['ok'])
        self.assertFalse(self.A.svc.same_owner.active(self.B.id))
        self.assertFalse(self.B.svc.same_owner.active(self.A.id))

    async def test_revoke_keeps_active_relation_and_other_rounds(self):
        await self.activate()
        await self.A.h.revoke(self.A.phones['air'][0].did)      # the starter of an already active relation
        self.assertTrue(self.A.svc.same_owner.active(self.B.id))
        await self.pump()
        self.assertTrue(self.B.svc.same_owner.active(self.A.id))

    async def test_any_bound_remote_unbinds_another_with_uv(self):
        h = self.A.h
        air, air_s = self.A.phones['air']                       # the first paired, "main" remote is lost
        res = await self.send_unbind(self.A, 'oppo', self.unbind(self.A, 'oppo', 'air'))
        self.assertTrue(res and res['ok'], res)
        self.assertNotIn(air.did, h.st.devices())
        self.assertNotIn(air_s.cid, h.sessions, 'its session is closed at once')
        self.assertEqual(h.st.removed_why(air.did), 'revoked')
        # the lost remote's old key can do nothing afterwards
        res = await self.send_unbind(self.A, 'air', self.unbind(self.A, 'air', 'oppo'))
        self.assertFalse(res and res.get('ok'))
        self.assertIn(self.A.phones['oppo'][0].did, h.st.devices())

    async def test_unbind_other_without_uv_refused(self):
        h = self.A.h
        res = await self.send_unbind(self.A, 'oppo', self.unbind(self.A, 'oppo', 'air', uv=False))
        self.assertEqual((res['ok'], res.get('why')), (False, 'passkey_required'))
        res = await self.send_unbind(self.A, 'keyonly', self.unbind(self.A, 'keyonly', 'air'))
        self.assertEqual((res['ok'], res.get('why')), (False, 'no_passkey'))
        self.assertIn(self.A.phones['air'][0].did, h.st.devices())
        # unbinding ITSELF needs only its own signature (gives up only its own access)
        res = await self.send_unbind(self.A, 'keyonly', self.unbind(self.A, 'keyonly', 'keyonly', uv=False))
        self.assertTrue(res and res['ok'], res)
        self.assertNotIn(self.A.phones['keyonly'][0].did, h.st.devices())

    async def test_concurrent_mutual_unbind_first_wins(self):
        h = self.A.h
        a = self.unbind(self.A, 'air', 'oppo')
        b = self.unbind(self.A, 'oppo', 'air')
        await asyncio.gather(self.send_unbind(self.A, 'air', a), self.send_unbind(self.A, 'oppo', b))
        left = {n for n in ('air', 'oppo') if self.A.phones[n][0].did in h.st.devices()}
        self.assertEqual(left, {'air'}, 'serialized: the first unbind wins, the removed remote\'s own request fails')


if __name__ == '__main__':
    unittest.main()
