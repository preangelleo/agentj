import json
import time
import unittest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from agentj.bots.store import BotError
from agentj.bots.visitor import VisitorSession,issue_claim,public,client_finish
from agentj.wire import b64u

class VisitorTest(unittest.TestCase):
    def setUp(self):
        self.bot='a'*32;self.host=Ed25519PrivateKey.generate();self.issuer=Ed25519PrivateKey.generate();self.visitor=X25519PrivateKey.generate()
        self.ticket=issue_claim(self.issuer,self.bot,b64u(public(self.host)),b64u(public(self.visitor)),1,int(time.time())+299)
        self.session=VisitorSession(self.bot,1,self.host,public(self.issuer))
    def connect(self):return client_finish(self.visitor,self.session.accept(self.ticket),public(self.host))
    def test_actual_cipher_roundtrip_and_tap_contains_no_body(self):
        client=self.connect();message={'t':'say','r':'b'*32,'text':'private order number 9876'};frame=client.seal(message)
        self.assertNotIn(b'private',frame);self.assertNotIn(b'9876',frame);self.assertEqual(self.session.decode(frame),message)
        reply=self.session.cipher.seal({'t':'reply','text':'private reply'});self.assertNotIn(b'private',reply);self.assertEqual(client.open(reply)['text'],'private reply')
    def test_owner_and_config_messages_refused(self):
        for kind in ('approve','answer','ctl','bot_write','secret','hello','pair','resume','identity_set'):
            self.setUp();client=self.connect()
            with self.subTest(kind=kind),self.assertRaises(BotError):self.session.decode(client.seal({'t':kind,'r':'b'*32}))
    def test_extra_identity_bot_or_other_visitor_refused(self):
        client=self.connect()
        with self.assertRaises(BotError):self.session.decode(client.seal({'t':'say','r':'b'*32,'text':'hi','visitor':'other'}))
    def test_other_host_bot_epoch_and_issuer_refused(self):
        for name,value in [('bot','c'*32),('host',b64u(public(Ed25519PrivateKey.generate()))),('epoch',2),('expires',int(time.time())-1)]:
            self.setUp();self.ticket['claim'][name]=value
            with self.subTest(name=name),self.assertRaises(BotError):self.session.accept(self.ticket)
    def test_wrong_client_host_key_and_transcript_refused(self):
        answer=self.session.accept(self.ticket)
        with self.assertRaises(BotError):client_finish(X25519PrivateKey.generate(),answer,public(self.host))
        with self.assertRaises(BotError):client_finish(self.visitor,answer,public(Ed25519PrivateKey.generate()))
        answer['transcript']['nonce']='different'
        with self.assertRaises(BotError):client_finish(self.visitor,answer,public(self.host))
    def test_replay_cross_session_and_modified_cipher_refused(self):
        client=self.connect();frame=client.seal({'t':'human','r':'b'*32});self.session.decode(frame)
        with self.assertRaises(BotError):self.session.decode(frame)
        other=VisitorSession(self.bot,1,self.host,public(self.issuer));other.accept(self.ticket)
        with self.assertRaises(BotError):other.decode(client.seal({'t':'human','r':'c'*32}))
    def test_expired_and_oversized_refused(self):
        client=self.connect();self.session.expires=0
        with self.assertRaises(BotError):self.session.decode(client.seal({'t':'human','r':'b'*32}))
        with self.assertRaises(BotError):client.seal({'text':'x'*20000})
    def test_handshake_ticket_has_no_chat_or_key(self):
        self.assertEqual(set(self.ticket['claim']),{'bot','host','visitor','epoch','expires','domain'})
        answer=self.session.accept(self.ticket);self.assertNotIn('private',json.dumps(answer))

if __name__=='__main__':unittest.main()
