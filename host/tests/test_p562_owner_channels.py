"""P562 (#337 / #336, chairman 10-28): the owner's own WeChat, Telegram private chat and main mailbox are answered by the main
conversation itself — no avatar.

- who is the owner: WeChat = the bound ClawBot sender (private, not a group / bot); Telegram = the enrolled id in its own
  private chat; mail = an allowlisted owner address that passed SPF / DKIM / DMARC (or the mailbox wrote to itself). A
  forged sender, a stranger, a group member, a guest address or an owner address with the check off keep the old path.
- such a message → one ordinary main-conversation message (serve._accept: the main queue, a page labelled with the channel),
  the answer back in the channel.
- approvals: a step of such a turn is the channel's request (serve.ask avatar=…): a Web App card, Face ID for ③ as graded,
  never a batch grant; the channel hears 「已转到 Agent J 网页，请在那里确认。」 once; its words never answer a card.
- #336: 「给我做张图片…写实风格」 while the main harness cannot draw → the owner's Codex draws it, the picture or the drawer's
  reason (+ a way on) comes back in WeChat; the exchange is on the Web App page.
- a wechat.bin written by 0.25.15a1 (held requests, avatar plan, old states) still loads; a held request still opens its card.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import owner_channels, provenance  # noqa: E402
from agentj.peer_session import TurnResult  # noqa: E402

from test_p336_approval_level import Base  # noqa: E402
from _testtmp import TEST_TMP  # noqa: E402

PNG = b'\x89PNG\r\n\x1a\n' + b'\0' * 32
ASK_336 = '给我做张图片有李雷有日本东京塔，写实风格'


class Identity(unittest.TestCase):
    def test_owner_channels_and_everyone_else(self):
        self.assertEqual(provenance.owner_channel(provenance.wechat_owner()), 'wechat')
        self.assertEqual(provenance.owner_channel(provenance.telegram_owner(5)), 'telegram')
        self.assertEqual(provenance.owner_channel(provenance.email_owner('boss@example.com')), 'mail')
        for other in (provenance.email_guest('boss@example.com'), provenance.telegram_forward(5),
                      provenance.telegram_group(-100, 5, 'family', 5), provenance.wechat_task('x'), provenance.phone(), None,
                      {'kind': 'wechat', 'role': 'group'}):
            self.assertIsNone(provenance.owner_channel(other), other)
        # the owner speaking, but never an owner-only path (a channel account is weaker than a paired remote)
        self.assertFalse(provenance.is_owner(provenance.wechat_owner()))
        self.assertTrue(provenance.untrusted_turn(NS(cur_send=NS(source=provenance.wechat_owner()))))
        self.assertIn('主人本人', provenance.header(provenance.wechat_owner()))
        self.assertIn("owner's own message", provenance.hook_context(provenance.wechat_owner()))
        self.assertEqual(provenance.from_name(provenance.wechat_owner()), provenance.WECHAT_OWNER)
        self.assertTrue(provenance.names_untrusted('from-name="' + provenance.WECHAT_OWNER + '"'))

    def test_a_stricter_request_source_wins(self):
        agent = NS(cur_send=NS(source=provenance.telegram_owner(5)))
        self.assertEqual(provenance.owner_channel_turn(agent), 'telegram')
        with provenance.request_source(provenance.telegram_group(-100, 7, 'proxy', 5)):
            self.assertIsNone(provenance.owner_channel_turn(agent))

    def test_a_tie_never_lets_an_owner_channel_mask_a_group(self):
        """Review B1: an OpenCode child started in a group turn finishing during a WeChat turn keeps the group's rule."""
        group, wechat = provenance.normalize(provenance.telegram_group(-100, 7, 'proxy', 5)), provenance.wechat_owner()
        self.assertEqual(provenance.stricter(wechat, group), group)
        self.assertEqual(provenance.stricter(group, wechat), group)
        self.assertEqual(provenance.stricter(wechat, provenance.email_guest('g@example.com'))['role'], 'group')
        self.assertEqual(provenance.stricter(wechat, None)['kind'], 'unknown')
        self.assertEqual(provenance.stricter(wechat, wechat), wechat)
        self.assertEqual(provenance.stricter(provenance.phone(), wechat), wechat)
        agent = NS(cur_send=None, out_src=provenance.stricter(wechat, group))
        self.assertIsNone(provenance.owner_channel_turn(agent))

    def test_image_requests(self):
        for text in (ASK_336, '帮我画一张宫崎骏风格的美少女在山间遛狗的图片，16:9', '画一只橘猫', 'draw me a cat'):
            self.assertTrue(owner_channels.wants_image(text), text)
        for text in ('做个 PDF 发我', '这张图里是什么', '画个流程图解释一下架构', '把截图发给我', '查一下明天天气', 'send me the picture'):
            self.assertFalse(owner_channels.wants_image(text), text)


class Approvals(Base):
    """A step of an owner-channel turn: the channel's card on the Web App; refused sources stay refused."""

    def turn(self, source, told=None):
        self.host.agent.cur_send = NS(source=source, on_card=(lambda: told.append(1)) if told is not None else None)

    def go(self, cmd, answer, tool='Bash'):
        async def run():
            t = asyncio.ensure_future(self.host.ask(tool, {'command': cmd} if tool == 'Bash' else cmd))
            for _ in range(300):
                await asyncio.sleep(0.01)
                open_ = [o for o in self.cards() if o['id'] in self.host.asks]
                if open_:
                    await asyncio.sleep(0.1)
                    if answer is not None:
                        await self.host._answer(self.s, self.phone.answer(open_[-1], answer))
                    break
            return await t
        self.host.ask_ttl = 3
        with mock.patch.object(owner_channels, 'CARD_NOTICE_AFTER', 0.05):
            return asyncio.run(run())

    def test_each_channel_asks_on_a_card_and_tells_the_channel_once(self):
        for source, label in ((provenance.wechat_owner(), '来自微信'), (provenance.telegram_owner(5), '来自 Telegram'),
                              (provenance.email_owner('boss@example.com'), '来自邮件')):
            told = []
            self.turn(source, told)
            self.sent.clear()
            res = self.go('rm -rf build', True)
            self.assertEqual(res['behavior'], 'allow', label)
            card = self.cards('Bash')[-1]
            self.assertIn(label, card['summary'])
            self.assertEqual(told, [1], 'the channel hears where to confirm, once')
            self.assertFalse(self.host.grants, 'never a batch grant')

    def test_full_level_face_id_kinds_stay_on_the_card(self):
        self.full()
        told = []
        self.turn(provenance.wechat_owner(), told)
        res = self.go('ls -la', None)                       # ① passes like the main conversation (P559)
        self.assertEqual(res['behavior'], 'allow'); self.assertEqual(self.cards('Bash'), [])
        res = self.go('rm -rf build', None)                 # one of the four ③ kinds: Face ID on the Web App card
        self.assertEqual(res['behavior'], 'deny')
        card = self.cards('Bash')[-1]
        self.assertTrue(card.get('passkey_required')); self.assertIn('来自微信', card['summary'])
        self.assertEqual(told, [1])

    def test_no_card_no_channel_notice(self):
        """Review S3: a slow decision that ends without a card never sends 「已转到 Agent J 网页」."""
        told = []
        self.turn(provenance.wechat_owner(), told)

        async def slow(*_a, **_k):
            await asyncio.sleep(0.2)
            return {'behavior': 'allow'}

        async def run():
            with mock.patch.object(owner_channels, 'CARD_NOTICE_AFTER', 0.05):
                return await owner_channels.ask(self.host, 'wechat', lambda _lbl: slow())
        self.assertEqual(asyncio.run(run())['behavior'], 'allow')
        self.assertEqual(told, [])

    def test_groups_guests_and_questions_are_refused_without_a_card(self):
        for source in (provenance.telegram_group(-100, 7, 'proxy', 5), provenance.email_guest('guest@example.com')):
            self.turn(source, [])
            self.sent.clear()
            res = self.go('rm -rf build', None)
            self.assertEqual(res['behavior'], 'deny'); self.assertEqual(self.cards(), [], source)
        self.turn(provenance.wechat_owner(), [])
        res = self.go({'questions': []}, None, tool='AskUserQuestion')
        self.assertEqual(res['behavior'], 'deny'); self.assertNotIn('分身', res['message'])


class _WeChat(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from agentj import wechat_protocol as protocol
        from agentj.wechat import WeChat
        from test_l1 import _host, _state
        from test_p501_wechat import ACCOUNT, Transport
        self.tmp = tempfile.TemporaryDirectory(prefix='p562-', dir=TEST_TMP); self.addCleanup(self.tmp.cleanup)
        self.st = _state(self.tmp.name); self.sent = []
        self.host = _host(self.st, self.sent)
        self.work = pathlib.Path(self.tmp.name) / 'work'; self.work.mkdir()
        self.host.agent_cfg = {'dir': str(self.work), 'kind': 'claude'}
        self.host.agent = NS(kind='claude', status='idle', halting=False, submit=MagicMock(), cur_send=None)
        self.c = WeChat(self.host)
        self.host.wechat = self.c
        self.c.store.data['account'] = dict(ACCOUNT); self.c.store.save()
        self.tr = Transport([{}] * 20); self.api = protocol.API(ACCOUNT, self.tr)
        self.c.api_factory = lambda *_a: self.api
        self.c.publish = lambda: None

    async def asyncTearDown(self):
        await self.c.stop()

    def said(self):
        out = []
        for call in self.tr.calls:
            item = json.loads(call[0][3])['msg']['item_list'][0]
            out.append(item.get('text_item', {}).get('text') if item['type'] == 1 else item['type'])
        return out

    async def receive(self, text, mid=1):
        await self.c.receive(self.api, {'from_user_id': 'owner', 'message_type': 1, 'message_id': mid, 'context_token': 'ctx',
                                        'item_list': [{'type': 1, 'text_item': {'text': text}}]}, self.c.store.data['generation'])


class WeChatMain(_WeChat):
    async def test_an_ordinary_question_goes_through_the_real_host_and_back(self):
        with mock.patch.object(self.host, 'push_notify'):
            await self.receive('明天东京天气怎么样')
        self.host.agent.submit.assert_called_once()
        send = self.host.agent.submit.call_args.args[0]
        self.assertEqual(send.source, provenance.wechat_owner())
        self.assertIn('明天东京天气怎么样', send.text)
        page = self.host.hist.get(send.turn)
        self.assertEqual((page['src']['k'], page['src']['name'], page['src']['text']), ('wechat', '微信', '明天东京天气怎么样'))
        self.assertEqual(self.said(), [], 'the answer comes from the main conversation')
        # the main conversation answers the turn → the same words in WeChat
        self.host.agent.cur_send = send
        self.host.agent_turn_start(send.text, send)
        self.host.agent_text('明天东京晴，最高 18 度。')
        self.host.agent_turn_end()
        await asyncio.gather(*list(self.c.card_tasks))
        self.assertEqual(self.said(), ['明天东京晴，最高 18 度。'])
        self.assertEqual(self.host.hist.get(send.turn)['reply']['text'], '明天东京晴，最高 18 度。')

    async def test_a_waiting_card_is_told_in_wechat(self):
        with mock.patch.object(self.host, 'push_notify'):
            await self.receive('把 build 目录删了')
        send = self.host.agent.submit.call_args.args[0]
        await send.on_card()
        self.assertEqual(self.said(), ['已转到 Agent J 网页，请在那里确认。'])

    async def test_busy_main_conversation_queues_it(self):
        self.host.agent.status = 'working'
        with mock.patch.object(self.host, 'push_notify'):
            await self.receive('一')
            await self.receive('二', mid=2)
        self.assertEqual(self.host.agent.submit.call_count, 2, 'both wait in the one main queue, no second conversation')


class Draw336(_WeChat):
    async def test_the_336_message_is_drawn_and_sent(self):
        res = TurnResult('reply', '画好了：东京塔前的人像，写实风格。', 'image', None, None); res.images = [('image/png', PNG)]
        self.c.draw = AsyncMock(return_value=res)
        with mock.patch('agentj.wechat_protocol.Media.upload', return_value={'type': 2, 'image_item': {'media': {'x': 1}}}):
            await self.receive(ASK_336)
        self.c.draw.assert_awaited_once_with(ASK_336)
        self.host.agent.submit.assert_not_called()
        self.assertEqual(self.said(), ['画好了：东京塔前的人像，写实风格。', 2])
        page = [t for t in self.host.hist.turns.values() if t['src'].get('k') == 'wechat'][-1]
        self.assertEqual(page['src']['text'], ASK_336)
        self.assertIn('画好了', page['reply']['text'])

    async def test_a_refusal_says_why_and_offers_a_way_on(self):
        from agentj import channel_native
        refused = TurnResult('reply', '不能生成真实人物的写实肖像。', 'image', None, None)
        with mock.patch.object(channel_native, 'draw_models', return_value=[None]), \
                mock.patch.object(channel_native.NativeCodex, 'turn', AsyncMock(return_value=refused)):
            await self.receive(ASK_336)
        [words] = self.said()
        self.assertIn('不能生成真实人物的写实肖像', words)
        self.assertIn('不要写实风格', words); self.assertIn('本人照片', words)
        self.host.agent.submit.assert_not_called()

    async def test_codex_main_conversation_draws_itself(self):
        self.host.agent.kind = 'codex'
        self.c.draw = AsyncMock()
        with mock.patch.object(self.host, 'push_notify'):
            await self.receive(ASK_336)
        self.c.draw.assert_not_awaited(); self.host.agent.submit.assert_called_once()


class PublishedFormat(unittest.IsolatedAsyncioTestCase):
    """A wechat.bin as 0.25.15a1 wrote it: held request, avatar plan, avatar sessions, finished rows."""

    async def test_old_state_loads_and_a_held_request_still_opens_its_card(self):
        from agentj.wechat import WeChat
        from agentj.wechat_store import Store
        from test_l1 import _host, _state
        with tempfile.TemporaryDirectory(prefix='p562f-', dir=TEST_TMP) as d:
            st = _state(d)
            old = Store(st)
            now = old.data.get('at') or __import__('time').time()
            old.data.update(account={'bot_token': 'b', 'ilink_bot_id': 'i', 'ilink_user_id': 'owner', 'baseurl': 'https://ilinkai.weixin.qq.com'},
                            context='ctx', greeting='sent', avatar={'asked': True, 'config': {'kind': 'codex'}},
                            sessions={'wechat-owner:claude': 'old-session'},
                            records={'held': {'id': 'held', 'paths': [], 'text': '帮我订机票', 'at': now, 'until': now + 3600,
                                              'state': 'pending', 'source': 'wechat', 'untrusted': True, 'execute': False},
                                     'done': {'id': 'done', 'paths': [], 'text': '翻译', 'at': now, 'until': now + 3600,
                                              'state': 'replied', 'source': 'wechat', 'untrusted': True, 'execute': False,
                                              'reply_summary': '译文', 'outcome': 'reply_prepared', 'delivery': 'sent'},
                                     'wait': {'id': 'wait', 'paths': [], 'text': 'x', 'at': now, 'until': now + 3600,
                                              'state': 'waiting_model', 'source': 'wechat', 'untrusted': True, 'execute': False}})
            old.save(); old.close()
            host = _host(st, [])
            c = WeChat(host)
            try:
                self.assertEqual({k: r['state'] for k, r in c.store.data['records'].items()},
                                 {'held': 'pending', 'done': 'replied', 'wait': 'waiting_model'})
                c.completed({'id': 1, 'src': {'k': 'wechat'}, 'reply': {'text': 'x'}})   # no 'main' row: nothing is sent
                c.store.data['records']['wait']['state'] = 'main'; c.store.data['records']['wait']['turn'] = 1
                c.poll_once = AsyncMock(side_effect=lambda *_a: setattr(c, 'closed', True))
                with mock.patch.object(host, 'push_notify'):
                    await asyncio.wait_for(c.run(), 5)         # the start re-arms held requests (open_card) and polls once
                    await asyncio.sleep(0.05)
                self.assertEqual(c.store.data['records']['wait']['state'], 'orphaned', 'review S4: a turn of the last run')
                self.assertIn('held', c.cards, 'the 0.25.15 held request still opens its signed card')
            finally:
                await c.stop()


class Mail(unittest.IsolatedAsyncioTestCase):
    """Forged and strange senders never reach the main conversation as the owner."""

    async def test_forged_owner_address_and_stranger_are_not_delivered(self):
        from email import policy as ep
        from email.parser import BytesParser
        from agentj import mail as M
        cfg = M.blank_config()
        cfg.update(enabled=True, address='agent@example.com', allow=[{'addr': 'boss@example.com', 'role': 'owner'}], auth='standard')

        def parse(frm, ar):
            raw = (f'From: {frm}\nTo: agent@example.com\nMessage-ID: <{abs(hash(frm + ar))}@example.com>\nSubject: hi\n'
                   + (f'Authentication-Results: mx.example.com; {ar}\n' if ar else '') + '\n帮我查一下\n').encode()
            return BytesParser(policy=ep.default).parsebytes(raw), raw
        forged = M.judge(parse('boss@example.com', 'spf=fail smtp.mailfrom=evil.example; dkim=none; dmarc=fail header.from=example.com')[0], cfg, [])
        self.assertEqual((forged.ok, forged.reason), (False, 'auth'))
        stranger = M.judge(parse('who@else.example', 'dmarc=pass header.from=else.example')[0], cfg, [])
        self.assertEqual((stranger.ok, stranger.reason), (False, 'not_allowed'))
        real = M.judge(parse('boss@example.com', 'dkim=pass header.d=example.com; dmarc=pass header.from=example.com')[0], cfg, [])
        self.assertEqual((real.ok, real.role), (True, 'owner')); self.assertIn(real.auth, M.VERIFIED)



from test_p170_mail import BOSS, _Base as _MailBase  # noqa: E402
from mailfake import mail as _letter  # noqa: E402


class MailReplyCard(_MailBase):
    """Review (10-28 03:3x): every `agentj mail reply` asks on the P503d card — also the owner's own verified letter
    answered only to the owner in the same thread (the self-reply exemption was withdrawn: crossing a gate is the chairman's
    call). An unverified letter is the guest's: its route is not role=owner and its reply is redacted like a guest's."""

    def reply_asks(self):
        return [c for c in self.host.ask.await_args_list if c.args[0] == 'agentj mail reply']

    async def test_the_owners_same_thread_self_reply_still_asks(self):
        from agentj import mail as M
        self.imap.add(_letter(msgid='<self@corp.example>'))
        await self.m.check()
        route = M.read_state(self.st)['routes']['101']
        self.assertEqual((route['role'], route['verified']), ('owner', True))
        await self.finish(101, '收到，周报已整理好。')
        [ask] = self.reply_asks()
        self.assertEqual(ask.kwargs.get('avatar'), '来自邮件')
        [(_f, to, msg)] = self.sent()
        self.assertEqual((to, msg['In-Reply-To'], msg['Cc']), ([BOSS], '<self@corp.example>', None))
        self.assertNotIn('reply_self', M.read_state(self.st)['counts'])

    async def test_an_unverified_letter_is_routed_and_answered_as_a_guest(self):
        from agentj import mail as M
        cfg = M.read_config(self.st); cfg['auth'] = 'off'; self.m.set_config(cfg)
        self.imap.add(_letter(msgid='<off@corp.example>'))
        await self.m.check()
        [(session, _text, _)] = self.delivered()
        self.assertFalse(provenance.is_owner(session.provenance))
        route = M.read_state(self.st)['routes']['101']
        self.assertEqual((route['role'], route['verified']), ('guest', False))
        (self.work / 'plan.txt').write_text('内部计划')
        await self.finish(101, f'联系 secret-person@private.example 就行，文件在 {self.work}/plan.txt')
        [ask] = self.reply_asks()
        self.assertNotIn('avatar', ask.kwargs)
        [(_f, to, msg)] = self.sent()
        body = msg.get_body(('plain',)).get_content()
        self.assertEqual(to, [BOSS])
        self.assertNotIn('secret-person@private.example', body)
        self.assertEqual(list(msg.iter_attachments()), [])

    async def test_a_route_without_the_verified_mark_is_answered_as_a_guest(self):
        """A route written by 0.25.15 (no `verified`) — the published format — never gets the owner's rules."""
        from agentj import mail as M
        self.imap.add(_letter(msgid='<old-route@corp.example>'))
        await self.m.check()
        s = M.read_state(self.st); s['routes']['101'].pop('verified'); M.save_state(self.st, s)
        await self.finish(101, '联系 secret-person@private.example 就行')
        [ask] = self.reply_asks()
        self.assertNotIn('avatar', ask.kwargs)
        [(_f, _to, msg)] = self.sent()
        self.assertNotIn('secret-person@private.example', msg.get_body(('plain',)).get_content())

class TelegramStranger(unittest.IsolatedAsyncioTestCase):
    async def test_a_stranger_private_chat_keeps_the_old_path(self):
        import test_p118_telegram as fixture
        base = fixture._Base(); base.setUp()
        try:
            base.t.stranger = AsyncMock()
            self.assertFalse(await base.t.incoming(fixture.msg('你好', uid=777, cid=777), base.cfg))
            base.host._accept.assert_not_awaited(); base.t.stranger.assert_awaited_once()
        finally:
            base.doCleanups()


if __name__ == '__main__':
    unittest.main()
