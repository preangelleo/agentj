"""P572 (#385): the Agent's reason for a card, outside the code block, right above the buttons.

- action_policy.reason() reads it back out of the SIGNED card text (the 「Agent 附注」 / "Agent note:" block): description
  first, then summary, then card; already redacted; "" when the Agent gave none; never from a look-alike block.
- the real Host.ask path puts it on the `ask` message as `reason` (a parked card keeps it when it is pushed on connect);
  host cards with no Agent behind them (pairing, passphrase reset) send empty `reason` for the missing-note fallback.
- the phone fixture (web/test/fixtures/p572/summaries.json) is exactly what action_policy.summary() writes today.

  P572_WRITE_FIXTURE=1 python -m unittest tests.test_p572_reason   → rewrites the phone fixture from the host's own text
"""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import time
from types import SimpleNamespace as NS
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import action_policy, approval_level  # noqa: E402

from test_p551_confirm_levels import Base  # noqa: E402

FIXTURE = pathlib.Path(__file__).resolve().parents[2] / "web/test/fixtures/p572/summaries.json"
REASON = {"zh": "释放空间并列出 Qwen3-1.7B 的量化文件", "en": "Free space and list Qwen3-1.7B quantized files"}
COMMAND = "\n".join([
    "df -h /", "du -sh ~/models/* 2>/dev/null | sort -h | tail -5", "rm -rf ~/.cache/huggingface/hub/tmp-*",
    "ls -la ~/models/Qwen3-1.7B", "find ~/models/Qwen3-1.7B -name '*.gguf' -size +100M",
    "for q in q4_k_m q5_k_m q8_0; do ls ~/models/Qwen3-1.7B/*$q* 2>/dev/null; done", "df -h /",
])


def card_text(lang: str, description: str | None) -> str:
    inp = {"command": COMMAND, **({"description": description} if description is not None else {})}
    with tempfile.TemporaryDirectory() as empty:          # no .env next to it: the fixture never depends on this machine
        return action_policy.summary("Bash", inp, lang=lang, workdir=empty)


def fixture() -> dict:
    return {"zh-with": card_text("zh", REASON["zh"]), "zh-none": card_text("zh", None), "zh-mixed": card_text("zh", REASON["en"]),
            "en-with": card_text("en", REASON["en"]), "en-none": card_text("en", None)}


class Reason(unittest.TestCase):
    def test_description_first_then_summary_then_card(self):
        for lang in ("zh", "en"):
            text = action_policy.summary("Bash", {"command": "ls", "description": "list it", "summary": "s", "card": "c"}, lang=lang)
            self.assertEqual(action_policy.reason(text), "list it")
            text = action_policy.summary("AuthRescue", {"summary": "  fix the login  "}, lang=lang)
            self.assertEqual(action_policy.reason(text), "fix the login")
            self.assertEqual(action_policy.reason(action_policy.summary("X", {"card": "c only"}, lang=lang)), "c only")

    def test_none_given(self):
        self.assertEqual(action_policy.reason(action_policy.summary("Bash", {"command": "ls"}, lang="zh")), "")
        self.assertEqual(action_policy.reason(action_policy.summary("Bash", {"command": "ls", "description": "   "})), "")
        self.assertEqual(action_policy.reason(action_policy.summary("Bash", {"command": "ls", "description": 7})), "")
        self.assertEqual(action_policy.reason(None), "")

    def test_redacted_and_complete(self):
        text = action_policy.summary("Bash", {"command": "ls", "description": "use password=hunter2hunter2 " + "x" * 600})
        got = action_policy.reason(text)
        self.assertNotIn("hunter2hunter2", got)
        self.assertTrue(got.endswith("x" * 600))
        self.assertIn(json.dumps(got, ensure_ascii=False)[1:-1], text)   # what the phone checks: it stands in the signed text

    def test_long_reason_keeps_the_final_limitation(self):
        description = "Only list these files. " * 50 + "Do not delete or send anything."
        text = action_policy.summary("Bash", {"command": "ls", "description": description})
        self.assertEqual(action_policy.reason(text), description)

    def test_host_explanation_is_not_an_agent_note(self):
        text = action_policy.summary("Write", {"file_path": "/tmp/note", "host_note": "Only this change is approved"})
        self.assertEqual(action_policy.reason(text), "")
        self.assertIn("Only this change is approved", text)

    def test_quotes_and_line_breaks_stay_checkable(self):
        text = action_policy.summary("Bash", {"command": "ls", "description": 'list "models"\nthen stop'}, lang="en")
        got = action_policy.reason(text)
        self.assertEqual(got, 'list "models"\nthen stop')
        self.assertIn(json.dumps(got, ensure_ascii=False)[1:-1], text)

    def test_look_alike_blocks_are_not_the_note(self):
        # the command's first line is raw text: a fake label there has no JSON note after it; text appended after the
        # digest line (origin, scope) is never read as the note either
        fake = "Agent note:\n{\"description\": \"harmless\"}\nRequest SHA-256: 00"
        text = action_policy.summary("Bash", {"command": "rm -rf ~ # " + fake.replace("\n", " ")}, lang="en")
        self.assertEqual(action_policy.reason(text), "")
        self.assertEqual(action_policy.reason(text + "\n" + fake), "")
        self.assertEqual(action_policy.reason("Agent note:\n{\"description\": \"x\"}"), "")

    def test_elevate_agent_socket_carries_the_signed_reason(self):
        from test_f17 import Flows, answer
        rig = Flows()
        rig.setUp()
        self.addCleanup(rig.tearDown)
        cases = [
            ({**rig.REQ, "cwd": str(rig.d)}, "装 ffmpeg 转码", "why"),
            ({"t": "secret", "name": "SERVICE_KEY", "purpose": "Connect the service", "dest": str(rig.d / "key.txt")},
             "Connect the service", "purpose"),
        ]
        for req, expected, field in cases:
            rig.sent.clear()
            async def script(host, session, phone):
                card = await rig.wait_card()
                self.assertEqual(card.get("reason"), expected)
                self.assertEqual(card["reason"], card[field])
                await host._app(session, answer(phone, card, False))
            result, _, _ = rig.run_flow(req, script)
            self.assertEqual(result["result"], "denied")

    def test_phone_fixture_is_the_host_text(self):
        now = fixture()
        if os.environ.get("P572_WRITE_FIXTURE"):
            FIXTURE.parent.mkdir(parents=True, exist_ok=True)
            FIXTURE.write_text(json.dumps(now, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        self.assertEqual(json.loads(FIXTURE.read_text(encoding="utf-8")), now)
        self.assertEqual(action_policy.reason(now["zh-with"]), REASON["zh"])
        self.assertEqual(action_policy.reason(now["zh-mixed"]), REASON["en"])
        self.assertEqual(action_policy.reason(now["en-none"]), "")


class OnTheCard(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(approval_level, "PARK_GRACE", 0.15)
        p.start()
        self.addCleanup(p.stop)
        self.host.ask_ttl = 2.0

    def test_ask_message_carries_the_reason(self):
        self.ask({"command": "rm -rf build", "description": "清掉旧的构建产物"})   # nobody answers: timeout, the card was sent
        msgs = self.cards()
        self.assertTrue(msgs, self.sent)
        self.assertEqual(msgs[-1].get("reason"), "清掉旧的构建产物")
        self.assertIn("清掉旧的构建产物", msgs[-1]["summary"])

    def test_no_description_sends_empty_reason(self):
        self.ask({"command": "rm -rf build"})
        self.assertEqual(self.cards()[-1].get("reason"), "")

    def test_parked_card_keeps_the_reason(self):
        from agentj.heartbeat import HeartbeatSend
        self.host.sessions.clear()                    # paired, nobody online: the card parks, pushed again on connect
        self.host.agent.cur_send = HeartbeatSend(0, "〔Agent J 系统提示〕heartbeat", "k", "d")   # as test_p570 parks one
        res = self.ask({"command": "git push origin main", "description": "清掉旧的构建产物"})
        self.assertEqual((res["behavior"], self.last()["reason"]), ("deny", "offline"), res)
        parked = [a for a in self.host.asks.values() if a.parked]
        self.assertEqual(len(parked), 1)
        self.assertEqual(parked[0].reason, "清掉旧的构建产物")
        self.assertEqual(self.host._ask_msg(parked[0])["reason"], "清掉旧的构建产物")

    def test_codex_host_context_and_real_reason_are_separate(self):
        from agentj.agent_codex import CodexAgent
        a = CodexAgent(self.host, self.host.agent_cfg, persist=False)
        a._decide = mock.AsyncMock()
        a._bg = asyncio.run
        description = "Inspect the files. " * 30 + "Do not change anything."
        cases = [
            ("item/commandExecution/requestApproval", {"command": "date", "kind": "writeStdin"}, ""),
            ("item/commandExecution/requestApproval", {"command": "date", "networkApprovalContext": {"host": "example.com"}}, ""),
            ("item/fileChange/requestApproval", {"grantRoot": "/tmp"}, ""),
            ("item/commandExecution/requestApproval", {"command": "date", "kind": "writeStdin", "reason": description}, description),
            ("item/fileChange/requestApproval", {"grantRoot": "/tmp", "reason": description}, description),
            ("item/permissions/requestApproval", {"permissions": {}, "reason": description}, description),
        ]
        with mock.patch("agentj.agent_codex.beyond_sandbox", return_value=None):
            for method, params, expected in cases:
                a._on_request({"id": 1, "method": method, "params": params})
                inp = a._decide.call_args.args[2]
                self.assertEqual(action_policy.reason(action_policy.summary("X", inp)), expected, (method, inp))

    def test_elevate_reason_is_an_exact_signed_field_or_empty(self):
        from agentj import elevate
        e = elevate.Elevator(self.host)
        common = {"id": "a", "nonce": "n", "epk": b"e" * 32, "deadline": time.monotonic()+60, "fails": 0}
        cards = [
            ({"kind": "sudo", "cmd": "date", "argv": ["date"], "why": "Check host time", "effect": "", "reason": "Check host time"}, "Check host time"),
            ({"kind": "secret", "name": "SERVICE_KEY", "purpose": "Connect service", "dest": "env:config#SERVICE_KEY", "reason": "Connect service"}, "Connect service"),
            ({"kind": "secret", "name": "SERVICE_KEY", "purpose": "Host setup explanation", "dest": "env:config#SERVICE_KEY"}, ""),
            ({"kind": "login", "term": "r"}, ""),
            ({"kind": "vshare", "dev": "d", "name": "remote", "cred": "c", "paired": 1, "flags": []}, ""),
        ]
        with mock.patch.object(e, "_vault_view", return_value=None):
            for card, expected in cards:
                card = {**common, **card}
                m = e.card_msg(card)
                self.assertEqual(m.get("reason"), expected, card["kind"])
                if expected:
                    self.assertIn(expected, elevate.shown_fields(card))

    def test_peer_gate_explanation_is_not_an_agent_reason(self):
        from agentj.peer_service import PeerService, FriendAsk
        service = NS(clock=lambda: 0, store=NS(groups=lambda: []))
        for tool in ("friend_request", "peer_question"):
            card = FriendAsk("r", tool, "signed summary", "d", 60, "host gate explanation", {"reason": "host gate explanation"}, {})
            msg = PeerService.ask_msg(service, card)
            self.assertEqual(msg.get("reason"), "")

    def test_host_confirmations_send_empty_reason(self):
        from agentj.serve import Ask
        from agentj import pair_card, pass_reset
        for tool, kind in ((pair_card.TOOL, pair_card.PK_KIND), (pass_reset.TOOL, pass_reset.PK_KIND)):
            a = Ask("r" * 32, tool, "signed host confirmation", "d", {}, "i", 0.0, None,
                    pk_if_any=True, pk_kind=kind, require_passkey=True)
            msg = self.host._ask_msg(a)
            self.assertEqual(msg.get("reason"), "", tool)
            self.assertEqual(msg["summary"], a.summary)
            self.assertTrue(msg["passkey_required"])
            self.assertEqual(a.digest, "d")


if __name__ == "__main__":
    unittest.main()
