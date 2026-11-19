"""Native Codex message notifications delivered to the main history and push path."""
import _hermetic
import tempfile
import unittest
from pathlib import Path

from _testtmp import TEST_TMP
from agentj import provenance
from agentj.agent_codex import CodexAgent
from agentj.compose import Send
from agentj.serve import Host
from agentj.shared_codex import SharedCodexAgent
from agentj.silent import public_turn
from agentj.state import State


class CodexReports(unittest.IsolatedAsyncioTestCase):
    async def check_reports(self, adapter, mode):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            root = Path(tmp)
            state = State(root / "state")
            state.init(relay="ws://127.0.0.1:1")
            host = Host(state, events="jsonl", read_stdin=False)
            host._post = lambda *args: None
            host.emit = lambda *args, **kwargs: None
            host.telegram = host.mail = host.wechat = None
            notifications = []
            host.push_notify = notifications.append
            agent = adapter(host, {"kind": "codex", "dir": str(root), "session_mode": mode})
            host.agent = agent
            agent.tid = "e4259bdf-3371-4d28-b82b-0c45a4c01071"

            def complete_system_turn(source, messages):
                row = host.hist.add({"k": "agent", "via": source, "text": "QA trigger"}, "", "open")
                send = Send(source, "qa-request", row["id"], text="QA trigger", by=source, source=provenance.system())
                agent.cur_send = send
                host.agent_turn_start(send.text, send)
                for item_id, phase, text in messages:
                    agent._on_note("item/completed", {"threadId": agent.tid, "turnId": "qa-turn", "item": {
                        "type": "agentMessage", "id": item_id, "text": text, "phase": phase,
                    }})
                host.agent_turn_end()
                return host.hist.get(row["id"])

            report = complete_system_turn("background_done", [
                ("m1", "commentary", "Checking the file."),
                ("m2", "final_answer", "Verified QA result."),
            ])
            self.assertEqual(report["end"], "done")
            self.assertEqual(report["src"]["via"], "background_done")
            self.assertIn("Verified QA result.", public_turn(report)["reply"]["text"])
            self.assertFalse(report.get("silent"))
            self.assertEqual(notifications, ["reply"])
            quiet = complete_system_turn("hook", [
                ("m3", "commentary", "Checking the follow-up."),
                ("m4", "final_answer", "〔静默消息〕Internal QA receipt only."),
            ])
            self.assertTrue(quiet["silent"])
            self.assertEqual(notifications, ["reply"])
            self.assertIn("Verified QA result.", public_turn(host.hist.get(report["id"]))["reply"]["text"])

    async def test_independent_system_report_and_quiet_followup(self):
        await self.check_reports(CodexAgent, "independent")

    async def test_shared_system_report_and_quiet_followup(self):
        await self.check_reports(SharedCodexAgent, "shared")
