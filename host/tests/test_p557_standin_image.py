"""P557: the owner's channel avatar finishes what it may do itself. Wording (local paths, internal tool words) is removed
and the reply is still sent, generated images go out as attachments (Telegram sendPhoto, WeChat image items); only a held
action (ask_owner) and real credentials go to Agent J Web App. An executor without an image tool asks the host to draw
with the owner's Codex in its own sandbox (macOS: sandbox-exec), never a paid per-use service."""
import _hermetic  # noqa: F401,I001
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

from agentj.channel_avatar import public_reply, Reply
from agentj.peer_session import TurnResult

PNG = b'\x89PNG\r\n\x1a\nfixture'
SAVED = '画好了！宫崎骏风格的少女在山坡遛狗，16:9。\n图片已保存到 /var/fixture/codex/generated_images/t1/ghibli.png'
CREDENTIAL = 'sk-ant-api03-' + 'Q7' * 45


class Projection(unittest.TestCase):
    def test_paths_and_tool_words_are_removed_not_handed_off(self):
        self.assertEqual(public_reply(SAVED), '画好了！宫崎骏风格的少女在山坡遛狗，16:9。')
        self.assertEqual(public_reply('Saved to ./out/x.png, enjoy the picture.'), 'enjoy the picture.')
        self.assertEqual(public_reply('调用 image_generation 画好了一只猫。很可爱。'), '很可爱。')
        self.assertEqual(public_reply('画好了，路径 C:\\Users\\me\\out.png'), '画好了')
        self.assertEqual(public_reply('图片在 outbox/abc.png。很好看。'), '很好看。')

    def test_ordinary_slashes_links_and_words_stay(self):
        text = '是/否都可以，and/or 也行，/start 命令，16/9 比例，本书目录在第一页，详见 https://example.com/a/b.png 这里。'
        self.assertEqual(public_reply(text), text)

    def test_image_only_reply_gets_a_caption_and_credentials_still_hold(self):
        self.assertEqual(public_reply('~/out/cat.png', [('image/png', PNG)], 'zh'), '图片在这里。')
        self.assertEqual(public_reply('~/out/cat.png', [('image/png', PNG)], 'en'), 'Here is the image.')
        self.assertEqual(public_reply('已保存到 /work/out.png', [('image/png', PNG)]), '图片在这里。')
        self.assertIsNone(public_reply('saved at /work/out.png'))
        self.assertIsNone(public_reply('画好了。key ' + CREDENTIAL, [('image/png', PNG)]))


class _Channel(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from _testtmp import TEST_TMP
        from agentj.state import State
        from agentj.external_channels import ExternalChannels
        self.tmp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.st = State(Path(self.tmp.name) / 'state'); self.st.init()
        work = Path(self.tmp.name) / 'work'; work.mkdir()
        self.host = NS(st=self.st, agent_cfg={'kind': 'claude', 'model': 'sonnet', 'dir': str(work)}, preferences={},
                       stopped=lambda: False, agent_notice=MagicMock(), _bg=MagicMock(), lang='zh',
                       hist=NS(turns={}), hist_add=MagicMock(return_value={'id': 7}), agent=NS(submit=MagicMock(), cur_send=None))
        self.channel = ExternalChannels(self.host, model=AsyncMock())
        self.channel.index = __import__('agentj.peer_guard', fromlist=['SecretIndex']).SecretIndex()
        self.channel.open_card = MagicMock()

    async def asyncTearDown(self):
        self.channel.closed = True
        await self.channel.sessions.stop_all()
        self.channel.store.close()

    def answer(self, decision, text, topic='', images=()):
        res = TurnResult(decision, text, topic, None, None)
        res.images = list(images)
        self.channel.model = AsyncMock(return_value=res)


class TelegramAvatar(_Channel):
    async def test_generated_image_with_path_is_sent_not_handed_off(self):
        self.answer('reply', SAVED, images=[('image/png', PNG)])
        reply = await self.channel.receive_owner('telegram', 'g:1', '帮我画宫崎骏风格的美少女山野遛狗 16:9')
        self.assertIsInstance(reply, Reply)
        self.assertEqual(reply.images, (('image/png', PNG),))
        self.assertNotIn('/', reply); self.assertNotIn('已转给', reply)
        self.channel.open_card.assert_not_called(); self.host.agent.submit.assert_not_called()
        self.assertEqual(self.channel.store.data['counts'].get('reply_rewritten'), 1)

    async def test_real_overreach_is_still_handed_off(self):
        self.answer('ask_owner', '要动主人电脑上的文件，先问主人')
        reply = await self.channel.receive_owner('telegram', 'g:2', '把我桌面上的合同发给张三')
        self.assertIn('已转给', reply)
        self.channel.open_card.assert_called_once()

    async def test_credential_reply_is_still_held_and_no_image_leaves(self):
        self.answer('reply', '画好了，顺便给你 key ' + CREDENTIAL, images=[('image/png', PNG)])
        reply = await self.channel.receive_owner('telegram', 'g:3', '画猫')
        self.assertIn('已转给', reply)
        self.assertEqual(getattr(reply, 'images', ()), ())

    async def test_executor_without_image_tool_asks_the_owners_codex_to_draw(self):
        self.answer('ask_owner', 'a cat', topic='image')
        drawn = TurnResult('reply', '画好了一只橘猫。保存在 /avatar/outbox/cat.png', '', None, None)
        drawn.images = [('image/png', PNG)]
        with patch('agentj.channel_native.NativeCodex.turn', AsyncMock(return_value=drawn)) as turn:
            reply = await self.channel.receive_owner('telegram', 'g:4', '画一只橘猫')
        self.assertEqual(turn.call_args.kwargs['cfg'], {'kind': 'codex'})     # never the avatar's Claude model
        self.assertEqual(str(reply), '画好了一只橘猫。'); self.assertEqual(reply.images, (('image/png', PNG),))
        self.channel.open_card.assert_not_called()

    async def test_drawer_unavailable_says_why_in_the_chat_never_a_paid_route(self):
        # P559: a failed drawing is said in the channel itself — never handed to the main conversation / Web App
        self.answer('ask_owner', 'a cat', topic='image')
        with patch('agentj.channel_native.NativeCodex.turn', AsyncMock(return_value=TurnResult('ask_owner', '', '', None, 'native_image_unavailable'))):
            reply = await self.channel.receive_owner('telegram', 'g:5', '画一只橘猫')
        self.assertNotIn('已转给', reply)
        self.assertIn('没有能画图的 Codex', reply); self.assertIn('没有使用按次付费的服务', reply)

    def test_main_conversation_hint_prefers_codex_over_paid_services(self):
        from agentj.wechat import DRAW_HINT
        self.assertIn('codex exec', DRAW_HINT); self.assertIn("owner's yes", DRAW_HINT)
        self.assertNotIn('Higgsfield', DRAW_HINT)


class TelegramDelivery(unittest.IsolatedAsyncioTestCase):
    async def test_owner_chat_gets_text_then_photo(self):
        import test_p118_telegram as fixture
        base = fixture._Base(); base.setUp()
        try:
            base.host.external_channels = NS(receive_owner=AsyncMock(return_value=Reply('画好了。', [('image/png', PNG)])))
            self.assertTrue(await base.t.incoming(fixture.msg('画猫'), base.cfg))
            self.assertEqual(len(base.bot.sent('sendPhoto')), 1)
            self.assertTrue(any('画好了' in json.dumps(m, ensure_ascii=False) for m in base.bot.sent('sendMessage')))
        finally:
            base.doCleanups()


class WeChatAvatar(_Channel):
    async def test_wechat_sends_clean_text_and_image_item(self):
        from agentj import wechat_protocol as protocol
        from agentj.wechat import WeChat
        from test_p501_wechat import ACCOUNT, Transport
        res = TurnResult('reply', SAVED, '', None, None); res.images = [('image/png', PNG)]
        c = WeChat(self.host, model=AsyncMock(return_value=res))
        c.index = self.channel.index; c.store.data['account'] = dict(ACCOUNT)
        c.open_card = MagicMock(); c.publish = lambda: None
        tr = Transport([{}, {}]); api = protocol.API(ACCOUNT, tr)
        try:
            with patch.object(protocol.Media, 'upload', return_value={'type': 2, 'image_item': {'media': {'fixture': True}}}) as upload:
                await c.receive(api, {'from_user_id': 'owner', 'message_type': 1, 'message_id': 1, 'context_token': 'fixture',
                                      'item_list': [{'type': 1, 'text_item': {'text': '画图'}}]}, c.store.data['generation'])
            upload.assert_called_once()
            first = json.loads(tr.calls[0][0][3])['msg']['item_list'][0]
            self.assertEqual(first['text_item']['text'], '画好了！宫崎骏风格的少女在山坡遛狗，16:9。')
            self.assertEqual(json.loads(tr.calls[1][0][3])['msg']['item_list'][0]['type'], 2)
            c.open_card.assert_not_called()
        finally:
            c.closed = True; c.store.close()

    async def test_wechat_overreach_still_handoff(self):
        from agentj import wechat_protocol as protocol
        from agentj.wechat import WeChat
        from test_p501_wechat import ACCOUNT, Transport
        c = WeChat(self.host, model=AsyncMock(return_value=TurnResult('ask_owner', '要花钱', '', None, None)))
        c.index = self.channel.index; c.store.data['account'] = dict(ACCOUNT)
        c.open_card = MagicMock(); c.publish = lambda: None
        tr = Transport([{}]); api = protocol.API(ACCOUNT, tr)
        try:
            await c.receive(api, {'from_user_id': 'owner', 'message_type': 1, 'message_id': 2, 'context_token': 'fixture',
                                  'item_list': [{'type': 1, 'text_item': {'text': '帮我买张机票'}}]}, c.store.data['generation'])
            self.assertIn('已转给', json.loads(tr.calls[0][0][3])['msg']['item_list'][0]['text_item']['text'])
            c.open_card.assert_called_once()
        finally:
            c.closed = True; c.store.close()


class DarwinCommand(unittest.IsolatedAsyncioTestCase):
    def test_macos_sandbox_hides_owner_files_and_only_scratch_is_writable(self):
        from _testtmp import TEST_TMP
        from agentj import channel_native, peer_session
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            t = Path(tmp)
            rel = t / 'release/bin'; rel.mkdir(parents=True)
            for name in ('codex', 'codex-code-mode-host'):
                (rel / name).write_bytes(b'')
            owner = t / 'owner'; (owner / '.codex').mkdir(parents=True)
            (owner / '.codex/auth.json').write_text('{}')
            sbx = t / 'sandbox-exec'; sbx.write_bytes(b'')
            root = t / 'state-peer-sandbox/native-owner'
            for d in ('codex', 'outbox', 'input'):
                (root / d).mkdir(parents=True)
            feats = set(peer_session.CODEX_DISABLE) | {'image_generation', 'code_mode_host'}
            with patch.object(channel_native.sys, 'platform', 'darwin'), patch.object(channel_native, 'SANDBOX_EXEC', str(sbx)), \
                 patch.object(channel_native.peer_session, 'codex_features', return_value=feats), \
                 patch.object(channel_native.peer_session, 'codex_user_model', return_value={}), \
                 patch('agentj.channel_native.os.path.expanduser', return_value=str(owner)):
                cmd, env = channel_native.command(str(rel / 'codex'), root, {}, {'HOME': str(owner), 'HTTPS_PROXY': 'http://proxy.example:8'}, images=['a.png'])
                images_dir = channel_native.out_dir(root)
            self.assertEqual(cmd[0], str(sbx))
            profile = cmd[2]
            for rule in ('(deny file-read* file-write* (subpath "/Users") (subpath "/Volumes") (subpath "/private/tmp") (subpath "/private/var/folders")',
                         '(deny file-write*)', '(deny file-link)', '(deny process-exec)',
                         '(deny network-outbound (remote unix-socket (subpath "/private/var/folders")))', '(allow process-exec (subpath (param "EXE_DIR")))',
                         '(allow file-read* (literal (param "AUTH")))', '(deny appleevent-send)'):
                self.assertIn(rule, profile)
            self.assertNotIn(str(owner), profile)                                  # paths travel only as -D parameters
            params = dict(cmd[i + 1].split('=', 1) for i, x in enumerate(cmd) if x == '-D')
            self.assertEqual(params['HOME'], os.path.realpath(owner))
            self.assertEqual(params['CODEX'], os.path.realpath(root / 'codex'))
            self.assertNotIn('ROOT_WRITE', params)
            self.assertEqual(params['AUTH'], os.path.realpath(owner / '.codex/auth.json'))
            self.assertEqual(os.readlink(root / 'codex/auth.json'), os.path.realpath(owner / '.codex/auth.json'))
            self.assertEqual(env['CODEX_HOME'], str(root / 'codex')); self.assertEqual(env['HOME'], str(root / 'home'))
            self.assertEqual(env['HTTPS_PROXY'], 'http://proxy.example:8')
            self.assertEqual(cmd[cmd.index('-i') + 1], str(root / 'input') + '/a.png')
            for feature in ('shell_tool', 'unified_exec', 'plugins', 'multi_agent', 'code_mode'):
                self.assertEqual(cmd[cmd.index(feature) - 1], '--disable')
            self.assertEqual(images_dir, root / 'codex/generated_images')
            self.assertTrue(images_dir.is_dir())
            # the harness can write inside codex/: a symlinked output folder is refused, never followed
            images_dir.rmdir(); images_dir.symlink_to(owner)
            self.assertFalse(channel_native.contained(root, images_dir))
            with patch.object(channel_native.sys, 'platform', 'darwin'), patch.object(channel_native, 'SANDBOX_EXEC', str(sbx)), \
                 patch('agentj.channel_native.os.path.expanduser', return_value=str(owner)), self.assertRaises(ValueError):
                channel_native._darwin(str(rel / 'codex'), root, owner / '.codex/auth.json')
            # a release folder that would cover the owner's home or the avatar root is refused
            with patch.object(channel_native, 'SANDBOX_EXEC', str(sbx)), \
                 patch('agentj.channel_native.os.path.expanduser', return_value=str(t / 'release/bin')), self.assertRaises(ValueError):
                channel_native._darwin(str(rel / 'codex'), t / 'release/scratch', owner / '.codex/auth.json')

    async def test_symlinked_or_hardlinked_outputs_are_not_attached(self):
        from _testtmp import TEST_TMP
        from agentj import channel_native, peer_session
        from agentj.channel_native import NativeCodex
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            root = Path(tmp); secret = root / 'owner-file.png'; secret.write_bytes(PNG + b'owner')
            sessions = NS(st=NS(write_private=lambda p, data: p.write_bytes(data)), store=NS(session_id=lambda *a: None, set_session_id=lambda *a: None),
                          _gen=0, _exe=lambda _: '/fixture/codex', cfg=lambda: {}, system_prompt=lambda *a: 'policy', input_images=())
            ok = '\n'.join((json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '{"decision":"reply","text":"ok","topic":""}'}}),
                            '{"type":"turn.completed"}'))
            async def hardlink(*args):
                os.link(secret, root / 'native-owner/outbox/linked.png'); return ok, '', 0
            async def symlinked(*args):
                out = root / 'native-owner/outbox'
                for f in out.iterdir():
                    f.unlink()
                out.rmdir(); out.symlink_to(root / 'elsewhere')
                (root / 'elsewhere').mkdir(); (root / 'elsewhere/new.png').write_bytes(PNG)
                return ok, '', 0
            native = NativeCodex(sessions)
            with patch.object(peer_session, 'sandbox_root', return_value=root), patch('agentj.channel_native.command', return_value=(['fixture'], {})):
                sessions._run = hardlink
                self.assertEqual((await native.turn({'id': 'owner'}, {}, '画')).images, [])
                sessions._run = symlinked
                res = await native.turn({'id': 'owner'}, {}, '画')
                self.assertTrue(res.error or res.images == [])


if __name__ == '__main__':
    unittest.main()
