"""#449: the heartbeat beat that carried the host's context notice may compact the main conversation itself — no owner
turn needed — through the same fixed P506 queue (own process tree, fresh handoff, not stopped). One shot; a beat
without the host's invite, a forged token or another session is still refused."""
import _hermetic  # noqa: F401
import asyncio
import contextlib
import os
import time
from unittest.mock import AsyncMock

from agentj import heartbeat, self_compact as S
from agentj.heartbeat import Heartbeat, HeartbeatSend, session_key
from agentj.slash import Result
from test_p506_compact import Compact


class HeartbeatInvite(Compact):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.host.heartbeat = Heartbeat(self.host)
        (self.work/'.agentj/handover/compact.md').write_text('# Handoff\nSynthetic task and next step.\n')

    def beat(self, invited=True, token=None):
        send = HeartbeatSend(7, 'beat', session_key(self.agent), 'sha8sha8')
        if invited:
            send.compact_invite = token or 'tok-' + os.urandom(6).hex()
            self.host.heartbeat.compact_invite = {'token': send.compact_invite, 'key': session_key(self.agent), 'at': time.time()}
        self.agent.cur_send = send
        os.utime(self.work/'.agentj/handover/compact.md')
        return send

    async def test_invited_beat_queues_and_runs_once_without_an_owner_turn(self):
        for kind in ('claude', 'codex', 'opencode'):
            self.agent.kind = kind
            self.st.set_agent_session(kind, 'synthetic-session')
            self.beat()
            res = await self.queue()
            self.assertEqual(res['result'], 'queued', (kind, res))
            cmd = self.agent.q.get_nowait()
            self.assertEqual((cmd.name, cmd.arg), ('compact', ''))
            self.assertIn('self_compact_invite', cmd.data)
            self.agent.cur_send = None                       # the queue runs after the beat's Send is cleared
            self.assertIsNone(S.guard(self.agent, cmd))
            self.assertEqual(S.guard(self.agent, cmd).kind, 'refused', 'the invite is one shot')

    async def test_real_queue_executes_the_invited_compaction(self):
        self.beat()
        self.assertEqual((await self.queue())['result'], 'queued')
        self.agent.cur_send = None
        done = asyncio.Event()
        results = []
        self.agent.command = AsyncMock(return_value=Result('compacted', 'ok'))
        self.host.cmd_done = lambda cmd, res: (results.append(res), done.set())
        task = asyncio.create_task(self.agent.run())
        try:
            await asyncio.wait_for(done.wait(), 2)
            self.agent.command.assert_awaited_once_with('compact', '')
            self.assertEqual(results[0].kind, 'ok')
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def test_beat_without_invite_or_forged_token_is_refused(self):
        self.beat(invited=False)
        self.assertEqual((await self.queue())['why'], 'source')
        send = self.beat()
        send.compact_invite = 'forged'
        self.assertEqual((await self.queue())['why'], 'source')
        self.beat()
        self.host.heartbeat.compact_invite['key'] = 'another-session'
        self.assertEqual((await self.queue())['why'], 'source')
        self.beat()
        self.host.heartbeat.compact_invite['at'] = time.time() - heartbeat.INVITE_TTL - 5
        self.assertEqual((await self.queue())['why'], 'source')
        self.assertTrue(self.agent.q.empty())

    async def test_a_non_heartbeat_send_cannot_borrow_the_token(self):
        from types import SimpleNamespace
        from agentj import provenance
        self.beat()
        tok = self.host.heartbeat.compact_invite['token']
        self.agent.cur_send = SimpleNamespace(source=provenance.email_guest('guest@example.test'), compact_invite=tok,
                                              created=time.monotonic())
        self.assertEqual((await self.queue())['why'], 'source')
        self.assertTrue(self.agent.q.empty())

    async def test_untrusted_request_scope_or_shared_terminal_input_refuses(self):
        from agentj import provenance
        self.beat()
        with provenance.request_source(provenance.email_guest('guest@example.test')):
            self.assertEqual((await self.queue())['why'], 'source')
        self.agent.untrusted = lambda: True
        self.assertEqual((await self.queue())['why'], 'source')
        self.assertTrue(self.agent.q.empty())

    async def test_invite_still_needs_a_fresh_handoff_and_no_stop(self):
        self.beat()
        (self.work/'.agentj/handover/compact.md').unlink()
        self.assertEqual((await self.queue())['why'], 'handoff')
        compact = self.work/'.agentj/handover/compact.md'
        compact.write_text('# Handoff\nnext\n')
        self.beat()
        self.host.stopped = lambda: True
        self.assertEqual((await self.queue())['why'], 'stopped')
        self.host.stopped = lambda: False
        self.beat()
        self.assertEqual((await self.queue())['result'], 'queued')
        cmd = self.agent.q.get_nowait()
        self.agent.cur_send = None
        self.host.stopped = lambda: True
        self.assertEqual(S.guard(self.agent, cmd).kind, 'refused')

    async def test_new_turn_before_the_queue_runs_cancels_it(self):
        self.beat()
        await self.queue()
        cmd = self.agent.q.get_nowait()
        self.agent.cur_send = None
        self.host.turn_t0 = 999.0                            # the owner (or anything) started a turn in between
        self.assertEqual(S.guard(self.agent, cmd).kind, 'refused')


# Only the invite cases run here; the P506 cases run in test_p506_compact.
for name in dir(Compact):
    if name.startswith('test_') and name not in HeartbeatInvite.__dict__:
        setattr(HeartbeatInvite, name, None)
del Compact
