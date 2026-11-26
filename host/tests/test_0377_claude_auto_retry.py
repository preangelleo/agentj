"""Native deny-to-ask routing and the real signed, request-bound Host UV decision."""
import _hermetic  # noqa: F401
import asyncio
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest import mock

from agentj import approval_review, approvals, claude_auto_retry, provenance, shared, shared_hook
from _p541_device import Phone, _owner_host
from test_l1 import _ready, _state


class ClaudeAutoRetry(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.host = _owner_host(_state(tmp.name), [])
        self.phone = Phone(self.host.st, with_passkey=True)
        self.device = _ready(self.host, self.phone)
        self.cwd = str(Path(tmp.name).resolve())
        self.command = 'systemd-run --user --collect --unit=agentj-fixture codex exec fixture'
        cfg = {'kind': 'claude', 'dir': self.cwd, 'session_mode': 'shared'}
        agent = self.host.agent = shared.SharedClaudeAgent(self.host, cfg)
        self.host.agent_cfg = cfg
        agent.session = {'sessionId': 'fixture-native', 'version': '2.1.323'}
        agent.phone_turn = agent.phone_seen = True
        agent.turn_closed = False
        page = self.host.hist_add({'k': 'phone', 'name': 'Owner', 'text': 'Run the fixture.'}, '', 'open')
        origin = approval_review.remember(self.host, page, provenance.phone())
        agent.cur_send = SimpleNamespace(source=provenance.phone(), approval_origin=origin)
        self.gone = asyncio.get_running_loop().create_future()
        self.retry = claude_auto_retry.Retry(agent)
        # Dispatch recognition has its own real process/path fixtures. This
        # test never launches a job or calls a remote service.
        proof = mock.patch.object(self.host, '_dispatch_check', return_value=('agentj-fixture.service', 'ok'))
        self.launch_proof = proof.start()
        self.addCleanup(proof.stop)

    def event(self, name, call='original', **fields):
        event = {'hook_event_name': name, 'tool_name': 'Bash', 'tool_input': {'command': self.command},
                 'cwd': self.cwd, 'permission_mode': 'auto', 'reason': '[Undermining Oversight]'}
        if call is not None:
            event['tool_use_id'] = call
        return {**event, **fields}

    async def arm(self):
        self.assertEqual(await self.retry.before(self.event('PreToolUse'), self.gone), {})
        row = await self.retry.denied(self.event('PermissionDenied'))
        self.assertTrue(row['hookSpecificOutput']['retry'])

    async def card(self):
        await self.arm()
        task = asyncio.create_task(self.retry.before(self.event('PreToolUse', 'retry'), self.gone))
        self.addAsyncCleanup(self.clean, task)
        for _ in range(150):
            if self.host.asks or task.done():
                break
            await asyncio.sleep(.01)
        self.assertFalse(task.done(), task.result() if task.done() else '')
        return task, next(iter(self.host.asks.values()))

    async def clean(self, task):
        if not self.gone.done():
            self.gone.set_result(True)
        await asyncio.gather(task, return_exceptions=True)

    def set_workdir(self, cwd):
        self.cwd = str(cwd)
        self.host.agent.cfg['dir'] = self.cwd

    async def test_card_workdir_under_home(self):
        home = '/home/agentj-fixture-owner'
        self.set_workdir(home + '/coding')
        expanduser = os.path.expanduser

        def fixture_expanduser(path):
            if path == '~' or path.startswith('~/'):
                return home + path[1:]
            return expanduser(path)

        with mock.patch.object(os.path, 'expanduser', side_effect=fixture_expanduser):
            task, ask = await self.card()
            self.assertTrue(ask.require_passkey)
            self.assertEqual(ask.summary.split('\n', 2)[1], 'Working directory: ~/coding')
            await self.host._answer(self.device, self.phone.answer(self.host._ask_msg(ask, self.device), True))
            self.assertEqual((await task)['hookSpecificOutput']['permissionDecision'], 'ask')
            self.assertEqual(self.retry.permission(self.event('PermissionRequest', None), self.gone), {'behavior': 'allow'})
            self.assertEqual(self.retry.permission(self.event('PermissionRequest', None), self.gone), {'behavior': 'deny'})

    async def test_card_workdir_outside_home(self):
        _, ask = await self.card()
        self.assertTrue(ask.require_passkey)
        self.assertEqual(ask.summary.split('\n', 2)[1], f'Working directory: {self.cwd}')

    async def test_card_workdir_with_spaces_and_symlink(self):
        actual = Path(self.cwd, 'real work folder')
        actual.mkdir()
        alias = Path(self.cwd, 'linked work folder')
        alias.symlink_to(actual, target_is_directory=True)
        self.set_workdir(alias)
        _, ask = await self.card()
        self.assertTrue(ask.require_passkey)
        self.assertEqual(ask.summary.split('\n', 2)[1], f'Working directory: {actual}')

    async def assert_invalid_workdir_display(self, line):
        await self.arm()
        display_text = approval_review.display_text

        def forged_display(text, normalizer):
            shown = display_text(text, normalizer)
            if shown.startswith('Bash\nWorking directory: '):
                tool, _, body = shown.split('\n', 2)
                return f'{tool}\n{line}\n{body}'
            return shown

        with mock.patch.object(approval_review, 'display_text', side_effect=forged_display):
            result = await self.retry.before(self.event('PreToolUse', 'retry'), self.gone)
        self.assertEqual(result['hookSpecificOutput']['permissionDecision'], 'deny')
        self.assertFalse(self.host.asks)
        self.assertIsNone(self.retry.approved)
        self.assertIn('invalid_display', [row['reason'] for row in approvals.read_log(self.host.st)])

    async def test_forged_workdir_heading_is_invalid_display(self):
        await self.assert_invalid_workdir_display(f'Forged Working directory: {self.cwd}')

    async def test_mismatched_workdir_is_invalid_display(self):
        await self.assert_invalid_workdir_display('Working directory: ~/other')

    async def test_paired_uv_releases_exact_native_retry_once(self):
        task, ask = await self.card()
        self.assertTrue(ask.require_passkey)
        signed = self.phone.answer(self.host._ask_msg(ask, self.device), True)
        no_uv = {k: v for k, v in signed.items() if k != 'fa'}
        await self.host._answer(self.device, no_uv)
        self.assertFalse(ask.fut.done())
        await self.host._answer(self.device, signed)
        self.assertEqual((await task)['hookSpecificOutput']['permissionDecision'], 'ask')
        self.assertEqual(self.retry.permission(self.event('PermissionRequest', None), self.gone),
                         {'behavior': 'allow'})
        receipts = [row for row in approvals.read_log(self.host.st) if row.get('reason') == 'device']
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]['device'], self.phone.did)
        self.assertEqual(approvals.check_record(self.host.st, receipts[0]), 'ok')
        self.assertEqual(self.retry.permission(self.event('PermissionRequest', None), self.gone),
                         {'behavior': 'deny'})
        self.assertFalse(self.host.asks)
        await self.host._answer(self.device, signed)
        self.assertEqual(len([row for row in approvals.read_log(self.host.st) if row.get('reason') == 'device']), 1)
        self.assertIsNone(self.retry.permission(
            self.event('PermissionRequest', None, tool_input={'command': 'touch other'}), self.gone))

    async def test_changed_input_cannot_take_prompt_or_approval(self):
        task, ask = await self.card()
        other = self.event('PermissionRequest', None, tool_input={'command': 'touch other'})
        self.assertIsNone(self.retry.permission(other, self.gone))
        self.assertIsNone(self.retry.approved)
        await self.host._answer(self.device, self.phone.answer(self.host._ask_msg(ask, self.device), True))
        self.assertEqual((await task)['hookSpecificOutput']['permissionDecision'], 'ask')
        self.assertEqual(self.retry.permission(other, self.gone), {'behavior': 'deny'})
        self.assertEqual(self.retry.permission(self.event('PermissionRequest', None), self.gone), {'behavior': 'allow'})

    async def test_expiry_and_changed_turn_deny(self):
        task, ask = await self.card()
        await self.host._answer(self.device, self.phone.answer(self.host._ask_msg(ask, self.device), True))
        self.assertEqual((await task)['hookSpecificOutput']['permissionDecision'], 'ask')
        with mock.patch.object(claude_auto_retry.time, 'monotonic', return_value=self.retry.context.until):
            self.assertEqual(self.retry.permission(self.event('PermissionRequest', None), self.gone),
                             {'behavior': 'deny'})
        self.assertFalse(self.host.asks)
        self.retry = claude_auto_retry.Retry(self.host.agent)
        await self.arm()
        self.host.agent.turn_gen += 1
        row = await self.retry.before(self.event('PreToolUse', 'retry'), self.gone)
        self.assertEqual(row['hookSpecificOutput']['permissionDecision'], 'deny')
        self.assertFalse(self.host.asks)

    async def test_without_classifier_verdict_or_verified_source_no_retry(self):
        self.assertEqual(await self.retry.denied(self.event('PermissionDenied')), {})
        await self.retry.before(self.event('PreToolUse'), self.gone)
        for fields in ({'reason': 'Classifier unavailable'}, {'agent_id': 'child'},
                       {'permission_mode': 'default'}, {'cwd': '/other'}):
            self.assertEqual(await self.retry.denied(self.event('PermissionDenied', **fields)), {})
        self.host.agent.cur_send.source = provenance.normalize(None)
        self.assertEqual(await self.retry.denied(self.event('PermissionDenied')), {})
        self.launch_proof.assert_not_called()

    async def test_unregistered_or_concurrent_identical_calls_do_not_retry(self):
        await self.retry.before(self.event('PreToolUse'), self.gone)
        self.launch_proof.return_value = (None, 'shape')
        self.assertEqual(await self.retry.denied(self.event('PermissionDenied')), {})
        await self.retry.before(self.event('PreToolUse', 'second'), self.gone)
        self.launch_proof.return_value = ('agentj-fixture.service', 'ok')
        self.assertEqual(await self.retry.denied(self.event('PermissionDenied')), {})

    async def test_never_creates_native_ask_before_uv_and_rechecks_launcher(self):
        task, ask = await self.card()
        self.assertFalse(task.done())
        self.assertIsNone(self.retry.approved)
        self.launch_proof.return_value = (None, 'shape')
        await self.host._answer(self.device, self.phone.answer(self.host._ask_msg(ask, self.device), True))
        self.assertEqual((await task)['hookSpecificOutput']['permissionDecision'], 'deny')
        self.assertEqual(self.retry.permission(self.event('PermissionRequest', None), self.gone), {'behavior': 'deny'})

    async def test_skipped_native_prompt_cannot_block_next_turn(self):
        task, ask = await self.card()
        self.host.agent._auto_retry = self.retry
        await self.host._answer(self.device, self.phone.answer(self.host._ask_msg(ask, self.device), True))
        self.assertEqual((await task)['hookSpecificOutput']['permissionDecision'], 'ask')
        self.host.agent.turn_gen += 1
        different = self.event('PermissionRequest', None, tool_input={'command': 'touch other'})
        current = claude_auto_retry.current(self.host.agent, different)
        self.assertIsNone(current.permission(different, self.gone))
        previous = self.event('PermissionRequest', None)
        retired = claude_auto_retry.current(self.host.agent, previous)
        self.assertEqual(retired.permission(previous, self.gone), {'behavior': 'deny'})

    async def test_wrapped_dispatch_can_request_exact_review(self):
        self.command = "systemd-run --user --collect --unit=agentj-fixture bash -c 'codex exec fixture'"
        await self.arm()

    async def test_dispatch_options_cannot_lower_permissions(self):
        self.command += ' --"sandbox" danger-full-access'
        await self.retry.before(self.event('PreToolUse'), self.gone)
        self.assertEqual(await self.retry.denied(self.event('PermissionDenied')), {})
        self.launch_proof.assert_not_called()

    def test_optional_hook_preserves_existing_permission_settings(self):
        dest = Path(self.cwd, '.claude', 'settings.local.json')
        dest.parent.mkdir()
        import json
        permissions = {'allow': ['Read'], 'deny': ['Bash(echo denied)']}
        dest.write_text(json.dumps({'permissions': permissions}))
        shared_hook.install(self.cwd, '/fixture/shared-claude.sock')
        self.assertNotIn('PermissionDenied', json.loads(dest.read_text())['hooks'])
        shared_hook.install(self.cwd, '/fixture/shared-claude.sock', denial_hook=True)
        doc = json.loads(dest.read_text())
        self.assertEqual(doc['permissions'], permissions)
        self.assertIn('PermissionDenied', doc['hooks'])
