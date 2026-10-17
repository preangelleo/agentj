"""P375 #82: thresholds, per-window notifications, blocked launches and reset. Local fixtures only."""
import _hermetic  # noqa
import asyncio
import os
import subprocess
import types
import unittest
from unittest.mock import patch
from test_p254_quota import AutoSwitch, SwitchHost, src, NOW
from agentj import quota

if os.environ.get("AJ_P375_BASELINE"):
    old = types.ModuleType("agentj._p375_before")
    old.__package__ = "agentj"
    exec(compile(subprocess.check_output(["git", "show", "8d782b4ca:" + "/".join(("agentjarvis", "host", "agentj", "quota.py"))]), "before-quota.py", "exec"), old.__dict__)
    quota = old

class P375Quota(AutoSwitch):
    def run_route(self, pct, *, auto=True, task=None, ok=True):
        h = SwitchHost(self.st, {"quota": {"auto_switch": auto}}, answer="deny")
        return asyncio.run(quota.route(h, "claude", task or {}, "work", rep=self.rep(claude_h5=pct), probe=self.probe(ok)))

    def test_79_80_90_reset(self):
        for pct, expected in [(79, "claude"), (80, "codex"), (90, "codex")]:
            self.assertEqual(self.run_route(pct)[0], expected)
        raw = src("claude")
        raw["h5"] = {"used_pct": 97, "reset_at": NOW - 1, "source_at": NOW - 100}
        rep = quota.report(self.st.root, NOW, sources=[raw, src("codex", used=20)])
        h = SwitchHost(self.st, {"quota": {"auto_switch": True}})
        self.assertEqual(asyncio.run(quota.route(h, "claude", {}, "work", rep=rep, probe=self.probe()))[0], "claude")
        self.assertNotIn("h5", rep["harnesses"][0])

    def test_90_never_starts_original_when_no_alternative(self):
        self.assertEqual(self.run_route(80, ok=False)[0], "claude")
        # RuntimeError is the public failure type: the scheduler must finish before invoking a harness.
        for kwargs in [{"ok": False}, {"task": {"harness": "claude"}}, {"auto": False}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(RuntimeError):
                self.run_route(90, **kwargs)

    def test_two_notifications_once_each_and_new_window(self):
        def due(pct, reset=NOW+3600):
            raw = src("claude")
            raw["h5"] = {"used_pct": pct, "reset_at": reset, "source_at": NOW}
            rep = quota.report(self.st.root, NOW, sources=[raw])
            return [x["kind"] for x in quota.due(rep, self.st.root)]
        self.assertEqual(due(79), [])
        self.assertEqual(due(80), ["h5_high"])
        self.assertEqual(due(80), [])
        self.assertEqual(due(90), ["h5_stop"])
        self.assertEqual(due(97), [])
        self.assertEqual(due(80, NOW+7200), ["h5_high"])

    def test_scheduler_blocks_before_executor(self):
        from agentj import tasks
        h = SwitchHost(self.st, {"quota": {"auto_switch": True}})
        sch = tasks.Scheduler(h)
        e = {"id": "w", "dir": str(self.st.root), "problems": [], "tsha": "x", "task": {"title": {"zh": "周报", "en": "Weekly"}, "mode": "normal", "harness": "claude"}}
        with patch.object(tasks, "find", return_value=e), patch.object(sch, "_exec") as run, patch.object(sch, "_save"), patch.object(quota, "report", return_value=self.rep(claude_h5=90)):
            res = asyncio.run(sch.run("w", "manual"))
        run.assert_not_called()
        self.assertNotEqual(res["verdict"], "ok")
        self.assertIn("90%", res["line"])

    def test_unknown_alternative_requires_live_reply_and_never_claims_a_percentage(self):
        rep = self.rep(claude_h5=90, codex_week=95)
        h = SwitchHost(self.st, {"quota": {"auto_switch": True}})
        self.assertEqual(asyncio.run(quota.route(h, "claude", {}, "work", rep=rep, probe=self.probe()))[0], "opencode")
        self.assertEqual(quota.headroom(rep, "claude"), [], "unknown quota is never printed as available percent")
        with self.assertRaises(RuntimeError):
            asyncio.run(quota.route(h, "claude", {}, "work", rep=rep, probe=self.probe(False)))

    def test_h5_without_week_still_routes(self):
        rep = self.rep(claude_h5=90)
        rep["harnesses"][0]["status"] = "unknown"
        h = SwitchHost(self.st, {"quota": {"auto_switch": True}})
        self.assertEqual(asyncio.run(quota.route(h, "claude", {}, "work", rep=rep, probe=self.probe()))[0], "codex")

# Do not discover the imported parent twice.
del AutoSwitch
