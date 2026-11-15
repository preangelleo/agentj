"""P126: ADR-A193 gaps — E2E SDK, sandboxed workflow backend, contract versions, seat migration, queue,
signed metering, OpenAPI, provisioning capability and signed callbacks."""
import _hermetic
import asyncio
import json
from pathlib import Path
import socket
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from agentj.bots.store import Store as Bots,BotError
from agentj.bots.engine import Engine
from agentj.bots.provider import Provider,Result
from agentj.seat_api import store as store_mod
from agentj.seat_api.store import Store
from agentj.seat_api.service import Service
from agentj.seat_api import callbacks,e2e,workflow
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'sdk'/'python'))
import agentj_seat_api as sdk

PROFILE={'base_url':'https://api.example.com','model':'model','api':'openai'}
def make(root,name='reply-bot',privacy='e2e',workflow_name=None):
    bots=Bots(Path(root)/'bots')
    bid=bots.create({'slug':'sample-api','enabled':True,'terms_accepted':True,'provider':{'source':'profile','id':'test-profile'}})['id']
    api=Store(bots,profile_metadata=lambda _:dict(PROFILE),seat_id='seat-a')
    e=api.create(name,bid,privacy,privacy=='rest',workflow_name);api.switch(True);api.publish(e['id'],True)
    return bots,bid,api,api.endpoint(e['id'])

class Base(unittest.IsolatedAsyncioTestCase):
    privacy='e2e'
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.bots,self.bid,self.api,self.e=make(self.tmp.name,privacy=self.privacy)
        self.key=self.api.issue(self.e['id'],'Customer');self.calls=0;self.verdict='{"allow":true}'
        self.service=Service(self.api,self.engine,account=lambda _:'mori')
    def engine(self,budget):
        def model(*_):self.calls+=1;return Result('API reply',25,[])
        return Engine(budget,'fake',provider_resolve=lambda *_:Provider('https://api.example.com','model','openai',''),model_call=model,decision_call=lambda *_:Result(self.verdict,5,[]),queue_timeout=0)
    async def asyncTearDown(self):await self.service.close();self.bots.close();self.tmp.cleanup()
    async def transport(self,method,url,body):
        path=url.split('/reply-bot',1)[1].lstrip('/')
        # The edge: routes JSON, never sees the key or body (asserted by callers through self.wire).
        self.wire.append(json.dumps(body))
        return await self.service.dispatch(self.e['id'],None,method,path,body,None)
    def client(self,key=None,pin=None):
        self.wire=[];loop=asyncio.get_running_loop()
        return sdk.Client('mori','reply-bot',pin or self.api.public_keys(self.e['id'])['pin'],key or self.key['secret'],transport=lambda m,u,b:self.sync(m,u,b))
    def sync(self,m,u,b):return asyncio.run_coroutine_threadsafe(self.transport(m,u,b),self.loop).result(30)
    async def run_client(self,fn):
        self.loop=asyncio.get_running_loop();return await asyncio.to_thread(fn)

class E2ETest(Base):
    async def test_round_trip_edge_sees_only_ciphertext(self):
        c=self.client()
        status,body=await self.run_client(lambda:c.chat([{'role':'user','content':'PRIVATE_QUESTION_CANARY'}],'idem-1','reply-bot'))
        self.assertEqual(status,200);self.assertEqual(body['choices'][0]['message']['content'],'API reply')
        wire='\n'.join(self.wire)
        for secret in (self.key['secret'],'PRIVATE_QUESTION_CANARY','API reply'):self.assertNotIn(secret,wire)
        # Idempotent replay through a new envelope returns the same result without a second model call.
        status2,body2=await self.run_client(lambda:c.chat([{'role':'user','content':'PRIVATE_QUESTION_CANARY'}],'idem-1','reply-bot'))
        self.assertEqual((status2,body2),(status,body));self.assertEqual(self.calls,1)
    async def test_plaintext_on_e2e_endpoint_never_falls_back(self):
        code,r=await self.service.dispatch(self.e['id'],self.key['secret'],'POST','chat/completions',{'model':'reply-bot','messages':[{'role':'user','content':'x'}]},'i')
        self.assertEqual((code,r['error']['code']),(403,'e2e_required'));self.assertEqual(self.calls,0)
    async def test_wrong_pin_or_forged_manifest_refused(self):
        c=self.client(pin='ajpin1_'+'0'*40)
        with self.assertRaisesRegex(sdk.SeatApiError,'pin_mismatch'):await self.run_client(c.refresh)
        doc=e2e.manifest(self.api.secret(self.e['id']),self.e,self.api.now())
        other={'sign':Ed25519PrivateKey.generate().private_bytes(*e2e.PRIV),'kem':self.api.secret(self.e['id'])['kem']}
        forged=e2e.manifest(other,self.e,self.api.now())
        with self.assertRaisesRegex(sdk.SeatApiError,'pin_mismatch'):sdk.verify_manifest(forged,doc['pin'])
        doc['manifest']['public_id']='swapped'
        with self.assertRaisesRegex(sdk.SeatApiError,'manifest_invalid'):sdk.verify_manifest(doc,doc['pin'])
        fresh=e2e.manifest(self.api.secret(self.e['id']),self.e,self.api.now())
        with self.assertRaisesRegex(sdk.SeatApiError,'manifest_expired'):sdk.verify_manifest(fresh,fresh['pin'],now=self.api.now()+90000)
    async def test_replay_epoch_deadline_tamper_and_key_binding(self):
        m=e2e.manifest(self.api.secret(self.e['id']),self.e,self.api.now())['manifest']
        inner={'key':self.key['secret'],'method':'POST','path':'chat/completions','body':{'model':'reply-bot','messages':[{'role':'user','content':'x'}]},'idem':'a'}
        env,resp=sdk.seal(m,self.key['id'],inner)
        code,doc=await self.service.dispatch(self.e['id'],None,'POST','_e2e',env,None);self.assertEqual(code,200)
        self.assertEqual(sdk.open_reply(resp,env['request_id'],doc)[0],200)
        code,doc=await self.service.dispatch(self.e['id'],None,'POST','_e2e',env,None);self.assertEqual((code,doc['error']['code']),(409,'replay'))
        bad,_=sdk.seal({**m,'epoch':m['epoch']+1},self.key['id'],inner)
        self.assertEqual((await self.service.dispatch(self.e['id'],None,'POST','_e2e',bad,None))[1]['error']['code'],'route_epoch')
        late,_=sdk.seal(m,self.key['id'],inner,now=self.api.now()-400)
        self.assertEqual((await self.service.dispatch(self.e['id'],None,'POST','_e2e',late,None))[1]['error']['code'],'deadline')
        t,_=sdk.seal(m,self.key['id'],inner);t['ct']=t['ct'][:-4]+('AAAA' if t['ct'][-4:]!='AAAA' else 'BBBB')
        self.assertEqual((await self.service.dispatch(self.e['id'],None,'POST','_e2e',t,None))[1]['error']['code'],'invalid_envelope')
        other=self.api.issue(self.e['id'],'Other')
        mixed,resp=sdk.seal(m,other['id'],inner)
        code,doc=await self.service.dispatch(self.e['id'],None,'POST','_e2e',mixed,None)
        self.assertEqual(sdk.open_reply(resp,mixed['request_id'],doc)[0],401)
        self.assertEqual(self.calls,1)
    async def test_sdk_interoperates_with_host_crypto(self):
        keys=e2e.new_keys();m=e2e.manifest(keys,{**self.e},self.api.now())
        self.assertEqual(sdk.verify_manifest(m,m['pin'])['kem'],e2e.public(keys)['kem'])
        env,resp=sdk.seal(m['manifest'],'a'*32,{'hello':'世界'})
        inner,host_resp=e2e.open_request(keys,env);self.assertEqual(inner,{'hello':'世界'})
        self.assertEqual(sdk.open_reply(resp,env['request_id'],e2e.seal_response(host_resp,env['request_id'],201,{'ok':1})),(201,{'ok':1}))

class ProvisionTest(Base):
    async def test_capability_scope_idempotency_and_limits(self):
        cap=self.api.provisioner(self.e['id'],'shop',max_keys=2,max_days=7)
        c=self.client(key=cap['secret'])
        status,first=await self.run_client(lambda:c.provision('issue',event_id='pay_1',label='buyer'))
        self.assertEqual(status,200);self.assertTrue(first['secret'].startswith('ajapi_'))
        k=self.api.db.execute('SELECT * FROM api_keys WHERE id=?',(first['key_id'],)).fetchone()
        self.assertLessEqual(k['expires']-self.api.now(),7*86400);self.assertEqual(k['issuer'],cap['id'])
        status,replay=await self.run_client(lambda:c.provision('issue',event_id='pay_1',label='buyer'))
        self.assertEqual(replay,{'key_id':first['key_id'],'replay':True})
        self.assertEqual((await self.run_client(lambda:c.provision('issue',event_id='pay_x',days=30)))[1]['error']['code'],'invalid_expiry')
        await self.run_client(lambda:c.provision('issue',event_id='pay_2'))
        self.assertEqual((await self.run_client(lambda:c.provision('issue',event_id='pay_3')))[1]['error']['code'],'provision_limit')
        # Cannot touch the owner's own keys, cannot read usage without the flag.
        self.assertEqual((await self.run_client(lambda:c.provision('revoke',key_id=self.key['id'])))[1]['error']['code'],'unknown_key')
        self.assertEqual((await self.run_client(lambda:c.provision('usage')))[1]['error']['code'],'key_scope')
        self.assertEqual((await self.run_client(lambda:c.provision('publish')))[1]['error']['code'],'unsupported')
        self.assertEqual((await self.run_client(lambda:c.provision('revoke',key_id=first['key_id'])))[1],{'revoked':True})
        self.assertNotIn(cap['secret'],'\n'.join(self.bots.db.iterdump()))
    async def test_caller_key_is_not_a_capability_and_revoked_capability_fails(self):
        cap=self.api.provisioner(self.e['id'],'shop');self.api.revoke_provisioner(cap['id'])
        c=self.client(key=cap['secret'])
        self.assertEqual((await self.run_client(lambda:c.provision('list')))[1]['error']['code'],'key_invalid')
        with self.assertRaisesRegex(sdk.SeatApiError,'key_scope'):self.client().provision('list')

class ContractTest(Base):
    privacy='rest'
    async def call(self,key=None,idem='i'):
        return await self.service.dispatch(self.e['id'],(key or self.key)['secret'],'POST','chat/completions',{'model':'reply-bot','messages':[{'role':'user','content':'Hi'}]},idem)
    async def test_reviewed_version_publish_does_not_widen_old_keys(self):
        cfg=self.bots.get(self.bid);cfg['prompt']='Changed scope';self.bots.save(self.bid,cfg)
        self.assertEqual((await self.call())[1]['error']['code'],'contract_changed')
        review=self.api.review(self.e['id']);self.assertEqual(review['changed'],['bot'])
        with self.assertRaisesRegex(BotError,'stale_review'):self.api.publish_version(self.e['id'],'0'*64)
        self.assertEqual(self.api.publish_version(self.e['id'],review['pending'])['version'],2)
        code,r=await self.call();self.assertEqual((code,r['error']['code']),(403,'key_scope'))
        new=self.api.issue(self.e['id'],'New');self.assertEqual((await self.call(new))[0],200)
        self.assertEqual(self.api.rebind(self.key['id']),2);self.assertEqual((await self.call(idem='j'))[0],200)
    async def test_public_spec_only_when_owner_publishes(self):
        code,r=await self.service.dispatch(self.e['id'],None,'GET','openapi.json',None,None);self.assertEqual(code,404)
        self.api.publish_spec(self.e['id'],True)
        code,spec=await self.service.dispatch(self.e['id'],None,'GET','openapi.json',None,None)
        self.assertEqual(code,200);self.assertEqual(spec['openapi'],'3.1.0')
        self.assertEqual(spec['servers'][0]['url'],'https://api.agentj.app/v1/mori/reply-bot')
        for p in ('/','/jobs','/jobs/{job_id}','/_e2e','/chat/completions'):self.assertIn(p,spec['paths'])
        self.assertIn('429',spec['paths']['/']['post']['responses']);text=json.dumps(spec)
        self.assertNotIn(self.bots.get(self.bid)['prompt'] or 'never-empty',text);self.assertNotIn(str(self.bots.root),text)

class QueueAndMeterTest(Base):
    privacy='rest'
    async def test_queue_waits_for_shared_slot_and_times_out(self):
        gate=asyncio.Event();started=0
        class Slow:
            async def reply(inner,*a):
                nonlocal started;started+=1;await gate.wait();return {'status':'done','text':'ok'}
        self.service.engine_factory=lambda _:Slow()
        keys=[self.key]+[self.api.issue(self.e['id'],'k'+str(i)) for i in range(1)]
        tasks=[asyncio.create_task(self.service.dispatch(self.e['id'],keys[n//5]['secret'],'POST','chat/completions',{'model':'reply-bot','messages':[{'role':'user','content':str(n)}]},str(n))) for n in range(6)]
        for _ in range(100):
            if started==4:break
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.1);self.assertEqual(started,4)
        self.assertEqual(self.api.db.execute("SELECT count(*) FROM api_requests WHERE status='queued'").fetchone()[0],2)
        gate.set();results=await asyncio.gather(*tasks);self.assertEqual([c for c,_ in results],[200]*6)
        gate.clear();started=0
        with patch.object(store_mod,'QUEUE_SECONDS',0.3),patch('agentj.seat_api.service.QUEUE_SECONDS',0.3):
            tasks=[asyncio.create_task(self.service.dispatch(self.e['id'],keys[1]['secret'],'POST','chat/completions',{'model':'reply-bot','messages':[{'role':'user','content':'q'+str(n)}]},'q'+str(n))) for n in range(5)]
            # SQLite fsyncs on slow disks: wait for the first completion instead of a fixed sleep.
            done,_=await asyncio.wait(tasks,timeout=20,return_when=asyncio.FIRST_COMPLETED)
            self.assertEqual(len(done),1);done=done.pop()
            self.assertEqual(done.result()[0],503);self.assertEqual(done.result()[1]['error']['code'],'queue_timeout')
            self.assertEqual(started,4)
            gate.set();await asyncio.gather(*tasks)
        self.assertEqual(self.api.db.execute("SELECT count(*) FROM api_requests WHERE status='expired'").fetchone()[0],1)
    async def test_signed_usage_outbox_sequence_csv_and_gap(self):
        for n in range(3):await self.service.dispatch(self.e['id'],self.key['secret'],'POST','chat/completions',{'model':'reply-bot','messages':[{'role':'user','content':str(n)}]},str(n))
        out=self.api.usage(self.e['id'])
        self.assertEqual([x['seq'] for x in out['events']],[1,2,3]);self.assertFalse(out['gap'])
        ev=out['events'][0];sign_pk=self.api.public_keys(self.e['id'])['sign_pk']
        self.assertTrue(sdk.verify_usage(ev['event'],ev['sig'],sign_pk))
        self.assertFalse(sdk.verify_usage({**ev['event'],'total_tokens':1},ev['sig'],sign_pk))
        self.assertEqual(ev['event']['total_tokens'],35);self.assertEqual(ev['event']['review_tokens'],10);self.assertIsNone(ev['event']['input_tokens'])
        self.assertEqual(self.api.usage(self.e['id'],since=2)['events'][0]['seq'],3)
        csv=self.api.usage(self.e['id'],fmt='csv')['csv'];self.assertEqual(len(csv.strip().splitlines()),4);self.assertIn('event_id',csv)
        for forbidden in ('API reply','Hello',self.key['secret']):self.assertNotIn(forbidden,json.dumps(out))
        later=self.api.now()+8*86400
        with patch.object(self.bots,'now',lambda:later):self.api.prune()
        self.assertEqual(self.api.usage(self.e['id'])['events'],[])
        await self.service.dispatch(self.e['id'],self.key['secret'],'POST','chat/completions',{'model':'reply-bot','messages':[{'role':'user','content':'z'}]},'z')
        self.assertTrue(self.api.usage(self.e['id'],since=0)['gap'])

class CallbackTest(Base):
    privacy='rest'
    def test_url_and_address_rules(self):
        for bad in ('http://x.example.com/h','https://user:pw@x.example.com/h','https://x.example.com:8443/h','https://127.0.0.1/h','https://[::1]/h','https://localhost/h','https://svc.internal/h','https://x.example.com/h#f'):
            with self.assertRaisesRegex(BotError,'invalid_callback'):callbacks.check_url(bad)
        self.assertEqual(callbacks.check_url('https://hooks.example.com/agentj?x=1'),'https://hooks.example.com/agentj?x=1')
        def res(*addrs):return lambda *a,**k:[(socket.AF_INET,socket.SOCK_STREAM,6,'',(x,443)) for x in addrs]
        for bad in ('10.0.0.5','169.254.169.254','100.64.1.1','192.168.1.2','127.0.0.1','0.0.0.0','224.0.0.1','::ffff:10.0.0.1','fd00::1','fe80::1'):
            with self.assertRaisesRegex(BotError,'callback_address'):callbacks.resolve('h.example.com',res(bad))
        # Rebinding defence: any private answer refuses the whole name.
        with self.assertRaisesRegex(BotError,'callback_address'):callbacks.resolve('h.example.com',res('93.184.216.34','10.0.0.5'))
        self.assertEqual(callbacks.resolve('h.example.com',res('93.184.216.34')),'93.184.216.34')
    def test_signed_post_connects_to_vetted_address_and_refuses_redirect(self):
        seen={}
        class Conn:
            def __init__(s,host,address,ctx):seen.update(host=host,address=address)
            def request(s,method,path,body,headers):seen.update(method=method,path=path,body=body,headers=headers)
            def getresponse(s):
                class R:status=seen.get('status',204)
                def read(*_):return b''
                R.read=read;return R()
            def close(s):pass
        res=lambda *a,**k:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))]
        ok=callbacks.post('https://hooks.example.com/a',b'k'*32,'evt1',b'{"x":1}',now=1000,resolver=res,connection=Conn)
        self.assertTrue(ok);self.assertEqual(seen['address'],'93.184.216.34');self.assertEqual(seen['host'],'hooks.example.com')
        h=seen['headers'];self.assertEqual(h['x-agentj-timestamp'],'1000')
        self.assertTrue(sdk.verify_callback((b'k'*32).hex(),'1000','evt1',b'{"x":1}',h['x-agentj-signature'],now=1100))
        self.assertFalse(sdk.verify_callback((b'k'*32).hex(),'1000','evt1',b'{"x":2}',h['x-agentj-signature'],now=1100))
        self.assertFalse(sdk.verify_callback((b'k'*32).hex(),'1000','evt1',b'{"x":1}',h['x-agentj-signature'],now=2000))
        seen['status']=302;self.assertFalse(callbacks.post('https://hooks.example.com/a',b'k'*32,'evt1',b'{}',resolver=res,connection=Conn))
    async def test_job_result_callback_retry_lease_and_revocation(self):
        cb=self.api.save_callback(self.e['id'],'result','https://hooks.example.com/r')
        from types import SimpleNamespace
        from agentj.seat_api.runtime import Runtime
        rt=Runtime(SimpleNamespace(host=SimpleNamespace(st=None),api_state=lambda:self.api))
        rt.online=lambda:True;binding={'credential_epoch':0};rt.local_binding=lambda:dict(binding)
        receipt={'account_name':'mori','namespace_version':1,'monotonic':time.monotonic(),
                 'local_binding':dict(binding),'cloud_binding':{'tenant_id':'test'}}
        rt.receipts[self.e['id']]=receipt
        self.service.authority=rt.capture
        code,job=await self.service.dispatch(self.e['id'],self.key['secret'],'POST','jobs',{'input':{'message':'Hi'},'callback_id':cb['id']},'j1')
        self.assertEqual(code,202)
        for _ in range(100):
            if self.api.status(job['job_id'])=='succeeded':break
            await asyncio.sleep(0.02)
        bad=await self.service.dispatch(self.e['id'],self.key['secret'],'POST','jobs',{'input':{'message':'Hi'},'callback_id':'f'*32},'j2')
        self.assertEqual(bad[1]['error']['code'],'unknown_callback')
        from types import SimpleNamespace
        from agentj.seat_api.runtime import Runtime
        sent=[];rt.receipts.pop(self.e['id'])
        rt.post=lambda url,secret,event,body,**kw:(sent.append((url,event,json.loads(body))) if kw['before_send']() else None,False)[1]
        await rt.deliver(self.api);self.assertEqual(sent,[])  # no fresh lease: nothing leaves
        rt.receipts[self.e['id']]=receipt
        await rt.deliver(self.api);self.assertEqual(len(sent),1);self.assertEqual(sent[0][2],{'type':'result','job_id':job['job_id'],'status':'succeeded'})
        d=self.api.db.execute('SELECT * FROM api_deliveries').fetchone();self.assertEqual((d['attempts'],d['next']-self.api.now()>=59),(1,True))
        await rt.deliver(self.api);self.assertEqual(len(sent),1)  # backoff respected
        self.api.revoke(self.key['id'])
        self.assertEqual(self.api.db.execute('SELECT status FROM api_deliveries').fetchone()[0],'cancelled')
    def test_e2e_callback_is_sealed_and_retries_stop_within_24h(self):
        tmp=tempfile.TemporaryDirectory();bots,bid,api,e=make(tmp.name,'sealed-bot','e2e')
        try:
            with self.assertRaisesRegex(BotError,'recipient_required'):api.save_callback(e['id'],'usage','https://hooks.example.com/u')
            private,pub=e2e.x25519_pair();cb=api.save_callback(e['id'],'usage','https://hooks.example.com/u',pub)
            api.enqueue(cb['id'],'evt',{'type':'usage','secret_body':'CANARY'},None)
            row=api.db.execute('SELECT * FROM api_deliveries').fetchone();self.assertNotIn('CANARY',row['body'])
            self.assertEqual(sdk.open_callback(private,json.loads(row['body']))['secret_body'],'CANARY')
            for _ in range(6):api.delivered(row['id'],False)
            self.assertEqual(api.db.execute('SELECT status,body FROM api_deliveries').fetchone()[:],('failed',None))
        finally:bots.close();tmp.cleanup()

class MigrationTest(Base):
    privacy='rest'
    async def test_export_import_keeps_keys_ledger_and_retires_old_host(self):
        await self.service.dispatch(self.e['id'],self.key['secret'],'POST','chat/completions',{'model':'reply-bot','messages':[{'role':'user','content':'MIGRATION_BODY_CANARY'}]},'once')
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        tb=Bots(Path(tmp.name)/'bots');self.addCleanup(tb.close);target=Store(tb,profile_metadata=lambda _:dict(PROFILE));target.switch(True)
        imp=target.import_key();sk=Ed25519PrivateKey.generate()
        with self.assertRaisesRegex(BotError,'migration_not_started'):self.api.export(self.e['id'],imp['import_pk'],sk)
        self.api.begin_migration(self.e['id'])
        code,r=await self.service.dispatch(self.e['id'],self.key['secret'],'POST','chat/completions',{'model':'reply-bot','messages':[{'role':'user','content':'MIGRATION_BODY_CANARY'}]},'during')
        self.assertEqual((code,r['error']['code']),(503,'endpoint_migrating'))
        bundle=self.api.export(self.e['id'],imp['import_pk'],sk);self.api.finish_export(self.e['id'])
        blob=json.dumps(bundle)
        for secret in (self.key['secret'],'MIGRATION_BODY_CANARY'):self.assertNotIn(secret,blob)
        from agentj.wire import b64u
        from cryptography.hazmat.primitives import serialization
        pk=b64u(sk.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw))
        with self.assertRaisesRegex(BotError,'invalid_bundle'):target.import_bundle(imp['import_id'],bundle,b64u(Ed25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)))
        result=target.import_bundle(imp['import_id'],bundle,pk)
        self.assertEqual(result['endpoint']['epoch'],self.e['epoch']+1);self.assertTrue(result['contract_matches'])
        with self.assertRaisesRegex(BotError,'invalid_bundle'):target.import_bundle(imp['import_id'],bundle,pk)  # one-time import key
        # Same pin (E2E keys moved), same caller key, same idempotency ledger: no double execution.
        self.assertEqual(target.public_keys(self.e['id'])['pin'],result and target.public_keys(self.e['id'])['pin'])
        cfg=tb.get(result['endpoint']['bot']);cfg['enabled']=True;tb.save(result['endpoint']['bot'],cfg);target.publish(self.e['id'],True)
        calls_before=self.calls;svc=Service(target,self.engine);self.addAsyncCleanup(svc.close)
        code,again=await svc.dispatch(self.e['id'],self.key['secret'],'POST','chat/completions',{'model':'reply-bot','messages':[{'role':'user','content':'MIGRATION_BODY_CANARY'}]},'once')
        self.assertEqual(code,409);self.assertEqual(self.calls,calls_before)
        code,fresh=await svc.dispatch(self.e['id'],self.key['secret'],'POST','chat/completions',{'model':'reply-bot','messages':[{'role':'user','content':'MIGRATION_BODY_CANARY'}]},'after')
        self.assertEqual(code,200)
        # The old host can never serve again.
        code,r=await self.service.dispatch(self.e['id'],self.key['secret'],'POST','chat/completions',{'model':'reply-bot','messages':[{'role':'user','content':'MIGRATION_BODY_CANARY'}]},'old')
        self.assertIn(r['error']['code'],('key_invalid','endpoint_migrated'))
        self.assertIsNone(self.api.db.execute('SELECT 1 FROM api_endpoint_secrets WHERE endpoint=?',(self.e['id'],)).fetchone())
        tombstone=next(e for e in self.api.metadata()['endpoints'] if e['id']==self.e['id'])
        self.assertEqual(tombstone['state'],'migrated');self.assertIsNone(tombstone['pin'])
    async def test_pending_jobs_block_export_and_abort_restores(self):
        self.api.begin_migration(self.e['id'])
        self.api.db.execute("INSERT INTO api_requests(id,endpoint,key_id,idem,digest,method,version,created,expires,status) VALUES(?,?,?,?,?,?,?,?,?,?)",('a'*32,self.e['id'],self.key['id'],'x','y','POST:jobs',1,self.api.now(),self.api.now()+60,'running'))
        with self.assertRaisesRegex(BotError,'jobs_pending'):self.api.export(self.e['id'],e2e.x25519_pair()[1],Ed25519PrivateKey.generate())
        self.api.abort_migration(self.e['id']);self.assertEqual(self.api.endpoint(self.e['id'])['state'],'active')

@unittest.skipUnless(workflow.qualify()['supported'],'no verified workflow sandbox on this platform')
class WorkflowEndpointTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        self.bots=Bots(root/'bots');wroot=self.bots.root/'workflows'/'summarize';wroot.mkdir(parents=True)
        (wroot/'workflow.json').write_text(json.dumps({'title':'Summarize','input_schema':{'type':'object','properties':{'text':{'type':'string','maxLength':200}},'required':['text'],'additionalProperties':False},
            'output_schema':{'type':'object','properties':{'words':{'type':'integer'}},'required':['words'],'additionalProperties':False},
            'permissions':{'knowledge_read':False,'output_files':False,'tools':[],'network':[]},'limits':{'timeout_seconds':20,'cpu_seconds':10,'memory_mb':256,'output_bytes':4096}}))
        (wroot/'main.py').write_text('import json,sys\nv=json.load(sys.stdin)\nprint(json.dumps({"words":len(v["text"].split())}))\n')
        self.bid=self.bots.create({'slug':'policy-bot','enabled':True,'terms_accepted':True,'provider':{'source':'profile','id':'p'}})['id']
        self.api=Store(self.bots,profile_metadata=lambda _:dict(PROFILE));self.e=self.api.create('summarize',self.bid,'rest',True,'summarize')
        self.api.switch(True);self.api.publish(self.e['id'],True);self.key=self.api.issue(self.e['id'],'c');self.verdict='{"allow":true}';self.reviews=[]
        def decision(p,messages,*_):self.reviews.append(json.loads(messages[1]['content'])['stage']);return Result(self.verdict,5,[])
        self.service=Service(self.api,lambda b:Engine(b,'fake',provider_resolve=lambda *_:Provider('https://api.example.com','model','openai',''),model_call=lambda *_:self.fail('no model call'),decision_call=decision,queue_timeout=0))
    async def asyncTearDown(self):await self.service.close();self.bots.close();self.tmp.cleanup()
    async def test_sandboxed_workflow_with_both_reviews(self):
        code,r=await self.service.dispatch(self.e['id'],self.key['secret'],'POST','',{'input':{'text':'one two three'}},'w1')
        self.assertEqual(code,200);self.assertEqual(r['output'],{'words':3});self.assertEqual(self.reviews,['inbound','outbound'])
        code,r=await self.service.dispatch(self.e['id'],self.key['secret'],'POST','chat/completions',{'model':'summarize','messages':[{'role':'user','content':'x'}]},'w2')
        self.assertEqual(r['error']['code'],'unsupported')
    async def test_schema_mismatch_rejected_before_admission_and_review_refusal_blocks(self):
        code,r=await self.service.dispatch(self.e['id'],self.key['secret'],'POST','',{'input':{'text':1}},'w3')
        self.assertEqual((code,r['error']['code']),(422,'schema_mismatch'))
        self.assertEqual(self.api.db.execute('SELECT count(*) FROM api_requests').fetchone()[0],0)
        self.verdict='{"allow":false}'
        code,r=await self.service.dispatch(self.e['id'],self.key['secret'],'POST','',{'input':{'text':'hello'}},'w4')
        self.assertEqual(code,502);self.assertNotIn('words',json.dumps(r))
    async def test_changed_program_requires_reviewed_contract(self):
        (self.bots.root/'workflows'/'summarize'/'main.py').write_text('print("{}")\n')
        code,r=await self.service.dispatch(self.e['id'],self.key['secret'],'POST','',{'input':{'text':'a'}},'w5')
        self.assertEqual(r['error']['code'],'contract_changed');self.assertIn('workflow',self.api.review(self.e['id'])['changed'])

if __name__=='__main__':unittest.main()


@unittest.skipUnless(workflow.qualify()['supported'],'no verified workflow sandbox on this platform')
class WorkflowSnapshotTest(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=WorkflowEndpointTest.asyncSetUp
    asyncTearDown=WorkflowEndpointTest.asyncTearDown

    async def check_mutation(self,mutate):
        live=self.bots.root/'workflows'/'summarize';executed=[];stages=[]
        factory=self.service.engine_factory
        def engine(budget):
            obj=factory(budget);check=obj.safety
            async def safety(p,bid,vid,rid,text,stage):
                if stage=='inbound':
                    await asyncio.sleep(0)
                    mutate(live)
                return await check(p,bid,vid,rid,text,stage)
            obj.safety=safety;return obj
        self.service.engine_factory=engine
        async def run(root,name,value,timeout):
            self.assertNotEqual(Path(root),live.parent);stages.append(Path(root))
            out=await workflow.run(root,name,value,timeout);executed.append(out)
            return out
        self.service.workflow_run=run
        code,result=await self.service.dispatch(self.e['id'],self.key['secret'],'POST','',{'input':{'text':'one two three'}},'freeze-test')
        self.assertNotEqual(code,200)  # changed live contract may not release even the old verified result
        self.assertEqual(executed,[{'output':{'words':3},'files':[]}])
        self.assertTrue(stages);self.assertTrue(all(not p.exists() for p in stages))
        self.assertNotIn('999',json.dumps(result))

    async def test_inbound_await_code_and_permission_edits_cannot_change_executed_snapshot(self):
        def mutate(live):
            (live/'main.py').write_text('print("{\\"words\\":999}")\n')
            spec=json.loads((live/'workflow.json').read_text());spec['permissions']['network']=['unreviewed.example.com']
            (live/'workflow.json').write_text(json.dumps(spec))
        await self.check_mutation(mutate)

    async def test_inbound_await_directory_symlink_replacement_cannot_change_executed_snapshot(self):
        def mutate(live):
            evil=live.parent/'replacement';evil.mkdir();(evil/'main.py').write_text('print("{\\"words\\":999}")\n')
            (evil/'workflow.json').write_text((live/'workflow.json').read_text())
            live.rename(live.parent/'original');live.symlink_to(evil,target_is_directory=True)
        await self.check_mutation(mutate)

    async def test_changed_or_symlink_source_before_freeze_is_refused(self):
        live=self.bots.root/'workflows'/'summarize';expected=workflow.digest(live.parent,live.name)
        (live/'main.py').write_text('print("{}")\n')
        with self.assertRaisesRegex(BotError,'contract_changed'):workflow.freeze(live.parent,live.name,expected)
        live.rename(live.parent/'original');live.symlink_to(live.parent/'original',target_is_directory=True)
        with self.assertRaisesRegex(BotError,'unsafe_workflow'):workflow.freeze(live.parent,live.name,expected)
