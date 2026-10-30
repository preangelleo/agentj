"""P570c (P570b rework, chairman 10-29 18:2x): the move card is a plain confirm (no Face ID) from a paired device, logged;
user texts say 「Agent J 主机」, never a bare 「电脑」; the phone's 定时任务 page gets `sys` (migrated | changed) and a signed
`task_migrate`; WSL2 without systemd gets one plain notice per version; a new task gets the system-scheduler hint; a loop
wake-up refused for approval tells the owner, in plain words, to use a scheduled task instead."""
import _hermetic  # noqa: F401,I001
import asyncio
import contextlib
import io
import json
import os
import pathlib
import re
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

HOST = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOST))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import cli, controls, provenance, sysmigrate, tasks  # noqa: E402

import test_p570b_migrate as m  # noqa: E402  (a module: its TestCases are not collected twice)


class Card(m.Base):
    def test_plain_confirm_card_and_no_face_id_wording(self):
        m.task(self.work, "a")
        self.enable("a")
        h = m.Host(self.st, m.YES)
        self.assertEqual(asyncio.run(sysmigrate.offer(h, plat="linux")), "yes")
        self.assertNotIn("require_passkey", h.asks[0][2], "chairman: 普通确认按钮，不扫 Face ID")
        self.assertIn("花钱、删数据等）也不再问", h.asks[0][1]["summary"], "the cost stays on the card")
        src = (HOST / "agentj" / "sysmigrate.py").read_text()
        self.assertNotIn("用 Face ID 确认", src)
        self.assertNotIn("Face ID", sysmigrate.why("none") + sysmigrate.why("none", "en"))


class FixtureShutdown(unittest.TestCase):
    def test_pending_disk_write_finishes_before_fixture_directory_is_removed(self):
        import threading
        import shutil
        from agentj import diskq
        import test_p283_tg_pair as tg_fixture
        import test_p71_peer_service as peer_fixture
        for base in (tg_fixture.Pairing, peer_fixture.Base):
            with self.subTest(fixture=base.__module__):
                events, chosen = [], []
                release, started = threading.Event(), threading.Event()
                real_flush, real_cleanup = diskq.aflush, tempfile.TemporaryDirectory.cleanup
                async def observed_flush(*a, **kw):
                    if chosen:
                        events.append("flush")
                        release.set()
                    return await real_flush(*a, **kw)
                def observed_cleanup(tmp):
                    if chosen and Path(tmp.name) == chosen[0]:
                        events.append("remove")
                        release.set()
                    return real_cleanup(tmp)
                class Probe(base):
                    async def runTest(case):
                        chosen.append(Path(case.tmp.name))
                        def write_later():
                            started.set()
                            if release.wait(diskq.LOOP_END_FLUSH):
                                (chosen[0] / "late-write").write_text("fixture data")
                                events.append("write")
                        diskq._submit(write_later)
                        case.assertTrue(await asyncio.to_thread(started.wait, diskq.LOOP_END_FLUSH))
                result = unittest.TestResult()
                try:
                    with mock.patch.object(diskq, "aflush", observed_flush), mock.patch.object(tempfile.TemporaryDirectory, "cleanup", observed_cleanup):
                        Probe("runTest").run(result)
                    self.assertTrue(diskq.flush(diskq.LOOP_END_FLUSH))
                    self.assertEqual(events[0], "flush", events)
                    self.assertLess(events.index("write"), events.index("remove"), events)
                    self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
                finally:
                    release.set()
                    diskq.flush(diskq.LOOP_END_FLUSH)
                    if chosen:
                        shutil.rmtree(chosen[0], ignore_errors=True)


class OptionWidths(unittest.TestCase):
    def test_host_wording_keeps_both_opencode_question_cards_buildable(self):
        from agentj import ask, shared
        for name in ("OWNER_DOWN_OPTS", "OWNER_DOWN_PW_OPTS"):
            for lang in ("zh", "en"):
                opts = [{**o, "description": o["description"].format(port=1234)} for o in getattr(shared, name)[lang]]
                with self.subTest(name=name, lang=lang):
                    card, _ = ask.build([{"question": "OpenCode?", "header": "OpenCode", "options": opts, "other": False}], lang)
                    self.assertEqual(len(card[0]["o"]), 2)


class HeartbeatWords(unittest.TestCase):
    def test_old_watchlist_is_preserved_without_a_duplicate(self):
        from agentj import heartbeat
        old = heartbeat.CHECKLIST["zh"].replace("Agent J 主机", "电脑")
        custom = "我的额外要求\n" + old
        self.assertEqual(heartbeat.merge_checklist(custom, "zh"), custom)
        self.assertTrue(heartbeat.has_checklist(custom, "zh"))
        self.assertEqual(heartbeat.merge_checklist("自定义", "zh").count("<!-- agentj:watchlist v1 -->"), 1)
        self.assertNotIn("电脑", heartbeat.CHECKLIST["zh"])


class Wording(unittest.TestCase):
    """TERMS P555: 「Agent J 主机」, never a bare 「电脑」 / "the computer" in what the owner reads."""

    def texts(self, lang):
        out = [sysmigrate.why(k, lang) for k in sysmigrate.WHY]
        out += [sysmigrate.card_text(["x"], lang), sysmigrate.hint("x", lang), sysmigrate.hint("x", lang, True),
                sysmigrate.pick(lang, *sysmigrate.WSL), sysmigrate.pick(lang, *provenance.LOOP_OWNER)]
        with mock.patch.object(sysmigrate, "_run", return_value=(0, "Linger=no\n")), mock.patch.object(sys, "platform", "linux"):
            out.append(sysmigrate.summary([{"id": "a", "title": None, "result": "migrated"}], lang))
        return out

    def test_no_bare_computer(self):
        for s in self.texts("zh"):
            self.assertNotIn("电脑", s)
        for s in self.texts("en"):
            self.assertNotRegex(s, r"(?i)\bcomputer\b")
        for lit in re.findall(r'"[^"\n]*"', (HOST / "agentj" / "sysmigrate.py").read_text()):
            self.assertNotIn("电脑", lit)
        self.assertIn("Agent J 主机自己的系统定时", sysmigrate.card_text(["x"], "zh"))
        self.assertIn("the Agent J host's own scheduler", sysmigrate.card_text(["x"], "en"))
        zh = sysmigrate.why("migrated_on")
        self.assertIn("在手机「定时任务」页点「退回」", zh)
        self.assertNotIn("在电脑上运行", zh)


class PhonePage(m.Base):
    """The 定时任务 page's contract with web: `sys` on task_list rows; `task_migrate` {id, tsha} signed like task_set."""

    def setUp(self):
        super().setUp()
        from test_l1 import Phone, _owner_host, _ready
        self.sent = []
        self.ph = Phone(self.st)
        self.host = _owner_host(self.st, self.sent)
        self.s = _ready(self.host, self.ph)

    def got(self, t, r):
        return [o for _, o in self.sent if o.get("t") == t and o.get("r") == r]

    def rows(self, r):
        asyncio.run(self.host._app(self.s, {"t": "task_list", "r": r}))
        return {x["id"]: x for x in self.got("tasks", r)[0]["items"]}

    def cmd(self, action, target, extra, wait=True, key=None):
        from test_controls import sign

        async def go():
            await self.host._app(self.s, sign(self.ph, action, target, extra, key=key))
            for _ in range(1200):
                if self.got("ctl_res", extra["r"]) or not wait:
                    break
                await asyncio.sleep(0.05)
            await asyncio.gather(*self.host.__dict__.get("_bg_tasks", ()))
        asyncio.run(go())
        res = self.got("ctl_res", extra["r"])
        return res[0] if res else None

    def migrate_cmd(self, tid, tsha, r, **kw):
        return self.cmd("task_migrate", {"id": tid, "tsha": tsha}, {"t": "task_migrate", "r": r, "id": tid, "tsha": tsha}, **kw)

    def test_task_list_marks_moved_and_changed_tasks(self):
        m.task(self.work, "daily")
        m.task(self.work, "other")
        self.enable("daily", "other")
        sysmigrate.migrate(self.st, ["daily"], plat="linux")
        rows = self.rows("l1")
        self.assertEqual(rows["daily"]["sys"], "migrated")
        self.assertNotIn("sys", rows["other"])
        (self.work / "workflows" / "daily" / "RUN.md").write_text("Something else.\n")
        self.assertEqual(self.rows("l2")["daily"]["sys"], "changed", "its runs refuse until moved again")

    def test_signed_task_migrate_moves_a_changed_task_again(self):
        m.task(self.work, "daily", title={"zh": "每日简报", "en": "Daily brief"})
        self.enable("daily")
        sysmigrate.migrate(self.st, ["daily"], plat="linux")
        (self.work / "workflows" / "daily" / "RUN.md").write_text("Something else.\n")
        tsha = self.rows("l1")["daily"]["tsha"]
        self.assertIn("task_migrate", controls.ACTIONS)
        res = self.migrate_cmd("daily", tsha, "m1")
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["text"], "已改用系统定时 1 个：每日简报。")
        pend = self.got("ctl_pending", "m1")
        self.assertEqual((pend[0]["action"], pend[0]["timeout_ms"]), ("task_migrate", sysmigrate.PENDING_MS))
        self.assertEqual(self.rows("l2")["daily"]["sys"], "migrated")
        self.assertEqual(sysmigrate.load(self.st)["tasks"]["daily"]["tsha"], tsha)
        self.assertNotIn("daily", self.on(), "moved: Agent J's own scheduler does not run it")
        log = [json.loads(x) for x in (self.st.root / "controls.log").read_text().splitlines()]
        self.assertEqual([(x["action"], x["device"], x["result"]) for x in log if x["action"] == "task_migrate"],
                         [("task_migrate", self.ph.did, "ok")], "a paired device's signed command, logged")

    def test_task_migrate_refusals_change_nothing(self):
        m.task(self.work, "daily")
        self.enable("daily")
        sysmigrate.migrate(self.st, ["daily"], plat="linux")
        tsha = self.rows("l1")["daily"]["tsha"]
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        res = self.migrate_cmd("daily", tsha, "b1", key=Ed25519PrivateKey.generate())
        self.assertEqual((res["ok"], res["why"]), (False, "bad_signature"))
        res = self.migrate_cmd("daily", "0" * 64, "b2")
        self.assertEqual((res["ok"], res["why"]), (False, sysmigrate.why("reload")))
        os.environ["FAKE_NO_SYSTEMD"] = "1"
        res = self.migrate_cmd("daily", tsha, "b3")
        self.assertEqual((res["ok"], res["why"]), (False, sysmigrate.why("no_systemd")))
        os.environ.pop("FAKE_NO_SYSTEMD")
        self.host._sysmigrate_card = True
        self.assertEqual(self.migrate_cmd("daily", tsha, "b4")["why"], sysmigrate.why("pending"))
        self.host._sysmigrate_card = False
        self.assertEqual(sysmigrate.migrated(self.st), {"daily": "agentj-task-daily.timer"}, "the move stays as it was")
        self.assertTrue((self.units / "agentj-task-daily.timer").exists())

    def test_a_failed_move_of_a_task_that_was_off_leaves_it_off(self):
        m.task(self.work, "daily")
        tsha = self.rows("l1")["daily"]["tsha"]
        self.verdict("VERDICT: FAIL no data")
        res = self.migrate_cmd("daily", tsha, "f1")
        self.assertFalse(res["ok"])
        self.assertIn("试跑没有通过：no data", res["text"])
        self.assertNotIn("daily", self.on())
        self.assertEqual(sysmigrate.migrated(self.st), {})

    def test_task_set_off_on_a_moved_task_moves_it_back_and_off(self):
        m.task(self.work, "daily")
        self.enable("daily")
        sysmigrate.migrate(self.st, ["daily"], plat="linux")
        tgt = {"id": "daily", "tsha": self.rows("l1")["daily"]["tsha"]}
        res = self.cmd("task_off", tgt, {"t": "task_set", "r": "o1", "on": False, **tgt})
        self.assertTrue(res["ok"], res)
        self.assertFalse((self.units / "agentj-task-daily.timer").exists())
        self.assertNotIn("sys", self.rows("l2")["daily"])
        self.assertNotIn("daily", self.on())


class Wsl(m.Base):
    def offer(self, h):
        return asyncio.run(sysmigrate.offer(h, plat="linux"))

    def test_wsl_without_systemd_says_so_once_per_version(self):
        m.task(self.work, "a")
        self.enable("a")
        os.environ["FAKE_NO_SYSTEMD"] = "1"
        h = m.Host(self.st, m.YES)
        with mock.patch.object(sysmigrate, "wsl", return_value=False):
            self.assertEqual(self.offer(h), "none")
        self.assertEqual(h.notices, [], "not WSL: nothing new")
        with mock.patch.object(sysmigrate, "wsl", return_value=True):
            self.assertEqual(self.offer(h), "wsl")
            self.assertEqual(self.offer(h), "none", "once per version")
        self.assertEqual(len(h.notices), 1)
        for s in ("WSL2，没开 systemd", "你不在时可能卡在审批上", "「帮我开启 WSL 的 systemd」", "/etc/wsl.conf", "wsl --shutdown",
                  "自动弹出迁移卡"):
            self.assertIn(s, h.notices[0])
        self.assertEqual(h.asks, [])
        self.assertIn("帮我开启 WSL 的 systemd", sysmigrate.why("no_systemd"))
        with mock.patch.dict(os.environ, {"WSL_DISTRO_NAME": "Ubuntu"}):
            self.assertTrue(sysmigrate.wsl())

    def test_wsl_without_tasks_stays_quiet(self):
        m.task(self.work, "a")
        os.environ["FAKE_NO_SYSTEMD"] = "1"
        h = m.Host(self.st, m.YES)
        with mock.patch.object(sysmigrate, "wsl", return_value=True):
            self.assertEqual(self.offer(h), "none")
        self.assertEqual(h.notices, [])


class NewTaskHint(m.Base):
    def test_add_template_and_every_enable_recommend_the_system_scheduler(self):
        from agentj import wizard
        m.task(self.work, "daily")
        want = {"id": "daily", "version": "1", "sha256": "ab" * 32, "title": {"zh": "日报", "en": "Daily"}}
        out = io.StringIO()
        with mock.patch.object(wizard, "add_template", return_value=(want, [])), mock.patch.object(wizard, "_state", lambda: self.st), \
                mock.patch.object(wizard, "_dir_arg", lambda a: self.work), mock.patch.object(wizard, "_say"), \
                mock.patch.object(sysmigrate, "unavailable", return_value=None), contextlib.redirect_stdout(out):
            wizard.cmd_add_template(Namespace(id="daily", json=False, dir=None))
        self.assertIn("更稳的做法：启用（agentj tasks enable daily）后用 `agentj tasks migrate daily` 改用系统定时", out.getvalue())
        out = io.StringIO()
        with mock.patch.object(cli, "State", lambda: self.st), mock.patch.object(cli, "_ctl_quiet"), contextlib.redirect_stdout(out):
            for mode in ("enable", "disable", "enable"):
                cli.cmd_tasks(Namespace(mode=mode, id="daily", json=False, dry_run=False, all=False))
        self.assertEqual(out.getvalue().count("`agentj tasks migrate daily`"), 2, "every enable, not only the first")
        self.assertIn("once enabled (agentj tasks enable x), `agentj tasks migrate x`", sysmigrate.hint("x", "en", True))


class LoopTick(unittest.IsolatedAsyncioTestCase):
    """ScheduleWakeup / CronCreate ticks stay refused (ADR-P570b); the Agent and the owner are told to use a scheduled task."""

    def agent(self, td):
        from agentj.shared import SharedClaudeAgent
        h = mock.Mock()
        h.st.root, h.lang, h.proactive_via = Path(td), "zh", "loop"
        h.stopped.return_value = False
        h.ask = mock.AsyncMock(return_value={"behavior": "allow"})
        a = SharedClaudeAgent(h, {"kind": "claude", "dir": td, "_workflow_ceo": True})
        a.session = {"sessionId": "owner"}
        a.transcript = Path(td) / "t.jsonl"
        a.transcript.touch()
        return h, a

    async def perm(self, a):
        d = await a.hook_event({"hook_event_name": "PermissionRequest", "session_id": "owner",
                                "tool_name": "Bash", "tool_input": {"command": "git push origin"}})
        return d["hookSpecificOutput"]["decision"]

    async def test_loop_refusal_points_at_scheduled_tasks(self):
        with tempfile.TemporaryDirectory(dir=m.TEST_TMP) as td:
            h, a = self.agent(td)
            a.native_source = provenance.terminal()
            with a.transcript.open("a") as f:
                f.write(json.dumps({"type": "user", "turnOrigin": "scheduled", "scheduledTaskId": "205c5723", "isMeta": True,
                                    "message": {"content": "# Autonomous loop tick (dynamic pacing)"}}) + "\n")
            a.read_transcript()
            a.session = {"sessionId": "owner"}
            for _ in range(2):
                d = await self.perm(a)
                self.assertEqual(d["behavior"], "deny")
                self.assertIn("循环唤醒（ScheduleWakeup / CronCreate）", d["message"])
                self.assertIn("请改用定时任务", d["message"])
                self.assertIn("agentj tasks enable", d["message"])
                self.assertIn("不是主人手动拒绝", d["message"])
            h.ask.assert_not_awaited()
            self.assertEqual(h.task_notice.call_count, 1, "one plain line to the phone, not one per tick")
            note = h.task_notice.call_args[0][0]
            self.assertIn("这种循环唤醒请改用定时任务", note)
            self.assertIn("「把这个循环改成定时任务」", note)

    def test_other_refusals_unchanged(self):
        from types import SimpleNamespace
        host = mock.Mock(proactive_via=None)
        a = SimpleNamespace(host=host, native_source=provenance.system())
        self.assertNotIn("循环唤醒", provenance.refusal_text(a))
        host.proactive_via = "loop"
        self.assertIn("循环唤醒", provenance.refusal_text(a))
        a.native_source = provenance.telegram_group(-1, 2, "proxy", 1)
        self.assertNotIn("循环唤醒", provenance.refusal_text(a), "a group turn is never called a loop")


if __name__ == "__main__":
    unittest.main()
