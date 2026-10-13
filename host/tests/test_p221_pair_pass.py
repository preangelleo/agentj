"""P221 (ADR-P221): the account page may authorize an Add-a-remote with the owner's remote pairing password instead of a fresh
passkey. Only this host opens and checks it: pass_hello hands out a one-use X25519 key (memory only), pass_start carries the
password sealed to it; scrypt + the persisted failure ledger / lock (gate.py) decide. Wrong → tries left (+ a fresh key),
5 wrong → locked, none set → said, a reused / expired / tampered blob → refused, and nothing readable ever leaves the host."""
import _hermetic
import hashlib
import json
import os
import time
import unittest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from agentj import gate, remote_pair as rp, wire
from test_p78_remote_pair import HostFlow, raw

PASSWORD = 'correct horse 221'


class P221Pass(HostFlow):
    def opened(self, q):
        reply = self.h.remote_pair.results[q['id']][1]
        aad = (rp.CONTEXT + '\n' + q['id']).encode()
        shared = self.browser.exchange(X25519PublicKey.from_public_bytes(wire.unb64u(reply['epk'])))
        return json.loads(AESGCM(hashlib.sha256(aad + shared).digest()).decrypt(wire.unb64u(reply['iv']), wire.unb64u(reply['data']), aad))

    async def send(self, **kw):
        self.q.update(id=wire.b64u(os.urandom(16)), **kw)
        q = dict(self.q)
        await self.h.remote_pair.execute(self.signed(q))
        return self.opened(q)

    async def hello(self):
        return await self.send(op='pass_hello', target=None)

    @staticmethod
    def seal(hello, password, host):
        """What the account page does: seal {"pass"} to the host's one-use key; AAD = context, nonce, host."""
        e = X25519PrivateKey.generate()
        shared = e.exchange(X25519PublicKey.from_public_bytes(wire.unb64u(hello['hpk'])))
        aad = rp.pass_aad(hello['nonce'], host)
        iv = os.urandom(12)
        data = AESGCM(hashlib.sha256(aad + shared).digest()).encrypt(iv, json.dumps({'pass': password}).encode(), aad)
        return {'nonce': hello['nonce'], 'epk': wire.b64u(raw(e.public_key())), 'iv': wire.b64u(iv), 'data': wire.b64u(data)}

    async def start(self, hello, password):
        return await self.send(op='pass_start', target=self.seal(hello, password, self.q['host']))

    async def test_not_set_is_said_before_any_try(self):
        h = await self.hello()
        self.assertEqual(h, {'status': 'pass_hello', 'set': False})
        self.assertIsNone(self.h.pairing)

    async def test_right_password_makes_the_qr_and_the_scan_is_granted_as_account_pass(self):
        gate.set_passphrase(self.st, PASSWORD)
        h = await self.hello()
        self.assertTrue(h['set']); self.assertEqual((h['left'], h['locked']), (gate.FREE_TRIES, 0))
        r = await self.start(h, PASSWORD)
        self.assertEqual(r['status'], 'link'); self.assertIsNotNone(self.h.pairing)
        p, s = await self.pending()
        await self.h.remote_pair.pending(p, s)
        self.assertEqual(s.state, 'ready'); self.assertEqual(self.st.devices()[s.device]['source'], 'account')
        self.assertIn('account_pass"', self.st.approvals_path.read_text())
        self.assertNotIn(PASSWORD, self.st.log_path.read_text() if self.st.log_path.exists() else '')
        self.assertNotIn(PASSWORD, json.dumps(self.h.remote_pair.results, default=str))

    async def test_wrong_password_counts_down_then_locks_and_never_makes_a_qr(self):
        gate.set_passphrase(self.st, PASSWORD)
        h = await self.hello()
        for left in range(gate.FREE_TRIES - 1, 0, -1):
            h = await self.start(h, 'wrong password')
            self.assertEqual((h['status'], h['left']), ('pass_wrong', left))
            self.assertTrue(h['hpk'] and h['nonce'], 'the next try comes with a fresh key')
        r = await self.start(h, 'wrong password')
        self.assertEqual(r['status'], 'pass_locked'); self.assertGreater(r['seconds'], 0)
        h = await self.hello(); self.assertGreater(h['locked'], 0)
        r = await self.start(h, PASSWORD)                    # right password, but locked: the lock is the host's
        self.assertEqual(r['status'], 'pass_locked')
        self.assertIsNone(self.h.pairing); self.assertEqual(self.st.devices(), {})

    async def test_key_is_one_use_expires_and_is_bound_to_this_host(self):
        gate.set_passphrase(self.st, PASSWORD)
        h = await self.hello()
        blob = self.seal(h, PASSWORD, self.q['host'])
        self.assertEqual((await self.send(op='pass_start', target=blob))['status'], 'link')
        self.h._pair_finish(self.h.pairing, {'ev': 'replaced'})
        self.assertEqual((await self.send(op='pass_start', target=blob))['status'], 'pass_expired', 'replayed blob')
        h = await self.hello()
        self.assertEqual((await self.send(op='pass_start', target=self.seal(h, PASSWORD, 'another-host')))['status'], 'pass_expired')
        h = await self.hello()
        self.h.remote_pair.pass_keys[h['nonce']] = (self.h.remote_pair.pass_keys[h['nonce']][0], self.h.remote_pair.now_ms() - 1)
        self.assertEqual((await self.start(h, PASSWORD))['status'], 'pass_expired')
        self.assertIsNone(self.h.pairing)
        self.assertEqual(gate.tries_left(self.st), gate.FREE_TRIES, 'an unreadable blob is not a guess')

    async def test_outstanding_keys_are_bounded(self):
        gate.set_passphrase(self.st, PASSWORD)
        for _ in range(rp.PASS_KEYS * 3):
            await self.hello()
        self.assertLessEqual(len(self.h.remote_pair.pass_keys), rp.PASS_KEYS)

    async def test_switch_off_or_offline_never_reaches_the_password(self):
        gate.set_passphrase(self.st, PASSWORD)
        h = await self.hello()
        rp.set_enabled(self.st, False)
        self.assertEqual((await self.start(h, PASSWORD))['status'], 'disabled')
        self.assertEqual(gate.tries_left(self.st), gate.FREE_TRIES)

    def test_strict_shape_of_pass_start(self):
        good = {'nonce': wire.b64u(os.urandom(16)), 'epk': wire.b64u(os.urandom(32)), 'iv': wire.b64u(os.urandom(12)), 'data': wire.b64u(os.urandom(40))}
        q = {**self.q, 'op': 'pass_start', 'target': good}
        self.assertEqual(self.open(q), q)
        for k, v in [('nonce', 'short'), ('epk', wire.b64u(os.urandom(31))), ('iv', wire.b64u(os.urandom(8))),
                     ('data', wire.b64u(os.urandom(8))), ('data', wire.b64u(os.urandom(1200))), ('extra', 'x')]:
            with self.subTest(k=k):
                self.assertIsNone(self.open({**q, 'target': {**good, k: v}}))
        self.assertIsNone(self.open({**self.q, 'op': 'pass_hello', 'target': good}))
        self.assertIsNone(self.open({**self.q, 'op': 'pass_start', 'target': None}))


if __name__ == '__main__':
    unittest.main()
