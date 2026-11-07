"""Real crypto/signatures and FD checks; only the independent HTTP network is a fake."""
import _hermetic
import asyncio
import hashlib
import json
import math
import secrets
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from agentj import controls, media, private_cipher as cipher, private_direct, private_files, private_http, wire
from test_feedback574_paired import sign
from _testtmp import TEST_TMP


class DirectFiles(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TEST_TMP); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.work = self.root/'work'; self.work.mkdir()
        self.key = Ed25519PrivateKey.generate(); self.signer = self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.hostkey = Ed25519PrivateKey.generate(); self.results = []; self.allowed = True
        self.session = NS(device='phone', cid=1)
        self.st = NS(root=self.root/'state', sign_key=lambda d:self.signer, agent_session=lambda kind:'session',
                     append_private=lambda *a:None, controls_path=self.root/'controls',
                     _ed_pub=lambda k:k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
        def write_private(path, data):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.st.write_private = write_private
        self.scanner = media.Media(self.st, str(self.work))
        self.host = NS(st=self.st, sk=self.hostkey, identity_ok=True, seat_refusal=lambda:None, channel=wire.channel_id(self.st._ed_pub(self.hostkey)),
                       cfg={'relay':'wss://relay.example'}, nonces=controls.Nonces(), agent=NS(kind='test'), media=self.scanner,
                       stopped=lambda:False, _paired_live=lambda s:s is self.session and self.allowed)
        async def send(s, result): self.results.append(result)
        self.host.send_app=send
        self.page={'id':1,'src':{'k':'phone','text':'Synthetic file request'},'reply':{'text':'[pdf](large.pdf)'},'end':'done'}
        self.host.hist=NS(epoch='epoch',parts={1:[1]},get=lambda tid:self.page if tid==1 else None)
        self.downloads=private_files.Downloads(self.host)
        self.host.private_files=self.downloads
        self.path=self.work/'large.pdf'
        self.ciphertexts={}; self.cloud_claim=None; self.http=[]
        self.make_file(18)
        self.fake=patch.object(private_http.RelayFiles,'request',lambda transport,method,path,body:self.http_request(transport,method,path,body))
        self.fake.start();self.addCleanup(self.fake.stop)

    def make_file(self, mib):
        self.downloads.offers.clear()
        with self.path.open('wb') as f:f.write(b'%PDF-1.7\n');f.truncate(mib*1024*1024)
        self.card=self.downloads.offer(self.scanner,media.Ref('large.pdf','large.pdf',True),turn=1,direct=True)
        self.assertIsNotNone(self.card)

    def http_request(self,transport,method,path,body):
        proof=private_http.headers(self.host,method,path,body,now=1234,nonce=wire.b64u(b'n'*16))
        msg=f"agentj-file-host-v1\n{method}\n{path}\n1234\n{proof['X-AJ-File-Nonce']}\n{hashlib.sha256(body).hexdigest()}".encode()
        self.hostkey.public_key().verify(wire.unb64u(proof['X-AJ-File-Signature']),msg)
        self.http.append((method,path,len(body)))
        self.assertNotIn(self.path.name.encode(),body)
        if '/chunk/' in path:
            index=int(path.rsplit('/',1)[1]); self.ciphertexts[index]=body
        elif path.endswith('/claim'):
            claim=json.loads(body)
            if self.cloud_claim is not None:self.assertEqual(self.cloud_claim,claim)
            self.cloud_claim=claim
        elif method=='DELETE':self.ciphertexts.clear()
        elif method=='PUT':
            meta=json.loads(body);self.assertEqual(set(meta),{'v','total','chunk_size','chunks','expires'})
            self.assertLessEqual(meta['expires'],__import__('time').time()+86400)

    def req(self, **kw):return {'id':self.card['id'],'seat':self.card['seat'],'claim':True,'transfer':'a'*32,**kw}
    async def call(self,req,session=None,key=None):
        session=session or self.session
        obj=sign(self.host,session,key or self.key,'private_file_get',req,request=req)
        await self.downloads.on_phone(session,obj)
        return self.results[-1]
    async def ready(self):
        response=await self.call(self.req());self.assertTrue(response['ok']);self.assertTrue(response['pending'])
        claim=self.downloads.direct.claims[self.card['id']]
        await claim['task']
        result=await self.call(self.req());self.assertTrue(result['ok'],result);self.assertFalse(result['pending'],result)
        return result
    def challenge(self, **kw):return {'offer':self.card['id'],'transfer':'a'*32,'device_pk':wire.b64u(self.signer),'index':0,**kw}

    async def test_18m_and_50m_ciphertext_uses_http_and_exact_hash(self):
        for mib in (18,50):
            with self.subTest(mib=mib):
                self.make_file(mib);self.ciphertexts={};self.cloud_claim=None;self.http=[]
                result=await self.ready()
                key=wire.unb64u(result['key']);prefix=wire.unb64u(result['nonce_prefix']);digest=hashlib.sha256()
                self.assertEqual(len(key),32);self.assertEqual(len(prefix),8)
                self.assertEqual(result['endpoint'],f"https://relay.example/v1/file/{self.host.channel}/{self.card['id']}")
                self.assertEqual(result['grant']['device_pk'],wire.b64u(self.signer))
                self.assertEqual(len(self.ciphertexts),math.ceil(mib*1024*1024/cipher.CHUNK))
                for index,encrypted in sorted(self.ciphertexts.items()):
                    self.assertLessEqual(len(encrypted),cipher.CHUNK+16)
                    digest.update(AESGCM(key).decrypt(cipher.nonce(prefix,index),encrypted,
                                  cipher.aad(self.host.channel,self.card['id'],'a'*32,index,mib*1024*1024)))
                self.assertEqual(digest.hexdigest(),self.card['sha256'])
                self.assertTrue(await self.downloads.direct.authorize(self.challenge()))
                self.assertTrue(all('d' not in response for response in self.results),'No file bytes ride Noise')
                self.results.clear()

    async def test_atomic_competing_claims_and_same_transfer_resume(self):
        first,second=await asyncio.gather(self.call(self.req()),self.call(self.req(transfer='b'*32)))
        self.assertTrue(first['ok']);self.assertFalse(second['ok'])
        await self.downloads.direct.claims[self.card['id']]['task']
        self.assertFalse((await self.call(self.req(transfer='b'*32)))['ok'])
        self.assertTrue((await self.call(self.req()))['ok'])
        other=NS(device='other',cid=2);old=self.host._paired_live
        self.host._paired_live=lambda s:self.allowed
        self.assertFalse((await self.call(self.req(),session=other))['ok'])
        self.host._paired_live=old

    async def test_authorization_rechecks_expiry_unpair_signer_stop_source_and_session(self):
        await self.ready()
        direct=self.downloads.direct;row=self.downloads.offers[self.card['id']]
        for mutation,restore in [
            (lambda:setattr(self.host,'identity_ok',False),lambda:setattr(self.host,'identity_ok',True)),
            (lambda:setattr(self.host,'seat_refusal',lambda:'suspended'),lambda:setattr(self.host,'seat_refusal',lambda:None)),
            (lambda:setattr(self.host,'_unbinding_all',True),lambda:setattr(self.host,'_unbinding_all',False)),
            (lambda:setattr(self.host,'_pairing_epoch',1),lambda:setattr(self.host,'_pairing_epoch',0)),
            (lambda:setattr(self.host,'st',object()),lambda:setattr(self.host,'st',self.st)),
            (lambda:setattr(self,'allowed',False),lambda:setattr(self,'allowed',True)),
            (lambda:setattr(self.host,'stopped',lambda:True),lambda:setattr(self.host,'stopped',lambda:False)),
            (lambda:setattr(self.st,'sign_key',lambda d:b'x'*32),lambda:setattr(self.st,'sign_key',lambda d:self.signer)),
            (lambda:setattr(self.st,'agent_session',lambda k:'changed'),lambda:setattr(self.st,'agent_session',lambda k:'session')),
            (lambda:row.update(until=0),lambda:row.update(until=self.downloads.clock()+60)),
        ]:
            mutation();self.assertFalse(await direct.authorize(self.challenge()));restore()
        for change in ({'device_pk':wire.b64u(b'z'*32)},{'transfer':'b'*32},{'offer':'e'*32},{'index':-1},{'index':True},{'index':999999}):
            self.assertFalse(await direct.authorize(self.challenge(**change)))
        self.path.write_bytes(b'%PDF-1.7\nchanged')
        self.assertFalse(await direct.authorize(self.challenge()))

    async def test_reconnect_requires_fresh_signed_same_device_same_transfer_claim(self):
        result=await self.ready()
        original_key=result['key']
        self.session=NS(device='phone',cid=2)
        self.assertFalse(await self.downloads.direct.authorize(self.challenge()))
        self.assertFalse((await self.call(self.req(transfer='b'*32)))['ok'])
        resumed=await self.call(self.req())
        self.assertTrue(resumed['ok'])
        self.assertEqual(resumed['key'],original_key)
        self.assertTrue(await self.downloads.direct.authorize(self.challenge()))

    async def test_stale_replaced_socket_and_wrong_request_ids_do_not_authorize(self):
        await self.ready();sent=[]
        async def send(value):sent.append(json.loads(value))
        ws=NS(send=send);self.host.ws=ws
        msg={'t':'file_authorize','rid':'r'*22,**self.challenge()}
        await self.downloads.direct.relay_message(ws,json.dumps(msg))
        self.assertEqual(sent,[{'t':'file_authorize_result','rid':'r'*22,'ok':True}])
        self.host.ws=object()
        await self.downloads.direct.relay_message(ws,json.dumps(msg));self.assertEqual(len(sent),1)
        self.host.ws=ws
        msg['rid']='invalid'
        await self.downloads.direct.relay_message(ws,json.dumps(msg));self.assertEqual(len(sent),1)

    async def test_failed_upload_never_reopens_claim_for_another_device(self):
        async def fail(*args):raise ValueError('fixed-network-failure')
        self.downloads.direct.upload=fail
        await self.call(self.req());await self.downloads.direct.claims[self.card['id']]['task']
        result=await self.call(self.req());self.assertFalse(result['ok']);self.assertTrue(result['retryable'])
        self.assertFalse((await self.call(self.req(transfer='b'*32,retry=True)))['ok'])
        self.downloads.direct.upload=self.downloads.direct._upload
        self.assertTrue((await self.call(self.req(retry=True)))['pending'])
        await self.downloads.direct.claims[self.card['id']]['task']
        self.assertTrue((await self.call(self.req()))['direct'])

    async def test_unsigned_cross_seat_replay_and_legacy_read_cannot_get_direct_bytes(self):
        req=self.req();obj=sign(self.host,self.session,self.key,'private_file_get',req,request=req)
        await self.downloads.on_phone(self.session,obj);await self.downloads.on_phone(self.session,obj)
        self.assertFalse(self.results[-1]['ok'])
        self.assertFalse((await self.call(self.req(seat='x'*22)))['ok'])
        self.assertFalse((await self.call({'id':self.card['id'],'seat':self.card['seat'],'offset':0,'transfer':'a'*32}))['ok'])
        await self.downloads.direct.claims[self.card['id']]['task']

    def legacy_req(self, offset=0, transfer='a'*32):
        return {'id':self.card['id'],'seat':self.card['seat'],'offset':offset,'transfer':transfer}

    async def test_old_signed_offset_50m_all_bytes_sha_and_one_recipient_burn(self):
        self.make_file(50);offset=0;digest=hashlib.sha256();count=0
        while offset < self.card['bytes']:
            result=await self.call(self.legacy_req(offset));self.assertTrue(result['ok'],result)
            data=wire.unb64u(result['d']);self.assertEqual(hashlib.sha256(data).hexdigest(),result['sha256'])
            self.assertEqual(result['offset'],offset);digest.update(data);offset+=len(data);count+=1
            self.assertEqual(result['last'],offset==self.card['bytes'])
        self.assertEqual(digest.hexdigest(),self.card['sha256']);self.assertEqual(offset,50*1024*1024)
        self.assertGreater(count,1000);self.assertEqual(self.http,[])
        self.legacy_receipt={'bytes':offset,'chunks':count,'sha256':digest.hexdigest(),
                             'expected_sha256':self.card['sha256'],'http_requests':len(self.http),'passed':True}
        self.assertNotIn(self.card['id'],self.downloads.offers)
        self.assertFalse((await self.call(self.legacy_req()))['ok'])
        self.assertFalse((await self.call(self.req()))['ok'])

    async def test_atomic_http_legacy_race_has_one_sticky_transport_winner(self):
        for legacy_first in (True,False):
            self.make_file(18)
            requests=(self.legacy_req(),self.req()) if legacy_first else (self.req(),self.legacy_req())
            results=await asyncio.gather(*(self.call(req) for req in requests))
            self.assertTrue(results[0]['ok']);self.assertFalse(results[1]['ok'])
            claim=self.downloads.direct.claims[self.card['id']]
            self.assertEqual(claim['mode'],'legacy' if legacy_first else 'https')
            self.assertEqual(len(self.downloads.direct.claims),1)
            if claim['task']:await claim['task']
            self.assertFalse((await self.call(requests[1]))['ok'])

    async def test_legacy_claim_rejects_device_mode_transfer_session_key_and_replay(self):
        result=await self.call(self.legacy_req());self.assertTrue(result['ok']);offset=len(wire.unb64u(result['d']))
        self.assertFalse((await self.call(self.req()))['ok'])
        self.assertFalse((await self.call(self.legacy_req(offset,'b'*32)))['ok'])
        self.assertFalse((await self.call(self.legacy_req(0)))['ok'])
        original_session=self.session;other=NS(device='other',cid=3)
        self.host._paired_live=lambda session:True
        self.assertFalse((await self.call(self.legacy_req(offset),session=other))['ok'])
        self.session=NS(device='phone',cid=4)
        self.assertFalse((await self.call(self.legacy_req(offset)))['ok'])
        self.session=original_session
        old_key=self.key;new_key=Ed25519PrivateKey.generate()
        self.st.sign_key=lambda device:new_key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
        self.assertFalse((await self.call(self.legacy_req(offset),key=new_key))['ok'])
        self.st.sign_key=lambda device:self.signer
        obj=sign(self.host,self.session,old_key,'private_file_get',self.legacy_req(offset),request=self.legacy_req(offset))
        await self.downloads.on_phone(self.session,obj);self.assertTrue(self.results[-1]['ok'])
        await self.downloads.on_phone(self.session,obj);self.assertFalse(self.results[-1]['ok'])

    async def test_legacy_chunk_rechecks_identity_seat_source_after_fd_read_before_release(self):
        for changed in ('identity','seat','source','session','estop'):
            self.make_file(18);original=self.downloads.read
            def read(row,offset):
                data=original(row,offset)
                if changed=='identity':self.host.identity_ok=False
                elif changed=='seat':self.host.seat_refusal=lambda:'suspended'
                elif changed=='source':self.path.write_bytes(b'%PDF-1.7 changed')
                elif changed=='session':self.st.agent_session=lambda kind:'replaced'
                elif changed=='estop':self.host.stopped=lambda:True
                return data
            self.downloads.read=read
            # Source mutation is caught by the actual FD reader if it occurs during reading;
            # this stronger fixture mutates after it returned and requires a final FD identity proof.
            result=await self.call(self.legacy_req())
            self.assertFalse(result['ok'],changed);self.assertNotIn('d',result)
            self.downloads.read=original;self.host.identity_ok=True;self.host.seat_refusal=lambda:None
            self.st.agent_session=lambda kind:'session';self.host.stopped=lambda:False

    async def test_malformed_legacy_request_never_reserves_one_time_offer(self):
        for offset in (False,True,-1,1,'0'):
            self.assertFalse((await self.call(self.legacy_req(offset)))['ok'])
            self.assertNotIn(self.card['id'],self.downloads.direct.claims)

    async def test_complete_burns_offer_and_cleans_up_cloud_ciphertext(self):
        await self.ready()
        result=await self.call(self.req(complete=True))
        self.assertTrue(result['complete'])
        self.assertFalse((await self.call(self.req()))['ok'])
        self.assertFalse(await self.downloads.direct.authorize(self.challenge()))
        if self.downloads.direct.cleanups:
            await asyncio.gather(*self.downloads.direct.cleanups)
        self.assertEqual(self.ciphertexts,{})

    async def test_completed_18m_media_page_carries_independent_download(self):
        from agentj import media_live
        self.host.st.log=lambda *a,**k:None
        updates=[]
        self.host.hist_update=lambda tid,**kw:updates.append(kw)
        row={'generation':1,'scanner':self.scanner,'keys':set()}
        await media_live.scan(self.host,1,row)
        items,skips=updates[-1]['media']
        self.assertEqual(skips,[])
        self.assertEqual(len(items),1)
        card=items[0]['download']
        self.assertEqual(card['transport'],'https1')
        self.assertEqual(card['bytes'],18*1024*1024)
        self.assertEqual(card['sha256'],items[0]['sha256'])

    def test_safe_offer_bounds_symlinks_and_24_hour_expiry(self):
        self.assertEqual(self.card['transport'],'https1');self.assertEqual(self.card['mime'],'application/pdf')
        self.assertEqual(self.card['expires'],self.card['expires_at'])
        row=self.downloads.offers[self.card['id']]
        self.assertAlmostEqual(row['until']-self.downloads.clock(),86400,delta=2)
        (self.work/'link.pdf').symlink_to(self.path)
        self.assertIsNone(self.downloads.offer(self.scanner,media.Ref('link.pdf','link.pdf',True),turn=1,direct=True))
        self.assertIsNone(self.downloads.offer(self.scanner,media.Ref('../large.pdf','../large.pdf',True),turn=1,direct=True))
        self.page['end']='open'
        self.assertIsNone(self.downloads.offer(self.scanner,media.Ref('large.pdf','large.pdf',True),turn=1,direct=True))

class HTTPSContract(unittest.TestCase):
    def test_relay_origin_is_fixed_and_no_downgrade_credentials_or_path(self):
        self.assertEqual(private_http.origin('wss://relay.example'),'https://relay.example')
        self.assertEqual(private_http.origin('ws://127.0.0.1:9000'),'http://127.0.0.1:9000')
        for bad in ('ws://relay.example','wss://user:password@relay.example','wss://relay.example/path','wss://relay.example/?query=x'):
            with self.assertRaises((ValueError, __import__('agentj.cloud',fromlist=['CloudError']).CloudError)):private_http.origin(bad)

    def test_429_retry_after_uses_fresh_proof_same_body_and_total_deadline(self):
        import io
        import urllib.error
        from unittest.mock import Mock
        clock=[0.0]
        hostkey=Ed25519PrivateKey.generate()
        host=NS(cfg={'relay':'wss://relay.example'},sk=hostkey,st=NS(_ed_pub=lambda k:k.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)))
        response=Mock(status=204,read=lambda n:b'')
        response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        limited=lambda:urllib.error.HTTPError('https://relay.example/file',429,'rate',{'Retry-After':'2'},io.BytesIO(b''))
        opener=Mock();opener.open.side_effect=[limited(),limited(),response]
        with patch.object(private_http.time,'monotonic',side_effect=lambda:clock[0]),patch.object(private_http.time,'sleep',side_effect=lambda n:clock.__setitem__(0,clock[0]+n)),patch.object(private_http.urllib.request,'build_opener',return_value=opener):
            transport=private_http.RelayFiles(host)
            transport.request('PUT','/v1/file/'+wire.channel_id(host.st._ed_pub(hostkey))+'/'+'a'*32,b'synthetic-ciphertext')
            self.assertEqual(clock[0],4)
            requests=[call.args[0] for call in opener.open.call_args_list]
            self.assertEqual({r.data for r in requests},{b'synthetic-ciphertext'})
            self.assertEqual(len({r.get_header('X-aj-file-nonce') for r in requests}),3)
            opener.open.side_effect=lambda *a,**k:limited()
            # Raise instead of return for an exhausted rate window.
            def always_limited(*a,**k):raise limited()
            opener.open.side_effect=always_limited
            transport.deadline=clock[0]+3
            with self.assertRaises(ValueError):transport.request('PUT','/v1/file/x/y',b'synthetic-ciphertext')
            self.assertEqual(clock[0],7)
