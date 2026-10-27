"""P554: no host-side time window — an old context still forwards; only a platform refusal says `needs_message`
(never a silent failure), is remembered, and clears when the owner writes to ClawBot again."""
import json
import unittest
from unittest.mock import AsyncMock
import test_p501_wechat as fixture
from agentj.wechat_protocol import API, Failure


class Window(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.Channel.asyncSetUp
    asyncTearDown = fixture.Channel.asyncTearDown

    def ready(self, age=0, replies=None):
        self.h.hist = {1: {'end': 'done', 'reply': {'text': 'hello'}, 'ts': 1}}
        self.c.store.data.update(context='fixture-context', context_at=self.c.clock() - age)
        self.transport = fixture.Transport(replies or [{}] * 5)
        self.c.api_factory = lambda account: API(account, self.transport)

    async def test_two_day_old_context_still_forwards(self):
        self.ready(age=2 * 86400)
        self.assertTrue(self.c.can_forward()); self.assertTrue(self.c.status()['can_forward'])
        self.assertEqual(await self.c.forward(1, 'phone'), {'ok': True})
        self.assertEqual(json.loads(self.transport.calls[0][0][3])['msg']['context_token'], 'fixture-context')

    async def test_no_context_yet_needs_message(self):
        self.ready(); self.c.store.data['context'] = ''
        self.assertEqual((await self.c.forward(1, 'phone'))['why'], 'needs_message')
        self.assertFalse(self.transport.calls)

    async def test_unbound_error_or_stopped_stay_not_configured(self):
        self.ready(age=2 * 86400)
        self.c.error = 'poll_failed'
        self.assertEqual((await self.c.forward(1, 'phone'))['why'], 'not_configured'); self.c.error = None
        self.stopped = True
        self.assertEqual((await self.c.forward(1, 'phone'))['why'], 'not_configured'); self.stopped = False
        self.c.store.data['account'] = {}
        self.assertEqual((await self.c.forward(1, 'phone'))['why'], 'not_configured')
        self.assertFalse(self.transport.calls)

    async def test_platform_refusal_needs_message_and_is_remembered(self):
        self.ready(age=2 * 86400, replies=[{'ret': -2}, {}])
        self.c.publish = unittest.mock.Mock()
        self.assertEqual(await self.c.forward(1, 'phone'), {'ok': False, 'why': 'needs_message'})
        self.h.st.log.assert_any_call('wechat_forward', device='phone', id=1, result='needs_message')
        self.assertTrue(self.c.store.data['context_stale']); self.assertFalse(self.c.can_forward())
        self.c.publish.assert_called()   # the phone learns at once (wxfwd off, Settings explains)
        self.assertTrue(self.c.status()['bound'])
        # A later tap still asks the platform (it decides), and success clears the mark.
        self.assertEqual(await self.c.forward(1, 'phone'), {'ok': True})
        self.assertFalse(self.c.store.data['context_stale']); self.assertTrue(self.c.can_forward())

    async def test_owner_message_recovers_after_refusal(self):
        self.c.store.data.update(context='old', context_at=0, context_stale=True)
        self.assertFalse(self.c.can_forward())
        await self.c.receive(self.api, fixture.Channel.msg(self), self.gen)
        self.assertFalse(self.c.store.data['context_stale']); self.assertTrue(self.c.can_forward())

    async def test_session_expiry_and_network_errors_are_not_needs_message(self):
        for reply in ({'ret': -14}, Failure('timeout')):
            self.ready(replies=[reply]); self.c.fwd_sent.clear()
            self.assertEqual(await self.c.forward(1, 'phone'), {'ok': False, 'why': 'send_failed'})
            self.assertFalse(self.c.store.data.get('context_stale'))

    async def test_missing_context_mid_send_is_needs_message(self):
        self.ready()
        self.c.send = AsyncMock(side_effect=Failure('no_context'))
        self.assertEqual(await self.c.forward(1, 'phone'), {'ok': False, 'why': 'needs_message'})


if __name__ == '__main__':
    unittest.main()
