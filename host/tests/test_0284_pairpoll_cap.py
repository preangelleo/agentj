"""A real signed poll must remain closed-schema compatible; no cloud calls."""
import _hermetic
import unittest
import json
from types import SimpleNamespace as NS
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from agentj import remote_pair,cloud,wire

class PollCompatibility(unittest.TestCase):
    def setUp(self):
        self.host=NS(st=NS(config=lambda:{}),channel='synthetic-channel',relay_up=True,sk=Ed25519PrivateKey.generate())
        self.pair=remote_pair.RemotePair(self.host);self.payloads=[]
    def old_server(self,url,envelope):
        self.host.sk.public_key().verify(wire.unb64u(envelope['sig']),f"{remote_pair.POLL_CONTEXT}\n{envelope['body']}".encode('ascii'))
        body=json.loads(wire.unb64u(envelope['body']));self.payloads.append(body)
        allowed={'v','t','channel','ts','seq','enabled','online','results'}
        return (200,{'ok':True,'now':1,'requests':[]})if set(body)<=allowed else(400,{'error':'bad_request'})
    def test_no_server_advertisement_omits_optional_field_and_old_strict_schema_accepts(self):
        with patch.object(cloud,'api_url',return_value='https://fixture.invalid'),patch.object(cloud,'post_json',side_effect=self.old_server):
            self.assertEqual(self.pair.poll({},[])[0],200)
        self.assertNotIn('unbind_all',self.payloads[-1])
    def test_recreated_state_and_unrelated_account_metadata_never_imply_capability(self):
        for link in ({'unbind_all':True},{'supported':True},{'capabilities':['unknown']},{'capabilities':{'unknown':True}}):
            self.pair=remote_pair.RemotePair(self.host)
            with patch.object(cloud,'api_url',return_value='https://fixture.invalid'),patch.object(cloud,'post_json',side_effect=self.old_server):
                self.assertEqual(self.pair.poll(link,[])[0],200)
            self.assertNotIn('unbind_all',self.payloads[-1])
