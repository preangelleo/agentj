"""Union seam: preserve subset delivery and signed narrowing of original-recipient native retry."""
import _hermetic
import copy,os
from types import SimpleNamespace
from unittest.mock import patch
from agentj import workflow_mail as wm
from test_workflow_mail_subset import SubsetDelivery

class Union(SubsetDelivery):
    async def prepared_unknown(self, cc):
        await self.grant(cc_contacts=self.ids)
        req=self.selected(cc)
        with patch.object(self.svc,'smtp_send',side_effect=TimeoutError('fixture')):
            result=await self.service.send(req,os.getpid())
        self.assertEqual(result['why'],'send_unknown')
        key=result['delivery'];new=copy.deepcopy(req);new['files']=[];new['delivery_manifest']['attachments']=[]
        retry={**new,'op':'retry','workflow_id':'story','delivery':key,'retry_original_manifest':copy.deepcopy(req['delivery_manifest'])}
        self.host.agent=SimpleNamespace(cur_send=SimpleNamespace(source={'kind':'scheduled_task','role':'task'}))
        self.host.scheduler.mail_scope={'live':True,'mail_contract':self.contract,'mail_admission':{'run_id':'rid','instance_id':'instance'}}
        self.ask.reset_mock()
        async def sign_retry(tool,inp,**kw):
            self.assertEqual(tool,'agentj mail workflow-retry')
            return await self.sign(wm.TOOL,inp,**kw)
        self.ask.side_effect=sign_retry
        return key,new,retry

    async def test_original_four_cc_unknown_retries_signed_narrowing_once(self):
        key,new,retry=await self.prepared_unknown(self.cc)
        # The new owner-signed retry may narrow the original recipients, never broaden them.
        narrowed=copy.deepcopy(retry);narrowed['cc']=self.cc[:2];narrowed['delivery_manifest']['cc']=self.cc[:2]
        approved=await self.service.control(narrowed,os.getpid());self.assertTrue(approved['ok'],approved)
        self.assertEqual((await self.service.send(new,os.getpid()))['why'],'retry_manifest_changed')
        new['cc']=self.cc[:2];new['delivery_manifest']['cc']=self.cc[:2]
        status=(await self.service.control({'op':'status','workflow_id':'story','delivery':key},os.getpid()))
        self.assertIn(wm.SUBSET_CAPABILITY,status['capabilities'])
        self.assertTrue(status['retry_authorization']['allowed'])
        result=await self.service.send(new,os.getpid());self.assertTrue(result['ok'],result)
        self.assertEqual(result['sent'],[self.service.load()['grants']['story']['core']['binding']['primary']]+self.cc[:2])
        self.assertEqual(self.service.load()['deliveries'][key]['previous']['status'],'unknown')
        self.assertEqual((await self.service.send(new,os.getpid()))['why'],'duplicate')
        self.ask.assert_awaited_once()

    async def test_subset_unknown_retry_card_displays_actual_selected_cc(self):
        key,new,retry=await self.prepared_unknown(self.cc[:2])
        result=await self.service.control(retry,os.getpid());self.assertTrue(result['ok'],result)
        summary=self.ask.await_args.args[1]['summary']
        for address in self.cc[:2]:self.assertIn(address,summary)
        for address in self.cc[2:]:self.assertNotIn(address,summary)
        result=await self.service.send(new,os.getpid());self.assertTrue(result['ok'],result)
        self.assertEqual(result['sent'][1:],self.cc[:2])
