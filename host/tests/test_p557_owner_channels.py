"""P557 (scope widened, chairman 10-27): every owner channel's avatar has the main conversation's rights; only missing
context goes to the main thread. It answers where it was asked, with that channel's own attachments, and the answer shows
on the Web App's main conversation too. Face ID steps go to a Web App card whichever channel asked.

- a saved native avatar plan (「用 Codex」 while the main Agent is Claude) no longer drops to the tool-less executor
  (the M1 root cause: no `avatar_turn` ever, a Codex tool-less turn, its disabled-tool try held as an overreach);
- a file the reply names → Telegram sendDocument / WeChat file item / mail attachment, path dropped from the words;
  a credential file → a pickup card + a note, never an attachment;
- research answers keep their links; mirror page 「Telegram / 微信分身已回复」 with the files on it;
- Face ID: the avatar's step goes through host.ask with its origin and the stricter passkey requirement, the channel only
  hears where to confirm, the result comes back in the same turn.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

from agentj import channel_card, media, owner_avatar, wechat
from agentj.channel_avatar import Reply, attachments, public_reply
from agentj.peer_session import TurnResult

PDF = b'%PDF-1.4\n' + b'0' * 2000 + b'\n%%EOF\n'
PNG = b'\x89PNG\r\n\x1a\nfixture'
KEY = 'sk-ant-api03-' + 'Ab3dE5gH7jK9mN1pQ3sT5vX7zA9cE1gI3kM5oQ7sU9wY'


class Plans(unittest.TestCase):
    def sessions(self, avatar=None, main=None):
        host = NS(st=NS(log=lambda *a, **k: None, agent_config=lambda: {}), ask=AsyncMock(), side_perm_env=lambda *a: None,
                  agent_cfg=main or {'kind': 'claude', 'model': 'claude-opus-5-5', 'dir': '/work', 'fence': True})
        store = NS(data={'avatar': avatar or {}})
        return wechat.Sessions(host, store, max_active=1)

    def test_saved_native_plan_keeps_main_rights_in_the_main_folder(self):
        s = self.sessions({'asked': True, 'config': {'kind': 'codex'}})
        self.assertTrue(owner_avatar.eligible(s))
        cfg = owner_avatar.config(s)
        self.assertEqual((cfg['kind'], cfg['dir'], cfg['fence']), ('codex', '/work', True))
        self.assertNotIn('model', cfg)                              # never the main Claude model on Codex
        same = self.sessions({'asked': True, 'config': {'kind': 'claude', 'model': 'claude-opus-5-5', 'dir': '/old'}})
        self.assertTrue(owner_avatar.eligible(same))
        self.assertEqual(owner_avatar.config(same)['dir'], '/work')  # the main folder now, not a stale snapshot

    def test_api_key_profile_openrouter_and_free_route_stay_tool_less(self):
        self.assertFalse(owner_avatar.eligible(self.sessions({'config': {'kind': 'codex', 'model': 'm', 'channel_profile': 'p1'}})))
        self.assertFalse(owner_avatar.eligible(self.sessions({'config': {'kind': 'opencode', 'channel_free': True}})))
        self.assertFalse(owner_avatar.eligible(self.sessions({'config': {'kind': 'opencode', 'model': 'openrouter/x/y'}},
                                                             {'kind': 'opencode', 'dir': '/work'})))

    def test_a_saved_snapshot_never_overrides_the_live_guards(self):
        stale = {'kind': 'claude', 'model': 'claude-opus-5-5', 'dir': '/old', 'fence': False, 'docker': True,
                 'danger_extra': [], 'high_risk_warnings': False, 'session_mode': 'shared', 'approvals': 'full'}
        live = {'kind': 'claude', 'model': 'claude-opus-5-5', 'dir': '/work', 'fence': True, 'docker': False,
                'danger_extra': ['rm -rf'], 'high_risk_warnings': True, 'session_mode': 'independent', 'approvals': 'ask'}
        cfg = owner_avatar.config(self.sessions({'asked': True, 'config': stale}, live))
        for k in ('dir', 'fence', 'docker', 'danger_extra', 'high_risk_warnings', 'session_mode', 'approvals'):
            self.assertEqual(cfg[k], live[k], k)

    def test_other_owners_friends_stay_tool_less(self):
        from agentj.peer_session import PeerSessions
        self.assertFalse(hasattr(PeerSessions, 'source'))         # only wechat.Sessions (owner channels) reach the avatar
        self.assertNotIn('owner_avatar', Path(__import__('agentj.peer_service', fromlist=['x']).__file__).read_text())

    def test_saved_codex_plan_runs_the_owner_avatar_not_the_tool_less_codex(self):
        s = self.sessions({'asked': True, 'config': {'kind': 'codex'}})
        s.source = 'telegram'
        done = TurnResult('reply', 'ok', '', None, None)
        with patch.object(owner_avatar.OwnerAvatar, 'turn', AsyncMock(return_value=done)) as turn, \
                patch('agentj.channel_native.NativeCodex.turn', AsyncMock()) as native:
            res = asyncio.run(s.turn({'id': 'owner'}, {'auto': {'mode': 'on'}}, '画一只猫'))
        self.assertIs(res, done)
        self.assertEqual(turn.call_args.kwargs['source'], 'telegram')
        native.assert_not_called()


class FaceId(unittest.TestCase):
    def test_avatar_step_needs_the_web_app_card_with_passkey_and_the_result_returns(self):
        seen = {}

        async def ask(tool, tool_input, gone=None, **kw):
            seen.update(tool=tool, **kw)
            await asyncio.sleep(0.05)
            return {'behavior': 'allow'}
        told = []
        with patch.object(owner_avatar, 'CARD_NOTICE_AFTER', 0.01):
            out = asyncio.run(owner_avatar.ask_for(NS(ask=ask), '来自 Telegram 分身', lambda: told.append(1), 'Bash',
                                                   {'command': 'security delete-generic-password x'}, require_passkey=True,
                                                   batch=True))
        self.assertEqual(out, {'behavior': 'allow'})
        self.assertEqual((seen['avatar'], seen['require_passkey']), ('来自 Telegram 分身', True))
        self.assertNotIn('batch', seen)                          # an avatar never takes a batch grant
        self.assertEqual(told, [1])                               # the channel hears 「去网页确认」 once


class _Owner(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from _testtmp import TEST_TMP
        from agentj.state import State
        from agentj.external_channels import ExternalChannels
        from agentj.peer_guard import SecretIndex
        self.tmp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.st = State(Path(self.tmp.name) / 'state'); self.st.init()
        self.work = Path(self.tmp.name) / 'work'; (self.work / 'out').mkdir(parents=True)
        (self.work / 'out/report.pdf').write_bytes(PDF)
        (self.work / 'out/cat.png').write_bytes(PNG)
        self.pages = []

        def hist_add(src, reply='', end='done', card=None):
            self.pages.append((src, reply))
            return {'id': len(self.pages)}
        self.host = NS(st=self.st, agent_cfg={'kind': 'claude', 'dir': str(self.work)}, preferences={}, lang='zh',
                       stopped=lambda: False, agent_notice=MagicMock(), _bg=MagicMock(), hist=NS(turns={}),
                       hist_add=hist_add, channel_media=MagicMock(), agent=NS(submit=MagicMock(), cur_send=None),
                       media=media.Media(self.st, str(self.work)))
        self.channel = ExternalChannels(self.host, model=AsyncMock())
        self.channel.index = SecretIndex()
        self.channel.open_card = MagicMock()

    async def asyncTearDown(self):
        self.channel.closed = True
        await self.channel.sessions.stop_all()
        self.channel.store.close()

    def answer(self, text, decision='reply', images=()):
        res = TurnResult(decision, text, '', None, None)
        res.images = list(images)
        return res


class TelegramOwner(_Owner):
    async def test_named_file_goes_as_attachment_and_the_web_app_sees_the_answer(self):
        self.channel.model = AsyncMock(return_value=self.answer(f'报告整理好了。文件在 {self.work}/out/report.pdf'))
        reply = await self.channel.receive_owner('telegram', 'g:1', '把季度报告发给我')
        self.assertEqual(str(reply), '报告整理好了。')
        self.assertEqual([(n, m) for n, m, _d in reply.files], [('report.pdf', 'application/pdf')])
        self.channel.open_card.assert_not_called(); self.host.agent.submit.assert_not_called()
        src, text = self.pages[-1]
        self.assertEqual((src['via'], src['text'], text), ('telegram', 'Telegram 分身已回复', '报告整理好了。'))
        page, paths = self.host.channel_media.call_args.args
        self.assertEqual(paths, [str(self.work / 'out/report.pdf')])

    async def test_research_answer_keeps_its_links(self):
        text = '今天美元走强，主要因为非农超预期。来源：https://news.example.com/a/b'
        self.channel.model = AsyncMock(return_value=self.answer(text))
        self.assertEqual(str(await self.channel.receive_owner('telegram', 'g:2', '美元今天为什么涨')), text)
        self.assertEqual(self.pages[-1][1], text)

    async def test_generated_image_shows_on_the_web_app_page(self):
        self.channel.model = AsyncMock(return_value=self.answer('画好了。', images=[('image/png', PNG)]))
        reply = await self.channel.receive_owner('telegram', 'g:3', '画一只猫')
        self.assertEqual(reply.images, (('image/png', PNG),))
        [placed] = self.host.channel_media.call_args.args[1]
        self.assertTrue(placed.endswith('.png') and Path(placed).read_bytes() == PNG)

    async def test_credential_file_becomes_a_pickup_card_never_an_attachment(self):
        (self.work / '.env').write_text('ANTHROPIC_API_KEY=' + KEY + '\n')
        self.channel.model = AsyncMock(return_value=self.answer('配置在这里 [env](.env)。'))
        with patch('agentj.owner_route.offer_files', AsyncMock(return_value=1)) as offer:
            reply = await self.channel.receive_owner('telegram', 'g:4', '把 env 发我')
        offer.assert_awaited_once()
        self.assertEqual(reply.files, ())
        self.assertIn('.env', str(reply)); self.assertNotIn(KEY, str(reply))

    async def test_overreach_still_goes_to_the_web_app(self):
        self.channel.model = AsyncMock(return_value=self.answer('要替主人付款，先问主人', decision='ask_owner'))
        self.assertIn('已转给', await self.channel.receive_owner('telegram', 'g:5', '帮我付这张账单'))
        self.channel.open_card.assert_called_once()
        self.assertFalse(any(s.get('text') == 'Telegram 分身已回复' for s, _ in self.pages))


class MailOwner(_Owner):
    async def test_mail_reply_carries_the_named_file_and_its_page_shows_it(self):
        self.channel.model = AsyncMock(return_value=self.answer(f'附上报告 {self.work}/out/report.pdf'))
        reply = await self.channel.receive_owner('mail', 'g:m1', '把报告发我')
        self.assertEqual([n for n, _m, _d in reply.files], ['report.pdf'])
        self.assertEqual(reply.page_files, [str(self.work / 'out/report.pdf')])
        self.assertEqual(self.pages, [])                         # mail writes its own page (it carries the route)
        from agentj import mail
        m = mail.Mail.__new__(mail.Mail)
        m.host, m.st, m.lang, m.password = self.host, self.st, lambda: 'zh', lambda cfg: 'pw'
        m.log = m.count = m.note = lambda *a, **k: None
        cfg = mail.blank_config()
        cfg.update(enabled=True, generation='g', allow=[{'addr': 'owner@example.com', 'role': 'owner'}], reply_per_hour=10)
        route = {'to': 'owner@example.com', 'role': 'owner', 'verified': True, 'gen': 'g', 'subject': 's', 'state': 'awaiting'}   # P562: a verified owner route
        built = {}

        def build(cfg_, route_, body, name, lang, files):
            built.update(body=body, files=files)
            raise RuntimeError('stop')
        with patch.object(mail, 'read_config', return_value=cfg), \
                patch.object(mail, 'read_state', return_value={'routes': {'1': route}, 'hour': {}, 'sent': []}), \
                patch.object(mail, 'save_state'), patch.object(mail, 'build_reply', build), \
                patch('agentj.owner_route.divert', AsyncMock(return_value=(str(reply), {}))), self.assertRaises(RuntimeError):
            await m.reply(1, reply)
        self.assertEqual([f[0] for f in built['files']], ['report.pdf'])


class WeChatOwner(_Owner):
    async def test_main_answer_sends_the_file_item(self):
        """P562 (#337): the owner's WeChat message is answered by the main conversation; a file its answer names goes to WeChat
        as a file message, the path dropped from the words."""
        from agentj import wechat_protocol as protocol
        from agentj.wechat import WeChat
        from test_p501_wechat import ACCOUNT, Transport
        c = WeChat(self.host, model=AsyncMock())
        c.index = self.channel.index; c.store.data['account'] = dict(ACCOUNT)
        c.open_card = MagicMock(); c.publish = lambda: None
        self.host._accept = AsyncMock(return_value=NS(turn=31))
        tr = Transport([{}, {}]); api = protocol.API(ACCOUNT, tr)
        c.api_factory = lambda *_a: api
        try:
            with patch.object(protocol.Media, 'upload', return_value={'type': 4, 'file_item': {'fixture': True}}) as upload:
                await c.receive(api, {'from_user_id': 'owner', 'message_type': 1, 'message_id': 9, 'context_token': 'fixture',
                                      'item_list': [{'type': 1, 'text_item': {'text': '做个 PDF 发我'}}]}, c.store.data['generation'])
                self.assertEqual(self.host._accept.call_args.args[0].source_kind, 'wechat')
                self.assertFalse(tr.calls)
                c.completed({'id': 31, 'src': {'k': 'wechat'}, 'reply': {'text': f'做好了，PDF 在 {self.work}/out/report.pdf'}, 'end': 'done'})
                await asyncio.gather(*list(c.card_tasks))
            self.assertEqual(upload.call_args.args[1], {'type': 4, 'file_item': {'file_name': 'report.pdf'}})
            self.assertEqual(upload.call_args.args[2], PDF)
            self.assertEqual(json.loads(tr.calls[0][0][3])['msg']['item_list'][0]['text_item']['text'], '做好了')
            c.open_card.assert_not_called(); c.model.assert_not_awaited()
        finally:
            c.closed = True; c.store.close()


class TelegramDelivery(unittest.IsolatedAsyncioTestCase):
    async def test_owner_chat_is_the_main_conversation(self):
        import test_p118_telegram as fixture
        base = fixture._Base(); base.setUp()
        try:
            base.host.external_channels = NS(receive_owner=AsyncMock(return_value=Reply('报告在这里。', (),
                                                                                     [('report.pdf', 'application/pdf', PDF)])))
            self.assertTrue(await base.t.incoming(fixture.msg('发报告'), base.cfg))
            base.host.external_channels.receive_owner.assert_not_awaited()
            base.host._accept.assert_awaited_once()     # its answer (and the files it names) go back via Telegram.completed
        finally:
            base.doCleanups()


class Pieces(unittest.TestCase):
    def test_file_only_reply_gets_a_caption(self):
        self.assertEqual(public_reply('/work/out/report.pdf', (), 'zh', [('report.pdf', 'application/pdf', PDF)]), '文件在这里。')
        self.assertEqual(public_reply('/work/out/report.pdf', (), 'en', [('report.pdf', 'application/pdf', PDF)]), 'Here is the file.')

    def test_attachments_need_a_media_root(self):
        self.assertEqual(attachments(NS(), '/work/out/report.pdf'), ([], [], [], []))

    def test_mirror_label_per_channel(self):
        host = NS(lang='en', hist_add=MagicMock(return_value={'id': 3}), channel_media=MagicMock())
        channel_card.reply(host, 'wechat', 'done', ['/w/a.pdf'])
        self.assertEqual(host.hist_add.call_args.args[0]['text'], 'WeChat avatar replied')
        host.channel_media.assert_called_once_with({'id': 3}, ['/w/a.pdf'])
        with self.assertRaises(ValueError):
            channel_card.reply(host, 'imessage', 'x')


if __name__ == '__main__':
    unittest.main()
