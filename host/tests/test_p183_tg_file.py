"""P183: a file card's 「转发到 Telegram」 — one file a reply showed (its §13 media id) → the owner's own Telegram private
chat. Host cap `tgfile` (with the Telegram channel present, linked or not); `tg_fwd {mid}` → `tg_fwd_res`. The file is
re-opened and re-hashed right before the upload (changed / gone → not_found); over the bot upload limit → too_big; same
dedupe and per-device rate as the reply forward; the log holds counts only (no names, no paths)."""
import _hermetic  # noqa: F401,I001
from _testtmp import TEST_TMP  # noqa: E402  P202: fixtures on tmpfs, not the system disk
import asyncio
import os
import pathlib
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE))
from agentj import media as media_mod, tg_media  # noqa: E402
from test_p118_telegram import _Base, OWNER  # noqa: E402

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64


class FileForward(_Base):
    def setUp(self):
        super().setUp()
        self.work = self.root / 'work'; (self.work / 'out').mkdir(parents=True)
        (self.work / 'out' / 'chart.png').write_bytes(PNG)
        (self.work / 'out' / 'notes.md').write_text('# 标题\n\n正文\n')
        self.host.media = media_mod.Media(self.st, str(self.work))
        items, _ = self.host.media.scan('看 `out/chart.png` 和 [笔记](out/notes.md)', 7)
        self.mids = {m['name']: m['mid'] for m in items}

    async def test_picture_and_document_reach_the_owner_once(self):
        r = await self.t.forward_media(self.mids['chart.png'], 'dev1'); self.assertEqual(r, {'ok': True})
        photo = self.bot.sent('sendDocument'); self.assertEqual(len(photo), 1)
        self.assertEqual(photo[0]['fields']['chat_id'], str(OWNER)); self.assertEqual(photo[0]['files']['document']['data'], PNG)
        self.assertEqual(photo[0]['files']['document']['filename'], 'chart.png')
        r = await self.t.forward_media(self.mids['notes.md'], 'dev1'); self.assertTrue(r['ok'])
        doc = self.bot.sent('sendDocument')[1:]; self.assertEqual(len(doc), 1)
        self.assertEqual(doc[0]['files']['document']['filename'], 'notes.md')
        self.assertEqual((await self.t.forward_media(self.mids['chart.png'], 'dev1'))['why'], 'recently_sent')
        log = self.st.log_path.read_text()
        self.assertIn('telegram_forward', log); self.assertNotIn('chart.png', log); self.assertNotIn(str(self.work), log)

    async def test_changed_unknown_too_big_and_switched_off(self):
        (self.work / 'out' / 'notes.md').write_text('# 改过了\n')
        # P573: the card's scan-time copy is what forwards (the file card persists); without the copy → not_found as before
        import shutil
        shutil.rmtree(self.host.media.store, ignore_errors=True)
        self.assertEqual((await self.t.forward_media(self.mids['notes.md'], 'dev2'))['why'], 'not_found')
        self.assertEqual((await self.t.forward_media('A' * 22, 'dev2'))['why'], 'not_found')
        with patch.object(tg_media, 'UPLOAD_MAX', 10):
            self.assertEqual((await self.t.forward_media(self.mids['chart.png'], 'dev2'))['why'], 'too_big')
        self.assertEqual(self.bot.sent('sendPhoto') + self.bot.sent('sendDocument'), [])
        self.host.preferences['telegram']['forward'] = False
        self.assertEqual((await self.t.forward_media(self.mids['chart.png'], 'dev2'))['why'], 'not_configured')
        self.host.preferences['telegram']['forward'] = True
        with patch('agentj.telegram.configuration', return_value=None):
            self.assertEqual((await self.t.forward_media(self.mids['chart.png'], 'dev2'))['why'], 'not_configured')

    async def test_photo_refused_falls_back_to_a_document_and_failures_are_masked(self):
        self.bot.fail.add('sendPhoto')
        r = await self.t.forward_media(self.mids['chart.png'], 'dev3'); self.assertTrue(r['ok'])
        self.assertEqual(self.bot.sent('sendDocument')[0]['files']['document']['data'], PNG)
        self.bot.fail.add('sendDocument')
        r = await self.t.forward_media(self.mids['notes.md'], 'dev3'); self.assertEqual(r, {'ok': False, 'why': 'send_failed'})
        self.assertNotIn('test-placeholder', self.st.log_path.read_text())

    async def test_shares_the_per_device_rate_with_the_reply_forward(self):
        self.host.hist = SimpleNamespace(get=lambda i: {'id': i, 'end': 'done', 'reply': {'text': f'r{i}'}})
        for i in range(10): self.assertTrue((await self.t.forward(100 + i, 'dev4'))['ok'])
        self.assertEqual((await self.t.forward_media(self.mids['chart.png'], 'dev4'))['why'], 'rate_limited')
        self.assertTrue((await self.t.forward_media(self.mids['chart.png'], 'dev5'))['ok'])

    def test_media_get_is_a_copy_and_expires(self):
        rec = self.host.media.get(self.mids['chart.png']); rec['name'] = 'x'
        self.assertEqual(self.host.media.get(self.mids['chart.png'])['name'], 'chart.png')
        self.assertIsNone(self.host.media.get('B' * 22))
        self.host.media.clock = lambda: time.time() + media_mod.KEEP_SECS + 1
        self.assertIsNotNone(self.host.media.get(self.mids['chart.png']), 'P573: a stored copy outlives 24 h')
        self.host.media.clock = lambda: time.time() + media_mod.STORE_SECS + 1
        self.assertIsNone(self.host.media.get(self.mids['chart.png']))


class HostWire(unittest.IsolatedAsyncioTestCase):
    async def test_cap_and_mid_routing(self):
        from test_l1 import _host, _state, Phone, _ready
        with tempfile.TemporaryDirectory(prefix='p183h-', dir=TEST_TMP) as d:
            st = _state(d); sent = []; host = _host(st, sent)
            phone = Phone(st); s = _ready(host, phone); s.p33 = True
            self.assertNotIn('tgfile', host._caps(s)['caps'])
            host.telegram = SimpleNamespace(can_forward=lambda: False, forward=AsyncMock(),
                                            forward_media=AsyncMock(return_value={'ok': False, 'why': 'not_configured', 'x': 1}))
            caps = host._caps(s)['caps']
            self.assertIn('tgfile', caps); self.assertNotIn('tgfwd', caps)
            mid = 'Q' * 22

            async def ask(obj):
                sent.clear(); await host.on_p33(s, 'tg_fwd', obj)
                for _ in range(100):
                    if sent: break
                    await asyncio.sleep(0.01)
                return sent[-1][1]
            self.assertEqual(await ask({'t': 'tg_fwd', 'r': 'r1', 'mid': mid}), {'t': 'tg_fwd_res', 'r': 'r1', 'ok': False, 'why': 'not_configured'})
            host.telegram.forward_media.assert_awaited_once_with(mid, s.device); host.telegram.forward.assert_not_awaited()
            self.assertEqual(await ask({'t': 'tg_fwd', 'r': 'r2', 'mid': '../etc/passwd'}), {'t': 'tg_fwd_res', 'r': 'r2', 'ok': False, 'why': 'not_found'})
            self.assertEqual(host.telegram.forward_media.await_count, 1, 'a malformed id never reaches Telegram')


if __name__ == '__main__':
    unittest.main()
