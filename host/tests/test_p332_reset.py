"""P332 (PROTOCOL §33) 紧急修复, host side: the reset ladder ①, the support-help flow ② (rescue_kit.help_flow /
parse_actions), the plain repair executors, serve's signed `reset` / `rescue_do` and `/reset`.
No network, no service manager, no harness: every outside effect is injected or patched."""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import asyncio
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import controls, rescue_kit, reset, slash  # noqa: E402
from agentj.state import State  # noqa: E402
from _testtmp import TEST_TMP  # noqa: E402

from test_controls import sign  # noqa: E402
from test_l1 import Phone, _host, _ready, _state  # noqa: E402

KIT = pathlib.Path(rescue_kit.__file__)


# ------------------------------------------------------------------ parse_actions
class ParseActions(unittest.TestCase):
    def test_only_the_last_exact_line_whitelisted_dedup_capped(self):
        p = rescue_kit.parse_actions
        self.assertEqual(p("Try this.\nAGENTJ-ACTIONS: reconnect,restart"), ["reconnect", "restart"])
        self.assertEqual(p("AGENTJ-ACTIONS: restart\nmore\nAGENTJ-ACTIONS: linger"), ["linger"], "the LAST line only")
        self.assertEqual(p("AGENTJ-ACTIONS: rm_rf,reconnect,sudo,reconnect"), ["reconnect"], "unknown dropped, deduped")
        self.assertEqual(p("AGENTJ-ACTIONS: new_session,rollback,linger,restart,reconnect,restart"),
                         ["new_session", "rollback", "linger", "restart", "reconnect"])
        for bad in ("  AGENTJ-ACTIONS: restart", "AGENTJ-ACTIONS: restart ", "AGENTJ-ACTIONS:restart",
                    "agentj-actions: restart", "AGENTJ-ACTIONS: restart;ls", "AGENTJ-ACTIONS: Restart",
                    "Please run AGENTJ-ACTIONS: restart", "AGENTJ-ACTIONS: restart\n  AGENTJ-ACTIONS: rm -rf /"):
            want = ["restart"] if bad.startswith("AGENTJ-ACTIONS: restart\n") else []
            self.assertEqual(p(bad), want, bad)
        self.assertEqual(p(None), [])
        self.assertEqual(p(""), [])
        self.assertEqual(rescue_kit.advice_of("Hi\nAGENTJ-ACTIONS: restart\nBye"), "Hi\nBye")
        self.assertEqual(rescue_kit.ACTIONS, ("reconnect", "restart", "linger", "rollback", "new_session"))


# ------------------------------------------------------------------ help_flow
class FakeDesk:
    """The support API: install session, messages (new + follow-up), the thread long poll."""

    def __init__(self, replies):
        self.replies = list(replies)      # one reply per GET round; None = nothing yet
        self.posts, self.gets, self.seq = [], 0, 0
        self.fail_post = False

    def __call__(self, method, url, token=None, body=None, timeout=30):
        if url.endswith("/v1/install-sessions"):
            return 201, {"install_session_id": "aji_" + "x" * 30}
        if url.endswith("/v1/support/messages"):
            if self.fail_post:
                raise OSError("down")
            assert token and token.startswith("aji_")
            self.posts.append(body)
            self.seq += 1
            return 201, {"thread": "st_" + "A" * 22, "seq": self.seq}
        if "/v1/support/threads/st_" in url and method == "GET":
            self.gets += 1
            r = self.replies.pop(0) if self.replies else None
            if r is None:
                return 200, {"messages": [], "cursor": self.seq, "waiting": True}
            self.seq += 1
            return 200, {"messages": [{"seq": self.seq, "author": "support", "body": r}], "cursor": self.seq}
        return 404, {}


def fake_report():
    r = rescue_kit.Report()
    r.facts.update(os="Linux test", os_kind="linux", service="agentj")
    r.checks.append(("service", rescue_kit.FAIL, "service not running"))
    r.checks.append(("https", rescue_kit.OK, "https ok"))
    return r


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        self.t += 1.0
        return self.t


class HelpFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-p332-", dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(rescue_kit, "DIR", pathlib.Path(self.tmp.name) / "rescue")
        p.start()
        self.addCleanup(p.stop)
        self.steps, self.ran = [], []

    def emit(self, step, state, text, offer=None):
        self.steps.append((step, state, text) + ((offer,) if offer else ()))

    def act(self, name):
        self.ran.append(name)
        return name != "linger", f"did {name}"

    def flow(self, desk, **kw):
        return rescue_kit.help_flow("zh", self.emit, self.act, http=desk, diagnose_fn=lambda with_doctor=False: fake_report(),
                                    clock=Clock(), **kw)

    def test_ticket_actions_filtered_results_posted(self):
        desk = FakeDesk(["先重连再重启。\ncategory: service_down\nAGENTJ-ACTIONS: reconnect,rm_rf,linger", "好了。"])
        res = self.flow(desk)
        self.assertEqual(res["ticket"], "st_" + "A" * 22)
        self.assertEqual(res["category"], "service_down")
        self.assertEqual(self.ran, ["reconnect"], "only reconnect / restart run on support's word (HIGH-1)")
        self.assertEqual(res["offer"], ["linger"], "linger is offered to the owner, not run")
        self.assertFalse(res["ok"], "an offer waits for the owner's tap")
        self.assertTrue(desk.posts[0]["body"].startswith("AGENTJ-RESCUE v1 [help]"))
        self.assertNotIn("thread", desk.posts[0])
        follow = desk.posts[1]
        self.assertEqual(follow["thread"], res["ticket"], "results go back on the same ticket")
        self.assertTrue(follow["body"].startswith("AGENTJ-RESCUE v1 [result]"))
        self.assertIn("- reconnect: ok", follow["body"])
        self.assertIn("- linger: offered", follow["body"])
        self.assertEqual(len(desk.posts), 2, "an offer ends the flow: the next move is the owner's")
        self.assertIn((2, "ok", "报告已发出，工单号 st_" + "A" * 22 + "。"), self.steps)
        advice = [t for s, st, t, *_ in self.steps if s == 3 and st == "ok"]
        self.assertTrue(advice and "AGENTJ-ACTIONS" not in advice[0] and "先重连再重启" in advice[0])
        offers = [x for x in self.steps if len(x) == 4]
        self.assertEqual(offers, [(4, "skip", "客服建议：让 Agent J 在没人登录时也能运行（点下面的按钮确认后才会做）", ["linger"])])
        for _s, _st, text, *_ in self.steps:
            for word in ("relay", "serve", "harness", "session"):
                self.assertNotIn(word, text.lower())

    def test_persistent_actions_are_only_offered(self):
        desk = FakeDesk(["category: other\nAGENTJ-ACTIONS: rollback,new_session,linger"])
        res = self.flow(desk)
        self.assertEqual(self.ran, [], "rollback / new_session / linger never run on support's word")
        self.assertEqual(res["offer"], ["rollback", "new_session", "linger"])
        self.assertFalse(res["ok"])
        body = desk.posts[1]["body"]
        for n in ("rollback", "new_session", "linger"):
            self.assertIn(f"- {n}: offered to the owner", body)
        self.assertNotIn(": ok", body)
        self.assertIn("点按钮确认", res["text"])

    def test_estop_only_reports_and_offers(self):
        desk = FakeDesk(["AGENTJ-ACTIONS: reconnect,restart,linger"])
        res = self.flow(desk, estop=True)
        self.assertEqual(self.ran, [], "during an e-stop nothing runs")
        self.assertEqual(res["offer"], ["reconnect", "restart", "linger"])
        self.assertIn("stop-everything", desk.posts[1]["body"])
        self.assertTrue(desk.posts[0]["body"].startswith("AGENTJ-RESCUE v1 [help]"), "the report still goes")

    def test_three_argument_emit_still_works(self):
        got = []
        res = rescue_kit.help_flow("en", lambda step, state, text: got.append((step, state)), self.act,
                                   http=FakeDesk(["AGENTJ-ACTIONS: linger"]), diagnose_fn=lambda with_doctor=False: fake_report(),
                                   clock=Clock())
        self.assertEqual(res["offer"], ["linger"])
        self.assertIn((4, "skip"), got, "an emit without `offer` still hears the step")

    def test_scheduled_restart_is_not_reported_done(self):
        steps = []

        def act(name):
            self.ran.append(name)
            return None, "restarts after this report"
        res = rescue_kit.help_flow("zh", lambda *a, **k: steps.append((a, k)), act, http=(desk := FakeDesk(["AGENTJ-ACTIONS: restart"])),
                                   diagnose_fn=lambda with_doctor=False: fake_report(), clock=Clock(), wait_s=5)
        self.assertEqual(self.ran, ["restart"])
        self.assertIn("- restart: scheduled", desk.posts[1]["body"])
        self.assertNotIn("restart: ok", desk.posts[1]["body"])
        self.assertFalse([a for a, k in steps if a[0] == 4 and a[1] == "ok"], "no 'ok' before the restart is tried")
        self.assertEqual(res["pending"], ["restart"])
        self.assertIn("马上重启", res["text"])

    def test_all_ok_is_ok(self):
        res = self.flow(FakeDesk(["AGENTJ-ACTIONS: restart", "谢谢，已经好了。"]))
        self.assertTrue(res["ok"])
        self.assertEqual(self.ran, ["restart"])

    def test_rounds_cap(self):
        desk = FakeDesk(["AGENTJ-ACTIONS: reconnect"] * 10)
        res = self.flow(desk, rounds=3)
        self.assertEqual(self.ran, ["reconnect"] * 3)
        self.assertEqual(len(desk.posts), 4, "one report + three result posts")
        self.assertIn("ticket", res)

    def test_advice_only_and_no_reply(self):
        res = self.flow(FakeDesk(["请检查电源。"]))
        self.assertFalse(res["ok"])
        self.assertEqual(self.ran, [])
        res = self.flow(FakeDesk([]), wait_s=5)
        self.assertFalse(res["ok"])
        self.assertEqual(res["step"], 3)

    def test_never_raises(self):
        def boom(with_doctor=False):
            raise RuntimeError("x")
        res = rescue_kit.help_flow("en", self.emit, self.act, http=FakeDesk([]), diagnose_fn=boom, clock=Clock())
        self.assertFalse(res["ok"])
        desk = FakeDesk([])
        desk.fail_post = True
        res = self.flow(desk)
        self.assertEqual((res["ok"], res["step"]), (False, 2))

        def bad_emit(*a):
            raise ValueError
        res = rescue_kit.help_flow("zh", bad_emit, lambda n: 1 / 0, http=FakeDesk(["AGENTJ-ACTIONS: restart", None]),
                                   diagnose_fn=lambda with_doctor=False: fake_report(), clock=Clock(), wait_s=5)
        self.assertFalse(res["ok"])
        self.assertIn("ticket", res)

    def test_budget(self):
        desk = FakeDesk(["AGENTJ-ACTIONS: reconnect"] * 10)
        res = self.flow(desk, budget_s=12)
        self.assertLess(len(self.ran), 3)
        self.assertFalse(res["ok"])

    def test_standalone_script_runs_without_the_package(self):
        home = pathlib.Path(self.tmp.name) / "home"
        home.mkdir()
        work = pathlib.Path(self.tmp.name) / "only"
        work.mkdir()
        (work / "rescue.py").write_bytes(KIT.read_bytes())
        env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "AGENTJ_STATE_DIR": str(home / "state"),
               "AGENTJ_RESCUE_API": "http://127.0.0.1:9/api", "AGENTJ_RESCUE_RELAY_HOST": "localhost"}
        r = subprocess.run([sys.executable, "-I", str(work / "rescue.py"), "--dry-run", "--no-send", "--en"], cwd=str(work),
                           env=env, capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
        self.assertIn(r.returncode, (0, 3), r.stderr[-2000:])
        self.assertNotIn("Traceback", r.stdout + r.stderr)


# ------------------------------------------------------------------ the ladder
class Ops:
    def __init__(self, results, healthy=True):
        self.results, self.calls, self._healthy = results, [], healthy

    def _f(name):
        async def f(self):
            self.calls.append(name)
            r = self.results.get(name, {"ok": False})
            if isinstance(r, Exception):
                raise r
            return r
        return f

    reconnect, repin, new_session, restart = _f("reconnect"), _f("repin"), _f("new_session"), _f("restart")

    async def healthy(self):
        return self._healthy


class Ladder(unittest.TestCase):
    def run_ladder(self, ops, steps=(1, 2, 3, 4)):
        events = []

        async def emit(step, state, text, detail=None):
            events.append(("step", step, state, text))

        async def finish(res):
            events.append(("done", res))
        res = asyncio.run(reset.ladder(ops, emit, finish, "zh", steps))
        return res, events

    def test_stops_at_first_success(self):
        ops = Ops({"reconnect": {"ok": True}})
        res, ev = self.run_ladder(ops)
        self.assertEqual(ops.calls, ["reconnect"])
        self.assertEqual((res["ok"], res["step"]), (True, 1))
        self.assertEqual([e[2] for e in ev if e[0] == "step"], ["run", "ok"])
        self.assertEqual(ev[-1][1]["text"], "连接好了，Agent J 在线。")

    def test_escalation_order_and_restart_answers_first(self):
        ops = Ops({"reconnect": {"ok": False}, "repin": RuntimeError("x"),
                   "new_session": {"ok": False}, "restart": {"ok": True}})
        res, ev = self.run_ladder(ops)
        self.assertEqual(ops.calls, ["reconnect", "repin", "new_session", "restart"])
        dones = [e for e in ev if e[0] == "done"]
        self.assertEqual(len(dones), 1)
        self.assertEqual((dones[0][1]["ok"], dones[0][1]["step"]), (True, 4))
        self.assertEqual(res["step"], 4)
        steps = [(e[1], e[2]) for e in ev if e[0] == "step"]
        self.assertEqual(steps, [(1, "run"), (1, "fail"), (2, "run"), (2, "fail"), (3, "run"), (3, "fail"), (4, "run")])
        for e in ev:
            text = e[3] if e[0] == "step" else e[1]["text"]
            for word in ("relay", "serve", "harness", "session"):
                self.assertNotIn(word, text.lower())

    def test_new_session_old_id_and_failed_restart(self):
        ops = Ops({"new_session": {"ok": True, "old_session": "abc-123"}}, healthy=False)
        ops.results["restart"] = {"ok": False, "why": "not_installed"}
        res, ev = self.run_ladder(ops)
        dones = [e[1] for e in ev if e[0] == "done"]
        self.assertEqual([d["ok"] for d in dones], [True, False], "step 4 answers first, then says the restart failed")
        self.assertEqual(dones[0]["old_session"], "abc-123")
        self.assertEqual(dones[1]["why"], "unavailable")
        ops = Ops({"new_session": {"ok": True, "old_session": "abc-123"}})
        res, ev = self.run_ladder(ops)
        self.assertEqual((res["ok"], res["step"], res["old_session"]), (True, 3, "abc-123"))

    def test_estop_and_skip(self):
        ops = Ops({"reconnect": {"stop": "estop"}})
        res, ev = self.run_ladder(ops)
        self.assertEqual(ops.calls, ["reconnect"], "a stopped Agent is not 'fixed' by a new conversation or a restart")
        self.assertEqual((res["ok"], res["why"]), (False, "estop"))
        ops = Ops({"new_session": {"skip": True}, "restart": {"ok": True}})
        res, ev = self.run_ladder(ops)
        self.assertIn(("step", 3, "skip"), [e[:3] for e in ev])
        self.assertEqual(res["step"], 4)

    def test_partial_ladders(self):
        ops = Ops({})
        res, _ = self.run_ladder(ops, (1, 2))
        self.assertEqual(ops.calls, ["reconnect", "repin"])
        self.assertEqual((res["ok"], res["step"]), (False, 2))


# ------------------------------------------------------------------ executors
class Executors(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-p332-", dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.st = State(pathlib.Path(self.tmp.name) / "state")
        self.st.root.mkdir(parents=True)

    def test_linger(self):
        calls = []

        def runner(argv, **kw):
            calls.append(argv[1:])
            return SimpleNamespace(returncode=0, stdout="no\n" if argv[1] == "show-user" else "")
        with mock.patch("shutil.which", return_value="/usr/bin/loginctl"):
            self.assertEqual(reset.run_linger(self.st, runner=runner, system=lambda: "Linux", user="alice"), {"ok": True})
            self.assertEqual(calls[-1], ["enable-linger", "alice"], "no sudo, only this user")
            self.assertEqual(reset.run_linger(self.st, runner=runner, system=lambda: "Linux", user="-x")["why"], "unavailable")
        self.assertEqual(reset.run_linger(self.st, system=lambda: "Darwin")["why"], "unavailable")

    def test_rollback_unavailable_and_newest_older(self):
        self.assertEqual(reset.run_rollback(self.st, current="0.22.1"), {"ok": False, "why": "unavailable"})
        cache = self.st.root / "upgrade-cache"
        cache.mkdir()
        for v in ("0.21.0", "0.22.0", "0.22.1", "0.23.0"):
            (cache / f"agentj-{v}-py3-none-any.whl").write_bytes(b"w")
            (cache / f"agentj-{v}-py3-none-any.verified.json").write_text("{}")
        (cache / "agentj-0.22.0a9-py3-none-any.whl").write_bytes(b"unverified")
        self.assertEqual(reset.previous_wheel(self.st, "0.22.1")[0], "0.22.0")
        with mock.patch("agentj.update.roll_back", return_value=True) as rb, mock.patch("agentj.update.install_kind", return_value={}):
            self.assertEqual(reset.run_rollback(self.st, current="0.22.1"), {"ok": True, "version": "0.22.0"})
            self.assertEqual(rb.call_args[0][3], "0.22.0")

    def test_restart_serve(self):
        calls = []
        svc = SimpleNamespace(status=lambda: {"installed": True}, platform=lambda: "linux", name=lambda: "agentj",
                              _systemctl=lambda *a, **k: calls.append(a), restart=lambda: calls.append("restart"))
        self.assertEqual(reset.run_restart_serve(self.st, svc=svc), {"ok": True})
        self.assertEqual(calls, [("--no-block", "restart", "agentj.service")])
        svc.status = lambda: {"installed": False}
        self.assertEqual(reset.run_restart_serve(self.st, svc=svc)["why"], "not_installed")

        def boom(*a, **k):
            raise RuntimeError
        svc.status = boom
        self.assertFalse(reset.run_restart_serve(self.st, svc=svc)["ok"])

    def test_new_session_undo_file_0600(self):
        st = self.st
        st.set_agent_session("claude", "11111111-2222-3333-4444-555555555555")
        dropped = []

        class A:
            kind, status, cfg = "claude", "down", {}

            async def drop_conversation(self):
                dropped.append(1)
        host = SimpleNamespace(agent=A(), st=st)
        res = asyncio.run(reset.new_session(host))
        self.assertEqual(res, {"ok": True, "old_session": "11111111-2222-3333-4444-555555555555"})
        self.assertEqual(dropped, [1])
        self.assertIsNone(st.agent_session("claude"))
        p = st.root / reset.UNDO
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        self.assertEqual(json.loads(p.read_text())["session"], "11111111-2222-3333-4444-555555555555")
        A.cfg = {"session_mode": "shared"}
        self.assertTrue(asyncio.run(reset.new_session(host)).get("skip"))
        A.cfg, A.status = {}, "working"
        self.assertFalse(asyncio.run(reset.new_session(host))["ok"])


class Undo(unittest.TestCase):
    """`agentj rescue undo`: the conversation step 3 put aside comes back; the newer one is kept for flipping back."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-p332u-", dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.st = State(pathlib.Path(self.tmp.name) / "state")
        self.st.root.mkdir(parents=True)

    def run_undo(self):
        from agentj import cli, names
        with mock.patch.object(names, "ctl_call", return_value={"ok": True, "when": "next_turn"}) as ctl, \
                mock.patch("sys.stdout", new_callable=__import__("io").StringIO) as out:
            cli._rescue_undo(self.st)
        return ctl, out.getvalue()

    def test_undo_flips_and_restarts_the_owned_harness(self):
        old, new = "11111111-2222-3333-4444-555555555555", "66666666-7777-8888-9999-000000000000"
        reset.write_undo(self.st, "claude", old)
        self.st.set_agent_session("claude", new)
        ctl, out = self.run_undo()
        self.assertEqual(self.st.agent_session("claude"), old)
        self.assertEqual(ctl.call_args[0][1], {"cmd": "agent_restart"})
        self.assertEqual(json.loads((self.st.root / reset.UNDO).read_text())["session"], new, "can flip back")
        self.assertIn("agentj", out.lower() + "agentj")
        self.assertNotIn(old, out, "the id is never printed")

    def test_nothing_to_undo_and_bad_record(self):
        from agentj import cli
        with self.assertRaises(SystemExit):
            cli._rescue_undo(self.st)
        (self.st.root / reset.UNDO).write_text(json.dumps({"kind": "claude", "session": "x; rm -rf /"}))
        with self.assertRaises(SystemExit):
            cli._rescue_undo(self.st)
        (self.st.root / reset.UNDO).write_text(json.dumps({"kind": "nope", "session": "abcdef12"}))
        with self.assertRaises(SystemExit):
            cli._rescue_undo(self.st)


class KitFixes(unittest.TestCase):
    def test_redaction_keeps_clock_times_and_generic_hostnames(self):
        with mock.patch.object(rescue_kit, "_identity", return_value=("alice", "examplelinux")), \
                mock.patch.object(rescue_kit, "_distro_names", return_value={"examplelinux"}):
            out = rescue_kit.redact("09:01:33 PM: suspend exit on examplelinux (ExampleLinux) fe80::1 2001:db8:0:0:1:2:3:4 alice")
        self.assertIn("09:01:33", out)
        self.assertIn("(ExampleLinux)", out, "the distribution's default hostname is not personal")
        self.assertNotIn("fe80::1", out)
        self.assertNotIn("2001:db8", out)
        self.assertNotIn("alice", out)
        with mock.patch.object(rescue_kit, "_identity", return_value=("alice", "leos-laptop")):
            self.assertNotIn("leos-laptop", rescue_kit.redact("host leos-laptop up"))

    def test_session_cap_gets_a_fresh_session_for_a_new_ticket(self):
        tmp = tempfile.TemporaryDirectory(prefix="aj-p332k-", dir=TEST_TMP)
        self.addCleanup(tmp.cleanup)
        made, posts = [], []

        def http(method, url, token=None, body=None, timeout=30):
            if url.endswith("/v1/install-sessions"):
                made.append(1)
                return 201, {"install_session_id": f"aji_{len(made):022d}"}
            posts.append((token, dict(body)))
            if len(posts) == 1:
                return 429, {"error": "rate_limited", "scope": "install_total"}
            return 201, {"thread": "st_" + "A" * 22}
        with mock.patch.object(rescue_kit, "DIR", pathlib.Path(tmp.name)):
            self.assertEqual(rescue_kit.send("AGENTJ-RESCUE v1\nx", "", http=http), "st_" + "A" * 22)
            self.assertEqual(len(made), 2)
            self.assertNotEqual(posts[0][0], posts[1][0])
            posts.clear()
            with self.assertRaises(RuntimeError):   # a follow-up on a ticket cannot move to a new session
                rescue_kit.send("AGENTJ-RESCUE v1 [result]\nx", "", http=http, thread="st_" + "B" * 22)

    def test_repair_installs_the_watchdog_only_when_agentj_knows_it(self):
        r = rescue_kit.Report()
        r.facts.update(os_kind="linux", service="agentj", unit={"LoadState": "loaded", "ActiveState": "active",
                                                                 "UnitFileState": "enabled"}, linger="yes")
        r.checks.append(("agentj", rescue_kit.OK, "ok"))
        calls = []

        def fake_run(argv, timeout=20, env=None):
            calls.append(argv[1:])
            if argv[1:3] == ["rescue", "status"]:
                return 0, json.dumps({"installed": False, "kind": "systemd"})
            return 0, ""
        with mock.patch.object(rescue_kit, "agentj_bin", return_value="/x/agentj"), mock.patch.object(rescue_kit, "run", fake_run), \
                mock.patch.object(rescue_kit, "say"):
            rescue_kit.repair(r)
        self.assertIn(["rescue", "install"], calls)
        self.assertIn(["doctor", "--reconnect"], calls)
        calls.clear()

        def old_agentj(argv, timeout=20, env=None):
            calls.append(argv[1:])
            return (2, "invalid choice: 'rescue'") if argv[1] == "rescue" else (0, "")
        with mock.patch.object(rescue_kit, "agentj_bin", return_value="/x/agentj"), mock.patch.object(rescue_kit, "run", old_agentj), \
                mock.patch.object(rescue_kit, "say"):
            rescue_kit.repair(r)
        self.assertNotIn(["rescue", "install"], calls)
        self.assertTrue(r.facts.get("no_watchdog"))

    def test_a_test_home_never_changes_the_real_service_manager(self):
        """12:5x leak: a simulation with a throw-away HOME started a stand-in `agentj-p…` unit in the developer's real
        systemd user manager (per uid, not per HOME) and the real `agentj busy` picked it up. Under AGENTJ_TEST=1 / a
        foreign HOME only a stand-in systemctl (outside /usr, /bin) may change it."""
        r = rescue_kit.Report()
        r.facts.update(os_kind="linux", service="agentj-p999x", linger="no",
                       unit={"LoadState": "loaded", "ActiveState": "failed", "UnitFileState": "disabled"})
        calls = []

        def fake_run(argv, timeout=20, env=None):
            calls.append(argv)
            return 0, ""
        self.assertEqual(os.environ.get("AGENTJ_TEST"), "1")          # _hermetic
        with mock.patch.object(rescue_kit, "agentj_bin", return_value=None), mock.patch.object(rescue_kit, "run", fake_run), \
                mock.patch.object(rescue_kit, "say"), mock.patch.object(rescue_kit.shutil, "which",
                                                                        lambda c: f"/usr/bin/{c}"):
            rescue_kit.repair(r)
        self.assertFalse([a for a in calls if a[0] in rescue_kit.SERVICE_MANAGERS], calls)
        stub = pathlib.Path(TEST_TMP) / "fakebin"
        with mock.patch.object(rescue_kit, "agentj_bin", return_value=None), mock.patch.object(rescue_kit, "run", fake_run), \
                mock.patch.object(rescue_kit, "say"), mock.patch.object(rescue_kit.shutil, "which",
                                                                        lambda c: str(stub / c)):
            rescue_kit.repair(r)
        self.assertIn(["systemctl", "--user", "start", "agentj-p999x.service"], calls)
        with mock.patch.dict(os.environ, {"AGENTJ_TEST": "0"}), \
                mock.patch.object(rescue_kit, "HOME", pathlib.Path(TEST_TMP) / "home"), \
                mock.patch.object(rescue_kit.shutil, "which", lambda c: f"/usr/bin/{c}"):
            self.assertTrue(rescue_kit.foreign_manager(["systemctl", "--user", "start", "x.service"]))
            self.assertFalse(rescue_kit.foreign_manager(["agentj", "doctor"]))
        with mock.patch.dict(os.environ, {"AGENTJ_TEST": "0"}), \
                mock.patch.object(rescue_kit, "HOME", pathlib.Path(os.path.expanduser("~" + __import__("getpass").getuser()))), \
                mock.patch.object(rescue_kit.shutil, "which", lambda c: f"/usr/bin/{c}"):
            self.assertFalse(rescue_kit.foreign_manager(["systemctl", "--user", "start", "x.service"]))   # a real run


# ------------------------------------------------------------------ review P332 MEDIUM-3 / LOW-11 / LOW-13
CHANNEL = "Zq3xR8vT2mK9pL4wN7bY1c"          # 22 chars, the shape of a real channel id
SECRETS = ("HomeWifi-5G-Leo", "Never Gonna Give You Up", CHANNEL, "@leo_private_bot", "alice", "bobsmith", "seat9",
           "SSID", "now playing")


def _fake_run(argv, timeout=20, env=None):
    a = " ".join(argv)
    if argv[0] == "systemd-inhibit":
        return 0, ("alice 1000 1234 firefox sleep \"Playing Never Gonna Give You Up\" block\n"
                   "me 1001 2222 agentj sleep \"Agent J keeps the phone reachable\" block\n")
    if argv[0] == "loginctl" and argv[1] == "list-sessions":
        return 0, "3 1000 alice seat0 tty2\n7 1002 bobsmith seat9 tty3\n"
    if argv[0] == "loginctl" and argv[1] == "show-session":
        return 0, "IdleHint=no\nLockedHint=no\nState=active\nName=alice\n"
    if argv[0] == "loginctl" and argv[1] == "show-user":
        return 0, "Linger=yes\n"
    if argv[0] == "journalctl" and "--user" in argv:
        return 0, ("2026-10-16T12:00:00+0800 pc agentj[42]: joined HomeWifi-5G-Leo SSID, now playing Never Gonna Give You Up\n"
                   "2026-10-16T12:00:01+0800 pc agentj[42]: telegram @leo_private_bot channel " + CHANNEL + "\n"
                   "2026-10-16T12:00:02+0800 pc systemd[900]: agentj.service: Main process exited, code=exited, status=1/FAILURE\n"
                   "2026-10-16T12:00:02+0800 pc systemd[900]: agentj.service: Failed with result 'exit-code'.\n"
                   "2026-10-16T12:00:03+0800 pc systemd[900]: Reached target HomeWifi-5G-Leo.\n")
    if argv[0] == "journalctl":
        return 0, ("2026-10-16T11:00:00+0800 pc kernel: PM: suspend entry (deep)\n"
                   "2026-10-16T11:00:00+0800 pc NetworkManager[7]: <info> HomeWifi-5G-Leo SSID disconnected\n"
                   "2026-10-16T11:30:00+0800 pc kernel: PM: suspend exit\n")
    if argv[0] == "systemctl" and "show" in argv:
        return 0, "LoadState=loaded\nActiveState=failed\nSubState=failed\nUnitFileState=enabled\nResult=exit-code\n"
    if argv[0] == "systemctl":
        return 0, "running\n"
    if argv[0] == "pgrep":
        return 1, ""
    if a.endswith("--version"):
        return 0, "agentj 0.22.1\n"
    if a.endswith(" doctor"):
        return 1, (f"✓ state ~/.local/state/agentj · 通道 / channel {CHANNEL}\n"
                   "✗ telegram @leo_private_bot not reachable\n✓ relay ok\n")
    return 127, ""


class ReportAllowlist(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-p332-", dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        self.state = root / "state"
        self.state.mkdir()
        (self.state / "config.json").write_text(json.dumps({"channel": CHANNEL}))
        now = __import__("time").time()
        lines = [{"ts": now - 60, "ev": "relay_down", "code": 1006, "phase": "up", "title": "Never Gonna Give You Up",
                  "ssid": "HomeWifi-5G-Leo", "why": "HomeWifi-5G-Leo has spaces", "channel": CHANNEL},
                 {"ts": now - 30, "ev": "telegram_fail", "status": 403, "result": "@leo_private_bot", "count": 2,
                  "text": "message for alice", "ms": 12.5, "reason": "x"},
                 {"ts": now - 10, "ev": "loop_stall", "ms": 900, "why": "x" * 41}]
        (self.state / "host.log").write_text("\n".join(json.dumps(x) for x in lines) + "\nnot json\n")
        home = root / "home"
        (home / ".config/hypr").mkdir(parents=True)
        (home / ".config/hypr/hypridle.conf").write_text(
            "listener {\n timeout = 600\n on-timeout = notify-send 'HomeWifi-5G-Leo' && systemctl suspend\n}\n")
        for p in (mock.patch.dict(os.environ, {"AGENTJ_STATE_DIR": str(self.state), "XDG_SESSION_ID": "3"}),
                  mock.patch.object(rescue_kit, "HOME", home), mock.patch.object(rescue_kit, "DIR", root / "rescue"),
                  mock.patch.object(rescue_kit, "run", _fake_run), mock.patch.object(rescue_kit, "os_kind", lambda: "linux"),
                  mock.patch.object(rescue_kit, "agentj_bin", lambda: "/x/agentj"), mock.patch.object(rescue_kit, "say"),
                  mock.patch.object(rescue_kit, "check_network", lambda r: r.add("https", rescue_kit.OK, "ok", "ok"))):
            p.start()
            self.addCleanup(p.stop)

    def test_nothing_unneeded_reaches_the_report(self):
        calls = []
        with mock.patch.object(rescue_kit, "run", lambda argv, *a, **k: calls.append(argv) or _fake_run(argv, *a, **k)):
            r = rescue_kit.diagnose(with_doctor=True)
        body, attach, _ = rescue_kit.compose(r, [], None, "help")
        report = body + "\n" + attach
        for bad in SECRETS:
            self.assertNotIn(bad, report, bad)
        self.assertNotIn(["loginctl", "list-sessions", "--no-legend"], calls, "never the list of everyone's sessions")
        # what the diagnosis does need is still there
        self.assertIn("count 2 · agentj yes", report)
        self.assertIn("IdleHint=no LockedHint=no State=active", report)
        self.assertIn("systemd: agentj.service: Main process exited, code=exited, status=1/FAILURE", report)
        self.assertIn("suspend entry", report)
        self.assertIn("listener timeout=600 action=suspend", report)
        self.assertIn('"ev": "relay_down", "code": 1006, "phase": "up"', report)
        self.assertIn('"status": 403', report)
        self.assertIn("<channel>", report, "doctor's channel line is redacted")
        self.assertIn("relay ok", report)

    def test_hostlog_line_allowlist(self):
        f = rescue_kit.hostlog_line
        self.assertEqual(json.loads(f({"ts": 1, "ev": "x", "why": "a b", "title": "t", "count": True, "ms": 3.5})),
                         {"ts": 1, "ev": "x", "ms": 3.5})
        self.assertIsNone(f({"title": "only"}))
        self.assertIsNone(f("not a dict"))

    def test_no_channel_in_config_falls_back_to_the_pattern(self):
        (self.state / "config.json").unlink()
        self.assertNotIn(CHANNEL, rescue_kit.redact(f"通道 / channel {CHANNEL}"))
        self.assertEqual(rescue_kit.redact("unit agentj@2 and a@b"), "unit agentj@2 and a@b", "only a free-standing @handle")
        self.assertEqual(rescue_kit.redact("bot @leo_private_bot"), "bot <handle>")

    def test_tickets_file_is_private_from_the_first_byte(self):
        seen = []
        real = os.open

        def spy(path, flags, mode=0o777, *a, **k):
            if str(path).endswith("tickets.txt"):
                seen.append(mode)
            return real(path, flags, mode, *a, **k)
        old = os.umask(0o022)
        try:
            with mock.patch.object(rescue_kit.os, "open", spy):
                rescue_kit.send("body", "", FakeDesk([]))
        finally:
            os.umask(old)
        self.assertEqual(seen, [0o600])
        self.assertEqual(stat.S_IMODE((rescue_kit.DIR / "tickets.txt").stat().st_mode), 0o600)

    def test_dns_check_changes_no_process_wide_default(self):
        import socket
        import time as _t
        self.assertIsNone(socket.getdefaulttimeout())
        with mock.patch.object(socket, "setdefaulttimeout", side_effect=AssertionError("process-wide")):
            self.assertIsNone(rescue_kit.resolve("x", lookup=lambda *a, **k: None))
            self.assertEqual(rescue_kit.resolve("x", lookup=lambda *a, **k: (_ for _ in ()).throw(socket.gaierror())), "gaierror")
            t0 = _t.monotonic()
            self.assertEqual(rescue_kit.resolve("x", timeout=0.2, lookup=lambda *a, **k: _t.sleep(2)), "timeout")
            self.assertLess(_t.monotonic() - t0, 1.5)
        self.assertNotIn("setdefaulttimeout", KIT.read_text())


# ------------------------------------------------------------------ serve: signed messages, refusals, busy, /reset
class Serve(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-p332-", dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.st = _state(self.tmp.name)
        self.sent = []

    def host(self):
        ph, other = Phone(self.st, "iPhone"), Phone(self.st, "Pixel")
        host = _host(self.st, self.sent)
        s = _ready(host, ph)
        s.p33 = True
        s2 = _ready(host, other, cid=31)
        s2.p33 = True
        return ph, other, host, s, s2

    def got(self, t, cid=None):
        return [o for c, o in self.sent if o["t"] == t and (cid is None or c == cid)]

    def controls_log(self):
        return [json.loads(x) for x in self.st.controls_path.read_text().splitlines()] if self.st.controls_path.exists() else []

    def test_refusals_and_logging(self):
        ph, other, host, s, s2 = self.host()
        tgt = {"what": "connection"}

        async def go():
            await host._app(s, {"t": "reset", "r": "u1"})                                        # unsigned
            await host._app(s, sign(ph, "reset", tgt, {"t": "reset", "r": "k1"}, key=other.sk))  # another key
            await host._app(s, sign(ph, "reset", tgt, {"t": "reset", "r": "s1"}, ts=1))          # stale
            await host._app(s, sign(ph, "rescue_do", {"action": "rm"}, {"t": "rescue_do", "r": "x1", "action": "rm"}))
            await host._app(s, sign(ph, "rescue_do", {"action": "restart"}, {"t": "rescue_do", "r": "p1", "action": "linger"}))
            m = sign(ph, "rescue_do", {"action": "linger"}, {"t": "rescue_do", "r": "ok1", "action": "linger"})
            await host._app(s, m)
            await host._app(s, {**m, "r": "rp1"})                                                # replay (busy or replay)
            await asyncio.sleep(0.3)
        with mock.patch.object(reset, "run_linger", return_value={"ok": False, "why": "unavailable"}):
            asyncio.run(go())
        done = {o["r"]: o for o in self.got("reset_done")}
        self.assertEqual([done[r].get("why") for r in ("u1", "k1", "s1", "x1", "p1", "rp1")],
                         ["shape", "bad_signature", "stale", "shape", "bad_signature", "replay"])
        self.assertEqual(done["ok1"]["flow"], "linger")
        self.assertFalse(done["ok1"]["ok"])
        self.assertEqual(done["ok1"]["why"], "unavailable")
        self.assertEqual(len([o for o in self.got("reset_done") if o["r"] == "ok1"]), 2, "the asking and the other remote")
        self.assertTrue(self.got("reset_step", cid=s.cid))
        self.assertFalse(self.got("reset_step", cid=s2.cid), "progress goes to the asking device only")
        log = self.controls_log()
        self.assertEqual([c["result"] for c in log][:5],
                         ["refused:shape", "refused:bad_signature", "refused:stale", "refused:shape", "refused:bad_signature"])
        self.assertIn("ok", [c["result"] for c in log])
        self.assertTrue(all(c["action"] in ("reset", "rescue_do") for c in log))

    def test_busy_and_estop(self):
        ph, other, host, s, s2 = self.host()
        gate = asyncio.Event

        async def go():
            release = gate()

            async def slow(*a, **k):
                await release.wait()
                return {"ok": True}
            host.estop = {"on": True, "at": 0, "by": "terminal"}
            self.assertTrue(host.stopped())
            with mock.patch.object(reset.HostOps, "reconnect", slow):
                await host._app(s, sign(ph, "reset", {"what": "connection"}, {"t": "reset", "r": "a1"}))
                await asyncio.sleep(0.05)
                await host._app(s2, sign(other, "reset", {"what": "connection"}, {"t": "reset", "r": "b1"}))
                await asyncio.sleep(0.05)
                release.set()
                await asyncio.sleep(0.2)
        asyncio.run(go())
        done = {o["r"]: o for o in self.got("reset_done")}
        self.assertEqual(done["b1"]["why"], "busy")
        self.assertTrue(done["a1"]["ok"], "allowed during an e-stop: it only repairs connectivity")
        self.assertEqual(done["a1"]["step"], 1)
        self.assertIsNone(host.reset_desk.busy)

    def test_help_offers_persistent_actions_and_forwards_offer(self):
        ph, other, host, s, s2 = self.host()
        seen = {}

        def fake_flow(lang, emit, act, **kw):
            seen["estop"] = kw.get("estop")
            seen["linger"] = act("linger")                      # support's word never runs it, even if asked directly
            seen["rollback"] = act("rollback")
            emit(4, "skip", "客服建议：退回上一个版本（点下面的按钮确认后才会做）", offer=["rollback", "rm"])
            return {"ok": False, "step": 5, "text": "t", "ticket": "st_" + "A" * 22, "offer": ["rollback", "bash"]}

        async def go():
            host.estop = {"on": True, "at": 0, "by": "terminal"}
            await host._app(s, sign(ph, "rescue_do", {"action": "help"}, {"t": "rescue_do", "r": "h1", "action": "help"}))
            for _ in range(100):
                await asyncio.sleep(0.02)
                if host.reset_desk.busy is None and self.got("reset_done"):
                    break
        with mock.patch.object(rescue_kit, "help_flow", fake_flow), \
                mock.patch.object(reset, "run_linger", side_effect=AssertionError("ran")), \
                mock.patch.object(reset, "run_rollback", side_effect=AssertionError("ran")):
            asyncio.run(go())
        self.assertTrue(seen["estop"], "an e-stop reaches help_flow")
        self.assertFalse(seen["linger"][0])
        self.assertFalse(seen["rollback"][0])
        step = [o for o in self.got("reset_step", cid=s.cid) if o.get("offer")]
        self.assertEqual(step[0]["offer"], ["rollback"], "only rescue_do names travel")
        done = [o for o in self.got("reset_done") if o["r"] == "h1"]
        self.assertEqual(len(done), 2)
        self.assertEqual(done[0]["offer"], ["rollback"])
        self.assertEqual(done[0]["flow"], "help")
        self.assertIsNone(host.reset_desk.busy)

    def test_failed_restart_after_help_clears_busy_and_says_so(self):
        ph, other, host, s, s2 = self.host()
        tries = []

        def fake_flow(lang, emit, act, **kw):
            ok, text = act("restart")
            tries.append(("act", ok))
            return {"ok": True, "step": 5, "text": text, "ticket": "st_" + "A" * 22}

        async def fail_restart(self_):
            tries.append("restart")
            return {"ok": False, "why": "not_installed"}

        async def go():
            await host._app(s, sign(ph, "rescue_do", {"action": "help"}, {"t": "rescue_do", "r": "h2", "action": "help"}))
            for _ in range(150):
                await asyncio.sleep(0.02)
                if host.reset_desk.busy is None and len([o for o in self.got("reset_done") if o["r"] == "h2"]) >= 4:
                    break
        with mock.patch.object(rescue_kit, "help_flow", fake_flow), mock.patch.object(reset.HostOps, "restart", fail_restart):
            asyncio.run(go())
        self.assertEqual(tries, [("act", None), "restart"], "scheduled first (never 'ok' before it is tried), then tried")
        done = [o for o in self.got("reset_done", cid=s.cid) if o["r"] == "h2"]
        self.assertEqual(len(done), 2, "the help result, then the restart's real outcome")
        self.assertEqual((done[1]["ok"], done[1]["why"], done[1]["step"]), (False, "restart_failed", 4))
        self.assertIn((4, "fail"), [(o["step"], o["state"]) for o in self.got("reset_step", cid=s.cid) if o["r"] == "h2"])
        self.assertIsNone(host.reset_desk.busy, "busy is cleared after a failed restart")

    def test_restart_after_rollback_clears_busy_even_when_it_raises(self):
        ph, other, host, s, s2 = self.host()

        async def boom(self_):
            raise RuntimeError("x")

        async def go():
            await host._app(s, sign(ph, "rescue_do", {"action": "rollback"}, {"t": "rescue_do", "r": "rb", "action": "rollback"}))
            for _ in range(150):
                await asyncio.sleep(0.02)
                if host.reset_desk.busy is None:
                    break
        with mock.patch.object(reset, "run_rollback", return_value={"ok": True, "version": "0.1"}), \
                mock.patch.object(reset.HostOps, "restart", boom):
            asyncio.run(go())
        self.assertIsNone(host.reset_desk.busy)
        done = [o for o in self.got("reset_done", cid=s.cid) if o["r"] == "rb"]
        self.assertEqual([d["ok"] for d in done], [True, False])

    def test_slash_reset_is_rung_one(self):
        ph, other, host, s, s2 = self.host()
        started = []
        host.reset_desk.start = lambda device, r, flow: started.append((device, r, flow))
        cards = []
        host.cmd_card = lambda name, res, by, turn=None, **k: cards.append((name, res))

        async def go():
            await host.on_slash(s, "reset", "", confirm=False, typed=True)                   # typed, unsigned
            auth = sign(ph, "reset", {"what": "connection"}, {"t": "slash", "cmd": "reset", "r": "c1"})
            await host.on_slash(s, "reset", "", confirm=False, typed=False, authorization=auth)
        asyncio.run(go())
        self.assertEqual([c[1].kind for c in cards], ["refused", "info"])
        self.assertEqual(started, [(s.device, "c1", "reset")])
        self.assertIn("reset", slash.LABEL)
        self.assertIn("/reset", slash.HELP)
        self.assertIn("/reset", slash.REFUSE)

    def test_controls_registered(self):
        self.assertIn("reset", controls.ACTIONS)
        self.assertIn("rescue_do", controls.ACTIONS)
        self.assertEqual(controls.object_text("reset", {"what": "connection"}), "connection")
        self.assertEqual(controls.object_text("rescue_do", {"action": "help"}), "help")

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_js_and_python_sign_the_same_bytes(self):
        cases = [("reset", {"what": "connection"})] + [("rescue_do", {"action": a}) for a in reset.RESCUE_ACTIONS]
        wire_js = pathlib.Path(__file__).resolve().parents[2] / "protocol" / "wire.js"
        js = (f"import {{ controlMessage }} from {json.dumps(wire_js.as_uri())};\n"
              f"const out = [];\nfor (const [a, o] of {json.dumps(cases)}) out.push(Buffer.from(await "
              f"controlMessage('CH', 'DEV', a, '{'b' * 32}', 1790000000123, o)).toString('hex'));\nconsole.log(JSON.stringify(out));")
        r = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        want = [controls.signed_message("CH", "DEV", a, "b" * 32, 1790000000123, controls.object_digest(a, o)).hex() for a, o in cases]
        self.assertEqual(json.loads(r.stdout), want)


if __name__ == "__main__":
    unittest.main()
