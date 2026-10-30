"""P418 (#183 ③): scheduled runs missed while the computer was off / asleep.

serve keeps `tasks_tick.json` (0600, the last minute it was alive). After a gap it counts, per enabled task, the cron minutes
it missed (since that tick / its enabling, 7 days back at most, capped at 1000), sends ONE summary notice and one multiple-
choice card (no "Other"); every ticked task is requested once (trigger `catch_up`, the normal run path). No answer / no
phone / "run none" = nothing runs; stop-everything = no card, no run. First start (no tick yet) reports nothing."""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentj import ask, tasks  # noqa: E402
from agentj.state import State  # noqa: E402

DAY = 86400


def task_dir(work: Path, tid: str, schedule: str, title=None) -> Path:
    d = work / "workflows" / tid
    d.mkdir(parents=True, exist_ok=True)
    t = {"v": 1, "id": tid, "title": title or {"zh": f"任务{tid}", "en": f"Task {tid}"}, "schedule": schedule, "tz": "local",
         "prompt_file": "RUN.md", "dry_run_prompt_file": "DRYRUN.md", "mode": "normal", "enabled": False,
         "needs": ["订单数据 / orders"], "outputs": ["reports/YYYY-MM-DD.md"]}
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
        self.notices: list[str] = []
        self.cards: list = []
        self.answer = ("timeout", None)
        self.online = True
        self.finished = []
        self.scheduler = tasks.Scheduler(self)

    def stopped(self):
        return self.paused

    def activity(self, *a, **k):
        pass

    def task_notice(self, text):
        self.notices.append(text)

    def task_finished(self, res):
        self.finished.append(res)

    def _online_approvers(self):
        return {"dev"} if self.online else set()

    async def question(self, card, gone, task=None):
        self.cards.append(card)
        return self.answer


class Missed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-p418t-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.h = Host(self.root)
        self.sch = self.h.scheduler
        self.now = time.time()

    def enable(self, tid, schedule, since_days=60, title=None):
        task_dir(self.root / "work", tid, schedule, title)
        tasks.set_enabled(self.h.st, self.h.agent_cfg["dir"], tid, True, "terminal")
        at = int(self.now - since_days * DAY)
        tasks.update(self.h.st, lambda d: d["on"][tid].__setitem__("at", at))

    def last_alive(self, ago_s):
        tasks.write_tick(self.h.st, int((self.now - ago_s) // 60 * 60))

    def run_tick(self):
        async def go():
            miss = self.sch.tick(self.now)
            if self.sch.offer is not None:
                await self.sch.offer
            return miss
        return asyncio.run(go())

    # ---------------------------------------------------------------- counting
    def test_missed_runs_counts_and_bounds(self):
        T = self.now // 3600 * 3600 + 30 * 60 + 7   # mid-hour: at minute :00 the 7-day window's first hour is its live minute (167)
        self.assertEqual(tasks.missed_runs("0 8 * * *", "local", T - 3 * DAY, T), 3)
        self.assertEqual(tasks.missed_runs("0 */6 * * *", "local", T - 3 * DAY, T), 12)
        # 7 days back at most, however long the computer was off
        self.assertEqual(tasks.missed_runs("0 8 * * *", "local", T - 40 * DAY, T), 7)
        self.assertEqual(tasks.missed_runs("0 * * * *", "local", T - 40 * DAY, T), 168)
        # capped: every minute for 7 days stops at 1000
        self.assertEqual(tasks.missed_runs("* * * * *", "local", T - 40 * DAY, T), tasks.CATCHUP_MAX)
        # the live minute and the last alive minute are not "missed"
        m = T // 60 * 60
        self.assertEqual(tasks.missed_runs("* * * * *", "local", m - 60, m + 5), 0)
        self.assertEqual(tasks.missed_runs("* * * * *", "local", m - 180, m + 5), 2)

    def test_ran_minute_is_not_missed(self):
        m = self.now // 60 * 60
        key = tasks.minute_key("local", __import__("datetime").datetime.fromtimestamp(m - 120).astimezone())
        self.assertEqual(tasks.missed_runs("* * * * *", "local", m - 240, m + 5, key), 2)

    # ---------------------------------------------------------------- first start / small gaps
    def test_first_start_reports_nothing_and_writes_a_private_tick(self):
        self.enable("daily", "0 8 * * *")
        self.assertEqual(self.run_tick(), [])
        self.assertEqual(self.h.notices, [])
        self.assertEqual(self.h.cards, [])
        p = tasks.tick_path(self.h.st)
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
        self.assertEqual(tasks.read_tick(self.h.st), int(self.now // 60 * 60))

    def test_short_gap_is_not_a_shutdown(self):
        self.enable("every", "* * * * *")
        self.last_alive(120)
        self.assertEqual(self.run_tick(), [])
        self.assertEqual(self.h.notices, [])

    def test_enabled_after_the_gap_started_counts_from_the_enabling(self):
        self.enable("daily", "0 8 * * *", since_days=2)
        self.last_alive(30 * DAY)
        miss = self.run_tick()
        self.assertEqual([m["n"] for m in miss], [2])

    def test_disabled_tasks_are_not_reported(self):
        self.enable("daily", "0 8 * * *")
        tasks.set_enabled(self.h.st, self.h.agent_cfg["dir"], "daily", False, "terminal")
        self.last_alive(3 * DAY)
        self.assertEqual(self.run_tick(), [])
        self.assertEqual(self.h.notices, [])

    # ---------------------------------------------------------------- one notice + one card
    def test_one_summary_notice_and_one_multiple_choice_card(self):
        self.enable("daily", "0 8 * * *", title={"zh": "日报", "en": "Daily"})
        self.enable("quarter", "0 */6 * * *", title={"zh": "巡检", "en": "Patrol"})
        self.last_alive(3 * DAY)
        miss = self.run_tick()
        self.assertEqual({m["id"]: m["n"] for m in miss}, {"daily": 3, "quarter": 12})
        self.assertEqual(len(self.h.notices), 1)
        n = self.h.notices[0]
        self.assertIn("Agent J 主机关机或休眠期间", n)
        self.assertIn("日报（3 次）", n)
        self.assertIn("巡检（12 次）", n)
        for word in ("relay", "serve", "harness"):
            self.assertNotIn(word, n.lower())
        self.assertEqual(len(self.h.cards), 1)
        card = self.h.cards[0]
        self.assertEqual(len(card), 1)
        q = card[0]
        self.assertTrue(q["m"])
        labels = [o["l"] for o in q["o"]]
        self.assertEqual(labels[-1], "都不补跑")
        self.assertIn("补跑一次：日报", labels)
        self.assertNotIn(ask.OTHER["zh"], labels)
        self.assertEqual(self.sch.waiting, [])          # timeout: nothing runs
        self.assertEqual(tasks.load(self.h.st)["runs"], {})

    def test_picked_tasks_each_run_once(self):
        self.enable("daily", "0 8 * * *", title={"zh": "日报", "en": "Daily"})
        self.enable("quarter", "0 */6 * * *", title={"zh": "巡检", "en": "Patrol"})
        self.last_alive(3 * DAY)
        # order of the card follows discovery: daily, quarter, run none → tick both
        self.h.answer = ("answer", [[1, 2]])
        self.run_tick()
        self.assertEqual(sorted(self.sch.waiting), [("daily", "catch_up"), ("quarter", "catch_up")])
        runs = tasks.load(self.h.st)["runs"]
        self.assertEqual(sorted((r["id"], r["trigger"]) for r in runs.values()),
                         [("daily", "catch_up"), ("quarter", "catch_up")])

    def test_only_the_picked_one(self):
        self.enable("daily", "0 8 * * *")
        self.enable("quarter", "0 */6 * * *")
        self.last_alive(3 * DAY)
        self.h.answer = ("answer", [[2]])
        self.run_tick()
        self.assertEqual(self.sch.waiting, [("quarter", "catch_up")])

    def test_run_none_cancel_and_no_answer_run_nothing(self):
        for answer in (("answer", [[3]]), ("answer", [[1, 3]]), ("cancel", None), ("timeout", None), ("no_device", None),
                       ("gone", None)):
            with self.subTest(answer=answer):
                self.setUp()
                self.enable("daily", "0 8 * * *")
                self.enable("quarter", "0 */6 * * *")
                self.last_alive(3 * DAY)
                self.h.answer = answer
                self.run_tick()
                self.assertEqual(len(self.h.notices), 1)
                self.assertEqual(self.sch.waiting, [])
                self.assertEqual(tasks.load(self.h.st)["runs"], {})

    def test_stop_everything_no_card_no_run(self):
        self.enable("daily", "0 8 * * *")
        self.last_alive(3 * DAY)
        self.h.paused = True
        self.h.answer = ("answer", [[1]])
        self.run_tick()
        self.assertEqual(len(self.h.notices), 1)
        self.assertIn("全部停下", self.h.notices[0])
        self.assertEqual(self.h.cards, [])
        self.assertIsNone(self.sch.offer)
        self.assertEqual(self.sch.waiting, [])

    def test_no_phone_online_waits_then_leaves_the_notice_only(self):
        self.enable("daily", "0 8 * * *")
        self.last_alive(3 * DAY)
        self.h.online = False
        with mock.patch.object(tasks, "CATCHUP_WAIT", 0.05), mock.patch.object(tasks, "CATCHUP_POLL", 0.01):
            self.run_tick()
        self.assertEqual(len(self.h.notices), 1)
        self.assertEqual(self.h.cards, [])
        self.assertEqual(self.sch.waiting, [])

    def test_card_waits_for_a_phone_to_come_online(self):
        self.enable("daily", "0 8 * * *")
        self.last_alive(3 * DAY)
        self.h.online = False
        self.h.answer = ("answer", [[1]])

        async def go():
            with mock.patch.object(tasks, "CATCHUP_POLL", 0.01):
                self.sch.tick(self.now)
                await asyncio.sleep(0.05)
                self.assertEqual(self.h.cards, [])
                self.h.online = True
                await self.sch.offer
        asyncio.run(go())
        self.assertEqual(len(self.h.cards), 1)
        self.assertEqual(self.sch.waiting, [("daily", "catch_up")])

    def test_estop_while_waiting_for_a_phone_runs_nothing(self):
        self.enable("daily", "0 8 * * *")
        self.last_alive(3 * DAY)
        self.h.online = False
        self.h.answer = ("answer", [[1]])

        async def go():
            with mock.patch.object(tasks, "CATCHUP_POLL", 0.01):
                self.sch.tick(self.now)
                await asyncio.sleep(0.03)
                self.h.paused = True
                self.h.online = True
                await self.sch.offer
        asyncio.run(go())
        self.assertEqual(self.h.cards, [])
        self.assertEqual(self.sch.waiting, [])

    def test_same_gap_reported_once(self):
        self.enable("daily", "0 8 * * *")
        self.last_alive(3 * DAY)
        self.run_tick()
        self.assertEqual(len(self.h.notices), 1)
        # a restarted serve (new scheduler) right after: the tick was written first, the gap is gone
        self.sch = tasks.Scheduler(self.h)
        self.now += 60
        self.run_tick()
        self.assertEqual(len(self.h.notices), 1)

    def test_sleep_inside_one_serve_is_a_gap_too(self):
        self.enable("daily", "0 8 * * *")
        self.last_alive(60)
        self.run_tick()
        self.assertEqual(self.h.notices, [])
        self.now += 3 * DAY                      # the computer slept with serve in memory
        self.run_tick()
        self.assertEqual(len(self.h.notices), 1)

    # ---------------------------------------------------------------- the owner's names on the card
    def test_long_and_hostile_names_fit_the_card(self):
        # taskspec already refuses control characters in a title; task_name cleans anyway (defence in depth)
        self.assertEqual(tasks.task_name({"id": "x", "title": {"zh": "日\u202e报\x07", "en": "D"}}, "zh"), "日 报")
        self.enable("a1", "0 8 * * *", title={"zh": "超级无敌长的每日经营数据汇总日报", "en": "A very long daily report name"})
        self.enable("a2", "0 8 * * *", title={"zh": "超级无敌长的每日经营数据汇总周报", "en": "A very long daily report name"})
        self.last_alive(3 * DAY)
        for lang in ("zh", "en"):
            with self.subTest(lang=lang):
                card, ids = tasks.catchup_card(tasks.missed(self.h.st, self.h.agent_cfg["dir"],
                                                            int(self.now - 3 * DAY), self.now, lang), lang)
                labels = [o["l"] for o in card[0]["o"]]
                self.assertEqual(len(set(labels)), len(labels))
                for lb in labels:
                    self.assertLessEqual(ask.zi(lb), ask.LABEL_MAX, lb)
                    self.assertNotIn("\u202e", lb)
                    self.assertNotIn("\x07", lb)
                self.assertEqual(ids, ["a1", "a2", None])
        self.h.lang = "en"
        self.run_tick()
        self.assertIn("While the computer was off or asleep", self.h.notices[0])
        self.assertTrue(self.h.cards[0][0]["o"][0]["l"].startswith("Run once: "))

    def test_many_tasks_card_stays_within_the_option_limit(self):
        miss = [{"id": f"t{i}", "name": f"任务{i}", "n": 1} for i in range(30)]
        card, ids = tasks.catchup_card(miss, "zh")
        self.assertEqual(len(card[0]["o"]), tasks.CATCHUP_OPTS + 1)
        self.assertIn("等 30 个", tasks.missed_text(miss, "zh", True))

    # ---------------------------------------------------------------- the run path
    def test_catch_up_run_takes_the_normal_path_and_respects_disabling(self):
        self.enable("daily", "0 8 * * *")

        async def fake_exec(*a):
            return "VERDICT: ok — done", 0
        self.sch._exec = fake_exec

        async def go(disable):
            rid = self.sch.request("daily", "catch_up")
            if disable:
                tasks.set_enabled(self.h.st, self.h.agent_cfg["dir"], "daily", False, "terminal")
            res = await self.sch.run("daily", "catch_up")
            return rid, res
        _, res = asyncio.run(go(False))
        self.assertEqual(res["verdict"], "ok")
        self.assertEqual(res["trigger"], "catch_up")
        self.assertEqual(self.h.finished[-1]["verdict"], "ok")
        _, res = asyncio.run(go(True))
        self.assertEqual(res["verdict"], "fail")
        self.assertIn("禁用", res["line"])


if __name__ == "__main__":
    unittest.main()
