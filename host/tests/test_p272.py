"""P272 file-card document receipt/latency regressions; fake Telegram only."""
import _hermetic
import asyncio
import json
import time
from unittest.mock import patch
from test_p183_tg_file import FileForward
from test_p118_telegram import _Base
from agentj import telegram

class FileReceipt(FileForward):
    setUp = FileForward.setUp

    async def test_document_once_and_stage_timings_without_payload(self):
        original=telegram.upload
        def slow(*a,**kw):
            time.sleep(.15)
            return original(*a,**kw)
        with patch.object(telegram,'upload',side_effect=slow):
            task=asyncio.create_task(self.t.forward_media(self.mids['chart.png'],'phone'))
            await asyncio.sleep(.04)
            self.assertFalse(task.done())
            self.assertEqual((await self.t.forward_media(self.mids['chart.png'],'phone'))['why'],'in_flight')
            self.assertTrue((await task)['ok'])
        self.assertEqual(len(self.bot.sent('sendDocument')),1)
        self.assertEqual(len(self.bot.sent('sendPhoto')),0)
        log=self.st.log_path.read_text()
        self.assertIn('telegram_forward_stage',log);self.assertIn('upload',log);self.assertIn('read',log)
        self.assertNotIn('chart.png',log);self.assertNotIn('test-placeholder',log)

    async def test_secret_and_changed_file_never_upload(self):
        (self.work/'out'/'api.key').write_text('fixture-secret')
        items,skips=self.host.media.scan('[secret](out/api.key)',12)
        self.assertFalse(items);self.assertTrue(skips)
        with patch.object(telegram,'upload') as upload:
            self.assertEqual((await self.t.forward_media('invalid','phone'))['why'],'not_found')
            (self.work/'out'/'notes.md').write_text('changed')
            # P573: the card's scan-time copy is what forwards (the file card persists); without the copy → not_found
            import shutil
            shutil.rmtree(self.host.media.store,ignore_errors=True)
            self.assertEqual((await self.t.forward_media(self.mids['notes.md'],'phone'))['why'],'not_found')
            upload.assert_not_called()

    async def test_failure_does_not_retry_or_claim_sent(self):
        with patch.object(telegram,'upload',side_effect=ValueError('safe')) as upload:
            self.assertEqual((await self.t.forward_media(self.mids['chart.png'],'phone'))['why'],'send_failed')
            self.assertEqual(upload.call_count,1)
        self.assertNotIn(('m',self.mids['chart.png']),self.t.fwd_sent)
