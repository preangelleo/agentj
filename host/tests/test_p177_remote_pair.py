"""P177: an account-page pairing slot is released at once — a newer account-page start takes over an unscanned QR, the
owner can cancel, a busy reply says who holds the slot until when, and a failed QR build never holds the slot."""
import _hermetic
import hashlib
import json
import os
import time
import unittest
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from agentj import remote_pair as rp, serve, wire
from test_p78_remote_pair import HostFlow


class P177(HostFlow):
    def opened(self, q):
        reply = self.h.remote_pair.results[q['id']][1]
        aad = (rp.CONTEXT + '\n' + q['id']).encode()
        shared = self.browser.exchange(X25519PublicKey.from_public_bytes(wire.unb64u(reply['epk'])))
        return json.loads(AESGCM(hashlib.sha256(aad + shared).digest()).decrypt(wire.unb64u(reply['iv']), wire.unb64u(reply['data']), aad))

    async def run_op(self, **kw):
        self.q.update(id=wire.b64u(os.urandom(16)), **kw)
        q = dict(self.q)
        await self.h.remote_pair.execute(self.signed(q))
        return self.opened(q)

    async def test_retry_takes_over_an_unscanned_account_qr(self):
        first = await self.run_op(op='start')
        self.assertEqual(first['status'], 'link'); self.assertTrue(first['cancel'])
        old = self.h.pairing
        second = await self.run_op(op='start')
        self.assertEqual(second['status'], 'link')
        self.assertIsNot(self.h.pairing, old); self.assertEqual(old.done.result(), {'ev': 'replaced'})
        self.assertNotEqual(first['link'], second['link'])

    async def test_terminal_pair_is_not_replaced_and_busy_says_who_and_until(self):
        p = serve.Pairing(os.urandom(16), os.urandom(32), time.time() + 200, time.monotonic() + 200, None); self.h.pairing = p
        busy = await self.run_op(op='start')
        self.assertEqual(busy['status'], 'busy'); self.assertEqual(busy['by'], 'local')
        self.assertEqual(busy['until'], int(p.expires)); self.assertTrue(busy['cancel']); self.assertFalse(busy['phone'])
        self.assertIs(self.h.pairing, p)

    async def test_phone_mid_pairing_is_never_replaced_or_cancelled(self):
        await self.run_op(op='start'); p, s = await self.pending()
        busy = await self.run_op(op='start')
        self.assertEqual((busy['status'], busy['by'], busy['phone'], busy['cancel']), ('busy', 'remote', True, False))
        self.assertEqual((await self.run_op(op='cancel'))['status'], 'busy')
        self.assertIs(self.h.pairing, p)

    async def test_cancel_releases_account_qr_and_terminal_pair(self):
        await self.run_op(op='start')
        self.assertEqual((await self.run_op(op='cancel'))['status'], 'cancelled'); self.assertIsNone(self.h.pairing)
        p = serve.Pairing(os.urandom(16), os.urandom(32), time.time() + 200, time.monotonic() + 200, None); self.h.pairing = p
        self.assertEqual((await self.run_op(op='cancel'))['status'], 'cancelled'); self.assertIsNone(self.h.pairing)
        self.assertEqual(p.done.result(), {'ev': 'denied', 'reason': 'account_cancelled'})
        self.assertEqual((await self.run_op(op='cancel'))['status'], 'cancelled')   # nothing held: still a clear answer
        self.assertEqual((await self.run_op(op='start'))['status'], 'link')

    async def test_cancel_is_owner_gated_like_every_op(self):
        rp.set_enabled(self.st, False); await self.h.remote_pair.execute(self.signed({**self.q, 'op': 'start'}))
        self.assertIsNone(self.h.pairing)
        self.assertIsNone(self.open({**self.q, 'op': 'cancel', 'target': {'x': 1}}))
        self.assertIsNotNone(self.open({**self.q, 'op': 'cancel'}))

    async def test_failed_qr_build_never_holds_the_slot(self):
        with patch('agentj.wire.pairing_qr', side_effect=ValueError('qr')):
            with self.assertRaises(ValueError):
                await self.h.remote_pair.execute(self.signed({**self.q, 'op': 'start'}))
        self.assertIsNone(self.h.pairing)


if __name__ == '__main__':
    unittest.main()
