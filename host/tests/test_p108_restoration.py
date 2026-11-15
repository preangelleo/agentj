"""Current signed-owner, captured-authority and real-loopback seat API regressions."""
import _hermetic
import asyncio
import json
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from agentj import provenance
from agentj.bots.store import BotError
from agentj.seat_api.runtime import Runtime
from agentj.seat_api import e2e
import test_p108 as base
import test_p82_owner as owner_base


class Authority(unittest.IsolatedAsyncioTestCase):
    setUp=base.ApiTest.setUp
    asyncTearDown=base.ApiTest.asyncTearDown
    body=base.ApiTest.body
    call=base.ApiTest.call

    def runtime(self):
        rt=Runtime(SimpleNamespace(host=SimpleNamespace(st=None),api_state=lambda:self.api))
        self.binding={'credential_epoch':0,'tenant':'original'}
        rt.online=lambda:True;rt.local_binding=lambda:dict(self.binding)
        rt.receipts[self.e['id']]={'account_name':'sample','namespace_version':1,'monotonic':time.monotonic(),
              'local_binding':dict(self.binding),'cloud_binding':{'host_id':'original','bound_at':1}}
        self.service.authority=rt.capture
        return rt

    async def test_expired_lease_refuses_admission_without_model(self):
        rt=self.runtime();rt.receipts[self.e['id']]['monotonic']-=61
        self.assertEqual((await self.call())[0],503)
        self.assertEqual(self.calls,0)
        self.assertEqual(self.api.db.execute('SELECT count(*) FROM api_requests').fetchone()[0],0)

    async def test_epoch_or_binding_change_during_generation_never_releases_output(self):
        for field,value in [('credential_epoch',1),('tenant','another')]:
            with self.subTest(field=field):
                rt=self.runtime();started=asyncio.Event();release=asyncio.Event()
                class Pending:
                    async def reply(inner,*args):
                        started.set();await release.wait();return {'status':'done','text':'PRIVATE_RESULT_CANARY'}
                self.service.engine_factory=lambda _:Pending()
                task=asyncio.create_task(self.call(idem=field));await started.wait()
                self.binding[field]=value;release.set();code,result=await task
                self.assertEqual(code,503);self.assertNotIn('PRIVATE_RESULT_CANARY',json.dumps(result))
                self.assertIsNone(rt.capture(self.e['id']))

    async def test_completed_result_keeps_original_capture_after_new_binding(self):
        rt=self.runtime();self.assertEqual((await self.call())[0],200)
        self.binding['credential_epoch']=2
        rt.receipts[self.e['id']]['local_binding']=dict(self.binding)
        rt.receipts[self.e['id']]['cloud_binding']['bound_at']=2
        code,result=await self.call();self.assertEqual(code,503)
        self.assertNotIn('API reply',json.dumps(result));self.assertEqual(self.calls,1)

    async def test_callback_checks_revocation_at_actual_send_boundary(self):
        rt=self.runtime()
        cb=self.api.save_callback(self.e['id'],'result','https://hooks.example.com/result')
        code,job=await self.call(body={'input':{'message':'Hi'},'callback_id':cb['id']},path='jobs')
        self.assertEqual(code,202)
        await asyncio.gather(*self.service.jobs.values())
        row=self.api.due()[0];capture=rt.capture(self.e['id'])
        self.assertTrue(rt.callback_live(self.api,row,capture))
        self.api.revoke(self.key['id'])
        self.assertFalse(rt.callback_live(self.api,row,capture))

    async def test_real_loopback_sdk_e2e_and_no_plaintext_fallback(self):
        import importlib.util
        from pathlib import Path
        spec=importlib.util.spec_from_file_location('seat_sdk',Path(__file__).resolve().parents[2]/'sdk/python/agentj_seat_api.py')
        sdk=importlib.util.module_from_spec(spec);spec.loader.exec_module(sdk)
        endpoint=self.api.create('encrypted-loopback',self.bid)
        self.api.publish(endpoint['id'],True);key=self.api.issue(endpoint['id'],'Loopback customer')
        manifest=e2e.manifest(self.api.secret(endpoint['id']),endpoint,self.api.now())
        m=sdk.verify_manifest(manifest,manifest['pin'])
        inner={'key':key['secret'],'method':'POST','path':'chat/completions','body':{'model':endpoint['public_id'],'messages':[{'role':'user','content':'PLAINTEXT_API_BODY_CANARY_must_not_appear_in_gateway_bytes'}]},'idem':'loopback'}
        env,response=sdk.seal(m,key['id'],inner);received=[]
        async def handle(reader,writer):
            try:
                head=await reader.readuntil(b'\r\n\r\n')
                size=int(next(x.split(b':',1)[1] for x in head.split(b'\r\n') if x.lower().startswith(b'content-length:')))
                raw=await reader.readexactly(size);received.append(raw)
                status,result=await self.service.dispatch(endpoint['id'],None,'POST','_e2e',json.loads(raw))
                payload=json.dumps(result).encode()
                writer.write(b'HTTP/1.1 '+str(status).encode()+b' OK\r\nContent-Length: '+str(len(payload)).encode()+b'\r\nConnection: close\r\n\r\n'+payload);await writer.drain()
            finally:writer.close();await writer.wait_closed()
        server=await asyncio.start_server(handle,'127.0.0.1',0)
        try:
            reader,writer=await asyncio.open_connection('127.0.0.1',server.sockets[0].getsockname()[1])
            raw=json.dumps(env).encode();writer.write(b'POST /_e2e HTTP/1.1\r\nContent-Length: '+str(len(raw)).encode()+b'\r\n\r\n'+raw);await writer.drain()
            reply=await reader.read();writer.close();await writer.wait_closed()
            status,result=sdk.open_reply(response,env['request_id'],json.loads(reply.split(b'\r\n\r\n',1)[1]))
            self.assertEqual(status,200);self.assertEqual(result['choices'][0]['message']['content'],'API reply')
            self.assertNotIn(key['secret'].encode(),received[0]);self.assertNotIn(b'PLAINTEXT_API_BODY_CANARY_must_not_appear_in_gateway_bytes',received[0])
            code,_=await self.service.dispatch(endpoint['id'],key['secret'],'POST','chat/completions',inner['body'],'plain')
            self.assertEqual(code,403)
        finally:server.close();await server.wait_closed()


class Owner(unittest.IsolatedAsyncioTestCase):
    setUp=owner_base.OwnerTest.setUp
    asyncTearDown=owner_base.OwnerTest.asyncTearDown
    message=owner_base.OwnerTest.message

    def live(self):
        self.host._paired_live=lambda s:s is self.session and s.state=='ready' and self.host.st.is_allowed(s.pub)
        self.manager.api_runtime=Runtime(self.manager)
        self.manager.api_runtime.local_binding=lambda:{'host':'test','credential_epoch':0}
        from agentj.bots.visitor import public
        self.host.ask=AsyncMock(return_value={'behavior':'allow','grant_info':{'uv':True,'device':self.session.device,'sign_pub':public(self.key)}})

    async def test_unsigned_json_cannot_upgrade_to_owner_even_with_source_label(self):
        self.live()
        with self.assertRaisesRegex(BotError,'signed_owner_required'):
            await self.manager.write({'op':'api_switch','enabled':True,'source':'phone'})
        await self.manager.phone(self.session,{**self.message({'op':'api_switch','enabled':True}),'sig':'bad'})
        self.assertFalse(self.sent[-1]['ok']);self.host.ask.assert_not_awaited()
        self.assertFalse(self.manager.api_state().enabled())

    async def test_signed_frame_then_device_bound_uv_and_replay_rejection(self):
        self.live();req={'op':'api_switch','enabled':True};msg=self.message(req)
        await self.manager.phone(self.session,msg)
        self.assertTrue(self.sent[-1]['ok']);self.assertTrue(self.manager.api_state().enabled())
        self.assertEqual(self.host.ask.await_args.args[0],'agentj api manage')
        self.assertEqual(self.host.ask.await_args.kwargs['only'],self.session.device)
        await self.manager.phone(self.session,msg);self.assertEqual(self.sent[-1]['why'],'replay')

    async def test_lost_session_or_wrong_uv_signer_never_mutates(self):
        self.live()
        async def lost(*a,**kw):self.session.state='gone';return {'behavior':'allow','grant_info':{'uv':True,'device':'owner'}}
        self.host.ask.side_effect=lost
        await self.manager.phone(self.session,self.message({'op':'api_switch','enabled':True}))
        self.assertFalse(self.manager.api_state().enabled())
        self.session.state='ready';self.host.ask.side_effect=None
        self.host.ask.return_value={'behavior':'allow','grant_info':{'uv':False,'device':'owner'}}
        await self.manager.phone(self.session,self.message({'op':'api_switch','enabled':True}))
        self.assertFalse(self.manager.api_state().enabled());self.assertEqual(self.sent[-1]['why'],'owner_uv_required')

    async def test_key_card_bound_to_signer_and_revocation_after_disconnect(self):
        self.live()
        bid=self.manager.state().create({'slug':'key-card-test','enabled':True,'terms_accepted':True,
             'provider':{'source':'profile','id':'sample-profile'}})['id']
        api=self.manager.api_state();api.profile_metadata=lambda _:{'base_url':'https://api.example.com','model':'sample','api':'openai'}
        api.switch(True);endpoint=api.create('card-bound-api',bid);api.publish(endpoint['id'],True)
        async def send(card):
            self.assertEqual(card['only'],self.session.device)
            self.assertTrue(card['value'].startswith('ajapi_'))
            self.session.state='gone'
            return {'result':'sent','id':'card-id'}
        self.host.elevate=SimpleNamespace(out=SimpleNamespace(send=AsyncMock(side_effect=send)))
        await self.manager.phone(self.session,self.message({'op':'api_issue','id':endpoint['id'],'label':'backend'}))
        self.assertFalse(self.sent[-1]['ok'])
        self.assertEqual(api.db.execute('SELECT revoked FROM api_keys').fetchone()[0],1)
        self.assertNotIn('ajapi_',str(self.sent));self.assertNotIn('ajapi_',''.join(self.logs))


class ActualOwnerUV(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import test_uv_list as uv
        await uv.ListApproval.asyncSetUp(self)
        self.s.p33=True  # real negotiated structured-frame capability
        from agentj.bots.owner import Manager
        self.manager=Manager(self.h)
        self.manager.api_runtime=Runtime(self.manager)
        self.manager.api_runtime.local_binding=lambda:{'host':'loopback-test','credential_epoch':0}

    async def asyncTearDown(self):
        if self.manager.store:self.manager.store.close()

    async def test_actual_signed_frame_and_real_webauthn_required_before_switch(self):
        import os
        from agentj import controls, wire, approvals
        req={'op':'api_switch','enabled':True};nonce=os.urandom(16).hex();ts=int(time.time()*1000)
        sig=self.ph.sk.sign(controls.signed_message(self.h.channel,self.s.device,'bots_write',nonce,ts,
                                                    controls.object_digest('bots_write',{'request':req})))
        msg={'t':'bots_write','r':'api-switch','request':req,'n':nonce,'ts':ts,'sig':wire.b64u(sig)}
        task=asyncio.create_task(self.manager.phone(self.s,msg))
        try:
            for _ in range(200):
                if self.h.asks or task.done():break
                await asyncio.sleep(.005)
            self.assertFalse(task.done(),str(task.result()) if task.done() else '')
            ask=next(iter(self.h.asks.values()));self.assertTrue(ask.require_passkey)
            self.assertEqual(ask.only,self.s.device)
            self.assertFalse(self.manager.api_state().enabled())
            card=self.h._ask_msg(ask,self.s);answer=self.ph.answer(card,True)
            await self.h._answer(self.s,{k:v for k,v in answer.items() if k!='fa'})
            self.assertFalse(task.done());self.assertFalse(self.manager.api_state().enabled())
            await self.h._answer(self.s,answer)
            await asyncio.wait_for(task,3)
            self.assertTrue(self.manager.api_state().enabled())
            recs=[r for r in approvals.read_log(self.st) if r.get('tool')=='agentj api manage']
            self.assertEqual(recs[-1]['decision'],'allow');self.assertEqual(approvals.check_record(self.st,recs[-1]),'ok')
        finally:
            if not task.done():task.cancel()
            await asyncio.gather(task,return_exceptions=True)


class SDKBinding(unittest.TestCase):
    def setUp(self):
        import importlib.util
        from pathlib import Path
        spec=importlib.util.spec_from_file_location('binding_sdk',Path(__file__).resolve().parents[2]/'sdk/python/agentj_seat_api.py')
        self.sdk=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.sdk)
        self.keys=e2e.new_keys();self.endpoint={'id':'a'*32,'public_id':'first-endpoint','epoch':2,'version':1,'privacy':'e2e'}
        self.doc=e2e.manifest(self.keys,self.endpoint,int(time.time()));self.posts=[]

    def transport(self,method,url,body):
        if method=='POST':self.posts.append(body);raise AssertionError('credential must not be posted')
        return 200,self.doc

    def test_valid_pin_wrong_caller_or_provisioner_endpoint_never_posts(self):
        other_keys=e2e.new_keys();other=e2e.manifest(other_keys,{**self.endpoint,'id':'b'*32,'public_id':'second-endpoint'},int(time.time()))
        self.assertNotEqual(other['pin'],self.doc['pin'])
        self.sdk.verify_manifest(other,other['pin'])
        for kind in ('ajapi','ajprov'):
            key=kind+'_'+'b'*32+'_'+'c'*32+'_'+'D'*43
            client=self.sdk.Client('sample','first-endpoint',self.doc['pin'],key,transport=self.transport)
            with self.assertRaisesRegex(self.sdk.SeatApiError,'key_scope'):
                if kind=='ajapi':client.call({'input':{'message':'test'}},'test')
                else:client.provision('list')
            self.assertEqual(self.posts,[])
            with self.assertRaisesRegex(self.sdk.SeatApiError,'key_scope'):
                self.sdk.seal(self.doc['manifest'],'c'*32,{'key' if kind=='ajapi' else 'cap':key})

    def test_signed_manifest_rejects_invalid_epoch_privacy_and_time_types(self):
        import copy
        cases=[('epoch',True),('epoch',0),('epoch',-1),('epoch',2**53),('epoch','2'),
               ('contract',False),('privacy','public'),('privacy',None),('issued',False),('expires',True),
               ('expires',int(time.time())+90000)]
        for field,value in cases:
            with self.subTest(field=field,value=value):
                doc=copy.deepcopy(self.doc);doc['manifest'][field]=value
                doc['sig']=e2e.sign(self.keys,'agentjarvis/seat-api-manifest-v1',doc['manifest'])
                self.doc_saved=self.doc;self.doc=doc
                try:
                    client=self.sdk.Client('sample','first-endpoint',doc['pin'],'ajapi_'+'a'*32+'_'+'c'*32+'_'+'D'*43,transport=self.transport)
                    with self.assertRaisesRegex(self.sdk.SeatApiError,'manifest_invalid'):
                        client.call({'input':{'message':'test'}},'test')
                    self.assertEqual(self.posts,[])
                finally:self.doc=self.doc_saved

    def test_signed_lower_route_epoch_never_replaces_current_manifest(self):
        client=self.sdk.Client('sample','first-endpoint',self.doc['pin'],'ajapi_'+'a'*32+'_'+'c'*32+'_'+'D'*43,transport=self.transport)
        client.refresh();self.doc=e2e.manifest(self.keys,{**self.endpoint,'epoch':1},int(time.time()))
        with self.assertRaisesRegex(self.sdk.SeatApiError,'route_epoch'):client.refresh()
        self.assertEqual(client.manifest['epoch'],2);self.assertEqual(self.posts,[])


class CredentialLinks(unittest.TestCase):
    def test_api_and_callback_secrets_redact_in_url_path_query_and_plain_text(self):
        from agentj import privacy
        samples=['ajapi_'+'a'*32+'_'+'b'*32+'_'+'C'*43,
                 'ajprov_'+'a'*32+'_'+'b'*32+'_'+'C'*43,
                 'ajwhsec_'+'a'*64]
        for secret in samples:
            for text in (secret,'https://backend.example/?key='+secret,'https://backend.example/'+secret):
                with self.subTest(kind=secret.split('_',1)[0],is_url=text.startswith('https')):
                    self.assertNotIn(secret,privacy.owner_text(text))
                    self.assertNotIn(secret,privacy.redact(text))
                    self.assertTrue(any(pattern.search(text) for _,pattern,_ in privacy._SECRETS))


class NoFallback(unittest.TestCase):
    setUp=SDKBinding.setUp
    def test_unauthenticated_success_or_redirect_is_never_a_business_result(self):
        for status in (201,202,204,301,302):
            with self.subTest(status=status):
                def transport(method,url,body):
                    return (200,self.doc) if method=='GET' else (status,{'output':'UNAUTHENTICATED_RESULT_CANARY'})
                client=self.sdk.Client('sample','first-endpoint',self.doc['pin'],'ajapi_'+'a'*32+'_'+'c'*32+'_'+'D'*43,transport=transport)
                with self.assertRaisesRegex(self.sdk.SeatApiError,'invalid_envelope'):client.call({'input':{'message':'test'}},'test')
    def test_plain_error_only_returns_bounded_error_metadata(self):
        def transport(method,url,body):
            return (200,self.doc) if method=='GET' else (503,{'error':{'code':'offline'},'private':'UNAUTHENTICATED_RESULT_CANARY'})
        client=self.sdk.Client('sample','first-endpoint',self.doc['pin'],'ajapi_'+'a'*32+'_'+'c'*32+'_'+'D'*43,transport=transport)
        self.assertEqual(client.call({'input':{'message':'test'}},'test'),(503,{'error':{'code':'offline'}}))


class ManagementInputs(unittest.TestCase):
    def test_unknown_fields_or_credential_metadata_rejected_before_card(self):
        from agentj.seat_api.admin import validate_request
        secret='ajapi_'+'a'*32+'_'+'b'*32+'_'+'C'*43
        for req in ({'op':'api_switch','enabled':True,'source':'phone'},
                    {'op':'api_issue','label':secret},
                    {'op':'api_handoff','purpose':'https://backend.example/?key='+secret},
                    {'op':'api_callback_save','url':'https://backend.example/'+secret}):
            with self.assertRaises(BotError):validate_request(req)


class WorkflowAndMediaCredentials(unittest.TestCase):
    def test_api_capabilities_are_refused_without_labels_or_entropy(self):
        from agentj import taskspec, media
        secrets = [
            ("seat_api_key", "ajapi_" + "a" * 32 + "_" + "b" * 32 + "_" + "C" * 43),
            ("seat_api_key", "ajprov_" + "a" * 32 + "_" + "b" * 32 + "_" + "C" * 43),
            ("seat_api_callback", "ajwhsec_" + "a" * 64),
        ]
        for kind, secret in secrets:
            with self.subTest(kind=kind, prefix=secret.split("_")[0]):
                text = "notes\n" + secret
                self.assertTrue(taskspec.secret_like(text))
                location = taskspec.secret_where(text)
                self.assertEqual((location["kind"], location["line"]), (kind, 2))
                self.assertNotIn(secret, json.dumps(location))
                self.assertIn("needs looks like it holds a credential: name the service only",
                              taskspec.problems({"needs": [secret]}))
                for owner in (False, True):
                    self.assertTrue(media._secret(text, html=False, owner=owner))
        self.assertFalse(taskspec.secret_like("API endpoint documentation; credentials stay on your backend."))
