"""#0303 R1 (chairman 11-09): requests from the owner's OWN authenticated channels are the owner.

The owner's bound Telegram DM (role owner), the owner's own WeChat (role owner) and the owner's mailbox whose sender
verification passed (email role owner; mail.judge builds email_owner only for an allowlisted owner address with
SPF / DKIM / DMARC) must not be refused as outsiders by the module gates. Groups, forwards, guests / unverified mail,
friend tasks, automation data and webhook system turns keep the old rules, and every such refusal says what to do.
UV-list actions still go to the owner's card (WeChat setup keeps require_passkey).
"""
import _hermetic  # noqa: F401
import asyncio
import pathlib
import unittest
from types import SimpleNamespace as NS
from unittest import mock

from agentj import provenance as P

OWNER_ADDR = "owner@example.com"
OWNER = {
    "telegram": P.telegram_owner(7001),
    "wechat": P.wechat_owner(),
    "mail": P.email_owner(OWNER_ADDR),
}
# outsiders that no gate admits as the owner (delegated tasks are listed separately: some gates send them to a card)
OUTSIDE = {
    "telegram_group": P.telegram_group(-100, 7001, "proxy", 7001),     # even the owner's id, inside a group
    "telegram_forward": P.telegram_forward(7001),
    "guest_mail": P.email_guest("guest@example.com"),
    "unverified_owner_mail": P.email_guest(OWNER_ADDR),                 # owner address, sender check failed
    "automation": P.automation("nightly-report"),
    "webhook_system": "webhook",
    "unknown": None,
}
FRIEND = {"kind": "friend_task", "role": "task"}


class Passed(Exception):
    """Raised by the first call after a gate: the request got past it."""


def agent_for(src):
    if src == "webhook":
        return NS(cur_send=NS(source=P.system(), hook_notice=True))
    return NS(cur_send=NS(source=src))


def run(coro):
    return asyncio.run(coro)


class Helper(unittest.TestCase):
    def test_owner_channels_are_owner_intent(self):
        for via, src in OWNER.items():
            with self.subTest(via=via):
                self.assertTrue(P.owner_intent(src))
                self.assertTrue(P.owner_speaking(agent_for(src)))
                self.assertTrue(P.untrusted_turn(agent_for(src)), "untrusted_turn itself is unchanged")
                self.assertFalse(P.is_owner(src), "is_owner stays phone / terminal (signing code depends on it)")
        for src in (P.phone(), P.terminal()):
            self.assertTrue(P.owner_intent(src))
            self.assertTrue(P.owner_speaking(agent_for(src)))

    def test_outsiders_are_not(self):
        for name, src in {**OUTSIDE, "friend_task": FRIEND}.items():
            with self.subTest(name=name):
                self.assertFalse(P.owner_speaking(agent_for(src)))
                if src != "webhook":
                    self.assertFalse(P.owner_intent(src))

    def test_stricter_request_scope_wins(self):
        a = agent_for(OWNER["telegram"])
        with P.request_source({"kind": "scheduled_task", "role": "task"}):
            self.assertFalse(P.owner_speaking(a))
        with P.request_source(P.wechat_owner()):
            self.assertFalse(P.owner_speaking(a), "another channel's request scope is not this turn")
        with P.request_source(P.phone()):
            self.assertTrue(P.owner_speaking(a))

    def test_strong_identity_only(self):
        self.assertFalse(P.owner_intent({"kind": "telegram", "role": "owner", "sender": 7001, "chat": 7002}),
                         "Telegram owner role outside the owner's own private chat")
        self.assertFalse(P.owner_intent({"kind": "telegram", "role": "owner", "sender": "7001", "chat": "7001"}))
        self.assertFalse(P.owner_intent({"kind": "email", "role": "owner", "sender": "not an address"}))
        self.assertFalse(P.owner_intent({"kind": "same_owner", "role": "task"}))
        self.assertFalse(P.owner_intent(P.system()))
        self.assertFalse(P.owner_intent({"kind": "external_mcp", "role": "group", "connector": "A" * 22}))
        self.assertFalse(P.owner_intent(P.telegram_owner(7001), NS(hook_notice=True)), "a webhook body is never the owner")
        self.assertTrue(P.owner_intent(P.telegram_owner(7001), NS(hook_notice=None)))
        src = P.telegram_owner(7001)
        before = dict(src)
        P.owner_intent(src)
        self.assertEqual(src, before, "pure: the source is not changed")


class Mail(unittest.TestCase):
    """The owner mail source exists only after mail.authenticate passed with an aligned result (mail.owner_source)."""
    CFG = {"allow": [{"addr": OWNER_ADDR, "role": "owner", "name": ""}], "auth": "standard", "auth_servid": "mx.example.org",
           "address": "agent@example.org"}

    def msg(self, results=None, frm=OWNER_ADDR):
        import email
        import email.policy
        head = f"From: {frm}\nTo: agent@example.org\nSubject: hi\nMessage-ID: <m1@example.com>\n"
        if results:
            head += f"Authentication-Results: mx.example.org; {results}\n"
        return email.message_from_string(head + "\nplease do it\n", policy=email.policy.default)

    def source(self, msg, cfg=None):
        from agentj import mail
        v = mail.judge(msg, cfg or self.CFG, [])
        cfg = cfg or self.CFG
        return v, (mail.owner_source(v, False, cfg.get("auth_servid") or "") if v.ok else None)

    def test_verified_owner_mail_is_owner(self):
        for res in ("dmarc=pass header.from=example.com", "dkim=pass header.d=example.com"):
            v, src = self.source(self.msg(res))
            self.assertTrue(v.ok, res)
            self.assertTrue(P.owner_intent(src), res)

    def test_forged_from_stays_refused(self):
        forged = ("",                                                     # From only, no results
                  "dkim=pass header.d=attacker.example",                  # signed, but not by the From domain
                  "spf=pass smtp.mailfrom=attacker.example",              # SPF for another domain
                  "dmarc=fail header.from=example.com dkim=pass header.d=example.com")
        for res in forged:
            v, src = self.source(self.msg(res or None))
            self.assertFalse(v.ok and P.owner_intent(src), res)
        from agentj import mail
        self.assertFalse(P.owner_intent(mail.owner_source(NS(ok=True, role="owner", auth="off", addr=OWNER_ADDR))),
                         "sender check turned off: allowlist only, never the owner")
        self.assertFalse(P.owner_intent(mail.owner_source(NS(ok=True, role="owner", auth="self", addr=OWNER_ADDR))))
        self.assertFalse(P.owner_intent(mail.owner_source(NS(ok=True, role="guest", auth="dkim", addr=OWNER_ADDR))))
        self.assertFalse(P.owner_intent(mail.owner_source(NS(ok=True, role="owner", auth="dkim", addr=OWNER_ADDR), True)),
                         "an automatic notice is data")
        self.assertEqual(set(mail.VERIFIED), {"dmarc", "dkim", "spf"})
        self.assertIn('source = owner_source(v, notice, cfg.get("auth_servid") or "")', pathlib.Path(mail.__file__).read_text(encoding="utf-8"))

    def test_r5_m1_injected_results_without_pinned_server_is_not_owner(self):
        # security r5 M1: provider writes no Authentication-Results; the sender injects one; no pinned server.
        unpinned = {**self.CFG, "auth_servid": ""}
        v, src = self.source(self.msg("dmarc=pass header.from=example.com"), unpinned)
        self.assertFalse(v.ok and P.owner_intent(src))
        from agentj import mail
        self.assertFalse(P.owner_intent(mail.owner_source(NS(ok=True, role="owner", auth="dmarc", addr=OWNER_ADDR))))


class Messages(unittest.TestCase):
    def test_outsider_detail_is_actionable(self):
        d = P.outsider_detail()
        for word in ("Agent J 网页", "Telegram", "微信", "Web App", "WeChat", "did not refuse"):
            self.assertIn(word, d)

    def test_cli_reasons_pass_source_codes(self):
        from agentj import action_policy
        for code in ("untrusted_turn", "untrusted_source", "task_plan", "unverified_plan_producer", "peer_unverified",
                     "source_denied", "session_changed", "owner_phone_required"):
            with self.subTest(code=code), mock.patch("agentj.elevate.client_request",
                                                     return_value={"result": "denied", "reason": code}):
                receipt = {}
                self.assertFalse(action_policy.confirm_cli("broker.execute", {}, receipt=receipt))
                self.assertEqual(receipt["reason"], code)
        with mock.patch("agentj.elevate.client_request", return_value={"result": "denied", "reason": "free text"}):
            receipt = {}
            action_policy.confirm_cli("broker.execute", {}, receipt=receipt)
            self.assertEqual(receipt["reason"], "unknown", "free text still never leaks")


class TaskPlan(unittest.TestCase):
    def svc(self, src):
        from agentj import peer_service
        svc = peer_service.PeerService.__new__(peer_service.PeerService)
        svc.host = NS(cur_turn=3, task_label=None, agent=agent_for(src), lang="zh", agent_notice=lambda *a: None,
                      ask=mock.AsyncMock(return_value={"behavior": "deny"}))
        return svc

    def test_gate(self):
        from agentj import bounded_plan
        acts = [{"tool": "Bash", "input": {"command": "cp a b"}}]
        with mock.patch.object(bounded_plan, "continuation_allowed", lambda h, x: False):
            for via, src in OWNER.items():
                svc = self.svc(src)
                self.assertEqual(run(svc.approve_actions(acts)), {"ok": False, "why": "not_approved"}, via)
                svc.host.ask.assert_awaited()          # reached the owner's card
            for name, src in {**OUTSIDE, "friend_task": FRIEND}.items():
                svc = self.svc(src)
                res = run(svc.approve_actions(acts))
                self.assertEqual(res["why"], "task_plan", name)
                self.assertTrue(res["detail"], name)
                svc.host.ask.assert_not_awaited()


class Google(unittest.TestCase):
    def test_gate(self):
        from agentj import google_api
        for via, src in OWNER.items():
            self.assertIsNone(google_api.refusal(agent_for(src)), via)
        for name, src in OUTSIDE.items():
            res = google_api.refusal(agent_for(src))
            self.assertEqual(res["why"], "group_source", name)
            self.assertIn("Web App", res["detail"])


class Support(unittest.TestCase):
    def test_gate(self):
        from agentj import elevate
        for via, src in OWNER.items():
            host = NS(agent=agent_for(src), preferences={}, st=None)
            self.assertFalse(elevate.external_support(host, "support"), via)
            self.assertFalse(elevate.external_support(host, "feedback_send"), via)
        for name, src in {**OUTSIDE, "friend_task": FRIEND}.items():
            host = NS(agent=agent_for(src), preferences={}, st=None)
            self.assertTrue(elevate.external_support(host, "support"), name)
        self.assertIn("provenance.outsider_detail()", pathlib.Path(elevate.__file__).read_text(encoding="utf-8"))


class Dispatch(unittest.TestCase):
    def caps(self, src):
        from agentj import capability
        c = capability.Capabilities.__new__(capability.Capabilities)
        c.host = NS(stopped=lambda: False, preferences={}, task_label=None, agent=agent_for(src),
                    scheduler=NS(current_id=None, waiting=[]), st=NS(log=lambda *a, **k: None))
        c.adapters = None
        c.cfg = lambda: {}
        return c

    def test_gate(self):
        from agentj import capability
        with mock.patch.object(capability, "plan_card", side_effect=Passed):
            for via, src in OWNER.items():
                with self.assertRaises(Passed, msg=via):
                    run(self.caps(src).dispatch("wf-one"))
            for name, src in {**OUTSIDE, "friend_task": FRIEND}.items():
                with self.assertRaises(capability.CapError, msg=name) as e:
                    run(self.caps(src).dispatch("wf-one"))
                self.assertIn("Web App", e.exception.detail)


class Heartbeat(unittest.TestCase):
    def hb(self, src):
        from agentj import heartbeat
        h = heartbeat.Heartbeat.__new__(heartbeat.Heartbeat)
        h.host = NS(agent=agent_for(src), preferences={}, st=NS(log=lambda *a, **k: None),
                    set_pref=mock.AsyncMock(return_value={"ok": True}))
        return h

    def test_gate(self):
        for via, src in OWNER.items():
            h = self.hb(src)
            self.assertEqual(run(h.handle({"op": "off"})), {"ok": True}, via)
            h.host.set_pref.assert_awaited_with("heartbeat.enabled", False)
        for name, src in OUTSIDE.items():
            h = self.hb(src)
            res = run(h.handle({"op": "off"}))
            self.assertEqual(res["why"], "untrusted_turn", name)
            self.assertIn("Web App", res["detail"])
            h.host.set_pref.assert_not_awaited()


class IMessage(unittest.TestCase):
    def im(self, src):
        from agentj import imessage
        i = imessage.IMessage.__new__(imessage.IMessage)
        i.host = NS(agent=agent_for(src), preferences={}, st=NS(log=lambda *a, **k: None))
        return i

    def test_gate(self):
        for via, src in OWNER.items():
            self.assertEqual(run(self.im(src).handle({"op": "nothing"}))["why"], "unknown_op", via)
        for name, src in OUTSIDE.items():
            res = run(self.im(src).handle({"op": "nothing"}))
            self.assertEqual(res["why"], "untrusted_turn", name)
            self.assertTrue(res["detail"])


class Contacts(unittest.TestCase):
    def svc(self, src):
        from agentj import contacts_serve
        s = contacts_serve.ContactService.__new__(contacts_serve.ContactService)
        s.host = NS(agent=agent_for(src), preferences={}, stopped=lambda: False)
        s.st = NS(log=lambda *a, **k: None)
        s.mail_state = lambda: ({}, None, None)
        s.resolve = mock.Mock(side_effect=Passed)
        return s

    def test_mail_send(self):
        req = {"to": ["someone@example.com"], "subject": "hi", "body": "hello"}
        for via, src in OWNER.items():
            with self.assertRaises(Passed, msg=via):
                run(self.svc(src).agent_send(req))
        for name, src in OUTSIDE.items():
            res = run(self.svc(src).agent_send(req))
            self.assertEqual(res["why"], "group_source", name)
            self.assertTrue(res["detail"], name)

    def test_card(self):
        from agentj import contacts_serve
        with mock.patch.object(contacts_serve.C, "read", side_effect=Passed):
            for via, src in OWNER.items():
                with self.assertRaises(Passed, msg=via):
                    run(self.svc(src).card({"owner": True}))
            for name, src in OUTSIDE.items():
                res = run(self.svc(src).card({"owner": True}))
                self.assertEqual(res["result"], "group_source", name)
                self.assertTrue(res["detail"])


class CoreHostside(unittest.TestCase):
    def test_gate(self):
        from agentj.core import hostside
        with mock.patch.object(hostside, "enabled", return_value=True):
            for via, src in OWNER.items():
                self.assertIsNone(hostside._blocked(NS(agent=agent_for(src), stopped=lambda: False)), via)
            for name, src in OUTSIDE.items():
                res = hostside._blocked(NS(agent=agent_for(src), stopped=lambda: False))
                self.assertEqual(res["result"], "group_source", name)
                self.assertTrue(res["detail"])


class WeChat(unittest.TestCase):
    def wc(self, src):
        from agentj import wechat
        w = wechat.WeChat.__new__(wechat.WeChat)
        w.h = NS(agent=agent_for(src), external_channels=None, lang="zh", stopped=lambda: False)
        w.store = NS(data={"records": {}})
        w.clock = lambda: 0
        w.closed = False
        w.handoff_lock = asyncio.Lock()
        w.approval = mock.AsyncMock(return_value=False)
        w.discard_approval = lambda r: None
        return w

    def test_pending_claim_setup_unbind(self):
        async def go(src, req):
            return await self.wc(src).agent_request(req)
        for via, src in OWNER.items():
            self.assertEqual(run(go(src, {"op": "pending"})), {"ok": True, "items": []}, via)
            self.assertEqual(run(go(src, {"op": "claim", "id": "x"}))["why"], "not_pending", via)
            for op in ("setup", "unbind"):
                self.assertEqual(run(go(src, {"op": op}))["why"], "denied", (via, op))     # reached the owner's card
        for name, src in {**OUTSIDE, "friend_task": FRIEND}.items():
            for op, why in (("pending", "owner_phone_required"), ("claim", "owner_phone_required"),
                            ("setup", "owner_required"), ("unbind", "owner_required")):
                res = run(go(src, {"op": op, "id": "x"}))
                self.assertEqual(res["why"], why, (name, op))
                self.assertTrue(res["detail"])

    def test_terminal_owner_can_list_pending(self):
        w = self.wc(None)
        w.h.agent = NS(cur_send=None, native_source=P.terminal())
        self.assertEqual(run(w.agent_request({"op": "pending"})), {"ok": True, "items": []})

    def test_setup_card_still_requires_passkey(self):
        from agentj import wechat
        self.assertIn("require_passkey=target == {'op':'setup'}", pathlib.Path(wechat.__file__).read_text(encoding="utf-8"))


class CapabilityPause(unittest.TestCase):
    def test_gate(self):
        from agentj import capability_pause as CP, self_compact
        with mock.patch.object(self_compact, "roots", return_value={10}), \
                mock.patch.object(CP.shared_hook, "ancestors", return_value=[10]):
            for via, src in OWNER.items():
                a = agent_for(src)
                a.cfg = {"dir": None}
                host = NS(agent=a, stopped=lambda: False, task_label=None)
                self.assertEqual(CP.handle(host, {"t": "capability_pause"}, 99)["why"], "workdir", via)
            for name, src in {**OUTSIDE, "friend_task": FRIEND}.items():
                a = agent_for(src)
                a.cfg = {"dir": None}
                host = NS(agent=a, stopped=lambda: False, task_label=None)
                res = CP.handle(host, {"t": "capability_pause"}, 99)
                self.assertEqual(res["why"], "source", name)
                self.assertTrue(res["detail"])


if __name__ == "__main__":
    unittest.main()
