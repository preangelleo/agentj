"""0.30.73 (PROTOCOL §29 `usd_est`): a price-table estimate for a turn the harness reported tokens but no dollars for.

It is a separate, display-only field. It never becomes `usd`, never enters a total, and is absent when the harness gave a
figure, when the model has no row, on a subscription login and when the token report is incomplete."""
import _hermetic  # noqa: F401
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from agentj import usage
from agentj.serve import Host

AJ = Path(__file__).resolve().parents[2]
CODEX = {"inputTokens": 2_000_000, "cachedInputTokens": 1_000_000, "outputTokens": 900_000, "reasoningOutputTokens": 100_000}


class St:
    def __init__(self, root):
        self.root = Path(root)

    def write_private(self, path, data):
        Path(path).write_bytes(data)
        os.chmod(path, 0o600)


def host_ns(d, h5=None):
    h = SimpleNamespace(st=St(d), turn_acc=None, phase_now=None, phase_timer=None, usage_today_cache=None,
                        meter_state={"h5": h5}, agent=SimpleNamespace(kind="codex"), status_changed=Mock())
    for name in ("_quota_pct", "turn_use", "_turn_use_done", "agent_phase", "usage_today", "_phase_send"):
        setattr(h, name, getattr(Host, name).__get__(h))
    return h


class TurnField(unittest.TestCase):
    def test_reported_dollars_only_the_reported_figure(self):
        t = usage.Turn("opencode")
        t.put("msg:1", usage.tokens_codex(CODEX), 0.5, "gpt-6.1-sol")
        u = t.finish()
        self.assertEqual(u["usd"], 0.5)
        self.assertNotIn("usd_est", u)
        self.assertNotIn("est", u)

    def test_a_reported_zero_is_still_a_report(self):
        t = usage.Turn("opencode")
        t.put("msg:1", {"i": 1_000_000, "o": 0, "cr": 0, "cw": 0}, 0, "gpt-6.1-sol")
        u = t.finish()
        self.assertEqual(u["usd"], 0.0)
        self.assertNotIn("usd_est", u)

    def test_no_report_and_a_price_row_only_the_estimate_field(self):
        t = usage.Turn("codex")
        t.put("codex:t1", usage.tokens_codex(CODEX), None, "gpt-6.1-sol")
        u = t.finish()
        self.assertEqual(u["usd_est"], 12.10)
        self.assertNotIn("usd", u)
        self.assertNotIn("est", u)
        self.assertEqual((u["i"], u["o"], u["cr"], u["cw"], u["b"]), (1_000_000, 1_000_000, 1_000_000, 0, "api"))

    def test_no_price_row_neither_field(self):
        t = usage.Turn("codex")
        t.put("codex:t1", usage.tokens_codex(CODEX), None, "some-model-without-a-row")
        u = t.finish()
        self.assertNotIn("usd", u)
        self.assertNotIn("usd_est", u)
        t = usage.Turn("codex")
        t.put("codex:t1", usage.tokens_codex(CODEX), None, None)      # the harness named no model
        self.assertNotIn("usd_est", t.finish())

    def test_a_partly_reported_turn_stays_unknown(self):
        t = usage.Turn("opencode")
        t.put("msg:1", {"i": 1_000_000, "o": 1, "cr": 0, "cw": 0}, 0.3, "gpt-6.1-sol")
        t.put("msg:2", {"i": 1_000_000, "o": 1, "cr": 0, "cw": 0}, None, "gpt-6.1-sol")
        u = t.finish()
        self.assertNotIn("usd", u)
        self.assertNotIn("usd_est", u)

    def test_an_incomplete_token_report_is_not_priced(self):
        t = usage.Turn("codex")
        t.put("codex:t1", {"o": 1_000_000}, None, "gpt-6.1-sol")       # no input count: the sum would be a guess
        self.assertNotIn("usd_est", t.finish())

    def test_a_subscription_login_gets_no_dollar_figure(self):
        t = usage.Turn("codex", 10.0)
        t.put("codex:t1", usage.tokens_codex(CODEX), None, "gpt-6.1-sol")
        u = t.finish(billing="sub", q1=10.5)
        self.assertEqual((u["b"], u["q"]), ("sub", 0.5))
        self.assertNotIn("usd_est", u)

    def test_the_owner_row_wins_and_a_broken_state_gives_no_estimate(self):
        with tempfile.TemporaryDirectory() as d:
            st = St(d)
            usage.set_price(st, "gpt-6.1-sol", {"in": 1.0, "out": 1.0, "cr": 1.0, "cw": 1.0})
            t = usage.Turn("codex")
            t.put("codex:t1", usage.tokens_codex(CODEX), None, "openai/gpt-6.1-sol")
            self.assertEqual(t.finish(st=st)["usd_est"], 3.0)
        t = usage.Turn("codex")
        t.put("codex:t1", usage.tokens_codex(CODEX), None, "gpt-6.1-sol")
        u = t.finish(st=SimpleNamespace())                             # a stand-in without a state root never raises
        self.assertEqual(u["o"], 1_000_000)
        self.assertNotIn("usd_est", u)


class NeverMoney(unittest.TestCase):
    """The estimate reaches the page and the local log line, and nothing that adds dollars up."""

    def test_the_page_and_the_log_carry_it_and_no_total_counts_it(self):
        with tempfile.TemporaryDirectory() as d:
            h = host_ns(d)
            h.turn_acc = usage.Turn("codex", h._quota_pct())
            h.turn_use("codex:t1", usage.tokens_codex(CODEX), None, "gpt-6.1-sol")
            use = h._turn_use_done()
            self.assertEqual(use["usd_est"], 12.10)
            self.assertNotIn("usd", use)
            row = json.loads(next((Path(d) / "usage").iterdir()).read_text())
            self.assertEqual(row["usd_est"], 12.10)
            self.assertNotIn("usd", row)
            for a in (usage.summary(h.st, 1)["total"], h.usage_today()):
                self.assertEqual((a["n"], a["usd"], a["est"], a["no_usd"]), (1, 0.0, False, 1))
                self.assertNotIn("usd_est", a)
            self.assertIn("—", usage.line(usage.summary(h.st, 1)["total"]))

    def test_a_host_turn_on_a_subscription_login_has_none(self):
        with tempfile.TemporaryDirectory() as d:
            h = host_ns(d, {"pct": 10.0})
            h.turn_acc = usage.Turn("codex", h._quota_pct())
            h.turn_use("codex:t1", usage.tokens_codex(CODEX), None, "gpt-6.1-sol")
            use = h._turn_use_done()
            self.assertEqual(use["b"], "sub")
            self.assertNotIn("usd_est", use)

    def take(self, use, **host):
        from agentj.tasks import Scheduler
        res = {}
        Scheduler._take_use(SimpleNamespace(run_use=dict(use), host=SimpleNamespace(**host)), res)
        return res["use"]

    def test_a_scheduled_run_on_a_subscription_login_drops_it(self):
        run = {"s": 4.0, "h": "codex", "i": 1, "o": 9, "usd_est": 0.01, "b": "api"}
        use = self.take(run, _quota_pct=lambda: 12.0, agent_cfg={"kind": "codex"})
        self.assertEqual(use["b"], "sub")
        self.assertNotIn("usd_est", use)
        use = self.take(run, _quota_pct=lambda: None, agent_cfg={"kind": "codex"})
        self.assertEqual((use["b"], use["usd_est"]), ("api", 0.01), "the main Agent's own harness, no subscription reading")

    def test_a_scheduled_run_keeps_the_estimate_only_when_api_billing_is_certain(self):
        """Security review P2: the main Agent on an API key, the task on another harness that may be a subscription login."""
        run = {"s": 4.0, "h": "codex", "i": 1, "o": 9, "usd_est": 0.01, "b": "api"}
        unsure = {
            "another harness than the main Agent's": dict(_quota_pct=lambda: None, agent_cfg={"kind": "claude"}),
            "no main Agent": dict(_quota_pct=lambda: None, agent_cfg=None),
            "a host that cannot tell": dict(agent_cfg={"kind": "codex"}),
            "another harness, subscription reading": dict(_quota_pct=lambda: 3.0, agent_cfg={"kind": "claude"}),
        }
        for name, host in unsure.items():
            with self.subTest(name):
                use = self.take(run, **host)
                self.assertNotIn("usd_est", use)
                self.assertEqual((use["i"], use["o"], use["s"]), (1, 9, 4.0), "everything else is kept")
        self.assertNotIn("usd_est", self.take({k: v for k, v in run.items() if k != "h"}, _quota_pct=lambda: None,
                                              agent_cfg={"kind": "codex"}), "a run that names no harness")
        # the whole path: a Codex task turn (tokens only, a price row) finished the way tasks.py finishes it
        t = usage.Turn("codex")
        t.put("turn", {"i": 1_000_000, "o": 1_000_000, "cr": 0, "cw": 0}, None, "gpt-6.1-sol")
        made = t.finish()
        self.assertEqual((made["b"], made["usd_est"]), ("api", 12.0), "tasks.py passes no billing: the turn alone says api")
        self.assertNotIn("usd_est", self.take(made, _quota_pct=lambda: None, agent_cfg={"kind": "claude"}))
        self.assertEqual(self.take(made, _quota_pct=lambda: None, agent_cfg={"kind": "codex"})["usd_est"], 12.0)
        # a reported figure is the harness's own number, not an estimate: untouched
        self.assertEqual(self.take({"s": 1.0, "h": "claude", "usd": 0.5, "b": "api"}, _quota_pct=lambda: None,
                                   agent_cfg={"kind": "codex"})["usd"], 0.5)

    def test_only_usage_and_tasks_name_the_field(self):
        """No budget, quota, grant or billing module reads it: the only host sources that mention it are the two that
        write / drop it."""
        named = sorted(p.relative_to(AJ / "host" / "agentj").as_posix() for p in (AJ / "host" / "agentj").rglob("*.py")
                       if "usd_est" in p.read_text(encoding="utf-8", errors="replace"))
        self.assertEqual(named, ["tasks.py", "usage.py"])


class Wire(unittest.TestCase):
    """Protocol compatibility: the field is documented as additive, and a page that predates it ignores it."""

    def test_protocol_documents_the_additive_field(self):
        doc = (AJ / "protocol" / "PROTOCOL.md").read_text(encoding="utf-8")
        sec = doc[doc.index("## 29. Spend & speed"):]
        sec = sec[:sec.index("\n## ", 5)]
        self.assertIn('"usd_est"?', sec)
        self.assertRegex(sec, r"never written to `usd`")
        self.assertRegex(sec, r"ignore")

    def test_the_shipped_page_reads_a_closed_field_list(self):
        """web/public/js/useof.js copies named fields only, so an older page drops `usd_est` and renders as before."""
        src = AJ / "web" / "public" / "js" / "useof.js"
        if not src.is_file():
            self.skipTest("web sources are not part of this tree")
        js = src.read_text(encoding="utf-8")
        body = js[js.index("export function useOf"):]
        self.assertRegex(body, r"const o = \{ s: NUM\(u\.s")
        self.assertNotRegex(body, r"\.\.\.u\b|Object\.assign\(|Object\.entries\(u\)|for \(const \w+ in u\)")
        self.assertEqual(set(re.findall(r"\bu\.(\w+)", body)) - {"s", "i", "o", "cr", "cw", "usd", "est", "b", "q", "tps",
                                                                 "tool_s", "m", "h"}, set())


if __name__ == "__main__":
    unittest.main()
