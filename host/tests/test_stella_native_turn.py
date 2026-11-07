"""Independent native-turn boundaries: no first-request or passive-event inference."""
import _hermetic  # noqa: F401
import asyncio
import unittest
from types import SimpleNamespace as NS
from test_stella_codex_children import Children
from agentj import provenance


class NativeTurn(unittest.IsolatedAsyncioTestCase):
    def make(self):
        fixture = Children('test_parent_done_child_read_passes')
        return fixture.make()

    def input(self, a, kind='started', ident='spawn', turn=None):
        a._on_note('item/started', {'threadId': a.tid, 'turnId': turn or a.turn_id, 'item': {
            'type': 'subAgentActivity', 'id': ident, 'kind': kind,
            'agentThreadId': 'child', 'agentPath': '/root/display-only'}})

    def turn(self, a, turn='child-1'):
        a._on_note('turn/started', {'threadId': 'child', 'turn': {'id': turn}})

    def next_owner(self, a):
        previous = a.cur_send or a._child_sources['child']['send']
        a.turn_id = 'owner-2'
        a.cur_send = NS(source=provenance.phone(), device='phone', state='delivered',
            approval_origin=dict(previous.approval_origin, message_id=2, root_message_id=2, task_record='message:epoch:2'))

    async def request(self, a, turn, child='child'):
        a._on_request({'id': 100, 'method': 'item/commandExecution/requestApproval', 'params': {
            'threadId': child, 'turnId': turn, 'command': 'ls /tmp', 'cwd': '/tmp', 'itemId': 'synthetic'}})
        while a.tasks:
            await asyncio.gather(*list(a.tasks))
        return a._answer.call_args.args[1]['decision']

    async def test_first_request_never_assigns_unknown_turn(self):
        a, _ = self.make(); self.input(a); a.cur_send = None
        self.assertEqual(await self.request(a, 'child-1'), 'decline')
        self.turn(a)
        self.assertEqual(await self.request(a, 'child-1'), 'accept')

    async def test_late_old_turn_cannot_borrow_new_task_before_or_after_new_turn(self):
        a, _ = self.make(); self.input(a); self.turn(a)
        self.next_owner(a); self.input(a, 'interacted', 'input-2'); a.cur_send = None
        self.assertEqual(await self.request(a, 'child-1'), 'decline')
        self.turn(a, 'child-2')
        self.assertEqual(await self.request(a, 'child-1'), 'decline')
        self.assertEqual(await self.request(a, 'child-2'), 'accept')

    async def test_duplicate_old_turn_started_cannot_activate_pending_new_task(self):
        a, _ = self.make(); self.input(a); self.turn(a)
        self.next_owner(a); self.input(a, 'interacted', 'input-2'); a.cur_send = None
        self.turn(a, 'child-1')
        self.assertEqual(await self.request(a, 'child-1'), 'decline')
        self.turn(a, 'child-2')
        self.assertEqual(await self.request(a, 'child-2'), 'accept')

    async def test_passive_completed_never_rebinds_cancelled_old_child(self):
        a, send = self.make(); self.input(a); self.turn(a); old = a._child_sources['child']
        self.next_owner(a); send.state = 'cancelled'; self.input(a, 'completed', 'passive')
        self.assertIs(a._child_sources['child'], old)
        a.cur_send = None
        self.assertEqual(await self.request(a, 'child-1'), 'decline')
        self.turn(a, 'child-2')
        self.assertEqual(await self.request(a, 'child-2'), 'decline')

    async def test_wrong_root_and_missing_turn_never_borrow_child_pin(self):
        a, _ = self.make(); self.input(a); self.turn(a); a.cur_send = None
        self.assertEqual(await self.request(a, None), 'decline')
        self.assertEqual(await self.request(a, 'owner-1'), 'decline')
        self.assertEqual(await self.request(a, 'child-1', a.tid), 'decline')

    async def test_explicit_unknown_input_keeps_new_turn_untrusted(self):
        a, _ = self.make(); self.input(a); self.turn(a)
        self.next_owner(a); a.cur_send.source = {'kind': 'unknown', 'role': 'group'}
        self.input(a, 'interacted', 'unknown-input'); self.turn(a, 'child-2'); a.cur_send = None
        self.assertEqual(await self.request(a, 'child-2'), 'decline')

    async def test_pending_source_remains_subject_to_revocation(self):
        for change in ('cancel', 'identity', 'epoch', 'expire', 'estop'):
            with self.subTest(change=change):
                a, send = self.make(); self.input(a)
                held = a._child_inputs[(a.tid, 'spawn')]['pin']
                if change == 'cancel': send.state = 'cancelled'
                if change == 'identity': a.host.st.sign_key = lambda _: b'other'
                if change == 'epoch': a.host.task_epoch = 'other'
                if change == 'expire': held['until'] = 0
                if change == 'estop': a.host.estop = {'on': False}
                self.turn(a); a.cur_send = None
                self.assertEqual(await self.request(a, 'child-1'), 'decline')

    async def test_legacy_success_needs_new_turn_and_failed_delivery_cannot_replay(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                a, _ = self.make(); Children.spawn(self, a)
                self.turn(a, 'legacy-old'); self.next_owner(a)
                p = {'threadId': a.tid, 'turnId': a.turn_id, 'item': {'type': 'collabAgentToolCall',
                    'id': 'legacy-input', 'tool': 'sendInput', 'senderThreadId': a.tid,
                    'status': 'inProgress', 'receiverThreadIds': ['child']}}
                a._on_note('item/started', p)
                self.assertEqual(await self.request(a, 'legacy-old'), 'decline')
                if fail:
                    p['item']['status'] = 'failed'; a._on_note('item/completed', p)
                p['item']['status'] = 'completed'; a._on_note('item/completed', p)
                self.assertEqual(await self.request(a, 'legacy-old'), 'decline')
                self.turn(a, 'legacy-new'); a.cur_send = None
                self.assertEqual(await self.request(a, 'legacy-new'), 'decline' if fail else 'accept')
