"""0.26.9 selfheal wave 2 lane w2-steer (#471, ADR-0269-w2-steer; security-r1 L-6).

#471 (Mac@M1, Codex long turn): the owner's new message waited until the turn ended while output kept piling onto the old
reply card. Now, with the stand-in Codex app-server (fakecodex.py, real subprocess, fenced where bubblewrap works):
- steer supported → the message goes INTO the running turn (`turn/steer`, expectedTurnId = that turn), every phone gets
  `msg_steered {mid, turn}`, nothing is queued, one turn/start only;
- unsupported (-32601) → it stays queued (say_res queued, delivered after the turn as its own turn/start), never lost; the
  probe is cached per app-server (a second message in a later turn sends no second turn/steer);
- a refusal mid-way (-32600) → falls back to the queue, no msg_steered;
- only the owner's own message into the owner's own turn; Claude Code / OpenCode unchanged.
L-6: a shared desktop turn waiting at its own approval (pending approval / unanswered tool call in the rollout) is never
judged stuck — not by the watchdog, not by an owner-signed end_turn — so no phone message is sent into that open turn.
"""
import _hermetic  # noqa: F401,I001
from _testtmp import TEST_TMP  # noqa: E402
import asyncio
import json
import os
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import agent as agents, codex_steer, compose, shared_codex, turn_heal  # noqa: E402
from agentj.agent_codex import CodexAgent, RPCError  # noqa: E402
from test_slash import _Chain  # noqa: E402
import test_p0269_selfheal as base  # noqa: E402

OWNER = {"kind": "phone", "role": "owner"}


# ------------------------------------------------------------------ the stand-in app-server, end to end through serve
class _Steer(_Chain):
    KIND = "codex"
    MODE = None              # fakecodex steer mode: None = no turn/steer (-32601) · "1" · "refuse"

    def setUp(self):
        super().setUp()
        if self.MODE:
            pathlib.Path(str(self.log) + ".steer").write_text(self.MODE)

    def methods(self, name):
        return [x for x in self.logged() if x.get("method") == name]

    def flow(self, check):
        async def script(c):
            h, s = c["host"], c["s"]
            s.p33 = True
            n = [0]

            async def say(text):
                n[0] += 1
                sid = f"{n[0]:022d}"
                await h._app(s, {"t": "say", "sid": sid, "text": text, "ts": 0})
                def res():
                    return [o for _, o in c["sent"] if o.get("t") == "say_res" and o.get("sid") == sid]
                await c["wait"](res, 5000)                  # posted through serve's send queue
                res = res()
                self.assertTrue(res[-1]["ok"], res)
                return res[-1]["turn"]
            first = await say("SLEEP: 3")
            await c["wait"](lambda: h.agent.status == "working" and isinstance(h.agent.turn_id, str) and h.cur_turn == first)
            second = await say("补充一句")
            await check(c, h, first, second, say)
        self.run_chain(script)


class SteerSupported(_Steer):
    MODE = "1"

    def test_message_goes_into_the_running_turn(self):
        async def check(c, h, first, second, say):
            await c["wait"](lambda: [o for _, o in c["sent"] if o.get("t") == "msg_steered"])
            self.assertTrue(h.agent.q.empty(), "no queue entry")
            [ev] = [o for _, o in c["sent"] if o.get("t") == "msg_steered"]
            self.assertEqual(ev, {"t": "msg_steered", "mid": second, "turn": first})
            states = [o["s"] for _, o in c["sent"] if o.get("t") == "say_state" and o.get("sid") == f"{2:022d}"]
            self.assertIn("delivered", states)
            self.assertNotIn("taken", states, "never taken off a queue: it was never queued")
            page = h.hist.get(second)
            self.assertEqual((page["end"], page["card"]["steered"]), ("done", first))
            await c["wait"](lambda: "STEERED: 补充一句" in (h.hist.get(first) or {}).get("reply", {}).get("text", ""))
            await c["idle"]()
            [st] = self.methods("turn/steer")
            starts = self.methods("turn/start")
            self.assertEqual(len(starts), 1, "one turn only: the message did not wait for its own")
            self.assertEqual(st["params"]["threadId"], starts[0]["params"]["threadId"])
            self.assertTrue(st["params"]["expectedTurnId"])
            self.assertEqual(st["params"]["input"][0]["text"].split("\n")[0], "补充一句")
            log = self.st.log_path.read_text()
            self.assertIn('"ev": "codex_steer"', log)
            self.assertIn('"result": "ok"', log)
            self.assertNotIn("补充一句", log, "host.log keeps no text")
        self.flow(check)


class SteerUnsupported(_Steer):
    MODE = None

    def test_stays_queued_and_is_delivered_after_the_turn_probe_cached(self):
        async def check(c, h, first, second, say):
            await c["wait"](lambda: self.methods("turn/steer"))
            await c["wait"](lambda: not h.agent.q.empty() or len(self.methods("turn/start")) == 2)
            res = [o for _, o in c["sent"] if o.get("t") == "say_res" and o.get("sid") == f"{2:022d}"]
            self.assertEqual(res[-1]["state"], "queued")
            await c["wait"](lambda: len(self.methods("turn/start")) == 2, 30000)
            self.assertEqual(self.methods("turn/start")[1]["params"]["input"][0]["text"].split("\n")[0], "补充一句")
            await c["wait"](lambda: "ECHO: 补充一句" in (h.hist.get(second) or {}).get("reply", {}).get("text", ""))
            await c["idle"]()
            third_turn = await say("SLEEP: 2")
            await c["wait"](lambda: h.cur_turn == third_turn and isinstance(h.agent.turn_id, str))
            await say("再补充")
            await c["wait"](lambda: len(self.methods("turn/start")) == 4, 30000)
            self.assertEqual(len(self.methods("turn/steer")), 1, "unsupported is cached for this app-server")
            self.assertFalse([o for _, o in c["sent"] if o.get("t") == "msg_steered"])
            self.assertIn('"result": "unsupported"', self.st.log_path.read_text())
            await c["idle"]()
        self.flow(check)


class SteerUnanswered(_Steer):
    MODE = "hang"

    def test_unanswered_steer_is_uncertain_and_not_resent(self):
        from unittest.mock import patch
        p = patch.object(codex_steer, "STEER_WAIT", 0.5)
        p.start()
        self.addCleanup(p.stop)

        async def check(c, h, first, second, say):
            await c["wait"](lambda: [o for _, o in c["sent"] if o.get("t") == "msg_steered"])
            [ev] = [o for _, o in c["sent"] if o.get("t") == "msg_steered"]
            self.assertEqual(ev, {"t": "msg_steered", "mid": second, "turn": first, "uncertain": True})
            self.assertTrue(h.agent.q.empty(), "not queued again")
            page = h.hist.get(second)
            self.assertEqual((page["end"], page["card"].get("uncertain")), ("done", True))
            self.assertIn("不会重发", page["reply"]["text"])
            states = [o["s"] for _, o in c["sent"] if o.get("t") == "say_state" and o.get("sid") == f"{2:022d}"]
            self.assertEqual(states[-1:], ["delivered"], "no 「取消」 for a message that may be in the turn")
            await c["idle"]()
            self.assertEqual(len(self.methods("turn/steer")), 1)
            self.assertEqual(len(self.methods("turn/start")), 1, "never re-sent as its own turn")
            self.assertIn('"result": "timeout"', self.st.log_path.read_text())
        self.flow(check)


class SteerRefusedMidway(_Steer):
    MODE = "refuse"

    def test_refusal_falls_back_to_the_queue(self):
        async def check(c, h, first, second, say):
            await c["wait"](lambda: len(self.methods("turn/start")) == 2, 30000)
            self.assertEqual(len(self.methods("turn/steer")), 1)
            self.assertEqual(self.methods("turn/start")[1]["params"]["input"][0]["text"].split("\n")[0], "补充一句")
            self.assertFalse([o for _, o in c["sent"] if o.get("t") == "msg_steered"])
            await c["wait"](lambda: "ECHO: 补充一句" in (h.hist.get(second) or {}).get("reply", {}).get("text", ""))
            self.assertIn('"result": "refused"', self.st.log_path.read_text())
            await c["idle"]()
        self.flow(check)


# ------------------------------------------------------------------ units: who may steer, the fallback, the order
def codex(**kw):
    a = object.__new__(CodexAgent)
    agents.Agent.__init__(a, NS(st=Mock(), lang="zh"), {"_workflow_ceo": True})
    a.cfg.pop("_workflow_ceo")
    a.persist, a.research, a.collect, a.quiet, a.tasks = True, False, None, False, set()
    a.proc = NS(pid=4242, returncode=None)
    a.turn_proc, a.tid, a.turn_id, a.turn_done, a.status = a.proc, "th", "turn-1", asyncio.Event(), "working"
    a.cur_send = NS(source=OWNER)
    a.call = AsyncMock(return_value={"turnId": "turn-1"})
    a.host.say_delivered, a.host.msg_steered = Mock(), Mock()
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def send(text="补充", source=OWNER, **kw):
    s = compose.Send("dev1", "S" * 22, 9, text=text, by="手机")
    s.source = source
    for k, v in kw.items():
        setattr(s, k, v)
    return s


class Units(unittest.IsolatedAsyncioTestCase):
    async def test_steered_never_queued(self):
        a, s = codex(), send()
        a.submit(s)
        await asyncio.gather(*a.tasks)
        self.assertTrue(a.q.empty())
        self.assertEqual((s.state, s.steered), ("delivered", "turn-1"))
        a.host.msg_steered.assert_called_once_with(s)
        a.host.say_delivered.assert_called_once_with(s)
        a.call.assert_awaited_once()
        self.assertEqual(a.call.await_args.args[0], "turn/steer")
        self.assertEqual(a.call.await_args.args[1]["expectedTurnId"], "turn-1")

    async def test_unsupported_cached_per_server_and_queued(self):
        a = codex()
        a.call = AsyncMock(side_effect=RPCError({"code": -32601, "message": "unknown method turn/steer"}))
        s1 = send()
        a.submit(s1)
        await asyncio.gather(*a.tasks)
        self.assertIs(a.q.get_nowait(), s1)
        self.assertEqual(s1.state, "pending", "still withdrawable, still deliverable")
        s2 = send()
        self.assertEqual(codex_steer.eligible(a, s2), "unsupported")
        a.proc = a.turn_proc = NS(pid=5151, returncode=None)            # a relaunched app-server probes again
        self.assertIsNone(codex_steer.eligible(a, s2))

    async def test_definite_no_falls_back(self):
        for err in (RPCError({"code": -32600, "message": "no active turn to steer"}),
                    ConnectionError(codex_steer.NOT_RUNNING)):           # refused / nothing was written
            a = codex()
            a.call = AsyncMock(side_effect=err)
            s = send()
            a.submit(s)
            await asyncio.gather(*a.tasks)
            self.assertIs(a.q.get_nowait(), s, err)
            self.assertEqual(s.state, "pending")
            self.assertIsNone(a.steer_off, "only method-not-found disables steering")
            a.host.msg_steered.assert_not_called()

    async def test_unanswered_steer_is_uncertain_never_requeued(self):
        """security-r2 N-4 / P33-X08: written, no answer → maybe in the turn: never re-sent; the owner is told."""
        for err in (asyncio.TimeoutError(), ConnectionError("codex app-server exited"), OSError("broken pipe")):
            a = codex()
            a.host.say_uncertain = Mock()
            a.call = AsyncMock(side_effect=err)
            s = send()
            a.submit(s)
            await asyncio.gather(*a.tasks)
            self.assertTrue(a.q.empty(), f"{err!r}: not queued again (no double execution)")
            self.assertEqual(s.state, "uncertain", err)
            a.call.assert_awaited_once()
            a.host.say_uncertain.assert_called_once_with(s)
            a.host.msg_steered.assert_called_once_with(s, uncertain=True)
            a.host.say_delivered.assert_not_called()

    async def test_order_kept_behind_a_fallback(self):
        a = codex()
        gate = asyncio.Event()

        async def slow(*_a, **_k):
            await gate.wait()
            raise RPCError({"code": -32600, "message": "no active turn to steer"})
        a.call = AsyncMock(side_effect=slow)
        s1, s2, cmd = send("一"), send("二"), agents.Cmd("context", "")
        a.submit(s1)
        await asyncio.sleep(0)
        a.submit(cmd)                         # a command while the steer is in flight waits for it
        a.submit(s2)
        gate.set()
        await asyncio.gather(*list(a.tasks))
        await asyncio.gather(*list(a.tasks))
        self.assertEqual([a.q.get_nowait() for _ in range(3)], [s1, cmd, s2])

    def test_who_may_steer(self):
        a = codex()
        self.assertIsNone(codex_steer.eligible(a, send()))
        self.assertEqual(codex_steer.eligible(a, send(source={"kind": "telegram", "role": "group", "sender": 1, "chat": 2})),
                         "not_owner")
        self.assertEqual(codex_steer.eligible(a, send(source=None)), "not_owner")
        a.cur_send = NS(source={"kind": "friend_task", "role": "task"})
        self.assertEqual(codex_steer.eligible(a, send()), "turn_not_owner", "never owner words into a stranger's turn")
        a.cur_send = None
        self.assertEqual(codex_steer.eligible(a, send()), "turn_not_owner", "a heartbeat / task turn")
        a = codex()
        for k in ("heartbeat_key", "official_notice_id", "quota_resume_id", "hook_notice", "completed"):
            self.assertEqual(codex_steer.eligible(a, send(**{k: "x"})), "not_say", k)
        self.assertEqual(codex_steer.eligible(a, send("/compact")), "not_plain")
        self.assertEqual(codex_steer.eligible(a, send(prep=object())), "not_plain", "voice still transcribing")
        for attr, v in (("turn_review", True), ("halting", True), ("quiet", True), ("turn_id", None), ("desktop_wait", True),
                        ("rate_retried", True), ("status", "idle"), ("research", True), ("persist", False)):
            b = codex(**{attr: v})
            self.assertIsNotNone(codex_steer.eligible(b, send()), attr)
        b = codex()
        b.turn_done.set()
        self.assertEqual(codex_steer.eligible(b, send()), "no_turn")
        b = codex()
        b.q.put_nowait("earlier")
        self.assertEqual(codex_steer.eligible(b, send()), "behind_queue")

    def test_other_harnesses_unchanged(self):
        self.assertIs(agents.ClaudeAgent.submit, agents.Agent.submit)
        from agentj.agent_opencode import OpenCodeAgent
        self.assertIs(OpenCodeAgent.submit, agents.Agent.submit)
        self.assertTrue(issubclass(shared_codex.SharedCodexAgent, CodexAgent), "the shared actor's own turn steers too")

    def test_unsupported_shapes(self):
        u = codex_steer.unsupported
        self.assertTrue(u(RPCError({"code": -32601, "message": "x"})))
        self.assertTrue(u(RPCError({"code": -32600, "message": "Invalid request: unknown variant `turn/steer`, expected one of"})))
        self.assertTrue(u(RPCError({"code": -32600, "message": "turn/steer requires experimentalApi capability"})))
        for m in ("no active turn to steer", "cannot steer a review turn", "cannot steer a compact turn",
                  "expected active turn id `a` but found `b`"):
            self.assertFalse(u(RPCError({"code": -32600, "message": m})), m)


# ------------------------------------------------------------------ L-6: a desktop turn waiting at its approval
def rollout(tmp, *extra):
    p = pathlib.Path(tmp) / "r.jsonl"
    lines = [{"type": "session_meta", "payload": {"id": "x"}}, {"type": "event_msg", "payload": {"type": "task_started"}},
             *extra]
    p.write_text("".join(json.dumps(x) + "\n" for x in lines))
    return p


CALL = {"type": "response_item", "payload": {"type": "function_call", "name": "exec_command", "call_id": "c1", "arguments": "{}"}}
OUT = {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "c1", "output": "ok"}}
ASK = {"type": "event_msg", "payload": {"type": "exec_approval_request", "call_id": "c2"}}


class PendingApproval(unittest.TestCase):
    def test_rollout_shapes(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            pa = shared_codex.pending_approval
            self.assertFalse(pa(rollout(tmp)))
            self.assertTrue(pa(rollout(tmp, CALL)), "a tool call with no output yet: maybe asking")
            self.assertFalse(pa(rollout(tmp, CALL, OUT)))
            self.assertTrue(pa(rollout(tmp, CALL, OUT, ASK)), "an approval request event")
            self.assertFalse(pa(rollout(tmp, ASK, {"type": "event_msg", "payload": {"type": "exec_command_end", "call_id": "c2"}})))
            self.assertFalse(pa(rollout(tmp, CALL, {"type": "event_msg", "payload": {"type": "turn_aborted"}})),
                             "an older turn's open call does not count")
            self.assertTrue(pa(pathlib.Path(tmp) / "missing.jsonl"), "unreadable = assume asking (never stuck)")


@unittest.skipUnless(base.LINUX, "holders are read from /proc")
class DesktopApprovalNotStuck(base.SharedCodexHeal):
    """Negative control on the 0269 customer fixture: the same 3 h quiet desktop turn, but waiting at its own approval."""

    def rollout(self):
        path = super().rollout()
        with path.open("a") as f:
            f.write(json.dumps(CALL) + "\n" + json.dumps(ASK) + "\n")
        os.utime(path, (base.OLD, base.OLD))
        return path

    async def test_watchdog_does_not_judge_it_stuck(self):
        path = self.rollout()
        self.holder(path)
        self.assertFalse(turn_heal.desktop_stuck(path))
        a = self.agent()
        t = asyncio.create_task(a.turn("into the open turn?"))
        self.addCleanup(t.cancel)
        await self.wait_for(lambda: a.desktop_wait)
        await self.heal.tick()
        self.assertFalse(self.heal.state["stuck"])
        await asyncio.sleep(1.5)
        self.assertTrue(a.desktop_wait)
        self.assertFalse([m for m, _ in self.calls if m == "turn/start"], "no phone message into the asking turn")

    async def test_owner_end_turn_does_not_release_it(self):
        path = self.rollout()
        self.holder(path)
        a = self.agent()
        t = asyncio.create_task(a.turn("x"))
        self.addCleanup(t.cancel)
        await self.wait_for(lambda: a.desktop_wait)
        a.release_stuck()
        await asyncio.sleep(2.5)
        self.assertTrue(a.desktop_wait, "still waiting behind the approval")
        self.assertFalse([m for m, _ in self.calls if m == "turn/start"])
        self.assertIn("shared_stuck_refused", [c.args[0] for c in self.host.st.log.call_args_list if c.args])

    async def test_clear_and_stop_treat_it_as_running(self):
        path = self.rollout()
        self.holder(path)
        a = self.agent()
        a.rollout = path
        self.assertTrue(await a._desktop_running())

    # the parent's positive cases run in test_p0269_selfheal; here only the negative control
    test_stuck_desktop_turn_heals_and_the_queued_message_reaches_the_same_thread = None
    test_desktop_turn_running_a_tool_is_not_stuck = None
    test_estop_blocks_the_automatic_heal = None
    test_queued_notice_carries_waited_and_stuck = None
    test_stop_on_a_stuck_desktop_turn_does_not_say_esc = None
    test_clear_on_a_stuck_desktop_turn_clears = None


if __name__ == "__main__":
    unittest.main()
