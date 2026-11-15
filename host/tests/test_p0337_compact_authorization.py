"""R-0028: queue-time authorization survives shared turn cleanup, once only."""
import _hermetic  # noqa: F401
import asyncio
import contextlib
import copy
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from agentj import provenance, self_compact as S
from agentj.shared import SharedClaudeAgent
from agentj.slash import Result
from test_p506_compact import Compact


class BoundCompact(Compact):
    async def test_shared_owner_authorization_survives_native_unknown_after_turn_end(self):
        # Actual Shared source resolver: pending phone while phone_turn, native UNKNOWN after cleanup.
        agent = SharedClaudeAgent(self.host, self.agent.cfg)
        agent.session = {'sessionId': 'synthetic-session', 'pid': __import__('os').getpid()}
        self.agent = self.host.agent = agent
        agent.pending_source = provenance.phone()
        agent.native_source = provenance.normalize(None)
        agent.phone_turn = True
        agent.cur_send = SimpleNamespace(source=provenance.phone(), created=time.monotonic())
        self.assertEqual((await self.queue())['result'], 'queued')
        agent.cur_send = None
        agent.phone_turn = False
        done = asyncio.Event()
        results = []
        agent.command = AsyncMock(return_value=Result('compacted', 'ok'))
        self.host.cmd_done = lambda cmd, res: (results.append(res), done.set())
        task = asyncio.create_task(agent.run())
        try:
            await asyncio.wait_for(done.wait(), 2)
            agent.command.assert_awaited_once_with('compact', '')
            self.assertEqual(results[0].kind, 'ok')
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def test_oversized_handoff_cannot_authorize_a_partial_hash(self):
        p = self.work/'.agentj/handover/compact.md'
        p.write_bytes(b'x' * (S.compactprep.READ_MAX + 1))
        self.assertEqual((await self.queue())['why'], 'handoff')
        self.assertTrue(self.agent.q.empty())

    async def test_same_send_downgrade_cannot_reuse_owner_authority(self):
        await self.queue()
        self.agent.cur_send.source = provenance.email_guest('guest@example.test')
        self.assertEqual(S.guard(self.agent, self.agent.q.get_nowait()).kind, 'refused')

    async def test_new_untrusted_flag_cancels_authorization(self):
        await self.queue()
        self.agent.untrusted = lambda: True
        self.assertEqual(S.guard(self.agent, self.agent.q.get_nowait()).kind, 'refused')

    async def test_cancelled_request_can_be_replaced_but_not_replayed(self):
        await self.queue()
        old = self.agent.q.get_nowait()
        old.cancelled = True
        await self.queue()
        new = self.agent.q.get_nowait()
        self.assertEqual(S.guard(self.agent, old).kind, 'refused')
        self.assertIsNone(S.guard(self.agent, new))

    async def test_expiry_releases_host_held_request(self):
        with patch.object(S, 'AUTH_TTL', 0.001):
            await self.queue()
            await asyncio.sleep(0.01)
        self.assertIsNone(self.agent._self_compact_authorization)
        self.assertEqual(S.guard(self.agent, self.agent.q.get_nowait()).kind, 'refused')

    async def test_authorization_is_consumed_once(self):
        await self.queue()
        cmd = self.agent.q.get_nowait()
        self.agent.cur_send = None
        self.assertIsNone(S.guard(self.agent, cmd))
        self.assertEqual(S.guard(self.agent, cmd).kind, 'refused')

    async def test_authorization_expires_even_with_fresh_handoff(self):
        await self.queue()
        cmd = self.agent.q.get_nowait()
        self.agent.cur_send = None
        with patch.object(S.time, 'monotonic', return_value=time.monotonic()+301):
            self.assertEqual(S.guard(self.agent, cmd).kind, 'refused')

    async def test_changed_handoff_content_cancels_even_if_mtime_is_preserved(self):
        p = self.work/'.agentj/handover/compact.md'
        stamp = p.stat().st_mtime
        await self.queue()
        cmd = self.agent.q.get_nowait()
        p.write_text('# Different handoff\nNever compact with a substituted handoff.\n')
        __import__('os').utime(p, (stamp, stamp))
        self.assertEqual(S.guard(self.agent, cmd).kind, 'refused')

    async def test_copied_command_cannot_borrow_an_authorization(self):
        await self.queue()
        cmd = self.agent.q.get_nowait()
        self.assertEqual(S.guard(self.agent, copy.copy(cmd)).kind, 'refused')

    async def test_shared_native_turn_generation_change_cancels(self):
        self.agent.turn_gen = 5
        await self.queue()
        cmd = self.agent.q.get_nowait()
        self.agent.turn_gen += 1
        self.assertEqual(S.guard(self.agent, cmd).kind, 'refused')

    async def test_no_authorization_on_forged_command_data(self):
        from agentj.agent import Cmd
        cmd = Cmd('compact', data={'self_compact': S.marker(self.agent), 'self_compact_source': provenance.phone()})
        self.assertEqual(S.guard(self.agent, cmd).kind, 'refused')

    async def test_refusal_describes_request_boundary_not_doubt_about_owner(self):
        for lang in ('en', 'zh'):
            self.host.lang = lang
            text = S.detail(self.host, 'source')
            self.assertNotIn('来自主人本人', text)
            self.assertNotIn('verified as coming from the owner', text)


for name in dir(Compact):
    if name.startswith('test_') and name not in BoundCompact.__dict__:
        setattr(BoundCompact, name, None)
del Compact
