"""0306 preview fr_set (TB 11-10 12:23): a Same owner round whose peer still has to bind a remote (too_old →
every confirm is refused with peer_binding_required) must not put a six-digit card on the owner's remotes — the
chairman's Mac Chrome showed 958145, every tap answered 「确认没生效」. The friends page carries same_owner_why."""
import _hermetic  # noqa: F401
import unittest
from unittest.mock import patch

from test_0304_any_signer import TwoHosts


def pairing_cards(svc, fid):
    return [a for a in svc.asks.values() if a.data.get('friend') == fid and a.extra.get('pairing')]


class NoCardWhilePeerMustBind(TwoHosts):
    async def test_no_card_while_peer_binding_required_then_card_once_ready(self):
        b = self.B.svc
        with patch.object(b.xfer, 'my_roster', lambda: []):
            await self.write(self.A, 'air', self.signed(self.A, 'air', {'friend': self.B.id, 'op': 'group', 'value': 'same-owner'}, uv=False))
            await self.pump()
        a = self.A.svc.same_owner
        self.assertEqual(a.info(self.B.id).get('same_owner_why'), 'peer_binding_required')
        self.assertEqual(pairing_cards(self.A.svc, self.B.id), [], 'no card that can never be confirmed')
        a.restart(self.B.id)                       # R-F11 restart (a remote of the snapshot was replaced/revoked)
        self.assertEqual(pairing_cards(self.A.svc, self.B.id), [], 'a restart does not resurrect it either')
        with patch.object(a, 'too_old', lambda *_a, **_k: False):   # the peer binds a remote → its packet re-runs pair_card
            a.pair_card(self.B.id)
        self.assertEqual(len(pairing_cards(self.A.svc, self.B.id)), 1)


if __name__ == '__main__':
    unittest.main()
