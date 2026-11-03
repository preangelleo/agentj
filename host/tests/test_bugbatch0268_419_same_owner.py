"""#419 residual (after P590): a same-owner request that was received / queued while the link was active must not end in a
bare 'cancelled' or a silent 'expired' ("nobody claimed") when the link ends before the main Agent gets it. It is blocked
with the fixed reason `relationship`, durable, with a receipt to the sender (why) and an owner line; never auto-replayed."""
import unittest
from unittest.mock import patch

import test_p405_same_owner as baseline
from test_p71_peer_service import pmsg, settle_pending


class LinkEndedBeforeHandoff(baseline.SameOwnerTests):
    def assert_relationship_block(self, mid):
        row = self.svc.guards.row(self.b, mid)
        self.assertEqual((row['state'], row.get('reason')), ('blocked', 'relationship'))
        self.assertTrue(any(o.get('t') == 'relay_state' and o.get('mid') == mid and o.get('state') == 'blocked'
                            and o.get('why') == 'relationship' for _, o in self.svc.net.sent))
        self.assertFalse(any(o.get('t') == 'relay_state' and o.get('mid') == mid and o.get('state') in ('cancelled', 'expired')
                             for _, o in self.svc.net.sent))
        self.assertTrue(any(mid in n and 'relationship' in n for n in self.host.notices))

    async def test_queued_then_link_revoked_is_blocked_with_reason(self):
        await self.confirm()
        m = pmsg('主 Agent 断开期间排队')
        agent, self.host.agent = self.host.agent, None
        self.host.hist_update = lambda *a, **kw: None
        await self.svc.same_owner.forward(self.b, m)
        self.assertIn(m['mid'], self.svc.same_owner.row(self.b).get('queue', {}))
        self.svc.same_owner.consent(self.b, False)
        self.host.agent = agent
        await self.svc.same_owner.retry()
        await settle_pending(self.svc)
        self.assertFalse(self.sends, 'never delivered after the link ended')
        self.assert_relationship_block(m['mid'])

    async def test_forwarded_then_link_inactive_sweep_is_not_silent_expiry(self):
        await self.confirm()
        m = pmsg('已交接后关系失效')
        self.svc.guards.accept(self.b, m)
        with self.svc.store._edit('same-owner.json', {}) as rows:
            rows[self.b].setdefault('forwarded', []).append(m['mid'])
        self.clock.t += self.store.settings()['relay_pending_seconds'] + 1
        with patch.object(self.svc.same_owner, 'active', return_value=False):
            self.svc.guards.sweep()
            await settle_pending(self.svc)
        self.assert_relationship_block(m['mid'])

    async def test_plain_friend_request_still_expires_as_before(self):
        """not a same-owner handoff (never queued / forwarded): the old lifecycle is unchanged."""
        await self.confirm()
        m = pmsg('普通请求')
        self.svc.guards.accept(self.b, m)
        self.clock.t += self.store.settings()['relay_pending_seconds'] + 1
        with patch.object(self.svc.same_owner, 'active', return_value=False):
            self.svc.guards.sweep()
            await settle_pending(self.svc)
        self.assertEqual(self.svc.guards.row(self.b, m['mid'])['state'], 'expired')


if __name__ == '__main__':
    unittest.main()
