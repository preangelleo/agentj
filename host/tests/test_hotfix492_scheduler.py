"""#492 (co-34e3d1, 0.26.5a1 → 0.26.8a2): enabled scheduled tasks never fired and 「立即运行」 never started.

Customer words as the contract: enable a task → at its minute, and after 「立即运行」, a task_run and a report appear within one
minute. The serve-side Scheduler.loop must keep scheduling when recover() / _due() / one task raises, must log the error
(host.log `task_sched_error`) instead of `except Exception: pass`, and a stuck current run (chat-turn lock never released,
or a run that hangs) must not park the queue forever."""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentj import diskq, tasks  # noqa: E402
from agentj.state import State  # noqa: E402

WITHIN = 60.0          # the customer's acceptance: within one minute
TICK = 0.05


def task_dir(work: Path, tid: str, schedule: str) -> Path:
    d = work / "workflows" / tid
    d.mkdir(parents=True, exist_ok=True)
    t = {"v": 1, "id": tid, "title": {"zh": f"任务{tid}", "en": f"Task {tid}"}, "schedule": schedule, "tz": "local",
         "prompt_file": "RUN.md", "dry_run_prompt_file": "DRYRUN.md", "mode": "normal", "enabled": False,
         "needs": ["数据 / data"], "outputs": ["reports/YYYY-MM-DD.md"]}
    (d / "task.json").write_text(json.dumps(t, ensure_ascii=False))
    (d / "RUN.md").write_text("do it\n")
    (d / "DRYRUN.md").write_text("dry\n")
    return d


class Host:
    def __init__(self, root: Path):
        self.root = root
        self.st = State(root / "st")
        self.st.root.mkdir(exist_ok=True)
        self.agent_cfg = {"dir": str(root / "work"), "kind": "fake"}
        self.turn_lock = asyncio.Lock()
        self.agent = None
        self.lang = "zh"
        self.task_label = self.brief_scope = None
        self.paused = False
        self.events: list[tuple[str, dict]] = []
        self.finished: list[dict] = []
        self.scheduler = tasks.Scheduler(self)

    def stopped(self):
        return self.paused

    def activity(self, kind, **kw):
        self.events.append((kind, kw))

    def task_notice(self, text):
        pass

    def task_finished(self, res):
        self.finished.append(res)

    def _online_approvers(self):
        return set()


class Hotfix492(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-492-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for p in (mock.patch.object(tasks, "TICK", TICK), mock.patch.dict("os.environ", {"AGENTJ_TEST": "1"})):
            p.start()
            self.addCleanup(p.stop)

    def host(self, *tids, schedule="0 0 1 1 *"):
        work = self.root / "work"
        h = Host(self.root)
        for tid in tids:
            task_dir(work, tid, schedule)
            tasks.set_enabled(h.st, h.agent_cfg["dir"], tid, True, "terminal")
        h.scheduler._exec = mock.AsyncMock(return_value=("VERDICT: ok — done", 0))
        return h

    @staticmethod
    def reports(h, tid):
        return sorted((Path(h.agent_cfg["dir"]) / "workflows" / tid / "reports").glob("run-*.md"))

    @staticmethod
    def ran(h, tid):
        return [kw for k, kw in h.events if k == "task_run" and kw.get("id") == tid]

    async def until(self, h, tid, n=1, within=WITHIN, rep=None):
        loop = asyncio.get_running_loop()
        end = loop.time() + within
        while loop.time() < end:
            if len(self.ran(h, tid)) >= n and len(self.reports(h, tid)) >= (n if rep is None else rep) and \
                    sum(1 for r in h.finished if r.get("id") == tid) >= n:
                return True
            await asyncio.sleep(0.02)
        return False

    async def start(self, h):
        t = asyncio.create_task(h.scheduler.loop())
        await asyncio.sleep(0)
        return t

    async def stop(self, h, t):
        await h.scheduler.stop_current()
        for x in (t, h.scheduler.ticker, getattr(h.scheduler, "migrate_card", None)):
            if x is not None:
                x.cancel()
                with __import__("contextlib").suppress(BaseException):
                    await x
        await diskq.aflush()

    def errors(self, h):
        try:
            lines = h.st.log_path.read_text().splitlines()
        except FileNotFoundError:
            return []
        return [json.loads(x) for x in lines if '"task_sched_error"' in x]

    # ------------------------------------------------------------------ the customer's two paths, healthy
    def test_scheduled_minute_and_run_now_each_produce_task_run_and_report(self):
        async def go():
            h = self.host("daily", schedule="* * * * *")
            t = await self.start(h)
            try:
                self.assertTrue(await self.until(h, "daily", 1), "scheduled minute: no task_run / report")
                h.scheduler.request("daily", "manual")
                self.assertTrue(await self.until(h, "daily", 2), "立即运行: no task_run / report")
                self.assertEqual({kw["trigger"] for kw in self.ran(h, "daily")}, {"schedule", "manual"})
            finally:
                await self.stop(h, t)
        asyncio.run(go())

    # ------------------------------------------------------------------ recover() raising must not kill the loop
    def test_recover_failure_does_not_kill_the_loop(self):
        async def go():
            h = self.host("daily")
            boom = {"n": 0}
            real = tasks.Scheduler.recover

            def flaky(self_):
                if boom["n"] < 2:            # fails at startup, then works: the loop retries instead of dying
                    boom["n"] += 1
                    raise OSError("tasks.json unreadable")
                return real(self_)
            with mock.patch.object(tasks.Scheduler, "recover", flaky):
                t = await self.start(h)
                try:
                    await asyncio.sleep(TICK * 2)
                    self.assertFalse(t.done(), "Scheduler.loop died after recover() raised")
                    h.scheduler.request("daily", "manual")
                    self.assertTrue(await self.until(h, "daily", 1), "立即运行 after a recover() failure never ran")
                    self.assertIsNotNone(h.scheduler.ticker, "ticker (task_missed) never started")
                finally:
                    await self.stop(h, t)
            self.assertTrue(any(e.get("where") == "recover" for e in self.errors(h)), "recover() failure not logged")
        asyncio.run(go())

    def test_malformed_old_run_record_does_not_block_startup(self):
        async def go():
            h = self.host("daily")
            raw = json.loads(h.st.tasks_path.read_text())
            raw["runs"].update({"old1": {"status": "running", "instance_id": "gone"}, "old2": "garbage"})
            h.st.tasks_path.write_text(json.dumps(raw))
            h.scheduler.recover()            # serve calls this unguarded before the loop starts
            t = await self.start(h)
            try:
                h.scheduler.request("daily", "manual")
                self.assertTrue(await self.until(h, "daily", 1))
            finally:
                await self.stop(h, t)
            runs = tasks.load(h.st)["runs"]
            self.assertEqual(runs["old1"]["status"], "interrupted")
            self.assertNotIn("old2", runs)
        asyncio.run(go())

    # ------------------------------------------------------------------ the loop body: an exception is logged and scheduling goes on
    def test_due_failing_every_tick_does_not_starve_run_now(self):
        async def go():
            h = self.host("daily")
            with mock.patch.object(tasks, "rows", side_effect=RuntimeError("bad row")):
                t = await self.start(h)
                try:
                    await asyncio.sleep(TICK * 2)
                    h.scheduler.request("daily", "manual")
                    self.assertTrue(await self.until(h, "daily", 1), "立即运行 starved behind a failing _due()")
                finally:
                    await self.stop(h, t)
            self.assertTrue(any(e.get("where") == "due" and e.get("exception_type") == "RuntimeError" for e in self.errors(h)),
                            "_due() failure swallowed without a log line")
        asyncio.run(go())

    def test_one_task_failing_does_not_skip_the_other_scheduled_task(self):
        async def go():
            h = self.host("aaa", "zzz", schedule="* * * * *")
            real = tasks.Scheduler.request

            def req(self_, tid, *a, **k):
                if tid == "aaa":
                    raise tasks.TaskError("busy")
                return real(self_, tid, *a, **k)
            with mock.patch.object(tasks.Scheduler, "request", req):
                t = await self.start(h)
                try:
                    self.assertTrue(await self.until(h, "zzz", 1), "one failing task blocked the other's schedule")
                finally:
                    await self.stop(h, t)
        asyncio.run(go())

    def test_crashing_run_is_logged_and_the_next_one_runs(self):
        async def go():
            h = self.host("daily")
            real = tasks.Scheduler.run
            calls = {"n": 0}

            async def run(self_, tid, trigger):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("run crashed before its end record")
                return await real(self_, tid, trigger)
            with mock.patch.object(tasks.Scheduler, "run", run):
                t = await self.start(h)
                try:
                    rid = h.scheduler.request("daily", "manual")
                    await asyncio.sleep(TICK * 3)
                    self.assertEqual(tasks.load(h.st)["runs"][rid]["status"], "interrupted", "crashed run left queued")
                    h.scheduler.request("daily", "manual")
                    self.assertTrue(await self.until(h, "daily", 1))
                finally:
                    await self.stop(h, t)
            self.assertTrue(any(e.get("where") == "run" for e in self.errors(h)))
        asyncio.run(go())

    # ------------------------------------------------------------------ a stuck current recovers
    def test_chat_turn_lock_never_released_does_not_park_the_queue_forever(self):
        async def go():
            h = self.host("daily")
            await h.turn_lock.acquire()      # a chat turn that never ends
            with mock.patch.object(tasks, "LOCK_WAIT", 0.3, create=True):
                t = await self.start(h)
                try:
                    rid = h.scheduler.request("daily", "manual")
                    loop = asyncio.get_running_loop()
                    end = loop.time() + WITHIN
                    while loop.time() < end and tasks.load(h.st)["runs"][rid]["status"] in ("queued", "running"):
                        await asyncio.sleep(0.02)
                    rec = tasks.load(h.st)["runs"][rid]
                    self.assertEqual(rec["status"], "finished")
                    self.assertIn("对话一直占用", rec["line"])
                    while h.scheduler.current_id is not None and loop.time() < end:
                        await asyncio.sleep(0.02)
                    self.assertIsNone(h.scheduler.current_id, "self.current stayed stuck")
                    h.scheduler._exec.assert_not_awaited()
                    h.turn_lock.release()    # the turn ends → the next 「立即运行」 runs normally
                    h.scheduler.request("daily", "manual")
                    self.assertTrue(await self.until(h, "daily", 2, rep=1))
                finally:
                    await self.stop(h, t)
        asyncio.run(go())

    def test_hung_run_is_cut_after_its_budget(self):
        async def go():
            h = self.host("daily")
            gate = {"hang": True}

            async def exe(*a, **k):
                if gate["hang"]:
                    await asyncio.Event().wait()     # hangs past every bound
                return "VERDICT: ok — done", 0
            h.scheduler._exec = exe
            with mock.patch.object(tasks.Scheduler, "_budget", lambda self_, tid: 0.3 if gate["hang"] else WITHIN, create=True), \
                    mock.patch.object(tasks, "RUN_TIMEOUT", 3600):
                t = await self.start(h)
                try:
                    first = h.scheduler.request("daily", "manual")
                    loop = asyncio.get_running_loop()
                    end = loop.time() + WITHIN
                    while loop.time() < end and tasks.load(h.st)["runs"][first]["status"] in ("queued", "running"):
                        await asyncio.sleep(0.02)
                    self.assertEqual(tasks.load(h.st)["runs"][first]["status"], "interrupted")
                    while h.scheduler.current_id is not None and loop.time() < end:
                        await asyncio.sleep(0.02)
                    self.assertIsNone(h.scheduler.current_id)
                    gate["hang"] = False
                    h.scheduler.request("daily", "manual")
                    self.assertTrue(await self.until(h, "daily", 2, rep=1), "queue stayed parked behind a hung run")
                finally:
                    await self.stop(h, t)
            self.assertTrue(any(e.get("where") == "stuck" for e in self.errors(h)))
        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
