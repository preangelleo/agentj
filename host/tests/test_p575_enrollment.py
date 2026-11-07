"""Offline real Host + synthetic paired sessions; real sealed crypto and UV signatures.
Run with TMPDIR/HOME/XDG paths confined to the worker directory. No phone or network.
"""
import _hermetic  # noqa: F401
import asyncio
import copy
import json
import unittest
from unittest import mock
import test_p547_peer as harness
from agentj import gate, pkpeer, wire
from agentj.elevate import seal, seal_aad


class Enrollment(unittest.TestCase):
    setUp = harness.Peer.setUp
    tearDown = harness.Peer.tearDown
    run_ = harness.Peer.run_
    to = harness.Peer.to
    device = harness.Peer.device
    connect = harness.Peer.connect
    req = harness.Peer.req
    fa = staticmethod(harness.Peer.fa)
    answer = harness.Peer.answer

    def setup_pair(self, target_key=None, phone_key=None):
        _, self.did, self.mac = self.device('Mac', target_key)
        _, self.pid, self.phone = self.device('phone', phone_key)
        self.host.push_notify = mock.Mock()  # capture notification transport, never UV/proof
        self.password = 'synthetic-p575-pair-password'
        gate.set_passphrase(self.st, self.password)
        self.res = self.req(self.mac)
        self.ask = self.to(self.phone, 'pk_peer_ask')[-1]
        return self.host.pkpeer.reqs[self.res['id']]

    def package(self, ask=None, password=None, device=None):
        a = ask or self.ask
        epk, ct = seal(wire.unb64u(a['epk']), (password or self.password).encode(),
                       seal_aad(self.host.channel, device or self.pid, a['id'], 'pk_bootstrap', a['n'], a['digest']))
        return dict(t='pk_peer_answer', id=a['id'], ok=True, bootstrap=True, epk=wire.b64u(epk), ct=wire.b64u(ct))

    def submit(self, obj=None, session=None):
        s = session or self.phone
        self.run_(self.host._app(s, obj or self.package()))
        return self.to(s, 'pk_peer_answer_res')[-1]

    def test_online_no_pk_receives_bootstrap_and_counts(self):
        r = self.setup_pair()
        self.assertEqual(self.res['online'], 1)
        self.assertTrue(self.ask['needs_setup'])
        self.assertTrue(self.ask['bootstrap'])
        self.assertEqual(self.to(self.mac, 'pk_offer'), [])
        self.assertTrue(self.submit()['ok'])
        self.assertEqual(r.exp, self.res['exp'])
        offer = self.to(self.mac, 'pk_offer')[-1]
        auth = harness.p57.Authenticator()
        self.run_(self.host._app(self.mac, auth.register(wire.unb64u(offer['n']))))
        self.assertTrue(self.to(self.mac, 'pk_reg_res')[-1]['ok'])
        self.assertFalse(self.host.pkpeer.approved(self.did, r.id))

    def test_background_no_pk_is_not_filtered_by_p33(self):
        r = self.setup_pair()
        self.phone.p33 = False
        self.sent.clear()
        self.run_(self.host.pkpeer.on_ready(self.phone))
        self.assertEqual(len(self.to(self.phone, 'pk_peer_ask')), 1)
        self.assertTrue(self.host.pkpeer._recipient(self.phone, r))

    def test_existing_target_pk_forbids_password_downgrade(self):
        self.setup_pair(target_key=harness.p57.Authenticator())
        self.assertTrue(self.ask['needs_setup'])
        self.assertNotIn('bootstrap', self.ask)
        self.assertFalse(self.submit(dict(t='pk_peer_answer', id=self.ask['id'], ok=True, bootstrap=True))['ok'])

    def test_existing_other_uv_root_forbids_fallback_even_offline(self):
        self.device('offline UV root', harness.p57.Authenticator(), online=False)
        self.setup_pair()
        self.assertNotIn('bootstrap', self.ask)

    def test_existing_phone_uv_has_priority(self):
        auth = harness.p57.Authenticator()
        r = self.setup_pair(phone_key=auth)
        self.assertNotIn('bootstrap', self.ask)
        self.assertTrue(self.answer(self.phone, auth, self.ask)['ok'])
        self.assertTrue(self.host.pkpeer.approved(self.did, r.id))

    def test_wrong_password_and_persisted_lock(self):
        self.setup_pair()
        for i in range(gate.FREE_TRIES):
            obj = self.package(password='synthetic-wrong-password')
            result = self.submit(obj)
            self.assertEqual(result['why'], 'locked' if i == gate.FREE_TRIES - 1 else 'wrong')
            self.ask = self.to(self.phone, 'pk_peer_ask')[-1]
        self.assertGreater(gate.lock_left(self.st), 0)
        self.assertEqual(self.submit()['why'], 'locked')
        self.assertEqual(self.to(self.mac, 'pk_offer'), [])

    def test_seal_wrong_device(self):
        self.setup_pair()
        self.assertEqual(self.submit(self.package(device=self.did))['why'], 'bad_seal')

    def test_ciphertext_replay(self):
        self.setup_pair()
        obj = self.package()
        self.assertTrue(self.submit(obj)['ok'])
        self.assertFalse(self.submit(obj)['ok'])

    def test_cross_rid(self):
        self.setup_pair()
        obj = self.package()
        obj['id'] = wire.b64u(b'X' * 16)
        self.assertFalse(self.submit(obj)['ok'])
        self.assertEqual(self.to(self.mac, 'pk_offer'), [])

    def test_wrong_phone_cannot_use_ciphertext(self):
        self.setup_pair()
        _, _, other = self.device('other phone')
        self.assertFalse(self.submit(self.package(), other)['ok'])

    def test_expired(self):
        r = self.setup_pair()
        self.host.pkpeer.clock = lambda: r.exp + 1
        self.assertFalse(self.submit()['ok'])

    def test_cancel(self):
        self.setup_pair()
        obj = self.package()
        self.run_(self.host._app(self.mac, dict(t='pk_peer_cancel', id=self.ask['id'])))
        self.assertFalse(self.submit(obj)['ok'])

    def test_estop(self):
        self.setup_pair()
        self.host.estop['on'] = True
        self.assertFalse(self.submit()['ok'])

    def test_session_epoch_changes(self):
        self.setup_pair()
        self.host.hist.epoch += 1
        self.assertFalse(self.submit()['ok'])

    def test_replaced_target_session(self):
        self.setup_pair()
        self.host.sessions[self.mac.cid] = copy.copy(self.mac)
        self.assertFalse(self.submit()['ok'])

    def test_approver_reconnect_cannot_replay_old_seal(self):
        self.setup_pair()
        obj = self.package()
        replacement = copy.copy(self.phone)
        self.host.sessions[self.phone.cid] = replacement
        self.assertFalse(self.submit(obj, replacement)['ok'])

    def test_target_unpair(self):
        self.setup_pair()
        self.st.remove_device(self.did)
        self.assertFalse(self.submit()['ok'])

    def test_deadline_mutation(self):
        r = self.setup_pair()
        obj = self.package()
        r.exp += 100
        self.assertFalse(self.submit(obj)['ok'])

    def test_mutation_during_password_verification(self):
        self.setup_pair()
        real = gate.verify
        def verify(st, password):
            result = real(st, password)  # actual password verification, never forced True
            self.host.sessions.pop(self.phone.cid)
            return result
        with mock.patch.object(gate, 'verify', verify):
            self.run_(self.host._app(self.phone, self.package()))
        self.assertEqual(self.to(self.mac, 'pk_offer'), [])

    def test_second_root_added_during_verification(self):
        self.setup_pair()
        real = gate.verify
        def verify(st, password):
            result = real(st, password)
            self.st.set_passkey(self.mac.pub, harness.p57.pk_record(harness.p57.Authenticator()))
            return result
        with mock.patch.object(gate, 'verify', verify):
            self.assertFalse(self.submit()['ok'])

    def test_post_approval_session_change_invalidates_offer(self):
        r = self.setup_pair()
        self.assertTrue(self.submit()['ok'])
        self.host.sessions[self.phone.cid] = copy.copy(self.phone)
        self.assertFalse(self.host.pkpeer.approved(self.did, r.id))

    def test_no_plaintext_in_emit_or_other_device(self):
        self.setup_pair()
        _, _, other = self.device('observer')
        self.assertTrue(self.submit()['ok'])
        for cid, message in self.sent:
            self.assertNotIn(self.password, json.dumps(message))
            if cid == other.cid:
                self.assertNotIn('ct', message)
                self.assertNotIn('epk', message)
        for path in (self.st.log_path, self.st.approvals_path):
            self.assertNotIn(self.password, path.read_text())

    def test_offline_no_pk_keeps_direct_bootstrap(self):
        _, _, mac = self.device('Mac')
        self.device('offline phone', online=False)
        self.assertEqual(self.req(mac)['mode'], 'direct')


if __name__ == '__main__':
    unittest.main()
