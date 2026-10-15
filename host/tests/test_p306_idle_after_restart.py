"""P306: after a serve restart (upgrade) the phone stayed grey until the owner's first message.

Root cause (host.log 10-15 10:17): the relay comes up before the Agent is made, so a phone that resumes at once is told
`status: none`; serve then recorded the new Agent's "idle" as already sent (`sent_status = eff_status()`) without sending
it, and nothing changed the status again until a turn ran. `agentj doctor --reconnect` saw "idle" at the host and repaired
nothing. Fixed: the Agent's start broadcasts its status; doctor --reconnect and agent_reconnect re-send it to every
phone; a shared Claude with nothing attached says `down` (grey + the notice) instead of a blue idle no message reaches."""
import _hermetic  # noqa: F401
from _testtmp import TEST_TMP  # noqa: E402
import asyncio
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import serve, shared, reconnect  # noqa: E402
from agentj import agent as agents  # noqa: E402
from agentj.state import State  # noqa: E402
import test_p147  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1] / "agentj"
N_SECONDS = 5          # the phone must leave grey within this after the restart


class FakeAgent:
    def __init__(self, host, cfg):
        self.host, self.cfg, self.kind, self.status = host, cfg, cfg["kind"], "idle"
        self.models_cache = []
    def start(self): pass
    def cur_model(self): return None
    def cur_effort(self): return None
    async def stop(self): pass
    async def halt(self, clear_queue=True): pass


def _host(tmp, mode):
    st = State(pathlib.Path(tmp) / "state")
    st.init(relay="ws://127.0.0.1:1")
    work = pathlib.Path(tmp) / "work"; work.mkdir()
    st.set_agent_config("claude", str(work), fence=False)
    host = serve.Host(st, events="quiet", read_stdin=False)
    host.agent_cfg = {**(st.agent_config() or {}), "session_mode": mode}
    return host


def _phone(host, sent):
    s = serve.Session(cid=1, state="ready", device="PHONEPHONEPHONE1", pub=os.urandom(32))
    s.p33 = True
    async def send_app(sess, obj):
        if obj.get("t") == "status":
            sent.append(obj["s"])
        return True
    host.send_app = send_app
    host.st.is_allowed = lambda pub: True
    return s


class Restart(unittest.IsolatedAsyncioTestCase):
    """The real serve start-up (`Host._run`), a phone that resumes before the Agent exists."""

    async def run_serve(self, mode):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            host = _host(tmp, mode)
            sent = []
            s = _phone(host, sent)
            async def relay_loop(self_):        # relay up first (as in host.log): the phone resumes at once
                host.sessions[s.cid] = s
                await host.send_app(s, host._status_msg())
                await asyncio.Event().wait()
            with patch.object(serve.Host, "relay_loop", relay_loop), patch.object(agents, "make", FakeAgent), \
                    patch.object(serve.Host, "on_ready", AsyncMock()):
                task = asyncio.create_task(host.run())
                try:
                    for _ in range(N_SECONDS * 20):
                        if sent and sent[-1] == "idle":
                            break
                        await asyncio.sleep(0.05)
                finally:
                    host.stopping.set()
                    with __import__("contextlib").suppress(Exception, asyncio.CancelledError):
                        await asyncio.wait_for(task, 20)
            return sent

    async def test_shared_phone_leaves_grey_without_a_message(self):
        sent = await self.run_serve("shared")
        self.assertEqual(sent[0], "none", "the phone was told before the Agent existed (the race itself)")
        self.assertEqual(sent[-1], "idle", f"within {N_SECONDS} s, no message sent: {sent}")

    async def test_independent_phone_leaves_grey_without_a_message(self):
        sent = await self.run_serve("independent")
        self.assertEqual(sent[-1], "idle", sent)

    def test_the_silent_record_is_gone(self):
        src = (ROOT / "serve.py").read_text()
        self.assertNotIn("self.sent_status = self.eff_status()", src)


class Push(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.host = _host(self.tmp.name, "shared")
        self.sent = []
        s = _phone(self.host, self.sent)
        self.host.sessions[s.cid] = s
        self.host.agent = FakeAgent(self.host, {"kind": "claude"})

    async def drain(self):
        for _ in range(10):
            await asyncio.sleep(0)

    async def test_status_push_resends_an_unchanged_status(self):
        self.host.status_changed(); await self.drain()
        self.host.status_changed(); await self.drain()
        self.assertEqual(self.sent, ["idle"], "unchanged status is not repeated by itself")
        self.host.push_status(); await self.drain()
        self.assertEqual(self.sent, ["idle", "idle"])

    async def test_agent_reconnect_pushes_even_when_nothing_changed(self):
        self.host.agent.reconnect = AsyncMock(return_value={"ok": True, "changed": False, "session": "abcdefab"})
        self.host.sent_status = "idle"
        res = await self.host.agent_reconnect()
        await self.drain()
        self.assertTrue(res["ok"])
        self.assertEqual(self.sent, ["idle"])

    async def test_no_agent_shows_none(self):
        self.host.agent = None
        self.host.push_status(); await self.drain()
        self.assertEqual(self.sent, ["none"])


class SharedNothingAttached(test_p147.Fixture):
    async def test_no_live_session_is_down_then_a_new_window_brings_idle(self):
        dead = "99999999-1111-1111-1111-111111111111"     # the pin from before the restart; that window is closed
        a = shared.SharedClaudeAgent(self.host, {**self.cfg, "shared_session_id": dead})
        self.assertFalse(await a.attach())
        self.assertEqual(a.status, "down")
        self.assertEqual(self.host.statuses[-1], "down")
        self.assertTrue(self.host.notices, "the owner is told what to do")
        sid = self.session("b", 200)
        self.assertTrue(await a.attach())
        self.assertEqual(a.session["sessionId"], sid)
        self.assertEqual((a.status, self.host.statuses[-1]), ("idle", "idle"))

    async def test_a_live_pin_attaches_idle_with_no_status_change(self):
        sid = self.session("a", 100)
        a = shared.SharedClaudeAgent(self.host, {**self.cfg, "shared_session_id": sid})
        self.assertTrue(await a.attach())
        self.assertEqual((a.status, self.host.statuses), ("idle", []))

    async def test_failed_attach_inside_a_turn_does_not_flip_working(self):
        a = shared.SharedClaudeAgent(self.host, {**self.cfg, "shared_session_id": "99999999-1111-1111-1111-111111111111"})
        a.status = "working"
        self.assertFalse(await a.attach())
        self.assertEqual(a.status, "working", "the turn's own end sets down via is_down()")


class Doctor(unittest.TestCase):
    def test_all_up_resends_the_status(self):
        sent = []
        def ctl(st, req, timeout=None):
            sent.append(req["cmd"])
            return {"relay_state": "up", "agent": "claude", "agent_status": "idle"} if req["cmd"] == "status" else {"ok": True}
        out = io.StringIO()
        code = reconnect.run(None, "zh", ask=False, out=out, ctl=ctl, sleep=lambda s: None, env={}, as_json=True)
        self.assertEqual(code, 0)
        self.assertEqual(sent, ["status", "status_push"])

    def test_an_older_serve_without_status_push_is_still_ok(self):
        def ctl(st, req, timeout=None):
            if req["cmd"] == "status_push":
                raise RuntimeError("unknown command")
            return {"relay_state": "up", "agent": "claude", "agent_status": "idle"}
        out = io.StringIO()
        self.assertEqual(reconnect.run(None, "zh", ask=False, out=out, ctl=ctl, sleep=lambda s: None, env={},
                                       as_json=True), 0)
        self.assertTrue(json.loads(out.getvalue())["ok"])


if __name__ == "__main__":
    unittest.main()
