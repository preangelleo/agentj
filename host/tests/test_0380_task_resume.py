"""Scoped transition journeys from paired phone dispatch to the native delivery boundary."""
import _hermetic  # noqa: F401
import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from agentj import agent, controls, diskq, handoff, history, phone_update, provenance, rescue_repair, serve, switch_harness, switch_report, upgrade_messages
from agentj.browser_login import RequestPhone
from test_l1 import Phone, _host, _state
from test_p313_receipts import _Agent, sid
from test_slash import _Chain

TASK = "Please make a draft receipt from the already prepared local fixture."
JOB = "a" * 16


class Journeys(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(self.cleanup, tmp)
        self.path = Path(tmp.name)
        self.work = self.path / "work"
        self.work.mkdir()
        self.st = _state(self.path)
        self.st.set_agent_config("claude", str(self.work), record_root=False)
        history.set_enabled(self.st, True)
        self.phone = Phone(self.st)
        for obj, name, value in ((switch_harness, "receipt_path", lambda name=switch_harness.RECEIPT: self.path / name),
                                 (upgrade_messages, "kick", lambda host: None)):
            patcher = patch.object(obj, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def cleanup(self, tmp):
        await diskq.aflush()
        tmp.cleanup()

    def boot(self, kind="claude"):
        frames = []
        host = _host(self.st, frames)
        host.lang = "en"
        host.agent = _Agent()
        host.agent.kind = kind
        host.agent.cfg = host.agent_cfg
        session = serve.Session(cid=1, state="ready", device=self.phone.did, pub=self.phone.pub, p33=True,
                                sign_pub=self.st.sign_key(self.phone.did), provenance=provenance.phone())
        host.sessions[1] = session
        session.login_phone = RequestPhone.from_session(host, session)
        host.receipts_restart()
        return host, session, frames

    def set_switch(self, status, job=JOB, **extra):
        switch_harness.write_receipt(self.st, dict(job=job, status=status, target="codex", **{"from": "claude"}, **extra))

    async def say(self, host, session, frames, mid, words=TASK):
        await host._app(session, {"t": "say", "sid": mid, "text": words, "ts": 0})
        for _ in range(10):
            await asyncio.sleep(0)
        return next(obj for _, obj in reversed(frames) if obj.get("t") == "say_res" and obj["sid"] == mid)

    async def drain(self, host):
        with patch.object(host, "_setup_round_trip", AsyncMock()), patch.object(host, "_model_set_flush", AsyncMock()):
            await asyncio.gather(upgrade_messages.resume(host), upgrade_messages.resume(host))

    async def distinct_terminal_delivery(self, host, send):
        target = agent.Agent(host, dict(host.agent_cfg))
        target.cur_send = send
        native = AsyncMock()
        host.cur_turn = send.turn
        await target.deliver(native)
        with self.assertRaises(agent.Withdrawn):
            await target.deliver(native)
        native.assert_awaited_once()
        host.agent_text("Confirmed QA draft receipt.")
        host.agent_turn_end()
        host.receipts.flush()
        self.assertTrue(await diskq.aflush())

    async def test_switch_delivers_once_without_rewriting_a_handoff_and_updates_same_receipt(self):
        old, session, frames = self.boot()
        origin_page = old.hist_add({"k": "phone", "dev": self.phone.did, "text": "Switch to Codex."}, "", "done")
        origin = SimpleNamespace(epoch=old.hist.epoch, device=self.phone.did, signing_key=session.sign_pub,
                                 source={"channel": "phone", "message_id": origin_page["id"]})
        switch_report.bind(self.st, JOB, origin)
        self.assertTrue(await diskq.aflush())
        self.set_switch("switching")
        mid = sid()
        result = await self.say(old, session, frames, mid)
        self.assertTrue(result["ok"], result)
        self.assertEqual(old.receipts.state(self.phone.did, mid), "held")
        self.assertFalse(old.agent.got)
        excerpt = handoff.switch_fallback(old.hist.turns)
        self.assertIn("Switch to Codex.", excerpt)
        self.assertNotIn(TASK, excerpt, "An unsubmitted task must reach the new assistant through only the durable queue.")
        self.assertFalse((self.work / ".agentj" / "handover" / "switch.md").exists())

        self.st.set_agent_config("codex", str(self.work), record_root=False)
        new, session, frames = self.boot("codex")
        await self.drain(new)
        self.assertFalse(new.agent.got)
        self.set_switch("ok", task_resume="checkpoint", manual_resumes=0)
        self.assertTrue(switch_report.deliver(new, JOB)["ok"])
        self.assertEqual(switch_report.task_meta(self.st, JOB)["task_resume"], "queued_unsent")
        receipt, = [p for p in new.hist.turns.values() if p["src"].get("switch_job") == JOB]
        self.assertEqual(receipt["reply"]["text"].splitlines()[-1],
                         "The task is still waiting to be sent to the new assistant.")
        await self.drain(new)
        send, = new.agent.got
        self.assertEqual((send.turn, send.sid, send.source), (result["turn"], mid, provenance.phone()))
        self.assertIn(TASK, send.text)
        self.assertTrue(send.delivery_allowed())
        self.assertTrue(switch_report.deliver(new, JOB)["ok"])
        result_meta = switch_harness.host_result(self.st, {"kind": "switch", "job": JOB})["receipt"]
        self.assertEqual(result_meta["task_resume"], "queued")
        self.assertEqual(new.hist.get(receipt["id"])["reply"]["text"].splitlines()[-1],
                         "Checking whether the task reached the new assistant. Please don’t resend yet.")
        await self.distinct_terminal_delivery(new, send)
        self.assertTrue(switch_report.deliver(new, JOB)["ok"])
        self.assertTrue(await diskq.aflush())
        receipt, = [p for p in new.hist.turns.values() if p["src"].get("switch_job") == JOB]
        self.assertEqual(receipt["reply"]["text"].splitlines()[-1],
                         "The task resumed automatically. Follow the original task message for updates.")
        self.assertEqual(receipt["src"]["quote"], {"id": origin_page["id"]})
        self.assertEqual(new.hist.get(result["turn"])["reply"]["text"], "Confirmed QA draft receipt.")

        again, session, frames = self.boot("codex")
        await self.drain(again)
        self.assertEqual((await self.say(again, session, frames, mid))["why"], "dup")
        self.assertFalse(again.agent.got)
        self.assertEqual(switch_report.task_meta(self.st, JOB)["manual_resumes"], 0)

    async def test_manually_continued_work_counts_exact_owner_requests_on_the_same_receipt(self):
        host, session, frames = self.boot()
        origin = host.hist_add({"k": "phone", "dev": self.phone.did, "text": "Switch to Codex."}, "", "done")
        bound = SimpleNamespace(epoch=host.hist.epoch, device=self.phone.did, signing_key=session.sign_pub,
                                source={"channel": "phone", "message_id": origin["id"]})
        switch_report.bind(self.st, JOB, bound)
        await diskq.aflush()
        self.st.set_agent_config("codex", str(self.work), record_root=False)
        host, session, frames = self.boot("codex")
        self.set_switch("ok", task_resume="checkpoint", manual_resumes=0)
        self.assertTrue(switch_report.deliver(host, JOB)["ok"])
        for words in ("Continue.", "继续", "A separate request", "Continue."):
            with patch.object(host, "_setup_round_trip", AsyncMock()), patch.object(host, "_model_set_flush", AsyncMock()):
                self.assertTrue((await self.say(host, session, frames, sid(), words))["ok"])
        await diskq.aflush()
        self.assertTrue(switch_report.deliver(host, JOB)["ok"])
        row, = [p for p in host.hist.turns.values() if p["src"].get("switch_job") == JOB]
        self.assertTrue(row["reply"]["text"].endswith(" You asked to continue manually 2 times."))
        self.assertEqual(switch_harness.host_result(self.st, {"kind": "switch", "job": JOB})["receipt"]["manual_resumes"], 2)

    async def test_switch_superseded_receipt_cannot_release_an_old_task(self):
        host, session, frames = self.boot()
        self.set_switch("switching")
        mid = sid()
        result = await self.say(host, session, frames, mid)
        self.st.set_agent_config("codex", str(self.work), record_root=False)
        self.set_switch("ok", "b" * 16)
        new, _, _ = self.boot("codex")
        await self.drain(new)
        self.assertFalse(new.agent.got)
        self.assertEqual(new.hist.get(result["turn"])["end"], "failed")

    async def test_compaction_preserves_unsubmitted_task_in_same_native_session(self):
        host, session, frames = self.boot()
        self.st.set_agent_session("claude", "native-1")
        host.agent.status = "compacting"
        result = await self.say(host, session, frames, sid())
        self.assertTrue(result["ok"], result)
        await self.drain(host)
        self.assertFalse(host.agent.got)
        host.agent.status = "idle"
        await self.drain(host)
        send, = host.agent.got
        self.assertTrue(send.delivery_allowed())
        self.assertEqual(send.turn, result["turn"])
        self.st.set_agent_session("claude", "native-2")
        self.assertFalse(send.delivery_allowed(), "Rebinding after claim and before native delivery fails closed.")

    async def test_signed_repair_and_upgrade_windows_hold_until_the_same_workspace_is_ready(self):
        for transition in ("repair", "upgrade"):
            with self.subTest(transition=transition):
                host, session, frames = self.boot()
                if transition == "repair":
                    rescue_repair.write_marker(self.st, "repair", "request", self.phone.did)
                    await diskq.aflush()
                else:
                    self.assertTrue(phone_update.reserve(self.st))
                mid = sid()
                result = await self.say(host, session, frames, mid)
                self.assertTrue(result["ok"], result)
                await self.drain(host)
                self.assertFalse(host.agent.got)
                if transition == "repair":
                    with patch("agentj.task_resume.os.getpid", return_value=-1):
                        new, _, _ = self.boot()
                        await self.drain(new)
                    (self.st.root / rescue_repair.MARKER).unlink()
                else:
                    phone_update.release(self.st)
                    new, _, _ = self.boot()
                    await self.drain(new)
                send, = new.agent.got
                self.assertTrue(send.delivery_allowed())
                self.assertEqual(send.turn, result["turn"])
                await self.distinct_terminal_delivery(new, send)

    async def test_estop_generation_after_intake_and_legacy_maybe_delivered_are_never_replayed(self):
        host, session, frames = self.boot()
        first = sid()
        with patch.object(host, "_setup_round_trip", AsyncMock()), patch.object(host, "_model_set_flush", AsyncMock()):
            accepted = await self.say(host, session, frames, first)
        host.receipts.mark(self.phone.did, first, "deliv", accepted["turn"], notice_only=True)
        self.set_switch("switching")
        waiting = await self.say(host, session, frames, sid())
        self.assertTrue(waiting["ok"])
        host.receipts.flush()
        controls.set_estop(self.st, True, "phone")
        controls.set_estop(self.st, False, "phone")
        self.st.set_agent_config("codex", str(self.work), record_root=False)
        self.set_switch("ok")
        new, _, _ = self.boot("codex")
        await self.drain(new)
        self.assertFalse(new.agent.got)
        self.assertEqual(new.hist.get(waiting["turn"])["end"], "failed")
        self.assertEqual(new.receipts.state(self.phone.did, first), "lost")


class ReceiptCopy(unittest.TestCase):
    def test_open_switch_page_requires_matching_held_evidence_for_unsent_copy(self):
        scope = {"job": JOB, "device": "owner"}
        page = {"id": 1, "ts": 123, "end": "open",
                "src": {"k": "phone", "dev": "owner", "source_kind": "phone",
                        "source_role": "owner", "switch_resume_job": JOB}}
        held = {"n": 1, "s": "held", "seal": "test-seal", "reply_page": (4, 123)}
        unknown = ("queued", "正在确认任务有没有交到新助手手里，先别重发。",
                   "Checking whether the task reached the new assistant. Please don’t resend yet.")
        unsent = ("queued_unsent", "任务还没发出，正在等新助手接收。",
                  "The task is still waiting to be sent to the new assistant.")
        resumed = ("resumed", "任务已自动接上。进展请看原任务消息。",
                   "The task resumed automatically. Follow the original task message for updates.")
        cases = {
            "held": ({("owner", "mid"): held}, unsent),
            "claimed": ({("owner", "mid"): {**held, "s": "recv"}}, unknown),
            "native_delivery": ({("owner", "mid"): {**held, "s": "deliv"}}, resumed),
            "notice_only": ({("owner", "mid"): {**held, "s": "deliv", "notice_only": True}}, unknown),
            "lost": ({("owner", "mid"): {**held, "s": "lost"}}, unknown),
            "wrong_epoch": ({("owner", "mid"): {**held, "reply_page": (3, 123)}}, unknown),
            "wrong_timestamp": ({("owner", "mid"): {**held, "reply_page": (4, 124)}}, unknown),
            "wrong_device": ({("other", "mid"): held}, unknown),
            "unsealed": ({("owner", "mid"): {**held, "seal": None}}, unknown),
            "missing": ({}, unknown),
            "conflicting": ({("owner", "mid"): held, ("owner", "other"): {**held, "s": "recv"}}, unknown),
        }
        for name, (items, (state, zh, en)) in cases.items():
            with self.subTest(evidence=name):
                host = SimpleNamespace(hist=SimpleNamespace(epoch=4, turns={1: page}),
                                       receipts=SimpleNamespace(items=items))
                result = switch_report._task_status(host, scope, {"status": "ok", "task_resume": "checkpoint"}, None)
                self.assertEqual(result, {"task_resume": state, "manual_resumes": 0})
                self.assertEqual(switch_report.task_text(result, "zh"), "\n" + zh)
                self.assertEqual(switch_report.task_text(result, "en"), "\n" + en)

        second = {**page, "id": 2, "ts": 124}
        host = SimpleNamespace(hist=SimpleNamespace(epoch=4, turns={1: page, 2: second}),
                               receipts=SimpleNamespace(items={("owner", "mid"): held}))
        self.assertEqual(switch_report._task_status(host, scope, {"status": "ok", "task_resume": "checkpoint"}, None),
                         {"task_resume": "queued", "manual_resumes": 0})

    def test_legacy_queued_metadata_uses_safe_copy_without_held_evidence(self):
        self.assertEqual(switch_report.task_text({"task_resume": "queued"}, "zh"),
                         "\n正在确认任务有没有交到新助手手里，先别重发。")
        self.assertEqual(switch_report.task_text({"task_resume": "queued"}, "en"),
                         "\nChecking whether the task reached the new assistant. Please don’t resend yet.")

    def test_zero_count_is_omitted_in_both_languages_for_every_state(self):
        for state in switch_report.TASK_STATES:
            for lang in ("zh", "en"):
                with self.subTest(state=state, lang=lang):
                    base = switch_report.task_text({"task_resume": state}, lang)
                    explicit_zero = switch_report.task_text({"task_resume": state, "manual_resumes": 0}, lang)
                    self.assertEqual(explicit_zero, base)
                    self.assertTrue(base.startswith("\n"))
                    self.assertNotIn("手动续做" if lang == "zh" else "manually", base)

    def test_positive_counts_follow_each_languages_sentence_spacing(self):
        state = {"task_resume": "resumed"}
        for count in (1, 2):
            with self.subTest(count=count):
                rec = {**state, "manual_resumes": count}
                self.assertEqual(switch_report.task_text(rec, "zh"),
                                 f"\n任务已自动接上。进展请看原任务消息。已手动续做 {count} 次。")
                unit = "time" if count == 1 else "times"
                self.assertEqual(switch_report.task_text(rec, "en"),
                                 "\nThe task resumed automatically. Follow the original task message for updates."
                                 f" You asked to continue manually {count} {unit}.")


class CodexTransport(_Chain):
    KIND = "codex"

    def test_existing_phone_route_reaches_the_new_app_server_once(self):
        history.set_enabled(self.st, True)
        isolated_receipt = Path(self.tmp.name) / "switch.json"

        async def flow(ctx):
            host, session = ctx["host"], ctx["s"]
            session.p33 = True
            session.sign_pub = self.st.sign_key(session.device)
            session.provenance = provenance.phone()
            session.login_phone = RequestPhone.from_session(host, session)
            await ctx["idle"]()
            rec = dict(job=JOB, target="codex", **{"from": "claude"})
            switch_harness.write_receipt(self.st, {**rec, "status": "switching"})
            request_id = sid()
            await host._app(session, {"t":"say", "sid":request_id, "text":TASK, "ts":0})
            await ctx["wait"](lambda: host.receipts.state(session.device, request_id) == "held")
            self.assertFalse(any(TASK in str(event.get("params")) for event in self.logged()))
            switch_harness.write_receipt(self.st, {**rec, "status": "ok"})
            await ctx["wait"](lambda: any(TASK in page["reply"]["text"] for page in host.hist.turns.values()))
            await ctx["idle"]()
            await host._app(session, {"t":"say", "sid":request_id, "text":TASK, "ts":0})
            calls = [event for event in self.logged() if event.get("method") == "turn/start"
                     and TASK in str(event.get("params"))]
            self.assertEqual(len(calls), 1, calls)

        with patch.object(switch_harness, "receipt_path", return_value=isolated_receipt):
            self.run_chain(flow)


if __name__ == "__main__":
    unittest.main()
