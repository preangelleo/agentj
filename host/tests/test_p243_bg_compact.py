"""P243 phone /compact and /clear on a shared Claude session with background work: Claude Code 2.1.296 keeps its registry at
"shell" (background Bash) or "busy" (background sub-agent) after the turn ended. Our hooks own the main thread; background
work alone no longer holds the control. Entirely local fixtures (registry status taken from a real probe of Claude Code 2.1.296)."""
import _hermetic  # noqa: F401
import asyncio
import unittest
from unittest.mock import Mock, patch
from agentj import shared
from agentj.agent import Cmd
from agentj.shared_state import AgentState


class Clock:
    """Debounce timers fire at once: the Stop's idle is immediate (the real 1.2 s debounce is AgentState's own test)."""
    def __init__(self): self.at = 0
    def now(self): return self.at
    def call_later(self, delay, fn):
        fn(); return Mock()


STOP_BG_SHELL = {'hook_event_name': 'Stop', 'last_assistant_message': '已启动', 'background_tasks': [{'id': 'b7x', 'type': 'shell'}]}


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.host = Mock(); self.host.lang = 'zh'; self.host.stopped.return_value = False
        self.a = shared.SharedClaudeAgent(self.host, {'kind': 'claude', 'dir': '/fixture', '_workflow_ceo': True})
        self.a.session = {'sessionId': 'owner-session', 'pid': 123, 'status': 'idle'}
        self.a.native = Mock(pid=123); self.a.master = 42
        self.a.read_statusline = Mock()
        self.a.cur_cmd = Cmd('compact', turn=9)
        self.a.state = AgentState(scheduler=Clock(), warn=self.a.state._warn)
        self.reg = 'idle'
        live = patch.object(shared, 'claude_sessions', side_effect=lambda *args: [dict(self.a.session, status=self.reg)])
        live.start(); self.addCleanup(live.stop)
        fresh = patch.object(shared.handoff, 'fresh', return_value=True)      # handoff written: no P151 prep turn
        fresh.start(); self.addCleanup(fresh.stop)

    def turn_with_background(self, stop=STOP_BG_SHELL):
        for ev in ({'hook_event_name': 'UserPromptSubmit'},
                   {'hook_event_name': 'PostToolUse', 'tool_name': 'Bash', 'tool_input': {'command': 'sleep 1500', 'run_in_background': True},
                    'tool_response': {'backgroundTaskId': 'b7x', 'stdout': ''}},
                   stop):
            self.a._note_background(ev['hook_event_name'], ev)
            self.a.state.handle(ev)

    async def run_compact(self, timeout=1.0):
        writes = []
        def write(fd, data):
            writes.append(data)
            if data == b'\r':
                self.a.control_ack.set()
        with patch.object(shared.os, 'write', side_effect=write):
            res = await asyncio.wait_for(self.a.command('compact', ''), timeout)
        return res, writes


class Gate(Base):
    async def test_foreground_busy_waits_and_never_types(self):
        # ① a turn is running (hooks working, registry busy): wait, nothing typed, plain waiting card
        self.a.state.handle({'hook_event_name': 'UserPromptSubmit'})
        self.reg = 'busy'
        with patch.object(shared, 'DESKTOP_IDLE_WAIT', .3), patch.object(shared, 'BUSY_FALLBACK_S', 0), patch.object(shared, 'BG_SETTLE_S', 0):
            res, writes = await self.run_compact()
        self.assertEqual(writes, []); self.assertEqual(res.kind, 'error')
        self.host.cmd_waiting.assert_called_once_with(self.a.cur_cmd)
        self.assertFalse(self.a.cur_cmd.native_started)

    async def test_background_shell_only_registry_shell_sends(self):
        # ② the real 2.1.296 picture: turn ended, a background sleep → registry "shell", hooks idle → /compact is typed
        self.turn_with_background()
        self.reg = 'shell'
        self.assertEqual(self.a.state.status, 'idle')
        with patch.object(shared, 'BG_SETTLE_S', .05):
            res, writes = await self.run_compact()
        self.assertEqual(res.kind, 'ok'); self.assertEqual(writes, [b'/compact', b'\r'])
        logged = [c for c in self.host.st.log.call_args_list if c.args[0] == 'shared_control_background']
        self.assertEqual(logged[0].kwargs['tasks'], 1); self.assertEqual(logged[0].kwargs['why'], 'bg')
        detail = self.host.cmd_waiting.call_args.args[1]
        self.assertIn('1 个后台任务', detail); self.assertIn('后台任务不受影响', detail)

    async def test_background_subagent_registry_busy_sends(self):
        # ② a background sub-agent keeps the registry "busy" — the main Stop lists it, so it is background only
        stop = {'hook_event_name': 'Stop', 'last_assistant_message': 'ok', 'background_tasks': [{'id': 'A2', 'type': 'subagent'}]}
        self.turn_with_background(stop)
        self.reg = 'busy'
        with patch.object(shared, 'BG_SETTLE_S', .05):
            res, writes = await self.run_compact()
        self.assertEqual(res.kind, 'ok'); self.assertEqual(writes, [b'/compact', b'\r'])
        self.assertEqual(self.a.bg_live, {'A2': 'subagent'})

    async def test_background_waits_for_settle_window(self):
        self.turn_with_background()
        self.reg = 'shell'
        with patch.object(shared, 'BG_SETTLE_S', 60), patch.object(shared, 'DESKTOP_IDLE_WAIT', .3):
            res, writes = await self.run_compact()
        self.assertEqual(writes, [])
        self.assertIn('后台任务', self.host.cmd_waiting.call_args.args[1])

    async def test_native_turn_or_phone_turn_holds_even_with_background(self):
        self.turn_with_background()
        self.reg = 'shell'
        for flag in ('native_turn', 'phone_turn'):
            setattr(self.a, flag, True)
            go, why = self.a._control_gate('shell')
            self.assertEqual((go, why), (False, 'fg'))
            setattr(self.a, flag, False)

    async def test_dialog_waiting_holds_and_says_so(self):
        self.turn_with_background()
        self.a.state.handle({'hook_event_name': 'PermissionRequest', 'tool_name': 'Bash'})
        self.assertEqual(self.a._control_gate('busy'), (False, 'fg'))
        self.assertIn('确认框', self.a._wait_detail('compact', 'fg'))

    async def test_registry_busy_nothing_known_uses_quiet_fallback(self):
        # nothing in Stop.background_tasks (e.g. Agent J attached later): the long silence fallback
        self.a.state.handle({'hook_event_name': 'Stop', 'last_assistant_message': 'x'})
        self.reg = 'busy'
        self.assertEqual(self.a._control_gate('busy'), (False, 'quiet'))
        self.assertIn('秒内没有新回合', self.a._wait_detail('compact', 'quiet'))
        with patch.object(shared, 'BUSY_FALLBACK_S', .05):
            res, writes = await self.run_compact()
        self.assertEqual(writes, [b'/compact', b'\r'])

    async def test_new_main_hook_restarts_quiet_window(self):
        self.turn_with_background()
        with patch.object(shared, 'BG_SETTLE_S', 1000):
            self.a.main_seen_at -= 2000
            self.assertTrue(self.a._control_gate('shell')[0])
            self.a._note_background('PreToolUse', {'hook_event_name': 'PreToolUse'})
            self.assertFalse(self.a._control_gate('shell')[0])
            # a background sub-agent's own hooks do not count as main-thread activity
            self.a.main_seen_at -= 2000
            self.a._note_background('PreToolUse', {'hook_event_name': 'PreToolUse', 'agent_id': 'A2'})
            self.assertTrue(self.a._control_gate('shell')[0])

    async def test_timeout_message_is_understandable(self):
        # ③ timeout: a sentence that says what happened and what to do, not a bare error
        self.a.state.handle({'hook_event_name': 'UserPromptSubmit'})
        self.reg = 'busy'
        with patch.object(shared, 'DESKTOP_IDLE_WAIT', .2):
            res, writes = await self.run_compact()
        self.assertEqual(res.kind, 'error'); self.assertEqual(writes, [])
        self.assertIn('等待超时', res.text); self.assertIn('没有发送', res.text); self.assertIn('/compact', res.text)
        self.assertTrue(any(c.args[0] == 'shared_control_timeout' for c in self.host.st.log.call_args_list))
        self.host.lang = 'en'
        self.assertIn('timed out', self.a._wait_timeout('compact', 'fg'))

    async def test_waiting_dialog_timeout_names_the_dialog(self):
        self.a.state.handle({'hook_event_name': 'PermissionRequest', 'tool_name': 'Bash'})
        self.assertIn('确认框', self.a._wait_timeout('clear', 'fg'))


class Bookkeeping(unittest.TestCase):
    def setUp(self):
        self.a = shared.SharedClaudeAgent(Mock(lang='zh'), {'kind': 'claude', 'dir': '/fixture', '_workflow_ceo': True})

    def ev(self, name, **kw):
        e = {'hook_event_name': name, **kw}
        self.a._note_background(name, e)

    def test_add_remove_replace(self):
        self.ev('PostToolUse', tool_name='Bash', tool_input={'run_in_background': True}, tool_response={'backgroundTaskId': 's1'})
        self.ev('PostToolUse', tool_name='Agent', tool_response={'agentId': 'A1', 'status': 'async_launched'})
        self.assertEqual(self.a.bg_live, {'s1': 'shell', 'A1': 'subagent'})
        self.ev('SubagentStop', agent_id='A1')
        self.ev('PostToolUse', tool_name='TaskStop', tool_input={'task_id': 's1'}, tool_response={})
        self.assertEqual(self.a.bg_live, {})
        self.ev('Stop', background_tasks=[{'id': 'x', 'type': 'shell'}, {'id': 'y', 'type': 'subagent'}, 'junk'])
        self.assertEqual(self.a.bg_live, {'x': 'shell', 'y': 'subagent'})
        self.ev('Stop', agent_id='y', background_tasks=[])            # a sub-agent's own Stop never replaces the list
        self.assertEqual(len(self.a.bg_live), 2)
        self.ev('SessionStart', source='compact')                    # background work survives a compaction
        self.assertEqual(len(self.a.bg_live), 2)
        self.ev('SessionStart', source='clear')
        self.assertEqual(self.a.bg_live, {})

    def test_foreground_agent_result_is_not_background(self):
        self.ev('PostToolUse', tool_name='Agent', tool_response={'agentId': 'A1', 'status': 'completed'})
        self.assertEqual(self.a.bg_live, {})

    def test_registry_quiet_for_recovery(self):
        self.a.session = {'sessionId': 's', 'pid': 1}
        for reg, bg, want in (('idle', {}, True), ('shell', {}, True), ('busy', {'A': 'subagent'}, True),
                              ('busy', {}, False), ('waiting', {'A': 'subagent'}, False)):
            self.a.bg_live = dict(bg)
            with patch.object(shared, 'claude_sessions', return_value=[{'pid': 1, 'status': reg}]):
                self.assertEqual(self.a._registry_quiet(), want, (reg, bg))
        self.a.state.handle({'hook_event_name': 'UserPromptSubmit'})
        with patch.object(shared, 'claude_sessions', return_value=[{'pid': 1, 'status': 'shell'}]):
            self.assertFalse(self.a._registry_quiet())


if __name__ == '__main__':
    unittest.main()
