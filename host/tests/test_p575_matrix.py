"""Additional real-Host synthetic matrix; same-key case requires owner's fcaf1ac6ac.
No production credentials, real accounts, messages, sockets or proof bypasses.
"""
import _hermetic  # noqa: F401
import copy
import unittest
from unittest import mock
import test_p575_enrollment as base
from agentj import gate


class Matrix(unittest.TestCase):
    setUp = base.Enrollment.setUp
    tearDown = base.Enrollment.tearDown
    run_ = base.Enrollment.run_
    to = base.Enrollment.to
    device = base.Enrollment.device
    connect = base.Enrollment.connect
    req = base.Enrollment.req
    setup_pair = base.Enrollment.setup_pair
    package = base.Enrollment.package
    submit = base.Enrollment.submit

    def test_bg_and_fg_both_receive(self):
        self.setup_pair()
        for fg in (False, True):
            with self.subTest(fg=fg):
                self.phone.fg = fg
                self.sent.clear()
                self.run_(self.host.pkpeer.on_ready(self.phone))
                self.assertEqual(len(self.to(self.phone,'pk_peer_ask')), 1)

    def test_repair_same_complete_key_keeps_uv_root_owner_dependency(self):
        auth = base.harness.p57.Authenticator()
        self.setup_pair(phone_key=auth)
        pk = self.st.passkey_of_device(self.pid)
        self.st.pair_device(self.phone.pub, self.phone.name, self.st.sign_key(self.pid))
        self.assertEqual(self.st.passkey_of_device(self.pid), pk, 'requires owner fcaf1ac6ac; do not redo State.add_device here')
        self.assertNotIn('bootstrap', self.host.pkpeer.ask_msg(self.phone, self.host.pkpeer.reqs[self.ask['id']]))

    def test_request_parameter_changes_rejected(self):
        r = self.setup_pair()
        obj = self.package()
        changes = {'x':'another-target-key', 'device':self.pid, 'sign_pub':'other-sign-key',
                   'digest':'different-shown-content', 'exp':r.exp+60, 'code':'000000'}
        for key, value in changes.items():
            with self.subTest(field=key):
                previous = getattr(r,key)
                setattr(r,key,value)
                self.assertFalse(self.submit(obj)['ok'])
                setattr(r,key,previous)
        self.assertEqual(self.to(self.mac,'pk_offer'), [])

    def test_expire_while_password_verifier_yields(self):
        r = self.setup_pair()
        real = gate.verify
        def verify(st, password):
            result = real(st,password)
            self.host.pkpeer.clock = lambda: r.exp+1
            return result
        with mock.patch.object(gate,'verify',verify):
            self.assertFalse(self.submit()['ok'])
        self.assertEqual(self.to(self.mac,'pk_offer'), [])

    def test_approver_identity_mutation_while_password_verifier_yields(self):
        self.setup_pair()
        real = gate.verify
        def verify(st,password):
            result = real(st,password)
            self.phone.device = self.did
            return result
        with mock.patch.object(gate,'verify',verify):
            self.run_(self.host._app(self.phone,self.package()))
        self.assertEqual(self.to(self.mac,'pk_offer'), [])

    def test_new_request_after_target_reconnect_gets_fresh_rid(self):
        self.setup_pair()
        old = self.ask['id']
        replacement = copy.copy(self.mac)
        self.host.sessions[self.mac.cid] = replacement
        res = self.req(replacement)
        self.assertNotEqual(res['id'], old)
        self.assertFalse(self.host.pkpeer.approved(self.did, old))
