"""Current native wire: subAgentActivity launch and explicit child input rebinding."""
import _hermetic  # noqa: F401
import asyncio
import time
from types import SimpleNamespace as NS
from test_stella_codex_children import Children
from agentj import codex_children, provenance


class NativeActivity(Children):
    def activity(self, a, kind='started', child='child', ident='activity', parent='parent', turn=None, method='item/started'):
        a._on_note(method, dict(threadId=parent, turnId=turn or a.turn_id, item=dict(
            type='subAgentActivity', id=ident, kind=kind, agentThreadId=child, agentPath='/root/child')))
        if method == 'item/started' and kind in ('started', 'interacted'):
            a._on_note('turn/started', {'threadId': child, 'turn': {'id': f'native:{turn or a.turn_id}:{ident}'}})

    async def request(self, a, command='ls /tmp', child='child'):
        pin=a.__dict__.get('_child_sources',{}).get(child) or {}
        a._on_request(dict(id=100, method='item/commandExecution/requestApproval', params=dict(
            threadId=child, turnId=pin.get('native_turn','childturn'), itemId='call', command=command, cwd='/tmp')))
        while a.tasks:
            await asyncio.gather(*list(a.tasks))
        return a._answer.call_args.args[1]

    def next_send(self, a, source=None):
        old=a.cur_send
        origin=dict(old.approval_origin, task_record='message:epoch:2',message_id=2)
        a.turn_id='turn2'
        a.cur_send=NS(source=source or provenance.phone(),approval_origin=origin,device='phone',state='delivered')
        return a.cur_send

    async def test_native_new_child_after_parent_done(self):
        a,_=self.make();self.activity(a);a.cur_send=None
        self.assertEqual(await self.request(a),{'decision':'accept'})

    async def test_native_interacted_rebinds_old_child_and_origin(self):
        a,old=self.make();self.spawn(a);self.next_send(a)
        old.state='cancelled';self.activity(a,'interacted',ident='new-task')
        pin=a._child_sources['child'];a.cur_send=None
        self.assertEqual(pin['origin']['task_record'],'message:epoch:2')
        self.assertEqual(await self.request(a),{'decision':'accept'})

    async def test_native_complete_duplicate_does_not_rebind_newer_source(self):
        a,_=self.make();self.activity(a);pin=a._child_sources['child']
        self.next_send(a,dict(kind='unknown',role='group'))
        self.activity(a,method='item/completed',turn='turn1')
        self.assertIs(a._child_sources['child'],pin)
        a.cur_send=None;self.assertEqual(await self.request(a),{'decision':'accept'})

    async def test_explicit_untrusted_rebind_cannot_borrow_previous_owner(self):
        a,_=self.make();self.activity(a)
        self.next_send(a,dict(kind='unknown',role='group'));self.activity(a,'interacted',ident='new-task')
        a.cur_send=None;self.assertEqual(await self.request(a),{'decision':'decline'})

    async def test_rebind_invalidates_old_inflight_and_descendants(self):
        a,_=self.make();self.activity(a);old=a._child_sources['child']
        self.spawn(a,'grand','child');grand=a._child_sources['grand']
        self.next_send(a);self.activity(a,'interacted',ident='new-task')
        self.assertFalse(codex_children.live(a,old));self.assertFalse(codex_children.live(a,grand))
        self.assertTrue(codex_children.live(a,a._child_sources['child']))

    async def test_completion_does_not_revoke_but_interruption_does(self):
        a,_=self.make();self.activity(a);self.activity(a,'completed',ident='finished')
        self.assertEqual(await self.request(a),{'decision':'accept'})
        self.activity(a,'interrupted',ident='stopped')
        self.assertEqual(await self.request(a),{'decision':'decline'})

    async def test_wrong_turn_unknown_parent_and_main_target_fail_closed(self):
        for kwargs in (dict(turn='stale'),dict(parent='unknown'),dict(child='parent')):
            with self.subTest(kwargs=kwargs):
                a,_=self.make();self.activity(a,**kwargs);a.cur_send=None
                self.assertEqual(await self.request(a),{'decision':'decline'})

    async def test_legacy_send_input_rebinds_and_is_idempotent(self):
        a,old=self.make();self.spawn(a);self.next_send(a);old.state='cancelled'
        p=dict(threadId='parent',turnId='turn2',item=dict(type='collabAgentToolCall',id='input',
            tool='sendInput',senderThreadId='parent',status='inProgress',receiverThreadIds=['child']))
        a._on_note('item/started',p)
        self.assertEqual(await self.request(a),{'decision':'decline'})
        a._on_note('turn/started',{'threadId':'child','turn':{'id':'legacy-new-turn'}})
        a.cur_send=None
        p['item']['status']='completed';a._on_note('item/completed',p)
        pin=a._child_sources['child'];a._on_note('item/completed',p)
        self.assertIs(a._child_sources['child'],pin)
        self.assertEqual(await self.request(a),{'decision':'accept'})

    async def test_legacy_multireceiver_bindings_revoke_independently(self):
        a,_=self.make()
        a._on_note('item/completed',dict(threadId='parent',turnId='turn1',item=dict(
            type='collabAgentToolCall',id='input',tool='sendInput',senderThreadId='parent',
            status='completed',receiverThreadIds=['one','two'])))
        for child in ('one','two'):
            a._on_note('turn/started',{'threadId':child,'turn':{'id':'legacy-new-'+child}})
        a._on_note('item/completed',dict(threadId='parent',turnId='turn1',item=dict(
            type='collabAgentToolCall',id='close',tool='closeAgent',senderThreadId='parent',
            status='completed',receiverThreadIds=['one'])))
        a.cur_send=None
        self.assertEqual(await self.request(a,child='one'),{'decision':'decline'})
        self.assertEqual(await self.request(a,child='two'),{'decision':'accept'})

    async def test_reused_item_id_cannot_change_turn_or_target(self):
        for turn in ('turn1','turn2'):
            a,_=self.make();self.activity(a,child='one');self.next_send(a,dict(kind='unknown',role='group'))
            self.activity(a,'interacted',child='two',turn=turn)
            a.cur_send=None
            self.assertEqual(await self.request(a,child='two'),{'decision':'decline'})

    async def test_failed_legacy_input_retires_only_its_own_binding(self):
        for newer in (False,True):
            a,_=self.make();self.spawn(a)
            p=dict(threadId='parent',turnId='turn1',item=dict(type='collabAgentToolCall',id='input',
                tool='sendInput',senderThreadId='parent',status='inProgress',receiverThreadIds=['child']))
            a._on_note('item/started',p)
            if newer:
                p['item']['status']='completed';a._on_note('item/completed',p)
                a._on_note('turn/started',{'threadId':'child','turn':{'id':'legacy-old-turn'}})
                self.activity(a,'interacted',ident='newer')
            p['item']['status']='failed';a._on_note('item/completed',p);a.cur_send=None
            self.assertEqual(await self.request(a),{'decision':'accept' if newer else 'decline'})
