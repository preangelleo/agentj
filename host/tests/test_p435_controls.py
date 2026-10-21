import _hermetic  # noqa
import asyncio
import pathlib
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest import mock
from agentj import phone_restart, reset, serve
from test_controls import sign
from test_l1 import Phone, _host, _ready, _state


class Restart(unittest.IsolatedAsyncioTestCase):
    def host(self, kind='claude', mode='independent'):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        st = _state(tmp.name); st.set_agent_session(kind, 'old-session')
        old = NS(kind=kind, cfg={'kind':kind, 'dir':tmp.name, 'session_mode':mode, 'fence':True}, stop=mock.AsyncMock())
        h = NS(agent=old, st=st, turn_lock=asyncio.Lock(), stopped=lambda:False)
        return h, old

    async def test_each_harness_resumes_without_changing_permissions(self):
        for kind in ('claude', 'codex', 'opencode'):
            h, old = self.host(kind)
            a = NS(kind=kind, cfg=dict(old.cfg), stop=mock.AsyncMock(), start=mock.Mock(), set_status=mock.Mock())
            with mock.patch.object(phone_restart.agent,'make',return_value=a), mock.patch.object(phone_restart,'prepare',new_callable=mock.AsyncMock) as prep:
                self.assertEqual(await phone_restart.run(h), {'ok':True,'new':False})
            prep.assert_awaited_once_with(a, 'old-session'); old.stop.assert_awaited_once()
            self.assertTrue(a.cfg['fence']); self.assertEqual(h.st.agent_session(kind),'old-session')

    async def test_failed_resume_creates_new_and_keeps_undo(self):
        h, old = self.host('codex','shared')
        def make(_h,cfg):
            return NS(kind='codex',cfg=dict(cfg),stop=mock.AsyncMock(),start=mock.Mock(),set_status=mock.Mock())
        with mock.patch.object(phone_restart.agent,'make',side_effect=make), mock.patch.object(phone_restart,'prepare',new_callable=mock.AsyncMock, side_effect=[ValueError(),None]) as prep:
            self.assertEqual(await phone_restart.run(h), {'ok':True,'new':True})
        self.assertEqual(prep.await_args_list[1].args[1],None)
        self.assertEqual(h.agent.cfg['shared_session_id'],'')
        self.assertIn('old-session',(h.st.root/reset.UNDO).read_text())

    async def test_estop_and_cancellation_do_not_restart(self):
        h, old = self.host(); h.stopped=lambda:True
        self.assertEqual((await phone_restart.run(h))['why'],'estop'); old.stop.assert_not_awaited()
        h.stopped=lambda:False
        a = NS(kind='claude',cfg=old.cfg,stop=mock.AsyncMock(),start=mock.Mock())
        with mock.patch.object(phone_restart.agent,'make',return_value=a), mock.patch.object(phone_restart,'prepare',new_callable=mock.AsyncMock,side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError): await phone_restart.run(h)
        a.start.assert_not_called(); a.stop.assert_awaited_once()

    async def test_unverified_shared_pid_never_signalled(self):
        h, old = self.host('claude','shared'); old.session={'pid':999999,'sessionId':'selected'}; old.native=None
        with mock.patch('agentj.shared.claude_sessions',return_value=[]), mock.patch.object(phone_restart.os,'kill') as kill:
            with self.assertRaises(ValueError): await phone_restart.end_selected(old)
        kill.assert_not_called()

    async def test_prepare_uses_native_resume_and_new_pin(self):
        a=NS(kind='claude',cfg={'session_mode':'shared','dir':'/tmp'},native=NS(pid=44),ordinary_start=mock.AsyncMock(),_repin=mock.Mock(),attach=mock.AsyncMock(return_value=True))
        with mock.patch('agentj.shared.claude_sessions',return_value=[{'pid':44,'sessionId':'selected'}]):
            await phone_restart.prepare(a,'selected')
        a.ordinary_start.assert_awaited_once_with(resume='selected', extra_flags=[]); a._repin.assert_called_once()
        for kind,fn in [('claude','_ensure'),('codex','_thread'),('opencode','_session_ok')]:
            a=NS(kind=kind,cfg={},_spawn=mock.AsyncMock(return_value=True)); setattr(a,fn,mock.AsyncMock(return_value=True))
            await phone_restart.prepare(a,'selected'); getattr(a,fn).assert_awaited_once()

    async def test_estop_during_prepare_wins(self):
        h, old = self.host(); stopped = [False]; h.stopped=lambda:stopped[0]
        a = NS(kind='claude',cfg=old.cfg,stop=mock.AsyncMock(),start=mock.Mock())
        async def prepare(*_): stopped[0]=True
        with mock.patch.object(phone_restart.agent,'make',return_value=a), mock.patch.object(phone_restart,'prepare',side_effect=prepare):
            self.assertEqual((await phone_restart.run(h))['why'],'estop')
        a.start.assert_not_called(); a.stop.assert_awaited_once()

    async def test_exact_selected_process_exits_other_process_survives(self):
        import sys
        from agentj.shared import _same_process
        children=[]
        try:
            for _ in range(2): children.append(await asyncio.create_subprocess_exec(sys.executable,'-c','import time; time.sleep(30)'))
            target, other=children
            async def reap(): return await target.wait()
            waiter=asyncio.create_task(reap())
            stat=pathlib.Path(f'/proc/{target.pid}/stat').read_text()
            rec={'pid':target.pid,'sessionId':'selected','procStart':stat[stat.rfind(')')+2:].split()[19]}
            self.assertTrue(_same_process(rec))
            a=NS(cfg={'dir':'/tmp'},session=rec,native=None)
            with mock.patch('agentj.shared.claude_sessions',return_value=[rec]): await phone_restart.end_selected(a)
            await waiter
            self.assertIsNotNone(target.returncode); self.assertIsNone(other.returncode)
        finally:
            for p in children:
                if p.returncode is None:p.kill()
                await p.wait()

    def test_native_restrictions_are_kept_without_replaying_prompt_or_routing(self):
        args=['claude','--resume','old','--restricted','--permission-mode','plan',
              '--allowedTools','Read','Bash(ls *)','--disallowed-tools=Write',
              '--settings','local.json','--setting-sources','project',
              '--strict-mcp-config','--mcp-config','first.json','second.json','--','--tools']
        self.assertEqual(phone_restart.native_flags(args),args[3:-2])
        with self.assertRaises(ValueError): phone_restart.native_flags(['claude','--permission-mode'])

    async def test_restrictions_survive_resume_failure_and_queue_is_closed(self):
        h, old = self.host('claude','shared')
        old.session={'sessionId':'old-session'}; old.q=asyncio.Queue(); old.cur_send=NS(turn=8,state='delivered')
        old.q.put_nowait(NS(turn=9));h.cur_turn=8;h.queue_dropped=mock.Mock();h.agent_turn_end=mock.Mock()
        h.grants={'old':1};h.end_grant=mock.Mock();h._parked_ok={'old':True}
        flags=['--restricted','--permission-mode','plan','--tools','Read']
        made=[]
        def make(_h,cfg):
            a=NS(kind='claude',cfg=dict(cfg),stop=mock.AsyncMock(),start=mock.Mock(),set_status=mock.Mock())
            made.append(a);return a
        with mock.patch.object(phone_restart,'profile',return_value=flags),mock.patch.object(phone_restart,'end_selected',new_callable=mock.AsyncMock),mock.patch.object(phone_restart.agent,'make',side_effect=make),mock.patch.object(phone_restart,'prepare',new_callable=mock.AsyncMock,side_effect=[ValueError(),None]):
            self.assertTrue((await phone_restart.run(h))['new'])
        self.assertTrue(old.halting);self.assertEqual(h.queue_dropped.call_count,1)
        h.agent_turn_end.assert_called_once();h.end_grant.assert_called_once_with('old','turn_end')
        self.assertFalse(hasattr(h,'_parked_ok'));self.assertTrue(all(a._phone_restart_flags==flags for a in made))
        self.assertTrue(all('_phone_restart_flags' not in a.cfg for a in made))

    async def test_arrivals_during_stop_follow_new_actor_and_picked_pending_is_cancelled(self):
        h, old = self.host();old.q=asyncio.Queue()
        picked=NS(state='pending',turn=4);h.sends=NS(items={('d','s'):picked});h.queue_dropped=mock.Mock()
        async def stop():old.q.put_nowait('new owner message')
        old.stop=mock.AsyncMock(side_effect=stop)
        a=NS(kind='claude',cfg=old.cfg,stop=mock.AsyncMock(),start=mock.Mock(),set_status=mock.Mock())
        with mock.patch.object(phone_restart.agent,'make',return_value=a),mock.patch.object(phone_restart,'prepare',new_callable=mock.AsyncMock):
            self.assertTrue((await phone_restart.run(h))['ok'])
        self.assertIs(a.q,old.q);self.assertEqual(a.q.get_nowait(),'new owner message')
        h.queue_dropped.assert_called_once_with(picked)

    async def test_failed_reopen_leaves_a_fresh_actor_for_next_owner_message(self):
        h, old = self.host();old.q=asyncio.Queue();made=[]
        def make(_h,cfg):
            a=NS(kind='claude',cfg=dict(cfg),stop=mock.AsyncMock(),start=mock.Mock(),set_status=mock.Mock())
            made.append(a);return a
        with mock.patch.object(phone_restart.agent,'make',side_effect=make),mock.patch.object(phone_restart,'prepare',new_callable=mock.AsyncMock,side_effect=ValueError()):
            self.assertFalse((await phone_restart.run(h))['ok'])
        self.assertEqual(len(made),3);made[-1].start.assert_called_once();self.assertIs(made[-1].q,old.q)

    async def test_unknown_dead_native_profile_is_not_resumed_with_broader_defaults(self):
        a=NS(cfg={'dir':'/tmp'},session=None,native=None)
        with mock.patch('agentj.shared.claude_sessions',return_value=[]):
            with self.assertRaises(ValueError):phone_restart.profile(a,'old-session')
        a.session={'pid':41,'sessionId':'old-session'};a.native=NS(pid=41,returncode=0)
        a._phone_restart_flags=['--restricted']
        self.assertEqual(phone_restart.profile(a,'old-session'),['--restricted'])
        with mock.patch('agentj.shared.claude_sessions',side_effect=AssertionError('must not inspect unrelated processes')):
            await phone_restart.end_selected(a)


class Signed(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.st=_state(self.tmp.name); self.sent=[]
        self.ph=Phone(self.st); self.h=_host(self.st,self.sent); self.s=_ready(self.h,self.ph); self.s.p33=True

    def test_signed_restart_and_replay_rejection(self):
        async def go():
            m=sign(self.ph,'rescue_do',{'action':'restart_session'},{'t':'rescue_do','r':'ab'*8,'action':'restart_session'})
            with mock.patch.object(reset.Desk,'start') as start:
                await self.h._app(self.s, {**m,'sig':'bad'}); start.assert_not_called()
                await self.h._app(self.s,m); start.assert_called_once_with(self.ph.did,'ab'*8,'restart_session')
                await self.h._app(self.s,m); self.assertEqual(start.call_count,1)
        asyncio.run(go())
        self.assertEqual(self.sent[-1][1]['why'],'replay')

    def test_signed_resume_restarts_dormant_actor_once(self):
        async def go():
            actor=NS(kind='claude',status='down',task=None,halting=False)
            def start():actor.task=NS(done=lambda:False)
            actor.start=mock.Mock(side_effect=start);self.h.agent=actor;self.h.estop={'on':True}
            with mock.patch.object(self.h,'status_changed'):
                for n in range(2):
                    await self.h._app(self.s,sign(self.ph,'resume',{}, {'t':'resume','r':str(n)}))
            actor.start.assert_called_once();self.assertFalse(self.h.stopped())
        asyncio.run(go())

    def test_resume_requires_passkey_and_valid_assertion(self):
        async def go():
            self.h.estop={'on':True}
            m=sign(self.ph,'resume',{}, {'t':'resume','r':'ab'*8})
            pk={'id':'credential'}
            with mock.patch.object(self.st,'passkey_of_device',return_value=pk), mock.patch.object(self.h,'do_resume',new_callable=mock.AsyncMock) as resume:
                await self.h._app(self.s,m)
                self.assertEqual(self.sent[-1][1]['why'],'passkey_required'); resume.assert_not_awaited()
                with mock.patch.object(serve.passkey,'approval_fields',return_value={'id':'credential'}),mock.patch.object(serve.passkey,'verify_assertion',return_value=1),mock.patch.object(self.st,'passkey_count'):
                    await self.h._app(self.s,{**m,'fa':{'test':True}})
                    resume.assert_awaited_once()
        asyncio.run(go())
