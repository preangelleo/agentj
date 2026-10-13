"""P221: an account-page pairing never waits in silence because this computer's clock is off. The host judges a request by
the account service's clock (learned from every poll, bounded to ±5 min); beyond that it answers 「clock」 instead of
dropping the request, so the account page can say what is wrong."""
import _hermetic
import hashlib
import json
import os
import unittest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from agentj import remote_pair as rp, wire
from test_p78_remote_pair import HostFlow


class P221Clock(HostFlow):
    def opened(self, q):
        reply = self.h.remote_pair.results[q['id']][1]
        aad = (rp.CONTEXT + '\n' + q['id']).encode()
        shared = self.browser.exchange(X25519PublicKey.from_public_bytes(wire.unb64u(reply['epk'])))
        return json.loads(AESGCM(hashlib.sha256(aad + shared).digest()).decrypt(wire.unb64u(reply['iv']), wire.unb64u(reply['data']), aad))

    async def start_at(self, skew_ms):
        """A start the account service created `skew_ms` after this computer's clock (positive = this computer is behind)."""
        rpair = self.h.remote_pair
        t = rpair.started_at + 1000 + skew_ms
        self.q.update(id=wire.b64u(os.urandom(16)), op='start', created_at=t, expires_at=t + 300000)
        q = dict(self.q)
        await rpair.execute(self.signed(q))
        return q

    async def test_clock_behind_without_learning_was_silent_now_answers(self):
        # before P221 the request was dropped with no reply: the account page said 「正在联系这台电脑…」 for five minutes
        q = await self.start_at(30000)
        self.assertIsNone(self.h.pairing)
        self.assertEqual(self.opened(q)['status'], 'clock')

    async def test_clock_behind_or_ahead_pairs_once_the_service_time_is_known(self):
        for skew in (30000, -40000, 290000, -290000):
            with self.subTest(skew=skew):
                if self.h.pairing:
                    self.h._pair_finish(self.h.pairing, {'ev': 'replaced'})
                rpair = self.h.remote_pair
                local = rpair.started_at + 1000
                rpair.learn_clock(local + skew, local - 50, local + 50)
                q = await self.start_at(skew)
                self.assertEqual(self.opened(q)['status'], 'link')
                self.assertIsNotNone(self.h.pairing)
                self.assertGreater(self.h.pairing.expires - __import__('time').time(), 250)   # a full five minutes to scan

    async def test_offset_is_bounded_and_needs_an_integer(self):
        rpair = self.h.remote_pair
        rpair.learn_clock(10 ** 13, 0, 10); self.assertEqual(rpair.offset, rp.MAX_SKEW)
        rpair.learn_clock(-10 ** 13, 0, 10); self.assertEqual(rpair.offset, -rp.MAX_SKEW)
        rpair.learn_clock('1', 0, 10); self.assertEqual(rpair.offset, -rp.MAX_SKEW)
        rpair.learn_clock(1.5, 0, 10); self.assertEqual(rpair.offset, -rp.MAX_SKEW)

    async def test_clock_answer_is_once_and_grants_nothing(self):
        q = await self.start_at(600000)
        self.assertEqual(self.opened(q)['status'], 'clock')
        self.h.remote_pair.results.clear()
        await self.h.remote_pair.execute(self.signed(q))           # delivered again: nonce already used
        self.assertEqual(self.h.remote_pair.results, {}); self.assertIsNone(self.h.pairing); self.assertEqual(self.st.devices(), {})

    async def test_forged_or_foreign_requests_still_get_no_answer(self):
        self.q.update(account='another', created_at=self.h.remote_pair.started_at + 600000)
        await self.h.remote_pair.execute(self.signed())
        self.assertEqual(self.h.remote_pair.results, {})
        self.assertIsNone(rp.open_request(self.signed(), self.st, self.now))


if __name__ == '__main__':
    unittest.main()
