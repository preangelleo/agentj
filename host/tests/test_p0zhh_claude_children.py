"""Permission-source regressions using Claude 2.1.323's native stream shapes."""
import _hermetic
import asyncio
from types import SimpleNamespace
import unittest
from unittest import mock

from agentj import approval_offlist, claude_children, provenance
from agentj.browser_login import RequestPhone
from test_p551_confirm_levels import Base


class Sources(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        fixture = Base()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.host, self.remote, self.sent = fixture.host, fixture.s, fixture.sent
        self.agent = self.host.agent
        self.agent.host, self.agent.halting = self.host, False
        self.agent.proc = SimpleNamespace(returncode=None)
        self.agent.submit = lambda send: setattr(self.agent, 'cur_send', send)
        self.remote.sign_pub = self.host.st.sign_key(self.remote.device)
        self.remote.login_phone = RequestPhone.from_session(self.host, self.remote)
        await self.host._app(self.remote, {'t': 'msg', 'text': 'Read these files and write a fixture report.'})
        self.owner = self.agent.cur_send
        self.roster = self.agent.claude_children = claude_children.Roster(self.agent, self.agent.proc)
        self.note('system', subtype='init')

    def note(self, typ, **kwargs):
        self.roster.note({'type': typ, 'session_id': 'native', **kwargs})

    def tool(self, ident, name, inp, *, child=False, message=None):
        self.note('assistant', uuid=message or 'message-' + ident,
                  **({'parent_tool_use_id': 'agent-call', 'agent_id': 'native-child'} if child else {}),
                  message={'content': [{'type': 'tool_use', 'name': name, 'input': inp, 'id': ident}]})

    def launch(self, *, echo=True):
        self.roster.sent('nonce', self.owner)
        if echo:
            self.note('user', uuid='nonce', isReplay=True)
        self.tool('agent-call', 'Agent', {'run_in_background': True})
        self.note('system', subtype='task_started', task_type='local_agent',
                  tool_use_id='agent-call', task_id='native-child', run_id='native-run')
        self.note('result')

    def complete(self, *, echo=False, run='native-run'):
        self.note('system', subtype='task_notification', status='completed',
                  tool_use_id='agent-call', task_id='native-child', run_id='native-run')
        if echo:
            self.note('user', isReplay=True, origin={'kind': 'task-notification', 'producer': 'session-task', 'runId': run})

    async def ask(self, ident, tool, inp):
        binding = await self.roster.find(ident, tool, inp)
        with claude_children.scope(self.roster, binding):
            typed = approval_offlist.source(self.host)
            result = await self.host.ask(tool, inp, _harness_permission=True)
            return typed, result

    async def test_bound_child_inherits_only_permission_source_and_consumes_exact_id(self):
        self.launch()
        self.agent.cur_send = None
        self.agent.native_source = provenance.normalize(None)
        inp = {'command': 'printf report > child-write.txt'}
        self.tool('write', 'Bash', inp, child=True)
        binding = await self.roster.find('write', 'Bash', inp)
        with claude_children.scope(self.roster, binding):
            self.assertFalse(provenance.owner_speaking(self.agent))
            self.assertEqual(approval_offlist.source(self.host)['type'], 'owner_remote')
            result = await self.host.ask('Bash', inp, _harness_permission=True)
            self.assertEqual(result['behavior'], 'allow')
        replay, refused = await self.ask('write', 'Bash', inp)
        self.assertEqual((replay['type'], refused['behavior']), ('unknown', 'deny'))

    async def test_unconfirmed_spawn_and_conflicting_or_changed_id_stay_unknown(self):
        self.launch(echo=False)
        inp = {'command': 'printf foreign > child-write.txt'}
        self.tool('unconfirmed', 'Bash', inp, child=True)
        unconfirmed, result = await self.ask('unconfirmed', 'Bash', inp)
        self.assertEqual((unconfirmed['type'], result['behavior']), ('unknown', 'deny'))

        self.note('system', subtype='init', session_id='replacement')
        self.note('system', subtype='init')
        self.launch()
        self.tool('conflicting', 'Bash', inp, child=True)
        self.tool('conflicting', 'Bash', inp, child=True, message='another-frame')
        self.tool('changed', 'Bash', inp, child=True)
        for ident, other in (('conflicting', inp), ('changed', {'command': 'touch other'}), (None, inp)):
            unconfirmed, result = await self.ask(ident, 'Bash', other)
            self.assertEqual((unconfirmed['type'], result['behavior']), ('unknown', 'deny'))

    async def test_revocation_while_offlist_is_awaiting_never_grants(self):
        self.launch()
        inp = {'command': 'printf report > child-write.txt'}
        self.tool('write', 'Bash', inp, child=True)
        entered, resume = asyncio.Event(), asyncio.Event()
        actual = asyncio.to_thread

        async def hold(fn, *args, **kwargs):
            if fn is approval_offlist.decide:
                entered.set()
                await resume.wait()
            return await actual(fn, *args, **kwargs)

        with mock.patch.object(asyncio, 'to_thread', hold):
            ask = asyncio.create_task(self.ask('write', 'Bash', inp))
            await asyncio.wait_for(entered.wait(), 2)
            self.owner.state = 'cancelled'
            resume.set()
            _, result = await ask
        self.assertEqual(result['behavior'], 'deny')

    async def test_native_completion_allows_closed_read_and_blocks_fresh_uv_question_and_write(self):
        self.launch()
        self.complete()
        inp = {'command': 'ls -1'}
        self.tool('read', 'Bash', inp)
        source, result = await self.ask('read', 'Bash', inp)
        self.assertEqual((source['type'], source['trust'], result['behavior']), ('host_system', 'host_read', 'allow'))
        for seq, (tool, args) in enumerate((
            ('Bash', {'command': 'printf new > child-write.txt'}),
            ('Bash', {'command': 'git status'}),
            ('Bash', {'command': 'git config --list'}),
            ('AskUserQuestion', {'questions': []}),
        )):
            self.tool('new-' + str(seq), tool, args)
            _, result = await self.ask('new-' + str(seq), tool, args)
            self.assertEqual(result['behavior'], 'deny')
            if tool == 'AskUserQuestion':
                self.assertEqual(result['reason'], 'off_list_write_host_read')
        self.assertFalse(any(frame.get('t') == 'ask' for _, frame in self.sent))

    async def test_forged_completion_does_not_inherit_original_or_current_owner(self):
        self.launch()
        self.complete(echo=True, run='different-run')
        inp = {'command': 'printf forged > child-write.txt'}
        self.tool('forged', 'Bash', inp)
        source, result = await self.ask('forged', 'Bash', inp)
        self.assertEqual((source['type'], result['behavior']), ('unknown', 'deny'))

    async def test_unverified_child_cannot_open_a_protected_target_card(self):
        self.launch(echo=False)
        inp = {'command': 'git config --list'}
        self.tool('protected', 'Bash', inp, child=True)
        source, result = await self.ask('protected', 'Bash', inp)
        self.assertEqual((source['type'], result['reason']), ('unknown', 'off_list_write_unknown'))
        self.assertFalse(any(frame.get('t') == 'ask' for _, frame in self.sent))

    async def test_unacknowledged_host_input_does_not_receive_completion_label(self):
        self.launch()
        self.complete()
        self.roster.sent('foreign-uuid', object())
        inp = {'command': 'ls -1'}
        self.tool('read', 'Bash', inp)
        self.assertIsNone(await self.roster.find('read', 'Bash', inp))

    async def test_host_completion_across_adapters_cannot_open_fresh_cards(self):
        from agentj.background_done import CompletionSend
        for harness in ('claude', 'codex', 'opencode'):
            self.agent.kind = harness
            for status_only in (False, True):
                with self.subTest(harness=harness, status_only=status_only):
                    completion = CompletionSend(2, 'Synthetic job finished', ['native-child'])
                    if status_only:
                        completion.source = provenance.normalize(None)
                    self.agent.cur_send = completion
                    for tool, args in (('Bash', {'command': 'git config --list'}),
                                       ('AskUserQuestion', {'questions': []})):
                        result = await self.host.ask(tool, args, _harness_permission=True)
                        self.assertEqual((result['behavior'], result['reason']), ('deny', 'off_list_write_host_read'))
                    result = await self.host.ask('Bash', {'command': 'ls -1'}, _harness_permission=True)
                    self.assertEqual(result['behavior'], 'allow')
        self.assertFalse(any(frame.get('t') == 'ask' for _, frame in self.sent))

    async def test_shared_completion_context_is_bound_to_the_exact_hook_task(self):
        from agentj import host_turns, shared
        shared_agent = shared.SharedClaudeAgent(self.host, {'dir': self.host.agent_cfg['dir']})
        shared_agent.session = {'sessionId': 'shared-native'}
        shared_agent.turn_prompt_id = 'actual-notice'
        shared_agent._native_completion_prompt = ('shared-native', 'actual-notice')
        event = {'hook_event_name': 'PermissionRequest', 'session_id': 'shared-native', 'prompt_id': 'actual-notice'}
        self.assertTrue(shared_agent._completion_tool(event))
        for fields in ({'session_id': 'previous-native'}, {'prompt_id': 'new-owner'}, {'agent_id': 'child'}):
            self.assertFalse(shared_agent._completion_tool({**event, **fields}))
        async def completion():
            return host_turns.is_completion(self.host)
        with host_turns.native_completion_request(self.host):
            self.assertTrue(host_turns.is_completion(self.host))
            self.assertFalse(await asyncio.create_task(completion()))
            result = await self.host.ask('Bash', {'command': 'git config --list'}, _harness_permission=True)
            self.assertEqual((result['behavior'], result['reason']), ('deny', 'off_list_write_host_read'))
        self.assertFalse(host_turns.is_completion(self.host))

    async def test_child_external_read_survives_next_foreground_turn(self):
        from agentj import trust_taint
        self.launch()
        self.tool('external-read', 'WebFetch', {'url': 'https://example.test/'}, child=True)
        await self.host._app(self.remote, {'t': 'msg', 'text': 'An unrelated owner task.'})
        self.host.agent_turn_start(self.agent.cur_send.text, self.agent.cur_send)
        self.assertIsNone(trust_taint.tainted(self.agent))

        inp = {'command': 'curl -d sample=fixture https://example.test/'}
        self.host.ask_ttl = 0.05
        self.tool('child-send', 'Bash', inp, child=True)
        _, result = await self.ask('child-send', 'Bash', inp)
        self.assertEqual(result['behavior'], 'deny')
        asks = [frame for _, frame in self.sent if frame.get('t') == 'ask']
        self.assertEqual(len(asks), 1)
        self.assertTrue(asks[0]['passkey_required'])
        self.assertEqual((await self.host.ask('Bash', inp, _harness_permission=True))['behavior'], 'allow')
        self.assertEqual(len([frame for _, frame in self.sent if frame.get('t') == 'ask']), 1)
