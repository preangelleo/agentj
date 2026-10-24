"""P254 (owner 2026-10-14): the quota steward — `agentj quota` per harness (used %, reset, day of the week, the even 7-day
pace ≈14.3 %/day, projected unused, recorded extra resets), four deterministic reminder rules, once a day per kind, owner can
turn them off; carried by the heartbeat and the morning brief.

Fixtures only: a tapped Claude status line, a Codex session log with the official `rate_limits` event, OpenCode's measured
provider window; a broken / missing source is `unknown`, never an error.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import heartbeat as hb, preferences, quota, serve  # noqa: E402
from agentj.state import State  # noqa: E402

DAY = 86400
NOW = 1_792_000_000.0          # a fixed clock


def claude_fixture(root: pathlib.Path, pct, reset, sid="s1", mtime=None):
    d = root / "claude-statusline"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sid}.json"
    p.write_text(json.dumps({"session_id": sid, "week": {"pct": pct, "reset": reset}, "h5": {"pct": 3, "reset": reset - DAY}}))
    if mtime:
        os.utime(p, (mtime, mtime))
    return p


def codex_fixture(home: pathlib.Path, used, reset, credits=True, window=10080, name="rollout-a.jsonl", day="14"):
    d = home / "sessions" / "2026" / "10" / day
    d.mkdir(parents=True, exist_ok=True)
    rl = {"limit_id": "codex", "limit_name": None,
          "primary": {"used_percent": used, "window_minutes": window, "resets_at": reset}, "secondary": None,
          "credits": {"has_credits": True, "unlimited": False, "balance": "62500.0000000000"} if credits else None,
          "plan_type": "pro"}
    lines = [json.dumps({"type": "session_meta", "payload": {"id": "x"}}),
             json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": None,
                                                          "rate_limits": {**rl, "primary": {**rl["primary"], "used_percent": 1.0}}}}),
             json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": {"total": 1}, "rate_limits": rl}}),
             '{"type": "event_msg", "payload": {"rate_limits": BROKEN',
             json.dumps({"type": "response_item", "payload": {"text": "no limits here"}})]
    (d / name).write_text("\n".join(lines) + "\n")
    return d / name


def src(harness="claude", used=50.0, days_left=3.5, now=NOW):
    return {"harness": harness, "status": "ok", "source": "fixture", "used_pct": used,
            "reset_at": now + days_left * DAY, "source_at": now - 60}


class Sources(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)

    def test_claude_newest_statusline_wins(self):
        claude_fixture(self.root, 20, NOW + 3 * DAY, "old", mtime=NOW - 3600)
        claude_fixture(self.root, 31.5, NOW + 3 * DAY, "new", mtime=NOW - 60)
        s = quota.claude_source(self.root, NOW)
        self.assertEqual((s["status"], s["used_pct"], s["reset_at"]), ("ok", 31.5, NOW + 3 * DAY))

    def test_claude_missing_or_broken_is_unknown(self):
        self.assertEqual(quota.claude_source(self.root, NOW), quota.unknown("claude", "no_statusline_tap"))
        d = self.root / "claude-statusline"
        d.mkdir()
        (d / "x.json").write_text("{not json")
        (d / "y.json").write_text(json.dumps({"session_id": "y", "week": None}))
        self.assertEqual(quota.claude_source(self.root, NOW)["why"], "no_week_window")

    def test_codex_last_rate_limits_event(self):
        home = self.root / "codex"
        codex_fixture(home, 13.0, NOW + 6 * DAY)
        s = quota.codex_source({"CODEX_HOME": str(home)}, NOW)
        self.assertEqual((s["status"], s["used_pct"], s["reset_at"]), ("ok", 13.0, NOW + 6 * DAY))
        self.assertEqual(s["credits"], {"has_credits": True, "unlimited": False, "balance": 62500.0})

    def test_codex_newest_day_first_and_failures_unknown(self):
        home = self.root / "codex"
        self.assertEqual(quota.codex_source({"CODEX_HOME": str(home)}, NOW)["why"], "no_session_logs")
        codex_fixture(home, 70.0, NOW + DAY, day="13")
        codex_fixture(home, 5.0, NOW + 6 * DAY, day="14", credits=False)
        s = quota.codex_source({"CODEX_HOME": str(home)}, NOW)
        self.assertEqual(s["used_pct"], 5.0)
        self.assertNotIn("credits", s)
        home2 = self.root / "codex2"
        codex_fixture(home2, 5.0, NOW + DAY, window=300)            # only a 5-hour window
        self.assertEqual(quota.codex_source({"CODEX_HOME": str(home2)}, NOW)["why"], "no_week_window")
        home3 = self.root / "codex3"
        (home3 / "sessions/2026/10/14").mkdir(parents=True)
        (home3 / "sessions/2026/10/14/r.jsonl").write_text('{"type":"session_meta"}\n')
        self.assertEqual(quota.codex_source({"CODEX_HOME": str(home3)}, NOW)["why"], "no_rate_limits")

    def test_opencode_partial_or_unknown(self):
        self.assertEqual(quota.opencode_source(None)["status"], "unknown")
        s = quota.opencode_source({"quota_windows": [{"window": "daily", "pct": 90}, {"window": "weekly", "pct": 42.0}]})
        self.assertEqual((s["status"], s["used_pct"], s["why"]), ("partial", 42.0, "no_reset_time"))
        a = quota.analyse(s, [], NOW)
        self.assertEqual(quota.assess(a), [], "no reset time → no pace, no reminder")

    def test_report_never_raises_and_lists_every_harness(self):
        with patch.object(quota, "claude_source", side_effect=RuntimeError("boom")):
            rep = quota.report(self.root, NOW, env={"CODEX_HOME": str(self.root / "none")})
        self.assertEqual([a["harness"] for a in rep["harnesses"]], ["codex", "opencode", "claude"])
        self.assertEqual(next(a for a in rep["harnesses"] if a["harness"] == "claude")["why"], "source_failed")
        self.assertTrue(all(a["status"] == "unknown" for a in rep["harnesses"]))


class Pace(unittest.TestCase):
    def test_even_pace_numbers(self):
        a = quota.analyse(src(used=30.0, days_left=3.5), [], NOW)            # day 3.5 of 7 → even pace 50 %
        self.assertEqual((a["elapsed_days"], a["even_pace_pct"], a["remaining_pct"]), (3.5, 50.0, 70.0))
        self.assertAlmostEqual(a["behind_days"], 1.4, places=2)
        self.assertEqual((a["projected_end_used_pct"], a["projected_end_unused_pct"]), (60.0, 40.0))

    def test_window_rolled_over_is_unknown(self):
        a = quota.analyse(src(days_left=-0.1), [], NOW)
        self.assertEqual((a["status"], a["why"]), ("unknown", "window_rolled_over"))
        self.assertEqual(quota.assess(a), [])


class Rules(unittest.TestCase):
    """The four owner situations, deterministic."""

    def kinds(self, used, days_left, resets=()):
        return quota.assess(quota.analyse(src(used=used, days_left=days_left), list(resets), NOW))

    def test_1_underuse_behind_more_than_a_day(self):
        self.assertEqual(self.kinds(20, 4.0), ["underuse"])                 # pace 42.9 %, 1.6 days behind
        self.assertEqual(self.kinds(32, 4.0), [], "0.76 day behind: within tolerance")
        self.assertEqual(self.kinds(0, 6.5), [], "first day of the window: no pace judgement")
        self.assertEqual(self.kinds(60, 4.0), [], "ahead of pace")

    def test_2_late_week_surplus(self):
        self.assertEqual(self.kinds(40, 2.5), ["late_surplus"])             # 60 % left > (2.5+1) × 14.3 = 50 %
        self.assertEqual(self.kinds(55, 2.5), [], "45 % left, 0.65 day behind: on pace enough")
        self.assertEqual(self.kinds(5, 5.0), ["underuse"], "early in the week the milder wording")
        self.assertEqual(self.kinds(70, 2.0), [], "on pace in the second half")

    def test_3_reset_expiring_within_a_week(self):
        soon = {"harness": "claude", "expires": NOW + 5 * DAY}
        later = {"harness": "claude", "expires": NOW + 20 * DAY}
        self.assertEqual(self.kinds(60, 4.0, [soon]), ["reset_expiring"])
        self.assertEqual(self.kinds(60, 4.0, [later]), [], "more than 7 days away: not yet")
        self.assertEqual(self.kinds(20, 4.0, [soon]), ["reset_expiring", "underuse"])

    def test_4_exhausted_with_reset_says_reset_now(self):
        r = {"harness": "claude", "expires": NOW + 20 * DAY}
        self.assertEqual(self.kinds(97, 2.0, [r]), ["reset_now"])
        self.assertEqual(self.kinds(97, 2.0), ["week_high"], "exhausted without a reset: a plain warning, nothing to press")
        self.assertEqual(self.kinds(91, 2.0), ["week_high"])
        self.assertEqual(self.kinds(85, 2.0), [], "below 90 %: no weekly warning")

    def test_texts_name_the_numbers(self):
        a = quota.analyse(src(used=20, days_left=4.0), [], NOW)
        t = quota.reminder_text(a, "underuse", "zh")
        self.assertIn("已用 20%", t)
        self.assertIn("14.3%", t)
        self.assertIn("多派点活", t)
        self.assertIn("more work", quota.reminder_text(a, "underuse", "en"))
        r = quota.analyse(src(used=97, days_left=2.0), [{"harness": "claude", "expires": NOW + 3 * DAY}], NOW)
        self.assertIn("现在就按 reset", quota.reminder_text(r, "reset_now", "zh"))
        self.assertIn("立刻用掉", quota.reminder_text(r, "reset_expiring", "zh"))
        self.assertIn("big jobs", quota.reminder_text(quota.analyse(src(used=40, days_left=2.5), [], NOW), "late_surplus", "en"))


class FiveHour(unittest.TestCase):
    """RESUME 1: the 5-hour window — read from both harnesses, ≥ 80 % warns once per window, names harnesses with room."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)

    def h5src(self, harness="claude", h5=85.0, used=50.0, reset_in=2 * 3600, status="ok"):
        s = src(harness, used=used)
        s["h5"] = {"used_pct": h5, "reset_at": NOW + reset_in, "source_at": NOW - 60}
        if status != "ok":
            s = {**quota.unknown(harness, "no_week_window"), "h5": s["h5"]}
        return s

    def test_sources_read_the_five_hour_window(self):
        d = self.root / "claude-statusline"
        d.mkdir()
        (d / "s.json").write_text(json.dumps({"session_id": "s", "week": {"pct": 40, "reset": NOW + 3 * DAY},
                                              "h5": {"pct": 82, "reset": NOW + 3600}}))
        s = quota.claude_source(self.root, NOW)
        self.assertEqual((s["h5"]["used_pct"], s["h5"]["reset_at"]), (82.0, NOW + 3600))
        (d / "s.json").write_text(json.dumps({"session_id": "s", "h5": {"pct": 82, "reset": NOW + 3600}}))
        s = quota.claude_source(self.root, NOW)
        self.assertEqual((s["status"], s["h5"]["used_pct"]), ("unknown", 82.0), "no weekly window: the 5-hour one still counts")
        home = self.root / "codex"
        f = codex_fixture(home, 30, NOW + 3 * DAY)
        rl = {"primary": {"used_percent": 88.0, "window_minutes": 300, "resets_at": NOW + 7200},
              "secondary": {"used_percent": 30.0, "window_minutes": 10080, "resets_at": NOW + 3 * DAY}}
        f.write_text(json.dumps({"type": "event_msg", "payload": {"rate_limits": rl}}) + "\n")
        c = quota.codex_source({"CODEX_HOME": str(home)}, NOW)
        self.assertEqual((c["used_pct"], c["h5"]["used_pct"]), (30.0, 88.0))
        self.assertNotIn("h5", quota.opencode_source({"quota_windows": [{"window": "weekly", "pct": 10}]}))

    def test_warns_at_80_and_stale_windows_are_dropped(self):
        a = quota.analyse(self.h5src(h5=80.0), [], NOW)
        self.assertEqual(quota.assess(a), ["h5_high"])
        self.assertEqual(quota.assess(quota.analyse(self.h5src(h5=79.9), [], NOW)), [])
        self.assertNotIn("h5", quota.analyse(self.h5src(h5=95, reset_in=-60), [], NOW), "the window already reset")
        self.assertNotIn("h5", quota.analyse(self.h5src(h5=95, reset_in=DAY), [], NOW), "not a 5-hour window")
        self.assertEqual(quota.assess(quota.analyse(self.h5src(h5=90, status="unknown"), [], NOW)), ["h5_stop"],
                         "a 5-hour reading alone still carries the duty")
        t = quota.reminder_text(a, "h5_high", "zh")
        self.assertIn("5 小时额度已用 80%", t)
        self.assertIn("服务器直接终止", t)
        self.assertIn("stopped by the server", quota.reminder_text(a, "h5_high", "en"))

    def test_once_per_window_and_other_accounts_named(self):
        rep = quota.report(self.root, NOW, sources=[self.h5src(), src("codex", used=45.0, days_left=3.5)])
        self.assertEqual([i["key"] for i in quota.due(rep, self.root, day="2026-10-14")], [f"claude:h5_high:{int(NOW + 7200)}"])
        self.assertEqual(quota.due(rep, self.root, day="2026-10-15"), [], "same window: once, even across midnight")
        rep2 = quota.report(self.root, NOW + 3 * 3600, sources=[self.h5src(reset_in=5 * 3600)])
        self.assertEqual(len(quota.due(rep2, self.root, day="2026-10-15")), 1, "a new window: again")
        alts = quota.headroom(rep, "claude")
        self.assertEqual([x["harness"] for x in alts], ["codex"])
        self.assertIn("新活可以派给它", quota.reminder_text(rep["harnesses"][0], "h5_high", "zh", alts))
        busy = quota.report(self.root, NOW, sources=[self.h5src(), self.h5src("codex", h5=90)])
        self.assertEqual(quota.headroom(busy, "claude"), [], "the other one is hot too: no suggestion")
        with patch.object(quota, "report", return_value=quota.report(self.root, NOW, sources=[self.h5src(h5=81, reset_in=4000),
                                                                                              src("codex", used=10.0)])):
            text = quota.heartbeat_lines(self.root, {}, "zh")
            self.assertIn("5 小时额度已用 81%", text)
            self.assertIn("Codex（本周已用 10%）", text)
            self.assertEqual(quota.heartbeat_lines(self.root, {}, "zh"), "")
        self.assertIn("5 小时窗口已用 85%", quota.render(rep, "zh"))


class SwitchHost:
    """Just what quota.route and tasks.Scheduler.run touch."""

    def __init__(self, st, prefs=None, answer="allow"):
        self.st, self.preferences, self.answer = st, prefs or {}, answer
        self.agent_cfg = {"kind": "claude", "dir": str(st.root), "model": "opus", "effort": "high",
                          "session_mode": "shared", "shared_session_id": "abc", "shared_opencode_port": 4096, "fence": False}
        self.lang, self.agent, self.task_label, self.brief_scope, self.busy, self.capabilities = "zh", None, None, None, None, None
        self.turn_lock = asyncio.Lock()
        self.asks, self.finished, self.meter_state = [], [], None

    def stopped(self):
        return False

    def activity(self, *a, **k):
        pass

    def task_finished(self, res):
        self.finished.append(res)

    async def ask(self, tool, tool_input, gone=None, batch=True, **k):
        self.asks.append((tool, tool_input["summary"], batch))
        return {"behavior": self.answer}


class AutoSwitch(unittest.TestCase):
    """RESUME 1: quota alarm → the new task goes to another AI account (auto when on; a phone card when off)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        (pathlib.Path(self.tmp.name) / "state").mkdir(mode=0o700)
        self.st = State(pathlib.Path(self.tmp.name) / "state")
        self.probed = []

    def rep(self, claude_h5=85.0, claude_week=50.0, codex_week=20.0, opencode=None):
        c = src("claude", used=claude_week)
        c["h5"] = {"used_pct": claude_h5, "reset_at": NOW + 3600, "source_at": NOW}
        srcs = [c, src("codex", used=codex_week)]
        if opencode is not None:
            srcs.append(src("opencode", used=opencode))
        return quota.report(self.st.root, NOW, sources=srcs)

    def probe(self, ok=True):
        def f(name):
            self.probed.append(name)
            return ok
        return f

    def route(self, host, rep, task=None, ok=True):
        return asyncio.run(quota.route(host, "claude", task or {}, "周报", rep=rep, probe=self.probe(ok)))

    def test_not_alarmed_stays_and_never_probes(self):
        h = SwitchHost(self.st, {"quota": {"auto_switch": True}})
        self.assertEqual(self.route(h, self.rep(claude_h5=40)), ("claude", None))
        self.assertEqual((self.probed, h.asks), ([], []))

    def test_on_switches_without_asking(self):
        h = SwitchHost(self.st, {"quota": {"auto_switch": True}})
        kind, note = self.route(h, self.rep())
        self.assertEqual((kind, h.asks, self.probed), ("codex", [], ["codex"]))
        self.assertIn("改用 Codex", note)
        h2 = SwitchHost(self.st, {"quota": {"auto_switch": True}})
        self.assertEqual(self.route(h2, self.rep(claude_h5=10, claude_week=93))[0], "codex", "a weekly alarm switches too")

    def test_off_asks_the_owner_first(self):
        h = SwitchHost(self.st, {}, answer="allow")
        self.assertEqual(self.route(h, self.rep())[0], "codex")
        tool, text, batch = h.asks[0]
        self.assertEqual((tool, batch), (quota.SWITCH_TOOL, False))
        self.assertIn("改用 Codex 来做", text)
        self.assertIn("5 小时额度 85%", text)
        no = SwitchHost(self.st, {}, answer="deny")
        self.assertEqual(self.route(no, self.rep()), ("claude", None), "the owner said no: the same AI carries on")
        self.assertIn(quota.SWITCH_TOOL, __import__("agentj.goal", fromlist=["x"]).SOFT_EXEMPT,
                      "never auto-approved by a goal tier")

    def test_failed_handshake_or_no_room_stays_and_does_not_ask(self):
        h = SwitchHost(self.st, {})
        self.assertEqual(self.route(h, self.rep(), ok=False), ("claude", None))
        self.assertEqual((self.probed, h.asks), (["codex", "opencode"], []))
        self.probed.clear()
        self.assertEqual(self.route(h, self.rep(codex_week=95))[0], "opencode", "P375: unknown quota still needs a real logged-in handshake")
        self.assertEqual(self.probed, ["opencode"])

    def test_workflow_named_harness_is_respected(self):
        h = SwitchHost(self.st, {"quota": {"auto_switch": True}})
        self.assertEqual(self.route(h, self.rep(), {"harness": "claude"}), ("claude", None))
        self.assertEqual(self.route(h, self.rep(opencode=10), {"harness": ["claude", "opencode"]})[0], "opencode")
        self.assertEqual(self.probed, ["opencode"])
        from agentj import taskspec
        base = {"v": 1, "id": "w", "title": {"zh": "周报", "en": "Weekly"}, "schedule": "0 9 * * 1", "tz": "local",
                "prompt_file": "RUN.md", "dry_run_prompt_file": "DRYRUN.md", "mode": "normal", "enabled": False,
                "needs": [], "outputs": ["reports/x.md"]}
        self.assertEqual(taskspec.problems(base), [])
        self.assertEqual(taskspec.problems({**base, "harness": "codex"}), [])
        self.assertEqual(taskspec.problems({**base, "harness": ["claude", "codex"]}), [])
        self.assertTrue(taskspec.problems({**base, "harness": "gemini"}))
        self.assertTrue(taskspec.problems({**base, "harness": []}))

    def test_alt_cfg_drops_the_main_harness_specifics(self):
        c = quota.alt_cfg(SwitchHost(self.st).agent_cfg, "codex")
        self.assertEqual((c["kind"], c["session_mode"]), ("codex", "independent"))
        for k in ("model", "effort", "shared_session_id", "shared_opencode_port"):
            self.assertNotIn(k, c)
        self.assertIs(c["fence"], False, "everything else (fence, approvals, folder) is the same")

    def test_scheduler_runs_the_task_on_the_switched_harness(self):
        from agentj import tasks
        h = SwitchHost(self.st, {"quota": {"auto_switch": True}})
        sch = tasks.Scheduler(h)
        e = {"id": "w", "dir": str(self.st.root), "problems": [], "tsha": "x",
             "task": {"title": {"zh": "周报", "en": "Weekly"}, "mode": "normal"}}
        seen = {}

        async def fake_exec(kind, e_, research):
            seen["kind"] = kind
            return "完成\nVERDICT: ok — 写好了", 0
        with patch.object(tasks, "find", return_value=e), patch.object(sch, "_exec", side_effect=fake_exec), \
                patch.object(sch, "_save", return_value=None), patch.object(tasks.Scheduler, "_check_report"), \
                patch.object(quota, "report", return_value=self.rep()), patch.object(quota, "handshake", return_value=True), \
                patch.object(quota.route, "__defaults__", (None, quota.handshake)):
            res = asyncio.run(sch.run("w", "manual"))
        self.assertEqual(seen["kind"], "codex")
        self.assertEqual((res["verdict"], res["harness"]), ("ok", "codex"))
        self.assertIn("这次改用 Codex 运行", res["line"])
        with patch.object(tasks, "find", return_value=e), patch.object(sch, "_exec", side_effect=fake_exec), \
                patch.object(sch, "_save", return_value=None), patch.object(tasks.Scheduler, "_check_report"), \
                patch.object(quota, "route", side_effect=RuntimeError("boom")):
            res = asyncio.run(sch.run("w", "manual"))
        self.assertEqual((seen["kind"], res["verdict"]), ("claude", "ok"), "a broken steward never stops the task")
        self.assertNotIn("harness", res)


class StateAndDedupe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)

    def test_recorded_resets(self):
        with self.assertRaises(ValueError):
            quota.parse_expires("2026-13-01")
        with self.assertRaises(ValueError):
            quota.parse_expires("2020-01-01")
        exp = quota.parse_expires("2099-10-20")
        self.assertEqual(time.localtime(exp)[:6], (2099, 10, 20, 23, 59, 59))
        self.assertEqual(time.localtime(quota.parse_expires("2099-10-20 09:30"))[3:5], (9, 30))
        quota.add_reset(self.root, "claude", NOW + 9 * DAY, now=NOW)
        quota.add_reset(self.root, "claude", NOW + 2 * DAY, note="one\nline", now=NOW)
        with self.assertRaises(ValueError):
            quota.add_reset(self.root, "gemini", NOW + DAY)
        st = quota.load_state(self.root)
        self.assertEqual(oct(quota.state_path(self.root).stat().st_mode & 0o777), "0o600")
        self.assertEqual([r["expires"] for r in quota.live_resets(st, "claude", NOW)], [NOW + 2 * DAY, NOW + 9 * DAY])
        self.assertEqual(st["resets"][1]["note"], "one line")
        used = quota.use_reset(self.root, "claude", now=NOW)
        self.assertEqual(used["expires"], NOW + 2 * DAY, "the soonest-expiring one goes first")
        self.assertIsNone(quota.use_reset(self.root, "codex", now=NOW))
        self.assertEqual(quota.live_resets(quota.load_state(self.root), "claude", NOW + 10 * DAY), [], "expired ones are not live")

    def test_once_a_day_per_kind(self):
        rep = quota.report(self.root, NOW, sources=[src(used=20, days_left=4.0), src("codex", used=10, days_left=4.0)])
        first = quota.due(rep, self.root, day="2026-10-14")
        self.assertEqual({i["key"] for i in first}, {"claude:underuse", "codex:underuse"})
        self.assertEqual(quota.due(rep, self.root, day="2026-10-14"), [], "same day: given already")
        self.assertEqual(len(quota.due(rep, self.root, day="2026-10-15")), 2, "next day: again")
        rep2 = quota.report(self.root, NOW, sources=[src(used=20, days_left=4.0)])
        quota.add_reset(self.root, "claude", NOW + 3 * DAY, now=NOW)
        rep2 = quota.report(self.root, NOW, sources=[src(used=20, days_left=4.0)])
        self.assertEqual([i["key"] for i in quota.due(rep2, self.root, day="2026-10-15")], ["claude:reset_expiring"],
                         "a different kind on the same day still reminds")
        self.assertEqual(quota.due(rep2, self.root, day="2026-10-16", mark=False), quota.due(rep2, self.root, day="2026-10-16"))

    def test_heartbeat_lines_and_switch(self):
        with patch.object(quota, "report", return_value=quota.report(self.root, NOW, sources=[src(used=20, days_left=4.0)])):
            self.assertEqual(quota.heartbeat_lines(self.root, {"quota": {"reminders": False}}), "", "the owner turned it off")
            text = quota.heartbeat_lines(self.root, {}, "zh")
            self.assertTrue(text.startswith("〔额度提醒〕"))
            self.assertIn("1–3 个具体的大活", text)
            self.assertIn("不要回〔不回群〕", text)
            self.assertEqual(quota.heartbeat_lines(self.root, {}, "zh"), "", "once a day")

    def test_schema_key(self):
        self.assertIs(preferences.defaults()["quota"]["reminders"], True)
        self.assertEqual(preferences.SCHEMA["quota.reminders"]["tier"], "user")
        from agentj import ctl_auth
        self.assertFalse(any("quota.reminders".startswith(p) for p in ctl_auth.SECURITY_PREFIXES))


class FakeAgent:
    native_source = {"kind": "terminal", "role": "owner"}
    kind = "claude"

    def __init__(self):
        self.cfg = {"session_mode": "independent"}
        self.status, self.halting, self.cur_send, self.cur_cmd = "idle", False, None, None
        self.q = asyncio.Queue()
        self.got = []

    def submit(self, x):
        self.got.append(x)

    def is_down(self):
        return False


class Wiring(unittest.TestCase):
    """serve: the heartbeat carries the day's reminders; heartbeat.sock answers quota ops (reads for anyone, writes not
    during an untrusted turn)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st = State(pathlib.Path(self.tmp.name) / "state")
        self.st.init(relay="ws://127.0.0.1:1")
        self.host = serve.Host(self.st, events="jsonl", read_stdin=False)
        self.host._post = lambda *a, **k: None
        self.agent = FakeAgent()
        self.host.agent = self.agent
        self.h = self.host.heartbeat
        for p in (patch.object(hb, "_night", return_value=False),
                  patch.object(type(self.st), "devices", return_value={"d1": {"name": "手机"}}),
                  patch.dict(os.environ, {"XDG_CONFIG_HOME": str(pathlib.Path(self.tmp.name) / "cfg"),
                                          "CODEX_HOME": str(pathlib.Path(self.tmp.name) / "codex")})):
            p.start()
            self.addCleanup(p.stop)

    def test_beat_carries_reminder_once(self):
        now = time.time()
        claude_fixture(self.st.root, 10, now + 4 * DAY)                    # day 3 of 7, 10 % used → underuse
        self.h.touch()
        r = asyncio.run(self.h.tick(self.h.next_due() + 1))
        self.assertTrue(r["ok"], r)
        text = self.agent.got[-1].text
        self.assertIn("〔额度提醒〕", text)
        self.assertIn("Claude Code 本周额度已用 10%", text)
        self.agent.got[-1].state = "delivered"
        asyncio.run(self.h.tick(self.h.next_due() + 1))
        self.assertNotIn("〔额度提醒〕", self.agent.got[-1].text, "the same reminder only once a day")

    def test_sock_ops(self):
        now = time.time()
        claude_fixture(self.st.root, 10, now + 4 * DAY)
        rep = asyncio.run(self.h.handle({"op": "quota"}))
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["harnesses"][0]["reminders"], ["underuse"])
        res = asyncio.run(self.h.handle({"op": "quota_reset_add", "harness": "claude", "expires": "2099-01-01"}))
        self.assertTrue(res["ok"], res)
        self.assertFalse(asyncio.run(self.h.handle({"op": "quota_reset_add", "harness": "claude", "expires": "soon"}))["ok"])
        rem = asyncio.run(self.h.handle({"op": "quota_remind", "lang": "zh"}))
        self.assertEqual([d["kind"] for d in rem["due"]], ["underuse"])
        self.assertIn("多派点活", rem["due"][0]["text"])
        self.assertEqual(asyncio.run(self.h.handle({"op": "quota_remind"}))["due"], [], "marked given")
        self.assertTrue(asyncio.run(self.h.handle({"op": "quota_reset_use", "harness": "claude"}))["ok"])
        self.assertFalse(asyncio.run(self.h.handle({"op": "quota_reset_use", "harness": "claude"}))["ok"])
        with patch("agentj.provenance.untrusted_turn", return_value=True):
            self.assertTrue(asyncio.run(self.h.handle({"op": "quota"}))["ok"], "reading is fine")
            self.assertEqual(asyncio.run(self.h.handle({"op": "quota_off"}))["why"], "untrusted_turn")
            self.assertEqual(asyncio.run(self.h.handle({"op": "quota_reset_add", "harness": "claude",
                                                        "expires": "2099-01-01"}))["why"], "untrusted_turn")

    def test_off_switch_through_serve(self):
        res = asyncio.run(self.h.handle({"op": "quota_off"}))
        self.assertTrue(res["ok"], res)
        self.assertIs(preferences.get(self.host.preferences, "quota.reminders", True), False)
        claude_fixture(self.st.root, 10, time.time() + 4 * DAY)
        self.h.touch()
        asyncio.run(self.h.tick(self.h.next_due() + 1))
        self.assertNotIn("〔额度提醒〕", self.agent.got[-1].text)
        self.assertFalse(asyncio.run(self.h.handle({"op": "quota_remind"}))["due"])


class Cli(unittest.TestCase):
    def test_cli_offline(self):
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            env = {**os.environ, "AGENTJ_STATE_DIR": os.path.join(d, "state"), "CODEX_HOME": os.path.join(d, "codex"),
                   "XDG_CONFIG_HOME": os.path.join(d, "cfg"), "AGENTJ_HEARTBEAT_SOCK": os.path.join(d, "none.sock")}
            os.makedirs(env["AGENTJ_STATE_DIR"])
            codex_fixture(pathlib.Path(env["CODEX_HOME"]), 10.0, time.time() + 4 * DAY)
            run = lambda *a: subprocess.run([sys.executable, "-c", "import sys; from agentj import cli; sys.argv=['agentj']+sys.argv[1:]; cli.main()", *a],
                                            cwd=HERE.parent, env=env, capture_output=True, text=True, timeout=60)
            out = run("quota", "--json")
            self.assertEqual(out.returncode, 0, out.stderr)
            rep = json.loads(out.stdout)
            codex = next(a for a in rep["harnesses"] if a["harness"] == "codex")
            self.assertEqual((codex["used_pct"], codex["reminders"]), (10.0, ["underuse"]))
            self.assertEqual(next(a for a in rep["harnesses"] if a["harness"] == "claude")["status"], "unknown")
            human = run("quota")
            self.assertIn("Codex: 已用 10%", human.stdout)
            self.assertIn("OpenCode: 查不到", human.stdout)
            self.assertEqual(run("quota", "reset", "add", "claude", "--expires", "2099-10-20").returncode, 0)
            self.assertIn("额外 reset（登记）", run("quota").stdout)
            self.assertIn("〔额度提醒〕", run("quota", "--remind").stdout)
            self.assertIn("今天没有新的额度提醒", run("quota", "--remind").stdout)
            self.assertEqual(run("quota", "off").returncode, 0)
            self.assertIn("额度提醒已关闭", run("quota").stdout)


if __name__ == "__main__":
    unittest.main()
