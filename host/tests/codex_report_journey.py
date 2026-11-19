"""Opt-in, synthetic QA job delivered by the actual Codex app-server and model."""
import asyncio
import json
import os
import time
from pathlib import Path

from agentj import busy
from agentj.agent_codex import CodexAgent
from agentj.silent import is_silent, strip_markers
from agentj.shared_codex import SharedCodexAgent


async def drive_native(host, root):
    work = root / "home" / "coding"
    evidence = work / "QA-REPORT.md"
    marker = "AVON-QA-" + os.urandom(4).hex().upper()
    evidence.write_text(f"The QA run passed with verification code {marker}.\n")
    # Supply an actual report and request its unique receipt in the fresh QA thread.
    (work / "AGENTS.md").write_text(
        "This is a disposable QA workspace. For a completed job, read its report "
        "in this directory and tell the owner the verification code as plain text. "
        "For a QA quiet webhook, output exactly 〔静默消息〕Internal QA receipt only. "
        "as the entire answer, without tools or a preamble. Do not change files or contact anyone.\n"
    )
    while not (root / "start-completions").exists():
        await asyncio.sleep(.1)
    agent = host.agent
    mode = os.environ['AGENTJ_TEST_NATIVE_CODEX_REPORT']
    expected = SharedCodexAgent if mode == "shared" else CodexAgent
    assert type(agent) is expected and agent.cfg["session_mode"] == mode
    assert Path(agent.cfg["dir"]).resolve() == work
    assert Path(os.environ['HOME']).resolve() == root / "home"
    async with asyncio.timeout(20):
        while agent.cur_send is not None or agent.status != "idle":
            await asyncio.sleep(.1)
    phone_sid = host.st.agent_session("codex")
    assert phone_sid
    if mode == "independent":
        assert agent.cfg.get("fence") and agent.isolation_effective
    now = int(time.time())
    item = busy.item("run:qa-codex-report", "qa", "ceo", "QA verification", since=now - 2, report=str(evidence))
    host.busy.finish({**item, "foreign": False}, 0, now)
    send = host.background_done.tick(now)
    assert send and send.by == "background_done"
    async with asyncio.timeout(170):
        while True:
            row = host.hist.get(send.turn) if send.turn else None
            if row and row.get("end") in ("done", "error", "stopped") and agent.cur_send is None:
                break
            await asyncio.sleep(.1)
    assert row["end"] == "done" and row["src"]["via"] == "background_done"
    assert not row.get("silent")
    # Inspect only the new private QA thread. The UI must render exactly what its native turn returned.
    sessions = root / "home" / ".codex" / "sessions"
    session_id = host.st.agent_session("codex")
    assert session_id == phone_sid
    rollouts = list(sessions.rglob(f"rollout-*-{session_id}.jsonl"))
    assert len(rollouts) == 1
    records = [json.loads(line) for line in rollouts[0].read_text().splitlines()]
    finals = [event['payload'].get('last_agent_message') for event in records
              if event.get('type') == 'event_msg' and event['payload'].get('type') == 'task_complete']
    assert len(finals) >= 2 and "QA-ACK" in finals[-2] and finals[-1] and marker in finals[-1]
    final = finals[-1].strip()
    history_text = row['reply']['text'].strip()
    (root / "native-outcome").write_text(json.dumps({
        "sameNativeThread": True, "modelReturnedReceipt": marker in final, "historyContainsFinal": final in history_text,
        "historyContainsReceipt": marker in history_text, "historyChars": len(history_text),
        "nativeFinalChars": len(final), "exactFinal": history_text == final,
    }))
    assert final in history_text
    (root / "native-final").write_text(final)
    (root / "reported").write_text(str(send.turn))

    while not (root / "next-webhook").exists():
        await asyncio.sleep(.1)
    quiet_prompt = "QA quiet webhook. The job needs no owner notification. Respond exactly: 〔静默消息〕Internal QA receipt only."
    assert host.hooks._to_agent(quiet_prompt, "qa-quiet-check") == "queued"
    async with asyncio.timeout(165):
        while True:
            rows, _ = host.hist.page(after=send.turn)
            quiet = next((r for r in rows if r.get("src", {}).get("via") == "hook" and r.get("end") == "done"), None)
            if quiet and agent.cur_send is None:
                break
            await asyncio.sleep(.1)
    assert host.st.agent_session("codex") == phone_sid
    records = [json.loads(line) for line in rollouts[0].read_text().splitlines()]
    quiet_final = [event["payload"].get("last_agent_message") for event in records
                   if event.get("type") == "event_msg" and event["payload"].get("type") == "task_complete"][-1]
    outcome = json.loads((root / "native-outcome").read_text())
    outcome.update(nativeQuietMarker=is_silent(quiet_final), nativeQuietExplanation=bool(strip_markers(quiet_final or "").strip()),
                   historyFlaggedQuiet=bool(quiet.get("silent")), quietReturnedChars=len(quiet_final or ""))
    (root / "native-outcome").write_text(json.dumps(outcome))
    assert is_silent(quiet_final)
    # Shared QA instructions request a marker followed by an explanation. The independent
    # product identity may deliberately reply with the marker alone. Both must stay quiet.
    if mode == "shared":
        assert strip_markers(quiet_final).strip()
    assert quiet.get("silent")
    (root / "completed").write_text(str(quiet["id"]))
