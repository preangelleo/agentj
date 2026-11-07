"""0.28.3 bridge regression: native child approval after parent turn closes."""
import _hermetic  # noqa: F401
import asyncio
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, AsyncMock
from agentj import provenance, approval_review
from agentj.agent_codex import CodexAgent


class Children(unittest.IsolatedAsyncioTestCase):
    def make(self, source=None):
        a = object.__new__(CodexAgent)
        origin = dict(message_id=1, root_message_id=1, session='epoch', channel='phone',
                      verified=True, suspicious=False, task_record='message:epoch:1', chain=[])
        send = NS(source=source or provenance.phone(), approval_origin=origin, device='phone', state='delivered')
        h = NS(hist=NS(epoch='epoch'), st=NS(sign_key=lambda _: b'key', log=Mock()), estop={},
               stopped=lambda: False, stopping=asyncio.Event(), agent=a, peers=None, lang='zh')
        a.host, a.tid, a.turn_id, a.proc = h, 'parent', 'turn1', NS(returncode=None)
        a.cfg = dict(dir='/tmp', high_risk_warnings=True)
        a.cur_send, a.tasks, a.pending = send, set(), {}
        a.thread, a.items, a.research, a.halting = {'sandbox': {'type': 'dangerFullAccess'}}, {}, False, False
        a.touch, a._answer, a._busy = Mock(), Mock(), Mock()
        a.first_output = Mock()
        a.collect, a.quiet, a.persist = [], True, False
        a.call = AsyncMock()
        a.local_only = Mock()
        a.host.agent_notice = Mock()
        h._classify = lambda tool, inp: NS(danger='rm ' in inp.get('command', ''), cats=['delete'] if 'rm ' in inp.get('command', '') else [])
        async def ask(*args, **kwargs):
            src = provenance.turn_source(a)
            if provenance.untrusted_turn(a):
                return {'behavior': 'deny', 'message': 'Unverified source'}
            self.assertEqual(approval_review.snapshot(h)['task_record'], 'message:epoch:1')
            self.cards.append(args[0])
            return {'behavior': 'allow'}
        self.cards = []
        h.ask = AsyncMock(side_effect=ask)
        return a, send

    def spawn(self, a, child='child', parent='parent'):
        a._on_note('item/completed', dict(threadId=parent, turnId='turn1', item=dict(
            type='collabAgentToolCall', id='spawn', tool='spawnAgent', senderThreadId=parent, status='completed', receiverThreadIds=[child])))

    async def request(self, a, command='ls /tmp', child='child'):
        a._on_request(dict(id=100, method='item/commandExecution/requestApproval', params=dict(
            threadId=child, turnId='childturn', itemId='call', command=command, cwd='/tmp')))
        while a.tasks:
            await asyncio.gather(*list(a.tasks))
        return a._answer.call_args.args[1]

    async def test_parent_done_child_read_passes(self):
        a, _ = self.make(); self.spawn(a); a.cur_send = None
        self.assertEqual(await self.request(a), {'decision': 'accept'})
        self.assertEqual(self.cards, [])

    async def test_danger_child_still_uses_normal_card_and_task_origin(self):
        a, _ = self.make(); self.spawn(a); a.cur_send = None
        self.assertEqual(await self.request(a, 'rm /tmp/example'), {'decision': 'accept'})
        self.assertEqual(self.cards, ['Bash'])

    async def test_unknown_child_cannot_borrow_current_owner(self):
        a, _ = self.make()
        self.assertEqual(await self.request(a), {'decision': 'decline'})

    async def test_foreign_child_cannot_borrow_next_owner(self):
        a, send = self.make(dict(kind='unknown', role='group')); self.spawn(a)
        a.cur_send = NS(source=provenance.phone())
        self.assertEqual(await self.request(a), {'decision': 'decline'})

    async def test_no_rebind_old_child_to_new_task(self):
        a, send = self.make(); self.spawn(a)
        send.state = 'cancelled'; a.cur_send = NS(source=provenance.phone())
        self.spawn(a)
        self.assertEqual(await self.request(a), {'decision': 'decline'})

    async def test_cancel_expire_process_session_pairing_and_stop_refuse(self):
        from agentj import codex_children
        for reason in ('cancel', 'expire', 'process', 'session', 'pairing', 'stop'):
            with self.subTest(reason=reason):
                a, send = self.make(); self.spawn(a); a.cur_send = None
                binding = a._child_sources['child']
                if reason == 'cancel': send.state = 'cancelled'
                if reason == 'expire': binding['until'] = time.monotonic() - 1
                if reason == 'process': a.proc = NS(returncode=None)
                if reason == 'session': a.host.hist.epoch = 'next'
                if reason == 'pairing': a.host.st.sign_key = lambda _: b'new'
                if reason == 'stop': a.host.estop = {'on': False}  # stop+resume changed marker
                self.assertEqual(await self.request(a), {'decision': 'decline'})

    async def test_child_cannot_inherit_from_wrong_parent_turn(self):
        a, _ = self.make()
        a._on_note('item/completed', dict(threadId='parent', turnId='oldturn', item=dict(
            type='collabAgentToolCall', id='spawn', tool='spawnAgent', senderThreadId='parent', status='completed', receiverThreadIds=['child'])))
        a.cur_send = None
        self.assertEqual(await self.request(a), {'decision': 'decline'})

    async def test_late_allow_after_revoke_declines(self):
        for reason in ('cancel', 'expire', 'session', 'stop'):
            with self.subTest(reason=reason):
                a, send = self.make(); self.spawn(a); a.cur_send = None
                entered, release = asyncio.Event(), asyncio.Event()
                async def late(*args, **kwargs):
                    entered.set(); await release.wait(); return {'behavior': 'allow'}
                a.host.ask.side_effect = late
                job = asyncio.create_task(self.request(a, 'rm /tmp/example'))
                await entered.wait()
                if reason == 'cancel': send.state = 'cancelled'
                if reason == 'expire': a._child_sources['child']['until'] = 0
                if reason == 'session': a.host.hist.epoch = 'new'
                if reason == 'stop': a.host.estop = {'on': False}
                release.set()
                self.assertEqual(await job, {'decision': 'decline'})

    async def test_thread_started_before_spawn_complete_and_nested_child(self):
        a, _ = self.make()
        a._on_note('item/started', dict(threadId='parent', turnId='turn1', item=dict(
            type='collabAgentToolCall', id='start', tool='spawnAgent', senderThreadId='parent',
            status='inProgress', receiverThreadIds=[])))
        a._on_note('thread/started', {'thread': {'id': 'child', 'source': {'subAgent': {
            'thread_spawn': {'parent_thread_id': 'parent', 'depth': 1}}}}})
        self.spawn(a, 'grandchild', 'child'); a.cur_send = None
        self.assertEqual(await self.request(a, child='grandchild'), {'decision': 'accept'})

    async def test_closing_one_child_keeps_sibling_but_revokes_descendants(self):
        a, _ = self.make(); self.spawn(a, 'one'); self.spawn(a, 'two'); self.spawn(a, 'grand', 'one')
        a.cur_send = None
        a._on_note('item/completed', dict(threadId='parent', turnId='turn1', item=dict(
            type='collabAgentToolCall', id='close', tool='closeAgent', senderThreadId='parent',
            status='completed', receiverThreadIds=['one'])))
        self.assertEqual(await self.request(a, child='two'), {'decision': 'accept'})
        self.assertEqual(await self.request(a, child='one'), {'decision': 'decline'})
        self.assertEqual(await self.request(a, child='grand'), {'decision': 'decline'})

    async def test_more_than_128_completed_spawns_still_bind_and_process_restart_resets(self):
        a, _ = self.make()
        for i in range(140): self.spawn(a, str(i))
        a.cur_send = None
        self.assertEqual(await self.request(a, child='139'), {'decision': 'accept'})
        a.proc = NS(returncode=None)
        a._on_note('thread/started', {'thread': {'id': 'new-main'}})
        self.assertEqual(a._child_sources, {})
        self.assertEqual(await self.request(a, child='139'), {'decision': 'decline'})

    async def test_child_never_uses_new_parent_secret_broker(self):
        a, _ = self.make(); self.spawn(a)
        broker = NS(register=Mock(), execute=AsyncMock(return_value={'behavior': 'allow'}))
        a.host.peers = NS(xfer=NS(broker=broker))
        a.cur_send = NS(source=provenance.phone())
        self.assertEqual(await self.request(a), {'decision': 'accept'})
        broker.register.assert_not_called(); broker.execute.assert_not_called()

    async def test_child_secret_call_keeps_native_hard_deny(self):
        a, _ = self.make(); self.spawn(a); a.cur_send = None
        self.assertEqual(await self.request(a, 'agentj secret grants'), {'decision': 'decline'})
        a.host.ask.assert_not_called()

    async def test_unreasonable_native_nesting_fails_closed_without_recursion_error(self):
        a, _ = self.make(); self.spawn(a, 'level0')
        for i in range(1, 32): self.spawn(a, f'level{i}', f'level{i-1}')
        a.cur_send = None
        self.assertEqual(await self.request(a, child='level31'), {'decision': 'decline'})
        self.assertEqual(await self.request(a, child='level0'), {'decision': 'accept'})
        self.assertEqual(await self.request(a, child='level1'), {'decision': 'accept'})


class RealHost(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import test_p541_approval_review as fixture
        await fixture.Review.asyncSetUp(self)
        old = self.h.agent
        a = object.__new__(CodexAgent)
        a.__dict__.update(old.__dict__)
        a.host = self.h
        a.tid, a.turn_id, a.proc = 'parent', 'turn1', NS(returncode=None)
        a.cfg = dict(self.h.agent_cfg, high_risk_warnings=True, session_mode='independent')
        a.tasks, a.pending, a.items = set(), {}, {}
        a.thread = {'sandbox': {'type': 'dangerFullAccess'}}
        a.research, a.halting, a.quiet, a.persist = False, False, True, False
        a.touch, a._answer, a._busy, a.first_output = Mock(), Mock(), Mock(), Mock()
        a.collect = []; a.call = AsyncMock(); a.local_only = Mock()
        a.cur_send.device = self.ph.did; a.cur_send.state = 'delivered'
        self.h.agent = self.agent = a
        self.h.agent_cfg = a.cfg

    async def test_child_danger_real_signed_card_after_parent_done(self):
        a = self.agent
        Children.spawn(self, a); a.cur_send = None
        a._on_request(dict(id=101, method='item/commandExecution/requestApproval', params=dict(
            threadId='child', turnId='childturn', command='rm /tmp/synthetic-stella', cwd=a.cfg['dir'])))
        for _ in range(100):
            if self.h.asks: break
            await asyncio.sleep(.005)
        self.assertTrue(self.h.asks)
        ask = next(iter(self.h.asks.values()))
        self.assertEqual(ask.envelope['source']['task_record'], self.origin['task_record'])
        await self.h._answer(self.s, self.ph.answer(self.h._ask_msg(ask, self.s), True))
        while a.tasks:
            await asyncio.gather(*list(a.tasks))
        self.assertEqual(a._answer.call_args.args, (101, {'decision': 'accept'}))
