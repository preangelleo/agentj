"""R-0056 (0.30.31): a Codex native decline tells the model the host's real classification.

Codex's approval responses carry no reason field (app-server 0.167 schema: `{decision}` / `{action}` only), so the model
sees "rejected by user" unless Agent J injects a developer item first. The note must say whether the OWNER refused it or
Agent J policy did (with the class), and it reaches the app-server before the decline. WHICH actions are refused is unchanged.
"""
import _hermetic  # noqa: F401
import asyncio
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

from agentj import refusal_intent
from agentj.agent_codex import CodexAgent


def bare_agent():
    a = CodexAgent.__new__(CodexAgent)
    a.research, a.tid, a.n, a.cfg, a.pending, a.thread = False, "thread-1", 0, {"dir": "/nonexistent"}, {}, {}
    a.perm_source, a.turn_source = "agentj", None
    wire = []
    a._send = lambda obj: wire.append(obj)
    return a, wire


class Note(unittest.TestCase):
    def test_owner_refusal_is_named_owner(self):
        text = refusal_intent.model_note({"behavior": "deny", "reason": "device", "message": "x"}, "x")
        self.assertIn("refused by the owner", text)
        self.assertNotIn("NOT refused by the owner", text)
        legacy = refusal_intent.model_note({"behavior": "deny", "message": "用户在手机上拒绝了这个操作。"}, "用户在手机上拒绝了这个操作。")
        self.assertIn("refused by the owner", legacy)
        from agentj import sysmigrate
        for text in sysmigrate.DECLINED:          # r1 F2: both languages of the device decline are the owner's
            with self.subTest(text=text):
                note = refusal_intent.model_note({"behavior": "deny", "message": text}, text)
                self.assertIn("refused by the owner on a paired device", note)
                self.assertNotIn("NOT refused", note)

    def test_policy_refusal_names_class(self):
        r = {"behavior": "deny", "reason": "off_list_write_external", "message": "清单外的写操作不执行（不是主人拒绝）"}
        text = refusal_intent.model_note(r, r["message"])
        self.assertIn("NOT refused by the owner", text)
        self.assertIn("Agent J policy: off_list_write_external", text)
        self.assertIn(r["message"], text)
        self.assertIn("rejected by user", text)        # names the misleading native wording it corrects
        self.assertIn("not only commands that write", text)   # off_list_write_* also covers reads outside the closed list
        unknown = refusal_intent.model_note({"behavior": "deny", "message": "已拒绝。"}, "已拒绝。")
        self.assertIn("reason unknown; treat as refused, do not retry another way", unknown)
        self.assertNotIn("NOT refused by the owner", unknown)     # r1 F2: unknown never claims who decided


class Decide(unittest.TestCase):
    def test_decide_injects_policy_class_before_decline(self):
        a, wire = bare_agent()
        order = []
        a._answer = lambda rid, res=None, error=None: order.append(("answer", rid, res))
        async def call(method, params, timeout=None):
            order.append(("call", method, params))
        a.call = call
        deny = {"behavior": "deny", "reason": "off_list_write_host_read",
                "message": "这一轮来自「后台完成」，不是主人本人；清单外的写操作不执行，也不发卡片（不是主人拒绝）。"}
        a.host = NS(peers=None, ask=AsyncMock(return_value=deny), agent_notice=Mock(), lang="zh")
        with patch("agentj.approval_jev.low_risk_pass", return_value=False):
            asyncio.run(a._decide(7, "Bash", {"command": "ls"}, {"decision": "accept"}, {"decision": "decline"}, True))
        self.assertEqual([o[0] for o in order], ["call", "answer"])
        self.assertEqual(order[1][1:], (7, {"decision": "decline"}))
        text = order[0][2]["items"][0]["content"][0]["text"]
        self.assertIn("NOT refused by the owner", text)
        self.assertIn("off_list_write_host_read", text)
        self.assertIn(deny["message"], text)
        self.assertIn(a.host.agent_notice.call_args.args[0], text)


class Refuse(unittest.TestCase):
    def test_sandbox_refusal_tells_model_before_decline(self):
        a, wire = bare_agent()
        a._answer = lambda rid, res=None, error=None: wire.append({"id": rid, "result": res})
        a.host = NS(policy_refused=Mock(), lang="zh")
        a.turn_source = {"label": "好友", "label_en": "a friend", "type": "peer", "trust": "external"}
        a._refuse("r1", "Bash", {"command": "curl -s https://example.com"}, {"decision": "decline"}, "Codex 的沙箱不允许联网")
        self.assertEqual([m.get("method") for m in wire], ["thread/inject_items", None])
        self.assertEqual(wire[1], {"id": "r1", "result": {"decision": "decline"}})
        inj = wire[0]
        self.assertEqual(inj["params"]["threadId"], "thread-1")
        text = inj["params"]["items"][0]["content"][0]["text"]
        self.assertIn("NOT refused by the owner", text)
        self.assertIn("Agent J policy: non_owner_turn_sandbox", text)
        self.assertIn("Codex 的沙箱不允许联网", text)
        self.assertIn("id", inj)
        json.dumps(inj)
        a.host.policy_refused.assert_called_once()          # the owner notice is unchanged

    def test_refusal_without_thread_still_declines(self):
        a, wire = bare_agent()
        a.tid = None
        a._answer = Mock()
        a.host = NS(policy_refused=Mock(), lang="zh")
        a._refuse("r2", "Bash", {"command": "curl -s https://example.com"}, {"decision": "decline"}, "Codex 的沙箱不允许联网")
        a._answer.assert_called_once_with("r2", {"decision": "decline"})
        self.assertEqual(wire, [])


class TellModelBestEffort(unittest.TestCase):
    """Codex r1 F3: `_tell_model` does not wait for the ack; a failed write, an error ack or a late ack never changes
    the decline and never breaks the reader."""

    def test_failed_inject_write_still_declines(self):
        a, wire = bare_agent()
        def send(obj):
            if obj.get("method") == "thread/inject_items":
                raise ConnectionError("pipe closed")
            wire.append(obj)
        a._send = send
        a.host = NS(policy_refused=Mock(), lang="zh")
        a._refuse("r3", "Bash", {"command": "curl -s https://example.com"}, {"decision": "decline"}, "Codex 的沙箱不允许联网")
        self.assertEqual(wire, [{"jsonrpc": "2.0", "id": "r3", "result": {"decision": "decline"}}])

    def test_reader_ignores_error_and_late_acks(self):
        a, wire = bare_agent()
        a.host = NS(policy_refused=Mock(), lang="zh", st=Mock())
        a._refuse("r4", "Bash", {"command": "curl -s https://example.com"}, {"decision": "decline"}, "Codex 的沙箱不允许联网")
        inject_id = wire[0]["id"]
        self.assertFalse(hasattr(a, "waits"), "no waiter is registered for the note")
        lines = [json.dumps({"id": inject_id, "error": {"code": -32600, "message": "thread not loaded"}}),
                 json.dumps({"id": inject_id, "result": {}}),                       # a late duplicate ack
                 json.dumps({"id": 99, "method": "item/commandExecution/requestApproval", "params": {}})]

        class Out:
            def __init__(self):
                self.rows = [(x + "\n").encode() for x in lines]
            async def readline(self):
                return self.rows.pop(0) if self.rows else b""

        class Proc:
            stdout, pid, returncode = Out(), 4242, 0
            async def wait(self):
                return 0
        a.waits, a.pending, a.ended, a.proc, a.turn_proc, a.turn_done = {}, {}, set(), None, None, None
        a._on_request, a._busy, a._on_note = Mock(), Mock(), Mock()
        with patch("agentj.codex_procs.forget"):
            asyncio.run(a._read(Proc()))
        a._on_request.assert_called_once()                    # the reader went on past both acks
        self.assertEqual(a._on_request.call_args.args[0]["id"], 99)


if __name__ == "__main__":
    unittest.main()
