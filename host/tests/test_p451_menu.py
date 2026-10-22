"""Native Claude modal regressions; only synthetic screens and owned PTYs."""
import _hermetic
import asyncio
import os
import pty
import tty
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, patch
from agentj import shared, shared_menu as menu

REWIND = 'Rewind\nRestore the code and/or conversation to the point before…\nEnter to continue · Esc to cancel'
QUOTA = "You've hit your session limit · resets 6:50pm\nWhat do you want to do?\n❯ 1. Stop and wait\n  2. Don't continue\n  3. Use extra usage credits\n  4. Upgrade your plan\nEnter to confirm · Esc to cancel"

class Parse(unittest.TestCase):
    def test_rewind_and_quota_positive_and_negative(self):
        self.assertEqual(menu.detect(REWIND)['kind'], 'rewind')
        q=menu.detect(QUOTA)
        self.assertEqual(q['reset'], '6:50pm')
        self.assertEqual(q['keys'], ['1','2','3','4'])
        self.assertIsNone(menu.detect('Earlier the tool printed:\n'+QUOTA+'\n❯ hello'))
        self.assertIsNone(menu.detect('Do you want to allow Bash?\n❯ 1. Yes\n2. No\nEsc to cancel'))
        self.assertIsNone(menu.detect('normal assistant response about Rewind'))
        self.assertEqual(menu.detect('Select model\nOpus\nEsc to cancel')['kind'],'menu')
        self.assertIsNone(menu.detect(QUOTA.replace('resets 6:50pm','resets synthetic-secret')))
    def test_incremental_screen_repaint_removes_old_modal(self):
        s=menu.Screen()
        for b in ('\x1b[2J\x1b[H'+REWIND).encode(): s.feed(bytes([b]))
        self.assertEqual(menu.detect(s.text())['kind'], 'rewind')
        s.feed(b'\x1b[2J\x1b[HReady\r\n> ')
        self.assertIsNone(menu.detect(s.text()))
    def test_bilingual_only_fixed_labels_and_bounded_reset_leave_host(self):
        for lang in ('zh','en'):
            q=menu.questions(menu.detect(QUOTA),lang)
            self.assertIn('6:50pm',q[0]['q'])
            self.assertEqual(len(q[0]['o']),5)
            self.assertIn('Esc',q[0]['o'][-1]['l'])

class Routing(unittest.IsolatedAsyncioTestCase):
    def actor(self):
        a=SimpleNamespace(host=SimpleNamespace(lang='en',st=SimpleNamespace(log=Mock()),question=AsyncMock(),
            agent_notice=Mock(),turn_queued_note=Mock()), session={'sessionId':'fixture','pid':17},native=None,master=None,
            native_lock=asyncio.Lock(),state=SimpleNamespace(status='working'),phone_turn=False,phone_claim=False,
            phone_seen=False,done=asyncio.Event(),status='working')
        a.host.stopped=lambda:False
        return a
    async def test_quota_is_a_signed_question_and_revalidates_before_key(self):
        a=self.actor(); d=menu.Desk(a)
        a.host.question.return_value=('answer',[[2]])
        with patch.object(d,'snapshot',new=AsyncMock(return_value=(menu.detect(QUOTA),'pane'))),patch.object(d,'send',new=AsyncMock(return_value=True)) as send:
            await d.poll(); await d.task
            a.host.question.assert_awaited_once()
            send.assert_awaited_once()
            self.assertEqual(send.call_args.args[1], '2')
    async def test_changed_screen_or_session_refuses_stale_answer(self):
        for changed in ('screen','session','estop','approval'):
            a=self.actor();d=menu.Desk(a)
            async def answer(*args,**kwargs):
                if changed=='session': a.session={'sessionId':'other','pid':18}
                if changed=='estop': a.host.stopped=lambda:True
                if changed=='approval': a.state.status='waiting'
                return 'answer',[[1]]
            a.host.question.side_effect=answer
            snap=AsyncMock(side_effect=[(menu.detect(QUOTA),'pane'),(None if changed=='screen' else menu.detect(QUOTA),'pane')])
            with patch.object(d,'snapshot',new=snap),patch.object(d,'send',new=AsyncMock()) as send:
                await d.poll();await d.task;send.assert_not_called()
    async def test_rewind_auto_escape_once_and_no_enter(self):
        a=self.actor(); d=menu.Desk(a)
        with patch.object(d,'snapshot',new=AsyncMock(return_value=(menu.detect(REWIND),'pane'))),patch.object(d,'send',new=AsyncMock(return_value=True)) as send:
            await d.poll();await d.task;await d.poll()
            self.assertEqual(send.await_count,1)
            self.assertEqual(send.call_args.args[1],'esc')
            a.host.agent_notice.assert_called_once()
    async def test_desktop_close_withdraws_card_and_marks_no_false_completion(self):
        a=self.actor(); d=menu.Desk(a)
        async def wait(qs,gone,**kwargs):
            await gone
            return 'gone',None
        a.host.question.side_effect=wait
        with patch.object(d,'snapshot',new=AsyncMock(side_effect=[(menu.detect(QUOTA),'pane'),(None,'pane')])),patch.object(d,'send',new=AsyncMock()) as send:
            await d.poll();await asyncio.sleep(0);await d.poll();await d.task
            self.assertIsNone(d.current);send.assert_not_called()
    async def test_drain_retains_current_modal_instead_of_discarding_bytes(self):
        a=self.actor();a.menu_desk=menu.Desk(a)
        r,w=os.pipe();a.master=r
        try:
            os.write(w,('\x1b[2J\x1b[H'+REWIND).encode())
            shared.SharedClaudeAgent._drain_terminal(a)
            self.assertEqual(menu.detect(a.menu_desk.surface.text())['kind'],'rewind')
            os.write(w,('\x1b[2J\x1b[H'+QUOTA.replace('\n','\r\n')).encode())
            shared.SharedClaudeAgent._drain_terminal(a)
            self.assertEqual(menu.detect(a.menu_desk.surface.text())['kind'],'quota')
        finally: os.close(r);os.close(w)
    async def test_drain_retains_limit_modal_without_hook(self):
        a=self.actor();a.menu_desk=menu.Desk(a)
        r,w=os.pipe();a.master=r
        try:
            os.write(w,('\x1b[2J\x1b[H'+QUOTA.replace('\n','\r\n')).encode())
            shared.SharedClaudeAgent._drain_terminal(a)
            self.assertEqual(menu.detect(a.menu_desk.surface.text())['reset'],'6:50pm')
        finally: os.close(r);os.close(w)
    async def test_control_gate_cannot_type_clear_into_modal(self):
        a=object.__new__(shared.SharedClaudeAgent)
        a.menu_desk=SimpleNamespace(current=menu.detect(QUOTA))
        a.state=SimpleNamespace(status='idle');a.native_turn=False;a.phone_turn=False
        self.assertEqual(a._control_gate('idle'),(False,'menu'))
    async def test_unreadable_screen_does_not_claim_menu_closed(self):
        a=self.actor();d=menu.Desk(a)
        a.host.question.return_value=('timeout',None)
        with patch.object(d,'snapshot',new=AsyncMock(side_effect=[(menu.detect(QUOTA),'pane'),(False,'pane')])):
            await d.poll();await d.task;await d.poll()
            self.assertEqual(d.current['kind'],'quota')
    async def test_timeout_reoffers_card_and_escape_updates_stop_debounce(self):
        a=self.actor();d=menu.Desk(a)
        a.host.question.return_value=('timeout',None)
        with patch.object(d,'snapshot',new=AsyncMock(return_value=(menu.detect(QUOTA),'pane'))):
            await d.poll();await d.task
            d.retry_at=0
            await d.poll();await d.task
            self.assertEqual(a.host.question.await_count,2)
        d=menu.Desk(a)
        with patch.object(d,'snapshot',new=AsyncMock(return_value=(menu.detect(REWIND),'pane'))),patch.object(d,'send',new=AsyncMock(return_value=True)):
            await d.poll();await d.task
            self.assertGreater(a._stop_esc_at,0)
    async def test_owned_pty_real_escape_and_number_bytes(self):
        a=self.actor();a.native=SimpleNamespace(pid=17,returncode=None)
        master,slave=pty.openpty();tty.setraw(slave);a.master=master
        try:
            d=menu.Desk(a)
            self.assertTrue(await d.send(None,'esc'));self.assertEqual(os.read(slave,10),b'\x1b')
            d.surface.feed(('\x1b[2J\x1b[H'+QUOTA.replace('\n','\r\n')).encode())
            self.assertTrue(await d.send(None,'2'));self.assertEqual(os.read(slave,10),b'\x1b[B\r')
            self.assertFalse(await d.send(None,'9'));self.assertFalse(await d.send(None,'enter'))
        finally: os.close(master);os.close(slave)

class SignedCard(unittest.IsolatedAsyncioTestCase):
    async def test_unsigned_refused_signed_pick_and_replay_once(self):
        import tempfile
        from agentj import approvals,wire
        from test_l1 import Phone,_state,_host,_ready
        with tempfile.TemporaryDirectory() as root:
            st=_state(root);sent=[];host=_host(st,sent);phone=Phone(st)
            session=_ready(host,phone);session.p33=True
            a=Routing().actor();a.host=host;a.kind='claude';host.agent=a
            a.status='working'
            d=menu.Desk(a);a.menu_desk=d
            with patch.object(d,'snapshot',new=AsyncMock(return_value=(menu.detect(QUOTA),'pane'))),patch.object(d,'send',new=AsyncMock(return_value=True)) as send:
                await d.poll()
                for _ in range(20):
                    if host.questions: break
                    await asyncio.sleep(.005)
                q=next(iter(host.questions.values()))
                bad={'t':'q_answer','id':q.qid,'pick':[[2]]}
                host._q_answer(session,bad)
                self.assertFalse(q.fut.done());send.assert_not_called()
                sig=wire.b64u(phone.sk.sign(approvals.question_message(host.channel,phone.did,q.qid,'answer',q.qs,[[2]])))
                host._q_answer(session,{**bad,'sig':sig})
                await d.task
                self.assertEqual(send.await_count,1)
                host._q_answer(session,{**bad,'sig':sig})
                self.assertEqual(send.await_count,1)
                self.assertFalse(host.questions)
                self.assertIn('"decision": "answer"',st.approvals_path.read_text())

class Lifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_reopening_actor_withdraws_existing_question_and_old_task_cannot_type(self):
        import tempfile
        from test_l1 import Phone,_state,_host,_ready
        with tempfile.TemporaryDirectory() as root:
            host=_host(_state(root),[]);phone=Phone(host.st);s=_ready(host,phone);s.p33=True
            a=Routing().actor();a.host=host;a.kind='claude';host.agent=a
            d=menu.Desk(a)
            sent=[]
            async def capture(session,obj): sent.append(obj);return True
            host.send_app=capture
            with patch.object(d,'snapshot',new=AsyncMock(return_value=(menu.detect(QUOTA),'pane'))):
                await d.poll()
                for _ in range(20):
                    if host.questions: break
                    await asyncio.sleep(.005)
                self.assertTrue(host.questions)
                await d.stop();await asyncio.sleep(.01)
                self.assertFalse(host.questions)
                self.assertTrue(any(x.get('t')=='question_done' for x in sent))
                self.assertFalse(await d.send('pane','esc'))

class External(unittest.IsolatedAsyncioTestCase):
    actor=Routing.actor
    async def test_external_screen_requires_exact_session_and_action_rechecks_pane(self):
        a=self.actor();d=menu.Desk(a)
        from agentj import shared_interrupt as si,shared_modelswitch as sm
        agents=[{'pane_id':'other','agent_session':{'value':'other'}}]
        with patch.object(si,'herdr_bin',return_value='fixture-herdr'),patch.object(si,'_agents',side_effect=lambda b:agents),patch.object(sm,'screen',return_value=QUOTA) as screen,patch.object(si,'_run',return_value={}) as run:
            self.assertEqual(await d.snapshot(),(False,None));screen.assert_not_called()
            agents[:]=[{'pane_id':'exact','agent_session':{'value':'fixture'}}]
            info,route=await d.snapshot();self.assertEqual(info['kind'],'quota')
            agents.clear()
            self.assertFalse(await d.send(route,'1'));run.assert_not_called()
    async def test_active_phone_turn_stops_waiting_on_limit_without_fake_receipt(self):
        a=self.actor();a.phone_turn=True;a.phone_reply_seen=False;d=menu.Desk(a)
        a.host.question.return_value=('timeout',None)
        with patch.object(d,'snapshot',new=AsyncMock(return_value=(menu.detect(QUOTA),'pane'))):
            await d.poll();await d.task
        self.assertTrue(a.done.is_set());self.assertTrue(a.phone_reply_seen)
        a.host.turn_queued_note.assert_called_with(False)
        self.assertIn('6:50pm',a.host.agent_notice.call_args.args[0])


class Diagnostics(unittest.TestCase):
    def test_support_status_contains_only_fixed_menu_kind(self):
        from agentj.serve import Host
        host=object.__new__(Host)
        host.agent=SimpleNamespace(_observe_errors=0,menu_desk=SimpleNamespace(current=menu.detect(QUOTA)))
        facts=host._observe_view()
        self.assertEqual(facts['menu'],'quota')
        self.assertNotIn('6:50pm',str(facts))
    def test_doctor_explains_native_menu(self):
        from agentj import doctor
        st=SimpleNamespace(exists=lambda:True)
        with patch.object(doctor,'_fenced',return_value=False),patch.object(doctor.names,'ctl_call',return_value={'relay_up':True,'agent_observe':{'menu':'quota'}}):
            row=doctor.check_serve(st)
        self.assertIn('quota',row['summary'])
        self.assertIn('phone menu card',row['hint'])


class Freeform(unittest.IsolatedAsyncioTestCase):
    async def test_custom_text_never_types_and_reoffers_menu(self):
        a=Routing().actor();d=menu.Desk(a)
        a.host.question.return_value=('answer',['wait please'])
        with patch.object(d,'snapshot',new=AsyncMock(return_value=(menu.detect(QUOTA),'pane'))),patch.object(d,'send',new=AsyncMock()) as send:
            await d.poll();await d.task
            send.assert_not_called()
            self.assertFalse(d.attempted,'a supported custom-text answer must not permanently consume the native menu card')

class RewindFallback(unittest.IsolatedAsyncioTestCase):
    async def test_unconfirmed_auto_exit_offers_owner_exit_without_second_auto_key(self):
        from test_p451_menu import REWIND
        a=Routing().actor();d=menu.Desk(a)
        a.host.question.return_value=('timeout',None)
        with patch.object(d,'snapshot',new=AsyncMock(return_value=(menu.detect(REWIND),'pane'))),patch.object(d,'send',new=AsyncMock(return_value=True)) as send:
            await d.poll();await d.task
            self.assertEqual(send.await_count,1)
            d.retry_at=0
            await d.poll()
            if d.task: await d.task
            self.assertEqual(a.host.question.await_count,1,'an unconfirmed automatic Escape needs a signed manual exit fallback')
            self.assertEqual(send.await_count,1,'the unchanged menu must not receive a second automatic Escape')




class PartialHost(unittest.IsolatedAsyncioTestCase):
    async def test_host_without_stop_interface_never_gets_terminal_controls(self):
        a=Routing().actor();del a.host.stopped;d=menu.Desk(a)
        with patch.object(d,'snapshot',new=AsyncMock()) as snapshot:
            await d.poll();snapshot.assert_not_called()
            self.assertFalse(await d.send(None,'esc'))
        self.assertIsNone(d.current)
