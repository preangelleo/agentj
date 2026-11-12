"""#558 real WeChat transport-shaped regression, no accounts or network."""
import _hermetic  # noqa: F401
import asyncio
import base64
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import test_p501_wechat as fx
from agentj import media
from agentj.wechat_protocol import API, CDN, encrypt


def png():
    import struct, zlib
    def chunk(t, d):
        return struct.pack('!I', len(d)) + t + d + struct.pack('!I', zlib.crc32(t+d))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!IIBBBBB', 1, 1, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(b'\x00\x20\x40\x60')) + chunk(b'IEND', b'')


class Images(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fx.Channel.asyncSetUp
    asyncTearDown = fx.Channel.asyncTearDown

    def arm(self, root=None):
        self.h.media = media.Media(self.st, str(self.work), generated=lambda: root)
        self.c.store.data.update(context='fixture-context', context_at=self.c.clock())
        self.tr = fx.Transport([{}] + [{'upload_full_url':CDN+'/upload'}, (200, {'x-encrypted-param':'fixture-down'}, b''), {}] * 5)
        self.c.api_factory = lambda account: API(account, self.tr)

    def kinds(self):
        return [json.loads(c[0][3])['msg']['item_list'][0]['type'] for c in self.tr.calls if 'sendmessage' in c[0][1]]

    async def reply(self, text):
        r = {'id':'r1', 'generation':self.c.store.data['generation'], 'state':'main', 'turn':7}
        self.c.store.data['records']['r1'] = r
        await self.c.answer_main(r, text, False)
        return r

    async def test_codex_current_session_generated_image(self):
        out = self.root/'codex'/'generated_images'/'current'
        out.mkdir(parents=True); (out/'image.png').write_bytes(png())
        self.arm(str(out))
        await self.reply(f'画好了：![图]({out}/image.png)')
        self.assertEqual(self.kinds(), [1, 2])

    async def test_claude_opencode_headless_output_copied_to_workdir(self):
        from agentj.wechat import DRAW_HINT
        self.assertIn('working folder', DRAW_HINT)
        source = self.root/'codex'/'generated_images'/'headless'
        source.mkdir(parents=True); (source/'image.png').write_bytes(png())
        for kind in ('claude','opencode'):
            with self.subTest(kind=kind):
                self.h.agent_cfg['kind'] = kind
                dest = self.work/(kind+'.png'); dest.write_bytes((source/'image.png').read_bytes())
                self.arm()
                await self.reply(f'画好了：![图]({dest})')
                self.assertEqual(self.kinds(), [1, 2])

    async def test_forward_page_image_and_document(self):
        (self.work/'image.png').write_bytes(png()); (self.work/'notes.txt').write_text('a short note')
        self.arm()
        items, skips = self.h.media.scan('![图](image.png) [文件](notes.txt)', 1)
        self.assertFalse(skips)
        self.h.hist = NS(get=lambda _: {'id':1,'end':'done','reply':{'text':'图片和文件'},'media':items})
        result = await self.c.forward(1,'paired')
        self.assertTrue(result['ok']); self.assertEqual(self.kinds(), [1, 2, 4])

    async def test_customer_main_reply_many_chinese_absolute_paths(self):
        """A bound owner's final Claude reply sends each local file, not only page cards."""
        names = ['七港报告.pdf'] + [f'七港图{i}.png' for i in range(8)]
        for name in names:
            (self.work / name).write_bytes(b'%PDF-1.4\nfixture\n' if name.endswith('.pdf') else png())
        self.arm()
        # Provider-shaped responses: one text send + upload negotiation/CDN/send per attachment.
        self.tr.replies = [{}] + [{'upload_full_url': CDN+'/upload'},
                                     (200, {'x-encrypted-param':'fixture-down'}, b''), {}] * len(names)
        await self.reply('文件如下：\n' + '\n'.join(str(self.work / name) for name in names))
        self.assertEqual(len(self.kinds()), 10)
        self.assertEqual(self.kinds().count(2), 8)
        self.assertEqual(self.kinds().count(4), 1)
        self.assertEqual(self.c.store.data['counts'].get('send_failed', 0), 0)

        names = [f'新图{i}.jpg' for i in range(7)]
        for name in names:
            (self.work / name).write_bytes(b'\xff\xd8\xff\xe0fixture')
        self.arm()
        self.tr.replies = [{}] + [{'upload_full_url': CDN+'/upload'},
                                     (200, {'x-encrypted-param':'fixture-down'}, b''), {}] * len(names)
        await self.reply('图片如下：\n' + '\n'.join(str(self.work / name) for name in names))
        self.assertEqual(self.kinds(), [1] + [2] * 7)

    async def test_customer_attachment_upload_failure_counts_send_failed(self):
        (self.work / '七港图.png').write_bytes(png())
        self.arm()
        self.tr.replies = [{}, RuntimeError('fixture failure')]
        await self.reply(str(self.work / '七港图.png'))
        self.assertEqual(self.kinds(), [1])
        self.assertGreaterEqual(self.c.store.data['counts'].get('send_failed', 0), 1)

    async def test_inbound_png_named_jpg_reaches_main_once_as_png(self):
        self.arm()
        key = b'k'*16; data = png()
        self.tr.replies = [(200, {}, encrypt(data,key))]
        msg = self.msg(item_list=[{'type':2,'image_item':{'file_name':'photo.jpg','media':{'full_url':CDN+'/download','aes_key':base64.b64encode(key).decode()}}}])
        await self.c.receive(self.c.api_factory(fx.ACCOUNT), msg, self.gen)
        await self.c.receive(self.c.api_factory(fx.ACCOUNT), msg, self.gen)
        self.h._accept.assert_awaited_once()
        blob = self.h._accept.call_args.kwargs['blobs'][0]
        self.assertEqual(Path(blob.path).read_bytes(), data)
        self.assertEqual(blob.mime, 'image/png')
        self.assertEqual(Path(blob.path).suffix, '.png')

    msg = fx.Channel.msg

    async def test_inbound_gif_preserves_image_mime_and_suffix(self):
        self.arm()
        data = b'GIF89a' + bytes([1, 0, 1, 0, 128, 0, 0, 0, 0, 0, 255, 255, 255, 33, 249, 4, 1, 0, 0, 0, 0, 44, 0, 0, 0, 0, 1, 0, 1, 0, 0, 2, 2, 68, 1, 0, 59])
        self.tr.replies = [(200, {}, data)]
        await self.c.receive(self.c.api_factory(fx.ACCOUNT), self.msg(item_list=[{'type':2,'image_item':{'file_name':'photo.jpg','media':{'full_url':CDN+'/download'}}}]), self.gen)
        blob = self.h._accept.call_args.kwargs['blobs'][0]
        self.assertEqual(blob.mime, 'image/gif')
        self.assertEqual(Path(blob.path).suffix, '.gif')
        self.assertEqual(Path(blob.path).read_bytes(), data)

    async def test_inbound_invalid_image_not_delivered(self):
        self.arm(); self.tr.replies = [(200, {}, b'not an image')]
        await self.c.receive(self.c.api_factory(fx.ACCOUNT), self.msg(item_list=[{'type':2,'image_item':{'media':{'full_url':CDN+'/download'}}}]), self.gen)
        self.h._accept.assert_not_awaited()


class Scope(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = Images.asyncSetUp
    asyncTearDown = Images.asyncTearDown
    arm = Images.arm
    reply = Images.reply
    kinds = Images.kinds

    async def test_other_session_and_nonimage_not_allowed(self):
        current = self.root/'codex'/'generated_images'/'current'; current.mkdir(parents=True)
        sibling = current.with_name('current-other'); sibling.mkdir()
        (sibling/'image.png').write_bytes(png()); (current/'notes.txt').write_text('note')
        self.arm(str(current))
        for path in (sibling/'image.png', current/'notes.txt'):
            rec, why = self.h.media.check(media.Ref(str(path),str(path),True), owner=True)
            self.assertIsNone(rec); self.assertEqual(why, 'outside')
        (current/'image.png').write_bytes(png())
        rec, why = self.h.media.check(media.Ref(str(current/'image.png'),str(current/'image.png'),True), owner=False)
        self.assertIsNone(rec); self.assertEqual(why, 'outside')

    async def test_root_replaced_with_symlink_refused(self):
        out = self.root/'generated_images'/'current'; out.mkdir(parents=True)
        (out/'image.png').write_bytes(png()); self.arm(str(out))
        moved = out.with_name('moved'); out.rename(moved); out.symlink_to(moved, target_is_directory=True)
        rec, why = self.h.media.check(media.Ref(str(out/'image.png'),str(out/'image.png'),True), owner=True)
        self.assertIsNone(rec)

    async def test_session_switch_during_scan_skips_image_and_keeps_workdir_file(self):
        out = self.root/'generated_images'/'current'; out.mkdir(parents=True)
        (out/'image.png').write_bytes(png()); (self.work/'safe.png').write_bytes(png())
        self.arm(str(out))
        current = [str(out)]
        self.h.media.generated = lambda: current[0]
        scan = media._hash_and_scan
        def switch(*args, **kwargs):
            result = scan(*args, **kwargs)
            current[0] = None
            return result
        with patch.object(media, '_hash_and_scan', side_effect=switch):
            items, skips = self.h.media.scan(f'![image]({out}/image.png) ![safe](safe.png)', 1)
        self.assertEqual([it['name'] for it in items], ['safe.png'])
        self.assertEqual([it['why'] for it in skips], ['gone'])

    async def test_per_account_root_is_only_state_exception(self):
        out = self.st.root/'accounts'/'codex'/'fixture-account'/'generated_images'/'current'
        out.mkdir(parents=True); (out/'image.png').write_bytes(png())
        self.arm(str(out)); await self.reply(f'画好了：![图]({out}/image.png)')
        self.assertEqual(self.kinds(), [1, 2])


class LaunchHome(unittest.IsolatedAsyncioTestCase):
    async def test_actual_child_env_used_for_owned_and_shared_adapters(self):
        from agentj.agent_codex import CodexAgent
        from agentj.shared_codex import SharedCodexAgent
        from agentj.serve import Host
        from unittest.mock import Mock, AsyncMock
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            for cls in (CodexAgent, SharedCodexAgent):
                with self.subTest(adapter=cls.__name__):
                    a = object.__new__(cls)
                    a.cfg = {'kind':'codex', 'dir':td, 'fence':False}
                    a.host = NS(preferences={}, st=NS(log=Mock()))
                    a._prepare_control_paths = AsyncMock()
                    a.argv = Mock(return_value=['fake-codex'])
                    a.authorized_launch_argv = AsyncMock(return_value=['fake-codex'])
                    a.call = AsyncMock(side_effect=[{}, {'config':{}}])
                    a._send = Mock(); a._bg = lambda coro: coro.close()
                    a._read = AsyncMock(); a._drain_err = AsyncMock()
                    child = NS(pid=999999999, returncode=None)
                    home = os.path.join(td,'state','accounts','codex','selected')
                    with patch('agentj.agent._bin', return_value='fake-codex'), patch('agentj.proxy.environment', return_value={'CODEX_HOME':home}), patch('agentj.agent_codex.asyncio.create_subprocess_exec', new=AsyncMock(return_value=child)), patch('agentj.workflow_mail.pin_current'), patch('agentj.codex_procs.record'):
                        self.assertTrue(await CodexAgent._spawn(a))
                    a.tid = 'current'
                    self.assertEqual(Host._agent_image_root(NS(agent=a)), home+'/generated_images/current')
                    a.image_home = None
                    self.assertIsNone(Host._agent_image_root(NS(agent=a)))


if __name__ == '__main__': unittest.main()
