"""Hermetic #398: structured rejection only; account payload never escapes."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
from agentj import quota_refresh, accounts
from agentj.agent_codex import RPCError

ACCOUNT={'account':{'type':'chatgpt','email':'fake@example.invalid','workspaceName':None}}

class Recovery(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.meta={'harness':{'codex':{'active':'a','switched':1}}}
        self.h=NS(channel='owner',hist=NS(epoch=1),task_epoch='task',task_grants={('task',):{'until':99,'used':{'consumed'}},('other',):{'until':99}},_task_id=lambda:'task',
                  st=NS(root='/work/unused'),auth_rescue=NS(flow=None))
        self.a=NS(host=self.h,kind='codex',tid='thread',cur_send=None,halting=False,
                  owns_harness=lambda:True,proc=object(),call=AsyncMock(return_value={**ACCOUNT,'rateLimits':{}}),
                  request_restart=Mock(),_quota_evidence=False)
        self.h.agent=self.a
        self.remote=NS(device='phone')
        self.h.sessions={'phone':self.remote};self.h._paired_live=lambda r:True
        self.h.st.sign_key=lambda device:b'fixture-public-signing-key'
        self.a.cur_send=NS(device='phone',sid='message',turn=1,state='delivering',source={'kind':'phone','role':'owner'})
        self.stop_patch=patch('agentj.controls.estop_state',return_value={'on':False})
        self.stop_patch.start();self.addCleanup(self.stop_patch.stop)
        self.p=patch.object(accounts,'load',return_value=self.meta);self.p.start();self.addCleanup(self.p.stop)
    async def test_refresh_is_explicit_and_discards_account(self):
        self.assertTrue(await quota_refresh.refresh(self.a))
        self.assertEqual(self.a.call.await_args_list[0].args,('account/read',{},20))
        self.assertEqual(self.a.call.await_args_list[1].args,('account/read',{'refreshToken':True},20))
        self.assertEqual(self.h.task_grants[('task',)],{'until':0,'used':{'consumed'}})
        self.assertEqual(self.h.task_grants[('other',)],{'until':99})
    async def test_shared_refuses_rpc(self):
        self.a.owns_harness=lambda:False
        self.assertFalse(await quota_refresh.refresh(self.a));self.a.call.assert_not_awaited()
    async def test_identity_change_refuses(self):
        async def changed(*args): self.h.hist.epoch=2;return ACCOUNT
        self.a.call.side_effect=changed
        self.assertFalse(await quota_refresh.refresh(self.a))
    async def test_unsupported_never_claims_refresh_or_restart(self):
        self.a.call.side_effect=RPCError({'code':-32601})
        self.assertFalse(await quota_refresh.refresh(self.a));self.a.request_restart.assert_not_called()
    async def test_error_is_not_string_classified(self):
        for error in ({'message':'usage_limit_reached'}, {'code':429}, {'data':{'message':'usageLimitExceeded'}}):
            self.assertFalse(quota_refresh.rejected(RPCError(error)))
        for error in ({'code':'usage_limit_reached'}, {'data':{'codexErrorInfo':'usageLimitExceeded'}}):
            self.assertTrue(quota_refresh.rejected(RPCError(error)))
    async def test_failure_releases_refresh_state(self):
        self.a.call.side_effect=ConnectionError()
        self.assertFalse(await quota_refresh.refresh(self.a));self.assertFalse(self.a._quota_refreshing)

class Start(Recovery):
    def setUp(self):
        super().setUp()
        self.remote=NS(device='phone')
        self.h.sessions={'phone':self.remote};self.h._paired_live=lambda r:True
        self.h.st.sign_key=lambda device:b'fixture-public-signing-key'
        self.a.cur_send=NS(device='phone',sid='message',turn=1,state='delivering',source={'kind':'phone','role':'owner'})
        self.a.turn_id=None;self.a.turn_status={};self.a._quota_evidence=False
        self.estop=patch('agentj.controls.estop_state',return_value={'on':False})
        self.estop.start();self.addCleanup(self.estop.stop)
    async def test_same_message_retried_once(self):
        params={'input':['same']}
        self.a.call.side_effect=[RPCError({'code':'usage_limit_reached'}),ACCOUNT,ACCOUNT,{'rateLimits':{}},{'turn':{'id':'accepted'}}]
        self.assertEqual(await quota_refresh.start(self.a,'turn/start',params),{'turn':{'id':'accepted'}})
        calls=self.a.call.await_args_list
        self.assertEqual([c.args[0] for c in calls],['turn/start','account/read','account/read','account/rateLimits/read','turn/start'])
        self.assertIs(calls[0].args[1],calls[4].args[1])
    async def test_second_quota_is_not_looped(self):
        self.a.call.side_effect=[RPCError({'code':'usage_limit_reached'}),ACCOUNT,ACCOUNT,{},RPCError({'code':'usage_limit_reached'})]
        with self.assertRaises(RPCError):await quota_refresh.start(self.a,'turn/start',{})
        self.assertEqual(self.a.call.await_count,5)
    async def test_accepted_review_output_and_unknown_never_replay(self):
        for method,evidence,error in [('review/start',False,RPCError({'code':'usage_limit_reached'})),('turn/start',True,RPCError({'code':'usage_limit_reached'})),('turn/start',False,TimeoutError()),('turn/start',False,ConnectionError())]:
            self.a._quota_evidence=evidence;self.a.call.reset_mock();self.a.call.side_effect=error
            with self.assertRaises(type(error)):await quota_refresh.start(self.a,method,{})
            self.assertEqual(self.a.call.await_count,1)
    async def test_cancel_releases_state(self):
        import asyncio
        self.a.call.side_effect=asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):await quota_refresh.refresh(self.a)
        self.assertFalse(self.a._quota_refreshing)
    async def test_changes_during_refresh_never_retry(self):
        for change in (lambda:setattr(self.h,'channel','changed'),
                       lambda:setattr(self.h.hist,'epoch',2),
                       lambda:setattr(self.h,'task_epoch','changed'),
                       lambda:self.meta['harness']['codex'].update(active='b'),
                       lambda:setattr(self.a,'_quota_stop',1),
                       lambda:setattr(self.h.st,'sign_key',lambda _:b'new-key'),
                       lambda:setattr(self.h,'_paired_live',lambda _:False),
                       lambda:setattr(self.a.cur_send,'state','cancelled')):
            self.setUp()
            async def rpc(method,*args):
                if method=='turn/start':raise RPCError({'code':'usage_limit_reached'})
                change();return ACCOUNT
            self.a.call.side_effect=rpc
            with self.assertRaises(RPCError):await quota_refresh.start(self.a,'turn/start',{})
            self.assertEqual(sum(c.args[0]=='turn/start' for c in self.a.call.await_args_list),1)
