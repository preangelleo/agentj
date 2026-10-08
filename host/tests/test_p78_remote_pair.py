import _hermetic
import asyncio
import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from agentj import remote_pair as rp, cloud, wire, serve
from agentj.state import State

def raw(pk): return pk.public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)

class Security(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.st=State(Path(self.tmp.name)/'s');self.st.init()
  self.link={'api':'https://agentj.app/api','host_id':wire.b64u(b'h'*16),'tenant':{'slug':'p78qa','name':'QA'},'linked_at':1,'last_seq':0,'via':'code'}
  cloud._write_cloud_file(self.st,self.link)
  self.sk=Ed25519PrivateKey.generate();self.browser=X25519PrivateKey.generate()
  self.now=int(time.time()*1000)
  self.q={'v':1,'id':wire.b64u(os.urandom(16)),'host':self.link['host_id'],'seat':self.link['host_id'],'account':'p78qa','channel':self.st.config()['channel'],'op':'start','browser':wire.b64u(raw(self.browser.public_key())),'device':None,'handshake':None,'target':None,'created_at':self.now,'expires_at':self.now+300000}
 def tearDown(self):self.tmp.cleanup()
 def signed(self,q=None):
  body=wire.b64u(json.dumps(q or self.q).encode());return {'body':body,'sig':wire.b64u(self.sk.sign((rp.CONTEXT+'\n'+body).encode()))}
 def open(self,q=None):return rp.open_request(self.signed(q),self.st,self.now,[raw(self.sk.public_key())])
 def test_binding_ttl_signature_and_strict_shape(self):
  self.assertEqual(self.open(),self.q)
  for k,v in [('host','another'),('seat','another'),('account','another'),('channel','another'),('expires_at',self.now),('expires_at',self.now+600001),('created_at',self.now-15001),('created_at',True),('op','shell'),('browser','bad'),('extra',1)]:
   with self.subTest(k=k,v=v):self.assertIsNone(self.open({**self.q,k:v}))
  self.assertIsNone(rp.open_request({'body':self.signed()['body'],'sig':wire.b64u(os.urandom(64))},self.st,self.now,[raw(self.sk.public_key())]))
 def test_nonce_durable_and_expiry(self):
  self.assertTrue(rp.consume(self.st,self.q,self.now));self.assertFalse(rp.consume(State(self.st.root),self.q,self.now+1))
  self.assertEqual((self.st.root/'remote_pair_nonces.json').stat().st_mode&0o777,0o600)
 def test_cipher_only_browser_can_open_and_binds_request(self):
  reply=rp.encrypt_reply(self.q,{'link':'private test material'})
  aad=(rp.CONTEXT+'\n'+self.q['id']).encode();shared=self.browser.exchange(X25519PublicKey.from_public_bytes(wire.unb64u(reply['epk'])))
  key=hashlib.sha256(aad+shared).digest()
  self.assertEqual(json.loads(AESGCM(key).decrypt(wire.unb64u(reply['iv']),wire.unb64u(reply['data']),aad))['link'],'private test material')
  self.assertNotIn('private test material',json.dumps(reply))
  with self.assertRaises(Exception):AESGCM(key).decrypt(wire.unb64u(reply['iv']),wire.unb64u(reply['data']),b'other request')
 def test_default_on_persistent_off(self):
  self.assertTrue(rp.enabled(self.st));rp.set_enabled(self.st,False);self.assertFalse(rp.enabled(State(self.st.root)))

class HostFlow(Security,unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.h=serve.Host(self.st,events='jsonl',read_stdin=False);self.h.relay_up=True;self.q["created_at"]=self.h.remote_pair.started_at
  self.h.send_app=AsyncMock(return_value=True);self.h._op=AsyncMock();self.h.on_ready=AsyncMock();self.h.onboarding_paired=AsyncMock();self.h._pk_offer=AsyncMock();self.h._bulk=AsyncMock();self.h.broadcast=AsyncMock()
  self.pin=patch('agentj.notices.official_keys',return_value=[raw(self.sk.public_key())]);self.pin.start()
 async def asyncTearDown(self):
  self.pin.stop()
  for s in self.h.sessions.values():
   if s.timer:s.timer.cancel()
  if self.h.pairing and self.h.pairing.timer:self.h.pairing.timer.cancel()
 async def pending(self):
  p=self.h.pairing;pub=os.urandom(32);s=serve.Session(cid=7,state='pending',pub=pub,device=wire.device_id(pub),name='P78 phone',h=os.urandom(32),deadline=time.monotonic()+60)
  p.cid=7;self.h.sessions[7]=s;return p,s
 async def test_start_scoped_auto_approval_logs_and_notification(self):
  await self.h.remote_pair.execute(self.signed());p,s=await self.pending()
  await self.h.remote_pair.pending(p,s)
  self.assertEqual(s.state,'ready');self.assertEqual(self.st.devices()[s.device]['source'],'account')
  self.assertIn('account_passkey',self.st.approvals_path.read_text());self.h.broadcast.assert_awaited_once()
  self.assertIsNone(self.h.pairing)
  await self.h.remote_pair.execute(self.signed());self.assertIsNone(self.h.pairing,'replayed request must not create QR')
 async def test_off_and_offline_never_create_qr(self):
  rp.set_enabled(self.st,False);await self.h.remote_pair.execute(self.signed());self.assertIsNone(self.h.pairing)
  rp.set_enabled(self.st,True);self.h.relay_up=False;self.q['id']=wire.b64u(os.urandom(16));await self.h.remote_pair.execute(self.signed());self.assertIsNone(self.h.pairing)
 async def test_switch_off_after_qr_prevents_grant(self):
  await self.h.remote_pair.execute(self.signed());p,s=await self.pending();rp.set_enabled(self.st,False)
  await self.h.remote_pair.pending(p,s);self.assertEqual(self.st.devices(),{})
 async def test_pending_approval_exact_handshake_and_device(self):
  # A local CLI QR; inspecting it never grants access.
  p=serve.Pairing(os.urandom(16),os.urandom(32),time.time()+60,time.monotonic()+60,None);self.h.pairing=p;p,s=await self.pending()
  self.q.update(op='inspect');await self.h.remote_pair.execute(self.signed());self.assertEqual(self.st.devices(),{})
  self.q.update(id=wire.b64u(os.urandom(16)),op='approve',device=s.device,handshake=wire.b64u(b'x'*16))
  await self.h.remote_pair.execute(self.signed());self.assertEqual(self.st.devices(),{})
  self.q.update(id=wire.b64u(os.urandom(16)),handshake=rp.handshake(s));await self.h.remote_pair.execute(self.signed());self.assertEqual(s.state,'ready')

class OptionalPassphrase(Security):
 def test_bound_without_passphrase_and_unbound_still_requires_it(self):
  from agentj import doctor, gate
  self.assertEqual(doctor.check_passphrase(self.st)['status'], 'ok')
  self.st.cloud_path.unlink()
  self.assertEqual(doctor.check_passphrase(self.st)['status'], 'warn')
  with self.assertRaises(gate.GateError):gate.verify(self.st, 'not-a-passphrase')

class OwnerApproval(HostFlow):
 async def test_resume_requires_exact_state_and_one_use_without_passphrase(self):
  from agentj import controls
  controls.set_estop(self.st,True,'terminal');self.h.do_resume=AsyncMock()
  self.q.update(op='resume',target={'sha':'0'*64})
  await self.h.remote_pair.execute(self.signed());self.h.do_resume.assert_not_awaited()
  self.q.update(id=wire.b64u(os.urandom(16)),target={'sha':rp.stop_sha(self.st)})
  await self.h.remote_pair.execute(self.signed());self.h.do_resume.assert_awaited_once()
  await self.h.remote_pair.execute(self.signed());self.h.do_resume.assert_awaited_once()
 async def test_expired_and_cross_host_never_resume(self):
  self.h.do_resume=AsyncMock();self.q.update(op='resume',target={'sha':rp.stop_sha(self.st)},expires_at=self.now-1)
  await self.h.remote_pair.execute(self.signed());self.h.do_resume.assert_not_awaited()
  self.q.update(expires_at=self.now+300000,host='wrong');await self.h.remote_pair.execute(self.signed());self.h.do_resume.assert_not_awaited()
 async def test_task_enable_requires_same_contract(self):
  self.q.update(op='task_on',target={'id':'qa-task','tsha':'a'*64})
  with patch('agentj.tasks.set_enabled') as fn:
   await self.h.remote_pair.execute(self.signed());fn.assert_called_once_with(self.st,None,'qa-task',True,'account:'+self.q['id'],'a'*64)

class RestartAndExpiry(HostFlow):
 async def test_restart_never_executes_a_preboot_request(self):
  self.q['created_at']=self.h.remote_pair.started_at-1
  await self.h.remote_pair.execute(self.signed());self.assertIsNone(self.h.pairing)
 async def test_expired_qr_never_grants_and_changed_account_never_grants(self):
  await self.h.remote_pair.execute(self.signed());p,s=await self.pending()
  p.remote['expires_at']=int(time.time()*1000)-1
  await self.h.remote_pair.pending(p,s);self.assertEqual(self.st.devices(),{})
 async def test_account_rebound_while_phone_is_pending_never_grants(self):
  await self.h.remote_pair.execute(self.signed());p,s=await self.pending()
  link=cloud.read_cloud(self.st);link['tenant']['slug']='other-account'
  self.st.write_private(self.st.cloud_path,json.dumps(link).encode())
  await self.h.remote_pair.pending(p,s)
  self.assertEqual(self.st.devices(),{})

 async def test_disabling_after_request_blocks_owner_resume(self):
  self.q.update(op='resume',target={'sha':rp.stop_sha(self.st)})
  rp.set_enabled(self.st,False);self.h.do_resume=AsyncMock()
  await self.h.remote_pair.execute(self.signed());self.h.do_resume.assert_not_awaited()


class HandoverRoute(unittest.TestCase):
    def test_bound_handover_prefers_account_and_retains_local_alternative(self):
        from agentj import handover
        f={"set_up":True,"name":"Desk","account":"fake-account","phones":0,"agent":None,"folder":None,"web":"https://m.agentj.app","account_url":"https://agentj.app/account","service":None}
        for lang,term in [("zh","通行密钥"),("en","passkey")]:
            text=handover.note(f,lang)
            self.assertLess(text.index(term),text.index("agentj pair"))
            self.assertIn("不需要在终端设置批准口令" if lang=="zh" else "No terminal approval passphrase is required",text)
            self.assertIn("agentj pair --link",text)
    def test_unbound_handover_still_explains_local_approval(self):
        from agentj import handover
        f={"set_up":True,"name":"Desk","account":None,"phones":0,"agent":None,"folder":None,"web":"https://m.agentj.app","account_url":"https://agentj.app/account","service":None}
        text=handover.note(f,"en")
        self.assertNotIn("No terminal approval passphrase is required",text)
        self.assertIn("then your approval passphrase",text)
