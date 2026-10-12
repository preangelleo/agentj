"""P193 (B11): a customer-service bot reply longer than Telegram's 4096 characters goes out in order, in pieces, never cut
silently. Per-chat concurrency and the >2000-character inbound notice were already fixed (P140, test_p140_channels)."""
import _hermetic  # noqa: F401,I001
import unittest
from types import SimpleNamespace

from agentj.bots import telegram as bt
import test_p109_bots as bot_fixtures


class Segments(unittest.TestCase):
    def test_short_reply_is_one_message(self):
        self.assertEqual(bt.segments('hello'), ['hello'])
        self.assertEqual(bt.segments('x' * bt.SEGMENT), ['x' * bt.SEGMENT])
        self.assertEqual(bt.segments(''), [])

    def test_long_reply_splits_at_breaks_and_keeps_everything(self):
        paras = ['第%d段 ' % i + '字' * 900 for i in range(10)]
        text = '\n\n'.join(paras)
        parts = bt.segments(text)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= bt.SEGMENT for p in parts))
        self.assertEqual(''.join(''.join(parts).split()), ''.join(text.split()), 'nothing dropped, order kept')
        self.assertTrue(all(p.startswith('第') for p in parts), 'cuts land on paragraph breaks')

    def test_unbroken_text_is_hard_split(self):
        parts = bt.segments('y' * (bt.SEGMENT * 2 + 5))
        self.assertEqual([len(p) for p in parts], [bt.SEGMENT, bt.SEGMENT, 5])

    def test_runaway_reply_is_bounded_and_marked(self):
        parts = bt.segments('z' * (bt.SEGMENT * (bt.MAX_SEGMENTS + 3)))
        self.assertEqual(len(parts), bt.MAX_SEGMENTS)
        self.assertTrue(parts[-1].endswith('…'))


class Deliver(unittest.IsolatedAsyncioTestCase):
    setUp = bot_fixtures.Bots.setUp
    tearDown = bot_fixtures.Bots.tearDown
    bot = bot_fixtures.Bots.bot
    engine = bot_fixtures.Bots.engine
    channel = bot_fixtures.Bots.channel
    update = bot_fixtures.Bots.update

    async def test_long_reply_is_sent_as_ordered_messages(self):
        bid = self.bot(enabled=True, terms_accepted=True, telegram={'enabled': True})
        ch = self.channel(bid)
        text = 'A' * 4000 + '\n' + 'B' * 3000

        async def reply(*args):
            return {'text': text}
        ch.runtime.engine = lambda: SimpleNamespace(reply=reply)
        await ch.handle(self.update())
        self.assertEqual([m[2]['text'] for m in self.sent], ['A' * 4000, 'B' * 3000])
        self.assertTrue(all(m[1] == 'sendMessage' and m[2]['chat_id'] == 123 for m in self.sent))

    async def test_short_reply_is_still_one_message(self):
        bid = self.bot(enabled=True, terms_accepted=True, telegram={'enabled': True})
        ch = self.channel(bid)
        await ch.handle(self.update())
        self.assertEqual(len(self.sent), 1)


if __name__ == '__main__':
    unittest.main()
