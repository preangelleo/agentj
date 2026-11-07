"""Offline real-device signatures for legacy single action and exact v2 scopes."""
import unittest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from agentj import approvals, approval_review

class ProtocolBoundaries(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.pub = self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.args = ('channel-synthetic', 'device-synthetic', '1'*32, 'allow', 'a'*64)

    def test_legacy_single_action_preserves_preimage(self):
        message = approvals.signed_message(*self.args)
        self.assertTrue(approvals.verify(self.pub, self.key.sign(message), *self.args))
        self.assertFalse(approvals.verify(self.pub, self.key.sign(message), *self.args, approval_v=2))

    def test_v2_scope_cannot_fallback_or_widen(self):
        scope = '{"actions":[{"object":"synthetic","count":1}],"seconds":60}'
        message = approval_review.decision_message(*self.args, task_scope=scope)
        sig = self.key.sign(message)
        self.assertTrue(approvals.verify(self.pub, sig, *self.args, task_scope=scope, approval_v=2))
        self.assertFalse(approvals.verify(self.pub, sig, *self.args, task_scope=scope))
        self.assertFalse(approvals.verify(self.pub, sig, *self.args, task_scope=scope.replace('60','600'), approval_v=2))

    def test_legacy_cannot_sign_task_scope(self):
        sig = self.key.sign(approvals.signed_message(*self.args))
        self.assertFalse(approvals.verify(self.pub, sig, *self.args, task_scope='exact-task', approval_v=2))

class DisplayDigestBoundary(unittest.IsolatedAsyncioTestCase):
    async def test_rebinding_rotates_v2_but_keeps_released_v1_digest(self):
        import time
        from types import SimpleNamespace
        host = SimpleNamespace(channel='synthetic',hist=SimpleNamespace(epoch=1,turns={7:{}}),agent=SimpleNamespace(kind='claude',cfg={}),_task_id=lambda:'task')
        ask = SimpleNamespace(rid='1'*32,tool='Bash',tool_input={'command':'echo synthetic'},input_sha='b'*64,
                              digest='a'*64,summary='echo synthetic',task=None,task_scope=None,reason='Show synthetic output',
                              goal_what='',allow_offer=[],cats=[],deadline=time.monotonic()+60)
        origin = {'session':'1','message_id':7,'root_message_id':7,'trigger_message_id':7,
                  'channel':'phone','text':'Synthetic owner request','verified':True,'suspicious':False,'chain':[]}
        approval_review.bind(host,ask,origin)
        digest, nonce = ask.envelope_digest, ask.envelope['nonce']
        approval_review.bind(host,ask,origin)
        self.assertEqual(ask.digest,'a'*64)
        self.assertEqual(ask.legacy_digest,'a'*64)
        self.assertNotEqual(ask.envelope_digest,digest)
        self.assertNotEqual(ask.envelope['nonce'],nonce)
        self.assertFalse(ask.review_v2_only)
