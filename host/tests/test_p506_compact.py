"""P506 #271: fixed self-compaction, real queue and Unix peer binding; no native inference."""
import _hermetic  # noqa: F401
import asyncio
import contextlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from agentj import capability, compactprep, provenance, self_compact as S
from agentj.agent import Agent, Cmd
from agentj.slash import Result
from agentj.state import State
from _testtmp import TEST_TMP

class Compact(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TEST_TMP); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.st = State(self.root/'st'); self.st.init()
        self.work = self.root/'work'; self.work.mkdir()
        self.host = SimpleNamespace(st=self.st, lang='en', stopped=lambda:False, turn_t0=123.0,
            cmd_turn=MagicMock(return_value=123), agent_status=MagicMock())
        self.agent = Agent(self.host, {'dir':str(self.work), 'session_mode':'independent'})
        self.agent.kind='claude'; self.agent.proc=SimpleNamespace(pid=os.getpid())
        self.host.agent=self.agent
        self.agent.cur_send=SimpleNamespace(source=provenance.phone(), created=time.monotonic())
        self.st.set_agent_session('claude', 'synthetic-session')
        compactprep.ensure_dir(str(self.work))
        (self.work/'.agentj/handover/compact.md').write_text('# Handoff\nSynthetic task and next step.\n')

    async def queue(self, req=None, pid=None):
        return await S.enqueue(self.host, {'t':'self_compact'} if req is None else req, os.getpid() if pid is None else pid)

    async def test_all_three_harnesses_use_the_fixed_queue_after_handoff(self):
        for kind in ('claude','codex','opencode'):
            self.agent.kind=kind; self.st.set_agent_session(kind, 'synthetic-session')
            self.assertEqual((await self.queue())['result'], 'queued')
            cmd=self.agent.q.get_nowait()
            self.assertEqual((cmd.name,cmd.arg), ('compact',''))
            self.assertIsNone(S.guard(self.agent, cmd))
        self.assertEqual(self.host.cmd_turn.call_count, 3)

    async def test_duplicate_request_does_not_queue_twice(self):
        await self.queue(); await self.queue()
        self.assertEqual(self.agent.q.qsize(),1)

    async def test_missing_handoff_and_unrelated_peer_are_refused(self):
        with patch.object(S.shared_hook,'ancestors',return_value=[99999]):
            self.assertEqual((await self.queue(pid=99999))['why'],'caller')
        (self.work/'.agentj/handover/compact.md').unlink()
        self.assertEqual((await self.queue())['why'],'handoff')
        self.assertTrue(self.agent.q.empty())

    async def test_no_target_or_other_slash_and_no_nonowner_source(self):
        self.assertEqual((await self.queue({'t':'self_compact','target':'other'}))['why'],'shape')
        self.agent.cur_send=SimpleNamespace(source=provenance.email_guest('guest@example.test'))
        self.assertEqual((await self.queue())['why'],'source')
        self.assertTrue(self.agent.q.empty())

    async def test_stale_session_or_an_intervening_turn_cannot_compact(self):
        await self.queue(); cmd=self.agent.q.get_nowait()
        self.st.set_agent_session('claude','new-session')
        self.assertEqual(S.guard(self.agent,cmd).kind,'refused')
        self.st.set_agent_session('claude','synthetic-session')
        self.host.turn_t0=456.0
        self.assertEqual(S.guard(self.agent,cmd).kind,'refused')

    async def test_real_agent_queue_checks_staleness_before_adapter(self):
        await self.queue(); self.st.set_agent_session('claude','new-session')
        done=asyncio.Event(); results=[]
        self.agent.command=AsyncMock(return_value=Result('unexpected','ok'))
        self.host.cmd_done=lambda cmd,res:(results.append(res),done.set())
        task=asyncio.create_task(self.agent.run())
        try:
            await asyncio.wait_for(done.wait(),2)
            self.agent.command.assert_not_called()
            self.assertEqual(results[0].kind,'refused')
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):await task

    async def test_real_capability_socket_checks_the_os_peer(self):
        manager=capability.Capabilities.__new__(capability.Capabilities); manager.host=self.host
        path=self.root/'cap.sock'
        server=await asyncio.start_unix_server(manager.on_client,path=str(path))
        try:
            reader,writer=await asyncio.open_unix_connection(str(path))
            writer.write(b'{"t":"self_compact"}\n');await writer.drain()
            res=json.loads(await asyncio.wait_for(reader.readline(),2))
            self.assertEqual(res['result'],'queued',res)
            writer.close();await writer.wait_closed()
        finally:
            server.close();await server.wait_closed()

    async def test_cli_requests_no_target_and_returns_without_waiting_for_compaction(self):
        import argparse
        parser=argparse.ArgumentParser(); sub=parser.add_subparsers(); capability.add_parser(sub)
        args=parser.parse_args(['capability','compact','--json'])
        with patch.object(capability,'client',return_value={'result':'queued'}) as client, patch('builtins.print'):
            self.assertEqual(capability.cmd_capability(args),0)
        client.assert_called_once_with({'t':'self_compact'})


    async def test_shared_claude_uses_existing_session_pid_and_shared_handoff(self):
        self.agent.cfg['session_mode']='shared'
        self.agent.proc=None; self.agent.session={'sessionId':'shared-session','pid':os.getpid()}
        folder=self.st.root/'handoff';folder.mkdir()
        (folder/'compact.md').write_text('# Shared handoff\nSynthetic task.\n')
        with patch.object(S.handoff,'path',side_effect=lambda name:folder/name), \
             patch.object(S.shared_hook,'ancestors',return_value=[os.getpid()]):
            self.assertEqual((await self.queue())['result'],'queued')
            self.assertIsNone(S.guard(self.agent,self.agent.q.get_nowait()))

    async def test_real_queue_executes_once_for_each_supported_adapter(self):
        for kind in ('claude','codex','opencode'):
            self.agent.kind=kind;self.st.set_agent_session(kind,'synthetic-session')
            await self.queue()
            done=asyncio.Event()
            self.agent.command=AsyncMock(return_value=Result('compacted','ok'))
            self.host.cmd_done=lambda cmd,res:done.set()
            task=asyncio.create_task(self.agent.run())
            try:
                await asyncio.wait_for(done.wait(),2)
                self.agent.command.assert_awaited_once_with('compact','')
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):await task

    async def test_stop_and_a_linked_handoff_are_refused(self):
        self.host.stopped=lambda:True
        self.assertEqual((await self.queue())['why'],'stopped')
        self.host.stopped=lambda:False
        path=self.work/'.agentj/handover/compact.md';path.unlink()
        other=self.root/'other.md';other.write_text('Synthetic handoff')
        path.symlink_to(other)
        self.assertEqual((await self.queue())['why'],'handoff')
        self.assertTrue(self.agent.q.empty())

    def nonowners(self):
        return (provenance.email_guest('guest@example.test'), {'kind':'friend_task','role':'task'},
                {'kind':'same_owner','role':'task'}, provenance.automation('external'),
                provenance.telegram_forward(123), provenance.telegram_group(-10,456,'proxy',123),
                provenance.system(), provenance.telegram_owner(123), provenance.email_owner('owner@example.test'), None, {})

    async def test_all_harnesses_reject_nonowner_and_external_turns(self):
        for kind in ('claude','codex','opencode'):
            self.agent.kind=kind; self.st.set_agent_session(kind,'synthetic-session')
            for src in self.nonowners():
                with self.subTest(kind=kind, source=src):
                    self.agent.cur_send=SimpleNamespace(source=src)
                    self.assertEqual((await self.queue())['why'],'source')
                    self.assertTrue(self.agent.q.empty())

    async def test_all_harnesses_reject_cleared_nonowner_sends(self):
        from agentj.agent_opencode import OpenCodeAgent
        for kind in ('claude','codex','opencode'):
            self.agent.kind=kind; self.st.set_agent_session(kind,'synthetic-session')
            for src in self.nonowners():
                with self.subTest(kind=kind, source=src):
                    self.agent.cur_send=SimpleNamespace(source=src)
                    if kind=='opencode':
                        OpenCodeAgent._note_turn(self.agent)
                    self.agent.cur_send=None
                    self.assertEqual((await self.queue())['why'],'source')
                    self.assertTrue(self.agent.q.empty())

    async def test_unknown_origin_is_denied_for_every_harness(self):
        for kind in ('claude','codex','opencode'):
            self.agent.kind=kind; self.st.set_agent_session(kind,'synthetic-session')
            self.agent.cur_send=None
            for src in (None, {}, 'owner'):
                with self.subTest(kind=kind, source=src):
                    self.agent.out_src=src
                    self.assertEqual((await self.queue())['why'],'source')
                    self.assertTrue(self.agent.q.empty())

    async def test_explicit_owner_channels_and_retained_opencode_owner_work(self):
        for kind in ('claude','codex','opencode'):
            self.agent.kind=kind; self.st.set_agent_session(kind,'synthetic-session')
            for src in (provenance.phone(),):
                with self.subTest(kind=kind, source=src):
                    self.agent.cur_send=SimpleNamespace(source=src)
                    self.assertEqual((await self.queue())['result'],'queued')
                    cmd=self.agent.q.get_nowait()
                    self.assertIsNone(S.guard(self.agent,cmd))
        from agentj.agent_opencode import OpenCodeAgent
        self.agent.cur_send=SimpleNamespace(source=provenance.phone())
        OpenCodeAgent._note_turn(self.agent); self.agent.cur_send=None
        self.assertEqual((await self.queue())['result'],'queued')
        self.assertIsNone(S.guard(self.agent,self.agent.q.get_nowait()))

    async def test_owner_queue_executes_after_send_is_cleared(self):
        for kind in ('claude','codex','opencode'):
            self.agent.kind=kind; self.st.set_agent_session(kind,'synthetic-session')
            self.agent.cur_send=SimpleNamespace(source=provenance.phone())
            if kind=='opencode':
                from agentj.agent_opencode import OpenCodeAgent
                OpenCodeAgent._note_turn(self.agent)
            self.assertEqual((await self.queue())['result'],'queued')
            self.agent.cur_send=None
            done=asyncio.Event(); results=[]
            self.agent.command=AsyncMock(return_value=Result('compacted','ok'))
            self.host.cmd_done=lambda cmd,res:(results.append(res),done.set())
            task=asyncio.create_task(self.agent.run())
            try:
                await asyncio.wait_for(done.wait(),2)
                self.agent.command.assert_awaited_once_with('compact','')
                self.assertEqual(results[0].kind,'ok')
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError): await task

    async def test_execution_guard_rejects_retained_nonowner_and_unknown_sources(self):
        for kind in ('claude','codex','opencode'):
            self.agent.kind=kind; self.st.set_agent_session(kind,'synthetic-session')
            for src in self.nonowners()+(None,):
                with self.subTest(kind=kind, source=src):
                    self.agent.cur_send=SimpleNamespace(source=provenance.phone())
                    self.assertEqual((await self.queue())['result'],'queued')
                    cmd=self.agent.q.get_nowait(); self.agent.cur_send=None
                    if kind=='opencode':
                        from agentj.agent_opencode import OpenCodeAgent
                        self.agent.cur_send=SimpleNamespace(source=src) if src is not None else None
                        OpenCodeAgent._note_turn(self.agent); self.agent.cur_send=None
                    else: self.agent.pending_source=src if src is not None else {}
                    self.assertEqual(S.guard(self.agent,cmd).kind,'refused')

    async def test_external_request_context_cannot_borrow_owner_send(self):
        for kind in ('claude','codex','opencode'):
            self.agent.kind=kind; self.st.set_agent_session(kind,'synthetic-session')
            for src in self.nonowners()[:6]:
                with self.subTest(kind=kind, source=src), provenance.request_source(src):
                    self.assertEqual((await self.queue())['why'],'source')
                    self.assertTrue(self.agent.q.empty())

    async def test_shared_pending_nonowner_and_missing_queued_owner_are_denied(self):
        self.agent.phone_turn=True
        self.agent.cur_send=None; self.agent.pending_source=provenance.email_guest('guest@example.test')
        self.assertEqual((await self.queue())['why'],'source')
        self.agent.pending_source=provenance.phone()
        self.assertEqual((await self.queue())['result'],'queued')
        cmd=self.agent.q.get_nowait(); cmd.data.pop('self_compact_source',None)
        self.assertEqual(S.guard(self.agent,cmd).kind,'refused')

    async def test_opencode_internal_turn_without_send_never_creates_an_owner(self):
        from agentj.agent_opencode import OpenCodeAgent
        from agentj.agent_opencode2 import OpenCodeV2Agent
        self.agent.kind='opencode'; self.st.set_agent_session('opencode','synthetic-session')
        for cls in (OpenCodeAgent,OpenCodeV2Agent):
            for send in (None,SimpleNamespace()):
                with self.subTest(adapter=cls.__name__, send=send):
                    self.agent.cur_send=send
                    cls._note_turn(self.agent)
                    self.agent.cur_send=None
                    self.assertEqual((await self.queue())['why'],'source')
                    self.assertTrue(self.agent.q.empty())

    async def test_readback_none_source_is_refused_by_all_harnesses_and_retention(self):
        from agentj.agent_opencode import OpenCodeAgent
        from agentj.agent_opencode2 import OpenCodeV2Agent
        for kind in ('claude', 'codex', 'opencode'):
            self.agent.kind = kind
            self.st.set_agent_session(kind, 'synthetic-session')
            self.agent.cur_send = capability.ReadBackSend('Synthetic workflow result')
            self.assertEqual(self.agent.cur_send.source, provenance.system())
            with self.subTest(kind=kind, phase='active'):
                self.assertEqual((await self.queue()).get('why'), 'source')
                self.assertTrue(self.agent.q.empty())
            if kind == 'opencode':
                for cls in (OpenCodeAgent, OpenCodeV2Agent):
                    self.agent.cur_send = capability.ReadBackSend('Synthetic workflow result')
                    cls._note_turn(self.agent)
                    self.agent.cur_send = None
                    with self.subTest(adapter=cls.__name__, phase='retained'):
                        self.assertEqual((await self.queue()).get('why'), 'source')
                        self.assertTrue(self.agent.q.empty())

    async def test_guard_refuses_readback_after_owner_queued_compaction(self):
        from agentj.agent_opencode import OpenCodeAgent
        for kind in ('claude', 'codex', 'opencode'):
            self.agent.kind = kind
            self.st.set_agent_session(kind, 'synthetic-session')
            self.agent.cur_send = SimpleNamespace(source=provenance.phone())
            self.assertEqual((await self.queue())['result'], 'queued')
            cmd = self.agent.q.get_nowait()
            self.agent.cur_send = capability.ReadBackSend('Synthetic workflow result')
            with self.subTest(kind=kind, phase='active'):
                self.assertIsNotNone(S.guard(self.agent, cmd))
            if kind == 'opencode':
                OpenCodeAgent._note_turn(self.agent)
                self.agent.cur_send = None
                self.assertIsNotNone(S.guard(self.agent, cmd))

    async def test_paired_phone_accept_records_explicit_source(self):
        from agentj import serve
        from test_l1 import Phone, _host, _state
        st = _state(str(self.root / 'phone-state'))
        phone = Phone(st)
        host = _host(st, [])
        host.agent = SimpleNamespace(kind='claude', submit=MagicMock())
        session = serve.Session(cid=11, state='ready', device=phone.did, name='Phone', pub=phone.pub, p33=True, provenance=provenance.phone())
        host.sessions[11] = session
        send = await host._accept(session, 'Synthetic owner request', None)
        self.assertEqual(send.source, provenance.phone())
        self.assertTrue(S.owner_channel(send.source))

    async def test_external_or_unpaired_accept_never_invents_phone_provenance(self):
        from agentj import serve
        from test_l1 import Phone, _host, _state
        st = _state(str(self.root / 'ingress-state'))
        phone = Phone(st)
        host = _host(st, [])
        host.agent = SimpleNamespace(kind='claude', submit=MagicMock())
        for variant in ('unregistered', 'external', 'unpaired'):
            session = serve.Session(cid=11, state='ready', device=phone.did, name='Synthetic', pub=phone.pub, p33=True)
            host.sessions.clear()
            if variant != 'unregistered':
                host.sessions[11] = session
            if variant == 'external':
                session.source_kind = 'telegram'
            if variant == 'unpaired':
                session.pub = bytes(32)
            with self.subTest(variant=variant):
                send = await host._accept(session, 'Synthetic request', None)
                self.assertIsNone(send.source)
                self.assertFalse(S.owner_channel(send.source))

    async def test_queued_capture_cannot_override_new_native_or_request_source(self):
        for source in (provenance.system(), provenance.normalize(None), provenance.telegram_owner(1)):
            self.agent.cur_send=SimpleNamespace(source=provenance.phone())
            self.assertEqual((await self.queue())['result'],'queued')
            cmd=self.agent.q.get_nowait();self.agent.cur_send=None
            self.agent.native_source=source
            self.assertEqual(S.guard(self.agent,cmd).kind,'refused')
        self.agent.native_source=None
        self.agent.cur_send=SimpleNamespace(source=provenance.phone())
        await self.queue();cmd=self.agent.q.get_nowait();self.agent.cur_send=None
        with provenance.request_source(provenance.system()):
            self.assertEqual(S.guard(self.agent,cmd).kind,'refused')
