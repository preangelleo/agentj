"""0304-frozen A3: automation outbound-send grant (THREAT-MODEL §A3, chairman 2026-11-09 19:30).

One owner fingerprint card → a grant bound to one webhook (id + auth/IP/target digest) or one scheduled task (id + tsha)
letting that automation's turns mail listed recipients / domains with no card; anything else refused with the fix.
Real Ed25519 + WebAuthn proofs (test_p57 Authenticator), local fake SMTP, synthetic @example.com addresses only.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import contextlib
import io
import json
import os
import time
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric import ed25519

from agentj import automation_grant as AG, approvals, contacts as C, hooks, passkey, provenance, wire
from agentj import workflow_mail as wm
from test_p194_owner_direct import _Svc
from test_p57_passkey import Authenticator, pk_record

OWNER = "owner@example.com"
ALLY = "ally@example.com"
OTHER = "other@example.com"


class FakeStore:
    def __init__(self, *hs):
        self.hs = {h["id"]: h for h in hs}

    def get(self, hid):
        return self.hs.get(hid)

    def find(self, ref):
        return self.hs.get(ref) or next((h for h in self.hs.values() if h["name"] == ref), None)


class Base(_Svc):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        C.local(self.st, {"op": "owner_set", "owner_ok": True, "contact": {"name": "Owner", "emails": [OWNER]}})
        self.auth = Authenticator()
        self.ed = ed25519.Ed25519PrivateKey.generate()
        self.st.sign_key = lambda d: self.ed.public_key().public_bytes_raw() if d == "dev1" else None
        self.st.passkey_of_device = lambda d: pk_record(self.auth) if d == "dev1" else None
        self.hook = hooks.build({"preset": "hmac", "name": "orders", "target": "main"}, "main", time.time())
        self.hook2 = hooks.build({"preset": "hmac", "name": "other-hook", "target": "main"}, "main", time.time())
        for n, h in enumerate((self.hook, self.hook2)):
            h.update(state="active", life="resident", expires=None)
            h["id"] = ("_" if n == 0 else "-") + h["id"][1:]      # token_urlsafe ids may start with _ or -
        self.host.hooks = SimpleNamespace(store=FakeStore(self.hook, self.hook2))
        self.owner_agent = self.host.agent
        self.task = {"id": "digest", "dir": str(self.work), "tsha": "a" * 64, "problems": []}
        self.host.scheduler = SimpleNamespace(workdir=lambda: str(self.work))
        def find(wd, tid):
            from agentj import tasks
            if tid != "digest":
                raise tasks.TaskError("unknown")
            return self.task
        self.find = patch("agentj.tasks.find", side_effect=find)
        self.find.start(); self.addCleanup(self.find.stop)
        self.caller = patch.object(AG, "hook_caller", return_value=True)
        self.caller.start(); self.addCleanup(self.caller.stop)
        self.ask.side_effect = self.sign
        self.cards = []

    async def sign(self, tool, inp, **kw):
        if tool != AG.TOOL:                      # an ordinary mail card (owner turn): allow without UV
            return {"behavior": "allow"}
        self.assertTrue(kw["always_ask"] and kw["require_passkey"] and kw["grant_info"])
        self.cards.append(inp["summary"])
        summary, channel, rid = inp["summary"], self.st.config()["channel"], "request-id"
        d = approvals.shown_digest(tool, summary)
        fa = self.auth.assert_(passkey.elevate_challenge(channel, "dev1", rid, "sandbox", rid, d), b"", nonce=b"")
        return {"behavior": "allow", "grant_info": {"device": "dev1", "rid": rid, "channel": channel, "summary": summary,
                "kind": "sandbox", "sign_pub": self.ed.public_key().public_bytes_raw(),
                "sig": wire.b64u(self.ed.sign(approvals.signed_message(channel, "dev1", rid, "allow", d))),
                "fa": {k: fa[k] for k in ("id", "cd", "ad", "sig")}}}

    def hook_turn(self, h=None):
        self.host.agent = SimpleNamespace(kind="claude", cur_send=SimpleNamespace(
            source=provenance.system(), hook_notice=(h or self.hook)["id"], by="hook"))

    def owner_turn(self):
        self.host.agent = self.owner_agent

    async def grant(self, **kw):
        req = {"kind": "hook", "ref": "orders", "to": [ALLY], "domains": [], **kw}
        with patch.object(AG, "owner_turn", return_value=True):
            res = await AG.issue(self.host, req, None)
        self.assertTrue(res["ok"], res)
        return res

    async def send(self, to, **kw):
        return await self.svc.agent_send({"to": to, "subject": "Order shipped", "body": "Your order shipped.", **kw}, 4242)

    def ledger(self):
        return json.loads((self.st.root / AG.FILE).read_text())


class Grants(Base):
    async def test_hook_grant_sends_in_scope_without_card(self):
        g = await self.grant()
        self.assertEqual(len(self.cards), 1)
        card = self.cards[0]
        row = self.ledger()["grants"][g["grant"]]
        self.assertEqual(card, AG.shown(row["core"]))
        # QA scene 3: zh labels (the id and the digest stay on the signed text)
        self.assertTrue(card.endswith("校验摘要 SHA256：" + wm.digest(row["core"])))
        self.assertIn("授权编号：" + g["grant"], card)
        self.assertNotIn("Grant:", card)
        self.assertIn(ALLY, card)
        self.assertEqual(oct((self.st.root / AG.FILE).stat().st_mode & 0o777), "0o600")
        self.hook_turn()
        self.ask.reset_mock()
        res = await self.send([ALLY])
        self.assertTrue(res["ok"] and res["no_card"], res)
        self.assertEqual(res["automation_grant"], g["grant"])
        self.ask.assert_not_awaited()
        self.assertEqual(self.smtp.sent[-1][1], [ALLY])
        self.assertEqual(self.ledger()["grants"][g["grant"]]["uses"], 1)

    async def test_card_text_survives_the_approval_display_projection(self):
        from agentj import action_policy, approval_review
        await self.grant(domains=["example.org"], expires="30d", max_uses=5)
        norm = action_policy.display_normalizer(lang="zh", workdir=str(self.work))
        self.assertEqual(approval_review.display_text(self.cards[0], norm), self.cards[0])

    async def test_tampered_grant_file_refused(self):
        g = await self.grant()
        for mutate in ("add_recipient", "proof"):
            with self.subTest(mutate=mutate):
                d = self.ledger()
                row = d["grants"][g["grant"]]
                if mutate == "add_recipient":
                    row["core"]["to"].append(OTHER)
                else:
                    row["proof"]["sig"] = wire.b64u(b"\0" * 64)
                (self.st.root / AG.FILE).write_text(json.dumps(d))
                self.hook_turn()
                res = await self.send([OTHER] if mutate == "add_recipient" else [ALLY])
                self.assertEqual(res["why"], "automation_grant_invalid", res)
                self.assertEqual(self.smtp.sent, [])
                await self.grant()     # fresh signed grant for the next case
                g = {"grant": AG.live_for(AG.load(self.st), "hook", self.hook["id"])[0]}

    async def test_hook_turn_cannot_request_grant(self):
        self.hook_turn()
        res = await AG.issue(self.host, {"kind": "hook", "ref": "orders", "to": [OTHER]}, None)   # real owner_turn
        self.assertEqual(res["why"], "automation_grant_owner_turn")
        self.assertIn("主人", res["detail"])
        self.assertEqual(self.cards, [])
        self.ask.assert_not_awaited()
        for src in ({"kind": "email", "role": "group", "sender": OTHER}, {"kind": "friend_task", "role": "task"},
                    {"kind": "unknown", "role": "group"}):
            self.host.agent = SimpleNamespace(kind="claude", cur_send=SimpleNamespace(source=src))
            self.assertFalse(AG.owner_turn(self.host, None), src)
        with patch.object(wm, "source", return_value={"workflow_id": "digest", "root": "/x", "tsha": "a" * 64}):
            self.owner_turn()
            self.assertFalse(AG.owner_turn(self.host, 4242))   # a task process, even during an owner turn

    async def test_out_of_scope_recipient_refused(self):
        await self.grant()
        self.hook_turn()
        res = await self.send([OTHER])
        self.assertEqual(res["why"], "automation_grant_required", res)
        self.assertEqual(res["missing"], [OTHER])
        self.assertIn(f"agentj automation grant --hook={self.hook['id']} --to ally@example.com --to other@example.com",
                      res["detail"])
        self.assertEqual(self.smtp.sent, [])
        self.ask.assert_awaited_once()            # the grant card only, no mail card

    async def test_mixed_recipients_refused(self):
        await self.grant()
        self.hook_turn()
        for kw in ({"to": [ALLY, OTHER]}, {"to": [ALLY], "cc": [OTHER]}, {"to": [ALLY], "bcc": [OTHER]}):
            with self.subTest(**{k: str(v) for k, v in kw.items()}):
                to = kw.pop("to")
                res = await self.send(to, **kw)
                self.assertEqual(res["why"], "automation_grant_required", res)
                self.assertEqual(res["missing"], [OTHER])
        self.assertEqual(self.smtp.sent, [])

    async def test_domain_suffix_confusion(self):
        await self.grant(to=[ALLY], domains=["@Example.ORG."])
        self.hook_turn()
        for addr, ok in (("news@example.org", True), ("a@mail.example.org", True), ("a@evil-example.org", False),
                         ("a@example.org.evil.example.com", False), ("a@xexample.org", False),
                         ("ally+tag@example.com", False), ("ALLY@EXAMPLE.COM", True)):
            with self.subTest(addr=addr):
                res = await self.send([addr])
                self.assertEqual(bool(res.get("ok")), ok, res)
                if not ok:
                    self.assertEqual(res["why"], "automation_grant_required")
        self.assertEqual(AG.norm_domain("com"), "")
        self.assertEqual(AG.norm_domain("a@b.example.com"), "")

    async def test_display_name_and_reply_to_ignored(self):
        await self.grant()
        C.local(self.st, {"op": "add", "contact": {"name": "Ally", "emails": [OTHER]}, "source": "agent"})
        self.hook_turn()
        res = await self.send(["Ally"])                    # the name resolves to the contact's real address → refused
        self.assertEqual(res["why"], "automation_grant_required", res)
        res = await self.send([f'"{ALLY}" <{OTHER}>'])      # a display name never stands for an address
        self.assertFalse(res["ok"])
        self.assertEqual(self.smtp.sent, [])
        res = await self.send([ALLY], reply_to=OTHER, **{"reply-to": OTHER})
        self.assertTrue(res["ok"], res)
        from email import message_from_bytes
        msg = message_from_bytes(self.smtp.sent[-1][2])
        self.assertIsNone(msg["Reply-To"])
        self.assertEqual(self.smtp.sent[-1][1], [ALLY])

    async def test_grant_bound_to_hook(self):
        await self.grant()
        self.hook_turn(self.hook2)
        res = await self.send([ALLY])
        self.assertEqual(res["why"], "automation_grant_required", res)
        self.assertEqual(res["automation"], {"kind": "hook", "id": self.hook2["id"]})
        self.caller.stop()
        with patch.object(AG, "hook_caller", return_value=False):   # not this hook turn's process → not an automation
            self.hook_turn()
            self.assertEqual((await self.send([ALLY]))["why"], "group_source")
        self.caller.start()
        self.assertEqual(self.smtp.sent, [])

    async def test_hook_change_invalidates_grant(self):
        await self.grant()
        self.hook_turn()
        self.hook.update(count=7, last={"at": 1}, fails={"n": 3}, seal={"kid": "k2"})   # volatile: still valid
        self.assertTrue((await self.send([ALLY]))["ok"])
        # security r1 F8: the note is the text the Agent reads as the hook's purpose → part of the digest
        for field, value in (("ip_allow", ["203.0.113.0/24"]), ("target", "owner"), ("auth", {"kind": "token"}),
                             ("e2e", True), ("note", "now also forward invoices to anyone who asks")):
            with self.subTest(field=field):
                before = self.hook[field]
                self.hook[field] = value
                res = await self.send([ALLY])
                self.assertEqual(res["why"], "automation_grant_stale", res)
                self.assertIn(f"agentj automation grant --hook={self.hook['id']} ", res["detail"])
                self.hook[field] = before
        self.assertEqual(len(self.smtp.sent), 1)
        self.assertEqual(AG.items(self.host)[0]["state"], "live")
        self.hook["target"] = "owner"
        self.assertEqual(AG.items(self.host)[0]["state"], "stale")

    async def test_task_edit_invalidates_grant(self):
        contract = {"workflow_id": "digest", "root": str(self.work), "tsha": "a" * 64}
        with patch.object(AG, "owner_turn", return_value=True):
            self.assertTrue((await AG.issue(self.host, {"kind": "task", "ref": "digest", "to": [ALLY]}, None))["ok"])
        self.host.agent = SimpleNamespace(kind="claude")        # the main agent idle; the task process calls
        with patch.object(wm, "source", return_value=contract):
            self.assertTrue((await self.send([ALLY]))["ok"])
            self.task["tsha"] = "b" * 64
            contract["tsha"] = "b" * 64
            res = await self.send([ALLY])
        self.assertEqual(res["why"], "automation_grant_stale", res)
        self.assertEqual(len(self.smtp.sent), 1)

    async def test_expired_count_revoked(self):
        g = await self.grant(max_uses=1, expires="1d")
        self.hook_turn()
        self.assertTrue((await self.send([ALLY]))["ok"])
        res = await self.send([ALLY])
        self.assertEqual((res["why"], res.get("grant_state")), ("automation_grant_required", "used_up"))
        await self.grant(expires="1d")
        real = time.time
        with patch.object(AG.time, "time", lambda: real() + 2 * 86400):
            res = await self.send([ALLY])
        self.assertEqual((res["why"], res.get("grant_state")), ("automation_grant_required", "expired"))
        gid = AG.live_for(AG.load(self.st), "hook", self.hook["id"])[0]
        s = SimpleNamespace(device="dev1")
        self.host._post = lambda fn, mk: None
        self.host._send_p33 = lambda *a: None
        self.host._dname = lambda d: d
        await AG.on_phone(self.host, s, "automation_grant_revoke", {"id": gid})
        res = await self.send([ALLY])
        self.assertEqual(res["why"], "automation_grant_required")
        self.assertEqual(AG.items(self.host)[0]["state"], "revoked")
        self.assertEqual(len(self.smtp.sent), 1)
        self.assertNotEqual(g["grant"], gid)

    async def test_revoke_between_check_and_smtp_sends_nothing(self):
        await self.grant()
        self.hook_turn()
        real = AG.check
        def check_then_revoke(*a, **kw):
            out = real(*a, **kw)
            AG.revoke(self.st, out.get("grant"), "dev1")
            return out
        with patch.object(AG, "check", side_effect=check_then_revoke):
            res = await self.send([ALLY])
        self.assertFalse(res["ok"], res)
        self.assertEqual(self.smtp.sent, [])

    async def test_owner_turn_unaffected(self):
        await self.grant()
        self.owner_turn()
        self.ask.reset_mock()
        res = await self.send([OTHER])                   # owner turn: the ordinary mail card path, no grant involved
        self.assertTrue(res["ok"], res)
        self.assertNotIn("automation_grant", res)
        self.assertEqual(self.ask.await_args.args[0], "agentj mail send")
        self.assertEqual(self.ledger()["grants"][AG.live_for(AG.load(self.st), "hook", self.hook["id"])[0]]["uses"], 0)
        self.hook_turn()                                 # a hook turn without grant cover is NOT carded through
        self.ask.reset_mock()
        self.assertEqual((await self.send([OTHER]))["why"], "automation_grant_required")
        self.ask.assert_not_awaited()
        # 0.30.6 order step 1 (0304-small): an all-owner list in a hook turn passes with no card and no grant use
        self.ask.reset_mock()
        res = await self.send([OWNER])
        self.assertTrue(res["ok"] and res["owner_direct"], res)
        self.assertNotIn("automation_grant", res)
        self.ask.assert_not_awaited()

    async def test_owner_plus_ungranted_third_party_refused(self):
        await self.grant()
        self.hook_turn()
        self.ask.reset_mock()
        for kw in ({"to": [OWNER, OTHER]}, {"to": [OWNER], "cc": [OTHER]}, {"to": [OWNER, ALLY], "bcc": [OTHER]}):
            with self.subTest(**{k: str(v) for k, v in kw.items()}):
                to = kw.pop("to")
                res = await self.send(to, **kw)
                self.assertEqual(res["why"], "automation_grant_required", res)
                self.assertEqual(res["missing"], [OTHER])     # the owner address needs no cover; only OTHER is missing
        self.assertEqual(self.smtp.sent, [])
        self.ask.assert_not_awaited()

    async def test_owner_only_without_any_grant_passes_but_third_party_refused(self):
        self.hook_turn()                                 # no grant at all: step 1 still passes, step 2 finds no cover
        self.assertTrue((await self.send([OWNER]))["ok"])
        self.assertEqual((await self.send([OWNER, OTHER]))["why"], "automation_grant_required")

    async def test_owner_plus_granted_third_party_allowed(self):
        await self.grant()
        self.hook_turn()
        res = await self.send([OWNER, ALLY])
        self.assertTrue(res["ok"], res)
        self.assertEqual(sorted(self.smtp.sent[-1][1]), sorted([OWNER, ALLY]))
        self.assertFalse(res["owner_direct"])            # mixed list: outward masking stays on

    async def test_settings_list_contract_and_revoke(self):
        g = await self.grant(domains=["example.org"], max_uses=3)
        sent, posted = [], []
        async def send_app(s, obj):
            sent.append(obj)
        self.host.send_app = send_app
        self.host._post = lambda fn, mk: posted.append(mk(None))
        self.host._send_p33 = lambda *a: None
        self.host._dname = lambda d: d
        s = SimpleNamespace(device="dev1")
        await AG.on_phone(self.host, s, "automation_grants", {})
        msg = sent[-1]
        self.assertEqual(msg["t"], "automation_grants")
        item = msg["items"][0]
        self.assertEqual(set(item), {"id", "kind", "target", "actions", "to", "domains", "expires", "permanent", "max_uses",
                                     "uses", "created", "state"})
        self.assertIs(item["permanent"], True)
        self.assertEqual((item["id"], item["kind"], item["target"], item["actions"], item["to"], item["domains"],
                          item["expires"], item["max_uses"], item["uses"], item["state"]),
                         (g["grant"], "hook", "orders", ["mail"], [ALLY], ["example.org"], None, 3, 0, "live"))
        await AG.on_phone(self.host, SimpleNamespace(device="stranger"), "automation_grant_revoke", {"id": g["grant"]})
        self.assertEqual(AG.items(self.host)[0]["state"], "live")     # an unpaired device: ignored
        await AG.on_phone(self.host, s, "automation_grant_revoke", {"id": g["grant"]})
        self.assertEqual(posted[-1]["t"], "automation_grants")
        self.assertEqual(posted[-1]["items"][0]["state"], "revoked")
        log = (self.st.root / "host.log").read_text() if (self.st.root / "host.log").exists() else ""
        self.assertNotIn(ALLY, log)

    async def test_unchanged_regrant_has_no_second_card(self):
        await self.grant()
        res = await self.grant()
        self.assertTrue(res.get("unchanged"), res)
        self.assertEqual(len(self.cards), 1)
        await self.grant(to=[ALLY, OTHER])                 # widening: a new card, the old grant replaced
        self.assertEqual(len(self.cards), 2)
        self.assertEqual(len(self.ledger()["grants"]), 1)

    async def test_denied_card_stores_nothing(self):
        async def deny(tool, inp, **kw):
            return {"behavior": "deny", "message": "no"}
        self.ask.side_effect = deny
        with patch.object(AG, "owner_turn", return_value=True):
            res = await AG.issue(self.host, {"kind": "hook", "ref": "orders", "to": [ALLY]}, None)
        self.assertEqual(res["why"], "denied")
        self.assertFalse((self.st.root / AG.FILE).exists())


class Plumbing(Base):
    def test_listed_tool_registrations(self):
        from agentj import uv_list, policy_registry
        self.assertTrue(uv_list.required(AG.TOOL, {"summary": "x"}))
        self.assertIn(AG.TOOL, policy_registry.HOST_TOOLS)
        from pathlib import Path
        self.assertIn("'agentj automation grant'", (Path(AG.__file__).parent / "list_approval.py").read_text(encoding="utf-8"))

    def test_refusal_codes_reviewed(self):
        allow = json.loads((__import__("pathlib").Path(__file__).with_name("refusal_allowlist.json")).read_text())["codes"]
        self.assertEqual(allow["automation_grant_owner_turn"]["category"], "anti_injection")
        # the scope refusals are not source-type names (no scanner word), so they are documented, not listed
        import test_0303_refusal_allowlist as gate
        for code in ("automation_grant_required", "automation_grant_stale", "automation_grant_invalid"):
            self.assertIsNone(gate.SOURCE_WORDS.search(code), code)

    def test_hook_caller_needs_a_process_of_this_turn(self):
        self.caller.stop()
        self.addCleanup(self.caller.start)
        from agentj.core import process_evidence as pe
        rows = {100: (1, 10.0, "t"), 200: (100, 50.0, "t"), 300: (200, 60.0, "t"), 400: (100, 20.0, "t")}
        self.host.agent = SimpleNamespace(session={"pid": 100}, cur_send=None)
        with patch.object(pe, "row", side_effect=lambda pid: rows.get(pid)):
            self.host._nonowner_spans = [[40.0, None]]
            self.assertTrue(AG.hook_caller(self.host, 300))
            self.assertFalse(AG.hook_caller(self.host, 400))        # born before this hook turn's span
            self.assertFalse(AG.hook_caller(self.host, 100))        # the session itself
            self.assertFalse(AG.hook_caller(self.host, 999))        # not under the session
            self.host._nonowner_spans = [[40.0, 70.0]]
            self.assertFalse(AG.hook_caller(self.host, 300))        # no open non-owner span
            self.host._nonowner_spans = []
            self.assertFalse(AG.hook_caller(self.host, 300))

    def test_cli_parser_and_dispatch(self):
        from agentj import cli
        out = io.StringIO()
        for argv in (["automation", "grant", "--help"], ["automation", "grants", "--help"],
                     ["automation", "revoke", "--help"], ["automation", "status", "--help"]):
            with self.subTest(argv=argv), contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as e:
                cli.main(argv)
            self.assertEqual(e.exception.code, 0)
        seen = []
        def fake(st, req, timeout=None, **kw):
            seen.append(req)
            return {"ok": True, "items": []}
        with patch("agentj.elevate.client_request", side_effect=fake), contextlib.redirect_stdout(out):
            cli.main(["automation", "grant", "--hook", "orders", "--to", ALLY, "--domain", "example.org",
                      "--expires", "30d", "--max", "5", "--json"])
            cli.main(["automation", "grants"])
            cli.main(["automation", "revoke", "a" * 32])
        self.assertEqual(seen[0], {"t": "automation_grant", "op": "grant", "kind": "hook", "ref": "orders", "to": [ALLY],
                                   "domains": ["example.org"], "expires": "30d", "max_uses": 5, "files": False})
        self.assertEqual([r["op"] for r in seen], ["grant", "list", "revoke"])

    def test_root_parser_has_no_duplicate_subcommand(self):
        import re
        from pathlib import Path
        pkg = Path(AG.__file__).parent
        names = {}
        for path in pkg.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r'def add_parser\(sub\)[^\n]*\n(?:.*\n){0,3}?\s*\w+ = sub\.add_parser\("([a-z-]+)"', text):
                names.setdefault(m.group(1), []).append(path.name)
        self.assertEqual({k: v for k, v in names.items() if len(v) > 1}, {})
        self.assertEqual(names.get("automation"), ["wfrun.py"])

    async def test_parse_and_shape(self):
        self.assertEqual(AG.parse_expires("30d"), 30 * 86400)
        self.assertEqual(AG.parse_expires("12h"), 12 * 3600)
        self.assertIsNone(AG.parse_expires(None))
        for bad in ("0d", "999d", "x", "-1"):
            with self.assertRaises(ValueError):
                AG.parse_expires(bad)
        with patch.object(AG, "owner_turn", return_value=True):
            for req in ({"kind": "hook", "ref": "orders"}, {"kind": "hook", "ref": "orders", "to": ["not-an-address"]},
                        {"kind": "hook", "ref": "orders", "domains": ["com"]}, {"kind": "bot", "ref": "x", "to": [ALLY]}):
                self.assertEqual((await AG.issue(self.host, req, None))["why"], "shape", req)
            self.assertEqual((await AG.issue(self.host, {"kind": "hook", "ref": "nope", "to": [ALLY]}, None))["why"], "no_target")
        self.assertEqual(self.cards, [])


if __name__ == "__main__":
    import unittest
    unittest.main()
