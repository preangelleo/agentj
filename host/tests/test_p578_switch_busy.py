"""P578 (Avon 10-29, OpenCode 2.2 · Ark Agent Plan): the switch was rolled back as 「OpenCode 回答后一直没有空下来」 after 262 s.

The live run (reports/qa/p578/) shows OpenCode 2.2.1 + Ark ends its turn with the real `session.execution.succeeded` and the
adapter goes idle at once; what kept the new Agent "not idle" is work after the answer — the next queued message (her WeChat
channel was busy), a long answer, a card on the phone — and a serve that cannot even answer its own status while the computer
is overloaded (#404). Verified here, with the fake control socket / service / clock of test_p155:
- answered then busy past IDLE_WAIT → kept (receipt `ok`, kept=busy, one restart, never rolled back), the receipt says how to
  go back; a card from the new Agent's turn counts as alive too (kept=card);
- serve not answering (stalled loop / restart) is not counted: the verdict waits for it and gives RECONNECT_GRACE after;
  if it never answers again → `no_reply` with detail `lost`, describing unavailable host status without assigning a cause;
- a new Agent that truly never answers still rolls back (a phone-only owner keeps someone to talk to), and the roll-back
  receipt now names both ways on (stay / try again with the exact command);
- `agentj agent switch` from the web terminal no longer says 「这一轮结束后」 (there is no turn): it says right away when idle.
Run (in host): .venv/bin/python -m unittest discover -s tests -p 'test_p578_switch_busy.py'
"""
import _hermetic  # noqa: F401,I001
import os
import pathlib
import shlex
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import ctl_auth, switch_harness as sw  # noqa: E402
from test_p155_switch_harness import UP, Clock, FakeServe, fake_probe, mk_state  # noqa: E402


class BusyServe(FakeServe):
    """After the restart the new Agent answers the hello and then stays `working` (the next queued turn) or `waiting`
    (a card); `lost` = (from, to) clock seconds during which the control socket does not answer."""

    def __init__(self, st, kind, clock, *, after="working", answers=True, lost=None, turn=True):
        super().__init__(st, kind)
        self.clock, self.after, self.lost, self.turn = clock, after, lost, turn
        self.behaviour = {}
        self.new_answers = answers

    def restart(self):
        super().restart()
        if self.kind != "claude":
            self.answers = self.new_answers

    def __call__(self, req):
        if req["cmd"] == "harness_hello" and self.restarts == 1:
            self.hello_t = self.clock.t
        h = getattr(self, "hello_t", None)          # `lost` counts from the new Agent's hello (the verification itself)
        if self.lost and h is not None and self.lost[0] <= self.clock.t - h < self.lost[1] and req["cmd"] == "status":
            return None
        r = super().__call__(req)
        if req["cmd"] == "status" and self.restarts == 1 and self.hellos and self.kind != "claude":
            r["agent_status"] = self.after if (self.answers or self.after == "waiting") else "idle"
            if self.turn:
                r.update(agent_turn_start_at=1001, agent_turn_end_at=0)
        return r


class Kept(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p578-st-")
        self.addCleanup(self.tmp.cleanup)
        unit = pathlib.Path(self.tmp.name) / "units"
        p = mock.patch.object(sw, "receipt_path", side_effect=lambda name=sw.RECEIPT: unit / ("agentj." + name))
        p.start()
        self.addCleanup(p.stop)
        self.clock = Clock()

    def run_job(self, st, fake, target, model=None):
        return sw.worker(st, target, model, probe=fake_probe(), ctl=fake, restart=fake.restart, status=UP,
                         ready=lambda st, limit: "idle", sleep=self.clock.sleep, clock=self.clock)

    def test_answered_then_busy_is_kept_not_rolled_back(self):
        st = mk_state(self.tmp.name, "codex")
        fake = BusyServe(st, "codex", self.clock)
        self.assertEqual(self.run_job(st, fake, "opencode", "ark-agent-plan/doubao-seed-2.1-pro"), 0)
        self.assertEqual(st.agent_config()["kind"], "opencode")
        self.assertEqual(fake.restarts, 1)                                # no roll-back restart
        rec = sw.read_receipt(st)
        self.assertEqual((rec["status"], rec["kept"]), ("ok", "busy"))
        self.assertGreaterEqual(rec["waited"], sw.IDLE_WAIT)
        self.assertNotIn("rolled_back", [h[1]["outcome"] for h in fake.hellos])
        zh = sw._say_receipt(rec, "zh")
        self.assertIn("✓ Codex → OpenCode", zh)
        self.assertIn("已改用火山 Agent Plan", zh)
        self.assertIn("还在忙", zh)
        self.assertIn("切回 Codex", zh)
        self.assertIn("agentj agent switch --undo", zh)
        self.assertNotIn("没有空下来", zh)
        self.assertIn("still busy", sw._say_receipt(rec, "en"))
        self.assertEqual(sw.result_code(rec), 0)
        self.assertTrue(fake.sent and "还在忙" in fake.sent[-1])        # the phone notice is the receipt

    def test_a_card_from_the_new_agent_is_alive(self):
        st = mk_state(self.tmp.name, "codex")
        fake = BusyServe(st, "codex", self.clock, after="waiting", answers=False)
        self.assertEqual(self.run_job(st, fake, "opencode", "ark-agent-plan/doubao-seed-2.1-pro"), 0)
        rec = sw.read_receipt(st)
        self.assertEqual((rec["status"], rec["kept"]), ("ok", "card"))
        self.assertIn("卡片", sw._say_receipt(rec, "zh"))

    def test_serve_not_answering_is_not_counted(self):
        st = mk_state(self.tmp.name, "codex")
        # the control socket goes quiet right after the hello for longer than HELLO_WAIT, then the answer is there and idle
        fake = BusyServe(st, "codex", self.clock, after="idle", lost=(0, sw.HELLO_WAIT + 60))
        self.assertEqual(self.run_job(st, fake, "opencode", "ark-agent-plan/doubao-seed-2.1-pro"), 0)
        rec = sw.read_receipt(st)
        self.assertEqual(rec["status"], "ok")
        self.assertEqual(fake.restarts, 1)
        self.assertGreater(self.clock.t - fake.hello_t, sw.HELLO_WAIT)   # the verdict really waited through the stall

    def test_lost_then_back_still_gets_grace(self):
        now = [0.0]
        polls = []

        def ctl(req):
            if req["cmd"] == "harness_hello":
                return {"ok": True, "at": 500.0}
            polls.append(now[0])
            if now[0] < sw.HELLO_WAIT + 10:
                return None                                            # stalled serve past the whole wait
            # back: the session is visibly in its turn, no word yet — answers 10 s later
            return {"ok": True, "agent_status": "working", "agent_said_at": 600.0 if now[0] >= sw.HELLO_WAIT + 20 else 0,
                    "agent_phase": "turn"}

        def sleep(s):
            now[0] += s
        r = sw._hello(ctl, {"outcome": "ok", "from": "codex", "to": "opencode"}, sleep, lambda: now[0])
        self.assertEqual((r or {}).get("ok"), True)                    # answered (busy) — not no_reply

    def test_never_back_is_no_reply_lost(self):
        now = [0.0]

        def ctl(req):
            return {"ok": True, "at": 500.0} if req["cmd"] == "harness_hello" else None

        def sleep(s):
            now[0] += s
        r = sw._hello(ctl, {"outcome": "ok", "from": "codex", "to": "opencode"}, sleep, lambda: now[0])
        self.assertEqual((r["reason"], r["detail"]), ("no_reply", "lost"))
        self.assertGreaterEqual(now[0], sw.HELLO_MAX + sw.LOST_MAX)
        zh = sw.reason_text("no_reply", "opencode", "zh", "lost")
        self.assertIn("Agent J 主机查不到状态", zh)
        self.assertNotIn("不是 OpenCode 的问题", zh)
        self.assertIn("could not be read", sw.reason_text("no_reply", "opencode", "en", "lost"))
        self.assertIsNotNone(sw.hello_prompt("codex", {"outcome": "rolled_back", "from": "codex", "to": "opencode",
                                                       "reason": "no_reply", "detail": "lost"}))

    def test_true_silence_still_rolls_back_with_both_ways_on(self):
        st = mk_state(self.tmp.name, "codex")
        raw = st.config()["agent"]
        fake = BusyServe(st, "codex", self.clock, after="idle", answers=False, turn=False)
        self.assertEqual(self.run_job(st, fake, "opencode", "ark-agent-plan/doubao-seed-2.1-pro"), 2)
        self.assertEqual(st.config()["agent"], raw)
        rec = sw.read_receipt(st)
        self.assertEqual((rec["status"], rec["reason"]), ("rolled_back", "no_reply"))
        zh = sw._say_receipt(rec, "zh")
        self.assertIn("已切回 Codex", zh)
        self.assertIn("两条路", zh)
        self.assertIn("再切一次 OpenCode", zh)
        self.assertIn("agentj agent switch opencode --model ark-agent-plan/doubao-seed-2.1-pro", zh)
        self.assertIn("Two ways on", sw._say_receipt(rec, "en"))

    def hello_timeline(self, status, **kw):
        def ctl(req):
            return {"ok": True, "at": 500.0} if req["cmd"] == "harness_hello" else status(self.clock.t)
        return sw._hello(ctl, {"from": "codex", "to": "opencode"}, self.clock.sleep, self.clock, **kw)

    def test_answer_then_permanent_disconnect_is_unconfirmed(self):
        r = self.hello_timeline(lambda t: {"ok": True, "agent_said_at": 600, "agent_status": "working"} if t < 2 else None)
        self.assertEqual((r["reason"], r["detail"]), ("no_reply", "lost"))
        self.assertNotIn("ok", r)

    def test_bad_status_with_stale_answer_is_not_alive(self):
        r = self.hello_timeline(lambda t: {"ok": t < 2, "agent_said_at": 600, "agent_status": "idle" if t >= 2 else "working"})
        self.assertEqual(r["detail"], "lost")

    def test_answer_then_failure_is_not_alive(self):
        r = self.hello_timeline(lambda t: {"ok": True, "agent_said_at": 600, "agent_status": "working" if t < 2 else "idle",
                                         "agent_phase": "turn" if t < 2 else "failed", "agent_turn_failed": t >= 2})
        self.assertEqual((r["reason"], r["detail"]), ("no_reply", "failed"))

    def test_current_down_or_auth_outranks_old_reply(self):
        for status, err in (("down", None), ("stopped", None), ("failed", None), ("working", "auth"), ("idle", "auth")):
            with self.subTest(status=status, err=err):
                self.clock = Clock()
                r = self.hello_timeline(lambda t: {"ok": True, "agent_said_at": 600,
                    "agent_status": "working" if t < 2 else status, "agent_err": None if t < 2 else err})
                self.assertEqual((r["reason"], r["detail"]), ("no_reply", "failed"))

    def test_disappeared_card_then_failed_turn_is_not_alive(self):
        r = self.hello_timeline(lambda t: {"ok": True, "agent_status": "waiting" if t < 2 else "idle",
                                         "agent_turn_start_at": 500, "agent_turn_end_at": 0 if t < 2 else 501,
                                         "agent_turn_failed": t >= 2})
        self.assertEqual((r["reason"], r["detail"]), ("no_reply", "failed"))

    def test_disappeared_card_still_working_does_not_count(self):
        r = self.hello_timeline(lambda t: {"ok": True, "agent_status": "waiting" if t < 2 else "working", "agent_turn_start_at": 500})
        self.assertEqual(r["reason"], "no_reply")

    def test_disconnect_reports_pending_then_recovers(self):
        pending = []
        r = self.hello_timeline(lambda t: None if t < sw.HELLO_WAIT + 10 else
                                {"ok": True, "agent_said_at": 600, "agent_status": "idle"},
                                pending=lambda: pending.append(self.clock.t))
        self.assertIsNone(r)
        self.assertEqual(len(pending), 1)
        self.assertGreaterEqual(pending[0], sw.HELLO_WAIT)
        rec = {"status": "switching", "detail": "lost", "target": "opencode", "from": "codex", "at": 0}
        self.assertEqual(sw.result_code(rec), 4)
        self.assertIn("还没确认", sw._say_receipt(rec, "zh"))
        self.assertIn("agentj agent switch --undo", sw._say_receipt(rec, "en"))

    def test_worker_pending_receipt_then_recovery_and_final_receipt(self):
        st = mk_state(self.tmp.name, "codex")
        fake = BusyServe(st, "codex", self.clock, after="idle", lost=(0, sw.HELLO_WAIT + 10))
        receipts = []
        original = sw.write_receipt
        def capture(state, rec):
            result = original(state, rec)
            receipts.append(dict(result))
            return result
        with mock.patch.object(sw, "write_receipt", side_effect=capture):
            self.assertEqual(self.run_job(st, fake, "opencode", "ark/model"), 0)
        pending = next(r for r in receipts if r.get("detail") == "lost")
        self.assertEqual(pending["status"], "switching")
        self.assertEqual(sw.result_code(pending), 4)
        self.assertIn("还没确认", sw._say_receipt(pending, "zh"))
        self.assertEqual(receipts[-1]["status"], "ok")
        self.assertNotIn("detail", receipts[-1])
        self.assertEqual(fake.restarts, 1)

    def test_retry_command_rejects_shell_syntax_and_quotes_argv(self):
        rec = {"target": "opencode", "from": "codex"}
        for model in ("ark/model;id", "ark/model$(id)", "ark/model`id`", "ark/model|id", "ark/model>out", "ark/model&x"):
            with self.subTest(model=model):
                self.assertIsNone(sw.check_spec({"kind": "switch", "target": "opencode", "model": model})[0])
                self.assertNotIn(model, sw._choices({**rec, "model": model}, "en"))
        model = "ark-agent-plan/doubao-seed-2.1-pro"
        with mock.patch.object(sw.shlex, "quote", wraps=shlex.quote) as quote:
            en = sw._choices({**rec, "model": model}, "en")
        quote.assert_any_call(model)
        quote.assert_any_call("opencode")
        self.assertIn("Web Terminal", en)

    def test_choices_never_echo_an_odd_model(self):
        rec = {"status": "rolled_back", "target": "opencode", "from": "codex", "at": 0, "reason": "no_reply",
               "model": "x; rm -rf ~"}
        self.assertNotIn("rm -rf", sw._say_receipt(rec, "zh"))
        self.assertEqual(sw._choices({**rec, "undo": True}, "zh"), "")


class QuotaClock(unittest.TestCase):
    """#402 (Avon 10-29): the card said 「预计 20:11」 (host clock) while the banner said 「预计 17:11」 (phone clock)."""

    def test_host_text_is_zone_free_and_names_its_clock(self):
        from agentj import quota_resume as qr
        now = 1_800_000_000.0
        zh, en = qr.host_when(now + 3 * 3600 + 17 * 60, "zh", now), qr.host_when(now + 3 * 3600 + 17 * 60, "en", now)
        self.assertIn("约 3 小时 17 分钟后", zh)
        self.assertIn("Agent J 主机时间 " + qr.hhmm(now + 3 * 3600 + 17 * 60, "zh", now), zh)
        self.assertIn("in about 3 h 17 min", en)
        self.assertIn("约 5 分钟后", qr.host_when(now + 300, "zh", now))


class Wording(unittest.TestCase):
    def test_terminal_says_right_away_agent_says_after_this_turn(self):
        r = {"ok": True, "target": "opencode", "from": "codex"}
        clean = {k: v for k, v in os.environ.items() if k not in ctl_auth.AGENT_ENV}
        with mock.patch.dict(os.environ, clean, clear=True):
            zh = sw._say(r, "zh")
            self.assertNotIn("这一轮结束后", zh)
            self.assertIn("马上", zh)
            self.assertIn("right away", sw._say(r, "en"))
        with mock.patch.dict(os.environ, {**clean, "AGENTJ_FENCED": "1"}, clear=True):
            self.assertIn("这一轮结束后", sw._say(r, "zh"))
            self.assertIn("when this turn ends", sw._say(r, "en"))


if __name__ == "__main__":
    unittest.main()
