"""One signed exact replacement keeps unknown history and native send boundaries."""
import _hermetic
import copy
import hashlib
import os
from types import SimpleNamespace
from unittest.mock import patch
from test_zero_tail_mail_migrate import Migration
from agentj import workflow_mail as wm

class Replacement(Migration):
    async def prepared(self):
        old = await self.legacy()
        # Historical SMTP attempt predates the new sender binding.
        key = wm.digest({'host': old['host'], 'workflow_id':'story', 'root':self.contract['root'], 'delivery':'episode-1'})
        data = self.service.load()
        data['deliveries'][key] = {'grant':old['id'],'manifest_sha256':wm.digest(self.req['delivery_manifest']),
            'status':'unknown','provider_acceptance':None,'day':0}
        self.service.save(data)
        self.host.agent = SimpleNamespace(cur_send=SimpleNamespace(source={'kind':'phone','role':'owner'}))
        self.assertTrue((await self.service.control({'op':'migrate','workflow_id':'story'},os.getpid()))['ok'])
        new = copy.deepcopy(self.req); new['files']=[]; new['delivery_manifest']['attachments']=[]
        retry = {**new,'op':'retry','workflow_id':'story','delivery':key,'retry_original_manifest':copy.deepcopy(self.req['delivery_manifest'])}
        self.ask.reset_mock()
        async def sign_retry(tool, inp, **kw):
            self.assertEqual(tool,'agentj mail workflow-retry')
            return await self.sign(wm.TOOL,inp,**kw)
        self.ask.side_effect=sign_retry
        return key, new, retry

    async def test_missing_pin_is_migration_not_account_drift(self):
        await self.legacy()
        self.assertEqual(self.service.why(self.service.load()['grants']['story'],self.contract),'smtp_binding_required')

    async def test_exact_replacement_once_and_unknown_history(self):
        key,new,retry=await self.prepared()
        result=await self.service.control(retry,os.getpid())
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['retry_manifest_sha256'],wm.digest(new['delivery_manifest']))
        self.assertEqual(self.service.load()['deliveries'][key]['status'],'unknown')
        self.ask.assert_awaited_once()
        self.assertTrue((await self.service.send(new,os.getpid()))['ok'])
        row=self.service.load()['deliveries'][key]
        self.assertEqual(row['previous']['status'],'unknown')
        self.assertEqual((await self.service.send(new,os.getpid()))['why'],'duplicate')

    async def test_changed_episode_binding_and_recipient_refuse_without_card(self):
        _,new,retry=await self.prepared()
        for field,val in [('idempotency_key','other'),('binding_sha256','c'*64)]:
            r=copy.deepcopy(retry); r['delivery_manifest'][field]=val
            self.assertFalse((await self.service.control(r,os.getpid()))['ok'])
        r=copy.deepcopy(retry); r['cc']=['outsider@example.test']
        self.assertFalse((await self.service.control(r,os.getpid()))['ok'])
        self.ask.assert_not_awaited()

    async def test_sent_partial_reserved_or_retried_refuse_without_card(self):
        key,new,retry=await self.prepared()
        original=self.service.load()
        for change in ({'status':'sent'},{'status':'reserved'},{'retried':True},{'error':{'type':'RecipientsRefused'}}):
            data=copy.deepcopy(original);data['deliveries'][key].update(change);self.service.save(data)
            self.assertFalse((await self.service.control(retry,os.getpid()))['ok'])
        self.ask.assert_not_awaited()

    async def test_approved_digest_does_not_allow_old_or_different_content(self):
        key,new,retry=await self.prepared()
        self.assertTrue((await self.service.control(retry,os.getpid()))['ok'])
        self.assertEqual((await self.service.send(self.req,os.getpid()))['why'],'retry_manifest_changed')
        altered=copy.deepcopy(new);altered['body']='new unapproved body'
        altered['delivery_manifest']['body_sha256']=hashlib.sha256(altered['body'].encode()).hexdigest()
        self.assertEqual((await self.service.send(altered,os.getpid()))['why'],'retry_manifest_changed')
        with patch.object(wm,'source',return_value=None):
            self.assertEqual((await self.service.send(new,os.getpid()))['why'],'workflow_source')
        self.assertTrue((await self.service.send(new,os.getpid()))['ok'])

    async def test_grant_or_record_change_while_waiting_is_stale(self):
        key,new,retry=await self.prepared()
        signer=self.ask.side_effect
        async def mutate(*a,**kw):
            data=self.service.load();data['deliveries'][key]['extra']='changed';self.service.save(data)
            return await signer(*a,**kw)
        self.ask.side_effect=mutate
        self.assertEqual((await self.service.control(retry,os.getpid()))['why'],'retry_stale')
        self.assertNotIn('retry_allowed',self.service.load()['deliveries'][key])

    async def test_grant_change_after_approval_does_not_redirect(self):
        key,new,retry=await self.prepared()
        self.assertTrue((await self.service.control(retry,os.getpid()))['ok'])
        data=self.service.load();data['grants']['story']['core']['id']='changed-grant'
        signed=await self.sign(wm.TOOL,{'summary':wm.shown(data['grants']['story']['core'])},always_ask=True,require_passkey=True,grant_info=True)
        data['grants']['story']['proof']={k:v for k,v in signed['grant_info'].items() if k != 'sign_pub'}
        self.service.save(data)
        self.assertEqual((await self.service.send(new,os.getpid()))['why'],'retry_stale')
        self.assertFalse(self.smtp.sent)

    async def test_status_is_exact_readonly_authorization_receipt(self):
        key,new,retry=await self.prepared()
        request={'op':'status','workflow_id':'story','delivery':key}
        self.assertFalse((await self.service.control(request,None))['retry_authorization']['allowed'])
        self.assertTrue((await self.service.control(retry,os.getpid()))['ok'])
        before=copy.deepcopy(self.service.load())
        result=(await self.service.control(request,None))['retry_authorization']
        self.assertTrue(result['allowed'])
        self.assertEqual(result['manifest_sha256'],wm.digest(new['delivery_manifest']))
        self.assertEqual(before,self.service.load())
        self.assertFalse((await self.service.control({**request,'delivery':'other'},None))['retry_authorization']['allowed'])
        await self.service.send(new,os.getpid())
        self.assertFalse((await self.service.control(request,None))['retry_authorization']['allowed'])

    def native(self):
        self.host.agent.cur_send.source={'kind':'scheduled_task','role':'task'}
        self.host.scheduler.mail_scope={'live':True,'mail_contract':self.contract,'mail_admission':{'run_id':'rid','instance_id':'instance'}}

    async def test_exact_native_can_only_request_replacement_with_real_uv(self):
        key,new,retry=await self.prepared();self.native()
        self.assertTrue((await self.service.control(retry,os.getpid()))['ok'])
        self.ask.assert_awaited_once()
        self.assertTrue(self.ask.await_args.kwargs['require_passkey'])
        self.assertTrue((await self.service.send(new,os.getpid()))['ok'])
        self.assertEqual(self.host.agent.cur_send.source['role'],'task')

    async def test_native_regular_retry_and_foreign_source_refuse(self):
        key,new,retry=await self.prepared();self.native()
        self.assertEqual((await self.service.control({'op':'retry','workflow_id':'story','delivery':key},os.getpid()))['why'],'workflow_source')
        for source in (None,{**self.contract,'workflow_id':'other'},{**self.contract,'tsha':'z'*64}):
            with patch.object(wm,'source',return_value=source):
                self.assertEqual((await self.service.control(retry,os.getpid()))['why'],'workflow_source')
        self.ask.assert_not_awaited()

    async def test_native_end_or_instance_change_while_card_waits_is_stale(self):
        key,new,retry=await self.prepared();self.native()
        sign=self.ask.side_effect
        async def end(*a,**kw):
            self.host.scheduler.mail_scope['mail_admission']['instance_id']='next-instance'
            return await sign(*a,**kw)
        self.ask.side_effect=end
        self.assertEqual((await self.service.control(retry,os.getpid()))['why'],'retry_stale')
        self.assertNotIn('retry_allowed',self.service.load()['deliveries'][key])

    async def test_native_changed_instance_after_approval_cannot_send(self):
        key,new,retry=await self.prepared();self.native()
        self.assertTrue((await self.service.control(retry,os.getpid()))['ok'])
        status={'op':'status','workflow_id':'story','delivery':key}
        self.assertTrue((await self.service.control(status,None))['retry_authorization']['allowed'])
        self.host.scheduler.mail_scope['mail_admission']['run_id']='next-run'
        self.assertFalse((await self.service.control(status,None))['retry_authorization']['allowed'])
        self.assertEqual((await self.service.send(new,os.getpid()))['why'],'retry_stale')
        self.assertFalse(self.smtp.sent)

    async def test_concurrent_native_retry_emits_one_card(self):
        import asyncio
        key,new,retry=await self.prepared();self.native()
        entered,release=asyncio.Event(),asyncio.Event();sign=self.ask.side_effect
        async def wait(*a,**kw):
            entered.set();await release.wait();return await sign(*a,**kw)
        self.ask.side_effect=wait
        task=asyncio.create_task(self.service.control(retry,os.getpid()))
        await asyncio.wait_for(entered.wait(),2)
        self.assertEqual((await self.service.control(retry,os.getpid()))['why'],'card_pending')
        release.set();self.assertTrue((await task)['ok']);self.ask.assert_awaited_once()

    async def test_actual_kernel_native_scope_requests_uv_without_owner(self):
        import asyncio,sys
        from agentj import provenance
        key,new,retry=await self.prepared();self.native();self.src.stop()
        proc=await asyncio.create_subprocess_exec(sys.executable,'-c','import time;time.sleep(30)')
        self.addAsyncCleanup(self.kill,proc)
        with provenance.task_run() as scope:
            scope['mail_contract']=self.contract
            scope['mail_admission']={'run_id':'rid','instance_id':'instance','research':False,'scheduled':True}
            wm.pin_current(proc);self.host.scheduler.mail_scope=scope
            self.assertEqual(wm.source(self.host,proc.pid),self.contract)
            result=await self.service.control(retry,proc.pid)
            self.assertTrue(result['ok'],result)
            self.ask.assert_awaited_once()
            self.assertTrue((await self.service.send(new,proc.pid))['ok'])
            self.assertEqual((await self.service.control(retry,os.getpid()))['why'],'workflow_source')
