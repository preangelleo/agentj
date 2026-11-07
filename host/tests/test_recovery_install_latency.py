"""Unchanged historical P230 numeric tests; adapter/storage integration is separate."""
import asyncio
import json
import time
import unittest
from agentj import latency

def rec(recv, first, done, fresh=False, short=True, sid=None, **kw):
    return latency.make(t_recv=recv, t_first_out=first, t_done=done, harness="claude", model="claude-opus-5-5", n=1,
                        fresh=fresh, short=short, sid=sid, **kw)


class Numbers(unittest.TestCase):
    def test_nearest_rank_percentiles(self):
        self.assertIsNone(latency.percentile([], 50))
        self.assertEqual(latency.percentile([7], 95), 7)
        self.assertEqual(latency.percentile([5, 1, 3, 2, 4], 50), 3)
        self.assertEqual(latency.percentile(list(range(1, 21)), 95), 19)
        self.assertEqual(latency.percentile(list(range(1, 21)), 50), 10)
        self.assertEqual(latency.percentile(list(range(1, 101)), 95), 95)

    def test_targets_are_the_design_numbers(self):
        self.assertEqual(latency.TARGETS, {"t0": 1000, "t1": 1000, "ttft_first": 15000, "ttft_later": 8000,
                                           "done_short": 20000})
        self.assertEqual(latency.WINDOW, 20)
        self.assertTrue(latency.over("ttft_later", 8001))
        self.assertFalse(latency.over("ttft_later", 8000))
        self.assertFalse(latency.over("done", 99999), "no target, never over")

    def test_summary_last_20_turns_per_metric(self):
        rows = [rec(0, 99999, 99999)] * 5                       # older than the window: never counted
        rows += [rec(1000, 1000 + 20000, 1000 + 30000, fresh=True)]
        rows += [rec(i * 100, i * 100 + 3000 + i, i * 100 + 9000, short=i % 2 == 0) for i in range(19)]
        rows[-1] = {**rows[-1], "t0": 300, "t1": 1500}
        s = latency.summary(rows)
        self.assertEqual(s["turns"], 20)
        m = s["metrics"]
        self.assertEqual((m["ttft_first"]["n"], m["ttft_first"]["p95"], m["ttft_first"]["over"]), (1, 20000, True))
        self.assertEqual(m["ttft_later"]["n"], 19)
        self.assertEqual(m["ttft_later"]["p50"], 3009)
        self.assertFalse(m["ttft_later"]["over"])
        self.assertEqual(m["done"]["n"], 20)
        self.assertEqual(m["done_short"]["n"], 1 + 10)
        self.assertEqual((m["t0"]["n"], m["t0"]["p95"], m["t0"]["over"]), (1, 300, False))
        self.assertEqual((m["t1"]["p95"], m["t1"]["over"]), (1500, True))
        self.assertIsNone(m["done"]["target"])

    def test_turn_without_output_or_absurd_numbers_are_skipped(self):
        rows = [rec(1000, None, 5000), rec(1000, 900, 5000), rec(0, None, latency.MAX_MS + 10)]
        m = latency.summary(rows)["metrics"]
        self.assertEqual(m["ttft_later"]["n"], 0, "no output / negative → not a TTFT")
        self.assertEqual(m["done"]["n"], 2, "a sleeping laptop is not a latency")

    def test_table_both_languages_marks_over_target(self):
        rows = [rec(0, 20000, 30000, fresh=True), rec(0, 2000, 6000)]
        zh = latency.table(latency.summary(rows), "zh")
        en = latency.table(latency.summary(rows), "en")
        self.assertIn("最近 2 轮", zh)
        self.assertIn("Response speed over the last 2 turns", en)
        line = [x for x in zh.splitlines() if "第一次输出（刚启动）" in x][0]
        self.assertTrue(line.startswith("!") and "超标" in line and "20.0 s" in line and "≤ 15.0 s" in line, line)
        line = [x for x in en.splitlines() if "First output (later)" in x][0]
        self.assertTrue(line.startswith("✓") and "over target" not in line, line)
        self.assertIn("下一条消息", zh, "says where T0 / T1 come from")
        self.assertIn("还没有记录", latency.table(latency.summary([]), "zh"))
        self.assertIn("Nothing recorded yet", latency.table(latency.summary([]), "en"))

    def test_sample_and_asample(self):
        calls = []

        def ok():
            calls.append(1)
            time.sleep(0.01)
        r = latency.sample(ok)
        self.assertEqual((r["n"], r["ok"], len(calls), r["errors"]), (3, 3, 3, []))
        self.assertTrue(all(x >= 9 for x in r["ms"]) and r["p50"] is not None and r["p95"] >= r["p50"])
        n = [0]

        def flaky():
            n[0] += 1
            if n[0] == 2:
                raise TimeoutError("secret detail sk-123")
        r = latency.sample(flaky, n=4)
        self.assertEqual((r["ok"], r["errors"]), (3, ["TimeoutError"]))
        self.assertNotIn("sk-123", json.dumps(r), "only the class name")

        async def aprobe():
            await asyncio.sleep(0.01)
        r = asyncio.run(latency.asample(aprobe, n=2))
        self.assertEqual((r["n"], r["ok"]), (2, 2))
