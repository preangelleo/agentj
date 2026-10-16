"""P340 D (FEEDBACK #42): Coding Plan usage windows for OpenCode providers — parsers on recorded shapes, the probe's
endpoint / key routing with a mocked HTTP opener. No network, no real key: the fixture key is a placeholder string."""
import _hermetic  # All host runtime paths remain temporary during tests.
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentj import coding_usage as cu
from agentj import provider_runtime as pr

NOW = 1_800_000_000.0
FIXTURE_KEY = "fixture-not-a-key"


def iso(t):
    import datetime as dt
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.123456789Z")


class Endpoints(unittest.TestCase):
    def test_known_plan_hosts_only(self):
        self.assertEqual(cu.endpoint("https://api.kimi.com/coding/v1"), ("kimi", "https://api.kimi.com/coding/v1/usages"))
        self.assertEqual(cu.endpoint("https://api.kimi.ai/coding/v1/"), ("kimi", "https://api.kimi.ai/coding/v1/usages"))
        self.assertEqual(cu.endpoint("https://open.bigmodel.cn/api/coding/paas/v4"),
                         ("zhipu", "https://open.bigmodel.cn/api/monitor/usage/quota/limit"))
        self.assertEqual(cu.endpoint("https://api.z.ai/api/anthropic"), ("zhipu", "https://api.z.ai/api/monitor/usage/quota/limit"))
        self.assertEqual(cu.endpoint("https://api.minimax.cn/anthropic/v1"),
                         ("minimax", "https://api.minimaxi.com/v1/api/openplatform/coding_plan/remains"))
        # Ark / Bailian Coding Plan: no usage read with the API key — nothing to call
        for base in ("https://ark.cn-beijing.volces.com/api/coding/v3", "https://coding.dashscope.aliyuncs.com/apps/anthropic/v1",
                     "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"):
            self.assertIsNone(cu.endpoint(base), base)
        # Moonshot open platform (pay as you go), http, credentials in the URL, other ports, look-alike hosts
        for base in ("https://api.moonshot.cn/v1", "https://api.kimi.com/v1", "http://api.kimi.com/coding/v1",
                     "https://u:p@api.kimi.com/coding/v1", "https://api.kimi.com:8443/coding/v1", "https://api.kimi.com.evil.example/coding/v1",
                     None, 5, "not a url"):
            self.assertIsNone(cu.endpoint(base), base)

    def test_builtin_providers_match_opencode_ids(self):
        for pid in ("kimi-code-plan-cn", "kimi-code-plan-global", "zhipuai-coding-plan", "zai-coding-plan", "minimax-coding-plan"):
            base, env = cu.BUILTIN[pid]
            self.assertIsNotNone(cu.endpoint(base), pid)
            self.assertRegex(env, r"^[A-Z_]+_API_KEY$")


class Parsers(unittest.TestCase):
    def test_kimi_ratio_pools(self):
        data = {"usages": {"limit_5h": {"used_ratio": 0.25, "reset_time": iso(NOW + 3600)},
                           "limit_7d": {"used_ratio": 0.4, "reset_time": iso(NOW + 3 * 86400)},
                           "limit_month_total": {"used_ratio": 1.7, "reset_time": iso(NOW + 20 * 86400)}},
                "user": {"membership": {"level": "LEVEL_BASIC"}}}
        w = cu.kimi(data, NOW)
        self.assertEqual([x["window"] for x in w], ["5h", "weekly", "monthly"])
        self.assertAlmostEqual(w[0]["pct"], 25.0); self.assertAlmostEqual(w[1]["pct"], 40.0); self.assertEqual(w[2]["pct"], 100.0)
        self.assertEqual(w[0]["reset"], int(NOW + 3600)); self.assertEqual(w[1]["reset"], int(NOW + 3 * 86400))
        self.assertNotIn("used", w[0])   # a ratio has no counts

    def test_kimi_counts_shape(self):
        data = {"usage": {"limit": "2048", "used": "214", "remaining": "1834", "resetTime": iso(NOW + 2 * 86400)},
                "limits": [{"window": {"duration": 300, "timeUnit": "TIME_UNIT_MINUTE"},
                            "detail": {"limit": "200", "remaining": "61", "resetTime": iso(NOW + 1800)}}]}
        w = cu.kimi(data, NOW)
        five = next(x for x in w if x["window"] == "5h"); week = next(x for x in w if x["window"] == "weekly")
        self.assertEqual((five["used"], five["limit"]), (139.0, 200.0)); self.assertAlmostEqual(five["pct"], 69.5)
        self.assertAlmostEqual(week["pct"], 214 / 2048 * 100); self.assertEqual(week["reset"], int(NOW + 2 * 86400))

    def test_kimi_garbage_and_stale_reset(self):
        self.assertEqual(cu.kimi(None, NOW), []); self.assertEqual(cu.kimi({"usage": "x", "limits": "y"}, NOW), [])
        self.assertEqual(cu.kimi({"usages": {"limit_7d": {"used_ratio": "nan"}}}, NOW), [])
        w = cu.kimi({"usages": {"limit_7d": {"used_ratio": 0.1, "reset_time": iso(NOW - 60)}}}, NOW)
        self.assertNotIn("reset", w[0])   # a reset in the past is not a reset

    def test_zhipu_tokens_windows_not_mcp(self):
        data = {"code": 200, "success": True, "data": {"limits": [
            {"type": "TIME_LIMIT", "unit": 5, "number": 1, "usage": 1000, "currentValue": 10, "percentage": 1, "nextResetTime": int((NOW + 86400) * 1000)},
            {"type": "TOKENS_LIMIT", "unit": 6, "number": 1, "percentage": 12, "nextResetTime": int((NOW + 5 * 86400) * 1000)},
            {"type": "TOKENS_LIMIT", "unit": 3, "number": 5, "usage": 4000, "currentValue": 1000, "remaining": 3000, "percentage": 25,
             "nextResetTime": int((NOW + 7200) * 1000)}]}}
        w = cu.zhipu(data, NOW)
        self.assertEqual([x["window"] for x in w], ["5h", "weekly"])
        self.assertEqual((w[0]["used"], w[0]["limit"], w[0]["pct"]), (1000.0, 4000.0, 25.0)); self.assertEqual(w[0]["reset"], int(NOW + 7200))
        self.assertEqual(w[1]["pct"], 12.0)
        self.assertEqual(cu.zhipu({"code": 401, "msg": "bad"}, NOW), [])

    def test_minimax_remaining_percent_only(self):
        data = {"model_remains": [
            {"model_name": "MiniMax-M2", "current_interval_remaining_percent": 80, "end_time": int((NOW + 3600) * 1000),
             "current_interval_usage_count": 999, "current_weekly_remaining_percent": 95},
            {"model_name": "other", "current_interval_remaining_percent": 60, "current_weekly_remaining_percent": 101}],
            "base_resp": {"status_code": 0}}
        w = cu.minimax(data, NOW)
        self.assertEqual([x["window"] for x in w], ["5h", "weekly"])
        self.assertEqual(w[0]["pct"], 40.0); self.assertNotIn("reset", w[0])   # busiest row (60 % left), it has no end_time
        self.assertEqual(w[1]["pct"], 5.0)

    def test_parse_never_raises(self):
        for kind in ("kimi", "zhipu", "minimax", "ark", "nope"):
            for data in (None, [], "x", {"data": {"limits": [None, 1, {"type": "TOKENS_LIMIT", "unit": "3"}]}}, {"model_remains": [None]}):
                self.assertIsInstance(cu.parse(kind, data, NOW), list)


class _Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): self.close()


class Probe(unittest.TestCase):
    """provider_runtime.probe: the plan URL, the key by NAME from the service env file, never an unrelated host."""
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(); self.addCleanup(self.td.cleanup)
        self.env = Path(self.td.name) / "agentj.env"
        self.env.write_text(f"KIMI_API_KEY={FIXTURE_KEY}\nARK_CODING_PLAN_API_KEY={FIXTURE_KEY}\n")
        self.seen = []

    def opener(self, body):
        test = self
        class O:
            def open(self, req, timeout=None):
                test.seen.append((req.full_url, dict(req.header_items()), timeout))
                return _Resp(json.dumps(body).encode())
        return mock.patch.object(pr.urllib.request, "build_opener", return_value=O())

    def run_probe(self, provider, cfg, body):
        with mock.patch.object(pr, "configured", return_value=cfg), mock.patch.object(pr, "env_path", return_value=str(self.env)), \
                mock.patch.dict(os.environ, {}, clear=False), self.opener(body):
            os.environ.pop("KIMI_API_KEY", None)
            return pr.probe(provider)

    def test_builtin_kimi_provider_reads_usages_with_its_key(self):
        body = {"usages": {"limit_5h": {"used_ratio": 0.5}, "limit_7d": {"used_ratio": 0.2}}}
        w = self.run_probe("kimi-code-plan-cn", {}, body)
        self.assertEqual([x["window"] for x in w], ["5h", "weekly"])
        url, hdr, timeout = self.seen[0]
        self.assertEqual(url, "https://api.kimi.com/coding/v1/usages"); self.assertEqual(timeout, 8)
        self.assertEqual(hdr.get("Authorization"), "Bearer " + FIXTURE_KEY)
        self.assertTrue(hdr.get("User-agent", "").startswith("agentj/"))

    def test_configured_kimi_block_uses_its_own_base_and_key_name(self):
        cfg = {"my-kimi": {"options": {"baseURL": "https://api.kimi.ai/coding/v1", "apiKey": "{env:KIMI_API_KEY}"}}}
        self.run_probe("my-kimi", cfg, {"usages": {"limit_7d": {"used_ratio": 0.3}}})
        self.assertEqual(self.seen[0][0], "https://api.kimi.ai/coding/v1/usages")

    def test_ark_coding_plan_sends_nothing(self):
        cfg = {"ark-coding-plan": {"options": {"baseURL": "https://ark.cn-beijing.volces.com/api/coding/v3", "apiKey": "{env:ARK_CODING_PLAN_API_KEY}"}}}
        with mock.patch.object(pr, "public_url", return_value=None):   # its /v1/usage would need public DNS: not this test's business
            self.assertEqual(self.run_probe("ark-coding-plan", cfg, {}), [])
        self.assertEqual(self.seen, [])

    def test_no_key_no_request(self):
        self.env.write_text("")
        self.assertEqual(self.run_probe("kimi-code-plan-cn", {}, {}), [])
        self.assertEqual(self.seen, [])

    def test_unknown_builtin_no_request(self):
        self.assertEqual(self.run_probe("volcengine-coding-plan", {}, {}), [])
        self.assertEqual(self.seen, [])


if __name__ == "__main__":
    unittest.main()
