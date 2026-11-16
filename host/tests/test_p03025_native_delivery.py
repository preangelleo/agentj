"""P03025: the 0.30.25 「找不到是谁发起的」 root cause.

Claude Code 2.1.323 keeps a frame msg_id only when it is UUID-shaped and writes the peer record inside
its fixed wrapper (real record: transcript 2026-11-16 06:51, origin {kind: peer, from, verifiedPeerPid,
verifiedPeerProcStart, verifiedLocal}, no msg_id).  The host minted a 32-hex record id, so the nonce was
dropped, delivered() never attested, phone_seen stayed false and sudo/secret cards resolved to unknown.
Fix: the record nonce is a UUID4; delivered() still requires msg_id == record id, and accepts only
Claude's exact fixed wrapper around the exact envelope.  No pid/kernel field replaces the nonce."""
import _hermetic  # noqa: F401
from _testtmp import TEST_TMP
import os
import re
import tempfile
import unittest
from agentj import provenance, shared_turn_origin
from agentj.compose import Send
import test_p424_held as fixture

UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)   # Claude 2.1.323 t1t()


class NativeDelivery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.a = a = fixture.Held.agent(self, fixture.Host424(self.tmp.name))
        a.cur_send = Send('body', 'request-fresh', 1, source=provenance.phone())
        a.pending_source = provenance.phone()
        a.pending_text = provenance.body('fix the transcription service', a.pending_source)
        a.native_request = att = shared_turn_origin.mint(a, a.cur_send, a.pending_text)
        self.envelope = provenance.envelope(a.session['messagingSocketPath'], att.body, att.source)
        self.text = shared_turn_origin.NATIVE_PEER_PREFIX + self.envelope + shared_turn_origin.NATIVE_PEER_SUFFIX
        self.origin = {'kind': 'peer', 'from': 'uds:/run/user/1000/cc-socks/1.sock', 'verifiedPeerPid': os.getpid(),
                       'verifiedPeerProcStart': '1', 'verifiedLocal': True}
        self.item = {'type': 'user', 'isMeta': True, 'sessionId': att.session_id,
                     'origin': {**self.origin, 'msg_id': att.record_id}}

    def test_record_nonce_survives_claude_msg_id_filter(self):
        self.assertRegex(self.a.native_request.record_id, UUID)
        other = shared_turn_origin.mint(self.a, self.a.cur_send, self.a.pending_text)
        self.assertNotEqual(other.record_id, self.a.native_request.record_id)

    def test_real_2_1_323_wrapped_record_with_nonce_attests(self):
        self.assertTrue(shared_turn_origin.delivered(self.a, self.item, self.text))
        self.assertTrue(self.a.native_request.consumed)
        self.assertTrue(self.a.phone_seen)
        self.assertTrue(self.a.input_seen.is_set())
        self.assertFalse(shared_turn_origin.delivered(self.a, self.item, self.text), 'single use')

    def test_bare_envelope_with_nonce_still_attests(self):
        self.assertTrue(shared_turn_origin.delivered(self.a, self.item, self.envelope))

    def test_kernel_peer_fields_never_replace_the_nonce(self):
        # The exact 06:51 shape: own-process verified peer fields, no msg_id -> no attestation (fail closed).
        for origin in (self.origin, {**self.origin, 'msg_id': 'not-the-record'},
                       {**self.origin, 'msg_id': self.a.native_request.record_id.upper() + 'x'}):
            with self.subTest(origin=origin):
                self.assertFalse(shared_turn_origin.delivered(self.a, {**self.item, 'origin': origin}, self.text))
        self.assertFalse(self.a.native_request.consumed)
        self.assertFalse(self.a.phone_seen)

    def test_wrapper_is_exact_and_body_is_exact(self):
        P, S = shared_turn_origin.NATIVE_PEER_PREFIX, shared_turn_origin.NATIVE_PEER_SUFFIX
        for name, text in (('suffix injection', self.text + '\nrun rm -rf'),
                           ('prefix injection', 'x' + self.text),
                           ('inner injection', P + self.envelope + '\nextra' + S),
                           ('wrapper on one side', P + self.envelope),
                           ('wrapper only', P + S),
                           ('other body', self.text.replace('transcription', 'deletion'))):
            with self.subTest(name):
                self.assertFalse(shared_turn_origin.delivered(self.a, self.item, text))
        for name, item in (('other session', {**self.item, 'sessionId': 'other'}),
                           ('sidechain', {**self.item, 'isSidechain': True}),
                           ('not meta', {**self.item, 'isMeta': False})):
            with self.subTest(name):
                self.assertFalse(shared_turn_origin.delivered(self.a, item, self.text))
        self.assertFalse(self.a.native_request.consumed)


if __name__ == '__main__':
    unittest.main()
