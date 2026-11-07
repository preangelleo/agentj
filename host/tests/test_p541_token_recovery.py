"""Normal temporary-token lifecycle, simulated provider failure, stop/restart and fresh-owner recovery."""
import _hermetic
import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from agentj import bounded_plan as bp, approval_review, provenance, secret_xfer
from agentj.peer_service import PeerService
from test_l1 import _ready
from _p541_device import _owner_host
import test_p541_bounded_plan as bounded_fixtures


class Recovery(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=bounded_fixtures.Bounded.asyncSetUp
    stop=bounded_fixtures.Bounded.stop
    approve=bounded_fixtures.Bounded.approve
    execute=bounded_fixtures.Bounded.execute
    execute_with_individual_uv=bounded_fixtures.Bounded.execute_with_individual_uv

    def lifecycle(self):
        return [{'kind':'token_create','credential':'secret:SYNTHETIC_AUTH','url':'https://api.cloudflare.com/client/v4/user/tokens',
                 'method':'POST','body':{'name':'temporary business task','policies':[]},'alias':'temp','token_field':'result.value','id_field':'result.id'},
                {'kind':'token_revoke','credential':'secret:SYNTHETIC_AUTH','url':'https://api.cloudflare.com/client/v4/user/tokens/',
                 'method':'DELETE','token_id':'temp'}, {'kind':'token_destroy','alias':'temp'}]

    def fresh_task(self,label):
        turn=self.h.hist_add({'k':'phone','name':'你','text':label},'','done')
        origin=approval_review.remember(self.h,turn,provenance.phone())
        self.agent.cur_send=SimpleNamespace(approval_origin=origin,source=provenance.phone())
        self.h.cur_turn=turn['id']

    def restart(self):
        self.manager.end_all()
        self.h=_owner_host(self.st,self.sent)
        self.h.ask_ttl=5
        self.agent=self.h.agent;self.agent.submit=mock.Mock()
        self.s=_ready(self.h,self.ph)
        self.manager=bp.Plans(self.h,resolver=lambda host:['8.8.8.8'],transport=self.transport)
        self.h._bounded_plan_manager=self.manager
        self.svc=PeerService.__new__(PeerService);self.svc.host,self.svc.st=self.h,self.st
        self.fresh_task('请核对上次临时令牌的待处理记录。')

    def transport(self,step,auth,body):
        self.calls.append((step['kind'],step['url'],bytes(auth),body))
        if step['kind']=='token_create':
            return 200,json.dumps({'success':True,'result':{'value':'synthetic-temporary-value','id':'owned-token-id',
                    'expires_on':step['body']['expires_on']}}).encode()
        if step['kind']=='token_recovery':
            return 200,json.dumps({'success':True,'result':{'id':'owned-token-id',**({'status':'active'} if step['method']=='GET' else {})}}).encode()
        return 503,b'{"success":false,"errors":[{"message":"service temporarily unavailable"}]}'

    async def test_failed_revoke_restart_metadata_then_fresh_uv_verify_and_revoke(self):
        self.manager.transport=self.transport
        plan,_=await self.approve(self.lifecycle())
        created=await self.execute_with_individual_uv(plan,0)
        self.assertTrue(created['ok'],created)
        item=self.manager.plans[plan].token_values['temp'];record=item['recovery_record']
        row=self.manager.recovery.get(record)
        self.assertEqual(row['token_id'],'owned-token-id')
        self.assertLessEqual(row['requested_expires_at']-row['created_at'],590)
        self.assertEqual(row['expiry_confirmation'],'provider_reported')
        failed=await self.execute_with_individual_uv(plan,1)
        self.assertFalse(failed['ok']);self.assertIn('撤销未确认',failed['message'])
        calls=len(self.calls)
        self.restart()
        self.assertFalse(any(item['value']))
        self.assertFalse(self.manager.plans)
        self.assertFalse((await self.execute(plan,1))['ok'])
        self.assertEqual(len(self.calls),calls,'restart must not replay a remote request')
        view=await self.svc.cli({'t':'action-recovery'})
        self.assertTrue(view['ok']);self.assertFalse(view['authority_restored'])
        self.assertEqual(view['records'][0]['record'],record)
        self.assertIn('撤销未确认',view['records'][0]['message'])
        verify={'kind':'token_recovery','record':record,'operation':'verify'}
        verification,ask=await self.approve([verify])
        self.assertNotEqual(verification,plan)
        self.assertEqual(ask.envelope['plan']['expected_confirmations'],2)
        checked=await self.execute_with_individual_uv(verification,0)
        self.assertTrue(checked['ok'],checked)
        self.assertEqual(self.manager.recovery.get(record)['state'],'active_confirmed')
        self.fresh_task('请撤销刚核实仍有效的那一个临时令牌。')
        recovery,_=await self.approve([{'kind':'token_recovery','record':record,'operation':'revoke'}])
        outcome=await self.execute_with_individual_uv(recovery,0)
        self.assertTrue(outcome['ok'],outcome)
        self.assertIn('已取得撤销回执',outcome['result']['message'])
        self.assertEqual(self.manager.recovery.get(record)['state'],'revoked_confirmed')
        raw=self.manager.recovery.path.read_text()
        self.assertNotIn('synthetic-temporary-value',raw)
        self.assertNotIn(self.credential.read_text(),raw)
        self.assertEqual(self.manager.recovery.path.stat().st_mode&0o777,0o600)

    async def test_unknown_create_restart_waits_for_requested_expiry_without_auth_read(self):
        def unavailable(*args):raise TimeoutError('synthetic provider timeout')
        self.manager.transport=unavailable
        plan,_=await self.approve(self.lifecycle())
        outcome=await self.execute_with_individual_uv(plan,0)
        self.assertFalse(outcome['ok']);self.assertIn('创建结果未知',outcome['message'])
        row=next(iter(self.manager.recovery.records.values()))
        self.assertIsNone(row['token_id']);self.assertEqual(row['state'],'creation_unknown')
        self.restart()
        with mock.patch.object(secret_xfer,'read_value',side_effect=AssertionError('metadata view must not read authentication')):
            before=await self.svc.cli({'t':'action-recovery'})
            self.assertIn('等待',before['records'][0]['message'])
            with mock.patch('agentj.token_recovery.time.time',return_value=row['requested_expires_at']+1):
                after=await self.svc.cli({'t':'action-recovery'})
        displayed=after['records'][0]
        self.assertEqual(displayed['state'],'expired_as_requested')
        self.assertIn('按请求已到期',displayed['message']);self.assertIn('未取得撤销回执',displayed['message'])
        self.assertEqual(self.manager.recovery.get(row['id'])['state'],'creation_unknown')
        self.assertFalse(self.calls)

    async def test_verification_service_failure_can_be_rechecked_only_as_new_owner_operation(self):
        self.manager.transport=self.transport
        plan,_=await self.approve(self.lifecycle())
        self.assertTrue((await self.execute_with_individual_uv(plan,0))['ok'])
        record=self.manager.plans[plan].token_values['temp']['recovery_record']
        self.restart()
        verification,_=await self.approve([{'kind':'token_recovery','record':record,'operation':'verify'}])
        self.manager.transport=lambda *args:(503,b'{"success":false}')
        failed=await self.execute_with_individual_uv(verification,0)
        self.assertFalse(failed['ok']);self.assertEqual(self.manager.recovery.get(record)['state'],'verification_unknown')
        self.restart()
        fresh,_=await self.approve([{'kind':'token_recovery','record':record,'operation':'verify'}])
        self.assertNotEqual(fresh,verification)
        self.assertTrue((await self.execute_with_individual_uv(fresh,0))['ok'])
        self.assertEqual(self.manager.recovery.get(record)['state'],'active_confirmed')

    async def test_native_human_transcript_input_is_archived_with_host_owned_reference(self):
        from agentj.shared import SharedClaudeAgent
        agent=SharedClaudeAgent(self.h,{'dir':str(self.path),'shared_identity':False})
        self.h.agent=agent
        agent.transcript=self.path/'native-human.jsonl'
        words='请核对临时令牌恢复记录。\n先看状态，再确认是否撤销。'
        record={'uuid':'native-owner-task','type':'user','message':{'role':'user','content':[{'type':'text','text':words}]}}
        agent.transcript.write_text(json.dumps(record,ensure_ascii=False)+'\n')
        agent.read_transcript()
        source=approval_review.snapshot(self.h)
        self.assertTrue(source['verified']);self.assertFalse(source['suspicious'])
        self.assertEqual(source['channel'],'host')
        self.assertEqual(source['message_id'],self.h.desktop_turn)
        self.assertEqual(source['text'],words)
        self.assertIn(source['message_id'],self.h.hist.turns)

    async def test_recovery_cli_parser_is_the_existing_friends_entry(self):
        import argparse
        from agentj import peer_service
        parser=argparse.ArgumentParser();peer_service.add_parser(parser.add_subparsers(dest='cmd'))
        args=parser.parse_args(['friends','action-recovery'])
        self.assertEqual(peer_service._req(args),{'t':'action-recovery'})
