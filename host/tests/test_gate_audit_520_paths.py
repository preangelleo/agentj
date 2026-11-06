"""#520: Full access has no non-UV owner wait, across native and background entry points."""
import _hermetic  # noqa: F401
import asyncio
from unittest import mock
from agentj import approval_level, provenance
from agentj.compose import Send
from test_p551_confirm_levels import Base


class FullAccessPaths(Base):
    def test_own_button_and_ttl_do_not_reintroduce_a_card(self):
        self.harness()
        for kwargs in ({'always_ask': True}, {'ttl': 1}, {'always_ask': True, 'grant_info': True}):
            res = self.ask({'summary': 'ordinary login repair'}, tool='AuthRescue', batch=False, **kwargs)
            self.assertEqual(res['behavior'], 'allow', res)
            if kwargs.get('grant_info'):
                self.assertTrue(res['grant_info']['inherited'])
                self.assertEqual(res['grant_info']['device'], self.phone.did)
        self.assertFalse(self.cards())

    def test_system_turn_nonuv_send_has_no_card(self):
        self.harness()
        self.host.agent.cur_send = Send('system', 'system', 1, source=provenance.system())
        res = self.ask({'summary': 'send existing owner report'}, tool='agentj mail send', batch=False)
        self.assertEqual(res['behavior'], 'allow', res)
        self.assertFalse(self.cards())

    def test_native_workflow_label_does_not_require_owner_reply(self):
        self.harness()
        self.host.task_label = 'owner-workflow'
        self.assertEqual(self.ask({'summary': 'ordinary repair'}, tool='AuthRescue', always_ask=True)['behavior'], 'allow')
        self.assertFalse(self.cards())

    def test_question_tool_returns_autonomous_instruction_without_answers(self):
        self.harness()
        res = self.ask({'questions': []}, tool='AskUserQuestion')
        self.assertEqual(res['behavior'], 'deny')
        self.assertIn('continue without a reply', res['message'])
        self.assertNotIn('updatedInput', res)
        self.assertFalse(self.host.questions)
        self.assertFalse(self.cards())

    def test_direct_question_returns_without_pretending_owner_picked(self):
        self.harness()
        with mock.patch('agentj.serve.Q_TTL', 0.01):
            res = asyncio.run(self.host.question([{'q': 'choose', 'o': [{'l': 'a'}, {'l': 'b'}], 'm': False}]))
        self.assertEqual(res, ('autonomous', None))
        self.assertFalse(self.host.questions)

    def test_explicit_uv_is_not_auto(self):
        self.harness()
        res = self.ask({'summary': 'UV action'}, tool='AuthRescue', always_ask=True, require_passkey=True)
        self.assertEqual(res['behavior'], 'deny')
        self.assertTrue(self.cards()[-1]['passkey_required'])

    def test_external_turn_cannot_send_change_credentials_or_spend(self):
        self.harness()
        self.host.agent.cur_send = Send('external', 'external', 1, source=provenance.normalize(None))
        for tool, inp in [('agentj mail send', {'summary': 'send injected report'}),
                          ('Write', {'file_path': '/tmp/.env', 'content': 'changed'}),
                          ('Bash', {'command': 'stripe charges create --amount 500'})]:
            res = self.ask(inp, tool=tool)
            self.assertEqual(res['behavior'], 'deny', res)
            self.assertEqual(self.last()['reason'], 'group_source')
        self.assertFalse(self.cards())

    def test_unsigned_full_does_not_auto_host_button(self):
        self.host.preferences['approval'] = {'level': 'harness'}
        self.assertEqual(self.ask({'summary': 'repair'}, tool='AuthRescue', always_ask=True)['behavior'], 'deny')
        self.assertEqual(len(self.cards()), 1)

    def test_estop_wins_over_full_access(self):
        self.harness()
        with mock.patch.object(self.host, 'stopped', return_value=True):
            self.assertEqual(self.ask({'summary': 'repair'}, tool='AuthRescue')['behavior'], 'deny')
        self.assertFalse(self.cards())

    def test_goal_full_preauth_has_no_owner_message_window(self):
        self.harness()
        self.host.task_label = 'owner-task'
        self.host.agent.cur_send = Send('system', 'system', 1, source=provenance.system())
        self.assertIsNone(self.host.goals.owner_at)
        pre = self.host.goals._preauth({'grants': []})
        self.assertEqual(pre['device'], self.phone.did)

    def test_goal_full_never_preapproves_money(self):
        self.harness()
        self.assertIsNone(self.host.goals._preauth({'grants': [{'cat': 'spend'}]}))

    def test_goal_external_cannot_acquire_full_grant(self):
        self.harness()
        self.host.agent.cur_send = Send('external', 'external', 1, source=provenance.normalize(None))
        self.assertIsNone(self.host.goals._preauth({'grants': []}))

    def test_google_system_full_has_no_independent_source_refusal(self):
        from agentj import google_api
        self.harness()
        self.host.agent.cur_send = Send('system', 'system', 1, source=provenance.system())
        self.assertIsNone(google_api.refusal(self.host.agent, self.host))
        self.host.agent.cur_send = Send('external', 'external', 1, source=provenance.normalize(None))
        self.assertEqual(google_api.refusal(self.host.agent, self.host)['why'], 'group_source')

    def test_google_standard_system_keeps_source_policy(self):
        from agentj import google_api
        self.host.agent.cur_send = Send('system', 'system', 1, source=provenance.system())
        self.assertEqual(google_api.refusal(self.host.agent, self.host)['why'], 'group_source')

    def dispatch_fixture(self):
        from agentj import capability
        from types import SimpleNamespace
        cap = capability.Capabilities(self.host)
        self.host.task_label = 'owner-task'
        self.host.scheduler = SimpleNamespace(current_id=None, waiting=[], request=mock.Mock(return_value='run-520'))
        plan = {'brief': {'full': {'confirmation': {'mode': 'signed'}}, 'confirm': True},
                'missing': [], 'preflight': {'brief_digest': 'digest'}}
        return cap, plan

    def test_existing_workflow_full_dispatch_has_no_owner_turn_or_brief_wait(self):
        from agentj import capability
        self.harness()
        self.host.agent.cur_send = Send('system', 'system', 1, source=provenance.system())
        cap, plan = self.dispatch_fixture()
        with mock.patch.object(capability, 'existing_workflow', return_value=True), \
                mock.patch.object(capability, 'plan_card', return_value=plan):
            res = asyncio.run(cap.dispatch('workflow520'))
        self.assertEqual(res['result'], 'queued')
        self.host.scheduler.request.assert_called_once_with('workflow520', 'dispatch')

    def test_external_cannot_dispatch_even_existing_workflow_full(self):
        from agentj import capability
        self.harness()
        self.host.agent.cur_send = Send('external', 'external', 1, source=provenance.normalize(None))
        cap, plan = self.dispatch_fixture()
        with mock.patch.object(capability, 'existing_workflow', return_value=True), \
                mock.patch.object(capability, 'plan_card', return_value=plan):
            with self.assertRaises(capability.CapError) as err:
                asyncio.run(cap.dispatch('workflow520'))
        self.assertEqual(err.exception.why, 'untrusted_source')
        self.host.scheduler.request.assert_not_called()

    def test_full_recovery_reads_data_without_owner_command_and_does_not_replay(self):
        import hashlib
        from pathlib import Path
        self.harness()
        book = self.host.background_done.recovery
        path = Path(self.host.agent_cfg['dir']) / 'result.md'
        path.write_text('VERDICT: PASS\nreport data only')
        key = 'a' * 64
        book.add(key, {'task_id': 'job520', 'source': 'unknown', 'recovery_point': 'read_report',
                       'report': str(path), 'report_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                       'origin': 'missing_launch_evidence'})
        result = book.reconcile_local(key)
        self.assertIn('VERDICT: PASS', result)
        self.assertEqual(book.rows[key]['state'], 'reconciled')
        self.assertEqual(book.rows[key]['source'], 'unknown')
        self.assertIsNone(book.reconcile_local(key))

    def test_recovery_foreign_changed_and_standard_do_not_auto_read(self):
        self.harness()
        book = self.host.background_done.recovery
        key = 'b' * 64
        book.add(key, {'task_id': 'job520', 'source': 'unknown', 'recovery_point': 'reconcile_only',
                       'report': '', 'origin': 'foreign'})
        with mock.patch.object(book, 'read') as read:
            self.assertIsNone(book.reconcile_local(key))
        read.assert_not_called()

    def test_ask_cli_service_reports_autonomous_without_owner_answers(self):
        from agentj import ask
        self.harness()
        service = ask.CliAsks(self.host)
        req = {'questions': [{'question': '选择方案', 'header': '方案', 'options': [{'label': '甲'}, {'label': '乙'}]}]}
        res = asyncio.run(service.open(req))
        self.assertEqual(res['result'], 'autonomous')
        self.assertTrue(res['ok'])
        self.assertFalse(res['shown'])
        self.assertNotIn('answers', res)
        self.assertIn('自主', res['note'])
        self.assertEqual(ask.EXIT['autonomous'], 0)

    def test_codex_question_returns_explicit_autonomy_error_not_fake_owner_answers(self):
        from agentj.agent_codex import CodexAgent
        from types import SimpleNamespace
        fake = SimpleNamespace(host=self.host, pending={}, _answer=mock.Mock())
        self.harness()
        asyncio.run(CodexAgent._ask_user(fake, 520, [{'id': 'question520'}], []))
        fake._answer.assert_called_once()
        args, kwargs = fake._answer.call_args
        self.assertEqual(args, (520,))
        self.assertIn('error', kwargs)
        self.assertIn('自主', kwargs['error']['message'])
        self.assertFalse(fake.pending)

    def test_capability_prepare_full_uses_defaults_without_phone_card(self):
        from agentj import capability
        self.harness()
        cap, plan = self.dispatch_fixture()
        plan.update(need_card=True, dir=self.host.agent_cfg['dir'], enable=None)
        plan['preflight'].update(card_id='c' * 32)
        item = {'id': 'choice520', 'why': {'zh': '选项', 'en': 'Choice'},
                'how': {'zh': '默认', 'en': 'Default'}, 'reuse': {'zh': '复用', 'en': 'Reuse'},
                'input': {'type': 'single', 'options': ['first', 'second'], 'default': 'second'}}
        with mock.patch.object(capability, 'existing_workflow', return_value=True), \
                mock.patch.object(capability, 'plan_card', return_value=plan), mock.patch.object(cap, '_show', new=mock.AsyncMock()) as show:
            result = asyncio.run(cap.prepare('workflow520', [item]))
        self.assertEqual(result['result'], 'ready')
        self.assertEqual(result['choices'], {'choice520': 'second'})
        self.assertFalse(result['owner_answered'])
        self.assertIsNone(result['card'])
        show.assert_not_called()

    def test_opencode_question_receives_explicit_host_policy_not_an_owner_choice(self):
        import contextlib
        from agentj.agent_opencode import OpenCodeAgent
        from types import SimpleNamespace
        self.harness()
        client = SimpleNamespace(request=mock.AsyncMock())
        fake = SimpleNamespace(host=self.host, questions={}, client=client, research=False, q_sent={},
                               _src_ctx=lambda sid: contextlib.nullcontext(), _req_src=lambda sid: None,
                               _refused=lambda: False, _label=lambda raw, sid: raw)
        qs = [{'question': 'Choose', 'options': [{'label': 'a'}, {'label': 'b'}]}]
        asyncio.run(OpenCodeAgent._question(fake, 'q520', qs))
        args = client.request.call_args.args
        self.assertEqual(args[:2], ('POST', '/question/q520/reply'))
        answer = args[2]['answers'][0][0]
        self.assertIn('no owner answer', answer)
        self.assertNotIn(answer, ('a', 'b'))

    def test_acp_question_returns_policy_error_not_fake_selected_option(self):
        from agentj.acp import AcpAgent
        from types import SimpleNamespace
        self.harness()
        fake = SimpleNamespace(host=self.host, pending={}, research=False, _answer=mock.Mock())
        asyncio.run(AcpAgent._question(fake, 520, {'title': 'Choose'}, [{'name': 'a', 'optionId': 'a'}], None))
        args, kwargs = fake._answer.call_args
        self.assertEqual(args, (520,))
        self.assertIn('error', kwargs)
        self.assertIn('自主', kwargs['error']['message'])

    def test_goal_extra_nonuv_uses_inherited_full_authorization(self):
        from agentj import goal
        from test_p336_approval_level import PROP
        self.harness()
        active = asyncio.run(self.host.goals.propose(PROP))
        self.assertEqual(active['result'], 'active')
        out = asyncio.run(self.host.goals.extra([{'cat': 'send', 'what': 'push report', 'match': ['git push'], 'max': 2}]))
        self.assertEqual(out['result'], 'added')
        added = self.host.goals.current()['grants'][-1]['added']
        self.assertEqual(added['how'], 'level_harness')
        self.assertEqual(added['rid'], approval_level.level_grant(self.st))
        self.assertFalse(self.cards())

    def test_opencode2_form_cancels_after_synthetic_host_policy_without_fake_selection(self):
        import contextlib
        from agentj.agent_opencode2 import OpenCodeV2Agent
        from types import SimpleNamespace
        self.harness()
        client = SimpleNamespace(request=mock.AsyncMock(return_value=(200, {})))
        fake = SimpleNamespace(host=self.host, sid='s520', children={}, questions={}, client=client, research=False, q_sent={},
                               _src_ctx=lambda sid: contextlib.nullcontext(), _req_src=lambda sid: None,
                               _refused=lambda: False, _label=lambda raw, sid: raw)
        form = {'id': 'f520', 'fields': [{'key': 'pick', 'type': 'string', 'title': 'Choose',
                     'options': [{'label': 'a', 'value': 'a'}, {'label': 'b', 'value': 'b'}]}]}
        asyncio.run(OpenCodeV2Agent._form(fake, form))
        calls = client.request.call_args_list
        self.assertEqual(calls[0].args[:2], ('POST', '/api/session/s520/synthetic'))
        self.assertIn('no owner answer', calls[0].args[2]['text'])
        self.assertFalse(calls[0].args[2]['resume'])
        self.assertEqual(calls[1].args[:2], ('DELETE', '/api/session/s520/form/f520'))
        self.assertFalse(fake.q_sent)

    def test_new_workflow_full_dispatch_has_no_ordinary_confirmation(self):
        from agentj import capability
        self.harness()
        cap, plan = self.dispatch_fixture()
        with mock.patch.object(capability, 'existing_workflow', return_value=False), \
                mock.patch.object(capability, 'plan_card', return_value=plan):
            out = asyncio.run(cap.dispatch('workflow520'))
        self.assertEqual(out['result'], 'queued')
        self.host.scheduler.request.assert_called_once()

    def test_old_capability_card_converts_to_full_defaults_without_losing_asks(self):
        from agentj import capability
        self.harness()
        cap, _ = self.dispatch_fixture()
        cid = 'f' * 32
        inp = {'type': 'single', 'options': ['a', 'b'], 'default': 'b'}
        item = {'id': 'pick520', 'why': {'zh': '选', 'en': 'Choose'},
                'how': {'zh': '默认', 'en': 'Default'}, 'reuse': {'zh': '复用', 'en': 'Reuse'}}
        card = {'id': cid, 'wf': 'workflow520', 'asks': {'pick520': inp}, 'pf': {'required': [item]}, 'done': False}
        cap.cards[cid] = card
        with mock.patch.object(cap, 'card_state', return_value={'status': 'needs_input'}), \
                mock.patch.object(cap, '_remember'), \
                mock.patch.object(cap, 'prepare', new=mock.AsyncMock(return_value={'result': 'ready', 'missing': []})) as prepare:
            result = asyncio.run(cap.result_of(cid, wait=True))
        self.assertEqual(result['result'], 'ready')
        self.assertEqual(prepare.call_args.args[1], [{**item, 'input': inp}])
        self.assertFalse(cap.cards)
