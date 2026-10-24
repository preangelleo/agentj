"""#175: native API dispatch, never a natural-language menu-command substitute."""
import _hermetic
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from agentj import harness_cmds
from agentj.agent_codex import CodexAgent


class Routes(unittest.TestCase):
    def test_headless_and_tui_are_distinguished(self):
        self.assertEqual(harness_cmds.route('codex','review'),'run')
        for kind in ('opencode','opencode2'):
            for name in ('init','review'): self.assertEqual(harness_cmds.route(kind,name),'run')
        for kind,name in (('codex','init'),('codex','diff'),('codex','approvals'),('opencode','sessions')):
            self.assertEqual(harness_cmds.route(kind,name),'none')
            self.assertTrue(harness_cmds.none_note(kind,name,'zh'))
        self.assertIsNone(harness_cmds.agent_text('/review','codex'))

    def test_opencode_native_shapes(self):
        from agentj.native_commands import opencode_request
        for v2 in (False,True):
            path,body=opencode_request('/review security focus','ses123','vendor/model',v2)
            self.assertEqual(path,('/api' if v2 else '')+'/session/ses123/command')
            self.assertEqual(body,{'name':'review','text':'security focus'} if v2 else
                             {'command':'review','arguments':'security focus','model':'vendor/model'})
            path,body=opencode_request('/init','ses123','vendor/model',v2)
            self.assertEqual(path,('/api/session/ses123/command' if v2 else '/session/ses123/init'))
        self.assertIsNone(opencode_request('please review','ses123',None,False))


class CodexReview(unittest.IsolatedAsyncioTestCase):
    async def test_turn_calls_review_start_and_waits_on_same_turn(self):
        a=object.__new__(CodexAgent)
        a._ready=AsyncMock(return_value=True);a.persist=False;a.cfg={};a.tid='thread-fixture'
        a.proc=object();a.tail_reset=Mock();a.revert=None;a.halting=False
        a.call=AsyncMock(return_value={'turn':{'id':'review-turn'}});a.await_turn=AsyncMock()
        async def deliver(fn): return await fn()
        a.deliver=deliver
        await a.turn('/review')
        a.call.assert_awaited_once_with('review/start',{'threadId':'thread-fixture','delivery':'inline','target':{'type':'uncommittedChanges'}})
        a.await_turn.assert_awaited_once();self.assertEqual(a.turn_id,'review-turn')

    async def test_custom_review_uses_native_custom_target(self):
        a=object.__new__(CodexAgent);a._ready=AsyncMock(return_value=True);a.persist=False;a.cfg={}
        a.tid='thread-fixture';a.proc=object();a.tail_reset=Mock();a.revert=None;a.halting=False
        a.call=AsyncMock(return_value={});a.await_turn=AsyncMock()
        async def deliver(fn): return await fn()
        a.deliver=deliver
        await a.turn('/review focus security')
        self.assertEqual(a.call.call_args.args[1]['target'],{'type':'custom','instructions':'focus security'})


class OpenCodeDispatch(unittest.IsolatedAsyncioTestCase):
    async def test_native_requests_keep_existing_serial_turn_and_no_replay(self):
        from agentj.agent_opencode import OpenCodeAgent
        from agentj.agent_opencode2 import OpenCodeV2Agent
        from agentj.shared_opencode2 import SharedOpenCodeV2Agent
        for cls in (OpenCodeAgent,OpenCodeV2Agent,SharedOpenCodeV2Agent):
            for command in ('/init','/review security focus'):
                for status in (200,404,'timeout'):
                    with self.subTest(adapter=cls.__name__,command=command,status=status):
                        h=SimpleNamespace(st=Mock(),lang='zh')
                        a=cls(h,{'kind':'opencode','dir':'/tmp','session_mode':'independent','model':'vendor/model'},persist=False)
                        a.proc=Mock(returncode=None);a.sid='ses_fixture'
                        a.client=SimpleNamespace(request=AsyncMock(return_value=(status,{})))
                        if status=='timeout': a.client.request.side_effect=asyncio.TimeoutError
                        a._protocol=AsyncMock(return_value=False);a._creds_check=AsyncMock()
                        a._ensure_started=AsyncMock(return_value=True);a._session_ok=AsyncMock(return_value=True)
                        a._identity=AsyncMock();a._model=AsyncMock();a._reminder=AsyncMock()
                        a._wait_turn=AsyncMock();a._catch_up=AsyncMock();a._kill=AsyncMock();a.fail_notice=Mock()
                        await a.turn(command)
                        self.assertEqual(a.client.request.await_count,1)
                        method,path,body=a.client.request.call_args.args
                        self.assertEqual(method,'POST');self.assertNotIn('prompt',path)
                        self.assertTrue(path.endswith('/init' if not a.v2 and command=='/init' else '/command'))
                        self.assertEqual(a._wait_turn.await_count,1 if status==200 else 0)


class NativePhone(unittest.TestCase):
    def test_exact_native_text_survives_composition(self):
        import tempfile
        from test_p412_native_cmds import _host
        with tempfile.TemporaryDirectory() as d:
            host,s,_=_host(d)
            host.agent=SimpleNamespace(kind='codex',init={},passthrough=lambda n:False,submit=Mock())
            host.onboarding_note=lambda:'\nmetadata is not review instructions'
            async def go():
                send=await host._accept(s,'/review focus security',None)
                self.assertEqual(send.text,'/review focus security')
            asyncio.run(go())


class NativeIdentity(unittest.TestCase):
    def test_v1_native_primary_keeps_existing_identity_and_permissions(self):
        import json
        from unittest.mock import patch
        from agentj.native_commands import main_config,NATIVE_AGENT
        cfg={'permission':{'*':'ask'},'agent':{'owner':{'prompt':'owner kept'}},'provider':{'fixture':{}}}
        with patch('agentj.main_identity.prompt',return_value='core-fixture'),patch('agentj.main_identity.turn_reminder',return_value='reminder-fixture'):
            out=json.loads(main_config(json.dumps(cfg),{}))
        self.assertEqual(out['permission'],cfg['permission']);self.assertEqual(out['agent']['owner'],cfg['agent']['owner'])
        self.assertEqual(out['agent'][NATIVE_AGENT]['prompt'],'core-fixturereminder-fixture')
        self.assertNotIn('permission',out['agent'][NATIVE_AGENT]);self.assertNotIn('tools',out['agent'][NATIVE_AGENT])


class NativeDelivery(unittest.IsolatedAsyncioTestCase):
    async def test_withdraw_waiting_for_send_lock_never_starts_native_request(self):
        from agentj import compose
        from agentj.agent import Withdrawn
        from agentj.agent_opencode import OpenCodeAgent
        from agentj.native_commands import request_v1
        host = SimpleNamespace(st=Mock(), lang='zh', say_uncertain=Mock(), say_delivered=Mock())
        agent = OpenCodeAgent(host, {'kind': 'opencode', 'dir': '/tmp'}, persist=False)
        send = compose.Send('device', 'sid', 1)
        sends = compose.Sends()
        sends.add(send)
        agent.cur_send = send
        entered = asyncio.Event()
        deliver = agent.deliver
        async def tracked_deliver(fn):
            entered.set()
            return await deliver(fn)
        agent.deliver = tracked_deliver
        async def request(*args, written, **kwargs):
            written.set()
            return 200, {}
        client = SimpleNamespace(request=AsyncMock(side_effect=request))
        await send.lock.acquire()
        cancel = asyncio.create_task(sends.cancel('device', 'sid'))
        await asyncio.sleep(0)  # cancellation queues first on the held lock
        task = asyncio.create_task(request_v1(agent, client, '/session/fixture/command', {}))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            send.lock.release()
            self.assertEqual((await cancel)[0], 'cancelled')
            with self.assertRaises(Withdrawn):
                await task
            client.request.assert_not_called()
            self.assertEqual(send.state, 'cancelled')
            host.say_delivered.assert_not_called()
        finally:
            if send.lock.locked():
                send.lock.release()
            await asyncio.gather(cancel, task, return_exceptions=True)

    async def test_sync_native_reply_never_holds_withdraw_lock_until_model_finishes(self):
        from agentj import compose
        from agentj.agent_opencode import OpenCodeAgent,Client
        received=asyncio.Event();release=asyncio.Event()
        async def backend(r,w):
            head=await r.readuntil(b'\r\n\r\n')
            length=next(int(x.partition(b':')[2]) for x in head.split(b'\r\n') if x.lower().startswith(b'content-length:'))
            await r.readexactly(length);received.set();await release.wait()
            w.write(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}');await w.drain();w.close();await w.wait_closed()
        server=await asyncio.start_server(backend,'127.0.0.1',0)
        host=SimpleNamespace(st=Mock(),lang='zh',say_uncertain=Mock(),say_delivered=Mock())
        a=OpenCodeAgent(host,{'kind':'opencode','dir':'/tmp','fence':False,'model':'vendor/model'},persist=False)
        send=compose.Send('device','sid',1);a.cur_send=send;a.proc=Mock(returncode=None);a.sid='ses_fixture'
        a.client=Client(server.sockets[0].getsockname()[1],'fixture')
        a._protocol=AsyncMock(return_value=False);a._creds_check=AsyncMock();a._ensure_started=AsyncMock(return_value=True);a._session_ok=AsyncMock(return_value=True)
        a._wait_turn=AsyncMock();a._catch_up=AsyncMock();a._kill=AsyncMock();a.fail_notice=Mock()
        task=asyncio.create_task(a.turn('/review'))
        try:
            await asyncio.wait_for(received.wait(),1)
            await asyncio.sleep(.02)
            self.assertFalse(send.lock.locked(),'native sync model response must not block the receiving lane on withdraw')
            self.assertEqual(send.state,'uncertain')  # bytes sent; native acceptance not yet confirmed
            sends=compose.Sends();sends.add(send)
            self.assertEqual((await asyncio.wait_for(sends.cancel('device','sid'),1))[0],'already_delivered')
            release.set();await task
            self.assertEqual(send.state,'delivered');host.say_delivered.assert_called_once_with(send)
        finally:
            release.set();await asyncio.gather(task,return_exceptions=True)
            server.close();await server.wait_closed()
