"""The old process accepts a phone say, a new process dispatches, another sees only dup."""
import _hermetic  # noqa: F401
import asyncio
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from _testtmp import TEST_TMP
from test_l1 import Phone, _host, _state
from test_p313_receipts import _Agent, sid

from agentj import controls, diskq, phone_update, provenance, serve, upgrade_messages, wire
from agentj.browser_login import RequestPhone
from agentj.state import State

WORDS = "这次安装遇到了什么问题？"


async def stage(root, phase, scenario):
    state = State(root / "s")
    if phase == "old":
        state = _state(root)
        phone = Phone(state)
        (root / "fixture.json").write_text(json.dumps([phone.did, wire.b64u(phone.pub), sid()]))
        phone_update.reserve(state)
    device, public, mid = json.loads((root / "fixture.json").read_text())
    events = []
    host = _host(state, events)
    host.agent = _Agent()
    session = serve.Session(cid=8, state="ready", device=device, pub=wire.unb64u(public), p33=True,
                            sign_pub=state.sign_key(device), provenance=provenance.phone())
    host.sessions[session.cid] = session
    session.login_phone = RequestPhone.from_session(host, session)
    host.receipts_restart()

    async def say():
        await host._app(session, {"t": "say", "sid": mid, "text": WORDS, "ts": 0})
        for _ in range(5):
            await asyncio.sleep(0)
        return next(obj for _, obj in reversed(events) if obj.get("t") == "say_res")

    with patch.object(host, "_setup_round_trip", AsyncMock()), patch.object(host, "_model_set_flush", AsyncMock()):
        if phase == "old":
            result = await say()
            assert result["ok"] and result["state"] == "queued", result
            assert not host.agent.got
            assert host.receipts.state(device, mid) == "held"
            if scenario == "cancel":
                await host._app(session, {"t": "say_cancel", "sid": mid})
            if scenario == "tamper":
                page = host.hist.get(result["turn"])
                page["src"]["text"] = "a different message"
                host.hist_update(page["id"], card={"queued": True})
        elif phase == "new":
            phone_update.release(state)
            if scenario == "revoked":
                state.remove_device(device)
            if scenario == "stop":
                host.estop = {"on": True}
            if scenario == "stop_then_resume":
                async def toggle():
                    host.estop = controls.set_estop(state, True, "terminal")
                    host.estop = controls.set_estop(state, False, "terminal")
                host._model_set_flush.side_effect = toggle
            await asyncio.gather(upgrade_messages.resume(host), upgrade_messages.resume(host))
            if scenario == "normal":
                assert len(host.agent.got) == 1
                send = host.agent.got[0]
                assert send.source == provenance.phone() and send.delivery_allowed() and WORDS in send.text
                host.cur_turn = send.turn
                host.say_delivered(send)
                host.agent_text("隔离测试回答")
                host.agent_turn_end()
            else:
                assert not host.agent.got
        else:
            await upgrade_messages.resume(host)
            assert not host.agent.got
            if scenario != "revoked":
                assert (await say())["why"] == "dup"
            pages, _ = host.hist.page()
            turns = [p for p in pages if p["src"].get("dev") == device]
            assert len(turns) == 1 and turns[0]["end"] != "open", turns
            if scenario == "normal":
                assert turns[0]["end"] == "done" and turns[0]["reply"]["text"] == "隔离测试回答"
    host.receipts.flush()
    await diskq.aflush()


class CrossProcess(unittest.TestCase):
    def test_handoff_and_restarts(self):
        for scenario in ("normal", "cancel", "tamper", "revoked", "stop", "stop_then_resume"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory(dir=TEST_TMP) as folder:
                for phase in ("old", "new", "again"):
                    result = subprocess.run([sys.executable, __file__, folder, phase, scenario],
                                            capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    asyncio.run(stage(Path(sys.argv[1]), sys.argv[2], sys.argv[3]))
