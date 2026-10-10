"""P123 regressions: quota windows, updated history, early websocket loss, hot update language."""
import _hermetic
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentj import agent_opencode as oc, doctor, phone_update, preferences, shared
from agentj.serve import Host, Session
from agentj.state import State
import test_p76_update


class Provider(unittest.TestCase):
    def test_quota_window_429_is_not_transient_rate_limit(self):
        for detail in ('weekly limit reached', 'monthly limit exceeded', 'daily limit reached',
                       'usage limit exceeded', 'weekly_limit_exceeded', 'monthly usage limit reached', 'quota exhausted', '本周额度已用尽',
                       'You exceeded your current quota', 'You exceeded your weekly allowance'):
            with self.subTest(detail=detail):
                self.assertEqual(oc.provider_error_reason('APIError', {'statusCode':429,'message':detail}), 'quota_window')
                self.assertEqual(oc.provider_error_reason('APIError', {'statusCode':429,'responseBody':json.dumps({'error':detail})}), 'quota_window')
        self.assertEqual(oc.provider_error_reason("APIError", {"statusCode":429,"responseBody":"["*1000 + '"quota"' + "]"*1000}), "quota_window")
        note = oc.PROVIDER_NOTES['quota_window']
        self.assertIn('重置',note); self.assertIn('reset',note); self.assertIn('升级',note)
        for language in ('zh','en'):
            a=oc.OpenCodeAgent.__new__(oc.OpenCodeAgent);a.cfg={'language':language}
            selected=a.provider_note('quota_window','fixture',note)
            self.assertEqual(selected,note.split(' / ')[0 if language=='zh' else -1])

    def test_real_rate_limit_and_balance_are_preserved(self):
        self.assertEqual(oc.provider_error_reason('APIError', {'statusCode':429,'message':'Upstream rate limit exceeded, please retry later'}), 'rate_limit')
        self.assertEqual(oc.provider_error_reason('APIError', {'statusCode':429,'message':'You exceeded your rate limit; retry later'}), 'rate_limit')
        self.assertEqual(oc.provider_error_reason('APIError', {'statusCode':429,'message':'insufficient credit balance'}), 'balance')
        self.assertEqual(oc.provider_error_reason('APIError', {'statusCode':402,'message':'quota'}), 'balance')

    def test_v1_v2_errors_deliver_fixed_localized_notes_and_metadata_only(self):
        from agentj.agent_opencode2 import OpenCodeV2Agent
        from types import SimpleNamespace
        raw='weekly limit reached CANARY-RAW-DO-NOT-DELIVER'
        for adapter in (oc.OpenCodeAgent, OpenCodeV2Agent):
            for language in ('zh','en'):
                with self.subTest(adapter=adapter.__name__,language=language):
                    a=adapter.__new__(adapter)
                    a.cfg={'language':language,'model':'fixture/model'}
                    a.host=SimpleNamespace(st=SimpleNamespace(log=Mock()))
                    a.sid='fixture';a.connected_at_start=['fixture'];a.provider_fail_noted=False
                    a.fail_notice=Mock()
                    if adapter is oc.OpenCodeAgent:
                        a.on_event({'type':'session.error','properties':{'sessionID':'fixture','error':
                                    {'name':'APIError','data':{'statusCode':429,'message':raw}}}})
                    else:
                        a._failed({'type':'api','status':429,'message':raw})
                    self.assertEqual(a.last_provider_fail['reason'],'quota_window')
                    self.assertEqual(a.fail_notice.call_args.args[0],oc.PROVIDER_NOTES['quota_window'].split(' / ')[0 if language=='zh' else -1])
                    self.assertNotIn('CANARY-RAW',str(a.host.st.log.call_args_list))
                    self.assertNotIn('CANARY-RAW',a.fail_notice.call_args.args[0])


class Resume(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        td=tempfile.TemporaryDirectory();self.addCleanup(td.cleanup)
        env=patch.dict(os.environ, {'HOME':td.name,'XDG_CONFIG_HOME':td.name+'/cfg'})
        env.start();self.addCleanup(env.stop)
        self.st=State(Path(td.name)/'st');self.st.init(relay='ws://127.0.0.1:1')
        self.h=Host(self.st);self.h._post=Mock();self.h.send_app=AsyncMock(return_value=True)
        self.h._bulk=AsyncMock();self.h.elevate.on_ready=AsyncMock()
        self.pub=b'a'*32;self.did=self.st.add_device(self.pub,'Phone')

    async def test_send_disconnect_committed_claude_completion_resume_new_cid(self):
        old=Session(1,state='ready',pub=self.pub,device=self.did,p33=True)
        self.h.sessions[1]=old
        turn=self.h.hist_add({'k':'phone','dev':self.did,'text':'question'},'', 'open')
        from agentj.compose import Send
        send=Send(device=self.did,sid='s'*22,text='question',turn=turn['id'])
        self.h.say_delivered(send)
        self.h.agent_turn_start('question',send)
        self.h.sessions.clear()  # disconnect before Claude's committed Stop
        a=shared.SharedClaudeAgent(self.h,{'kind':'claude','dir':str(self.st.root),'_workflow_ceo':True})
        a.phone_turn=a.phone_seen=True
        a._finish_stop('answer completed offline')
        self.h.agent_turn_end()
        # A later notice can move the last-id cursor beyond the mutable phone turn.
        last=self.h.hist_add({'k':'sys','text':''},'notice','done')['id']
        new=Session(9,state='hello',pub=self.pub,device=self.did,p33=True,hist={'epoch':self.h.hist.epoch,'last':last})
        self.h.sessions[9]=new
        with patch('agentj.service.platform',return_value='linux'):
            await self.h._accept_resume(new,0)
        turns=[c.args[1]['turn'] for c in self.h.send_app.call_args_list if c.args[1].get('t')=='hist_turn']
        reply=next((t for t in turns if t['id']==turn['id']),None)
        self.assertIsNotNone(reply,'last-id cursors must replay updates to already-known turns')
        self.assertEqual(reply['reply']['text'],'answer completed offline')
        self.assertEqual(reply['end'],'done')
        self.assertTrue(a.done.is_set())
        # A second resume is a snapshot of the same IDs, never duplicate history rows.
        await self.h.on_ready(new,0)
        self.assertEqual(self.h.hist.meta()['count'],2)


class WebsocketNoise(unittest.IsolatedAsyncioTestCase):
    async def test_actual_library_early_loss_callback_is_debug_only(self):
        from websockets.asyncio.connection import Connection
        from websockets.protocol import Protocol, CLIENT
        from websockets import __version__
        h=Host.__new__(Host);h._previous_exception_handler=None
        loop=asyncio.get_running_loop();before=loop.get_exception_handler()
        default=Mock();c=Connection(Protocol(CLIENT))
        try:
            loop.set_exception_handler(h.loop_exception_handler)
            with patch.object(loop,'default_exception_handler',default), self.assertLogs('agentj.serve',level='DEBUG') as logs:
                loop.call_soon(c.connection_lost,None)
                await asyncio.sleep(0);await asyncio.sleep(0)
            default.assert_not_called()
            self.assertEqual(len(logs.output),1);self.assertIn(__version__,logs.output[0])
            self.assertNotIn('Traceback',logs.output[0])
        finally:
            loop.set_exception_handler(before)

    async def test_actual_asyncio_transport_wrapper_early_loss_is_debug_only(self):
        from asyncio.selector_events import _SelectorTransport
        from websockets.asyncio.connection import Connection
        from websockets.protocol import Protocol, CLIENT
        h=Host.__new__(Host);h._previous_exception_handler=None
        loop=asyncio.get_running_loop();before=loop.get_exception_handler()
        transport=_SelectorTransport.__new__(_SelectorTransport)
        transport._protocol_connected=True
        transport._protocol=Connection(Protocol(CLIENT))
        transport._sock=Mock();transport._server=None
        try:
            loop.set_exception_handler(h.loop_exception_handler)
            with patch.object(loop,'default_exception_handler') as default, patch('agentj.serve.logging.getLogger') as logger:
                loop.call_soon(transport._call_connection_lost,None)
                await asyncio.sleep(0);await asyncio.sleep(0)
                default.assert_not_called()
                logger.return_value.debug.assert_called_once()
        finally:
            loop.set_exception_handler(before)
            if transport._sock is not None:
                transport._sock.close();transport._sock=None

    async def test_unrelated_errors_use_existing_handler(self):
        h=Host.__new__(Host);prior=Mock();h._previous_exception_handler=prior
        ctx={'exception':AttributeError('recv_messages'),'message':'application bug'}
        loop=asyncio.get_running_loop();h.loop_exception_handler(loop,ctx)
        prior.assert_called_once_with(loop,ctx)

    async def test_handler_is_restored_even_when_host_startup_fails(self):
        h=Host.__new__(Host);h._run=AsyncMock(side_effect=RuntimeError("fixture startup failed"))
        loop=asyncio.get_running_loop();previous=Mock();before=loop.get_exception_handler()
        try:
            loop.set_exception_handler(previous)
            with self.assertRaises(RuntimeError):
                await h.run()
            self.assertIs(loop.get_exception_handler(),previous)
        finally:
            loop.set_exception_handler(before)

    def test_doctor_and_dependency_cap(self):
        from websockets import __version__
        self.assertIn(__version__,doctor.check_websockets()['summary'])
        self.assertIn('websockets>=14,<18',(Path(__file__).resolve().parents[1]/'pyproject.toml').read_text())


class HotLanguage(unittest.IsolatedAsyncioTestCase):
    setUp = test_p76_update.Upgrade.setUp
    command = test_p76_update.Upgrade.command

    async def test_running_language_switch_then_signed_update_both_directions(self):
        # Exercise actual preference activation and signed /update, no installer side effects.
        for first,last in [('zh','en'),('en','zh')]:
            self.h.nonces=__import__('agentj.controls',fromlist=['Nonces']).Nonces()
            self.h.language_changed(first)
            raw=preferences.edit('{}','appearance.language',last)
            await self.h.apply_preferences(raw)
            self.assertEqual(preferences.get(self.h.preferences,'appearance.language'),last)
            with patch('agentj.update.check',return_value={'status':'current','latest':'0.17.3a1'}),patch.object(phone_update,'launch') as launch:
                await self.command(self.obj)
                progress=self.h.cmd_card.call_args.args[1].text
                self.assertIn("Checking and upgrading" if last=="en" else "正在查最新版",progress)
                self.assertNotIn("正在查最新版" if last=="en" else "Checking and upgrading",progress)
                await self.h.phone_upgrade_task
                launch.assert_not_called()
            text=self.h.cmd_card.call_args.args[1].text
            self.assertEqual(text,phone_update.text({'reason':'already_current','to':'0.17.3a1'},last))
