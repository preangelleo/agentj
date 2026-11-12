"""0311: the shared Claude handoff is read in the first postcompact turn, not a stale acknowledgement."""
import _hermetic  # noqa: F401
import asyncio
import unittest
from unittest.mock import patch

from agentj import handoff
from test_p151_handoff import Handoff


class CompactReread(Handoff):
    async def test_preparation_does_not_request_a_fixed_reply(self):
        for language in ("zh", "en"):
            text = handoff.prep_text(language)
            self.assertNotIn('只回一句', text)
            self.assertNotIn('"Handoff written"', text)

    async def test_recovery_first_reply_names_in_progress_item(self):
        self.write_handoff(text="# 交接\n- 在跑：0.30.10 发布检查\n")
        await self.ev("PreCompact", trigger="manual")
        await self.ev("SessionStart", source="compact")
        await self.ev("PostCompact", trigger="manual")
        await self.settle()
        self.assertEqual(len(self.sent), 1, "recover without waiting for a new external message")
        self.assertIn("在跑", self.sent[0][0])
        self.assertIn("第一条回复", self.sent[0][0])
        self.assertNotIn("交接写好了", self.sent[0][0])

    async def test_prep_waits_for_reply_not_just_file_write(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        loop = asyncio.get_running_loop()

        def send(_session, text, _source):
            if "马上要压缩上下文" in text:
                self.write_handoff(text="# 交接\n- 在跑：发布检查\n")
                loop.call_soon_threadsafe(entered.set)
                async def finish():
                    await release.wait()
                    self.a._finish_stop("准备好了")
                loop.call_soon_threadsafe(lambda: asyncio.create_task(finish()))

        with patch("agentj.shared.claude_send", side_effect=send):
            task = asyncio.create_task(self.a._handoff_prep())
            await asyncio.wait_for(entered.wait(), 2)
            await asyncio.sleep(0.02)
            self.assertFalse(task.done(), "file write alone cannot initiate compaction")
            release.set()
            self.assertTrue(await asyncio.wait_for(task, 2))
