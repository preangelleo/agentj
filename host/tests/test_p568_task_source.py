"""P568 A: task delegation must not borrow the main conversation's source or approvals."""
import _hermetic  # noqa: F401
import asyncio
from types import SimpleNamespace as NS
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from agentj import provenance, approval_jev, tasks
from agentj.agent_codex import CodexAgent
from agentj.agent_opencode import OpenCodeAgent
from test_p551_confirm_levels import Base


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
            await a._decide(1, 'Bash', {'command': 'date'}, {'decision': 'accept'}, {'decision': 'decline'}, True)
            a._answer.assert_called_once_with(1, {'decision': 'decline'})
            self.host.agent_notice.assert_called_once()
            self.assertIn('owner did not reject', self.host.agent_notice.call_args.args[0])
            self.assertEqual(a.call.call_args.args[0], 'thread/inject_items')
            self.assertIn(self.host.agent_notice.call_args.args[0], str(a.call.call_args))
        asyncio.run(run())

    def test_low_risk_task_not_guest_or_system(self):
        cfg = {'high_risk_warnings': True}
        with provenance.task_run():
            self.assertTrue(approval_jev.low_risk_pass(self.host, cfg, 'Bash', {'command': 'date'}))
            self.assertTrue(approval_jev.low_risk_pass(self.host, cfg, 'Write', {'file_path': '/tmp/task-notes.txt', 'content': 'notes'}))
            with provenance.request_source(provenance.normalize(None)):
                self.assertFalse(approval_jev.low_risk_pass(self.host, cfg, 'Bash', {'command': 'date'}))
        for source in (provenance.email_guest('guest@example.com'), provenance.telegram_group(-1, 2, 'proxy', 1), None):
            self.host.agent.native_source = source
            self.assertFalse(approval_jev.low_risk_pass(self.host, cfg, 'Bash', {'command': 'date'}))
            result = self.ask('date')
            self.assertEqual(self.last()['reason'], 'group_source')
            self.assertNotIn('Telegram', result['message'])
        self.assertEqual(self.cards(), [])
        # P570: `system` inferred without a host Send (a native wake-up) stays refused (round-2 F2)
        self.host.agent.native_source = provenance.system()
        self.assertFalse(approval_jev.low_risk_pass(self.host, cfg, 'Bash', {'command': 'date'}))
        self.ask('date')
        self.assertEqual(self.last()['reason'], 'group_source')
        self.assertEqual(self.cards(), [])
        # P570: a host-written system turn (heartbeat Send) gets no low-risk pass either, but it ASKS — a card
        from agentj.heartbeat import HeartbeatSend
        self.host.agent.cur_send = HeartbeatSend(0, 'heartbeat', 'k', 'd')
        self.assertFalse(approval_jev.low_risk_pass(self.host, cfg, 'Bash', {'command': 'date'}))
        self.ask('date')
        self.assertNotEqual(self.last()['reason'], 'group_source')
        self.assertEqual(len(self.cards()), 1)
        self.assertNotIn('batch', self.cards()[-1])

    def test_task_outward_secret_delete_need_cards_even_with_background_flag(self):
        with provenance.task_run():
            for command in ('git push origin main', 'cat .env', 'rm -rf /tmp/task-output'):
                n = len(self.cards())
                self.ask(command, background='task')
                self.assertEqual(len(self.cards()), n + 1, command)
                self.assertNotEqual(self.last()['reason'], 'group_source')
            self.assertTrue(self.cards()[-1]['passkey_required'])

    def test_three_permission_paths_bash_and_write(self):
        async def run():
            cfg = {**self.host.agent_cfg, '_workflow_ceo': True, 'high_risk_warnings': False}
            with provenance.task_run() as scope:
                for tool, inp in (('Bash', {'command': 'date'}), ('Write', {'file_path': '/tmp/task.txt', 'content': 'notes'})):
                    a = CodexAgent(self.host, cfg, persist=False)
                    a._answer = Mock()
                    a.call = AsyncMock()
                    await a._decide(1, tool, inp, {'decision': 'accept'}, {'decision': 'decline'}, True)
                    self.assertNotEqual(self.last()['reason'], 'group_source')
                    a._answer.assert_called_once()
                    oc = OpenCodeAgent(self.host, cfg, persist=False)
                    oc.client = NS(request=AsyncMock())
                    oc._reply_permission = AsyncMock()
                    req = {'permission': 'bash' if tool == 'Bash' else 'edit', 'patterns': ['date' if tool == 'Bash' else '/tmp/task.txt']}
                    if tool == 'Write':
                        req['metadata'] = {'diff': '--- /dev/null\n+++ /tmp/task.txt\n@@ -0,0 +1 @@\n+notes'}
                    await oc._decide('r', req, asyncio.get_running_loop().create_future())
                    self.assertNotEqual(self.last()['reason'], 'group_source')
                    oc._reply_permission.assert_awaited_once()
                    # Claude arrives on a different socket-handler Task, carrying only its spent claim's scope.
                    w = object()
                    self.host.perm_w = w
                    self.host._perm_send = AsyncMock()
                    await self.host._perm_ask(w, {'id': 3, 'tool': tool, 'input': inp}, {}, task_scope=scope)
                    self.assertNotEqual(self.last()['reason'], 'group_source')
        asyncio.run(run())
