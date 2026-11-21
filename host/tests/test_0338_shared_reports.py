"""Public shared-Claude reports reach their original history page and notify once."""
import _hermetic
import asyncio
import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from _testtmp import TEST_TMP
from agentj import provenance, shared
from agentj.compose import Send
from agentj.serve import Host
from agentj.state import State
import test_p424_held as fixture


class SharedReport(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        st = State(root / "state")
        st.init(relay="ws://127.0.0.1:1")
        self.host = Host(st, events="jsonl", read_stdin=False)
        self.host._post = lambda *args: None
        self.host.telegram = self.host.mail = self.host.wechat = None
        self.pushes = []
        self.host.push_notify = self.pushes.append
        self.host.emit = lambda *args, **kw: None
        self.a = fixture.Held.agent(self, self.host)
        self.a.state = shared.AgentState(on_turn=self.a._committed_stop)
        self.a.transcript = root / "native.jsonl"
        self.a.transcript.touch()
        self.a.loop = asyncio.get_running_loop()
        self.enterContext(patch.object(shared, "claude_sessions", return_value=[self.a.session]))
        self.enterContext(patch.object(shared, "TURN_END_GRACE", .015))
        self.enterContext(patch.object(shared, "EMPTY_STOP_WAIT", .025))

    def rows(self, *rows):
        with self.a.transcript.open("a") as stream:
            for row in rows:
                stream.write(json.dumps(row) + "\n")
        self.a.read_transcript()

    def text(self, value):
        self.rows({"type":"assistant", "message":{"content":[{"type":"text","text":value}]}})

    def wakeup(self):
        self.rows({"type":"assistant","message":{"content":[{"type":"tool_use","id":"w","name":"ScheduleWakeup","input":{}}]}},
                  {"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"w","content":"scheduled"}]}})

    def owner_turn(self, *, unread=False, prompt_id=None, via=None):
        page = self.host.hist.add({"k":"phone","text":"please report"}, "", "open")
        send = Send("fixture", "request", page["id"], text="please report", source=provenance.phone())
        if via is not None:
            send.source = provenance.system()
            page["src"] = {"k": "agent", "via": via, "text": send.text}
            if via == "hook":
                send.hook_notice = "fixture"
            else:
                send.background_done_ids = ["fixture"]
        self.a.cur_send = send
        self.host.agent_turn_start(send.text, send)
        self.a.phone_turn = True
        self.a.pending_source, self.a.pending_text = send.source, send.text
        self.a.native_request = shared.shared_turn_origin.mint(self.a, send, send.text)
        row = {"type":"user","sessionId":self.a.session["sessionId"],"isMeta":True,
                   "timestamp":datetime.now(timezone.utc).isoformat(),"origin":{"kind":"peer"},
                   "message":{"content":provenance.envelope(self.a.session["messagingSocketPath"],send.text,send.source)}}
        if prompt_id:
            row["promptId"] = prompt_id
        if unread:
            with self.a.transcript.open("a") as stream:
                stream.write(json.dumps(row) + "\n")
            return page
        self.rows(row)
        self.assertTrue(shared.shared_turn_origin.display_received(self.a))
        self.assertFalse(self.a.input_seen.is_set())
        return page

    async def test_tool_result_then_late_body_one_page_and_push(self):
        page = self.owner_turn()
        self.text("before")
        self.wakeup()
        self.a._schedule_empty_stop()
        await asyncio.sleep(.08)
        self.assertFalse(self.a.done.is_set(), "tool result is provisional")
        self.text("after [silent]")
        self.rows({"type":"system","subtype":"turn_duration"})
        await asyncio.sleep(.08)
        self.assertTrue(self.a.done.is_set())
        self.host.agent_turn_end()
        self.a.phone_turn = False
        self.a._finish_stop("after [silent]")  # late native Stop is not another page
        self.assertEqual(page["reply"]["text"],"before\n\nafter")
        self.assertEqual(len(self.host.hist.turns),1)
        self.assertEqual(self.pushes,["reply"])

    async def test_marker_stop_recovers_only_current_owner_text(self):
        self.text("unrelated earlier turn")
        page = self.owner_turn()
        self.text("report ready")
        self.text("〔静默消息〕")
        self.a._finish_stop("〔静默消息〕")
        self.host.agent_turn_end()
        self.assertEqual(page["reply"]["text"],"report ready")
        self.assertEqual(self.pushes,["reply"])

    async def test_system_report_followed_by_explicit_hook_silence(self):
        page = self.owner_turn(via="background_done")
        self.text("release confirmed")
        await self.native_stop("release confirmed")
        self.host.agent_turn_end()
        self.assertEqual(page["reply"]["text"], "release confirmed")
        hook = self.owner_turn(via="hook")
        self.text("〔静默消息〕 background detail")
        self.a._stream_texts()
        self.assertFalse(hook["reply"]["text"], "unclassified hook prose must not stream")
        await self.native_stop("〔静默消息〕 background detail")
        self.host.agent_turn_end()
        self.assertTrue(hook["silent"])
        self.assertEqual(self.pushes, ["reply"])

    async def test_wakeup_completion_keeps_report_without_marker_explanation(self):
        page = self.owner_turn(via="background_done")
        self.text("report before scheduled continuation")
        self.wakeup()
        self.text("[silent] internal explanation")
        await self.native_stop("[silent] internal explanation")
        self.host.agent_turn_end()
        self.assertEqual(page["reply"]["text"], "report before scheduled continuation")
        self.assertEqual(self.pushes, ["reply"])

    async def test_system_marker_with_trailing_explanation_and_separate_marker(self):
        hook = self.owner_turn(via="hook")
        self.text("background reason [silent]")
        await self.native_stop("background reason [silent]")
        self.host.agent_turn_end()
        self.assertTrue(hook["silent"])
        self.assertEqual(self.pushes, [])
        page = self.owner_turn(via="background_done")
        self.text("release report")
        self.text("[silent]")
        await self.native_stop("[silent]")
        self.host.agent_turn_end()
        self.assertEqual(page["reply"]["text"], "release report")
        self.assertEqual(self.pushes, ["reply"])

    async def test_system_report_does_not_recover_a_tool_preamble(self):
        page = self.owner_turn(via="background_done")
        self.rows({"type": "assistant", "message": {"id": "tool-message", "content": [
            {"type": "text", "text": "Trying another source."}]}},
            {"type": "assistant", "message": {"id": "tool-message", "content": [
                {"type": "tool_use", "name": "WebSearch", "id": "search", "input": {}}]}})
        self.text("[silent]")
        await self.native_stop("[silent]")
        self.host.agent_turn_end()
        self.assertTrue(page.get("silent"))
        self.assertEqual(self.pushes, [])

    async def test_native_proactive_stop_drops_tool_preamble_and_keeps_distinct_result(self):
        self.rows({"type": "user", "turnOrigin": "scheduled", "message": {"content": "scheduled tick"}},
                  {"type": "assistant", "message": {"content": [
                      {"type": "text", "text": "Trying another source."},
                      {"type": "tool_use", "name": "WebSearch", "id": "search", "input": {}}]}})
        self.a._finish_stop("Trying another source.")
        self.assertEqual(self.pushes, [])
        self.rows({"type": "user", "turnOrigin": "scheduled", "message": {"content": "second tick"}})
        self.text("核对完成。")
        self.a._finish_stop("核对完成。")
        self.assertEqual(self.pushes, ["reply"])
        self.assertEqual(list(self.host.hist.turns.values())[-1]["reply"]["text"], "核对完成。")

    async def test_native_loop_stop_keeps_report(self):
        self.rows({"type":"user","turnOrigin":"scheduled","message":{"content":"scheduled tick"}})
        self.text("loop report")
        self.wakeup()
        self.text("[silent]")
        self.a._finish_stop("[silent]")
        self.assertEqual([p["reply"]["text"] for p in self.host.hist.turns.values()],["loop report"])
        self.assertEqual(self.pushes,["reply"])

    async def test_tool_without_stop_or_duration_falls_back_once(self):
        page = self.owner_turn()
        with patch.object(shared, "WAKEUP_CONTINUATION_WAIT", .05):
            self.text("report before tool")
            self.wakeup()
            await asyncio.sleep(.15)
        self.assertTrue(self.a.done.is_set())
        self.host.agent_turn_end()
        self.assertEqual(page["reply"]["text"],"report before tool")
        self.assertEqual(self.pushes,["reply"])

    async def test_next_turn_cancels_previous_tool_timer(self):
        self.rows({"type":"user","turnOrigin":"scheduled","message":{"content":"first tick"}})
        with patch.object(shared, "WAKEUP_CONTINUATION_WAIT", .05):
            self.wakeup()
            page = self.owner_turn()
            self.text("new report")
            await asyncio.sleep(.15)
        self.assertFalse(self.a.done.is_set())
        self.a._finish_stop("new report")
        self.host.agent_turn_end()
        self.assertEqual(page["reply"]["text"],"new report")

    async def test_pure_marker_stays_silent(self):
        page = self.owner_turn()
        self.text("〔静默消息〕")
        self.a._finish_stop("〔静默消息〕")
        self.host.agent_turn_end()
        self.assertTrue(page["silent"])
        self.assertEqual(self.pushes,[])

    async def native_stop(self, reply, prompt_id=None):
        await self.a.hook_event({"hook_event_name":"Stop", "session_id":self.a.session["sessionId"],
                                 "last_assistant_message":reply, "prompt_id":prompt_id})

    async def queue_native_stop(self, reply):
        # AgentState's foreground watchdog commits on a worker thread. The test
        # owns the release point so a new input can interleave before loop delivery.
        self.a.state._agents["foreground"] = None
        await self.native_stop(reply)
        event = self.a.state._deferred_stop
        self.assertIsNotNone(event)
        self.a.state._agents.clear()
        worker = threading.Thread(target=self.a.state._commit_stop, args=(event,))
        worker.start()
        worker.join()
        return event

    async def test_queued_old_stop_leaves_new_send_and_boundary_untouched(self):
        self.owner_turn()
        await self.queue_native_stop("old body")
        page = self.owner_turn()
        self.text("new body")
        self.a.untrusted_state, self.a.peer_low = "running", True
        original = self.a.ended_at, self.a.stop_claimed
        await asyncio.sleep(0)
        self.assertFalse(self.a.done.is_set())
        self.assertEqual((self.a.ended_at,self.a.stop_claimed), original)
        self.assertEqual((self.a.untrusted_state,self.a.peer_low), ("running",True))
        await self.native_stop("new body")
        await asyncio.sleep(0)
        self.assertTrue(self.a.done.is_set())
        self.host.agent_turn_end()
        self.assertEqual(page["reply"]["text"], "new body")

    async def test_same_send_stop_reads_first_receipt_and_completes(self):
        page = self.owner_turn(unread=True)
        gen = self.a.turn_gen
        await self.native_stop("this send")
        await asyncio.sleep(0)
        self.assertTrue(shared.shared_turn_origin.display_received(self.a))
        self.assertEqual(self.a.turn_gen, gen + 1)
        self.assertTrue(self.a.done.is_set())
        self.host.agent_turn_end()
        self.assertEqual(page["reply"]["text"], "this send")

    async def test_old_stop_arriving_during_identical_new_reply_waits_for_its_native_end(self):
        first = self.owner_turn()
        self.text("same report")
        self.rows({"type":"system","subtype":"turn_duration"})
        await asyncio.sleep(.08)
        self.host.agent_turn_end()
        self.a.done.clear()
        second = self.owner_turn()
        self.text("same report")
        await self.native_stop("same report")   # A's late hook has no native turn id
        await asyncio.sleep(0)
        self.assertFalse(self.a.done.is_set())
        self.rows({"type":"system","subtype":"turn_duration"})
        await asyncio.sleep(.08)
        self.assertTrue(self.a.done.is_set())
        self.host.agent_turn_end()
        self.assertEqual([first["reply"]["text"],second["reply"]["text"]], ["same report","same report"])
        self.assertEqual(self.pushes, ["reply","reply"])

    async def test_identical_reply_with_current_native_prompt_id_completes_immediately(self):
        first = self.owner_turn(prompt_id="prompt-A")
        self.text("same report")
        self.rows({"type":"system","subtype":"turn_duration"})
        await asyncio.sleep(.08)
        self.host.agent_turn_end()
        self.a.done.clear()
        second = self.owner_turn(prompt_id="prompt-B")
        await self.native_stop("same report", "prompt-A")
        await asyncio.sleep(0)
        self.assertFalse(self.a.done.is_set())
        await self.native_stop("same report", "prompt-B")
        await asyncio.sleep(0)
        self.assertTrue(self.a.done.is_set())
        self.host.agent_turn_end()
        self.assertEqual([first["reply"]["text"],second["reply"]["text"]], ["same report","same report"])

    async def test_stop_reread_discovers_new_native_turn_before_commit(self):
        self.owner_turn()
        await self.queue_native_stop("old body")
        with self.a.transcript.open("a") as stream:
            stream.write(json.dumps({"type":"user","turnOrigin":"scheduled","message":{"content":"next tick"}}) + "\n")
        ended_at = self.a.ended_at
        await asyncio.sleep(0)
        self.assertFalse(self.a.done.is_set())
        self.assertEqual(self.a.ended_at, ended_at)
        self.assertIsNone(self.a.stop_claimed)
        self.assertTrue(self.a.peer_low)
        self.assertTrue(self.a.native_turn)

    async def test_legacy_stop_for_distinct_new_native_text_reaches_each_send(self):
        first = self.owner_turn()
        self.text("A")
        self.rows({"type":"system","subtype":"turn_duration"})
        await asyncio.sleep(.08)
        self.host.agent_turn_end()
        pages = [first]
        for body in ("B", "C"):
            self.a.done.clear()
            pages.append(self.owner_turn())
            self.text(body)
            await self.native_stop(body)
            self.assertTrue(self.a.done.is_set())
            self.host.agent_turn_end()
        self.assertEqual([p["reply"]["text"] for p in pages], ["A","B","C"])

    async def test_superseded_watchdog_event_has_no_destination(self):
        self.owner_turn()
        old_event = await self.queue_native_stop("old")
        page = self.owner_turn()
        self.a._committed_stop(old_event, "old")
        await asyncio.sleep(0)
        self.assertFalse(self.a.done.is_set())
        await self.native_stop("current")
        self.host.agent_turn_end()
        self.assertEqual(page["reply"]["text"], "current")
