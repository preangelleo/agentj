"""P328: the phone's × (while the Agent works) is /stop for every harness. A shared Claude Code whose terminal offers no
safe interrupt route must SAY so — 「请在电脑上按 Esc」 — never stay silent; an idle pane is not an error; a sent Esc says
nothing extra; the approval-dialog guard is unchanged (Esc never answers a native permission prompt)."""
import _hermetic  # noqa: F401
from _testtmp import TEST_TMP  # noqa: E402
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agentj import shared


class SharedClaudeStop(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='p328-', dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.host = Mock()
        self.host.st.root = Path(self.tmp.name)
        self.host.lang = 'zh'
        self.a = shared.SharedClaudeAgent(self.host, {'kind': 'claude', 'dir': self.tmp.name})
        self.a.session = {'sessionId': 'owner', 'pid': 1}
        self.a.status = 'working'

    async def halt_with(self, result):
        with patch('agentj.shared_interrupt.interrupt', return_value=result) as fn:
            r = await self.a.halt(clear_queue=False)
        fn.assert_called_once()
        return r

    async def test_no_route_says_press_esc_zh(self):
        for why in ('unavailable', 'no_pane', 'blocked'):
            with self.subTest(why=why):
                self.host.agent_notice.reset_mock()
                self.assertFalse(await self.halt_with(why))
                self.host.agent_notice.assert_called_once()
                text = self.host.agent_notice.call_args.args[0]
                self.assertIn('不能从手机中断', text)
                self.assertIn('按 Esc', text)
                self.assertIn('Claude Code', text)

    async def test_no_route_english(self):
        self.host.lang = 'en'
        self.assertFalse(await self.halt_with('unavailable'))
        text = self.host.agent_notice.call_args.args[0]
        self.assertIn("can't be stopped from the phone", text)
        self.assertIn('press Esc on the computer', text)

    async def test_sent_is_quiet_and_true(self):
        self.assertTrue(await self.halt_with('sent'))
        self.host.agent_notice.assert_not_called()
        self.assertTrue(self.a.done.is_set())

    async def test_idle_pane_is_not_reported_as_unstoppable(self):
        self.assertFalse(await self.halt_with('idle'))
        self.host.agent_notice.assert_not_called()

    async def test_waiting_on_approval_still_refuses_esc(self):
        self.a.state.status = 'waiting'
        with patch('agentj.shared_interrupt.interrupt') as fn:
            self.assertFalse(await self.a.halt(clear_queue=False))
        fn.assert_not_called()
        self.assertIn('审批', self.host.agent_notice.call_args.args[0])


# ------------------------------------------------------------------ host side (P328 主机收尾): /stop's ONE answer per case
import asyncio  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import AsyncMock  # noqa: E402

from agentj import humane, serve, shared_opencode2  # noqa: E402
from agentj.agent import Agent  # noqa: E402
from agentj.shared_codex import SharedCodexAgent  # noqa: E402

NONE_ZH = '现在没有在跑的一轮'


def fake_host(agent, ran=False, lang='zh'):
    h = SimpleNamespace(agent=agent, lang=lang, preferences=None, status_changed=Mock(),
                        scheduler=SimpleNamespace(stop_running=AsyncMock(return_value=ran)))
    return h


async def stop(h, **kw):
    return await serve.Host.stop_turn(h, 'phone', **kw)


class StopTurnOneAnswer(unittest.IsolatedAsyncioTestCase):
    """serve.stop_turn: an unstoppable turn answers with its explanation ONLY; nothing running → only 「不用停」."""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='p328s-', dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.ah = Mock()
        self.ah.st.root = Path(self.tmp.name)
        self.ah.lang = 'zh'
        self.a = shared.SharedClaudeAgent(self.ah, {'kind': 'claude', 'dir': self.tmp.name})
        self.a.session = {'sessionId': 'owner', 'pid': 1}
        self.a.status = 'working'

    async def test_claude_unstoppable_one_message(self):
        with patch('agentj.shared_interrupt.interrupt', return_value='unavailable'):
            r = await stop(fake_host(self.a))
        self.assertIn('按 Esc', r.text)
        self.assertNotIn(NONE_ZH, r.text)
        self.assertNotIn('停下了', r.text)
        self.ah.agent_notice.assert_not_called()          # not a second message beside the answer
        self.assertFalse(self.a.halt_quiet)

    async def test_claude_sent_confirms(self):
        with patch('agentj.shared_interrupt.interrupt', return_value='sent'):
            r = await stop(fake_host(self.a))
        self.assertIn('停下了', r.text)
        self.assertNotIn('Esc', r.text)
        self.ah.agent_notice.assert_not_called()

    async def test_nothing_running_only_none(self):
        self.a.status = 'idle'
        with patch('agentj.shared_interrupt.interrupt', return_value='idle'):
            r = await stop(fake_host(self.a))
        self.assertIn(NONE_ZH, r.text)
        self.assertNotIn('Esc', r.text)
        self.ah.agent_notice.assert_not_called()

    async def test_no_agent_only_none(self):
        r = await stop(fake_host(None))
        self.assertIn(NONE_ZH, r.text)

    async def test_approval_open_one_message(self):
        self.a.state.status = 'waiting'
        r = await stop(fake_host(self.a))
        self.assertIn('审批', r.text)
        self.assertNotIn(NONE_ZH, r.text)
        self.ah.agent_notice.assert_not_called()

    async def test_task_stopped_and_turn_unstoppable(self):
        with patch('agentj.shared_interrupt.interrupt', return_value='unavailable'):
            r = await stop(fake_host(self.a, ran=True))
        self.assertIn(humane.say('stop.what_task', 'zh', None), r.text)
        self.assertIn('按 Esc', r.text)

    async def test_telegram_stop_keeps_the_notice(self):
        with patch('agentj.shared_interrupt.interrupt', return_value='unavailable'):
            await stop(fake_host(self.a), answered=False)     # Telegram shows no answer: the phone still hears why
        self.ah.agent_notice.assert_called_once()
        self.assertIn('按 Esc', self.ah.agent_notice.call_args.args[0])


SID = '44444444-4444-4444-4444-444444444444'


class CodexStop(unittest.IsolatedAsyncioTestCase):
    """Shared Codex: the phone's own turn → turn/interrupt + 「停下了」; the owner's desktop turn → 「请在电脑上按 Esc」."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='p328c-', dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.home = root / 'codex'
        self.proj = root / 'proj'
        self.proj.mkdir()
        env = patch.dict(os.environ, {'CODEX_HOME': str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        from agentj.state import State
        st = State(root / 'state')
        st.root.mkdir(parents=True, exist_ok=True)
        self.ah = Mock()
        self.ah.st = st
        self.ah.lang = 'zh'
        self.ah.preferences = None
        self.a = SharedCodexAgent(self.ah, {'kind': 'codex', 'dir': str(self.proj), '_workflow_ceo': True,
                                            'session_mode': 'shared', 'shared_session_id': SID})
        self.a.call = AsyncMock(return_value={})

    def rollout(self, active):
        d = self.home / 'sessions' / '2026' / '10' / '16'
        d.mkdir(parents=True, exist_ok=True)
        items = [{'type': 'session_meta', 'payload': {'id': SID, 'cwd': str(self.proj)}}]
        if active:
            items.append({'type': 'event_msg', 'payload': {'type': 'task_started'}})
        path = d / f'rollout-2026-10-16T00-00-00-{SID}.jsonl'
        path.write_text(''.join(json.dumps(i) + '\n' for i in items))
        self.a.rollout = path
        return path

    async def test_phone_turn_interrupts_and_confirms(self):
        self.rollout(active=True)            # the rollout shows the phone's own turn running
        a = self.a
        a.status, a.phone_turn = 'working', True
        a.turn_done, a.tid, a.turn_id, a.proc = asyncio.Event(), SID, 'turn-1', Mock()
        async def call(method, params, timeout=60):
            if method == 'turn/interrupt':
                a.turn_done.set()
            return {}
        a.call = AsyncMock(side_effect=call)
        r = await stop(fake_host(a))
        self.assertEqual(a.call.call_args.args[0], 'turn/interrupt')
        self.assertIn('停下了', r.text)
        self.assertNotIn('Esc', r.text)
        self.ah.agent_notice.assert_not_called()

    async def test_desktop_turn_cannot_be_stopped(self):
        self.rollout(active=True)
        self.a.status = 'idle'                # the owner's turn on the computer; no phone turn
        r = await stop(fake_host(self.a))
        self.assertIn('电脑上发给 Codex', r.text)
        self.assertIn('按 Esc', r.text)
        self.assertNotIn(NONE_ZH, r.text)
        self.a.call.assert_not_called()       # nothing sent to an app-server that does not run that turn
        self.ah.agent_notice.assert_not_called()

    async def test_desktop_turn_english(self):
        self.rollout(active=True)
        self.ah.lang = 'en'
        r = await stop(fake_host(self.a, lang='en'))
        self.assertIn("phone can't stop it", r.text)
        self.assertIn('Esc (or Ctrl+C)', r.text)

    async def test_phone_message_waiting_behind_desktop_turn(self):
        self.rollout(active=True)
        a = self.a
        a.status, a.phone_turn, a.turn_done = 'working', False, asyncio.Event()
        waiter = asyncio.create_task(a.wait_desktop(a.rollout))
        await asyncio.sleep(0.05)
        self.assertTrue(a.desktop_wait)
        r = await stop(fake_host(a))
        self.assertIn('按 Esc', r.text)
        self.assertIn('撤回', r.text)                     # the waiting phone message is withdrawn, and said so
        self.assertNotIn('停下了', r.text)
        from agentj.shared_codex import Stopped
        with self.assertRaises(Stopped):
            await asyncio.wait_for(waiter, 3)

    async def test_idle_codex_only_none(self):
        self.rollout(active=False)
        r = await stop(fake_host(self.a))
        self.assertIn(NONE_ZH, r.text)
        self.ah.agent_notice.assert_not_called()

    async def test_task_making_room_stays_quiet(self):
        self.rollout(active=True)              # tasks.py halts before a scheduled run: no one asked, nothing said
        await self.a.halt(clear_queue=False)
        self.ah.agent_notice.assert_not_called()

    async def test_stop_everything_says_it(self):
        self.rollout(active=True)              # estop (clear_queue=True): the desktop turn goes on — say so
        self.assertFalse(await self.a.halt(clear_queue=True))
        self.assertIn('按 Esc', self.ah.agent_notice.call_args.args[0])


class OpenCodeStop(unittest.IsolatedAsyncioTestCase):
    """Attached OpenCode (v1 + owner v2): the phone's turn → abort (unchanged); an owner's desktop turn → say so."""

    def make(self, cls):
        ah = Mock()
        ah.lang = 'zh'
        a = cls.__new__(cls)
        Agent.__init__(a, ah, {'kind': 'opencode', 'dir': '/var/tmp'})
        a.proc, a.client, a.sid = Mock(), Mock(), 'ses_1'
        a.client.request = AsyncMock(return_value=(200, {}))
        return a, ah

    async def check(self, cls):
        a, ah = self.make(cls)
        a.desktop_turn, a.turn_done, a.status = True, None, 'idle'
        r = await stop(fake_host(a))
        self.assertIn('电脑上的 OpenCode', r.text)
        self.assertIn('按 Esc', r.text)
        self.assertNotIn(NONE_ZH, r.text)
        a.client.request.assert_not_called()
        # the phone's own turn: abort sent, confirmed, no explanation
        a, ah = self.make(cls)
        a.desktop_turn, a.turn_done, a.status = False, asyncio.Event(), 'working'
        async def req(*args, **kw):
            a.turn_done.set()
            return 200, {}
        a.client.request = AsyncMock(side_effect=req)
        r = await stop(fake_host(a))
        self.assertIn('停下了', r.text)
        self.assertIn('interrupt' if cls is shared_opencode2.OwnerOpenCodeV2Agent else 'abort', a.client.request.call_args.args[1])
        ah.agent_notice.assert_not_called()

    async def test_v1_attached(self):
        await self.check(shared.SharedOpenCodeAgent)

    async def test_v2_owner(self):
        await self.check(shared_opencode2.OwnerOpenCodeV2Agent)


class AcpStopsAlways(unittest.TestCase):
    """Kimi / Qwen run as Agent J's own ACP child: /stop = `session/cancel` + the process tree ended — always stoppable,
    so they keep Agent.halt (no explanation needed)."""

    def test_kimi_qwen_keep_base_halt(self):
        from agentj.agent_kimi import KimiAgent
        from agentj.agent_qwen import QwenAgent
        for cls in (KimiAgent, QwenAgent):
            self.assertIs(cls.halt, Agent.halt, cls.__name__)


if __name__ == '__main__':
    unittest.main()
