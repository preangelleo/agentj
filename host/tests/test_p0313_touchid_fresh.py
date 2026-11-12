"""Click-time biometric self-check challenges remain bound to the paired device."""
import _hermetic  # noqa: F401
import unittest

import test_p555_faceid as faceid_fixture
from agentj import wire
import test_p57_passkey as p57


class ClickChallenge(unittest.TestCase):
    setUp = faceid_fixture.FaceIdRecognize.setUp
    tearDown = faceid_fixture.FaceIdRecognize.tearDown
    run_ = faceid_fixture.FaceIdRecognize.run_
    to = faceid_fixture.FaceIdRecognize.to
    device = faceid_fixture.FaceIdRecognize.device
    fa = staticmethod(faceid_fixture.FaceIdRecognize.fa)
    state_msg = faceid_fixture.FaceIdRecognize.state_msg

    def test_click_issues_fresh_and_old_challenge_is_still_rejected(self):
        a = p57.Authenticator()
        _, _, s = self.device('Mac Chrome', a)
        old = self.state_msg(s)
        self.host.pkpeer.clock = lambda: 9999999999
        self.run_(self.host._app(s, {'t': 'pk_check_ch', 'r': 'fresh'}))
        fresh = self.to(s, 'pk_check_ch_res')[-1]
        self.assertEqual(fresh['r'], 'fresh')
        self.assertEqual(fresh['fa'], old['fa'])
        self.assertNotEqual(fresh['n'], old['n'])
        self.run_(self.host._app(s, {'t': 'pk_check', 'n': old['n'], 'fa': self.fa(a, wire.unb64u(old['fa_ch']))}))
        self.assertEqual(self.to(s, 'pk_check_res')[-1]['why'], 'no_check')
        self.run_(self.host._app(s, {'t': 'pk_check', 'n': fresh['n'], 'fa': self.fa(a, wire.unb64u(fresh['fa_ch']))}))
        self.assertTrue(self.to(s, 'pk_check_res')[-1]['ok'])

    def test_unpaired_or_missing_key_cannot_get_challenge(self):
        _, _, s = self.device('unkeyed')
        self.run_(self.host._app(s, {'t': 'pk_check_ch', 'r': 'none'}))
        self.assertEqual(self.to(s, 'pk_check_ch_res')[-1], {'t': 'pk_check_ch_res', 'r': 'none'})
        s.state = 'closed'
        self.run_(self.host._app(s, {'t': 'pk_check_ch', 'r': 'closed'}))
        self.assertEqual(len(self.to(s, 'pk_check_ch_res')), 1, 'closed session gets no challenge')
