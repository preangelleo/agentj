"""P175: every Codex failure gets a line the owner can act on (not 「Codex 没能完成这条请求，查看详情后重试」), and Codex users
learn before the first turn whether OpenAI is reachable from their computer.

Samples are Codex CLI 0.162.1 wording: its error Display strings and app-server `codexErrorInfo` names, read from the binary
(`strings`), plus the HTTP bodies OpenAI returns (unsupported_country_region_territory). Request ids / urls are redacted."""
import _hermetic  # noqa: F401
import asyncio
import io
import os
import pathlib
import sys
import unittest
import urllib.error
from types import SimpleNamespace
from unittest import mock
from unittest.mock import Mock
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import chatgpt_reach, doctor, harness
from agentj.agent import CODEX_FEEDBACK, codex_failure
from agentj.agent_codex import CodexAgent

REGION_BODY = ('{"error":{"code":"unsupported_country_region_territory","message":"Country, region, or territory not '
               'supported","param":null,"type":"request_forbidden"}}')
SAMPLES = {
    "region": [
        f"unexpected status 403 Forbidden: {REGION_BODY}, url: https://chatgpt.com/backend-api/codex/responses",
        f"exceeded retry limit, last status: 403 Forbidden, request id: <id> {REGION_BODY}",
        "Codex 这一轮没有正常完成：Country, region, or territory not supported",
        "ChatGPT is not available in your country.",
    ],
    "usage": [
        "You've hit your usage limit. Upgrade to Plus to continue using Codex (https://chatgpt.com/explore/plus), or try again at 3:05 PM.",
        "You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit https://chatgpt.com/settings/usage to purchase more credits or try again in 2 days 3 hours.",
        "You've hit your usage limit. To get more access now, send a request to your admin or try again at 9:00 AM.",
        "Codex 这一轮没有正常完成：unexpected error\n\"usageLimitExceeded\"",
    ],
    "rate": [   # P309: a bare 429 / "rate limit" is a momentary throttle, not a used-up allowance
        "exceeded retry limit, last status: 429 Too Many Requests, request id: <id>",
        "rate limit exceeded",
    ],
    "quota": ["Quota exceeded. Check your plan and billing details.",
              'unexpected status 429 Too Many Requests: {"error":{"code":"insufficient_quota"}}'],
    "plan": ["To use Codex with your ChatGPT plan, upgrade to Plus: https://chatgpt.com/explore/plus.",
             "Codex 这一轮没有正常完成：usage not included"],
    "workspace": ["Codex is not enabled for your workspace. Contact your workspace administrator to request access to Codex.",
                  "Login is restricted to workspace(s) <ws>",
                  "External auth must use one of workspace(s) <ws>"],
    "auth": ["HTTP 401", "Unauthorized", "OAuth refresh token was rejected: invalid_grant"],
    "context": ["Codex ran out of room in the model's context window. Start a new thread or clear earlier history before retrying.",
                "Codex 这一轮没有正常完成：bad request\n\"contextWindowExceeded\"",
                '{"error":{"code":"context_length_exceeded"}}'],
    "policy": ["This request has been flagged for possible cybersecurity risk.",
               "Incomplete response returned, reason: content_filter"],
    "server": ["Selected model is at capacity. Please try a different model.",
               "unexpected status 500 Internal Server Error: <body>",
               "exceeded retry limit, last status: 503 Service Unavailable, request id: <id>",
               "Codex 这一轮没有正常完成：x\n\"serverOverloaded\""],
    "sandbox": ["sandbox denied exec error, exit code: 1, stdout: , stderr: bwrap: Creating new namespace failed",
                "Codex 这一轮没有正常完成：x\n\"sandboxError\""],
    "access": ["unexpected status 403 Forbidden: <html><title>Just a moment...</title></html>"],
    "network": ["stream disconnected before completion: error sending request for url (https://chatgpt.com/backend-api/codex/responses)",
                'Codex 这一轮没有正常完成：x\n{"httpConnectionFailed": {"httpStatusCode": null}}',
                "workspace routing discovery failed", "connection timed out", "DNS lookup failed"],
    "crash": ["Codex 退出了（137）", "Codex 退出了（1）：thread 'main' panicked at core/src/codex.rs:1:1",
              "Codex（app-server）没有启动（TimeoutError）。"],
    "missing": ["这台Agent J 主机上没找到 Codex（codex 命令）。装好并登录后再试。"],
    "other": ["something nobody has seen before", ""],
}
VAGUE = ("Codex 没能完成这条请求，查看详情后重试。", "Codex could not finish this request. Check the details and retry.")


class Classify(unittest.TestCase):
    def test_every_sample_has_its_class_and_an_actionable_line_in_both_languages(self):
        for kind, raws in SAMPLES.items():
            for raw in raws:
                for lang in ("zh", "en"):
                    with self.subTest(kind=kind, raw=raw[:50], lang=lang):
                        got, text, _ = codex_failure(raw, None, lang)
                        self.assertEqual(got, kind)
                        self.assertNotIn(VAGUE[0], text); self.assertNotIn(VAGUE[1], text)
                        first = text.split("\n\n")[0]
                        self.assertGreater(len(first), 20)
                        if lang == "zh":
                            self.assertRegex(first, r"[一-鿿]")
                        else:
                            self.assertNotRegex(first, r"[一-鿿]")
                        if raw:
                            self.assertIn("```text", text)

    def test_region_points_to_proxy_setting_and_local_models(self):
        zh = codex_failure(SAMPLES["region"][0], None, "zh")[1]
        self.assertIn("你所在地区连不上 ChatGPT", zh)
        self.assertIn("agentj config set proxy.https", zh)
        self.assertIn("OpenCode + 国内模型", zh); self.assertIn("Claude Code", zh)
        en = codex_failure(SAMPLES["region"][0], None, "en")[1]
        self.assertIn("proxy/VPN", en); self.assertIn("agentj agent opencode", en)

    def test_usage_quotes_reset_time_only_when_codex_gave_one(self):
        self.assertIn("3:05 PM", codex_failure(SAMPLES["usage"][0], None, "zh")[1].split("\n\n")[0])
        self.assertIn("at 3:05 PM", codex_failure(SAMPLES["usage"][0], None, "en")[1].split("\n\n")[0])
        self.assertIn("in 2 days 3 hours", codex_failure(SAMPLES["usage"][1], None, "en")[1].split("\n\n")[0])
        line = codex_failure(SAMPLES["usage"][3], None, "zh")[1].split("\n\n")[0]
        self.assertIn("没给恢复时间", line); self.assertIn("换个更省的模型", line)

    def test_other_says_what_to_do_next(self):
        for lang in ("zh", "en"):
            line = codex_failure("weird", None, lang)[1].split("\n\n")[0]
            self.assertIn(CODEX_FEEDBACK, line)
            self.assertIn("截图" if lang == "zh" else "screenshot", line)
        self.assertEqual(CODEX_FEEDBACK, "feedback@agentj.app")  # P341 (#25)

    def test_crash_names_the_exit_code(self):
        self.assertIn("137", codex_failure("Codex 退出了（137）", None)[1])
        self.assertIn("内存不足", codex_failure("Codex 退出了（137）", None)[1])
        self.assertIn("agentj agent restart", codex_failure("Codex 退出了（1）", None, "en")[1])

    def test_model_rejection_is_unchanged(self):
        raw = "The 'gpt-6.1-sol' model is not supported when using Codex with a ChatGPT account."
        self.assertEqual(codex_failure(raw, None)[0::2], ("model", "gpt-6.1-sol"))


class TurnFailedCarriesErrorInfo(unittest.TestCase):
    def test_codex_error_info_reaches_the_classifier(self):
        a = object.__new__(CodexAgent)
        a.cfg = {"model": "gpt-6"}; a.thread = {}; a.models_cache = []; a.unavailable_models = set()
        a.halting = False; a.tid = "t"; a.turn_done = asyncio.Event(); a.meter = Mock(); a.tasks = set()
        a.host = SimpleNamespace(lang="zh", turn_failed=Mock(), agent_notice=Mock(), models_changed=Mock())
        a._on_note("turn/completed", {"threadId": "t", "turn": {"status": "failed", "error": {
            "message": "unexpected error", "codexErrorInfo": "usageLimitExceeded",
            "additionalDetails": "try again at 4:00 PM"}}})
        text = a.host.agent_notice.call_args.args[0]
        self.assertIn("用量到上限", text); self.assertIn("4:00 PM", text)


class _Resp:
    def __init__(self, status): self.status = status
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _http_error(code, body=""):
    return urllib.error.HTTPError(chatgpt_reach.URL, code, "x", {}, io.BytesIO(body.encode()))


class Reach(unittest.TestCase):
    def run_probe(self, outcome):
        opener = Mock()
        if isinstance(outcome, BaseException):
            opener.open.side_effect = outcome
        else:
            opener.open.return_value = outcome
        with mock.patch.dict(os.environ, {"AGENTJ_REACH_PROBE": "on"}), \
                mock.patch.object(chatgpt_reach, "_opener", return_value=opener):
            return chatgpt_reach.probe({})

    def test_classes(self):
        self.assertEqual(self.run_probe(_http_error(401, '{"error":{"code":"invalid_api_key"}}')), "ok")
        self.assertEqual(self.run_probe(_http_error(403, REGION_BODY)), "region")
        self.assertEqual(self.run_probe(urllib.error.URLError("timed out")), "unreachable")
        self.assertEqual(self.run_probe(TimeoutError()), "unreachable")
        self.assertEqual(self.run_probe(_Resp(200)), "ok")
        self.assertEqual(self.run_probe(_http_error(502)), "unknown")

    def test_tests_never_touch_the_network(self):
        self.assertEqual(chatgpt_reach.probe(), "skipped")

    def test_opener_uses_the_given_proxy(self):
        o = chatgpt_reach._opener({"HTTPS_PROXY": "http://127.0.0.1:7890"})
        ph = [h for h in o.handlers if isinstance(h, urllib.request.ProxyHandler)][0]
        self.assertEqual(ph.proxies.get("https"), "http://127.0.0.1:7890")

    def test_detect_adds_advice_for_codex_only(self):
        d = {"harnesses": [{"name": "codex", "installed": True}], "usable": ["codex"], "decision": "use:codex"}
        with mock.patch.object(chatgpt_reach, "probe", return_value="unreachable"):
            out = harness.reach(dict(d))
        self.assertEqual((out["chatgpt"], out["decision"]), ("unreachable", "use:codex"))
        self.assertIn("OpenCode", out["advice"])
        none = harness.reach({"harnesses": [{"name": "codex", "installed": False}], "usable": [], "decision": "none"})
        self.assertNotIn("chatgpt", none)

    def test_doctor_row_warns_with_advice(self):
        st = Mock(); st.exists.return_value = True; st.agent_config.return_value = {"kind": "codex"}
        with mock.patch.object(chatgpt_reach, "probe", return_value="region"):
            row = doctor.check_chatgpt(st)
        self.assertEqual(row["status"], doctor.WARN)
        self.assertIn("OpenCode", row["hint"])
        st.agent_config.return_value = {"kind": "claude"}
        self.assertIsNone(doctor.check_chatgpt(st))
        st.agent_config.return_value = {"kind": "codex"}
        self.assertIsNone(doctor.check_chatgpt(st, offline=True))


if __name__ == "__main__":
    unittest.main()
