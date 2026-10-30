"""P568: real card/transport boundaries and persistent backwards-compatible channel status."""
import asyncio
import http.client
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
from types import SimpleNamespace as NS
import test_p501_wechat as fixture
from agentj import codex_forms, compactprep, policy_registry as policy, working_root
from agentj.agent_codex import mcp_tool_approval
from agentj.wechat import poll_error, error_hint, WeChat
from agentj.wechat_protocol import Failure, checked


class Forms(unittest.TestCase):
    def form(self, **fields):
        return {'mode': 'openai/form', 'serverName': 'test', 'message':'Please choose', 'requestedSchema': {'type':'object', 'properties': fields}}

    def test_forms_aliases_types_and_bounds(self):
        p=self.form(city={'type':'string','minLength':2}, count={'type':'integer','minimum':1,'maximum':4}, enabled={'type':'boolean'})
        for mode in codex_forms.FORM_MODES:
            p['mode']=mode; c=codex_forms.card_for(p)
            self.assertEqual(codex_forms.content_for(p,['Tokyo','3',[1]],c),{'city':'Tokyo','count':3,'enabled':True})
            for picks in (['X','3',[1]],['Tokyo','5',[1]],['Tokyo','3.5',[1]],['Tokyo','3','yes']):
                with self.assertRaises(codex_forms.Unsupported):codex_forms.content_for(p,picks,c)

    def test_secret_never_uses_question_form(self):
        for f in ({'type':'string'}, {'type':'string','format':'password'}):
            with self.assertRaisesRegex(codex_forms.Unsupported,'secret'):
                codex_forms.card_for(self.form(api_key=f))
        self.assertIn('agentj secret request',codex_forms.notice('en','secret'))

    def test_unsupported_constraints_and_empty_approval_not_generic_form(self):
        for f in ({'type':'object'}, {'type':'string','pattern':'x'}):
            with self.assertRaises(codex_forms.Unsupported):codex_forms.card_for(self.form(x=f))
        with self.assertRaises(codex_forms.Unsupported):codex_forms.card_for(self.form())

    def test_mcp_approval_modes_remain_bound_and_once(self):
        from test_p539_passkey import Units
        p=Units.ELI
        for mode in codex_forms.FORM_MODES:
            calls={'i':{'server':'computer-use','tool':'click','arguments':{'x':1},'turn':'u'}}
            self.assertEqual(mcp_tool_approval({**p,'mode':mode},calls)[1],{'x':1})
            self.assertIsNone(mcp_tool_approval({**p,'mode':mode},calls))

    def test_null_schema_and_tool_metadata_still_require_owned_item(self):
        p={'mode':'openai/form','threadId':'t','turnId':'u','serverName':'test','message':'Confirm this step',
           'requestedSchema':None,'_meta':{'codex_approval_kind':'mcp_tool_call','tool_name':'read_file','tool_params':{'cmd':'forged'}}}
        calls={'i':{'server':'test','tool':'read_file','arguments':{'path':'normal'},'turn':'u'}}
        self.assertIsNone(mcp_tool_approval(p,{}))
        self.assertEqual(mcp_tool_approval(p,calls)[1],{'path':'normal'})
        self.assertIsNone(mcp_tool_approval(p,calls))


class Policy(unittest.TestCase):
    def test_connector_reads_and_writes_never_enable_channels(self):
        for tool,expected in [('search_threads','allow'),('get_message','allow'),('list_files','allow'),('read_document','allow'),('send_email','hold'),('delete_message','uv'),('get_secret','hold'),('read_api_key','hold'),('execute_script','hold')]:   # P573: by name a button
            name='mcp__claude_ai_Gmail__'+tool
            self.assertEqual(policy.main_requirement(name,{}),expected)
            self.assertIn(policy.channel_tool_decision(name),('deny','handoff'))
        self.assertEqual(policy.main_requirement('invented_action',{}),'deny')
        self.assertEqual(policy.main_requirement('mcp__bad__search__inject',{}),'deny')

    def test_pretooluse_connector_read_write_and_injected_command(self):
        import io, json
        from agentj import danger
        for tool, inp, want in [('search_threads',{},None),('send_email',{},'ask'),('delete_message',{},'ask'),
                                 ('search_threads',{'command':'rm -rf /tmp/example'},'ask')]:
            out=io.StringIO()
            with patch('sys.stdin',io.StringIO(json.dumps({'tool_name':'mcp__claude_ai_Gmail__'+tool,'tool_input':inp}))),patch('sys.stdout',out):
                self.assertEqual(danger.hook_main([]),0)
            decision=json.loads(out.getvalue())['hookSpecificOutput']['permissionDecision'] if out.getvalue() else None
            self.assertEqual(decision,want)

    def test_protected_macos_working_folders(self):
        for name in ('Downloads/编程','Desktop/x','Documents/x','Library/Mobile Documents/com~apple~CloudDocs/x'):
            self.assertTrue(working_root.protected_macos('/test-owner/'+name,platform='darwin',home='/test-owner'))
        self.assertFalse(working_root.protected_macos('/test-owner/coding',platform='darwin',home='/test-owner'))
        self.assertFalse(working_root.protected_macos('/test-owner/Downloads',platform='linux',home='/test-owner'))

    def test_error_types_and_no_exception_message_leak(self):
        for error,code in ((PermissionError('secret'),'state_unwritable'),(ConnectionResetError(),'connection_reset'),(http.client.RemoteDisconnected(),'proxy_dropped'),(Failure('api_error',ret=-14),'binding_expired')):
            self.assertEqual(poll_error(error),code)
            self.assertTrue(error_hint(code,'en'))
        with self.assertRaises(Failure) as got:checked({'ret':1,'errmsg':'logged in elsewhere'})
        self.assertEqual(got.exception.code,'binding_taken_over')


class FormApproval(unittest.IsolatedAsyncioTestCase):
    async def test_nonempty_approval_keeps_action_floor_before_form(self):
        p={'mode':'form','message':'Confirm','serverName':'example','requestedSchema':{'type':'object','properties':{'confirmed':{'type':'boolean'}}}}
        card=codex_forms.card_for(p)
        h=NS(ask=AsyncMock(return_value={'behavior':'deny'}),question=AsyncMock(return_value=('answer',[[1]])))
        a=NS(host=h,pending={},_answer=Mock(),local_only=Mock())
        ap=('mcp__example__delete_file',{'path':'example'},'i',None)
        await codex_forms.answer(a,1,p,card,approval=ap)
        h.question.assert_not_awaited();a._answer.assert_called_with(1,{'action':'decline'})
        h.ask.return_value={'behavior':'allow'}
        await codex_forms.answer(a,2,p,card,approval=ap)
        h.ask.assert_awaited_with(ap[0],ap[1],unittest.mock.ANY,batch=False)
        a._answer.assert_called_with(2,{'action':'accept','content':{'confirmed':True}})


class Compact(unittest.IsolatedAsyncioTestCase):
    async def test_codex_host_writes_fixed_path_without_agent_file_tool(self):
        with tempfile.TemporaryDirectory() as d:
            a=NS(cfg={'dir':d},collect=None,halting=False)
            async def turn(text):
                self.assertIn('Do not call tools',text)
                a.collect.append('# Handoff\nOriginal goal\nNext step')
            a.turn=turn
            await compactprep._codex_prepare(a,'en')
            p=Path(d)/'.agentj/handover/compact.md'
            self.assertIn('Original goal',p.read_text());self.assertEqual(p.stat().st_mode&0o777,0o600)
            self.assertIsNone(a.collect)
            p.unlink(); target=Path(d)/'target';target.write_text('keep');p.symlink_to(target)
            with self.assertRaises(OSError):await compactprep._codex_prepare(a,'en')
            self.assertEqual(target.read_text(),'keep')


class Channel(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixture.Channel.asyncSetUp
    asyncTearDown=fixture.Channel.asyncTearDown

    async def test_forward_status_including_preflight_failure_survives_restart(self):
        self.c.store.data['context']=''
        result=await self.c.forward(1,'phone')
        self.assertEqual(result['why'],'needs_message')
        self.assertEqual(self.c.status()['last_forward']['why'],'needs_message')
        self.assertEqual(self.c.store.read()['last_forward']['why'],'needs_message')

    async def test_poll_invalidation_stops_retries_and_survives_restart(self):
        self.c.poll_once=AsyncMock(side_effect=Failure('api_error',ret=-14))
        async def sleep(_):
            if self.c.store.data.get('binding_error'):self.c.closed=True
        with patch('agentj.wechat.asyncio.sleep',sleep):await self.c.run()
        self.c.poll_once.assert_awaited_once()
        self.assertFalse(self.c.status()['bound']);self.assertFalse(self.c.can_forward())
        self.assertEqual(self.c.store.read()['binding_error'],'binding_expired')
        self.assertIn('another Agent J host',error_hint('binding_expired','en'))
