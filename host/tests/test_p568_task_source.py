"""P568 A: task delegation must not borrow the main conversation's source or approvals."""
import _hermetic  # noqa: F401
import asyncio
import shlex
import tempfile
from types import SimpleNamespace as NS
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from agentj import provenance, approval_jev, tasks
from agentj.agent_codex import CodexAgent
from agentj.agent_opencode import OpenCodeAgent
from test_p551_confirm_levels import Base


async def signed_adapter(test, awaitable, *, approve=True):
    """Drive a real native permission/card with the real v2 device encoder."""
    from _p541_device import Phone as V2Phone
    task = asyncio.create_task(awaitable)
    try:
        for _ in range(200):
            if test.host.asks or task.done(): break
            await asyncio.sleep(.001)
        test.assertFalse(task.done(), 'native permission must wait for an actual signed tap')
        ask = next(iter(test.host.asks.values()))
        card = test.host._ask_msg(ask, test.s)
        test.assertEqual(card['approval_v'], 2)
        await test.host._answer(test.s, V2Phone.answer(test.phone, card, approve))
        result = await asyncio.wait_for(task, 2)
        test.assertEqual(test.last()['reason'], 'device')
        return result, card
    finally:
        if not task.done(): task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def guest_boundary(test):
    """Actual PeerSessions launch, real OS fence, fake vendors, no owner data."""
    import json
    import test_peer_session as peer_fixture
    from agentj import peer_guard
    from agentj.peer_session import PeerSessions
    test.assertTrue(peer_fixture.FENCE, 'a real OS fence is required, not a mock authorization counter')
    rig = peer_fixture.FakeHarness()
    rig.setUp()
    try:
        rig.mode({'tool': True})
        for kind in ('claude', 'codex', 'opencode'):
            ps = PeerSessions(peer_fixture.Host(rig.st, kind), rig.store)
            try:
                result = await ps.turn(rig.store.get(peer_fixture.FID), rig.store.group('default'),
                                       peer_guard.wrap(peer_fixture.FID, '小明', '你好'))
                test.assertEqual((result.decision, result.error), ('ask_owner', 'tool_call'), kind)
                test.assertTrue(result.tools, kind)
                record = rig.starts(kind)[-1]
                rig.check_common(kind, record)
                argv = record['argv']
                if kind == 'claude':
                    test.assertEqual(argv[argv.index('--tools') + 1], '')
                    test.assertIn('--strict-mcp-config', argv)
                elif kind == 'codex':
                    disabled = {argv[i + 1] for i, value in enumerate(argv) if value == '--disable'}
                    test.assertTrue({'shell_tool', 'unified_exec', 'multi_agent'} <= disabled)
                    test.assertIn('sandbox_mode="read-only"', argv)
                else:
                    config = json.loads(record['env']['OPENCODE_CONFIG_CONTENT'])
                    test.assertEqual(config['tools'], {'*': False})
                    test.assertEqual(config['permission'], {'*': 'deny'})
            finally:
                await ps.stop_all()
    finally:
        rig.doCleanups()


class TaskSource(Base):
    def setUp(self):
        super().setUp()
        self.host.agent.native_source = provenance.normalize(None)

    def test_task_is_delegated_and_scope_expires(self):
        with provenance.task_run() as scope:
            self.assertEqual(provenance.rank(provenance.turn_source(self.host.agent)), 1)
            self.assertFalse(provenance.is_owner(provenance.turn_source(self.host.agent)))
            self.assertTrue(provenance.delegated_turn(self.host.agent))
            with provenance.request_source(provenance.email_guest('guest@example.com')):
                self.assertFalse(provenance.delegated_turn(self.host.agent))
        with provenance.task_request(scope):
            self.assertFalse(provenance.delegated_turn(self.host.agent))
        self.assertTrue(provenance.untrusted_turn(self.host.agent))

    def test_actual_scheduler_manual_and_scheduled_admission(self):
        from test_p418_tasks import task_dir
        wd = self.host.agent_cfg['dir']
        task_dir(Path(wd), 'source-test', '0 8 * * *')
        tasks.set_enabled(self.st, wd, 'source-test', True, 'terminal')
        self.host.agent.halt = AsyncMock()
        seen = []
        async def execute(*args):
            self.assertTrue(provenance.delegated_turn(self.host.agent))
            self.assertFalse(provenance.is_owner(provenance.turn_source(self.host.agent)))
            seen.append(provenance.task_scope())
            return 'VERDICT: ok — done', 0
        sch = self.host.scheduler
        sch._exec = execute
        with patch('agentj.quota.route', AsyncMock(return_value=('claude', None))):
            for trigger in ('manual', 'schedule'):
                asyncio.run(sch.run('source-test', trigger))
        self.assertEqual(len(seen), 2)
        self.assertTrue(all(not s['live'] for s in seen))
        tasks.set_enabled(self.st, wd, 'source-test', False, 'terminal')
        asyncio.run(sch.run('source-test', 'schedule'))
        self.assertEqual(len(seen), 2, 'a disabled schedule never gets task authority')

    def test_codex_decline_delivers_host_reason_to_model_and_owner(self):
        async def run():
            a = CodexAgent(self.host, self.host.agent_cfg, persist=False)
            a._answer = Mock()
            a.call = AsyncMock()
            self.host.agent_notice = Mock()
            await signed_adapter(self, a._decide(1, 'Bash', {'command': 'date'}, {'decision': 'accept'}, {'decision': 'decline'}, True), approve=False)
            a._answer.assert_called_once_with(1, {'decision': 'decline'})
            self.host.agent_notice.assert_called_once()
            self.assertIn('denied', self.host.agent_notice.call_args.args[0])
            self.assertEqual(self.last()['reason'], 'device')
            self.assertEqual(a.call.call_args.args[0], 'thread/inject_items')
            self.assertIn(self.host.agent_notice.call_args.args[0], a.call.call_args.args[1]['items'][0]['content'][0]['text'])
        asyncio.run(run())

    def test_low_risk_task_not_guest_or_system(self):
        cfg = {'high_risk_warnings': True}
        with provenance.task_run():
            self.assertFalse(approval_jev.low_risk_pass(self.host, cfg, 'Bash', {'command': 'date'}))
            self.assertFalse(approval_jev.low_risk_pass(self.host, cfg, 'Write', {'file_path': '/tmp/task-notes.txt', 'content': 'notes'}))
            with provenance.request_source(provenance.normalize(None)):
                self.assertFalse(approval_jev.low_risk_pass(self.host, cfg, 'Bash', {'command': 'date'}))
        from test_p512_policy import native_tap
        for source in (provenance.email_guest('guest@example.com'), provenance.telegram_group(-1, 2, 'proxy', 1), None):
            self.host.agent.native_source = source
            self.assertFalse(approval_jev.low_risk_pass(self.host, cfg, 'Bash', {'command': 'date'}))
            result, card = asyncio.run(native_tap(self.host, self.phone, self.s, 'Bash', {'command': 'date'}))
            self.assertEqual(result['behavior'], 'allow')
            self.assertFalse(card.get('passkey_required', False))
            self.assertEqual(self.host.agent.native_source, source, 'historical label is never promoted to owner')
        # These are native permissions in the served owner harness. Live peer
        # execution is separately restricted by the real NoTools/fence boundary.
        asyncio.run(guest_boundary(self))
        self.host.agent.native_source = provenance.system()
        self.assertFalse(approval_jev.low_risk_pass(self.host, cfg, 'Bash', {'command': 'date'}))
        result, _ = asyncio.run(native_tap(self.host, self.phone, self.s, 'Bash', {'command': 'date'}))
        self.assertEqual(result['behavior'], 'allow')
        from agentj.heartbeat import HeartbeatSend
        self.host.agent.cur_send = HeartbeatSend(0, 'heartbeat', 'k', 'd')
        self.assertFalse(approval_jev.low_risk_pass(self.host, cfg, 'Bash', {'command': 'date'}))
        result, _ = asyncio.run(native_tap(self.host, self.phone, self.s, 'Bash', {'command': 'date'}))
        self.assertEqual(result['behavior'], 'allow')

    def test_task_outward_secret_delete_need_cards_even_with_background_flag(self):
        from test_p512_policy import native_tap
        with provenance.task_run():
            for command in ('git push origin main', 'cat .env', 'rm -rf ' + shlex.quote(str(Path(tempfile.gettempdir()) / 'task-output'))):
                n = len(self.cards())
                result, card = asyncio.run(native_tap(self.host, self.phone, self.s, 'Bash', {'command': command}, background='task'))
                self.assertEqual(result['behavior'], 'allow')
                self.assertEqual(len(self.cards()), n + 1, command)
                self.assertFalse(card.get('passkey_required', False))
            result, card = asyncio.run(native_tap(self.host, self.phone, self.s, 'Bash', {'command': 'rm -rf ~/Documents/old'}, background='task'))
            self.assertEqual(result['behavior'], 'allow')
            self.assertFalse(card.get('passkey_required', False))
            # Control-root ancestors remain listed, independent of background.
            result, card = asyncio.run(native_tap(self.host, self.phone, self.s, 'Bash', {'command': 'rm -rf ~'}, approve=False, background='task'))
            self.assertTrue(card['passkey_required'])
            self.assertEqual(result['behavior'], 'deny')

    def test_three_permission_paths_bash_and_write(self):
        async def run():
            cfg = {**self.host.agent_cfg, '_workflow_ceo': True, 'high_risk_warnings': False}
            with provenance.task_run() as scope:
                for tool, inp in (('Bash', {'command': 'date'}), ('Write', {'file_path': '/tmp/task.txt', 'content': 'notes'})):
                    a = CodexAgent(self.host, cfg, persist=False)
                    a._answer = Mock()
                    a.call = AsyncMock()
                    await signed_adapter(self, a._decide(1, tool, inp, {'decision': 'accept'}, {'decision': 'decline'}, True))
                    self.assertNotEqual(self.last()['reason'], 'group_source')
                    a._answer.assert_called_once()
                    oc = OpenCodeAgent(self.host, cfg, persist=False)
                    oc.client = NS(request=AsyncMock())
                    oc._reply_permission = AsyncMock()
                    req = {'permission': 'bash' if tool == 'Bash' else 'edit', 'patterns': ['date' if tool == 'Bash' else '/tmp/task.txt']}
                    if tool == 'Write':
                        req['metadata'] = {'diff': '--- /dev/null\n+++ /tmp/task.txt\n@@ -0,0 +1 @@\n+notes'}
                    await signed_adapter(self, oc._decide('r', req, asyncio.get_running_loop().create_future()))
                    self.assertNotEqual(self.last()['reason'], 'group_source')
                    oc._reply_permission.assert_awaited_once()
                    # Claude arrives on a different socket-handler Task, carrying only its spent claim's scope.
                    w = object()
                    self.host.perm_w = w
                    self.host._perm_send = AsyncMock()
                    await signed_adapter(self, self.host._perm_ask(w, {'id': 3, 'tool': tool, 'input': inp}, {}, task_scope=scope))
                    self.assertNotEqual(self.last()['reason'], 'group_source')
        asyncio.run(run())
