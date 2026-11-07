"""Host-owned bounded executor with synthetic registry, files and transport. No live credentials/network."""
import _hermetic
import asyncio
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from agentj import bounded_plan as bp, approval_review, approvals, secret_xfer
from agentj.peer_service import PeerService
import test_p541_approval_review as fixtures


class Bounded(unittest.IsolatedAsyncioTestCase):
    stop=fixtures.Review.stop
    intent=fixtures.Review.intent
    async def asyncSetUp(self):
        await fixtures.Review.asyncSetUp(self)
        self.path=pathlib.Path(self.tmp.name)
        self.credential=self.path/'managed.key'
        self.credential.write_bytes(b'Q8x7N2p9F4z6V3s5W0k1L8m2T7u9R4c6')
        os.chmod(self.credential,0o600)
        patch=mock.patch.object(secret_xfer,'registry_dest',side_effect=lambda st,name: {'mode':'file','path':str(self.credential),'key':name} if name=='SYNTHETIC_AUTH' else None)
        patch.start();self.addCleanup(patch.stop)
        self.calls=[]
        def transport(step,auth,body):
            self.calls.append((step['kind'],step['url'],bytes(auth),body))
            if step['kind']=='token_create':return 200,b'{"success":true,"result":{"value":"synthetic-temporary-value","id":"owned-token-id"}}'
            if step['kind']=='token_revoke':return 200,b'{"success":true,"result":{"id":"owned-token-id"}}'
            return 200,b'{"id":"ordinary-result","token":"never-return-this-field"}'
        self.manager=bp.Plans(self.h,resolver=lambda host:['8.8.8.8'],transport=transport)
        self.h._bounded_plan_manager=self.manager
        self.svc=PeerService.__new__(PeerService);self.svc.host,self.svc.st=self.h,self.st

    async def approve(self,steps):
        task=asyncio.create_task(self.svc.approve_actions(steps))
        for _ in range(200):
            if self.h.asks or task.done():break
            await asyncio.sleep(.005)
        self.assertFalse(task.done(),task.result() if task.done() else '')
        ask=next(iter(self.h.asks.values()))
        self.addAsyncCleanup(self.stop,task,ask)
        self.assertTrue(ask.require_passkey)
        await self.h._answer(self.s,self.ph.answer(self.h._ask_msg(ask,self.s),True))
        result=await task
        self.assertTrue(result['ok'],result)
        return result['plan'],ask

    async def execute(self,plan,step):
        return await self.svc.cli({'t':'execute-action','plan':plan,'step':step})

    async def test_authentication_queries_one_uv_each_step_once_no_response_secret(self):
        steps=[{'kind':'https_auth','credential':'secret:SYNTHETIC_AUTH','url':f'https://api.stripe.com/v1/{resource}'} for resource in ('customers','subscriptions','promotion_codes')]
        plan,ask=await self.approve(steps)
        for i in range(3):self.assertTrue((await self.execute(plan,i))['ok'])
        self.assertFalse((await self.execute(plan,0))['ok'])
        self.assertEqual(len(self.calls),3)
        visible=json.dumps(self.sent,ensure_ascii=False,default=str)
        self.assertNotIn('Q8x7N2p9F4z6V3s5W0k1L8m2T7u9R4c6',visible)
        self.assertNotIn('never-return-this-field',visible)
        self.assertIn('sensitive_storage',ask.envelope['effects'])

    async def test_credential_endpoint_epoch_and_replay_are_rejected(self):
        step={'kind':'https_auth','credential':'secret:SYNTHETIC_AUTH','url':'https://api.stripe.com/v1/customers'}
        plan,_=await self.approve([step])
        self.credential.write_bytes(b'a-changed-authentication-value')
        self.assertFalse((await self.execute(plan,0))['ok'])
        self.assertEqual(self.calls,[])
        self.assertFalse((await self.execute(plan,0))['ok'])
        self.credential.write_bytes(b'Q8x7N2p9F4z6V3s5W0k1L8m2T7u9R4c6')
        plan2,_=await self.approve([{**step,"url":step["url"]+"?limit=2"}])
        self.manager.resolver=lambda host:['1.1.1.1']
        self.assertFalse((await self.execute(plan2,0))['ok'])
        self.assertEqual(self.calls,[])

    async def test_backup_migration_cleanup_preserve_exact_bytes(self):
        source=self.path/'source';source.mkdir();(source/'one').write_text('one');sub=source/'sub';sub.mkdir();(sub/'two').write_text('two')
        backup=self.path/'backup';moved=self.path/'moved'
        plan,_=await self.approve([{'kind':'backup','source':str(source),'destination':str(backup)},
                                  {'kind':'migrate','source':str(source),'destination':str(moved)}])
        self.assertTrue((await self.execute(plan,0))['ok'])
        self.assertEqual((backup/'sub/two').read_text(),'two')
        result=await self.execute(plan,1)
        self.assertTrue(result['ok'],result)
        self.assertFalse(source.exists());self.assertEqual((moved/'one').read_text(),'one')
        recovery=self.path/'recovered'
        plan2,_=await self.approve([{'kind':'clean','source':str(moved),'destination':str(recovery)}])
        self.assertTrue((await self.execute(plan2,0))['ok'])
        self.assertFalse(moved.exists());self.assertEqual((recovery/'sub/two').read_text(),'two')

    async def test_changed_tree_parent_symlink_and_new_destination_do_not_execute(self):
        source=self.path/'source';source.mkdir();(source/'one').write_text('one')
        dest=self.path/'dest'
        plan,_=await self.approve([{'kind':'backup','source':str(source),'destination':str(dest)}])
        (source/'new').write_text('new')
        self.assertFalse((await self.execute(plan,0))['ok']);self.assertFalse(dest.exists())
        (source/'new').unlink()
        dest=self.path/'dest-second'
        plan2,_=await self.approve([{'kind':'backup','source':str(source),'destination':str(dest)}])
        dest.write_text('user-data')
        self.assertFalse((await self.execute(plan2,0))['ok']);self.assertEqual(dest.read_text(),'user-data')

    async def test_expired_plan_safe_renewal_new_request_once_and_denial_terminal(self):
        steps=[{'kind':'https_auth','credential':'secret:SYNTHETIC_AUTH','url':'https://api.stripe.com/v1/customers?test=renew'}]
        self.h.ask_ttl=.05
        task=asyncio.create_task(self.svc.approve_actions(steps))
        for _ in range(100):
            if self.h.asks:break
            await asyncio.sleep(.001)
        old=next(iter(self.h.asks.values()))
        result=await task
        self.assertFalse(result['ok'])
        self.assertEqual(old.review_state,'expired')
        self.assertFalse((await self.svc.approve_actions(steps))['ok'])
        await approval_review.on_phone(self.h,self.s,self.intent(old,'renew'))
        self.agent.cur_send=self.agent.submit.call_args.args[0]
        self.h.ask_ttl=5
        plan,fresh=await self.approve(steps)
        self.assertNotEqual(plan,old.rid)
        self.assertEqual(fresh.bounded_plan.renewed_from,old.rid)
        self.assertFalse((await self.execute(old.rid,0))['ok'])
        self.assertTrue((await self.execute(plan,0))['ok'])
        self.assertFalse((await self.svc.approve_actions(steps))['ok'])
        self.assertFalse((await self.execute(plan,0))['ok'])
        self.assertEqual(len(self.calls),1)

    async def test_concurrent_identical_plan_joins_one_signed_operation(self):
        steps=[{'kind':'https_auth','credential':'secret:SYNTHETIC_AUTH','url':'https://api.stripe.com/v1/customers?test=concurrent'}]
        first=asyncio.create_task(self.svc.approve_actions(steps))
        second=asyncio.create_task(self.svc.approve_actions(steps))
        for _ in range(100):
            if self.h.asks:break
            await asyncio.sleep(.005)
        await asyncio.sleep(.02)
        self.assertEqual(len(self.h.asks),1)
        ask=next(iter(self.h.asks.values()));self.addAsyncCleanup(self.stop,first,ask);self.addAsyncCleanup(self.stop,second,ask)
        await self.h._answer(self.s,self.ph.answer(self.h._ask_msg(ask,self.s),True))
        a,b=await asyncio.gather(first,second)
        self.assertTrue(a['ok']);self.assertEqual(a['plan'],b['plan'])
        self.assertEqual(len(self.manager.plans),1)
        self.assertTrue((await self.execute(a['plan'],0))['ok'])
        self.assertFalse((await self.execute(b['plan'],0))['ok'])
        self.assertEqual(len(self.calls),1)

    async def test_destination_parent_swap_during_fd_open_cannot_write(self):
        source=self.path/'source-file';source.write_text('approved-data')
        parent=self.path/'destination-parent';parent.mkdir()
        destination=parent/'output'
        plan,_=await self.approve([{'kind':'backup','source':str(source),'destination':str(destination)}])
        original=bp._parent_fd
        def swap(path,seal=None):
            if path==str(destination):
                parent.rename(self.path/'approved-parent-moved')
                parent.mkdir()
            return original(path,seal)
        with mock.patch.object(bp,'_parent_fd',side_effect=swap):
            result=await self.execute(plan,0)
        self.assertFalse(result['ok'],result)
        self.assertFalse(destination.exists())
        self.assertFalse((self.path/'approved-parent-moved'/'output').exists())

    async def test_unknown_authentication_destination_and_secret_upload_require_safe_boundary(self):
        step={'kind':'https_auth','credential':'secret:SYNTHETIC_AUTH','url':'https://receiver.example.invalid/collect'}
        sealed=await asyncio.to_thread(self.manager.seal,[step])
        self.assertIn('sensitive_delivery',sealed.steps[0]['effects'])
        self.assertTrue(sealed.steps[0]['individual_uv'])
        file=self.path/'ordinary-report.txt'
        file.write_text('API_KEY='+'sk_live_'+os.urandom(32).hex())
        upload={**step,'method':'POST','content_type':'multipart/form-data','file':{'path':str(file),'field':'file'}}
        self.assertFalse((await self.svc.approve_actions([upload]))['ok'])
        self.assertFalse(self.h.asks)

    async def test_known_auth_value_encodings_never_enter_public_descriptor_or_upload(self):
        import base64
        value=self.credential.read_bytes()
        for spelling in (value.decode(),base64.b64encode(value).decode(),base64.urlsafe_b64encode(value).decode(),value.hex()):
            step={'kind':'https_auth','credential':'secret:SYNTHETIC_AUTH','url':'https://api.stripe.com/v1/promotion_codes','method':'POST','body':{'coupon':'fixed','code':spelling}}
            with self.assertRaises(bp.PlanError):self.manager.seal([step])
        file=self.path/'unlabelled.txt';file.write_bytes(value)
        upload={'kind':'https_auth','credential':'secret:SYNTHETIC_AUTH','url':'https://audio.example.invalid/transcribe','method':'POST','file':{'path':str(file),'field':'file'},'body':{'model':'fixed'}}
        with self.assertRaises(bp.PlanError):self.manager.seal([upload])
        self.assertFalse(self.h.asks)

    async def test_unconfirmed_revoke_retains_token_cannot_destroy_or_retry(self):
        steps=[{'kind':'token_create','credential':'secret:SYNTHETIC_AUTH','url':'https://api.cloudflare.com/client/v4/user/tokens','method':'POST','body':{'name':'temporary','policies':[]},'alias':'temp','token_field':'result.value','id_field':'result.id'},
               {'kind':'token_revoke','credential':'secret:SYNTHETIC_AUTH','url':'https://api.cloudflare.com/client/v4/user/tokens/','method':'DELETE','token_id':'temp'},
               {'kind':'token_destroy','alias':'temp'}]
        plan,_=await self.approve(steps)
        self.assertTrue((await self.execute_with_individual_uv(plan,0))['ok'])
        item=self.manager.plans[plan].token_values['temp'];before=bytes(item['value'])
        self.manager.transport=lambda *args:(200,b'{"success":true,"result":{"id":"different-token"}}')
        self.assertFalse((await self.execute_with_individual_uv(plan,1))['ok'])
        self.assertFalse(item['revoked']);self.assertEqual(bytes(item['value']),before)
        self.assertFalse((await self.execute(plan,1))['ok'])
        self.assertFalse((await self.execute(plan,2))['ok'])
        record=item['recovery_record']
        self.manager.end_all()
        self.assertFalse(any(item['value']))
        self.assertEqual(self.manager.recovery.get(record)['token_id'],'owned-token-id')

    async def test_invalid_descriptor_auth_root_and_unknown_direct_tool(self):
        for step in ({'kind':'https_auth','credential':'raw-inline-token','url':'https://api.stripe.com/v1/customers'},
                     {'kind':'https_auth','credential':'secret:SYNTHETIC_AUTH','url':'http://api.stripe.com/v1/customers'},
                     {'kind':'https_auth','credential':'secret:SYNTHETIC_AUTH','url':'https://api.stripe.com/v1/charges','method':'POST','body':{'amount':10}}):
            self.assertFalse((await self.svc.approve_actions([step]))['ok'])
        forged=await self.h.ask(bp.TOOL,{'scope':{'actions':[{'effects':['sensitive_storage']}]}},require_passkey=True)
        self.assertEqual(forged['behavior'],'deny')

    async def test_unresolved_execution_cannot_enter_or_execute_a_plan(self):
        command = 'rm -rf ~/x'
        for _ in range(9):
            command = 'agentj busy run -- bash -c ' + json.dumps(command)
        commands = (command, 'nohup ' * 20 + 'ls', 'env -S "rm -rf \'unbalanced"', "env --split-string='git push --force origin main'")
        before = len(self.h.asks)
        for command in commands:
            self.assertFalse((await self.svc.approve_actions([{'kind':'Bash','command':command}]))['ok'])
            self.assertFalse((await self.svc.approve_actions([{'kind':'inspect','credential':'secret:SYNTHETIC_AUTH','command':command}]))['ok'])
        self.assertEqual(len(self.h.asks), before)
        plan,_ = await self.approve([{'kind':'https_auth','credential':'secret:SYNTHETIC_AUTH','url':'https://api.stripe.com/v1/customers'}])
        self.manager.plans[plan].steps[0]['tool'] = 'Bash'
        self.manager.plans[plan].steps[0]['tool_input'] = {'command':commands[0]}
        with mock.patch.object(self.manager,'_execute',side_effect=AssertionError('must not execute')) as execute:
            result = await self.execute(plan,0)
        self.assertFalse(result['ok'])
        self.assertEqual(result['why'],'unresolved_execution')
        execute.assert_not_called()
        self.assertEqual(self.calls, [])

    async def execute_with_individual_uv(self,plan,index):
        task=asyncio.create_task(self.execute(plan,index))
        for _ in range(200):
            if self.h.asks or task.done():break
            await asyncio.sleep(.005)
        if not task.done():
            ask=next(iter(self.h.asks.values()))
            self.addAsyncCleanup(self.stop,task,ask)
            self.assertTrue(ask.require_passkey)
            await self.h._answer(self.s,self.ph.answer(self.h._ask_msg(ask,self.s),True))
        return await task

    async def test_temporary_token_create_use_revoke_destroy_requires_root_uv(self):
        steps=[{'kind':'token_create','credential':'secret:SYNTHETIC_AUTH','url':'https://api.cloudflare.com/client/v4/user/tokens',
                'method':'POST','body':{'name':'bounded temporary','policies':[]},'alias':'temp', 'token_field':'result.value','id_field':'result.id'},
               {'kind':'token_use','credential':'plan:temp','url':'https://api.cloudflare.com/client/v4/user/tokens/verify'},
               {'kind':'token_revoke','credential':'secret:SYNTHETIC_AUTH','url':'https://api.cloudflare.com/client/v4/user/tokens/',
                'method':'DELETE','token_id':'temp'},
               {'kind':'token_destroy','alias':'temp'}]
        plan,_=await self.approve(steps)
        self.assertTrue((await self.execute_with_individual_uv(plan,0))['ok'])
        self.assertTrue((await self.execute(plan,1))['ok'])
        self.assertTrue((await self.execute_with_individual_uv(plan,2))['ok'])
        self.assertTrue((await self.execute(plan,3))['ok'])
        self.assertEqual(self.manager.plans[plan].token_values['temp']['value'],bytearray())
        self.assertEqual([x[0] for x in self.calls],['token_create','token_use','token_revoke'])
        self.assertTrue(self.calls[-1][1].endswith('/owned-token-id'))
        self.assertNotIn('synthetic-temporary-value',json.dumps(self.sent,default=str))
        records=[r for r in approvals.read_log(self.st) if r.get('reason')=='device']
        self.assertEqual(len(records),3)  # plan, create root and revoke root; no root mutation inherits the plan UV
        self.assertTrue(all(approvals.check_record(self.st,r)=='ok' for r in records))

    async def test_concurrent_spend_expiry_revocation_task_end_and_restart(self):
        step={'kind':'https_auth','credential':'secret:SYNTHETIC_AUTH','url':'https://api.stripe.com/v1/customers'}
        plan,_=await self.approve([step])
        outcomes=await asyncio.gather(self.execute(plan,0),self.execute(plan,0))
        self.assertEqual(sum(x['ok'] for x in outcomes),1);self.assertEqual(len(self.calls),1)
        for change in ('expiry','session','task_end','revocation','restart'):
            self.h.cur_turn=7
            plan,_=await self.approve([{**step,"url":step["url"]+"?case="+change}])
            record=self.manager.plans[plan]
            if change=='expiry':record.expires=0
            elif change=='session':self.h.hist.epoch+=1
            elif change=='task_end':self.h.cur_turn=None
            elif change=='revocation':record.signing_key=b'changed-root'
            else:self.manager.end_all()
            self.assertFalse((await self.execute(plan,0))['ok'],change)
            if change=='session':self.h.hist.epoch-=1
            self.h.cur_turn=7

    async def test_actual_unix_socket_producer_scope_and_execution(self):
        from agentj import peer_origin
        self.h.agent.session={'sessionId':'synthetic-native','pid':os.getpid(),'procStart':peer_origin._stat(pathlib.Path('/proc'),os.getpid())[2]}
        import time
        self.h.agent.phone_turn=True;self.h.agent.phone_seen=True
        self.h.agent.cur_send.created=time.monotonic()
        await asyncio.sleep(.03)
        self.svc.log=lambda *args,**kwargs:None
        socket_path=self.path/'friends.sock'
        server=await asyncio.start_unix_server(self.svc.on_client,path=str(socket_path))
        self.addAsyncCleanup(server.wait_closed)
        self.addCleanup(server.close)
        async def request(obj):
            import subprocess,sys
            code="import socket,sys,json;s=socket.socket(socket.AF_UNIX);s.connect(sys.argv[1]);s.sendall(sys.stdin.buffer.read());f=s.makefile('rb');sys.stdout.buffer.write(f.readline())"
            res=await asyncio.to_thread(subprocess.run,[sys.executable,'-c',code,str(socket_path)],input=json.dumps(obj)+'\n',capture_output=True,text=True,timeout=15)
            self.assertEqual(res.returncode,0,res.stderr)
            return json.loads(res.stdout)
        steps=[{'kind':'https_auth','credential':'secret:SYNTHETIC_AUTH','url':'https://api.stripe.com/v1/customers'}]
        task=asyncio.create_task(request({'t':'approve-actions','actions':steps}))
        for _ in range(200):
            if self.h.asks or task.done():break
            await asyncio.sleep(.005)
        self.assertFalse(task.done(),task.result() if task.done() else '')
        ask=next(iter(self.h.asks.values()));self.addAsyncCleanup(self.stop,task,ask)
        await self.h._answer(self.s,self.ph.answer(self.h._ask_msg(ask,self.s),True))
        result=await task;self.assertTrue(result['ok'],result)
        self.assertTrue((await request({'t':'execute-action','plan':result['plan'],'step':0}))['ok'])
        self.assertFalse((await request({'t':'execute-action','plan':result['plan'],'step':0}))['ok'])
        self.h.agent.session={'sessionId':'different','pid':1,'procStart':'wrong'}
        bad=await request({'t':'execute-action','plan':result['plan'],'step':0})
        self.assertEqual(bad['why'],'unverified_plan_producer')
        server.close()

    async def test_typed_question_explain_reopen_same_request_execute_once(self):
        from agentj import wire
        steps=[{'kind':'https_auth','credential':'secret:SYNTHETIC_AUTH','url':'https://api.stripe.com/v1/customers'}]
        task=asyncio.create_task(self.svc.approve_actions(steps))
        for _ in range(200):
            if self.h.asks or task.done():break
            await asyncio.sleep(.005)
        ask=next(iter(self.h.asks.values()));self.addAsyncCleanup(self.stop,task,ask)
        deadline=ask.deadline;expiry=ask.bounded_plan.expires
        def intent(op):
            obj={'t':'approval_review','id':ask.rid,'op':op,'revision':ask.review_revision,
                 'envelope_sha256':ask.envelope_digest,'event_id':os.urandom(16).hex()}
            obj['sig']=wire.b64u(self.ph.sk.sign(approval_review.review_message(self.h.channel,self.ph.did,ask.rid,op,
                obj['revision'],obj['event_id'],obj['envelope_sha256'])))
            return obj
        await approval_review.on_phone(self.h,self.s,intent('question'))
        pending=await task
        self.assertEqual(pending['why'],'review_pending')
        old_rid=ask.rid
        explanation=self.agent.submit.call_args.args[0]
        self.agent.cur_send=explanation
        again=await self.svc.approve_actions(steps)
        self.assertEqual(again['plan'],old_rid)
        self.assertEqual(len(self.h.asks),1)
        await approval_review.on_phone(self.h,self.s,intent('reopen'))
        self.assertEqual((ask.deadline,ask.bounded_plan.expires),(deadline,expiry))
        await self.h._answer(self.s,self.ph.answer(self.h._ask_msg(ask,self.s),True))
        await asyncio.sleep(0)
        continuation=self.agent.submit.call_args.args[0]
        self.agent.cur_send=continuation
        final=await self.svc.approve_actions(steps)
        self.assertTrue(final['ok'],final)
        self.assertEqual(final['plan'],old_rid)
        self.assertFalse(self.h.asks)
        self.assertTrue((await self.execute(old_rid,0))['ok'])
        self.assertFalse((await self.execute(old_rid,0))['ok'])
        self.assertEqual(len(self.calls),1)

    async def test_26_business_shapes_safe_concretizations_and_negative_controls(self):
        from agentj import provenance
        from types import SimpleNamespace
        categories={
          'allow-0002':'backup','allow-0006':'clean','allow-0007':'inspect','allow-0009':'compare',
          'allow-0013':'lifecycle','allow-0018':'coupon-create','allow-0019':'customers','allow-0020':'subscriptions',
          'allow-0021':'cancel','allow-0022':'coupon-disable','allow-0025':'inspect','allow-0032':'model-test',
          'allow-0033':'upload','allow-0036':'backup','allow-0038':'backup','allow-0042':'clean','allow-0043':'clean',
          'allow-0048':'backup','allow-0119':'lifecycle','allow-0059':'promotions','allow-0065':'migrate',
          'allow-0077':'inspect','allow-0078':'inspect','allow-0081':'network','allow-0124':'inspect','allow-0135':'inspect'}
        results=[]
        def owner_task(label):
            turn=self.h.hist_add({'k':'phone','name':'你','text':label},'', 'done')
            origin=approval_review.remember(self.h,turn,provenance.phone())
            self.agent.cur_send=SimpleNamespace(approval_origin=origin,source=provenance.phone())
            self.h.cur_turn=turn['id']
        def descriptors(sample,kind,phase):
            folder=self.path/(sample+'-'+phase);folder.mkdir()
            source=folder/'source';source.mkdir();(source/'config.json').write_text('{"apiKey":"synthetic-private-value","baseURL":"https://name:password@example.invalid/?token=value"}')
            file=source/'config.json'
            base={'kind':'https_auth','credential':'secret:SYNTHETIC_AUTH','url':'https://api.stripe.com/v1/customers'}
            if kind in ('backup','clean','migrate'):
                return [{'kind':kind,'source':str(source),'destination':str(folder/'target')}],source
            if kind in ('inspect','compare'):
                step={'kind':'inspect','path':str(file),'projection':'metadata'}
                if kind=='compare':
                    other=folder/'other';other.write_bytes(file.read_bytes());step.update(projection='digest-compare',other=str(other))
                return [step],file
            if kind=='lifecycle':
                return [{'kind':'token_create','credential':'secret:SYNTHETIC_AUTH','url':'https://api.cloudflare.com/client/v4/user/tokens','method':'POST','body':{'name':'temporary bounded','policies':[]},'alias':'lifecycle','token_field':'result.value','id_field':'result.id'},
                        {'kind':'token_use','credential':'plan:lifecycle','url':'https://api.cloudflare.com/client/v4/user/tokens/verify'},
                        {'kind':'token_revoke','credential':'secret:SYNTHETIC_AUTH','url':'https://api.cloudflare.com/client/v4/user/tokens/','method':'DELETE','token_id':'lifecycle'},
                        {'kind':'token_destroy','alias':'lifecycle'}],file
            if kind=='coupon-create':base.update(url='https://api.stripe.com/v1/promotion_codes',method='POST',body={'coupon':'coupon_fixed','code':'SYNTHETIC'})
            elif kind=='coupon-disable':base.update(url='https://api.stripe.com/v1/promotion_codes/promo_fixed',method='POST',body={'active':False})
            elif kind=='subscriptions':base['url']='https://api.stripe.com/v1/subscriptions'
            elif kind=='promotions':base['url']='https://api.stripe.com/v1/promotion_codes'
            elif kind=='cancel':base.update(url='https://api.stripe.com/v1/subscriptions/sub_fixed',method='POST',body={'cancel_at_period_end':True})
            elif kind=='network':base['url']='https://api.zerotier.com/api/network/fixed/member'
            elif kind=='model-test':base.update(url='https://models.example.invalid/v1/chat/completions',method='POST',body={'model':'synthetic','max_tokens':1,'messages':[{'role':'user','content':'ping'}]})
            elif kind=='upload':
                audio=folder/'synthetic.wav';audio.write_bytes(b'RIFF synthetic audio bytes')
                base.update(url='https://audio.example.invalid/v1/audio/transcriptions',method='POST',body={'model':'synthetic'},file={'path':str(audio),'field':'file'})
            return [base],file
        for sample,kind in categories.items():
            with self.subTest(sample=sample,kind=kind):
                owner_task(sample+' positive')
                steps,_=descriptors(sample,kind,'positive')
                plan,_=await self.approve(steps)
                successes=[]
                for i in range(len(steps)):
                    result=await self.execute_with_individual_uv(plan,i)
                    self.assertTrue(result['ok'],(sample,result));successes.append(result['ok'])
                self.assertFalse((await self.execute(plan,0))['ok'])
                owner_task(sample+' negative')
                bad_steps,source=descriptors(sample,kind,'negative')
                plan2,_=await self.approve(bad_steps)
                before=len(self.calls)
                if kind in ('backup','clean','migrate','inspect','compare'):
                    if source.is_dir():(source/'new-user-file').write_text('not part of the approved object')
                    else:source.write_text('different object contents')
                else:
                    self.manager.plans[plan2].signing_key=b'revoked-owner-key'
                blocked=await self.execute(plan2,0)
                self.assertFalse(blocked['ok'],(sample,blocked));self.assertEqual(len(self.calls),before)
                results.append({'sample':sample,'kind':kind,'concretized_positive':all(successes),'negative_blocked':not blocked['ok'],
                                'unknown_cost_or_root_individual_uv':kind in ('model-test','upload','lifecycle','network')})
        target=pathlib.Path(__file__).resolve().parents[3]/'reports/qa/541-implement/host/business26-results.json'
        target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(results,indent=2)+'\n')
        self.assertEqual(len(results),26)

    async def test_existing_file_plan_real_javascript_signature_accepted(self):
        import base64, subprocess
        from cryptography.hazmat.primitives.serialization import Encoding,PrivateFormat,NoEncryption
        file=self.path/'existing-file';file.write_text('ordinary approved contents')
        actions=[{'tool':'Bash','input':{'command':'rm -- '+str(file)}}]
        task=asyncio.create_task(self.svc.approve_actions(actions))
        for _ in range(200):
            if self.h.asks or task.done():break
            await asyncio.sleep(.005)
        self.assertFalse(task.done(),task.result() if task.done() else '')
        ask=next(iter(self.h.asks.values()));self.addAsyncCleanup(self.stop,task,ask)
        frame=self.h._ask_msg(ask,self.s)
        self.assertIsInstance(frame['envelope']['plan']['object_bindings']['Bash:'+ask.input_sha]['paths'][0]['chain'][-1]['stat'][7],str)
        module=pathlib.Path(__file__).resolve().parents[2]/'protocol/wire.js'
        code="""import fs from 'node:fs';import {webcrypto} from 'node:crypto';import {pathToFileURL} from 'node:url';
        if(!globalThis.crypto)Object.defineProperty(globalThis,'crypto',{value:webcrypto});const w=await import(pathToFileURL(process.argv[1]));const x=JSON.parse(fs.readFileSync(0,'utf8'));
        x.frame.envelope_verified=true;const digest=await w.approvalEnvelopeDigest(x.frame.envelope);
        const message=await w.approvalV2Message(x.channel,x.device,x.frame,'allow');
        const key=await crypto.subtle.importKey('pkcs8',Buffer.from(x.private,'base64'),{name:'Ed25519'},false,['sign']);
        const sig=Buffer.from(await crypto.subtle.sign('Ed25519',key,message)).toString('base64url');
        process.stdout.write(JSON.stringify({digest,sig}));"""
        payload={'frame':frame,'channel':self.h.channel,'device':self.ph.did,
                 'private':base64.b64encode(self.ph.sk.private_bytes(Encoding.DER,PrivateFormat.PKCS8,NoEncryption())).decode()}
        result=await asyncio.to_thread(subprocess.run,['node','--input-type=module','-e',code,str(module)],input=json.dumps(payload),capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        signed=json.loads(result.stdout);self.assertEqual(signed['digest'],ask.envelope_digest)
        answer=self.ph.answer(frame,True);answer['sig']=signed['sig']
        await self.h._answer(self.s,answer)
        self.assertTrue((await task)['ok'])
