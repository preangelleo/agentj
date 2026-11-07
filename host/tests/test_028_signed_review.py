"""Existing Host control signature verification, in-memory synthetic paired key."""
import hashlib
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from agentj import controls, approval_level, wire
from test_028_tail_runtime import candidate
from agentj.privacy_cheap import review

class SignedReview(unittest.TestCase):
    def setUp(self):
        self.key=Ed25519PrivateKey.generate()
        self.pub=self.key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
        self.target={'key':'jev.mode','value':review(candidate())}
        self.channel='fixture-channel';self.device='fixture-phone';self.n='a'*32
        self.ts=int(time.time()*1000)
        message=controls.signed_message(self.channel,self.device,'approval_set',self.n,self.ts,
                                        controls.object_digest('approval_set',self.target))
        self.obj={'n':self.n,'ts':self.ts,'sig':wire.b64u(self.key.sign(message))}
        self.st=SimpleNamespace(sign_key=lambda _:self.pub, config=lambda:{'channel':self.channel})
    def test_signed_digest_uses_existing_control_and_nonce_once(self):
        nonces=controls.Nonces()
        self.assertIsNone(controls.check(self.st,nonces,self.channel,self.device,self.obj,'approval_set',self.target))
        self.assertEqual(controls.check(self.st,nonces,self.channel,self.device,self.obj,'approval_set',self.target),'replay')
    def test_other_channel_device_or_target_rejected(self):
        for channel,device,target in [('other',self.device,self.target),
          (self.channel,'other',self.target),(self.channel,self.device,{'key':'jev.mode','value':'off'})]:
            self.assertEqual(controls.check(self.st,controls.Nonces(),channel,device,self.obj,'approval_set',target),'bad_signature')
    def test_live_record_rechecks_current_paired_key(self):
        record={'value':self.target['value'],'device':self.device,'rid':'fixture-rid',
                'sk_sha':hashlib.sha256(self.pub).hexdigest(),'at':1,**self.obj}
        with patch.object(approval_level,'signers',return_value={'jev.mode':record}):
            self.assertIsNotNone(approval_level._live(self.st,'jev.mode'))
            self.st.sign_key=lambda _:None
            self.assertIsNone(approval_level._live(self.st,'jev.mode'))
    def test_changed_config_no_longer_matches_signed_digest(self):
        config=candidate();config['threshold']=.4;config['evaluation']['threshold']=.4
        target={'key':'jev.mode','value':review(config)}
        self.assertEqual(controls.check(self.st,controls.Nonces(),self.channel,self.device,self.obj,'approval_set',target),'bad_signature')

if __name__=='__main__':unittest.main()
