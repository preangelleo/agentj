"""2026-11-02 story-channel: a background caller (no turn) is refused at the source gate with the real source kind and
the remedy; group / guest / forwarded turns stay refused before SMTP; a scheduled-task turn reaches the card, never no-card."""
import _hermetic  # noqa: F401,I001
import pathlib
import sys
import unittest
from types import SimpleNamespace

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import provenance as p  # noqa: E402
from test_p194_owner_direct import _Svc  # noqa: E402


class Source(_Svc):
    def turn(self, src):
        self.host.agent = SimpleNamespace(kind="codex", native_source=src)

    async def refused(self, src):
        self.turn(src)
        res = await self.svc.agent_send({"to_owner": True, "subject": "s", "body": "b"})
        self.assertFalse(res["ok"], res)
        self.assertEqual(res["why"], "group_source")
        self.ask.assert_not_awaited()
        self.assertEqual(self.smtp.sent, [])
        return res

    async def test_background_caller_gets_unknown_and_remedy(self):
        res = await self.refused(None)
        self.assertEqual(res["source"], "unknown")
        self.assertIn("agentj tasks enable", res["detail"])

    async def test_negative_controls_stay_refused(self):
        for src in ({"kind": "telegram", "role": "group", "sender": 5, "chat": -9},
                    {"kind": "email", "role": "group", "sender": "guest@x.example"},
                    {"kind": "telegram", "role": "forward", "sender": 5, "chat": 5},
                    {"kind": "automation", "role": "data", "name": "story"},
                    {"kind": "owner_session", "role": "owner"}):          # forged kind → unknown
            res = await self.refused(src)
            if res["source"] != "unknown":      # #0303: every refusal says what to do (ask from the owner's own chat)
                self.assertNotIn("agentj tasks enable", res["detail"])
                self.assertIn("Web App", res["detail"])

    async def test_scheduled_task_reaches_the_card_not_no_card(self):
        self.turn(None)
        with p.task_run():
            res = await self.svc.agent_send({"to_owner": True, "subject": "s", "body": "b"})
        self.assertTrue(res["ok"], res)
        self.ask.assert_awaited_once()
        self.assertFalse(res["no_card"] or res["owner_direct"])


if __name__ == "__main__":
    unittest.main()
