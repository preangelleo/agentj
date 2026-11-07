"""P204 (0.21, PROTOCOL §29): spend & speed of turns and background tasks — usage.py, the harness adapters, serve's turn
bookkeeping, `agentj usage`."""
import _hermetic  # noqa: F401
import io
import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from agentj import usage
from agentj.serve import Host


class St:
    def __init__(self, root):
        self.root = Path(root)

    def write_private(self, path, data):
        Path(path).write_bytes(data)
        os.chmod(path, 0o600)

    def exists(self):
        return True


class Prices(unittest.TestCase):
    def test_model_ids_normalise_and_price(self):
        for raw in ("claude-opus-5-5", "anthropic/claude-opus-5-5", "claude-opus-5-5[1m]", "claude-opus-5-5-20260401",
                    "us.anthropic.claude-opus-5-5", "Claude-Opus-5-5"):
            self.assertEqual(usage.norm_model(raw), "claude-opus-5-5", raw)
        self.assertIsNone(usage.norm_model("a b"))
        self.assertIsNone(usage.price_of("gpt-unknown-9"))
        p = usage.price_of("claude-sonnet-5-5")
        self.assertEqual((p["in"], p["out"]), (2.0, 10.0))

    def test_estimate_counts_cache_at_cache_prices(self):
        p = usage.PRICES["claude-opus-5-5"]
        usd = usage.estimate({"i": 1_000_000, "o": 1_000_000, "cr": 1_000_000, "cw": 1_000_000}, p)
        self.assertAlmostEqual(usd, 4 + 20 + 0.2 + 5)
        self.assertIsNone(usage.estimate({"i": 0, "o": 0, "cr": 0, "cw": 0}, p))
        self.assertIsNone(usage.estimate({"i": 5}, None))

    def test_owner_rows_win_and_can_be_removed(self):
        with tempfile.TemporaryDirectory() as d:
            st = St(d)
            usage.set_price(st, "gpt-6", {"in": 1.25, "out": 10, "cr": 0.125})
            self.assertEqual(usage.price_of("openai/gpt-6", st)["src"], "owner")
            self.assertEqual(oct((Path(d) / "prices.json").stat().st_mode & 0o777), "0o600")
            usage.set_price(st, "claude-opus-5-5", {"in": 1, "out": 2})
            self.assertEqual(usage.price_of("claude-opus-5-5", st)["in"], 1.0)
            usage.set_price(st, "claude-opus-5-5", None)
            self.assertEqual(usage.price_of("claude-opus-5-5", st)["src"], "built-in")
            with self.assertRaises(ValueError):
                usage.set_price(st, "x", {"in": -1, "out": 2})
            with self.assertRaises(ValueError):
                usage.set_price(st, "x", {"in": 1})            # out is required


class TurnMath(unittest.TestCase):
    def test_reported_dollars_win_over_the_estimate(self):
        t = usage.Turn("claude")
        t.put("result:1", {"i": 10, "o": 100, "cr": 1000, "cw": 0}, 0.0123, "claude-opus-5-5")
        u = t.finish()
        self.assertEqual(u["usd"], 0.0123)
        self.assertNotIn("est", u)
        self.assertEqual((u["i"], u["o"], u["cr"], u["cw"], u["m"], u["b"], u["h"]),
                         (10, 100, 1000, 0, "claude-opus-5-5", "api", "claude"))

    def test_estimate_when_the_harness_gives_no_dollars(self):
        t = usage.Turn("codex")
        t.put("codex", {"i": 1_000_000, "o": 0, "cr": 0, "cw": 0}, None, "claude-sonnet-5-5")
        u = t.finish()
        self.assertEqual((u["usd"], u["est"]), (2.0, True))

    def test_a_zero_cost_for_real_tokens_is_no_price(self):
        t = usage.Turn("opencode")
        t.put("msg:1", {"i": 1_000_000, "o": 0}, 0, "claude-sonnet-5-5")
        u = t.finish()
        self.assertEqual((u["usd"], u["est"]), (2.0, True))

    def test_a_zero_cost_for_real_tokens_is_no_price(self):
        t = usage.Turn("opencode")
        t.put("msg:1", {"i": 1_000_000, "o": 0}, 0, "claude-sonnet-5-5")
        u = t.finish()
        self.assertEqual((u["usd"], u["est"]), (2.0, True))

    def test_no_row_no_dollars(self):
        t = usage.Turn("codex")
        t.put("codex", {"i": 50, "o": 5}, None, "gpt-6")
        self.assertNotIn("usd", t.finish())

    def test_a_key_replaces_its_last_value(self):
        t = usage.Turn("codex")
        t.put("codex", {"i": 10, "o": 1})
        t.put("codex", {"i": 30, "o": 4})
        t.put("step:2", {"i": 1, "o": 1})
        u = t.finish()
        self.assertEqual((u["i"], u["o"]), (31, 5))

    def test_subscription_shows_quota_share_never_dollars_on_the_page(self):
        t = usage.Turn("claude", q0=40.0)
        t.put("result:1", {"i": 1, "o": 2}, 0.5, "claude-opus-5-5")
        u = t.finish(billing="sub", q1=41.5)
        self.assertEqual((u["b"], u["q"]), ("sub", 1.5))
        t = usage.Turn("claude", q0=90.0)          # the window reset in the middle: no number, not a negative one
        self.assertNotIn("q", t.finish(billing="sub", q1=3.0))

    def test_tool_time_and_speed(self):
        t = usage.Turn("claude")
        t.t0 -= 10
        t.phase, t.phase_t = "tool", time.monotonic() - 4
        t.set_phase("gen")
        t.put("result:1", {"o": 600})
        u = t.finish()
        self.assertAlmostEqual(u["tool_s"], 4, delta=0.3)
        self.assertAlmostEqual(u["tps"], 600 / 6, delta=5)

    def test_adapters_token_shapes(self):
        self.assertEqual(usage.tokens_claude({"input_tokens": 2, "output_tokens": 3, "cache_read_input_tokens": 13610,
                                              "cache_creation_input_tokens": 3470}), {"i": 2, "o": 3, "cr": 13610, "cw": 3470})
        self.assertEqual(usage.tokens_codex({"inputTokens": 100, "cachedInputTokens": 60, "outputTokens": 7,
                                             "reasoningOutputTokens": 3}), {"i": 40, "o": 10, "cr": 60, "cw": 0})
        self.assertEqual(usage.tokens_opencode({"input": 5, "output": 6, "reasoning": 1, "cache": {"read": 7, "write": 8}}),
                         {"i": 5, "o": 7, "cr": 7, "cw": 8})
        self.assertEqual(usage.tokens_claude({"input_tokens": True, "output_tokens": -3}), {"i": 0, "o": 0, "cr": 0, "cw": 0})

    def test_a_background_claude_run(self):
        u = usage.from_claude_result({"type": "result", "usage": {"input_tokens": 4, "output_tokens": 9}, "duration_ms": 2500,
                                      "total_cost_usd": 0.01, "modelUsage": {"claude-haiku-5-5": {"outputTokens": 9}}})
        self.assertEqual((u["s"], u["o"], u["usd"], u["m"]), (2.5, 9, 0.01, "claude-haiku-5-5"))


class Log(unittest.TestCase):
    def test_record_summary_and_cli_never_add_subscription_dollars(self):
        with tempfile.TemporaryDirectory() as d:
            st = St(d)
            usage.record(st, "turn", {"s": 3.0, "i": 10, "o": 20, "cr": 0, "cw": 0, "usd": 0.5, "b": "api"})
            usage.record(st, "turn", {"s": 2.0, "i": 1, "o": 2, "usd": 9.0, "b": "sub", "q": 1.5})
            usage.record(st, "task", {"s": 5.0, "i": 3, "o": 4, "usd": 0.25, "est": True, "b": "api"}, task="weekly")
            f = next((Path(d) / "usage").iterdir())
            self.assertEqual(oct(f.stat().st_mode & 0o777), "0o600")
            self.assertEqual(oct((Path(d) / "usage").stat().st_mode & 0o777), "0o700")
            s = usage.summary(st, 7)
            self.assertEqual(s["total"]["n"], 3)
            self.assertAlmostEqual(s["total"]["usd"], 0.75)
            self.assertTrue(s["total"]["est"])
            self.assertEqual((s["total"]["q"], s["total"]["sub"]), (1.5, 1))
            self.assertEqual(s["by_kind"]["task"]["n"], 1)
            self.assertEqual(usage.today(st)["n"], 3)
            for lang, words in (("zh", ("今天", "后台任务", "订阅额度 1.5%", "≈$0.75", "不外发")),
                                ("en", ("Today", "background tasks", "subscription quota 1.5%", "never sent"))):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = usage._usage(st, SimpleNamespace(action=None, json=False, week=False, days=1, lang=lang))
                self.assertEqual(rc, 0)
                for w in words:
                    self.assertIn(w, buf.getvalue(), (lang, w))
            buf = io.StringIO()
            with redirect_stdout(buf):
                usage._usage(st, SimpleNamespace(action=None, json=True, week=True, days=1, lang="en"))
            self.assertEqual(json.loads(buf.getvalue())["days"], 7)

    def test_price_cli(self):
        with tempfile.TemporaryDirectory() as d:
            st = St(d)
            a = SimpleNamespace(action="price", model="gpt-6", remove=False, input=1.0, output=8.0, cache_read=0.1,
                                cache_write=None, lang="en", json=False)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(usage._usage(st, a), 0)
                a.input = None
                self.assertEqual(usage._usage(st, a), 2)
            buf = io.StringIO()
            with redirect_stdout(buf):
                usage._usage(st, SimpleNamespace(action="prices", json=True, lang="en"))
            self.assertEqual(json.loads(buf.getvalue())["prices"]["gpt-6"]["src"], "owner")


def host_ns(tmp, h5=None):
    st = St(tmp)
    h = SimpleNamespace(st=st, turn_acc=None, phase_now=None, phase_timer=None, usage_today_cache=None,
                        meter_state={"h5": h5}, agent=SimpleNamespace(kind="claude"), status_changed=Mock())
    for name in ("_quota_pct", "turn_use", "_turn_use_done", "agent_phase", "usage_today", "_phase_send"):
        setattr(h, name, getattr(Host, name).__get__(h))
    return h


class HostBookkeeping(unittest.TestCase):
    def test_a_turn_on_an_api_key_records_dollars_and_returns_its_use(self):
        with tempfile.TemporaryDirectory() as d:
            h = host_ns(d)
            h.turn_acc = usage.Turn("claude", h._quota_pct())
            h.turn_use("result:1", {"i": 5, "o": 50, "cr": 0, "cw": 0}, 0.02, "claude-opus-5-5")
            use = h._turn_use_done()
            self.assertEqual((use["b"], use["usd"], use["o"]), ("api", 0.02, 50))
            self.assertIsNone(h.turn_acc)
            self.assertEqual(h.usage_today()["n"], 1)

    def test_a_subscription_turn_carries_the_quota_share(self):
        with tempfile.TemporaryDirectory() as d:
            h = host_ns(d, {"pct": 10.0})
            h.turn_acc = usage.Turn("claude", h._quota_pct())
            h.turn_use("result:1", {"i": 5, "o": 50}, 0.02, "claude-opus-5-5")
            h.meter_state["h5"] = {"pct": 10.4}
            use = h._turn_use_done()
            self.assertEqual((use["b"], use["q"]), ("sub", 0.4))

    def test_nothing_measured_is_not_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            h = host_ns(d)
            h.turn_acc = usage.Turn("claude")
            self.assertIsNone(h._turn_use_done())
            self.assertFalse((Path(d) / "usage").exists())
            h.turn_acc = usage.Turn("claude")
            h.turn_acc.t0 -= 5                       # a shared session reports nothing: the page gets its time only
            self.assertEqual(h._turn_use_done(), {"s": 5.0, "h": "claude"})
            self.assertFalse((Path(d) / "usage").exists())

    def test_phase_outside_a_turn_is_ignored_and_reset_is_sent(self):
        with tempfile.TemporaryDirectory() as d:
            h = host_ns(d)
            h.agent_phase("tool", "Bash")             # no turn: nothing
            self.assertIsNone(h.phase_now)
            h.turn_acc = usage.Turn("claude")
            h.agent_phase("tool", "Bash")            # no running loop in this test: state kept, the send is skipped
            self.assertEqual(h.phase_now, ("tool", "Bash"))
            h.agent_phase("tool", "Bash")
            h._turn_use_done()
            self.assertIsNone(h.phase_now)

    def test_status_message_carries_the_phase_only_while_working(self):
        h = SimpleNamespace(phase_now=("tool", "Bash"), stopped=lambda: False, agent=SimpleNamespace(kind="claude",
                            status="working"), asks={}, questions={}, telegram=None,
                            st=SimpleNamespace(agent_name=lambda: None))
        h.eff_status = Host.eff_status.__get__(h)
        h._open_question = Host._open_question.__get__(h)
        m = Host._status_msg(h)
        self.assertEqual((m["ph"], m["tool"]), ("tool", "Bash"))
        h.agent.status = "idle"
        self.assertNotIn("ph", Host._status_msg(h))


class BackgroundTasks(unittest.TestCase):
    def test_a_run_use_goes_to_its_page_and_the_log_and_subscription_drops_dollars(self):
        from agentj.tasks import Scheduler
        sch = SimpleNamespace(run_use={"s": 4.0, "o": 9, "usd": 0.01, "b": "api"},
                              host=SimpleNamespace(_quota_pct=lambda: 12.0))
        res = {}
        Scheduler._take_use(sch, res)
        self.assertEqual(res["use"]["b"], "sub")
        self.assertIsNone(sch.run_use)
        with tempfile.TemporaryDirectory() as d:
            st = St(d)
            st.log = Mock()
            h = SimpleNamespace(st=st, grants={}, end_grant=Mock(), emit=Mock(), activity=Mock(), _remember=Mock(), _post=Mock(),
                                _send_legacy=None, _render=None, hist_add=Mock(return_value={"id": 5}), media_later=Mock(),
                                hist_update=Mock(), push_notify=Mock(), usage_today_cache=("x", {}))
            Host.task_finished(h, {"id": "weekly", "title": "周报", "verdict": "pass", "line": "ok", "use": {"s": 4.0, "o": 9, "b": "api", "usd": 0.01}})
            h.hist_update.assert_called_with(5, use={"s": 4.0, "o": 9, "b": "api", "usd": 0.01})
            self.assertIsNone(h.usage_today_cache)
            self.assertEqual(usage.summary(st, 1)["by_kind"]["task"]["n"], 1)
            h.push_notify.assert_called_with("reply")


class ClaudeAdapter(unittest.TestCase):
    def agent(self):
        from agentj.agent import ClaudeAgent
        host = SimpleNamespace(turn_use=Mock(), agent_phase=Mock(), agent_text=Mock(), agent_status=Mock(),
                               st=SimpleNamespace(set_agent_session=Mock(), agent_session=lambda k: None, log=Mock()))
        a = ClaudeAgent.__new__(ClaudeAgent)
        a.host, a.cfg, a.kind, a.capture, a.replay, a.turn_done, a.halting = host, {"dir": "/tmp"}, "claude", None, False, None, False
        a.last_result, a.init, a.status = None, {}, "working"
        a.cur_model = lambda: "claude-opus-5-5"
        return a, host

    def test_tool_use_then_text_and_cumulative_cost(self):
        a, host = self.agent()
        a.on_event({"type": "assistant", "message": {"model": "claude-opus-5-5", "content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}})
        host.agent_phase.assert_called_with("tool", "Bash")
        a.on_event({"type": "user", "message": {"content": [{"type": "tool_result", "content": "x"}]}})
        host.agent_phase.assert_called_with("gen", None)
        a.on_event({"type": "assistant", "message": {"model": "claude-opus-5-5", "content": [{"type": "text", "text": "hi"}]}})
        host.agent_text.assert_called_with("hi")
        a.cost_seen = 0.0
        a.on_event({"type": "result", "subtype": "success", "usage": {"input_tokens": 2, "output_tokens": 3},
                    "total_cost_usd": 0.0034})
        a.on_event({"type": "result", "subtype": "success", "usage": {"input_tokens": 2, "output_tokens": 3},
                    "total_cost_usd": 0.0042})
        (k1, t1, usd1, m1), (k2, t2, usd2, m2) = [c.args for c in host.turn_use.call_args_list]
        self.assertNotEqual(k1, k2)
        self.assertEqual((usd1, usd2, m2), (0.0034, 0.0008, "claude-opus-5-5"))
        self.assertEqual(t1["o"], 3)

    def test_a_scheduled_run_does_not_touch_the_chat_turn(self):
        a, host = self.agent()
        a.cfg["_workflow_ceo"] = True
        a.use_put("result:1", {"o": 3}, 0.1, "claude-opus-5-5")
        host.turn_use.assert_not_called()
        self.assertEqual(a.task_turn.finish()["o"], 3)


class CodexAdapter(unittest.TestCase):
    def test_turn_is_thread_total_minus_total_at_turn_start(self):
        from agentj.agent_codex import CodexAgent
        a = CodexAgent.__new__(CodexAgent)
        host = SimpleNamespace(turn_use=Mock(), agent_phase=Mock())
        a.host, a.cfg, a.kind, a.tid, a.research, a.persist = host, {}, "codex", "th1", False, True
        a.items, a.turn_done, a.thread = {}, None, {}
        a.cur_model = lambda: "gpt-6"
        a.meter = Mock()
        a.context_metadata = None
        tot = lambda i, o: {"inputTokens": i, "cachedInputTokens": 0, "outputTokens": o, "reasoningOutputTokens": 0}
        a._on_note("turn/started", {"threadId": "th1", "turn": {"id": "t1"}})
        a._on_note("thread/tokenUsage/updated", {"threadId": "th1", "tokenUsage": {"total": tot(100, 10), "last": tot(100, 10)}})
        self.assertEqual(host.turn_use.call_args.args[1]["i"], 100)
        a._on_note("turn/started", {"threadId": "th1", "turn": {"id": "t2"}})
        a._on_note("thread/tokenUsage/updated", {"threadId": "th1", "tokenUsage": {"total": tot(250, 30), "last": tot(150, 20)}})
        t = host.turn_use.call_args.args[1]
        self.assertEqual((t["i"], t["o"]), (150, 20))
        a._on_note("item/started", {"threadId": "th1", "item": {"type": "commandExecution", "id": "c1", "command": "ls"}})
        host.agent_phase.assert_called_with("tool", "Bash")
        a._on_note("item/completed", {"threadId": "th1", "item": {"type": "commandExecution", "id": "c1"}})
        host.agent_phase.assert_called_with("gen", None)


if __name__ == "__main__":
    unittest.main()
