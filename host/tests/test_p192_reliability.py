"""P192 (0.20, audit B13/B15/B17/B18): host reliability after P142.

- B13: no bare `create_task(...)` / `ensure_future(...)` statement anywhere in agentj (P142 cleared serve.py only);
  `bg.spawn` holds the task and logs a failure (type only); the codex / opencode `_bg` log failures too.
- B15: `update apply --authorization` rolls back to the cached old wheel like `apply`; `apply`'s OSError before the
  service hand-off rolls back instead of leaving a half-installed version.
- B17: any error in a shared observe pass is retried (a KeyError used to end the task); doctor's serve row shows a
  shared session marked down by the observe loop.
- B18: swallowed errors on connection / upgrade / approval paths leave a rate-limited host.log line; doctor rows keep the
  exception message, redacted."""
import _hermetic  # noqa: F401,I001
import ast
import asyncio
import gc
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentj import __version__, bg, diskq, doctor, names, shared, update  # noqa: E402
from agentj.state import State  # noqa: E402

from test_p142_update import INFO, Runner, wheel  # noqa: E402

AGENTJ = Path(bg.__file__).parent


def events(st):
    diskq.flush()
    if not st.log_path.exists():
        return []
    return [json.loads(x) for x in st.log_path.read_text().splitlines()]


class Tmp(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp(prefix="aj-p192-"))
        self.addCleanup(shutil.rmtree, d, True)
        self.st = State(d / "st")
        self.st.root.mkdir(parents=True)
        bg._last.clear()


class B13Spawn(Tmp):
    def test_no_bare_task_statement_in_agentj(self):
        bare = []
        for p in sorted(AGENTJ.rglob("*.py")):
            for n in ast.walk(ast.parse(p.read_text())):
                if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call):
                    f = n.value.func
                    if (f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")) in ("create_task", "ensure_future"):
                        bare.append(f"{p.name}:{n.lineno}")
        self.assertEqual(bare, [], "use bg.spawn(coro, what, log) / Host._bg")

    def test_failure_held_and_logged_without_message(self):
        async def boom():
            await asyncio.sleep(0)
            raise KeyError("token-like-detail")

        async def main():
            t = bg.spawn(boom(), "probe", self.st.log)
            self.assertIn(t, bg._held)
            gc.collect()
            await asyncio.sleep(0.02)
            self.assertNotIn(t, bg._held)
        asyncio.run(main())
        ev = [e for e in events(self.st) if e["ev"] == "bg_task_failed"]
        self.assertEqual([(e["why"], e["exception_type"]) for e in ev], [("probe", "KeyError")])
        self.assertNotIn("token-like", self.st.log_path.read_text())

    def test_success_and_cancel_are_quiet(self):
        async def main():
            bg.spawn(asyncio.sleep(0), "ok", self.st.log)
            t = bg.spawn(asyncio.sleep(10), "long", self.st.log)
            await asyncio.sleep(0.01)
            t.cancel()
            await asyncio.sleep(0.01)
            self.assertEqual(bg._held, set())
        asyncio.run(main())
        self.assertEqual(events(self.st), [])

    def test_no_loop_raises_and_closes_the_coroutine(self):
        async def never():
            pass
        c = never()
        with self.assertRaises(RuntimeError):
            bg.spawn(c, "x")
        self.assertIsNone(c.cr_frame, "closed: no 'never awaited' warning")

    def test_harness_bg_logs_failures(self):
        from agentj import agent_codex, agent_opencode

        class H:
            st = self.st
        for cls, why in ((agent_codex.CodexAgent, "codex"), (agent_opencode.OpenCodeAgent, "opencode")):
            a = object.__new__(cls)
            a.host, a.tasks = H(), set()

            async def boom():
                raise OSError("x")

            async def main():
                a._bg(boom())
                await asyncio.sleep(0.01)
            asyncio.run(main())
            self.assertIn(("bg_task_failed", why), [(e["ev"], e.get("why")) for e in events(self.st)])


class B18Swallowed(Tmp):
    def test_rate_limited_and_folded(self):
        for _ in range(5):
            bg.swallowed(self.st.log, "post_failed", ConnectionResetError(), why="send_app")
        self.assertEqual(len(events(self.st)), 1)
        bg._last[("post_failed", "send_app", "ConnectionResetError")][0] -= 61
        self.assertTrue(bg.swallowed(self.st.log, "post_failed", ConnectionResetError(), why="send_app"))
        ev = events(self.st)
        self.assertEqual(len(ev), 2)
        self.assertEqual(ev[1]["count"], 5, "the 4 folded + this one")

    def test_message_never_logged_and_none_is_noop(self):
        bg.swallowed(self.st.log, "perm_send_failed", OSError("/home/someone/secret path"), every=0)
        self.assertNotIn("secret", self.st.log_path.read_text())
        self.assertFalse(bg.swallowed(None, "x", OSError()))

    def test_post_loop_logs_and_keeps_running(self):
        from agentj import serve
        h = object.__new__(serve.Host)
        h.st = self.st
        h.post_q = asyncio.Queue()
        done = []

        async def bad():
            raise BrokenPipeError()

        async def good():
            done.append(1)

        async def main():
            t = asyncio.ensure_future(h.post_loop())
            await h.post_q.put((bad, ()))
            await h.post_q.put((good, ()))
            for _ in range(50):
                await asyncio.sleep(0)
            t.cancel()
        asyncio.run(main())
        self.assertEqual(done, [1])
        self.assertIn(("post_failed", "bad", "BrokenPipeError"),
                      [(e["ev"], e.get("why"), e.get("exception_type")) for e in events(self.st)])

    def test_perm_send_failure_logged(self):
        from agentj import serve
        h = object.__new__(serve.Host)
        h.st = self.st

        class W:
            def write(self, b):
                raise ConnectionResetError()

        asyncio.run(h._perm_send(W(), {"t": "x"}))
        self.assertEqual([e["ev"] for e in events(self.st)], ["perm_send_failed"])


class B18DoctorWhy(unittest.TestCase):
    def test_message_kept_and_redacted(self):
        e = OSError("open /home/alice/.config/agentj/x failed via https://api.telegram.org/bot123456:ABCdefGhIJKlmnOPqrSTuvWXyz012345/getMe")
        w = doctor._why(e)
        self.assertTrue(w.startswith("OSError: open ~/"), w)
        self.assertIn("https://api.telegram.org/…", w)
        self.assertNotIn("alice", w)
        self.assertNotIn("ABCdef", w)

    def test_plain_message_kept_and_empty_falls_back(self):
        self.assertEqual(doctor._why(ValueError("bad state")), "ValueError: bad state")
        self.assertEqual(doctor._why(TimeoutError()), "TimeoutError")
        self.assertLessEqual(len(doctor._why(ValueError("x" * 500))), 200)


class B17DoctorRow(Tmp):
    def row(self, res):
        if not self.st.exists():
            self.st.init()
        with patch.object(names, "ctl_call", return_value=res):
            return doctor.check_serve(self.st)

    def test_down_is_warn_with_reconnect_hint(self):
        r = self.row({"ok": True, "relay_up": True, "relay_state": "up",
                      "agent_observe": {"errors": 5, "down": True, "error": "PermissionError"}})
        self.assertEqual(r["status"], doctor.WARN)
        self.assertIn("PermissionError", r["summary"])
        self.assertIn("--reconnect", r["hint"])

    def test_retrying_is_noted_and_healthy_unchanged(self):
        r = self.row({"ok": True, "relay_up": True, "relay_state": "up", "agent_observe": {"errors": 2, "down": False}})
        self.assertEqual(r["status"], doctor.OK)
        self.assertIn("×2", r["summary"])
        r = self.row({"ok": True, "relay_up": True, "relay_state": "up", "agent_observe": {"errors": 0, "down": False}})
        self.assertEqual((r["status"], r["summary"]), (doctor.OK, "在运行 / running · relay connected"))
        r = self.row({"ok": True, "relay_up": True, "relay_state": "up"})     # an older serve: no field
        self.assertEqual(r["status"], doctor.OK)


class B17AnyError(unittest.IsolatedAsyncioTestCase):
    async def test_key_error_is_retried_not_fatal(self):
        d = tempfile.mkdtemp(prefix="aj-p192obs-")
        self.addCleanup(shutil.rmtree, d, True)

        class H:
            def __init__(self):
                self.st = State(Path(d) / "st")
                self.st.root.mkdir(parents=True)
                self.statuses = []

            def agent_status(self, s):
                self.statuses.append(s)
        h = H()
        a = shared.SharedClaudeAgent(h, {"kind": "claude", "dir": "/nonexistent", "_workflow_ceo": True, "language": "zh"})
        a.session = {"sessionId": "x"}
        a.transcript = Path(d) / "t.jsonl"
        a.transcript.write_text("")
        a.status = "idle"
        calls = {"n": 0}

        def read():
            calls["n"] += 1
            if calls["n"] == 1:
                raise KeyError("message")
        real = asyncio.sleep

        async def fast(_s):
            await real(0)
        with patch.object(a, "read_transcript", read), patch.object(a, "read_statusline", lambda: None), \
                patch.object(a, "sync_compacting", lambda: None), patch.object(shared.asyncio, "sleep", fast):
            task = asyncio.ensure_future(a.observe())
            while calls["n"] < 3 and not task.done():
                await real(0)
            self.assertFalse(task.done(), "the loop survived a KeyError")
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual((a._observe_errors, a._observe_error), (0, "KeyError"))
        await diskq.aflush()
        self.assertIn("shared_observe_retry", [e["ev"] for e in events(h.st)])


class B15Rollback(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-p192up-")
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {"HOME": self.tmp.name, "AGENTJ_SERVICE_NAME": "aj.p192.test"})
        env.start()
        self.addCleanup(env.stop)
        self.st = State(Path(self.tmp.name) / "st")
        self.st.init()

    def authorized(self, run, old, svc_on=True):
        from agentj import cloud
        with patch.object(update, "install_kind", return_value=INFO), patch.object(update, "new_argv", return_value=["agentj"]), \
                patch.object(update, "writable", return_value=True), patch.object(cloud, "read_cloud", return_value={"x": 1}):
            return update.authorized_apply(self.st, "AJUP-ABCDE-FGHJK-MNPQR-STVWX", "9.9.9",
                                           plan_fn=lambda st, info, target: update.commands(info, target),
                                           check_fn=lambda: {"status": "newer", "latest": "9.9.9"},
                                           auth_fn=lambda st, c, v: {"status": "ok", "http": "200"},
                                           run=run, svc_on=svc_on, rollback_fn=lambda st: old)

    def test_authorized_version_mismatch_rolls_back(self):
        old = wheel(self.st, __version__)
        run = Runner(current="1.0.0")
        res = self.authorized(run, old)
        self.assertEqual((res["reason"], res["rollback"]), ("not_upgraded", "ok"))
        self.assertTrue(any(str(old) in " ".join(map(str, c)) for c in run.calls), "the old wheel went back")
        self.assertFalse((self.st.root / update.PENDING).exists())

    def test_authorized_service_failure_rolls_back(self):
        old = wheel(self.st, __version__)
        res = self.authorized(Runner(fail=("--deferred",)), old)
        self.assertEqual((res["reason"], res["rollback"], res["service"]), ("service_restart_failed", "ok", "failed"))
        self.assertFalse((self.st.root / update.PENDING).exists())

    def test_authorized_success_pending_carries_old_wheel(self):
        old = wheel(self.st, __version__)
        res = self.authorized(Runner(), old)
        self.assertEqual((res["result"], res["service"]), ("ok", "restart_scheduled"))
        self.assertNotIn("rollback", res)
        self.assertEqual(update.read_pending(self.st, "9.9.9")["old"], str(old))

    def test_apply_oserror_before_handoff_rolls_back(self):
        old = wheel(self.st, __version__)
        base = Runner()

        def run(argv, **kw):
            if "--deferred" in argv:
                raise OSError("exec format error")
            return base(argv, **kw)
        with patch.object(update, "install_kind", return_value=INFO), patch.object(update, "new_argv", return_value=["agentj"]), \
                patch.object(update, "writable", return_value=True):
            res = update.apply(self.st, "9.9.9", plan_fn=lambda st, info, target: update.commands(info, target),
                               check_fn=lambda: {"status": "newer", "latest": "9.9.9"}, run=run, svc_on=True,
                               rollback_fn=lambda st: old)
        self.assertEqual((res["reason"], res["rollback"], res["service"]), ("self_check_failed", "ok", "failed"))
        self.assertFalse((self.st.root / update.PENDING).exists())

    def test_apply_oserror_after_handoff_does_not_roll_back(self):
        old = wheel(self.st, __version__)
        base = Runner()

        def run(argv, **kw):
            if "doctor" in argv:
                raise OSError("gone")
            return base(argv, **kw)
        with patch.object(update, "install_kind", return_value=INFO), patch.object(update, "new_argv", return_value=["agentj"]), \
                patch.object(update, "writable", return_value=True):
            res = update.apply(self.st, "9.9.9", plan_fn=lambda st, info, target: update.commands(info, target),
                               check_fn=lambda: {"status": "newer", "latest": "9.9.9"}, run=run, svc_on=True,
                               rollback_fn=lambda st: old)
        self.assertEqual(res["reason"], "self_check_failed")
        self.assertNotIn("rollback", res, "the service already took over: the recovery worker owns rollback now")
        self.assertTrue((self.st.root / update.PENDING).exists())

    def test_rollback_wheel_failure_logged(self):
        from agentj import auto_update
        with patch.object(auto_update, "cached_wheel", side_effect=ValueError("nope")):
            self.assertIsNone(update.rollback_wheel(self.st))
        self.assertIn(("rollback_wheel_failed", "ValueError"),
                      [(e["ev"], e.get("exception_type")) for e in events(self.st)])


if __name__ == "__main__":
    unittest.main()
