"""Misfire #4: completed guest delivery cannot taint a later owner/task turn."""
import _hermetic  # noqa: F401
import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from agentj import approval_review, busy, provenance
from agentj.compose import Send
from test_p570b_resid import Shared, NOTE
from test_p551_confirm_levels import Base


class Turns(Shared):
    def delivered(self, a, source, text):
        send = Send('say', 's', 0, text=text, by='phone')
        send.source = source
        send.approval_origin = {'session': 'epoch', 'message_id': 7, 'root_message_id': 7,
                                'trigger_message_id': 7, 'channel': 'phone', 'verified': True,
                                'suspicious': False, 'text': 'owner work', 'chain': []}
        a.cur_send = send
        a.phone_turn = True
        a.pending_source = a.native_source = source
        a.pending_text = provenance.body(text, source)
        a.native_request = ('owner', send, a.pending_text)
        if not provenance.is_owner(source):
            a._mark_untrusted('pending')
        self.fire(a, {'type': 'user', 'isMeta': True, 'origin': {'kind': 'peer'},
                      'message': {'content': provenance.envelope('fixture', text, source)}})
        return send

    async def test_child_lifecycle_cannot_clear_parent_owner_or_task_origin(self):
        with tempfile.TemporaryDirectory() as td:
            _, a = self.agent(td)
            owner, tasks = object(), {'parent-task': object()}
            a._owner_approval_origin, a._native_task_origins = owner, tasks
            for name in ('SessionStart', 'SessionEnd'):
                a._note_background(name, {'session_id': 'owner', 'agent_id': 'child'})
                self.assertIs(a._owner_approval_origin, owner)
                self.assertIs(a._native_task_origins, tasks)

    async def test_completed_email_then_owner_and_own_task(self):
        with tempfile.TemporaryDirectory() as td:
            b = busy.Board(None)
            h, a = self.agent(td, b)
            h.agent = a
            h.hist.epoch = 'epoch'
            guest = provenance.email_guest('guest@example.test')
            self.delivered(a, guest, 'guest data')
            self.assertTrue(a.untrusted())
            a._finish_stop('done')
            a.session = {'sessionId': 'owner'}
            a.cur_send, a.phone_turn = None, False
            self.assertFalse(a.untrusted(), 'completed guest pending state must be retired')
            owner = self.delivered(a, provenance.phone(), 'owner work')
            self.assertFalse(provenance.untrusted_turn(a))
            launch={'hook_event_name':'PreToolUse','session_id':'owner','tool_use_id':'exact-own-call',
                    'tool_name':'Bash','tool_input':{'command':'sleep 9'}}
            a._note_background('PreToolUse',launch)
            a._note_background('PostToolUse',{**launch,'hook_event_name':'PostToolUse',
                                             'tool_response':{'backgroundTaskId':'own'}})
            self.launch(b, 'own', True)
            from agentj import shared_turn_origin
            shared_turn_origin.committed_launch(a, {'hook_event_name': 'PostToolUse', 'tool_response': {'backgroundTaskId': 'own'}})
            a._finish_stop('done')
            a.session = {'sessionId': 'owner'}
            a.cur_send, a.phone_turn = None, False
            self.notify(a, NOTE.format('own', 'build'))
            self.assertEqual(provenance.unattended_turn(a), 'system')
            self.assertEqual(approval_review.snapshot(h)['root_message_id'], owner.approval_origin['root_message_id'])
            self.assertTrue(approval_review.snapshot(h)['verified'])
            # A Host-created system Send resumes the proven owner origin, not mail data.
            a._finish_stop('done')
            a.session = {'sessionId': 'owner'}
            a.cur_send, a.phone_turn = Send('say', 'sys', 0, text='host notice'), False
            a.cur_send.source = provenance.system()
            from agentj import shared_turn_origin
            shared_turn_origin.prepare(a, provenance.system())
            self.assertEqual(a.cur_send.approval_origin['root_message_id'], 7)
            self.delivered(a, provenance.system(), 'host notice')
            self.assertEqual(provenance.unattended_turn(a), 'system')
            self.assertTrue(approval_review.snapshot(h)['verified'])
            a._finish_stop('done')
            a.session = {'sessionId': 'owner'}
            a.cur_send, a.phone_turn = None, False
            # Duplicate native notification consumes no new owner origin.
            self.notify(a, NOTE.format('own', 'build'))
            self.assertFalse(approval_review.snapshot(h))


class DeadCards(Base):
    def test_unknown_display_origin_offlist_uses_owner_turn_without_card(self):
        # The display snapshot does not retype the host-held owner turn. Off-list
        # calls are answered immediately; always_ask cannot invent a card.
        receipt = {}
        with mock.patch.object(self.host, '_interrupt') as interrupt, \
                mock.patch.object(self.host, 'push_notify') as push, \
                mock.patch.object(approval_review, 'snapshot', return_value=None):
            result = self.ask('rm -rf /home/owner/data', batch=False, always_ask=True,
                              _receipt=receipt)
        self.assertEqual(result['behavior'], 'allow')
        interrupt.assert_not_called()
        push.assert_not_called()
        self.assertEqual(self.cards(), [])
        self.assertFalse(receipt['approval_card_shown'])
        self.assertEqual(receipt['source'], 'owner_terminal')
        self.assertEqual(receipt['reason'], 'off_list')
        self.assertEqual(self.last()['reason'], 'off_list')
        self.assertEqual(self.last()['decision'], 'allow')
        # Source refusal remains audited when the host-held turn really is external;
        # the missing display snapshot must not be used to forge that provenance.
        with mock.patch.object(self.host.agent, 'native_source', provenance.email_guest('guest@example.test')):
            refused = self.ask('rm -rf /home/owner/data', batch=False, always_ask=True)
        self.assertEqual(refused['behavior'], 'deny')
        self.assertEqual(refused['reason'], 'off_list_write_external')
        self.assertEqual(self.last()['reason'], 'off_list_write_external')
        self.assertEqual(self.last()['decision'], 'deny')
        self.assertEqual(self.cards(), [])

    def test_verified_but_suspicious_display_origin_offlist_uses_owner_turn(self):
        source = {'session': str(self.host.hist.epoch), 'message_id': 7, 'root_message_id': 7,
                  'verified': True, 'suspicious': True, 'text': 'quoted external material', 'chain': []}
        receipt = {}
        with mock.patch.object(self.host, '_interrupt') as interrupt, \
                mock.patch.object(self.host, 'push_notify') as push, \
                mock.patch.object(approval_review, 'snapshot', return_value=source):
            result = self.ask('rm -rf /home/owner/data', batch=False, always_ask=True,
                              _receipt=receipt)
        self.assertEqual(result['behavior'], 'allow')
        interrupt.assert_not_called()
        push.assert_not_called()
        self.assertEqual(self.cards(), [])
        self.assertFalse(receipt['approval_card_shown'])
        self.assertEqual(receipt['source'], 'owner_terminal')
        self.assertEqual(receipt['reason'], 'off_list')
        self.assertEqual(self.last()['reason'], 'off_list')
        self.assertEqual(self.last()['decision'], 'allow')
