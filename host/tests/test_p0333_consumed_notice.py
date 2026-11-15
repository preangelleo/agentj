"""0333: native consumption is a display receipt, not an owner-attestation grant."""
import _hermetic  # noqa: F401
from _testtmp import TEST_TMP
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, AsyncMock
from agentj import claude_inbound, provenance, shared, shared_turn_origin
from agentj.compose import Send
import test_p424_held as fixture


class Consumption(unittest.IsolatedAsyncioTestCase):
    agent = fixture.Held.agent

    async def run_turn(self, mode='received', busy=False, silent=False):
        temp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(temp.cleanup)
        h = fixture.Host424(temp.name)
        a = self.agent(h)
        h.agent = a
        page = fixture.send(h, 'One owner instruction')
        page.sid = 'request-0333'
        page.source = provenance.phone()
        a.cur_send = page
        h.agent_turn_start(page.text, page)
        a.transcript = Path(temp.name)/'native.jsonl'
        a.transcript.write_text('')
        a.state.status = 'working' if busy else 'idle'
        h.agent_text = lambda text: h.hist_update(page.turn, text=text)
        h.desktop_text = lambda text: None
        h.desktop_end = lambda: None
        h.queue_notes = []
        h.turn_queued_note = h.queue_notes.append

        def transport(session, text, source):
            if mode == 'failed':
                raise ConnectionError('fixture unavailable')
            if mode in ('queued','strict-timeout-race'):
                return
            record = {'type':'user', 'uuid':'native-input', 'sessionId':session['sessionId'],
                      'isMeta':True, 'isSidechain':False,
                      'timestamp':datetime.now(timezone.utc).isoformat(),
                      'origin':{'kind':'peer'},
                      'message':{'content':provenance.envelope(session['messagingSocketPath'], text, source)}}
            if mode == 'wrong-id':
                record['origin']['msg_id'] = 'different-host-record'
            if mode == 'old':
                record['timestamp'] = '2000-01-01T00:00:00Z'
            if mode == 'queue-only':
                record = {'type':'queue-operation', 'operation':'enqueue', 'content':record['message']['content']}
            rows = [record]
            if silent:
                rows += [{'type':'assistant', 'uuid':'native-silent',
                          'message':{'content':[{'type':'text','text':'〔静默消息〕'}]}}]
            a.transcript.write_text(''.join(json.dumps(r)+'\n' for r in rows))

        async def observe():
            while not a.transcript.stat().st_size:
                await asyncio.sleep(.001)
            if mode == 'during-busy-wait':
                await asyncio.sleep(.06)
            a.read_transcript()
            if a.native_request.received:
                # A display receipt is not completion: native Stop ends this fixture.
                a._finish_stop('〔静默消息〕' if silent else '0333 final reply')

        if mode == 'strict-timeout-race':
            async def boundary():
                att=a.native_request
                text=provenance.envelope(a.session['messagingSocketPath'],att.body,att.source)
                item={'type':'user','sessionId':att.session_id,'isMeta':True,
                      'timestamp':datetime.now(timezone.utc).isoformat(),
                      'origin':{'kind':'peer','msg_id':att.record_id}}
                self.assertTrue(shared_turn_origin.received(a,item,text))
                self.assertTrue(shared_turn_origin.delivered(a,item,text))
                raise asyncio.TimeoutError     # event arrived at wait_for's timeout boundary
            a.input_seen.wait = boundary
            a._await_turn_end = AsyncMock()
        observer = None if mode in ('queued','failed','strict-timeout-race') else asyncio.create_task(observe())
        try:
            with patch.object(shared,'claude_send', side_effect=transport), \
                 patch.object(shared,'claude_sessions',return_value=[a.session]), \
                 patch.object(shared,'CLAUDE_INPUT_WAIT',.04), \
                 patch.object(shared,'DESKTOP_IDLE_WAIT',.04), \
                 patch.object(claude_inbound,'hold_reason',return_value=None), \
                 patch.object(claude_inbound,'effective',return_value=('user',None,'accept')):
                await asyncio.wait_for(a.turn(page.text), 2)
        finally:
            if observer:
                observer.cancel()
                await asyncio.gather(observer,return_exceptions=True)
        return h, a, page

    async def test_idle_consumed_without_native_msg_id_has_no_card(self):
        h,a,page = await self.run_turn()
        self.assertNotIn('held', h.hist.get(page.turn).get('card') or {})
        self.assertFalse(any(ev=='shared_input_unaccepted' for ev,_ in h.logs))
        self.assertFalse(a.native_request.consumed, 'display receipt never grants strict owner attestation')
        self.assertFalse(a.phone_seen)
        self.assertFalse(a.input_seen.is_set())
        self.assertFalse(a.phone_anchored)

    async def test_silent_reply_does_not_make_consumed_message_unreceived(self):
        h,a,page = await self.run_turn(silent=True)
        self.assertNotIn('held', h.hist.get(page.turn).get('card') or {})
        self.assertFalse(any(ev=='shared_input_unaccepted' for ev,_ in h.logs))

    async def test_busy_owner_still_gets_existing_queue_card(self):
        h,a,page = await self.run_turn(mode='queued', busy=True)
        self.assertEqual(h.hist.get(page.turn)['card']['held'],'pending')
        self.assertTrue(h.hist.get(page.turn)['card']['queued'])
        self.assertEqual(h.hist.get(page.turn)['reply']['text'],'')

    async def test_wrong_id_old_record_and_queue_enqueue_are_not_consumption(self):
        for mode in ('wrong-id','old','queue-only'):
            with self.subTest(mode=mode):
                h,a,page = await self.run_turn(mode=mode)
                self.assertEqual(h.hist.get(page.turn)['card']['held'],'pending')
                self.assertFalse(a.native_request.consumed)

    async def test_consumption_during_busy_wait_clears_transient_queue_note(self):
        h,a,page = await self.run_turn(mode='during-busy-wait', busy=True)
        self.assertEqual(h.queue_notes[0], True)
        self.assertTrue(h.queue_notes[1:])
        self.assertTrue(all(note is False for note in h.queue_notes[1:]))
        self.assertNotIn('held',h.hist.get(page.turn).get('card') or {})
        self.assertTrue(a.native_request.received)
        self.assertFalse(a.input_seen.is_set())

    async def test_consumption_after_timeout_clears_existing_marker(self):
        h,a,page = await self.run_turn(mode='queued')
        self.assertEqual(h.hist.get(page.turn)['card']['held'],'pending')
        att=a.native_request
        a.cur_send = None       # Agent._worker clears the completed turn's Send
        item={'type':'user','uuid':'late-input','isMeta':True,
              'sessionId':att.session_id,'origin':{'kind':'peer'},
              'timestamp':datetime.now(timezone.utc).isoformat(),
              'message':{'content':provenance.envelope(a.session['messagingSocketPath'],att.body,att.source)}}
        a.transcript.write_text(json.dumps(item)+'\n')
        with patch.object(shared,'claude_sessions',return_value=[a.session]):
            a.read_transcript()
        card=h.hist.get(page.turn).get('card') or {}
        self.assertNotEqual(card.get('held'),'pending')
        self.assertNotIn('queued',card)
        self.assertFalse(a.input_seen.is_set())

    async def test_strict_ack_at_timeout_boundary_still_awaits_reply(self):
        h,a,page = await self.run_turn(mode='strict-timeout-race')
        a._await_turn_end.assert_awaited_once()
        self.assertTrue(a.native_request.consumed)
        self.assertTrue(a.input_seen.is_set())
        self.assertNotIn('held',h.hist.get(page.turn).get('card') or {})
        self.assertFalse(h.queue_notes)

    async def test_actual_transport_failure_still_raises_for_existing_failure_handler(self):
        with self.assertRaises(ConnectionError):
            await self.run_turn(mode='failed')


class ReceiptNegatives(unittest.TestCase):
    def test_fallback_never_grants_authority_or_accepts_other_record(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as root:
            h = fixture.Host424(root)
            a = fixture.Held.agent(self, h)
            a.cur_send = Send('body', 'request-fresh', 1, source=provenance.phone())
            a.pending_source = provenance.phone()
            a.pending_text = provenance.body('body', a.pending_source)
            a.native_request = shared_turn_origin.mint(a, a.cur_send, a.pending_text)
            att = a.native_request
            text = provenance.envelope(a.session['messagingSocketPath'], att.body, att.source)
            item = {'type':'user','isMeta':True,'sessionId':att.session_id,
                    'origin':{'kind':'peer'}, 'timestamp':datetime.fromtimestamp(
                        int(att.created_at * 1000) / 1000, timezone.utc).isoformat(timespec='milliseconds')}
            for field,value in (('sessionId','other-session'), ('type','assistant'), ('isMeta',False),
                                ('synthetic',True), ('ignored',True), ('isCompactSummary',True), ('isSidechain',True),
                                ('timestamp','2000-01-01T00:00:00Z'),
                                ('timestamp',datetime.fromtimestamp((int(att.created_at * 1000)-1)/1000,
                                     timezone.utc).isoformat(timespec='milliseconds'))):
                with self.subTest(field=field):
                    self.assertFalse(shared_turn_origin.received(a, {**item,field:value}, text))
            for wrong in (text+'tail', 'prefix'+text,
                          provenance.envelope('another-socket',att.body,att.source)):
                self.assertFalse(shared_turn_origin.received(a,item,wrong))
            for field,value in (('cur_send',SimpleNamespace()), ('pending_source',provenance.system()),
                                ('pending_text','another body')):
                before=getattr(a,field)
                setattr(a,field,value)
                self.assertFalse(shared_turn_origin.received(a,item,text))
                setattr(a,field,before)
            authority = (a.native_source, a.untrusted_state, getattr(a,"_owner_approval_origin",None))
            self.assertTrue(shared_turn_origin.received(a,item,text))
            self.assertEqual(authority, (a.native_source,a.untrusted_state,getattr(a,"_owner_approval_origin",None)))
            self.assertTrue(att.received)
            self.assertFalse(att.consumed)
            self.assertFalse(a.input_seen.is_set())
            self.assertFalse(a.phone_seen)
            self.assertFalse(a.phone_anchored)
            self.assertFalse(shared_turn_origin.delivered(a,item,text))


if __name__ == '__main__':
    unittest.main()
