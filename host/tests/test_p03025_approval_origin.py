"""P03025: approval cards resolve the owner record by the active host turn."""
import _hermetic  # noqa: F401
import copy
import unittest
from types import SimpleNamespace

from agentj import approval_context, approval_review


class ApprovalOriginByTurn(unittest.TestCase):
    def setUp(self):
        self.host = SimpleNamespace(_approval_origins={}, desktop_turn=1)
        self.origin1 = {"message_id": 1, "root_message_id": 1, "session": "s", "channel": "host",
                        "text": "owner first", "verified": True, "suspicious": False}
        self.origin2 = {"message_id": 2, "root_message_id": 2, "session": "s", "channel": "host",
                        "text": "owner nth", "verified": True, "suspicious": False}
        self.host._approval_origins.update({1: copy.deepcopy(self.origin1), 2: copy.deepcopy(self.origin2)})

    def test_owner_first_call_uses_first_turn_record(self):
        self.host.agent = SimpleNamespace(cur_send=SimpleNamespace(turn=1, source={"kind": "terminal", "role": "owner"}))
        self.assertEqual(approval_review.source(self.host), self.origin1)
        self.assertEqual(approval_context.snapshot(self.host), self.origin1)

    def test_owner_nth_call_uses_nth_turn_not_previous_owner(self):
        self.host.agent = SimpleNamespace(cur_send=SimpleNamespace(turn=2, source={"kind": "terminal", "role": "owner"}))
        self.assertEqual(approval_review.source(self.host), self.origin2)

    def test_system_prompt_between_owner_turns_does_not_inherit_owner(self):
        self.host.agent = SimpleNamespace(cur_send=SimpleNamespace(turn=3, source={"kind": "system", "role": "owner"}))
        self.assertIsNone(approval_review.source(self.host))
        self.assertIsNone(approval_context.snapshot(self.host))

    def test_heartbeat_unknown_does_not_inherit_desktop_owner(self):
        self.host.agent = SimpleNamespace(cur_send=SimpleNamespace(turn=4, source={"kind": "system", "role": "owner"}))
        self.host.desktop_turn = 1
        self.assertIsNone(approval_review.source(self.host))

    def test_verified_owner_channel_direct_origin_is_allowed(self):
        send = SimpleNamespace(turn=99, source={"kind": "telegram", "role": "owner", "sender": 7, "chat": 7},
                               approval_origin=self.origin1)
        self.host.agent = SimpleNamespace(cur_send=send)
        self.assertEqual(approval_review.source(self.host), self.origin1)

    def test_group_forward_guest_and_webhook_direct_origins_are_rejected(self):
        sources = (
            {"kind": "telegram", "role": "group", "sender": 7, "chat": 8},
            {"kind": "telegram", "role": "forward", "sender": 7, "chat": 7},
            {"kind": "email", "role": "group", "sender": "guest@example.com"},
            {"kind": "webhook", "role": "owner"},
            {"kind": "friend", "role": "owner"},
        )
        for source in sources:
            with self.subTest(source=source):
                send = SimpleNamespace(turn=99, source=source, approval_origin=self.origin1)
                self.host.agent = SimpleNamespace(cur_send=send)
                self.assertIsNone(approval_review.source(self.host))

    def test_shared_session_nth_tool_call_joins_exact_phone_turn(self):
        # 0.30.25 report: shared Claude session, phone message, sudo card on a later tool call.
        SharedClaude = type("SharedClaude", (), {})
        agent = SharedClaude()
        agent.cfg, agent.phone_turn, agent.phone_seen = {"session_mode": "shared"}, False, False
        agent.cur_send = SimpleNamespace(turn=2, source={"kind": "phone", "role": "owner"}, approval_origin=None)
        self.host.agent = agent
        self.host.hist = SimpleNamespace(epoch="s", get=lambda turn: None)
        self.assertEqual(approval_review.source(self.host), self.origin2)
        # Another conversation epoch never labels this turn.
        self.host.hist.epoch = "other"
        self.assertIsNone(approval_review.source(self.host))

    def test_shared_owner_channel_waiting_send_does_not_label_native_turn(self):
        # Security r3 F1: a Telegram owner Send waiting for native input must not label
        # whatever native turn is running (desktop / background notice / peer) until delivery is attested.
        SharedClaude = type("SharedClaude", (), {})
        agent = SharedClaude()
        agent.cfg, agent.phone_turn, agent.phone_seen = {"session_mode": "shared"}, True, False
        agent.cur_send = SimpleNamespace(turn=2, source={"kind": "telegram", "role": "owner", "sender": 7, "chat": 7},
                                         approval_origin=None)
        self.host.agent = agent
        self.host.hist = SimpleNamespace(epoch="s", get=lambda turn: None)
        self.assertIsNone(approval_review.source(self.host))
        agent.phone_seen = True       # native delivery attested for this Send
        self.assertEqual(approval_review.source(self.host), self.origin2)

    def test_independent_request_never_borrows_current_owner_turn(self):
        # A CLI callback / worker runs under its own request scope: no host origin -> no owner record.
        from agentj import provenance
        self.host.agent = SimpleNamespace(cur_send=SimpleNamespace(turn=1, source={"kind": "terminal", "role": "owner"},
                                                                   approval_origin=self.origin1))
        with provenance.request_source({"kind": "unknown", "role": "group"}):
            self.assertEqual(approval_review.source(self.host), {})
        with approval_review.origin_scope(self.origin2):
            self.assertEqual(approval_review.source(self.host), self.origin2)

    def test_host_typed_system_send_keeps_its_review_origin(self):
        # Review-event / read-back Sends are host-built with a HostSystem source and the request's own origin.
        from agentj import provenance
        send = SimpleNamespace(turn=77, source=provenance.system(), approval_origin=self.origin1)
        self.host.agent = SimpleNamespace(cur_send=send)
        self.assertEqual(approval_review.source(self.host), self.origin1)
        # A plain wire-shaped system dict is not host-typed and cannot present a verified owner record.
        send.source = {"kind": "system", "role": "owner"}
        self.assertIsNone(approval_review.source(self.host))


if __name__ == "__main__":
    unittest.main()
