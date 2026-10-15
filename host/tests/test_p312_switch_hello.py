"""P312: `agentj agent switch codex` — the headless probe passed twice, the long-running session needed ~180 s from spawn to its
first turn (Leo's Mac M1, 10-15 12:10 JST, load 14: host.log agent_start → thread created 81 s → turn started 179 s) and the
hello wait (180 s from the hello) ran out 3 s before it; `--result` only said "no self-introduction within 3 minutes".

- the start probe: for Codex the job starts `codex app-server` as serve will (the Agent's folder, shared launch here) and times
  initialize → config/read → thread/start {ephemeral: true} — never a turn; a failed / too slow start fails the gate with a
  fixed code (+ the stderr class, never the line), nothing changes; a slow one stretches the hello wait; an app-server
  without ephemeral threads is "not measured".
- the hello wait: while serve's status says the new Agent is still `starting` it goes on (≤ HELLO_MAX); once the turn runs
  it gets TURN_MIN; a failed start ends it at once; an older serve without the field behaves as before (180 s).
- no_reply is broken down (starting · prompt · approval · failed · turn · idle + the stderr class) into the receipt, the
  `--result` line and the rolled-back Agent's host turn, zh + en, each with the owner's next step.
- Agent.phase / CodexAgent.err_kind / serve status carry fixed codes only.
Run (in host): .venv/bin/python -m unittest discover -s tests -p 'test_p312_switch_hello.py'
"""
import _hermetic  # noqa: F401,I001
import json
import os
import pathlib
import stat
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from agentj import agent as agentmod, harness, serve, switch_harness as sw  # noqa: E402
from test_p155_switch_harness import UP, Clock, FakeServe, fake_probe, mk_state  # noqa: E402

CANARY = "stderr-canary-91c2"

APP_SERVER = r'''#!/usr/bin/env python3
import json, os, sys, time
here = os.path.dirname(os.path.abspath(sys.argv[0]))
mode = open(os.path.join(here, "codex.mode")).read().strip()
log = open(os.path.join(here, "codex.log"), "a")
log.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd()}) + "\n"); log.flush()
for line in sys.stdin:
    m = json.loads(line)
    log.write(json.dumps(m) + "\n"); log.flush()
    if "id" not in m:
        continue
    meth = m["method"]
    if meth == "thread/start":
        if mode == "slow":
            time.sleep(3)
        if mode == "hang":
            time.sleep(30)
        if mode == "fail":
            sys.stderr.write("ERROR: 401 Unauthorized ''' + CANARY + r'''\n"); sys.stderr.flush()
            print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "error": {"code": -32600, "message": "auth"}}), flush=True)
            continue
        if mode == "old":
            print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "error": {"code": -32602, "message": "unknown field `ephemeral`"}}), flush=True)
            continue
        if mode == "die":
            sys.exit(3)
        print(json.dumps({"jsonrpc": "2.0", "method": "mcpServer/startupStatus/updated", "params": {"name": "x"}}), flush=True)
        print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": {"thread": {"id": "t"}}}), flush=True)
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": {"userAgent": "codex/0.162.1", "config": {}}}), flush=True)
'''


class StartProbe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p312-")
        self.addCleanup(self.tmp.cleanup)
        self.bin = pathlib.Path(self.tmp.name) / "bin"
        self.bin.mkdir()
        exe = self.bin / "codex"
        exe.write_text(APP_SERVER)
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
        p = mock.patch.dict(os.environ, {harness.BIN_ENV["codex"]: str(exe)})
        p.start()
        self.addCleanup(p.stop)
        self.st = mk_state(self.tmp.name, "claude")
        self.cfg = {**self.st.agent_config(), "session_mode": "shared"}

    def mode(self, m):
        (self.bin / "codex.mode").write_text(m)
        (self.bin / "codex.log").unlink(missing_ok=True)

    def log(self):
        return [json.loads(x) for x in (self.bin / "codex.log").read_text().splitlines()]

    def test_measures_the_session_start_like_serve_never_a_turn(self):
        self.mode("ok")
        r = sw.start_probe(self.st, "codex", self.cfg)
        self.assertEqual(set(r), {"secs"})
        log = self.log()
        self.assertEqual((log[0]["argv"], os.path.realpath(log[0]["cwd"])), (["app-server"], os.path.realpath(self.cfg["dir"])))
        methods = [m.get("method") for m in log[1:]]
        self.assertEqual(methods, ["initialize", "initialized", "config/read", "thread/start"])
        start = log[-1]["params"]
        self.assertEqual((start["ephemeral"], start["cwd"]), (True, self.cfg["dir"]))   # nothing kept in the owner's history
        self.assertNotIn("turn/start", methods)

    def test_slow_start_is_timed(self):
        self.mode("slow")
        r = sw.start_probe(self.st, "codex", self.cfg)
        self.assertGreaterEqual(r["secs"], 2.5)
        self.assertNotIn("reason", r)

    def test_too_slow_is_start_timeout(self):
        self.mode("hang")
        r = sw.start_probe(self.st, "codex", self.cfg, limit=2)
        self.assertEqual(r["reason"], "start_timeout")

    def test_failure_is_a_fixed_code_and_the_stderr_class_never_the_line(self):
        self.mode("fail")
        with mock.patch.object(harness, "codex_login", return_value=("ok", "")):
            r = sw.start_probe(self.st, "codex", self.cfg)
        self.assertEqual((r["reason"], r["err"]), ("start_failed", "auth"))
        self.assertNotIn(CANARY, json.dumps(r))
        self.mode("die")
        self.assertEqual(sw.start_probe(self.st, "codex", self.cfg)["reason"], "start_failed")

    def test_not_measured(self):
        self.mode("old")
        self.assertIsNone(sw.start_probe(self.st, "codex", self.cfg))           # no ephemeral threads: never a kept thread
        self.assertIsNone(sw.start_probe(self.st, "opencode", self.cfg))
        self.assertIsNone(sw.start_probe(self.st, "codex", {**self.cfg, "dir": "/nonexistent-p312"}))


class Phases(FakeServe):
    """serve whose status walks through phases: [(until_t, phase, err)], answering at `answer_at` (None = never)."""

    def __init__(self, st, kind, clock, script=(), answer_at=None, old=False):
        super().__init__(st, kind)
        self.clock, self.script, self.answer_at, self.old = clock, list(script), answer_at, old

    def __call__(self, req):
        r = super().__call__(req)
        if req["cmd"] == "harness_hello":
            self.said = 0
            self.base = self.clock()
        if req["cmd"] == "status" and self.kind == "codex":
            t = self.clock() - getattr(self, "base", 0)
            if self.answer_at is not None and t >= self.answer_at:
                self.said = 2000
                r["agent_status"] = "idle"
            else:
                r["agent_status"] = "working"
            if not self.old:
                ph = next(((p, e) for until, p, e in self.script if t < until), (self.script[-1][1], self.script[-1][2])
                          if self.script else ("idle", None))
                r["agent_phase"], r["agent_err"] = ph
        return r


class Hello(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p312-st-")
        self.addCleanup(self.tmp.cleanup)
        unit = pathlib.Path(self.tmp.name) / "units"
        p = mock.patch.object(sw, "receipt_path", side_effect=lambda name=sw.RECEIPT: unit / ("agentj." + name))
        p.start()
        self.addCleanup(p.stop)
        self.clock = Clock()
        self.st = mk_state(self.tmp.name, "claude")
        self.raw = self.st.config()["agent"]

    def job(self, fake, start=None):
        return sw.worker(self.st, "codex", probe=fake_probe(), ctl=fake, restart=fake.restart, status=UP,
                         ready=lambda st, limit: "idle", sleep=self.clock.sleep, clock=self.clock, start=start)

    def test_incident_replay_a_slow_start_is_waited_for(self):
        # 10-15: thread open after ~145 s, turn started at ~184 s; the answer then came within seconds
        fake = Phases(self.st, "claude", self.clock, [(145, "starting", None), (999, "turn", None)], answer_at=195)
        self.assertEqual(self.job(fake), 0)
        self.assertEqual((self.st.agent_config()["kind"], sw.read_receipt(self.st)["status"]), ("codex", "ok"))

    def test_still_starting_at_the_cap_rolls_back_with_the_detail(self):
        fake = Phases(self.st, "claude", self.clock, [(9999, "starting", None)])
        self.assertEqual(self.job(fake), 2)
        rec = sw.read_receipt(self.st)
        self.assertEqual((rec["status"], rec["reason"], rec["detail"]), ("rolled_back", "no_reply", "starting"))
        self.assertGreaterEqual(rec["waited"], sw.HELLO_MAX - 1)
        self.assertLessEqual(rec["waited"], sw.HELLO_MAX + 2 * sw.POLL)
        self.assertEqual(self.st.config()["agent"], self.raw)
        told = [h for h in fake.hellos if h[1]["outcome"] == "rolled_back"][0][1]
        self.assertEqual((told["reason"], told["detail"]), ("no_reply", "starting"))
        for lang, words in (("zh", ("还在启动", "MCP", "等了")), ("en", ("still starting", "MCP", "waited"))):
            line = sw._say_receipt(rec, lang)
            for w in words:
                self.assertIn(w, line)
            self.assertIn(w, sw.hello_prompt("claude", told, lang) if w == "MCP" else w)

    def test_failed_start_ends_the_wait_at_once_with_the_error_class(self):
        fake = Phases(self.st, "claude", self.clock, [(9999, "failed", "network")])
        self.assertEqual(self.job(fake), 2)
        rec = sw.read_receipt(self.st)
        self.assertEqual((rec["detail"], rec["err"]), ("failed", "network"))
        self.assertLess(rec["waited"], 10)
        self.assertIn("代理", sw._say_receipt(rec, "zh"))
        self.assertIn("proxy", sw._say_receipt(rec, "en"))

    def test_open_session_that_never_speaks_gets_turn_min_then_detail_turn(self):
        fake = Phases(self.st, "claude", self.clock, [(170, "starting", None), (9999, "turn", None)])
        self.assertEqual(self.job(fake), 2)
        rec = sw.read_receipt(self.st)
        self.assertEqual(rec["detail"], "turn")
        self.assertGreaterEqual(rec["waited"], 170 + sw.TURN_MIN - 1)
        self.assertIn('codex exec "say hi"', sw._say_receipt(rec, "zh"))

    def test_prompt_and_approval_details(self):
        for ph, word in (("prompt", "终端"), ("approval", "批准卡")):
            self.setUp()
            fake = Phases(self.st, "claude", self.clock, [(9999, ph, None)])
            self.assertEqual(self.job(fake), 2)
            rec = sw.read_receipt(self.st)
            self.assertEqual(rec["detail"], ph)
            self.assertLessEqual(rec["waited"], sw.HELLO_WAIT + sw.POLL)     # no stretching for something only a person ends
            self.assertIn(word, sw._say_receipt(rec, "zh"))

    def test_older_serve_without_phase_keeps_the_old_180s(self):
        fake = Phases(self.st, "claude", self.clock, old=True)
        self.assertEqual(self.job(fake), 2)
        rec = sw.read_receipt(self.st)
        self.assertEqual(rec["reason"], "no_reply")
        self.assertNotIn("detail", rec)
        self.assertLessEqual(rec["waited"], sw.HELLO_WAIT + sw.POLL)

    def test_start_probe_gates_and_stretches(self):
        fake = Phases(self.st, "claude", self.clock, [(9999, "starting", None)])
        self.assertEqual(self.job(fake, start=lambda st, t, cfg: {"secs": 3, "reason": "start_failed", "err": "auth"}), 1)
        rec = sw.read_receipt(self.st)
        self.assertEqual((rec["status"], rec["reason"], rec["err"], fake.restarts), ("failed", "start_failed", "auth", 0))
        self.assertEqual(self.st.config()["agent"], self.raw)                 # nothing changed
        self.assertIn("codex login", sw._say_receipt(rec, "zh"))
        seen = []

        def slow(st, t, cfg):
            seen.append((t, cfg["dir"], cfg["kind"]))
            return {"secs": 70.4}
        fake = Phases(self.st, "claude", self.clock, old=True, answer_at=300)  # old serve: only the measured start stretches
        self.assertEqual(self.job(fake, start=slow), 0)
        self.assertEqual(seen, [("codex", self.raw["dir"], "claude")])        # measured before anything changed
        self.assertEqual(sw.read_receipt(self.st)["start_secs"], 70)

    def test_dry_run_runs_the_start_probe_too(self):
        fake = FakeServe(self.st, "claude")
        r = sw.worker(self.st, "codex", job="j", dry_run=True, probe=fake_probe(), ctl=fake, restart=fake.restart, status=UP,
                      start=lambda st, t, cfg: {"secs": 1})
        self.assertEqual((r, sw.read_receipt(self.st)["status"], sw.read_receipt(self.st)["start_secs"]), (0, "probe_ok", 1))
        self.assertIn("长驻会话能打开", sw._say({"ok": True, "dry_run": True, "target": "codex", "start_secs": 1}, "zh"))
        self.assertIn("waits longer", sw._say({"ok": True, "scheduled": True, "target": "codex", "from": "claude",
                                               "start_secs": 40}, "en"))


class Codes(unittest.TestCase):
    def test_hello_prompt_takes_only_fixed_detail_codes(self):
        base = {"outcome": "rolled_back", "from": "claude", "to": "codex", "reason": "no_reply"}
        self.assertIsNone(sw.hello_prompt("claude", {**base, "detail": "rm -rf"}))
        self.assertIsNone(sw.hello_prompt("claude", {**base, "err": "Unauthorized"}))
        zh = sw.hello_prompt("claude", {**base, "detail": "turn", "err": "network"})
        self.assertIn("没说话", zh)
        self.assertIn("代理", zh)
        self.assertIn("never spoke", sw.hello_prompt("claude", {**base, "detail": "turn"}, "en"))
        for lang in ("zh", "en"):
            self.assertEqual(set(sw.DETAIL[lang]), set(sw.DETAIL["zh"]))
            self.assertEqual(set(sw.ERR[lang]), set(sw.ERR["zh"]))

    def test_agent_phase(self):
        host = SimpleNamespace(asks={}, st=None, agent_status=lambda s: None)
        a = agentmod.Agent.__new__(agentmod.Agent)
        a.host, a.status, a.proc = host, "idle", None
        self.assertEqual(a.phase(), "idle")
        a.status = "working"
        self.assertEqual(a.phase(), "starting")                                # queued hello, process not spawned yet
        a.proc = SimpleNamespace(pid=os.getpid(), returncode=None)
        with mock.patch.object(agentmod, "tty_waiting", return_value=False):
            self.assertEqual(a.phase(), "turn")
            with mock.patch.object(agentmod.Agent, "session_open", return_value=False):
                self.assertEqual(a.phase(), "starting")
            host.asks = {"x": SimpleNamespace(fut=SimpleNamespace(done=lambda: False))}
            self.assertEqual(a.phase(), "approval")
        with mock.patch.object(agentmod, "tty_waiting", return_value=True):
            self.assertEqual(a.phase(), "prompt")
        with mock.patch.object(agentmod.Agent, "is_down", return_value=True):
            self.assertEqual(a.phase(), "failed")

    def test_codex_err_kind_is_a_fixed_code(self):
        from agentj.agent_codex import CodexAgent
        a = CodexAgent.__new__(CodexAgent)
        a.err_tail, a.tid = "", None
        self.assertIsNone(a.err_kind())
        self.assertFalse(a.session_open())
        a.err_tail = "boot\nERROR: stream disconnected: connection refused " + CANARY + "\n"
        k = a.err_kind()
        self.assertTrue(sw._CODE.fullmatch(k) and k in sw.ERR["zh"], k)
        self.assertNotIn(CANARY, k)

    def test_serve_status_fields(self):
        h = SimpleNamespace(agent=SimpleNamespace(phase=lambda: "starting", err_kind=lambda: None))
        self.assertEqual(serve.Host._agent_phase(h), {"agent_phase": "starting", "agent_err": None})
        self.assertEqual(serve.Host._agent_phase(SimpleNamespace(agent=None)), {})
        bad = SimpleNamespace(agent=SimpleNamespace(phase=lambda: 1 / 0, err_kind=lambda: None))
        self.assertEqual(serve.Host._agent_phase(bad), {})


if __name__ == "__main__":
    unittest.main()
