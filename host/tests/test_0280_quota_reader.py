"""Phone quota intent must leave the same connection free to answer its card."""
import _hermetic
import asyncio
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
from agentj import compose, quota_refresh, wire
from agentj.serve import Host


class PhoneReader(unittest.IsolatedAsyncioTestCase):
    async def test_pending_login_card_does_not_hold_say_reader(self):
        waiting = asyncio.Event()
        started = asyncio.Event()
        tasks = []
        async def card(*args, **kwargs):
            started.set()
            await waiting.wait()
            return {'ok': True}
        def background(coro, label):
            task = asyncio.create_task(coro)
            tasks.append(task)
            return task
        host = NS(sends=compose.Sends(), receipts=None, stopped=lambda:False,
                  seat_refusal=lambda:None, agent=NS(kind='codex'), _bg=background,
                  send_app=AsyncMock(), _paired_live=lambda remote:True)
        remote = NS(device='fixture-phone', cid=1, name='fixture')
        sid = wire.b64u(b'1'*16)
        try:
            with patch.object(quota_refresh, 'upgrade_intent', return_value=True), \
                 patch.object(quota_refresh, 'owner_bound', return_value=lambda:True), \
                 patch.object(quota_refresh, 'upgraded', side_effect=card):
                await asyncio.wait_for(Host.on_say(host, remote, {'sid':sid,'text':'升级了'}), .1)
                await asyncio.wait_for(started.wait(), .1)
                self.assertTrue(tasks)
                self.assertFalse(tasks[0].done())
                response = host.send_app.await_args.args[1]
                self.assertEqual(response['t'], 'say_res')
                self.assertTrue(response['ok'])
                self.assertTrue(host.sends.used(remote.device, sid))
        finally:
            waiting.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_deferred_card_cannot_adopt_a_later_owner_or_task(self):
        pending = []
        current = [True]
        host = NS(sends=compose.Sends(), receipts=None, stopped=lambda:False,
                  seat_refusal=lambda:None, agent=NS(kind='codex'),
                  _bg=lambda coro,label:pending.append(coro), send_app=AsyncMock())
        remote = NS(device='fixture-phone', cid=1, name='fixture')
        with patch.object(quota_refresh,'upgrade_intent',return_value=True), \
             patch.object(quota_refresh,'owner_bound',return_value=lambda:current[0]), \
             patch.object(quota_refresh,'recheck',new=AsyncMock())as login:
            await Host.on_say(host,remote,{'sid':wire.b64u(b'2'*16),'text':'升级了'})
            current[0] = False
            result = await pending[0]
            self.assertFalse(result['ok'])
            self.assertEqual(result['why'],'changed')
            login.assert_not_awaited()
