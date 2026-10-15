"""P314 (owner 2026-10-15): a turn stopped by a used-up usage limit continues by itself after the reset — marked, at most
once, cancellable on the phone (「额度用完，预计 HH:MM 自动继续」), the chat (own or shared session) and workflow runs, never a
repeat and never a reused approval.
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
from types import SimpleNamespace
from unittest import mock
from unittest.mock import Mock, patch

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import agent as agents, codex_perm, heartbeat as hb, quota, quota_resume as qr, serve, tasks  # noqa: E402
from agentj.agent_codex import CodexAgent  # noqa: E402
from agentj.state import State  # noqa: E402

NOW = 1_792_000_000.0
H = 3600


class Parse(unittest.TestCase):
    def test_claude_limit_words(self):
        for t in ("Claude AI usage limit reached|1792003600", "5-hour limit reached ∙ resets 3pm",
                  "You've hit your limit · resets 3pm (Asia/Tokyo)", "You've hit your weekly limit · resets Oct 20, 9am",
                  "Opus weekly limit reached ∙ resets Oct 20, 9am"):
            self.assertTrue(qr.claude_limit(t), t)
        for t in ("API Error: 429 rate_limit_error", "Invalid API key · Please run /login", "", None,
                  "Credit balance is too low"):
            self.assertFalse(qr.claude_limit(t), t)

    def test_parse_when(self):
        self.assertEqual(qr.parse_when("Claude AI usage limit reached|1792003600", NOW), 1792003600.0)
        self.assertEqual(qr.parse_when("…|1792003600000", NOW), 1792003600.0, "milliseconds too")
        self.assertEqual(qr.parse_when("try again in 2 hours 5 minutes", NOW), NOW + 2 * H + 300)
        self.assertEqual(qr.parse_when("try again in 1 day", NOW), NOW + 86400)
        tz = "Asia/Tokyo"
        at = qr.parse_when(f"You've hit your limit · resets 3pm ({tz})", NOW)
        from datetime import datetime
        from zoneinfo import ZoneInfo
        d = datetime.fromtimestamp(at, ZoneInfo(tz))
        self.assertEqual((d.hour, d.minute), (15, 0))
        self.assertTrue(NOW < at <= NOW + 86400, "the next 3pm, never one in the past")
        at = qr.parse_when("or try again at Oct 20th, 2026 3:05 PM.", NOW)
        d = datetime.fromtimestamp(at)
        self.assertEqual((d.year, d.month, d.day, d.hour, d.minute), (2026, 10, 20, 15, 5))
        self.assertIsNone(qr.parse_when("resets 3 things", NOW), "a bare number is not a time")
        self.assertIsNone(qr.parse_when("no time here", NOW))

    def test_reset_time_order_and_sanity(self):
        rate = {"status": "rejected", "resetsAt": NOW + 2 * H}
        self.assertEqual(qr.reset_time("claude", "limit reached|1792003600", rate, now=NOW), NOW + 2 * H)
        self.assertEqual(qr.reset_time("claude", "Claude AI usage limit reached|1792003600", {"status": "allowed"}, now=NOW),
                         1792003600.0)
        meters = {"h5": {"pct": 100, "reset": NOW + H}, "week": {"pct": 40, "reset": NOW + 3 * 86400}}
        self.assertEqual(qr.reset_time("claude", "5-hour limit reached", None, meters, now=NOW), NOW + H)
        meters["week"]["pct"] = 99
        self.assertEqual(qr.reset_time("claude", "limit reached", None, meters, now=NOW), NOW + 3 * 86400, "both used up: later")
        self.assertIsNone(qr.reset_time("claude", "limit reached|1791999000", None, None, now=NOW), "a past reset is stale")
        self.assertIsNone(qr.reset_time("claude", "limit reached", None, {"h5": {"pct": 60, "reset": NOW + H}}, now=NOW))
        self.assertIsNone(qr.sane(NOW + 30 * 86400, NOW), "more than 8 days away is not believed")

    def test_texts(self):
        rec = {"harness": "codex", "at": NOW}
        self.assertTrue(qr.continue_text(rec).startswith("继续。"))
        self.assertIn("不授予任何新权限", qr.continue_text(rec))
        self.assertTrue(qr.continue_text(rec, "en").startswith("Continue."))
        self.assertIn("改用 Codex", qr.task_note({"harness": "claude", "to": "codex"}))
        self.assertIn("额度恢复后的自动重跑", qr.task_note({"harness": "claude"}))
        self.assertEqual(qr.hhmm(NOW + 60, "zh", NOW)[:2], time.strftime("%H", time.localtime(NOW + 60)))


class FakeAgent:
    kind = "claude"

    def __init__(self, host):
        self.host = host
        self.cfg = {"session_mode": "independent"}
        self.status, self.halting, self.cur_send, self.cur_cmd = "idle", False, None, None
        self.q = asyncio.Queue()
        self.got = []

    def submit(self, x):
        self.got.append(x)

    def is_down(self):
        return False


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st = State(pathlib.Path(self.tmp.name) / "state")
        self.st.init(relay="ws://127.0.0.1:1")
        for p in (patch.dict(os.environ, {"XDG_CONFIG_HOME": str(pathlib.Path(self.tmp.name) / "cfg"),
                                          "CODEX_HOME": str(pathlib.Path(self.tmp.name) / "codex")}),
                  patch.object(type(self.st), "devices", return_value={"d1": {"name": "手机"}})):
            p.start()
            self.addCleanup(p.stop)
        self.host = self.make_host()

    def make_host(self):
        h = serve.Host(self.st, events="jsonl", read_stdin=False)
        self.posted = []
        h._post = lambda fn, *a: self.posted.append(a[0](None) if a and callable(a[0]) else a)
        h.agent = FakeAgent(h)
        h.notes = []
        orig = h.agent_notice
        h.agent_notice = lambda t, orig=orig: (h.notes.append(t), orig(t))
        return h

    def saved(self):
        p = self.st.root / qr.FILE
        return json.loads(p.read_text())["items"] if p.exists() else {}


class Chat(Base):
    """The chat (own or shared session): mark, persist, show, fire once."""

    def test_hit_schedules_shows_and_fires_once(self):
        r = self.host.quota_resume
        at = time.time() + 2 * H
        rec = r.hit("main", "claude", at)
        self.assertEqual(self.saved()["main"]["id"], rec["id"], "the mark is on disk before anything waits")
        self.assertIn("预计", self.host.notes[-1])
        self.assertIn("自动发一句「继续」", self.host.notes[-1])
        self.assertEqual(self.posted[-1], {"t": "quota_resume", "items": [{"id": rec["id"], "scope": "main",
                                                                         "harness": "claude", "at": int(at)}]})
        self.assertIs(hb.Heartbeat.wait_reason(self.host.heartbeat), "quota_wait", "no heartbeat into a used-up quota")
        self.assertEqual(r.fire("main"), "fired")
        send = self.host.agent.got[-1]
        self.assertEqual(send.quota_resume_id, rec["id"])
        self.assertTrue(send.text.startswith("继续。"))
        self.assertEqual((send.source, send.blobs, send.device), ({"kind": "system", "role": "owner"}, [], "quota"))
        page = self.host.hist.get(send.turn)
        self.assertEqual(page["src"].get("via"), "quota")
        self.assertEqual(self.saved(), {}, "removed before the turn is queued: a restart never sends it again")
        self.assertEqual(r.fire("main"), "gone", "only once")
        self.assertEqual([p for p in self.posted if isinstance(p, dict) and p.get("t") == "quota_resume"][-1],
                         {"t": "quota_resume", "items": []})

    def test_same_hit_keeps_its_id_and_a_new_hit_replaces(self):
        r = self.host.quota_resume
        at = time.time() + H
        a = r.hit("main", "claude", at)
        self.assertEqual(r.hit("main", "claude", at)["id"], a["id"], "one record per scope")
        b = r.hit("main", "claude", at + H)
        self.assertNotEqual(a["id"], b["id"])
        self.assertEqual(len(r.items), 1)

    def test_dropped_not_fired_when_busy_stopped_or_owner_spoke(self):
        r = self.host.quota_resume
        r.hit("main", "claude", time.time() + H)
        self.host.agent.status = "working"
        self.assertEqual(r.fire("main"), "busy")
        self.assertEqual(self.host.agent.got, [])
        self.host.agent.status = "idle"
        r.hit("main", "claude", time.time() + H)
        with patch.object(self.host, "stopped", return_value=True):
            self.assertEqual(r.fire("main"), "stopped")
        r.hit("main", "claude", time.time() + H)
        r.owner_spoke("main")
        self.assertEqual((r.items, self.host.agent.got), ({}, []))

    def test_open_approval_card_counts_as_busy(self):
        r = self.host.quota_resume
        r.hit("main", "claude", time.time() + H)
        fut = asyncio.new_event_loop().create_future()
        self.host.asks["x"] = SimpleNamespace(fut=fut)
        self.assertEqual(r.fire("main"), "busy", "never sent while a card waits: it could be answered by the wrong turn")

    def test_unknown_reset_schedules_nothing(self):
        self.assertIsNone(self.host.quota_resume.hit("main", "codex", None))
        self.assertEqual(self.host.quota_resume.items, {})
        self.assertIn("没给重置时间", self.host.notes[-1])

    def test_a_resumed_turn_hitting_the_same_reset_does_not_loop(self):
        r = self.host.quota_resume
        at = time.time() + H
        rec = r.hit("main", "claude", at)
        r.fire("main")
        self.assertIsNone(r.hit("main", "claude", at, frm_resume=rec["id"]))
        self.assertIn("不再自动重试", self.host.notes[-1])
        self.assertIsNotNone(r.hit("main", "claude", at + 5 * H, frm_resume=rec["id"]), "a strictly later reset may")

    def test_cancel_from_phone_and_from_the_agent(self):
        r = self.host.quota_resume
        rec = r.hit("main", "claude", time.time() + H)
        sent = []

        async def send_app(s, m):
            sent.append(m)
            return True
        self.host.send_app = send_app
        s = SimpleNamespace(device="d1", name="手机", cid=1)
        asyncio.run(self.host.on_p33(s, "qr_cancel", {"r": "r1", "id": "nope"}))
        self.assertEqual((sent[-1]["ok"], sent[-1]["why"]), (False, "gone"))
        asyncio.run(self.host.on_p33(s, "qr_cancel", {"r": "r2", "id": rec["id"]}))
        self.assertEqual(sent[-1], {"t": "qr_res", "r": "r2", "ok": True, "items": []})
        self.assertEqual(self.host.notes[-1], "已取消自动继续。")
        self.assertIn("qr_cancel", serve.P33_ONLY)
        rec = r.hit("main", "claude", time.time() + H)
        res = asyncio.run(quota.handle(self.host, {"op": "quota_resume"}))
        self.assertEqual(res["items"][0]["id"], rec["id"])
        res = asyncio.run(quota.handle(self.host, {"op": "quota_resume_cancel", "id": rec["id"]}))
        self.assertTrue(res["ok"])
        self.assertIn("quota_resume_cancel", quota.WRITE_OPS)

    def test_phone_say_drops_the_waiting_continue(self):
        r = self.host.quota_resume
        r.hit("main", "claude", time.time() + H)
        s = SimpleNamespace(device="d1", name="手机", cid=1, source_kind=None, provenance=None)
        with patch.object(self.host, "speaker_note", return_value=""), patch.object(self.host, "onboarding_note", return_value=""):
            asyncio.run(self.host._accept(s, "我自己来", None))
        self.assertEqual(r.items, {})
        self.assertEqual(self.host.agent.got[-1].text.strip()[:4], "我自己来")

    def test_estop_drops_everything(self):
        r = self.host.quota_resume
        r.hit("main", "claude", time.time() + H)
        r.hit("task:w", "codex", time.time() + H, "周报")
        self.host.agent.halt = mock.AsyncMock(return_value=False)
        with patch.object(self.host.scheduler, "stop_current", new=mock.AsyncMock(return_value=False)), \
                patch.object(serve.controls, "set_estop", return_value={"on": True}):
            asyncio.run(self.host.do_estop("phone:d1", "手机"))
        self.assertEqual(r.items, {})

    def test_queue_drop_and_delivery_bookkeeping(self):
        r = self.host.quota_resume
        rec = r.hit("main", "claude", time.time() + H)
        r.fire("main")
        send = self.host.agent.got[-1]
        self.host.say_delivered(send)
        self.host.queue_dropped(send)
        self.assertEqual(send.state, "cancelled")
        self.assertEqual(self.host.hist.get(send.turn)["end"], "stopped")
        self.assertFalse(any(isinstance(p, dict) and p.get("t") == "say_state" for p in self.posted),
                         "no phone ever owned this message")
        del rec

    def test_restart_rearms_and_a_switched_harness_fires_at_once(self):
        r = self.host.quota_resume
        rec = r.hit("main", "claude", time.time() + 2 * H)
        h2 = self.make_host()
        h2.quota_resume.load()
        self.assertEqual(h2.quota_resume.items["main"]["id"], rec["id"])
        self.assertGreater(h2.quota_resume.due_at(h2.quota_resume.items["main"], "main"), time.time())
        h2.agent.kind = "codex"           # serve came back on another harness (another account)
        self.assertEqual(h2.quota_resume.due_at(h2.quota_resume.items["main"], "main"), 0.0)

        async def go():
            h2.quota_resume.start()
            for _ in range(20):
                await asyncio.sleep(0)
        asyncio.run(go())
        self.assertEqual(h2.agent.got[-1].quota_resume_id, rec["id"])
        self.assertEqual(self.saved(), {})

    def test_timer_fires_after_the_reset(self):
        r = self.host.quota_resume

        async def go():
            with patch.object(qr, "RESUME_MARGIN", 0), patch.object(qr, "MIN_LEAD", 0):
                r.hit("main", "claude", time.time() + 0.05)
                await asyncio.sleep(0.3)
        asyncio.run(go())
        self.assertEqual(len(self.host.agent.got), 1)


class Adapters(unittest.TestCase):
    def setUp(self):
        self.r = Mock()
        self.host = SimpleNamespace(lang="zh", turn_failed=Mock(), agent_notice=Mock(), models_changed=Mock(),
                                    st=SimpleNamespace(log=Mock(), root=pathlib.Path("/nonexistent")), quota_resume=self.r,
                                    meter_state={}, auth_rescue=None)

    def test_claude_result_with_the_limit(self):
        a = agents.ClaudeAgent(self.host, {"kind": "claude", "dir": "/tmp", "model": None})
        a.halting, a.capture, a.turn_done = False, None, None
        a.rate = {"status": "rejected", "resetsAt": time.time() + 2 * H}
        a.on_event({"type": "result", "is_error": True, "result": "You've hit your limit · resets 3pm"})
        args = self.r.hit.call_args.args
        self.assertEqual((args[0], args[1]), ("main", "claude"))
        self.assertAlmostEqual(args[2], a.rate["resetsAt"], delta=1)
        self.r.hit.reset_mock()
        a.on_event({"type": "result", "is_error": True, "result": "API Error: 500 overloaded"})
        self.assertFalse(self.r.hit.called, "only a used-up usage limit")

    def test_shared_claude_short_reply_only(self):
        from agentj.shared import SharedClaudeAgent
        a = object.__new__(SharedClaudeAgent)
        a.host, a.cfg, a.cur_send = self.host, {"session_mode": "shared"}, None
        a._limit_check("Here is why a usage limit reached message happens… " * 20)
        self.assertFalse(self.r.hit.called, "the model talking about limits is not the limit")
        a._limit_check("Claude AI usage limit reached|%d" % int(time.time() + H))
        self.assertEqual(self.r.hit.call_args.args[:2], ("main", "claude"))


def bare_codex(host, workflow=False):
    a = object.__new__(CodexAgent)
    a.cfg = {"model": "gpt-6", **({"_workflow_ceo": True} if workflow else {})}
    a.thread = {"modelProvider": "openai"}; a.human = {}; a.models_cache = []
    a.unavailable_models = set(); a.halting = False; a.tid = "t"; a.meter = Mock(); a.tasks = set(); a.thread_at = None
    a.rate_retry, a.rate_retried, a.cur_send = False, False, None
    a.host = host
    return a


class Codex(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(codex_perm, "provider", return_value={"id": "openai", "host": None, "relay": False})
        p.start(); self.addCleanup(p.stop)
        self.r = Mock()
        self.host = SimpleNamespace(lang="zh", turn_failed=Mock(), agent_notice=Mock(), models_changed=Mock(),
                                    st=SimpleNamespace(log=Mock(), root=pathlib.Path("/nonexistent")), quota_resume=self.r,
                                    meter_state={"h5": {"pct": 100, "reset": time.time() + H}}, auth_rescue=None)

    def test_usage_limit_marks_the_chat(self):
        a = bare_codex(self.host)
        a.fail_notice("You've hit your usage limit. Upgrade to Pro, or try again in 3 hours.")
        args = self.r.hit.call_args.args
        self.assertEqual(args[:2], ("main", "codex"))
        self.assertAlmostEqual(args[2], time.time() + 3 * H, delta=5)
        self.r.hit.reset_mock()
        a.fail_notice("exceeded retry limit, last status: 429 Too Many Requests")
        self.assertFalse(self.r.hit.called, "a momentary 429 waits for nobody")
        a.fail_notice("insufficient_quota: check your plan and billing")
        self.assertFalse(self.r.hit.called, "an empty balance is not fixed by time")

    def test_workflow_run_only_records_it(self):
        a = bare_codex(self.host, workflow=True)
        a.fail_notice("You've hit your usage limit. try again in 2 hours.")
        self.assertFalse(self.r.hit.called)
        self.assertAlmostEqual(a.limit_hit["at"], time.time() + 2 * H, delta=5)
        self.assertEqual(a.limit_hit["harness"], "codex")


class TaskHost:
    def __init__(self, st, prefs=None):
        self.st, self.preferences = st, prefs or {}
        self.agent_cfg = {"kind": "claude", "dir": str(st.root), "fence": False}
        self.lang, self.agent, self.task_label, self.brief_scope, self.busy, self.capabilities = "zh", None, None, None, None, None
        self.turn_lock = asyncio.Lock()
        self.finished, self.meter_state, self.notes = [], None, []
        self.quota_resume = qr.Resumer(self)

    def stopped(self):
        return False

    def activity(self, *a, **k):
        pass

    def agent_notice(self, t):
        self.notes.append(t)

    def task_finished(self, res):
        self.finished.append(res)


class Tasks(unittest.TestCase):
    """A workflow / dispatched run stopped by the limit reruns once after the reset (or at once on another account)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        (pathlib.Path(self.tmp.name) / "state").mkdir(mode=0o700)
        self.st = State(pathlib.Path(self.tmp.name) / "state")
        self.e = {"id": "w", "dir": str(self.st.root), "problems": [], "tsha": "x",
                  "task": {"title": {"zh": "周报", "en": "Weekly"}, "mode": "normal"}}

    def run_task(self, h, sch, trigger, outcome, rep=None):
        seen = {}

        async def fake_exec(kind, e_, research):
            seen.update(kind=kind, resume=sch.cur_resume)
            if outcome == "limit":
                sch.limit = {"harness": kind, "at": time.time() + 2 * H}
                return "", 1
            return "VERDICT: ok — 写好了", 0
        with patch.object(tasks, "find", return_value=self.e), patch.object(sch, "_exec", side_effect=fake_exec), \
                patch.object(sch, "_save", return_value=None), patch.object(tasks.Scheduler, "_check_report"), \
                patch.object(quota, "report", return_value=rep or quota.report(self.st.root, NOW, sources=[])), \
                patch.object(quota, "handshake", return_value=True), \
                patch.object(tasks, "uncertain_run", return_value=False):
            rid = sch.request("w", trigger) if trigger == "quota_resume" else None
            res = asyncio.run(sch.run("w", trigger))
        return res, seen, rid

    def test_rerun_after_the_reset_with_a_note(self):
        h = TaskHost(self.st)
        sch = tasks.Scheduler(h)
        h.scheduler = sch
        res, seen, _ = self.run_task(h, sch, "manual", "limit")
        self.assertEqual(res["verdict"], "fail")
        rec = h.quota_resume.items["task:w"]
        self.assertEqual((rec["harness"], rec["title"], rec.get("to")), ("claude", "周报", None))
        self.assertIn("工作流「周报」预计", h.notes[-1])
        with patch.object(sch, "request") as req:
            self.assertEqual(h.quota_resume.fire("task:w"), "fired")
            req.assert_called_once_with("w", "quota_resume")
        self.assertEqual(sch.resume["w"]["id"], rec["id"])
        self.assertEqual(h.quota_resume.items, {})
        res, seen, _ = self.run_task(h, sch, "quota_resume", "ok")
        self.assertEqual((res["verdict"], seen["resume"]["id"]), ("ok", rec["id"]))
        self.assertIn("自动重跑", qr.task_note(seen["resume"]))
        self.assertEqual(sch.resume, {}, "used once")

    def test_a_new_run_of_the_task_drops_the_waiting_rerun(self):
        h = TaskHost(self.st)
        sch = tasks.Scheduler(h)
        h.quota_resume.hit("task:w", "claude", time.time() + H, "周报")
        self.run_task(h, sch, "manual", "ok")
        self.assertEqual(h.quota_resume.items, {})

    def test_auto_switch_reruns_at_once_on_another_account(self):
        h = TaskHost(self.st, {"quota": {"auto_switch": True}})
        sch = tasks.Scheduler(h)
        c = {"harness": "claude", "status": "ok", "source": "f", "used_pct": 100.0, "reset_at": NOW + 86400, "source_at": NOW}
        x = {"harness": "codex", "status": "ok", "source": "f", "used_pct": 20.0, "reset_at": NOW + 86400, "source_at": NOW}
        rep = quota.report(self.st.root, NOW, sources=[c, x])
        self.run_task(h, sch, "manual", "limit", rep)
        rec = h.quota_resume.items["task:w"]
        self.assertEqual((rec["to"], rec["frm"]), ("codex", "claude"))
        self.assertLess(rec["at"], time.time() + qr.SWITCH_DELAY + 5)
        self.assertIn("改用 Codex", h.notes[-1])
        h.scheduler = sch
        with patch.object(sch, "request"):
            h.quota_resume.fire("task:w")
        res, seen, _ = self.run_task(h, sch, "quota_resume", "ok", rep)
        self.assertEqual((seen["kind"], res.get("harness")), ("codex", "codex"))
        self.assertIn("改用 codex 运行", res["line"])

    def test_pinned_harness_never_moves(self):
        h = TaskHost(self.st, {"quota": {"auto_switch": True}})
        sch = tasks.Scheduler(h)
        self.e["task"]["harness"] = "claude"
        c = {"harness": "claude", "status": "ok", "source": "f", "used_pct": 100.0, "reset_at": NOW + 86400, "source_at": NOW}
        x = {"harness": "codex", "status": "ok", "source": "f", "used_pct": 20.0, "reset_at": NOW + 86400, "source_at": NOW}
        self.run_task(h, sch, "manual", "limit", quota.report(self.st.root, NOW, sources=[c, x]))
        self.assertNotIn("to", h.quota_resume.items["task:w"])


if __name__ == "__main__":
    unittest.main()
