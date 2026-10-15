"""P286: up to five quotes in one message, from the same or different reply cards.

The phone sends the first quote as `reply_to` / `excerpt` (every host knows those) and quotes 2…5 as `quote_more`
(PROTOCOL §10.2 / §10.6); a host announcing `mq5` builds one numbered block per quote from its OWN history — 「引用 i/n」,
the page id (the Agent's own reply), time (date when not today), the words, and for an excerpt the reply's opening line — and stores
`src.quote` (the first, older phones draw it) with `src.quote.more` (the rest, in order). One quote stays byte-identical
to the old single block. Shapes are strict: `quote_more` only with `reply_to`, 1–4 entries, int ids; every excerpt counts
toward the 20 000 limit; any id outside the epoch = `reply_unknown`.
"""
import _hermetic  # noqa: F401,I001
import os
import pathlib
import sys
import time
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import compose, wire  # noqa: E402
from test_p33_chain import _Chain  # noqa: E402

NOW = int(time.time() * 1000)


def turn(i, reply, ts=NOW, k="agent"):
    return {"id": i, "ts": ts, "src": {"k": k}, "reply": {"text": reply}}


class Block(unittest.TestCase):
    def test_one_quote_is_the_old_block(self):
        t = turn(3, "第一行\n第二行")
        self.assertEqual(compose.quotes_block([(t, None)], "zh"), compose.quote_block(t, None, "zh"))
        self.assertEqual(compose.quotes_block([(t, "第二行")], "en"), compose.quote_block(t, "第二行", "en"))

    def test_numbered_blocks_in_order_with_source_and_context(self):
        a = turn(3, "## 结论\n先说结论：可以上线。\n细节在后面。")
        b = turn(9, "另一条回复", ts=NOW - 3 * 86400_000)
        out = compose.quotes_block([(a, "细节在后面。"), (b, None), (a, "先说结论")], "zh")
        blocks = out.split("\n\n")
        self.assertEqual(len(blocks), 3)
        self.assertTrue(blocks[0].startswith("【引用 1/3 · 你的回复 #3 · "), blocks[0])
        self.assertIn("> 细节在后面。\n> （摘录）\n> （选自这条回复，它的开头：结论）", blocks[0])
        self.assertRegex(blocks[1].split("\n")[0], r"^【引用 2/3 · 你的回复 #9 · \d\d-\d\d \d\d:\d\d】$",
                         "another day → the date too")
        self.assertIn("> 另一条回复", blocks[1])
        self.assertNotIn("（摘录）", blocks[1], "a whole page is not an excerpt")
        self.assertRegex(blocks[2].split("\n")[0], r"^【引用 3/3 · 你的回复 #3 · \d\d:\d\d】$", "today → time only")

    def test_english(self):
        out = compose.quotes_block([(turn(1, "Hello there"), "there"), (turn(2, "Second"), None)], "en")
        self.assertIn("[Quote 1/2 · your reply #1 · ", out)
        self.assertIn("> (selected from that reply, which begins: Hello there)", out)
        self.assertIn("[Quote 2/2 · your reply #2 · ", out)


class ClaudeMultiQuote(_Chain):
    KIND = "claude"

    def test_five_quotes_reach_the_agent_and_the_page(self):
        async def script(c):
            self.assertIn(compose.CAP_MQ, c["host"]._caps(c["s"])["caps"])
            _, t1 = await c["said"]("第一条")
            _, t2 = await c["said"]("第二条")
            more = [{"id": t2["id"], "ex": "第二"}, {"id": t1["id"]}, {"id": t2["id"]}, {"id": t1["id"], "ex": "一条"}]
            r, t = await c["said"]("这五段一起看", reply_to=t1["id"], excerpt="第一", quote_more=more)
            self.assertTrue(r["ok"], r)
            rep = t["reply"]["text"]
            for i in range(1, 6):
                self.assertIn(f"【引用 {i}/5 · 你的回复 #", rep)
            self.assertLess(rep.index("【引用 1/5"), rep.index("【引用 5/5"), "the order the owner picked")
            self.assertIn("这五段一起看", rep)
            q = t["src"]["quote"]
            self.assertEqual((q["id"], q["ex"]), (t1["id"], True), "src.quote = the first (older phones)")
            self.assertEqual([(m["id"], m["ex"]) for m in q["more"]],
                             [(t2["id"], True), (t1["id"], False), (t2["id"], False), (t1["id"], True)])
            # two quotes from the same page, one whole page: still one block each
            r, t = await c["said"]("两段", reply_to=t2["id"], quote_more=[{"id": t2["id"], "ex": "二条"}])
            self.assertTrue(r["ok"], r)
            self.assertIn("【引用 2/2", t["reply"]["text"])
            # refusals
            for kw, why in (
                    ({"quote_more": [{"id": t1["id"]}]}, "shape"),                                     # without reply_to
                    ({"reply_to": t1["id"], "quote_more": []}, "shape"),
                    ({"reply_to": t1["id"], "quote_more": [{"id": t1["id"]}] * 5}, "shape"),         # 6 quotes in all
                    ({"reply_to": t1["id"], "quote_more": [{"id": "1"}]}, "shape"),
                    ({"reply_to": t1["id"], "quote_more": [{"id": t1["id"], "ex": 5}]}, "shape"),
                    ({"reply_to": t1["id"], "quote_more": {"id": t1["id"]}}, "shape"),
                    ({"reply_to": t1["id"], "quote_more": [{"id": 999999}]}, "reply_unknown"),
                    ({"reply_to": t1["id"], "quote_more": [{"id": t1["id"], "ex": "a\x07"}]}, "shape"),
                    ({"reply_to": t1["id"], "quote_more": [{"id": t1["id"], "ex": "x" * 2001}]}, "too_long"),
                    ({"text": "x" * 14000, "reply_to": t1["id"], "excerpt": "y" * 2000,
                      "quote_more": [{"id": t1["id"], "ex": "z" * 2000}] * 3}, "too_long")):  # 14 000 + 4 × 2 000 > 20 000
                sid = wire.b64u(os.urandom(16))
                await c["host"]._app(c["s"], {"t": "say", "sid": sid, "ts": 0, "text": kw.pop("text", "看"), **kw})
                await c["wait"](lambda: c["res"](sid) is not None)
                self.assertEqual((c["res"](sid)["ok"], c["res"](sid)["why"]), (False, why), kw)
            await c["idle"]()
        self.run_chain(script)


if __name__ == "__main__":
    unittest.main()
