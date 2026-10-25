"""P509: explicit owner provenance and fail-closed request paths."""
import _hermetic  # noqa: F401
import asyncio
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
from agentj import provenance as p, goal, capability, skill_ops
from agentj.agent_opencode import OpenCodeAgent


def agent(src):
    return NS(cur_send=NS(source=src))


class Sources(unittest.TestCase):
    def test_unknown_and_system_are_never_owner(self):
        for src in (None, {}, p.system(), p.telegram_owner(7), p.email_owner('a@example.com'),
                    p.automation('daily'), {'kind': 'same_owner', 'role': 'task'},
                    {'kind': 'friend_task', 'role': 'task'}):
            with self.subTest(src=src):
                self.assertFalse(p.is_owner(src))
                self.assertTrue(p.untrusted_turn(agent(src)))
                self.assertFalse(skill_ops.owner_turn(agent(src)))
        self.assertTrue(p.untrusted_turn(None))
        self.assertEqual(p.normalize(None)['kind'], 'unknown')

    def test_explicit_phone_and_terminal_still_owner(self):
        for src in (p.phone(), {'kind': 'terminal', 'role': 'owner'}):
            with self.subTest(src=src):
                self.assertTrue(p.is_owner(src))
                self.assertFalse(p.untrusted_turn(agent(src)))
                self.assertTrue(skill_ops.owner_turn(agent(src)))

    def test_pending_and_native_sources_are_explicit(self):
        for src in (None, p.system(), p.phone(), {'kind': 'terminal', 'role': 'owner'}):
            for a in (NS(cur_send=None, phone_turn=True, pending_source=src), NS(cur_send=None, native_source=src)):
                with self.subTest(src=src, a=a):
                    self.assertEqual(p.untrusted_turn(a), not (isinstance(src, dict) and src.get('kind') in ('phone', 'terminal')))

    def test_request_context_never_upgrades_unknown(self):
        with p.request_source(p.phone()):
            self.assertTrue(p.untrusted_turn(agent(None)))
        with p.request_source(None):
            self.assertTrue(p.untrusted_turn(agent(p.phone())))

    def test_goal_preauth_checks_current_source(self):
        for src in (None, p.system(), p.phone(), {'kind': 'terminal', 'role': 'owner'}):
            h = NS(agent=agent(src), st=object(), preferences={})
            g = goal.Goals(h)
            g.owner_asked()
            with patch('agentj.approval_level.goal_preauth', return_value='grant') as grant:
                self.assertEqual(g._preauth({'grants': []}), 'grant' if src and src.get('kind') in ('phone', 'terminal') else None)
                self.assertEqual(grant.call_count, int(bool(src and src.get('kind') in ('phone', 'terminal'))))
                self.assertIsNone(g._preauth({'grants': []}))

    def test_opencode_missing_background_is_untrusted(self):
        a = OpenCodeAgent.__new__(OpenCodeAgent)
        a.cur_send = None
        a.sid = 'main'
        self.assertFalse(p.is_owner(a._cur_src()))
        with a._src_ctx('main'):
            self.assertTrue(p.untrusted_turn(a))

    def test_dispatch_checks_current_source_before_any_work(self):
        async def run():
            for src in (None, p.system(), p.phone(), {'kind': 'terminal', 'role': 'owner'}):
                h = NS(agent=agent(src), stopped=lambda: False, st=object(), scheduler=NS(current_id='wf', waiting=[]))
                c = capability.Capabilities.__new__(capability.Capabilities)
                c.host = h
                with patch('agentj.capability.audit'):
                    if p.is_owner(src):
                        self.assertEqual((await c.dispatch('wf'))['result'], 'already_running')
                    else:
                        with self.assertRaises(capability.CapError) as got:
                            await c.dispatch('wf')
                        self.assertEqual(got.exception.why, 'untrusted_source')
        asyncio.run(run())


class Consumers(unittest.IsolatedAsyncioTestCase):
    async def test_sensitive_consumers_owner_positive_and_missing_system_negative(self):
        from unittest.mock import Mock, AsyncMock
        from agentj import approval_jev, contacts_serve, elevate, heartbeat, imessage, term_bang, serve, fallback
        from agentj.shared import SharedClaudeAgent
        for src, owner in ((p.phone(), True), ({'kind': 'terminal', 'role': 'owner'}, True), (None, False), (p.system(), False)):
            with self.subTest(source=src):
                a = agent(src)
                a.cfg, a.halting = {}, False
                h = NS(agent=a, st=Mock(), lang='en', stopped=lambda: False,
                       _approvers=lambda: [], _classify=lambda *_: NS(danger=False, cats=[]), goals=None)
                self.assertEqual(approval_jev.low_risk_pass(h, {'high_risk_warnings': True}, 'Read', {'file_path': 'fixture.txt'}), owner)
                desk = term_bang.BangDesk.__new__(term_bang.BangDesk)
                desk.h = h
                self.assertEqual(desk.untrusted(123), not owner)
                im = imessage.IMessage.__new__(imessage.IMessage)
                im.host = h
                self.assertEqual((await im.handle({'op': 'unrecognised'}))['why'], 'unknown_op' if owner else 'untrusted_turn')
                hb = heartbeat.Heartbeat.__new__(heartbeat.Heartbeat)
                hb.host = h
                h.set_pref = AsyncMock(return_value={'ok': True})
                self.assertEqual((await hb.handle({'op': 'on'}))['ok'], owner)
                self.assertEqual(h.set_pref.call_count, int(owner))
                el = elevate.Elevator.__new__(elevate.Elevator)
                el.host, el.st, el.cards = h, h.st, {}
                with patch('agentj.elevate.audit'):
                    self.assertEqual((await el.request({'kind': 'secret'}))['result'], 'no_device' if owner else 'group_source')
                cs = contacts_serve.ContactService.__new__(contacts_serve.ContactService)
                cs.host, cs.st = h, h.st
                with patch('agentj.contacts_serve.C.read', return_value={}), patch('agentj.contacts_serve.C.get', return_value=None):
                    self.assertEqual((await cs.card({'id': 'missing'}))['result'], 'not_found' if owner else 'group_source')
                cs.mail_state = lambda: ({}, '', None)
                cs.resolve = Mock(return_value=(None, None))
                got = await cs.agent_send({'to': ['a@example.com'], 'subject': 'hello'})
                self.assertEqual(got['why'], 'not_found' if owner else 'group_source')
                self.assertEqual(cs.resolve.call_count, int(owner))
                # Batch-grant consumer: no stale owner grant on unknown/system turns.
                g = NS(tool='Bash', scope=('x', 'y'))
                h.grants, h._grant_live = {'g': g}, lambda _: True
                with patch('agentj.danger.same_scope', return_value=True):
                    self.assertIs(serve.Host._grant_for(h, 'Bash', ('x', 'y')), g if owner else None)
                # Thinking-only replies are projected only for explicit owners.
                sh = NS(phone_turn=True, pending_source=src, turn_texts=[], turn_thinking=('m', 'answer'), turn_last_msg='m')
                self.assertEqual(SharedClaudeAgent._turn_reply(sh), 'answer' if owner else '')
                # Fallback diagnosis is itself an owner-only action, including a missing Send.
                h.cur_failed, h._fallback_used = True, False
                with patch('agentj.fallback.diagnose', return_value=None) as diagnose:
                    await fallback.after_failure(h, a)
                    self.assertEqual(diagnose.call_count, int(owner))

    async def test_dispatch_rechecks_after_planning_before_receipt_or_queue(self):
        from unittest.mock import Mock
        h = NS(agent=agent(p.phone()), stopped=lambda: False, task_label=None,
               agent_cfg={}, st=object(), scheduler=NS(current_id=None, waiting=[], request=Mock()))
        c = capability.Capabilities(h)
        def changed(*args, **kw):
            h.agent.cur_send.source = p.system()
            return {'brief': {'full': {'confirmation': {'mode': 'requested'}}}}
        with patch('agentj.capability.plan_card', side_effect=changed), patch('agentj.capability.add_receipt') as receipt:
            with self.assertRaises(capability.CapError) as got:
                await c.dispatch('wf')
            self.assertEqual(got.exception.why, 'untrusted_source')
            receipt.assert_not_called()
            h.scheduler.request.assert_not_called()

    async def test_host_approval_and_bang_consumers(self):
        import tempfile
        from unittest.mock import Mock, AsyncMock
        from test_l1 import _host, _state
        from agentj import approvals, diskq, serve
        for src, owner in ((p.phone(), True), (p.terminal(), True), (None, False), (p.system(), False)):
            with self.subTest(source=src), tempfile.TemporaryDirectory() as td:
                st = _state(td)
                h = _host(st, [])
                h.agent = NS(kind='codex', status='idle', cur_send=NS(source=src))
                h._approvers = lambda: []
                await h.ask('Bash', {'command': 'rm -rf /tmp/owned-fixture'})
                await diskq.aflush()
                self.assertEqual(approvals.read_log(st)[-1]['reason'], 'no_device' if owner else 'group_source')
                h.seat_refusal = lambda: False
                h.sends = NS(used=lambda *a: False, full=lambda *a: False)
                h._accept = AsyncMock(return_value=NS(turn=3))
                h.hist_add = Mock(return_value={'id': 4})
                s = NS(device='owner', name='phone')
                got = await h.bang_turn(s, 'sid', 'pwd', '', False, 0, 'ok', 'nonce')
                self.assertEqual(got, 3 if owner else 4)
                self.assertEqual(h._accept.call_count, int(owner))

    async def test_phone_skill_consumer_only_counts_explicit_owner(self):
        from unittest.mock import Mock
        from agentj import serve
        for src, owner in ((p.phone(), True), (p.terminal(), True), (None, False), (p.system(), False)):
            h = NS(st=Mock(), agent_cfg={}, onboarding_lang=lambda: 'en', preferences={})
            with self.subTest(source=src), patch('agentj.skill_usage.installed', return_value=[]), \
                    patch('agentj.skill_ops.record') as count, patch('agentj.skill_ops.take_note', return_value='offer'):
                self.assertEqual(serve.Host.skill_turn_note(h, NS(provenance=src), 'make the report'), 'offer' if owner else '')
                self.assertEqual(count.call_count, int(owner))

    async def test_shared_delivery_normalizes_missing_source(self):
        from agentj.shared import SharedClaudeAgent
        from unittest.mock import AsyncMock, Mock
        # The actual merge-delivery path stores normalized pending provenance before delivering.
        for src, owner in ((p.phone(), True), (p.terminal(), True), (None, False), (p.system(), False)):
            nxt = NS(sid='s', state='ready', source=src, text='hello', wait_ready=AsyncMock(return_value=True))
            q = asyncio.Queue()
            q.put_nowait(nxt)
            a = NS(q=q, halting=False, session={'sessionId': 's'}, cur_send=None,
                   host=NS(turn_merged=lambda: None, agent_turn_start=Mock(), turn_queued_note=Mock()),
                   _mark_untrusted=Mock(), deliver=AsyncMock())
            with self.subTest(source=src), patch('agentj.compactprep.decorate', return_value='hello'):
                await SharedClaudeAgent._merge_queued(a)
                self.assertEqual(a.pending_source, p.normalize(src))
                self.assertEqual(a._mark_untrusted.call_count, int(not owner))

    async def test_readback_is_explicit_system_not_owner(self):
        send = capability.ReadBackSend('result data')
        self.assertEqual(send.source, p.system())
        self.assertTrue(p.untrusted_turn(agent(send.source)))

    async def test_opencode_none_cannot_become_a_null_context(self):
        from unittest.mock import Mock
        a = OpenCodeAgent.__new__(OpenCodeAgent)
        a.sid, a.host = 'main', NS(agent=None)
        for src, owner in ((p.phone(), True), (p.terminal(), True), (None, False), (p.system(), False)):
            with self.subTest(source=src):
                a.cur_send = NS(source=src)
                a._note_turn()
                a.cur_send = None
                with a._src_ctx('main'):
                    self.assertEqual(a._refused(), not owner)
                a.cur_send = NS(source=p.phone())
                with a._src_ctx('untracked'):
                    self.assertTrue(a._refused(), 'unknown child None must not mean current owner context')
                a.children['known'] = {'src': src}
                with a._src_ctx('known'):
                    self.assertEqual(a._refused(), not owner)
        self.assertEqual(p.stricter(p.phone(), None), p.normalize(None))
        self.assertEqual(p.stricter(None, p.phone()), p.normalize(None))

    async def test_shared_recovery_revokes_previous_terminal_source(self):
        from unittest.mock import Mock
        from agentj.shared import SharedClaudeAgent
        a = NS(cur_send=None, native_source=p.terminal(), phone_turn=False, turn_began_at=None,
               session={'sessionId': 'owner'}, native_turn=False, state=NS(status='idle'),
               host=NS(stopped=lambda: False, st=Mock(), lang='en', agent_notice=Mock()),
               _registry_quiet=lambda: True)
        def delivery(*args):
            self.assertEqual(a.native_source, p.system())
            self.assertTrue(p.untrusted_turn(a))
        with patch('agentj.handoff.RECOVER_DELAY', 0), patch('agentj.shared.claude_send', side_effect=delivery) as sent:
            await SharedClaudeAgent._recover(a, 'compact', True, None)
            sent.assert_called_once()

    async def test_v2_native_input_and_synthetic_input_have_distinct_authority(self):
        from unittest.mock import Mock
        from agentj.shared_opencode2 import OwnerOpenCodeV2Agent
        a = OwnerOpenCodeV2Agent.__new__(OwnerOpenCodeV2Agent)
        a.sid, a.phone_inbox, a.phone_texts = 'main', set(), []
        a.desktop_turn, a.host, a.cur_send = False, NS(desktop_input=Mock(), desktop_end=Mock()), None
        def event(synthetic):
            return {'type': 'session.inbox.enqueued', 'data': {'sessionID': 'main', 'inboxID': str(synthetic),
                    'item': {'type': 'user', 'synthetic': synthetic, 'payload': {'text': 'read the report'}}}}
        a.on_event(event(False))
        self.assertFalse(p.untrusted_turn(a))
        a.host.desktop_input.assert_called_once()
        a.on_event(event(True))
        self.assertTrue(p.untrusted_turn(a))
        a.host.desktop_input.assert_called_once()

    async def test_shared_permission_consumer_and_native_prompt_evidence(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import Mock, AsyncMock
        from agentj.shared import SharedClaudeAgent
        for src, owner in ((p.phone(), True), (p.terminal(), True), (None, False), (p.system(), False)):
            with self.subTest(source=src), tempfile.TemporaryDirectory() as td:
                h = Mock()
                h.st.root, h.lang = Path(td), 'en'
                h.stopped.return_value = False
                h.ask = AsyncMock(return_value={'behavior': 'allow'})
                a = SharedClaudeAgent(h, {'kind': 'claude', 'dir': td, '_workflow_ceo': True})
                a.session = {'sessionId': 'owner'}
                a.cur_send = NS(source=src)
                result = await a.hook_event({'hook_event_name': 'PermissionRequest', 'session_id': 'owner',
                                            'tool_name': 'Bash', 'tool_input': {'command': 'git push origin'}})
                self.assertEqual(h.ask.call_count, int(owner))
                self.assertEqual(result['hookSpecificOutput']['decision']['behavior'], 'allow' if owner else 'deny')
                # A real native prompt provides terminal evidence; synthetic input revokes it.
                a.cur_send = None
                await a.hook_event({'hook_event_name': 'UserPromptSubmit', 'session_id': 'owner', 'prompt': 'read the report'})
                self.assertFalse(p.untrusted_turn(a))
                await a.hook_event({'hook_event_name': 'UserPromptSubmit', 'session_id': 'owner',
                                   'prompt': 'system readback', 'synthetic': True})
                self.assertTrue(p.untrusted_turn(a))

    async def test_explicit_background_delegation_stays_non_owner_and_can_ask_fresh_card(self):
        for kind in ('friend_task', 'same_owner'):
            a = NS(cur_send=None, out_src={'kind': kind, 'role': 'task'})
            with p.request_source(a.out_src):
                self.assertTrue(p.untrusted_turn(a))
                self.assertTrue(p.delegated_turn(a))
            a.out_src = None
            with p.request_source({'kind': kind, 'role': 'task'}):
                self.assertTrue(p.untrusted_turn(a))
                self.assertFalse(p.delegated_turn(a), 'an unknown current turn cannot borrow a delegation')

    async def test_accept_goal_only_records_explicit_owner_request(self):
        import tempfile
        from unittest.mock import Mock, AsyncMock
        from test_l1 import _host, _state, _ready, Phone
        for src, owner in ((p.phone(), True), (p.terminal(), True), (None, False), (p.system(), False)):
            with self.subTest(source=src), tempfile.TemporaryDirectory() as td:
                h = _host(_state(td), [])
                h.agent = NS(kind='codex', status='idle', submit=Mock())
                h._setup_round_trip, h._model_set_flush = AsyncMock(), AsyncMock()
                h.speaker_note, h.onboarding_note = lambda *_: '', lambda: ''
                h.skill_turn_note, h._meeting_text = lambda *_: '', lambda _s, text, _a: text
                h.goals.owner_asked = Mock()
                s = _ready(h, Phone(h.st))
                s.provenance = src
                send = await h._accept(s, '/goal finish the report', None)
                self.assertEqual(h.goals.owner_asked.call_count, int(owner))
                self.assertEqual(send.source, src)
                h.agent.submit.assert_called_once_with(send)

    async def test_verified_phone_transport_owner_source_is_live_and_task_bound(self):
        import tempfile
        from unittest.mock import Mock, AsyncMock
        from test_l1 import _host, _state, _ready, Phone
        from agentj import wire
        with tempfile.TemporaryDirectory() as td:
            h = _host(_state(td), [])
            s = _ready(h, Phone(h.st))
            s.recv = NS(decrypt=lambda *_: b'fixture')
            h.agent = None
            seen = []
            async def handled(*_):
                seen.append(p.untrusted_turn(h.agent))
                if not seen[-1]:
                    async def child(): return p.untrusted_turn(None)
                    self.assertTrue(await asyncio.create_task(child()), 'child Task must not inherit owner authority')
            h._app = AsyncMock(side_effect=handled)
            with patch('agentj.wire.unpad_json', return_value={'t': 'fixture'}):
                for src, owner in ((p.phone(), True), (None, False), (p.system(), False)):
                    s.provenance = src
                    await h.on_data(s, bytes([wire.DATA]) + b'data')
                    self.assertEqual(seen[-1], not owner)
                    self.assertTrue(p.untrusted_turn(None), 'owner scope must end with this request')
                s.provenance = p.phone()
                with patch.object(h.st, 'is_allowed', return_value=False):
                    await h.on_data(s, bytes([wire.DATA]) + b'data')
                    self.assertTrue(seen[-1], 'revoked device is never owner')
                h.sessions.pop(s.cid)
                await h.on_data(s, bytes([wire.DATA]) + b'data')
                self.assertTrue(seen[-1], 'detached Session is never owner')

    async def test_paired_origin_rechecks_revocation_and_never_grants_unknown_children(self):
        live = [True]
        with p.request_source(p.system()), p.paired_request(lambda: live[0]):
            self.assertFalse(p.untrusted_turn(None), 'a fresh verified phone request has its own origin')
            self.assertFalse(p.delegated_turn(None))
            self.assertFalse(p.same_owner_source(None))
            async def child(): return p.untrusted_turn(None)
            self.assertTrue(await asyncio.create_task(child()))
            live[0] = False
            self.assertTrue(p.untrusted_turn(agent(p.phone())), 'revocation cannot fall back to a stale owner turn')
        self.assertTrue(p.untrusted_turn(None))

    async def test_signed_phone_background_callback_carries_only_its_own_origin(self):
        import tempfile
        from unittest.mock import AsyncMock
        from test_l1 import _host, _state, _ready, Phone
        from test_controls import sign
        from agentj.bots.owner import Manager
        for src, owner in ((p.phone(), True), (None, False), (p.system(), False)):
            with self.subTest(source=src), tempfile.TemporaryDirectory() as td:
                h = _host(_state(td), [])
                ph = Phone(h.st)
                s = _ready(h, ph)
                s.p33, s.provenance = True, src
                manager = Manager(h)
                seen = []
                async def write(*_):
                    seen.append(p.untrusted_turn(None))
                    async def child(): return p.untrusted_turn(None)
                    self.assertTrue(await asyncio.create_task(child()))
                    return {'fixture': True}
                manager.write = write
                req = {'op': 'human_email_code', 'id': 'fixture'}
                obj = sign(ph, 'bots_write', {'request': req}, {'t': 'bots_write', 'r': 'fixture', 'request': req})
                await manager.phone(s, obj, background=True)
                await asyncio.gather(*list(manager.phone_tasks))
                self.assertEqual(seen, [not owner])
                self.assertTrue(p.untrusted_turn(None))
                # A forged signature never admits the callback in the first place.
                seen.clear()
                bad = sign(ph, 'bots_write', {'request': req}, {'t': 'bots_write', 'r': 'bad', 'request': req})
                bad['sig'] = 'invalid'
                await manager.phone(s, bad, background=True)
                self.assertEqual(seen, [])

    async def test_native_terminal_input_never_promotes_peer_or_unknown_origin(self):
        for record in ({'role': 'user'}, {'role': 'user', 'turnOrigin': 'human'}, {'origin': {'kind': 'terminal'}}):
            self.assertTrue(p.is_owner(p.terminal_input(record, [], 'please read the report')))
        for record, text in (({'synthetic': True}, 'system readback'), ({'turnOrigin': 'scheduled'}, 'heartbeat'),
                             ({'origin': {'kind': 'peer'}}, 'external task'), ({'origin': {}}, 'unknown source'),
                             ({'role': 'user'}, '<cross-session-message from-name="unknown-peer">hello</cross-session-message>'),
                             ({'role': 'user'}, '＜CROSS-session-message＞hello＜/CROSS-session-message＞')):
            self.assertFalse(p.is_owner(p.terminal_input(record, [], text)))

    async def test_system_hook_stop_cannot_restore_old_phone_pending_source(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import Mock
        from agentj.shared import SharedClaudeAgent
        with tempfile.TemporaryDirectory() as td:
            h = Mock();h.st.root=Path(td);h.stopped.return_value=False
            a = SharedClaudeAgent(h, {'kind':'claude','dir':td,'_workflow_ceo':True})
            a.session={'sessionId':'owner'}
            a.pending_source = p.phone()  # the previous phone turn
            a.native_source = p.system()
            await a.hook_event({'hook_event_name':'UserPromptSubmit','session_id':'owner',
                                'prompt':p.envelope('fixture','system readback',p.system())})
            await a.hook_event({'hook_event_name':'Stop','session_id':'owner','last_assistant_message':'read'})
            self.assertTrue(p.untrusted_turn(a), 'Stop must not resurrect the previous phone owner source')
            await a.hook_event({'hook_event_name':'UserPromptSubmit','session_id':'owner','prompt':'new owner instruction'})
            self.assertFalse(p.untrusted_turn(a), 'a fresh native owner prompt still passes')


class ContinuationB(unittest.IsolatedAsyncioTestCase):
    async def test_phone_transcript_survives_wait_timeout_and_send_cleanup(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import Mock, AsyncMock
        from agentj import shared
        for timeout in ('input', 'end'):
            for meta in (False, True):
                for src in (p.phone(), None, p.system()):
                    with self.subTest(timeout=timeout, meta=meta, source=src), tempfile.TemporaryDirectory() as td:
                        h = Mock()
                        h.st.root, h.lang = Path(td), 'en'
                        h.stopped.return_value = False
                        h.ask = AsyncMock(return_value={'behavior': 'allow'})
                        a = shared.SharedClaudeAgent(h, {'kind': 'claude', 'dir': td, '_workflow_ceo': True})
                        a.session = {'sessionId': 'owner'}
                        a.transcript = Path(td) / 'transcript.jsonl'
                        a.transcript.touch()
                        a.cur_send = NS(sid='request-b', source=src)
                        a.menu_desk.wait_clear = AsyncMock(return_value=True)
                        a._wait_busy_input = AsyncMock(return_value=False)
                        a._await_turn_end = AsyncMock(side_effect=asyncio.TimeoutError)
                        a._idle_reply = AsyncMock(return_value=False)
                        a.fail_notice = Mock()
                        body = 'continue the authorized report'
                        echo = p.envelope('fixture', body, src)
                        def read(record):
                            with a.transcript.open('a') as out:
                                out.write(json.dumps(record) + '\n')
                            a.read_transcript()
                        async def deliver(_):
                            if timeout == 'end':
                                read({'type': 'user', 'isMeta': meta, 'message': {'content': echo}})
                                a.input_seen.set()
                        a.deliver = AsyncMock(side_effect=deliver)
                        with patch('agentj.shared.CLAUDE_INPUT_WAIT', .001), \
                                patch('agentj.shared.claude_sessions', return_value=[a.session]), \
                                patch('agentj.claude_inbound.turn_notice', return_value='fixture timeout'), \
                                patch('agentj.claude_inbound.diagnostic', return_value='fixture diagnostic'), \
                                patch('agentj.held.note', return_value=None):
                            await a.turn(body)
                            self.assertFalse(a.phone_turn)
                            a.cur_send = None  # Agent.run releases the Send, native work continues.
                            if timeout == 'input':
                                read({'type': 'user', 'isMeta': meta, 'message': {'content': echo}})
                            self.assertEqual(p.untrusted_turn(a), not p.is_owner(src))
                            decision = await a.hook_event({'hook_event_name': 'PermissionRequest', 'session_id': 'owner',
                                'tool_name': 'Bash', 'tool_input': {'command': 'git push origin'}})
                            self.assertEqual(decision['hookSpecificOutput']['decision']['behavior'],
                                             'allow' if p.is_owner(src) else 'deny')
                            self.assertEqual(h.ask.call_count, int(p.is_owner(src)))
                            # A same-body system readback is not this phone request's echo.
                            read({'type': 'user', 'isMeta': True,
                                  'message': {'content': p.envelope('fixture', body, p.system())}})
                            self.assertTrue(p.untrusted_turn(a))
                            # A new unverified input must replace the bound phone source.
                            read({'type': 'user', 'synthetic': True, 'message': {'content': 'system readback'}})
                            self.assertTrue(p.untrusted_turn(a))
                            # Replaying even the old exact phone envelope cannot restore authority.
                            read({'type': 'user', 'isMeta': True, 'message': {'content': echo}})
                            self.assertTrue(p.untrusted_turn(a))

    async def test_phone_labels_without_a_bound_request_never_grant_owner(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import Mock
        from agentj.shared import SharedClaudeAgent
        for src in (None, p.system()):
            with self.subTest(source=src), tempfile.TemporaryDirectory() as td:
                h = Mock(); h.st.root = Path(td)
                a = SharedClaudeAgent(h, {'kind': 'claude', 'dir': td, '_workflow_ceo': True})
                a.native_source, a.pending_source = p.normalize(src), p.phone()
                a.pending_text = 'old phone text'
                a.transcript = Path(td) / 'transcript.jsonl'
                a.transcript.write_text(json.dumps({'type': 'user', 'isMeta': True,
                    'message': {'content': p.envelope('fixture', a.pending_text, p.phone())}}) + '\n')
                a.read_transcript()
                self.assertTrue(p.untrusted_turn(a))

    async def test_existing_requested_dispatch_writes_receipt_and_enqueues_only_for_owner(self):
        import tempfile
        from unittest.mock import Mock
        from test_l1 import _state
        from agentj import diskq
        for src in (p.phone(), p.terminal(), None, p.system()):
            with self.subTest(source=src), tempfile.TemporaryDirectory() as td:
                st = _state(td)
                capability.add_receipt(st, 'wf', {'mode': 'signed', 'fixture': True})
                await diskq.aflush()
                self.assertTrue(capability.existing_workflow(st, 'wf'))
                sch = NS(current_id=None, waiting=[], request=Mock(return_value='run-b'))
                h = NS(agent=agent(src), stopped=lambda: False, task_label=None, st=st,
                       scheduler=sch, agent_cfg={}, activity=Mock())
                c = capability.Capabilities(h)
                brief = {'confirmation': {'mode': 'requested', 'request_id': 'request-b'}, 'revision': 1}
                plan = {'brief': {'full': brief}, 'missing': [], 'preflight': {'brief_digest': 'fixture'}}
                with patch('agentj.capability.plan_card', return_value=plan) as planned, \
                        patch('agentj.capability.scope_digest', return_value='fixture-scope'), \
                        patch('agentj.capability.working_root', return_value=None):
                    if p.is_owner(src):
                        result = await c.dispatch('wf')
                        self.assertEqual(result['result'], 'queued')
                        sch.request.assert_called_once_with('wf', 'dispatch')
                        receipts = capability.receipts(st)['wf']
                        self.assertEqual(len(receipts), 2)
                        self.assertEqual(receipts[-1]['mode'], 'requested')
                        self.assertEqual(receipts[-1]['request_id'], 'request-b')
                        self.assertEqual(capability._read(st, 'dispatch.json', {})['wf']['run_id'], 'run-b')
                    else:
                        with self.assertRaises(capability.CapError) as got:
                            await c.dispatch('wf')
                        self.assertEqual(got.exception.why, 'untrusted_source')
                        planned.assert_not_called()
                        sch.request.assert_not_called()
                        self.assertEqual(len(capability.receipts(st)['wf']), 1)
                        self.assertEqual(capability._read(st, 'dispatch.json', {}), {})
                await diskq.aflush()


    async def test_phone_echo_is_bound_to_session_and_latest_request(self):
        from unittest.mock import Mock
        from agentj.shared import SharedClaudeAgent
        a = SharedClaudeAgent(Mock(), {'kind': 'claude', '_workflow_ceo': True})
        send = NS(sid='request-b', source=p.phone())
        a.session = {'sessionId': 'owner'}
        a.native_source = a.pending_source = p.phone()
        a.pending_text = 'request b text'
        a.native_request = ('owner', send, a.pending_text)
        echo = p.envelope('fixture', a.pending_text, p.phone())
        self.assertTrue(a._phone_echo({'isMeta': True}, echo))
        for src in (None, p.system(), p.telegram_owner(7), p.email_owner('a@example.com'),
                    p.automation('daily'), {'kind': 'same_owner', 'role': 'task'},
                    {'kind': 'friend_task', 'role': 'task'}):
            with self.subTest(source=src):
                other = p.envelope('fixture', a.pending_text, src)
                self.assertFalse(a._phone_echo({'isMeta': True}, other))
                self.assertFalse(a._phone_echo({'isMeta': True, 'origin': {'name': p.PHONE}}, other))
        self.assertTrue(a._phone_echo({'origin': {'name': p.PHONE}}, a.pending_text))
        self.assertFalse(a._phone_echo({'synthetic': True}, echo))
        self.assertFalse(a._phone_echo({'turnOrigin': 'scheduled'}, echo))
        a.session = {'sessionId': 'different-session'}
        self.assertFalse(a._phone_echo({'isMeta': True}, echo))
        a.session = {'sessionId': 'owner'}
        a.pending_text = 'request c text'
        a.native_request = ('owner', NS(sid='request-c', source=p.phone()), a.pending_text)
        self.assertFalse(a._phone_echo({'isMeta': True}, echo))
        self.assertTrue(a._phone_echo({'isMeta': True}, p.envelope('fixture', a.pending_text, p.phone())))
