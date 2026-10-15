"""P282 (追加, Mac 10-14 22:33): after /switch-harness to a shared Codex the owner's message read as queued while the Agent was
already opening the session for it (46–65 s app-server spawn + thread resume), and a Codex turn waited on `turn/completed` only.

- serve: say_state `taken` the moment the Agent takes a phone say off its queue (not for heartbeat / bot / notice sends)
- CodexAgent.await_turn: turn/completed, else the quiet-tail fallback (reply came, nothing open, no card, TAIL_QUIET s silent)
  interrupts the turn on Codex's side and ends it; a late turn/completed of that turn never ends the next one
"""
import _hermetic  # noqa: F401
from _testtmp import TEST_TMP  # noqa: E402  P202: fixtures on tmpfs
import asyncio
import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import agent_codex
from agentj.agent_codex import CodexAgent
from agentj.serve import Host


def codex():
    a = object.__new__(CodexAgent)
    a.cfg, a.thread, a.human, a.models_cache = {}, {}, {}, []
    a.unavailable_models, a.halting, a.tid, a.turn_id = set(), False, "th", "turn-1"
    a.turn_done, a.turn_status, a.items, a.pending, a.tasks = asyncio.Event(), {}, {}, {}, set()
    a.open_items, a.said, a.last_note, a.tail_cut = set(), False, 0.0, None
    a.host = SimpleNamespace(lang="zh", st=Mock(), turn_failed=Mock(), agent_notice=Mock(), asks={})
    a.persist, a.research, a.quiet, a.collect = True, False, False, None
    a.meter, a.touch, a._emit = Mock(), Mock(), Mock()
    a.call = AsyncMock(return_value={})
    return a


def note(a, method, **p):
    a._on_note(method, {"threadId": "th", **p})


class TailFallback(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for k, v in (("TAIL_QUIET", 0.2), ("TAIL_TICK", 0.02)):
            p = patch.object(agent_codex, k, v)
            p.start()
            self.addCleanup(p.stop)

    async def test_reply_then_silence_ends_the_turn_and_interrupts_it(self):
        a = codex()
        a.tail_reset()
        note(a, "item/started", item={"id": "m1", "type": "agentMessage"})
        note(a, "item/completed", item={"id": "m1", "type": "agentMessage", "text": "切换已完成"})
        await asyncio.wait_for(a.await_turn(), 3)
        self.assertTrue(a.turn_done.is_set())
        a.call.assert_awaited_once_with("turn/interrupt", {"threadId": "th", "turnId": "turn-1"}, 3)
        self.assertEqual((a.tail_cut, a.turn_status.get("status")), ("turn-1", "completed"))
        self.assertEqual(a.host.st.log.call_args.args[0], "codex_turn_tail")

    async def test_late_completion_of_the_cut_turn_does_not_end_the_next(self):
        a = codex()
        a.tail_reset()
        note(a, "item/completed", item={"id": "m1", "type": "agentMessage", "text": "x"})
        await asyncio.wait_for(a.await_turn(), 3)
        a.turn_done, a.turn_id = asyncio.Event(), None          # the next turn began, its id not known yet
        note(a, "turn/completed", turn={"id": "turn-1", "status": "interrupted"})
        self.assertFalse(a.turn_done.is_set())
        self.assertIsNone(a.tail_cut)
        note(a, "turn/completed", turn={"id": "turn-2", "status": "completed"})
        self.assertTrue(a.turn_done.is_set())

    async def _still_running(self, a):
        t = asyncio.create_task(a.await_turn())
        await asyncio.sleep(0.5)
        self.assertFalse(a.turn_done.is_set())
        a.call.assert_not_awaited()
        note(a, "turn/completed", turn={"id": "turn-1", "status": "completed"})
        await asyncio.wait_for(t, 2)
        self.assertEqual(a.turn_status.get("status"), "completed")

    async def test_no_reply_yet_waits(self):
        a = codex()
        a.tail_reset()
        await self._still_running(a)

    async def test_running_command_is_never_cut(self):
        a = codex()
        a.tail_reset()
        note(a, "item/completed", item={"id": "m1", "type": "agentMessage", "text": "先跑个构建"})
        note(a, "item/started", item={"id": "c1", "type": "commandExecution"})
        await self._still_running(a)

    async def test_open_approval_card_is_never_cut(self):
        a = codex()
        a.tail_reset()
        note(a, "item/completed", item={"id": "m1", "type": "agentMessage", "text": "x"})
        a.pending[7] = asyncio.get_running_loop().create_future()
        await self._still_running(a)

    async def test_other_thread_items_do_not_count(self):
        a = codex()
        a.tail_reset()
        a._on_note("item/completed", {"threadId": "child", "item": {"id": "m9", "type": "agentMessage", "text": "x"}})
        self.assertFalse(a.said)
        await self._still_running(a)


class Taken(unittest.TestCase):
    def host(self):
        h = object.__new__(Host)
        h.turn_by, h.activity, h.posted = None, Mock(), []
        h._post = lambda fn, *args: h.posted.append(args)
        return h

    def test_phone_say_gets_taken(self):
        h = self.host()
        h.agent_turn_start("hi", SimpleNamespace(device="dev1", sid="S" * 22, turn=5, by=None))
        self.assertEqual(h.posted, [("dev1", {"t": "say_state", "sid": "S" * 22, "s": "taken"})])
        self.assertEqual(h.cur_turn, 5)

    def test_host_sends_do_not(self):
        for k in ("heartbeat_key", "bot_handoff_id", "official_notice_id"):
            h = self.host()
            h.agent_turn_start("hi", SimpleNamespace(device="d", sid="S" * 22, turn=1, by=None, **{k: "x"}))
            self.assertEqual(h.posted, [], k)
        h = self.host()
        h.agent_turn_start("hi", None)
        self.assertEqual(h.posted, [])


class SharedKeep(unittest.IsolatedAsyncioTestCase):
    """P282: a shared Codex keeps its app-server (thread loaded) for a phone message already queued; else released."""

    def agent(self, tmp):
        from agentj.shared_codex import SharedCodexAgent
        host = Mock()
        host.st.agent_session.return_value = None
        a = SharedCodexAgent(host, {"kind": "codex", "dir": tmp, "_workflow_ceo": True,
                                    "shared_session_id": "11111111-1111-1111-1111-111111111111"})
        a.proc = SimpleNamespace(returncode=None)
        a.tid = "11111111-1111-1111-1111-111111111111"
        a._ready = AsyncMock(return_value=True)
        a.remind = AsyncMock()
        a.call = AsyncMock(return_value={"turn": {"id": "t1"}})
        a.await_turn = AsyncMock()
        a.read_desktop = Mock()
        a._kill = AsyncMock()
        a.meter = Mock()
        return a

    async def test_queued_next_turn_keeps_then_skips_spawn_and_resume(self):
        import tempfile
        from agentj import shared_codex
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp, patch.object(shared_codex, "KEEP_WAIT", 0.05):
            a = self.agent(tmp)
            a.q.put_nowait("next")
            await a.turn("hello")
            self.assertTrue(a.kept)
            a._kill.assert_not_awaited()
            a.call.reset_mock()
            self.assertTrue(await shared_codex.SharedCodexAgent._thread(a))     # the loaded thread is used as is
            a.call.assert_not_awaited()
            self.assertFalse(a.kept)
            await asyncio.sleep(0.1)                                            # the release timer sees it taken: no kill
            a._kill.assert_not_awaited()

    async def test_nothing_queued_releases_at_once(self):
        import tempfile
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            a = self.agent(tmp)
            await a.turn("hello")
            self.assertFalse(a.kept)
            a._kill.assert_awaited_once()

    async def test_kept_but_never_taken_is_released(self):
        import tempfile
        from agentj import shared_codex
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp, patch.object(shared_codex, "KEEP_WAIT", 0.05):
            a = self.agent(tmp)
            a.q.put_nowait("withdrawn later")
            await a.turn("hello")
            p = a.proc
            await asyncio.sleep(0.15)
            self.assertFalse(a.kept)
            a._kill.assert_awaited_once_with(p)


if __name__ == "__main__":
    unittest.main()
