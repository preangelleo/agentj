"""Local native Claude stand-in for the paired system-report release journey."""
import asyncio
import contextlib
import os
import time
import traceback
from pathlib import Path
from unittest.mock import patch

from agentj import busy, provenance, shared


async def drive(host, root):
    work = root / "home" / "coding"
    agent = shared.SharedClaudeAgent(host, {"kind": "claude", "dir": str(work),
                                            "language": "zh", "session_mode": "shared"})
    agent.session = {"pid": os.getpid(), "cwd": str(work), "status": "idle", "kind": "interactive",
                     "sessionId": "af66b6d0-14c3-4470-9e86-c086aadc458c",
                     "messagingSocketPath": str(root / "native.sock")}
    agent.loop = asyncio.get_running_loop()
    agent.transcript = root / "native.jsonl"
    agent.transcript.touch()
    replies = ("QA verified release report.", "〔静默消息〕Internal QA receipt only.")

    request = asyncio.Queue()

    def native_send(session, prompt, source):
        assert provenance.from_name(source) == provenance.SYSTEM
        agent.loop.call_soon_threadsafe(request.put_nowait, prompt)

    while not (root / "start-completions").exists():
        await asyncio.sleep(.1)
    assert host.agent is None
    host.agent = agent
    agent.set_status("idle")
    with patch.object(shared, "claude_send", native_send), patch.object(shared, "claude_sessions", return_value=[agent.session]):
        worker = asyncio.create_task(agent.run())
        try:
            expected_pushes = {}
            for i, reply in enumerate(replies):
                if i == 0:
                    now = int(time.time())
                    item = busy.item("run:journey", "fixture", "ceo", "QA completed job", since=now - 2)
                    host.busy.finish({**item, "foreign": False}, 0, now)
                    send = host.background_done.tick(now)
                    assert send and send.by == "background_done"
                else:
                    while not (root / "next-webhook").exists():
                        await asyncio.sleep(.05)
                    assert host.hooks._to_agent("QA follow-up webhook", "fixture-webhook") == "queued"
                prompt = await asyncio.wait_for(request.get(), 10)
                send = agent.cur_send
                if not send or send.by != ("background_done" if i == 0 else "hook"):
                    (root / "fixture-error").write_text(f"unexpected native source: {getattr(send, 'by', None)}")
                    return
                async with asyncio.timeout(5):
                    while send.state != "delivered":
                        await asyncio.sleep(.01)
                event = {"session_id": agent.session["sessionId"]}
                await agent.hook_event({**event, "hook_event_name": "UserPromptSubmit", "prompt": prompt})
                await agent.hook_event({**event, "hook_event_name": "Stop", "last_assistant_message": reply})
                async with asyncio.timeout(12):
                    while agent.cur_send is not None or agent.status != "idle":
                        await asyncio.sleep(.05)
                row = host.hist.get(send.turn)
                assert row["src"]["via"] == send.by and row["end"] == "done"
                if i == 0:
                    assert row["reply"]["text"] == reply and not row.get("silent")
                    expected_pushes = dict(host.push_last)
                    assert len(expected_pushes) == 1
                    (root / "reported").write_text(str(send.turn))
                else:
                    assert row.get("silent")
                    assert host.push_last == expected_pushes, "silent final initiated a later push"
                    (root / "completed").write_text(str(send.turn))
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            host.agent = None


def install(cls):
    original = cls.run
    async def with_fixture(host):
        root = Path(os.environ["AGENTJ_TEST_COMPLETION_JOURNEY"]).resolve()
        assert root == host.st.root.parent and root.name.startswith("aj-history-journey-")
        async def guarded():
            try:
                await drive(host, root)
            except Exception as error:
                frame = traceback.extract_tb(error.__traceback__)[-1]
                (root / "fixture-error").write_text(f"{type(error).__name__} {Path(frame.filename).name}:{frame.lineno}")
        task = asyncio.create_task(guarded())
        try:
            return await original(host)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    cls.run = with_fixture
