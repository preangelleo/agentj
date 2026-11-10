"""0.30.6: a secret card the owner opens from the phone Settings page (mail auth code, model API key) saves in its countdown.

Field (0.30.x, 11-10): Settings → mail channel → QQ mail auth code showed a card whose source was
an unrelated chat message from an hour earlier (Message ID 1910). Cause: elevate.request runs the card in its own worker
task, where the creator's paired-request scope no longer resolves, so approval_review.snapshot used the main chat thread.
When that thread's source was unverified or changed during the countdown, the save was refused (「主机没有收下」).
Synthetic state, device and key only.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from agentj import approval_review, elevate, provenance
from agentj.heartbeat import HeartbeatSend
from test_f17 import answer, enroll
from test_l1 import _ready, _state
from _p541_device import Phone, _owner_host


class SettingsSecretCard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.d = pathlib.Path(self.tmp.name)
        self.st, self.sent = _state(self.tmp.name), []
        self.host = _owner_host(self.st, self.sent)
        self.ph = enroll(self.st, Phone(self.st))
        self.s = _ready(self.host, self.ph)
        self.dest = self.d / 'keys' / '.env'; self.dest.parent.mkdir(); self.dest.write_text('OTHER=1\n')

    def of(self, t):
        return [o for _, o in self.sent if o and o.get('t') == t]

    async def wait_for(self, t, n=1):
        for _ in range(500):
            if len(self.of(t)) >= n:
                return self.of(t)[n - 1]
            await asyncio.sleep(0.01)
        self.fail('no ' + t)

    async def settings_card(self, marked=True):
        """As keys.py / provider_runtime.py / serve mail_key open it: a marked card inside a paired-request scope."""
        card = elevate.norm_secret({'name': 'ARK_API_KEY', 'purpose': '调用火山方舟', 'cwd': str(self.dest.parent),
                                    'dest': f'env:{self.dest}#ARK_API_KEY'}, self.st.root)
        card['only'] = self.s.device
        if marked:
            card['settings_origin'] = True
        async def tap():
            with provenance.paired_request(lambda: self.host._paired_live(self.s)):
                return await self.host.elevate.request(card)
        task = asyncio.ensure_future(tap())
        return task, await self.wait_for('elev')

    def unrelated_chat(self):
        with mock.patch.object(self.host, 'hist_emit'):
            page = self.host.hist_add({'k': 'phone', 'name': '你', 'text': 'Unrelated earlier chat: weekly report.'}, '', 'done')
        origin = approval_review.remember(self.host, page, provenance.phone())
        self.host.agent.cur_send = SimpleNamespace(approval_origin=origin, source=provenance.phone())
        return page

    def test_source_is_settings_not_an_unrelated_chat_message(self):
        page = self.unrelated_chat()
        async def run():
            task, shown = await self.settings_card()
            src = shown['envelope']['source']
            self.assertNotEqual(src.get('message_id'), page['id'])
            self.assertIsNone(src.get('message_id'))
            self.assertTrue(src.get('verified')); self.assertFalse(src.get('suspicious'))
            self.assertIn('设置', src.get('initiator', '') + src.get('text', ''))
            await self.host._app(self.s, answer(self.ph, shown, True, 'ark-' + 'a' * 28))
            return await asyncio.wait_for(task, 10)
        res = asyncio.run(run())
        self.assertEqual('saved', res['result'], res)

    def test_saves_while_the_chat_thread_moves_on(self):
        """Opened during an unattended heartbeat turn; the turn ends and the owner chats before saving."""
        value = 'ark-' + 'b' * 28                       # a fixture, not a credential
        self.host.agent.cur_send = HeartbeatSend(1, 'synthetic heartbeat', 'key', 'digest')
        self.host.desktop_turn = None                   # no owner chat turn behind it: the main thread's source is unknown
        async def run():
            task, shown = await self.settings_card()
            self.host.agent.cur_send = None
            with mock.patch.object(self.host, 'hist_emit'):
                self.host.desktop_input('A later owner message in the chat.')
            await self.host._app(self.s, answer(self.ph, shown, True, value))
            return await asyncio.wait_for(task, 10)
        res = asyncio.run(run())
        self.assertEqual('saved', res['result'], res)
        self.assertEqual([], self.of('elev_refused'))
        self.assertIn('ARK_API_KEY=' + value, self.dest.read_text())

    def test_settings_card_still_bound_to_agent_and_history(self):
        """The settings label never widens authority: switching the Agent still retires the card."""
        async def run():
            task, shown = await self.settings_card()
            self.host.agent = SimpleNamespace(kind='claude', cur_send=None, phone_turn=False, cfg={})
            await self.host._app(self.s, answer(self.ph, shown, True, 'ark-' + 'c' * 28))
            return await asyncio.wait_for(task, 10)
        res = asyncio.run(run())
        self.assertEqual('refused', res['result'])
        self.assertNotIn('ARK_API_KEY=', self.dest.read_text())

    def test_other_paired_frames_do_not_get_the_settings_label(self):
        """Security review r1 F2: auth_rescue / login_flow / setup / terminal / contacts frames are not Settings."""
        page = self.unrelated_chat()
        async def run():
            task, shown = await self.settings_card(marked=False)
            src = shown['envelope']['source']
            self.assertNotIn('设置', src.get('initiator', '') + src.get('text', ''))
            self.assertEqual(page['id'], src.get('message_id'))
            await self.host._app(self.s, answer(self.ph, shown, False))
            await asyncio.wait_for(task, 5)
        asyncio.run(run())

    def test_real_keys_settings_caller_marks_its_card(self):
        """The real Settings → keys handler (keys._set) opens the card with the Settings source."""
        from agentj import keys
        self.unrelated_chat()
        target = self.d / 'host-keys.env'
        async def run():
            with mock.patch.object(keys, 'env_path', return_value=str(target)):
                await keys._set(self.host, self.s, 'r1', {'name': 'ARK_API_KEY'})
                shown = await self.wait_for('elev')
                src = shown['envelope']['source']
                self.assertIsNone(src.get('message_id'))
                self.assertIn('设置', src.get('initiator', '') + src.get('text', ''))
                await self.host._app(self.s, answer(self.ph, shown, False))
                for _ in range(300):
                    if self.of('keys_res'):
                        break
                    await asyncio.sleep(0.01)
        asyncio.run(run())

    def test_provider_rescue_path_is_not_settings(self):
        """Security r2 F2b: request_key with a session (the OpenCode rescue path) keeps its own source; only the Settings
        provider page passes settings=True."""
        from agentj import bailian, provider_runtime
        from agentj.opencode_provider import NPM
        page = self.unrelated_chat()
        target = self.d / 'provider.env'
        async def one(settings):
            self.sent.clear()
            with mock.patch.object(provider_runtime, 'key_name', return_value='SYNTH_API_KEY'), \
                 mock.patch.object(provider_runtime, 'configured', return_value={'synth': {'npm': next(iter(NPM.values()))}}), \
                 mock.patch.object(provider_runtime, 'env_path', return_value=str(target)), \
                 mock.patch.object(bailian, 'verify_for', return_value=('https://example.com/v1/models', 'Authorization: Bearer {value}')):
                task = asyncio.ensure_future(provider_runtime.request_key(self.host, 'synth', session=self.s, settings=settings))
                shown = await self.wait_for('elev')
                src = shown['envelope']['source']
                await self.host._app(self.s, answer(self.ph, shown, False))
                await asyncio.wait_for(task, 5)
            return src
        async def run():
            return await one(False), await one(True)
        rescue, settings = asyncio.run(run())
        self.assertNotIn('设置', rescue.get('initiator', '') + rescue.get('text', ''))
        self.assertEqual(page['id'], rescue.get('message_id'))
        self.assertIn('设置', settings.get('initiator', '') + settings.get('text', ''))
        self.assertIsNone(settings.get('message_id'))

    def test_agent_turn_card_keeps_its_turn_source(self):
        """Without the paired Settings scope (an Agent's own `agentj secret request`) nothing changes."""
        page = self.unrelated_chat()
        async def run():
            card = elevate.norm_secret({'name': 'ARK_API_KEY', 'purpose': 'x', 'cwd': str(self.dest.parent),
                                        'dest': f'env:{self.dest}#ARK_API_KEY'}, self.st.root)
            with approval_review.origin_scope(approval_review.snapshot(self.host)):
                task = asyncio.ensure_future(self.host.elevate.request(card))
            shown = await self.wait_for('elev')
            self.assertEqual(page['id'], shown['envelope']['source'].get('message_id'))
            await self.host._app(self.s, answer(self.ph, shown, False))
            await asyncio.wait_for(task, 5)
        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
