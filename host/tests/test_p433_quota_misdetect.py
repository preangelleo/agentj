"""P433 (#196, 2026-10-21): the phone showed 5H 14% / WEEKLY 39% yet said 「Claude Code 额度用完了，预计 20:01 自动发『继续』」
while the workflow bar said 「预计 17:01 自动重跑」. Only a confirmed usage limit is 「额度用完」; a per-model cap names the model
and the one to switch to; one reset per harness + limit for every entry. Correction (12:17, the owner's Claude page: This week
100 % used, 「Reset for free」 offered): the limit was real and the phone's readings were stale — a confirmed limit always wins,
contradicting readings are marked 「可能过期」, a refused window reads 100 %, every reading carries when it was measured, and
Claude's notice points at the free reset (never pressed). Samples are Claude Code 2.1.296's own wording (read from its binary: `at(limitName, …)`,
the toe{} limit names, the 429 / 529 / context / spend lines) and Codex 0.162's.
"""
import _hermetic  # noqa: F401,I001
import pathlib
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import agent as agents, quota_resume as qr  # noqa: E402
from agentj import codex_perm  # noqa: E402
from unittest import mock  # noqa: E402
from test_p314_quota_resume import Base, bare_codex  # noqa: E402

NOW = 1_792_000_000.0
H = 3600
FULL = {"h5": {"pct": 100, "reset": NOW + H}, "week": {"pct": 60, "reset": NOW + 3 * 86400}}
ROOMY = {"h5": {"pct": 14, "reset": NOW + 3 * H}, "week": {"pct": 39, "reset": NOW + 4 * 86400}}   # the #196 screenshot

LIMITS = {   # Claude Code's own line → the limit it names
    "You've hit your session limit · resets 8pm (Asia/Shanghai)": "session",
    "You've hit your weekly limit · resets Oct 24, 8pm · progress saved": "weekly",
    "You've hit your Opus limit · resets 8pm": "opus",
    "You've hit your Sonnet limit · resets Oct 24, 9am": "sonnet",
    "You've hit your Fable limit · resets Oct 24, 9am": "fable",
    "Fable limit reached · continuing on Opus 5.5 uses usage credits, and the prompt to confirm went unanswered": "fable",
    "You've hit your fast limit": "fast",
    "You've hit your usage limit · resets 8pm": "usage",
    "You've hit your limit · resets 3pm (Asia/Tokyo)": "",
    "Claude AI usage limit reached|1792003600": "",
    "5-hour limit reached ∙ resets 3pm": "session",
    "Opus weekly limit reached ∙ resets Oct 20, 9am": "opus",
}
NOT_LIMITS = (
    'API Error: 429 {"type":"error","error":{"type":"rate_limit_error","message":"This request would exceed your rate limit"}}',
    "Server is temporarily limiting requests (not your usage limit) · Rate limited",
    'API Error: 529 {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}',
    "Repeated 529 Overloaded errors",
    "Opus is experiencing high load, please use /model to switch to Sonnet",
    "Context limit reached · /compact or /clear to continue",
    "API Error: Connection error.",
    "Invalid API key · Please run /login",
    "OAuth token has expired. Please obtain a new token or refresh your existing token.",
    "You've hit your monthly spend limit · resets Nov 1",
    "Credit balance is too low",
    "好的，总结一下：when you hit your limit, Claude Code shows a reset time · resets 8pm",
    "The session limit reached its cap in the docs example — nothing to worry about.",
    "", None,
)


class Classify(unittest.TestCase):
    def test_real_limit_lines(self):
        for text, name in LIMITS.items():
            self.assertEqual(qr.limit_of(text), name, text)
            self.assertTrue(qr.claude_limit(text), text)

    def test_not_a_usage_limit(self):
        for text in NOT_LIMITS:
            self.assertIsNone(qr.limit_of(text), text)
            self.assertFalse(qr.claude_limit(text), text)
            self.assertIsNone(qr.judge("claude", text, None, FULL, now=NOW), text)

    def test_rate_event_names_the_window(self):
        rej = lambda t: {"status": "rejected", "rateLimitType": t, "resetsAt": NOW + 2 * H}  # noqa: E731
        self.assertEqual(qr.limit_of("You've hit your weekly limit · resets 9am", rej("seven_day_sonnet")), "sonnet",
                         "Pro words a Sonnet cap as 「weekly limit」")
        self.assertEqual(qr.limit_of("You've hit your limit", rej("seven_day_opus")), "opus")
        self.assertEqual(qr.limit_of("You've hit your limit", {"status": "allowed", "rateLimitType": "seven_day_opus"}), "")
        self.assertIsNone(qr.limit_of("API Error: 500", rej("five_hour")), "a reject alone is not this turn's words")


class Judge(unittest.TestCase):
    def test_used_up_window_with_its_reset(self):
        v = qr.judge("claude", "You've hit your session limit · resets 8pm", None, FULL, now=NOW)
        self.assertEqual((v["limit"], v.get("model")), ("session", None))
        self.assertNotIn("stale", v)
        self.assertTrue(NOW < v["at"] <= NOW + 86400)

    def test_a_confirmed_limit_wins_over_roomy_readings(self):
        """#196 correction: the readings were out of date, not the limit — still 「额度用完」 with its reset."""
        for text, stale in (("You've hit your limit|1792003600", {"h5": 14.0, "week": 39.0}),
                            ("Claude AI usage limit reached|1792003600", {"h5": 14.0, "week": 39.0}),
                            ("You've hit your session limit|1792003600", {"h5": 14.0}),
                            ("You've hit your weekly limit|1792003600", {"week": 39.0})):
            v = qr.judge("claude", text, None, ROOMY, now=NOW)
            self.assertEqual(v["at"], 1792003600.0, text)
            self.assertEqual(v["stale"], stale, text)
        v = qr.judge("claude", "You've hit your session limit · resets 8pm", None,
                     {"h5": {"pct": 92, "reset": NOW + H}}, now=NOW)
        self.assertNotIn("stale", v, "only clearly-not-full windows contradict")
        old = {"h5": {"pct": 14, "reset": NOW - 60}, "week": {"pct": 39, "reset": NOW - 60}}
        self.assertNotIn("stale", qr.judge("claude", "You've hit your limit|1792003600", None, old, now=NOW),
                         "readings of a window that already reset say nothing")
        rate = {"status": "rejected", "rateLimitType": "seven_day", "resetsAt": NOW + 5 * H}
        v = qr.judge("claude", "You've hit your weekly limit · resets 5pm", rate, ROOMY, now=NOW)
        self.assertEqual((v["limit"], v["at"], v["stale"]), ("weekly", NOW + 5 * H, {"week": 39.0}),
                         "the harness's reset, never one computed from a stale reading")

    def test_model_limit_is_not_contradicted_and_keeps_its_own_reset(self):
        rate = {"status": "rejected", "rateLimitType": "seven_day_opus", "resetsAt": NOW + 10 * H}
        v = qr.judge("claude", "You've hit your Opus limit · resets 8pm", rate, ROOMY, now=NOW)
        self.assertEqual((v["limit"], v["model"], v["at"]), ("opus", "opus", NOW + 10 * H))
        v = qr.judge("claude", "You've hit your Opus limit|1792036000", None, ROOMY, now=NOW)
        self.assertEqual((v["model"], v["at"]), ("opus", 1792036000.0), "the words alone: the harness's own time")

    def test_codex_usage_against_roomy_meters(self):
        v = qr.judge("codex", "You've hit your usage limit. try again in 3 hours.", None, ROOMY, now=NOW)
        self.assertEqual((v["at"], v["stale"]), (NOW + 3 * H, {"h5": 14.0, "week": 39.0}))
        v = qr.judge("codex", "You've hit your usage limit. try again in 3 hours.", None,
                     {"h5": {"pct": 100, "reset": NOW + H}}, now=NOW)
        self.assertEqual(v["at"], NOW + 3 * H)

    def test_texts(self):
        self.assertIn("Opus 专属周限额", qr.limit_label("claude", "opus"))
        self.assertIn("换成 Sonnet", qr.switch_advice("claude", "opus"))
        self.assertIn("/model sonnet", qr.switch_advice("claude", "opus"))
        self.assertIn("换成 Opus", qr.switch_advice("claude", "fable"))
        self.assertIn("/fast", qr.switch_advice("claude", "fast"))
        self.assertIn("pick Sonnet", qr.switch_advice("claude", "opus", "en"))
        self.assertEqual(qr.switch_advice("claude", "session"), "")
        self.assertEqual(qr.limit_label("claude", ""), "Claude Code 额度")
        t = qr.stale_note({"h5": 14.0, "week": 39.0})
        self.assertIn("5H 14%", t)
        self.assertIn("WEEKLY 39%", t)
        self.assertIn("已过期", t)
        self.assertIn("可能过期", t)
        self.assertIn("out of date", qr.stale_note({"h5": 14.0}, "en"))
        self.assertEqual(qr.stale_note(None), "")

    def test_free_reset_hint(self):
        import tempfile
        from agentj import quota
        with tempfile.TemporaryDirectory() as d:
            h = qr.reset_hint("claude", "weekly", d)
            self.assertIn("Claude 设置 → Usage", h)
            self.assertIn("Reset for free", h)
            self.assertIn("不会替你点", h)
            self.assertIn("never presses it", qr.reset_hint("claude", "", d, "en"))
            quota.add_reset(d, "claude", time.time() + 2 * 86400)
            self.assertIn("你记录过一次可用的免费重置", qr.reset_hint("claude", "weekly", d))
            self.assertIn("recorded free reset", qr.reset_hint("claude", "opus", d, "en"))
        self.assertEqual(qr.reset_hint("codex", "", None), "", "Codex has no reset button")
        self.assertEqual(qr.reset_hint("claude", "fast", None), "")


class Chat(Base):
    def claude(self, meters):
        self.host.meter_state.update(meters)
        a = agents.ClaudeAgent(self.host, {"kind": "claude", "dir": "/tmp", "model": None})
        a.halting, a.capture, a.turn_done = False, None, None
        self.host.agent.kind = "claude"
        return a

    def test_the_196_case_limit_wins_readings_marked_stale(self):
        now = time.time()
        a = self.claude({"h5": {"pct": 14, "reset": now + 3 * H, "at": now - 5 * H},
                         "week": {"pct": 39, "reset": now + 4 * 86400, "at": now - 5 * H}})
        a.on_event({"type": "result", "is_error": True, "result": "You've hit your limit|%d" % int(now + 7 * H)})
        rec = self.host.quota_resume.items["main"]
        self.assertAlmostEqual(rec["at"], now + 7 * H, delta=1)
        note = self.host.notes[-1]
        self.assertIn("Claude Code 额度用完了", note)
        self.assertIn("5H 14%、WEEKLY 39%", note)
        self.assertIn("可能过期", note)
        self.assertIn("Reset for free", note)
        m = self.host.meter_state
        self.assertTrue(m["h5"]["stale"] and m["week"]["stale"], "the phone marks both readings 「可能过期」")
        self.assertEqual(m["week"]["pct"], 39, "the number stays, only marked")

    def test_rejected_window_reads_full_and_every_reading_is_timed(self):
        now = time.time()
        a = self.claude({})
        a.on_event({"type": "rate_limit_event", "rate_limit_info": {
            "status": "rejected", "rateLimitType": "seven_day", "resetsAt": int(now + 7 * H),
            "unifiedWindows": {"five_hour": {"utilization": 0.14, "resetsAt": int(now + 3 * H)},
                               "seven_day": {"utilization": 0.39, "resetsAt": int(now + 7 * H)}}}})
        m = self.host.meter_state
        self.assertEqual((m["week"]["pct"], m["week"]["reset"]), (100.0, int(now + 7 * H)),
                         "Claude Code's cached 39 % gives way to its own refusal of that window")
        self.assertEqual(m["h5"]["pct"], 14.0)
        for k in ("h5", "week"):
            self.assertAlmostEqual(m[k]["at"], now, delta=2, msg="when it was measured")
        a.on_event({"type": "result", "is_error": True, "result": "You've hit your weekly limit · resets 5pm"})
        self.assertNotIn("stale", m["week"], "the corrected reading agrees with the limit")
        self.assertAlmostEqual(self.host.quota_resume.items["main"]["at"], int(now + 7 * H), delta=1)

    def test_opus_cap_names_the_model_and_the_switch(self):
        now = time.time()
        a = self.claude({"h5": {"pct": 14, "reset": now + 3 * H}, "week": {"pct": 39, "reset": now + 4 * 86400}})
        a.on_event({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "rateLimitType": "seven_day_opus",
                                                                     "resetsAt": now + 6 * H}})
        a.on_event({"type": "result", "is_error": True, "result": "You've hit your Opus limit · resets 8pm"})
        rec = self.host.quota_resume.items["main"]
        self.assertEqual((rec["at"], rec["limit"]), (now + 6 * H, "opus"))
        note = self.host.notes[-1]
        self.assertIn("Claude Code 的 Opus 专属周限额用完了", note)
        self.assertIn("换成 Sonnet", note)
        self.assertEqual(self.host.quota_resume.view()[0]["limit"], "opus", "the phone's bar names it too")

    def test_stale_reject_of_an_earlier_turn_is_not_this_reset(self):
        now = time.time()
        a = self.claude({"h5": {"pct": 100, "reset": now + 2 * H}})
        a.on_event({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": now + 9 * H}})
        a.turn_rate = None                       # a new turn began (ClaudeAgent.turn resets it)
        a.on_event({"type": "result", "is_error": True, "result": "You've hit your session limit|%d" % int(now + 2 * H)})
        self.assertAlmostEqual(self.host.quota_resume.items["main"]["at"], now + 2 * H, delta=1)

    def test_one_reset_per_harness_and_limit(self):
        r = self.host.quota_resume
        now = time.time()
        r.hit("task:w", "claude", now + 3 * H, "视频号评论回复草稿", limit="opus")
        r.hit("main", "claude", now + 6 * H, limit="opus")
        self.assertEqual({k: v["at"] for k, v in r.items.items()}, {"main": now + 6 * H, "task:w": now + 6 * H},
                         "the bar and the chat never disagree (17:01 vs 20:01)")
        r.hit("task:x", "claude", None, "周报", limit="opus")
        self.assertEqual(r.items["task:x"]["at"], now + 6 * H, "a hit without a time takes the one already known")
        r.hit("task:y", "claude", now + H, "另一个", limit="session")
        self.assertEqual(r.items["main"]["at"], now + 6 * H, "another limit keeps its own reset")


class CodexChat(unittest.TestCase):
    def test_usage_limit_against_roomy_meters_still_used_up(self):
        p = mock.patch.object(codex_perm, "provider", return_value={"id": "openai", "host": None, "relay": False})
        p.start(); self.addCleanup(p.stop)
        now = time.time()
        self.r = Mock()
        self.host = SimpleNamespace(lang="zh", turn_failed=Mock(), agent_notice=Mock(), models_changed=Mock(),
                                    st=SimpleNamespace(log=Mock(), root=pathlib.Path("/nonexistent")), quota_resume=self.r,
                                    meter_state={"h5": {"pct": 10, "reset": now + H}, "week": {"pct": 20, "reset": now + 86400}},
                                    auth_rescue=None)
        a = bare_codex(self.host)
        self.host.meter_update = Mock()
        a.fail_notice("You've hit your usage limit. Upgrade to Pro, or try again in 3 hours.")
        self.assertTrue(self.r.hit.called, "a confirmed limit is never pressed by stale readings")
        self.assertEqual(self.r.hit.call_args.kwargs["stale"], {"h5": 10.0, "week": 20.0})
        self.assertEqual(set(self.host.meter_update.call_args.kwargs), {"h5", "week"})
        self.assertTrue(all(w["stale"] for w in self.host.meter_update.call_args.kwargs.values()))

    def test_rate_meter_is_timed(self):
        a = bare_codex(SimpleNamespace(lang="zh", turn_failed=Mock(), agent_notice=Mock(), models_changed=Mock(),
                                       st=SimpleNamespace(log=Mock(), root=pathlib.Path("/nonexistent")),
                                       quota_resume=Mock(), meter_state={}, auth_rescue=None))
        a.persist, a.meter = True, Mock()
        a.rate_meter({"primary": {"usedPercent": 12, "windowDurationMins": 300, "resetsAt": int(time.time()) + H}})
        self.assertAlmostEqual(a.meter.call_args.kwargs["h5"]["at"], time.time(), delta=2)


class TaskRun(unittest.TestCase):
    """tasks.py's own Claude stream uses the same verdict (and the chat's meters when the harness is the same)."""

    def test_workflow_claude_run(self):
        from agentj import tasks
        now = time.time()
        host = SimpleNamespace(agent=SimpleNamespace(kind="claude"), st=SimpleNamespace(root=pathlib.Path("/nonexistent")),
                               meter_state={"h5": {"pct": 14, "reset": now + 3 * H}, "week": {"pct": 39, "reset": now + 86400}})
        self.assertIs(qr.host_meters(host, "claude"), host.meter_state)
        self.assertIsNone(qr.host_meters(host, "codex"), "the phone's meters are the chat harness's")
        self.assertIn("quota_resume.judge", pathlib.Path(tasks.__file__).read_text())
        self.assertIn("mark_stale", pathlib.Path(tasks.__file__).read_text())
        self.assertEqual(qr.judge("claude", "You've hit your limit · resets 5pm", None,
                                  qr.host_meters(host, "claude"))["stale"], {"h5": 14.0, "week": 39.0})


if __name__ == "__main__":
    unittest.main()
