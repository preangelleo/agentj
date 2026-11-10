"""0304-frozen A3 — security r1 controls (reports/qa/0304-frozen/security-threatmodel-r1.md F2 F3 F8 F9 F12 F13;
THREAT-MODEL.md rows R-F2 R-F3 R-F8 R-F13).

Same fixtures as test_0304_automation_grant (real Ed25519 + WebAuthn proofs, local fake SMTP). Synthetic addresses are
@example.com; a consumer-mailbox address is assembled at run time (never a literal address in source).
"""
import _hermetic  # noqa: F401,I001
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

from agentj import automation_grant as AG, controls, hooks, provenance
from agentj import workflow_mail as wm
from test_0304_automation_grant import ALLY, OTHER, OWNER, Base


def fm(local, *labels):
    """A consumer-mailbox address built at run time (no literal real-provider address in the source)."""
    return local + "@" + ".".join(labels)


class DomainPolicy(Base):                                  # F2
    async def issue(self, **kw):
        req = {"kind": "hook", "ref": "orders", "to": [], "domains": [], **kw}
        with patch.object(AG, "owner_turn", return_value=True):
            return await AG.issue(self.host, req, None)

    async def test_public_suffix_domain_refused(self):
        for d in ("co.uk", "com.cn", "org.au", "gov.br", "ac.jp", "tokyo.jp", "x.osaka.jp", "bj.cn", "ny.us",
                  "k12.ny.us", "eu.org", "uk.com", "github.io", "herokuapp.com", "vercel.app", "x.sch.uk", "CO.UK.",
                  "spb.ru", "msk.ru"):                                   # security impl r1 L6
            with self.subTest(domain=d):
                res = await self.issue(domains=[d])
                self.assertEqual((res["why"], res.get("reason")), ("automation_grant_domain_refused", "public_suffix"), res)
                self.assertIn("--to name@", res["detail"])
        self.assertEqual((await self.issue(domains=["com"]))["why"], "shape")      # one label is never a domain
        self.assertEqual(self.cards, [])
        res = await self.issue(domains=["acme.co.uk", "shop.example.com.cn"])        # a registrable name under them is fine
        self.assertTrue(res["ok"], res)
        self.assertEqual(len(self.cards), 1)

    async def test_freemail_domain_refused_exact_address_ok(self):
        for d in ("gmail.com", "googlemail.com", "qq.com", "foxmail.com", "163.com", "126.com", "yeah.net", "sina.com",
                  "sohu.com", "outlook.com", "hotmail.com", "live.com", "msn.com", "icloud.com", "me.com", "mac.com",
                  "yahoo.com", "yahoo.co.jp", "aol.com", "proton.me", "protonmail.com", "gmx.de", "gmx.net",
                  "yandex.ru", "yandex.com", "mail.ru", "naver.com", "aliyun.com", "vip.qq.com", "mail.qq.com",
                  "hotmail.fr", "outlook.jp", "live.cn", "yahoo.co.uk",
                  # security impl r1 L6: more mailboxes and the alias / forwarding relays
                  "duck.com", "tutamail.com", "mailfence.com", "posteo.de", "mailbox.org", "zoho.in", "email.cn",
                  "163.net", "sohu.net", "riseup.net", "disroot.org", "addy.io", "anonaddy.me", "simplelogin.com",
                  "tuta.io", "tutanota.com", "pm.me", "hey.com", "fastmail.com", "fastmail.fm", "gmx.com", "web.de",
                  "t-online.de", "laposte.net", "orange.fr", "libero.it", "rambler.ru", "seznam.cz", "naver.com",
                  "daum.net", "hanmail.net", "mozmail.com", "slmail.me"):
            with self.subTest(domain=d):
                res = await self.issue(domains=[d])
                self.assertEqual((res["why"], res.get("reason")), ("automation_grant_domain_refused", "freemail"), res)
        self.assertIsNone(AG.domain_refusal("live.example.com"))      # a company sub-domain named "live" is not a mailbox
        self.assertIsNone(AG.domain_refusal("outlook.office365.com"))
        self.assertEqual(self.cards, [])
        friend = fm("pat.lee", "gmail", "com")
        res = await self.issue(to=[friend.upper()])                    # an exact address there is fine
        self.assertTrue(res["ok"], res)
        self.hook_turn()
        self.assertTrue((await self.send([friend]))["ok"])
        res = await self.send([fm("someone.else", "gmail", "com")])
        self.assertEqual((res["why"], res["missing"]), ("automation_grant_required", [fm("someone.else", "gmail", "com")]))
        self.assertEqual(len(self.smtp.sent), 1)
        # an old / tampered ledger row with a broad domain does not verify any more
        data = self.ledger()
        row = next(iter(data["grants"].values()))
        row["core"]["domains"] = ["gmail.com"]
        AG.save(self.st, data)
        self.assertEqual((await self.send([friend]))["why"], "automation_grant_invalid")

    async def test_idn_domain_shown_punycode(self):
        res = await self.issue(domains=["Bücher.example"], to=["Shop@Bücher.example"])   # NFD + NFC forms
        self.assertTrue(res["ok"], res)
        card = self.cards[-1]
        self.assertIn("xn--bcher-kva.example（显示为 bücher.example）", card)
        self.assertIn("shop@xn--bcher-kva.example", card)
        self.assertNotIn("混用多种文字", card)
        core = self.ledger()["grants"][res["grant"]]["core"]
        self.assertEqual((core["domains"], core["to"]), (["xn--bcher-kva.example"], ["shop@xn--bcher-kva.example"]))
        self.assertTrue(AG.covers(core, "sales@xn--bcher-kva.example"))
        self.assertTrue(AG.covers(core, "sales@eu.xn--bcher-kva.example"))      # label-wise sub-domain
        self.assertFalse(AG.covers(core, "sales@xn--bcher-kva.example.evil.test"))
        self.assertFalse(AG.covers(core, "sales@notxn--bcher-kva.example"))
        self.assertTrue(AG.covers(core, "Shop@Bücher.example"))           # the same normalization on both sides
        self.hook_turn()
        self.assertTrue((await self.send(["sales@xn--bcher-kva.example"]))["ok"])
        res = await self.issue(domains=["аpple.example"])                   # Cyrillic а + Latin
        self.assertTrue(res["ok"], res)
        self.assertIn("【注意：混用多种文字，可能在仿冒别的域名】", self.cards[-1])
        self.assertTrue(AG.mixed_script(AG.norm_domain("аpple.example")))
        self.assertFalse(AG.mixed_script(AG.norm_domain("中文abc.example")))      # Latin + CJK is the ordinary mix
        from agentj import action_policy, approval_review                          # the card text survives the projection
        norm = action_policy.display_normalizer(lang="zh", workdir=str(self.work))
        self.assertEqual(approval_review.display_text(self.cards[-1], norm), self.cards[-1])


class CallerLineage(Base):                                 # F3
    async def test_concurrent_foreign_process_cannot_use_hook_grant(self):
        await self.grant()
        self.caller.stop()
        self.addCleanup(self.caller.start)
        self.hook_turn()
        self.host.agent.session = {"pid": 100}
        self.host._nonowner_spans = [[40.0, None]]           # this hook turn opened its span at t=40
        from agentj.core import process_evidence as pe
        for born, ok in ((20.0, False), (50.0, True)):       # a job started before the hook turn vs. one of this turn
            rows = {100: (1, 10.0, "t"), 4242: (100, born, "t")}
            with self.subTest(born=born), patch.object(pe, "row", side_effect=lambda pid, rows=rows: rows.get(pid)):
                res = await self.send([ALLY])
                self.assertEqual(res["ok"], ok, res)
                if not ok:
                    self.assertEqual(res["why"], "group_source")
        self.assertEqual(len(self.smtp.sent), 1)

    async def test_friend_task_never_uses_automation_grant(self):
        await self.grant()

        async def no_grant():
            # Whatever the turn's ordinary rule is (refused, or the delegated turn's own mail card), the grant is
            # never what lets it through: no `automation_grant` in the result, and anything sent was carded.
            self.ask.reset_mock()
            res = await self.send([ALLY])
            self.assertNotIn("automation_grant", res, res)
            self.assertFalse(res.get("no_card"), res)
            if res["ok"]:
                self.assertEqual(self.ask.await_args.args[0], "agentj mail send")
        for kind in ("friend_task", "same_owner", "wechat_task", "official_assist"):
            with self.subTest(turn=kind):
                self.host.agent = SimpleNamespace(kind="claude", cur_send=SimpleNamespace(
                    source={"kind": kind, "role": "task"}, hook_notice=self.hook["id"], by="hook"))
                await no_grant()
            with self.subTest(request=kind):                 # a hook turn, but the request around it is stricter
                self.hook_turn()
                with provenance.request_source({"kind": kind, "role": "task"}):
                    await no_grant()
        self.hook_turn()
        with provenance.request_source(provenance.email_guest("guest@example.com")):   # rank-2 request in a hook turn
            await no_grant()
        self.hook_turn()
        with patch("agentj.background_done.chain_turn", return_value={"id": "c"}):    # a host chain turn
            await no_grant()
        self.hook_turn()
        self.host.agent.pending_source = {"kind": "same_owner", "role": "task"}
        await no_grant()
        self.assertEqual(next(iter(self.ledger()["grants"].values()))["uses"], 0)
        self.hook_turn()                                     # the plain hook turn still uses it (control)
        self.assertEqual((await self.send([ALLY])).get("automation_grant"), next(iter(self.ledger()["grants"])))

    async def test_task_grant_requires_run_lineage(self):
        contract = {"workflow_id": "digest", "root": str(self.work), "tsha": "a" * 64}
        with patch.object(AG, "owner_turn", return_value=True):
            self.assertTrue((await AG.issue(self.host, {"kind": "task", "ref": "digest", "to": [ALLY]}, None))["ok"])
        self.host.agent = SimpleNamespace(kind="claude")
        self.assertEqual((await self.send([ALLY]))["why"], "group_source")          # no live run scope at all
        proc = SimpleNamespace(returncode=None, pid=777)
        self.host.scheduler.mail_scope = {"live": True, "mail_roots": [(proc, "tok")], "mail_contract": contract,
                                          "mail_admission": {}}
        with patch.object(wm, "descendant", return_value=False):                    # peer is not under the run root
            self.assertEqual((await self.send([ALLY]))["why"], "group_source")
        with patch.object(wm, "source", return_value=contract):
            with provenance.request_source({"kind": "friend_task", "role": "task"}):
                self.assertEqual((await self.send([ALLY]))["why"], "group_source")   # stricter request around it
            self.assertTrue((await self.send([ALLY]))["ok"])
        self.assertEqual(len(self.smtp.sent), 1)


class Lifecycle(Base):                                     # F8
    async def test_grant_dies_on_hook_delete_estop_signer_revoke(self):
        # (1) hook delete: hooks.Store.end → HookService._grants_gone → target_gone (revoked + epoch)
        g = await self.grant()
        store = hooks.Store(self.st.root / "hk")
        store.on_end = lambda hid: AG.target_gone(self.st, "hook", hid)
        store.doc["hooks"][self.hook["id"]] = self.hook
        store.end(self.hook, "revoked")
        self.assertEqual(AG.items(self.host)[0]["state"], "revoked")
        self.assertEqual(self.ledger()["grants"][g["grant"]]["revoked_why"], "deleted")
        self.hook_turn()
        self.assertEqual((await self.send([ALLY]))["why"], "group_source")          # a gone hook is no automation
        self.hook.update(state="active", ended=None)
        self.assertEqual((await self.send([ALLY]))["why"], "automation_grant_required")
        from agentj import hook_service
        self.assertIn("self.store.on_end = self._grants_gone", __import__("inspect").getsource(hook_service.HookService))
        # a hook that disappears without end() (store edited): current_digest None → stale, never usable
        await self.grant()
        self.host.hooks.store.hs.pop(self.hook["id"])
        self.assertEqual(AG.items(self.host)[0]["state"], "stale")
        self.host.hooks.store.hs[self.hook["id"]] = self.hook
        # (2) Stop everything: the grant dies even after Resume; the refusal says the owner must sign again
        g = await self.grant(to=[ALLY, OTHER])
        self.hook_turn()
        self.assertTrue((await self.send([ALLY]))["ok"])
        controls.set_estop(self.st, True, "terminal")
        controls.set_estop(self.st, False, "terminal")
        res = await self.send([ALLY])
        self.assertEqual((res["why"], res["reason"]), ("automation_grant_stale", "estop"), res)
        self.assertIn("全部停下", res["detail"])
        self.assertIn("must approve it again", res["detail"])
        self.assertEqual(AG.items(self.host)[0]["state"], "stale")
        controls.set_estop(self.st, True, "terminal")
        with patch.object(AG, "owner_turn", return_value=True):
            self.assertEqual((await AG.issue(self.host, {"kind": "hook", "ref": "orders", "to": [ALLY]}, None))["why"],
                             "estop")
        controls.set_estop(self.st, False, "terminal")
        g = await self.grant()                                      # re-signed after Resume: usable again
        self.assertTrue((await self.send([ALLY]))["ok"])
        # (3) the signing remote is removed (no approval key any more) → invalid
        keep = self.st.sign_key
        self.st.sign_key = lambda d: None
        res = await self.send([ALLY])
        self.assertEqual(res["why"], "automation_grant_invalid", res)
        self.assertIn("遥控器已移除", res["detail"])
        self.assertEqual(AG.items(self.host)[0]["state"], "stale")
        self.st.sign_key = keep
        # (4) the SMTP account changes → the grant (bound to the old one) is stale
        from agentj import mail as M
        cfg = M.read_config(self.st)
        from test_p194_owner_direct import AGENT, CODE
        M.save_config(self.st, M.validate({**cfg, "smtp": {**cfg["smtp"], "host": "localhost"}}))
        M.store_secret(self.st, bytearray(CODE.encode()), AGENT)
        res = await self.send([ALLY])
        self.assertEqual((res.get("why"), res.get("reason")), ("automation_grant_stale", "account"), res)
        M.save_config(self.st, cfg)
        M.store_secret(self.st, bytearray(CODE.encode()), AGENT)
        self.assertTrue((await self.send([ALLY]))["ok"])
        self.assertEqual(len(self.smtp.sent), 3)
        self.assertNotEqual(g["grant"], None)

    async def test_attachments_need_separate_action(self):
        f = self.work / "invoice.txt"
        f.write_text("Invoice 1042: two boxes, shipped.\n")
        await self.grant()
        self.assertIn("动作：发邮件（不带附件）", self.cards[-1])
        self.hook_turn()
        res = await self.send([ALLY], files=[str(f)])
        self.assertEqual((res["why"], res["reason"]), ("automation_grant_required", "mail+files"), res)
        self.assertIn("--files", res["detail"])
        self.assertEqual(self.smtp.sent, [])
        self.assertTrue((await self.send([ALLY]))["ok"])                          # without the file: in scope
        self.owner_turn()
        g = await self.grant(files=True)
        self.assertIn("动作：发邮件（可带附件）", self.cards[-1])
        self.assertEqual(self.ledger()["grants"][g["grant"]]["core"]["actions"], ["mail", "mail+files"])
        self.hook_turn()
        res = await self.send([ALLY], files=[str(f)])
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["files"], ["invoice.txt"])
        self.assertFalse(res["owner_direct"])                                     # outward masking stays on

    async def test_card_says_permanent_and_who_controls_the_content(self):
        await self.grant()
        card = self.cards[-1]
        self.assertIn("到期时间：长期有效（直到你撤销）", card)
        self.assertIn("能触发这个 hook 的人都能让 Agent 给这些收件人发信，信的内容不受你控制", card)
        self.assertIn("发信账号：", card)
        self.host.lang = "en"
        await self.grant(to=[ALLY, OTHER])
        card = self.cards[-1]
        self.assertIn("Expires: no expiry (until you revoke it)", card)
        self.assertIn("Anyone who can trigger this hook can make the Agent mail these recipients", card)
        self.host.lang = "zh"
        await self.grant(expires="30d", to=[OTHER])
        self.assertNotIn("长期有效", self.cards[-1])
        self.assertIs(AG.items(self.host)[0]["permanent"], False)

    async def test_use_log_names_grant_and_request(self):
        g = await self.grant()
        self.hook_turn()
        self.assertTrue((await self.send([ALLY, OWNER]))["ok"])
        rows = [json.loads(x) for x in (self.st.root / "host.log").read_text().splitlines() if "automation_grant_use" in x]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], g["grant"])
        self.assertRegex(rows[0]["request"], r"^[0-9a-f]{16}$")
        self.assertIn(("auto", rows[0]["request"]), [(k, kw.get("id")) for k, kw in self.acts])
        log = (self.st.root / "host.log").read_text()
        self.assertNotIn(ALLY, log)

    def test_hook_digest_fields(self):
        from agentj import hook_service
        self.assertEqual(hook_service.GRANT_FIELDS, ("id", "preset", "life", "auth", "ip_allow", "target", "e2e", "note"))
        h = dict(self.hook)
        base = hook_service.grant_digest(h)
        for k, v in (("count", 9), ("last", {"at": 2}), ("fails", {"n": 1}), ("seal", {"kid": "x"}), ("name", "renamed"),
                     ("enabled", False), ("approved", {"by": "dev1"})):
            self.assertEqual(hook_service.grant_digest({**h, k: v}), base, k)
        self.assertNotIn("secret", json.dumps(h["auth"]).lower())        # the auth method only; the key is in secrets/


class Issuance(Base):                                      # F13
    async def test_grant_card_never_batch_or_goal(self):
        seen = []
        orig = self.sign

        async def sign(tool, inp, **kw):
            seen.append(kw)
            return await orig(tool, inp, **kw)
        self.ask.side_effect = sign
        await self.grant()
        kw = seen[-1]
        self.assertIs(kw["batch"], False)
        self.assertTrue(kw["always_ask"] and kw["require_passkey"] and kw["grant_info"])
        for k in ("allow_offer", "task_actions", "_bounded_context", "risk_scope"):
            self.assertNotIn(k, kw)
        from agentj import preauth, uv_list
        self.assertIsNone(preauth.offer(AG.TOOL))                         # Computer-Use preauth can never cover it
        self.assertTrue(uv_list.required(AG.TOOL, {"summary": "x"}))
        n = len(self.cards)
        req = {"kind": "hook", "ref": "orders", "to": [ALLY, OTHER]}
        for ctx, setup in (("goal", lambda: setattr(self.host, "goals", SimpleNamespace(current=lambda: {"id": "g1"}))),
                           ("batch", lambda: (setattr(self.host, "grants", {"b": object()}),
                                              setattr(self.host, "_grant_live", lambda g: True))),
                           ("bounded_plan", lambda: setattr(self.host.agent, "cur_send",
                                                            SimpleNamespace(bounded_plan_resume="rid1")))):
            with self.subTest(context=ctx):
                self.owner_turn()
                saved = dict(vars(self.host)), getattr(self.host.agent, "cur_send", None)
                setup()
                with patch.object(AG, "owner_turn", return_value=True):
                    res = await AG.issue(self.host, req, None)
                self.assertEqual((res["why"], res["reason"]), ("automation_grant_context", ctx), res)
                for k in ("goals", "grants", "_grant_live"):
                    if k not in saved[0]:
                        vars(self.host).pop(k, None)
                self.host.agent.cur_send = saved[1]
        self.assertEqual(len(self.cards), n)
        # same_owner / friend / chain turns: owner_turn itself says no (the real function, no terminal, no task)
        for src in ({"kind": "same_owner", "role": "task"}, {"kind": "friend_task", "role": "task"}):
            self.host.agent = SimpleNamespace(kind="claude", cur_send=SimpleNamespace(source=src))
            self.assertFalse(AG.owner_turn(self.host, None, (False, False)), src)
        self.owner_turn()
        with patch.object(provenance, "owner_speaking", return_value=True), \
                patch("agentj.application_ingress.allowed", return_value=True):
            self.assertTrue(AG.owner_turn(self.host, None, (False, False)))
            with patch("agentj.background_done.chain_turn", return_value={"id": "c"}):
                self.assertFalse(AG.owner_turn(self.host, None, (False, False)))


class NativeSchedulerQuote(Base):                          # F12
    def test_native_scheduler_card_follows_541_rule(self):
        from agentj import elevate
        card = {"_native_scheduler_nonce": "n1"}
        adapter = SimpleNamespace(card=card, terminal=False, rid="r1", envelope={"source": {}})
        host = SimpleNamespace(_approval_special={"r1": adapter}, stopped=lambda: False)
        adapter.host = host
        card["review_adapter"] = adapter
        me = SimpleNamespace(host=host, _native_plan=lambda c: SimpleNamespace(live=lambda: True))
        cases = (({"verified": True, "suspicious": False}, True),                                   # plain owner turn
                 ({"verified": True, "suspicious": True, "root": {"verified": True}}, True),        # owner quoted material
                 ({"verified": False, "suspicious": True, "root": {"verified": False}}, False),     # guest / forward
                 ({"verified": False, "suspicious": True, "root": {"verified": True}}, False),
                 ({"verified": True, "suspicious": True}, False),                                   # no owner root
                 ({"verified": True, "suspicious": True, "root": "x"}, False))
        for source, want in cases:
            with self.subTest(source=source):
                adapter.envelope = {"source": source}
                self.assertIs(elevate.Elevator._source_allows(me, card), want)
        me = SimpleNamespace(host=host, _native_plan=lambda c: SimpleNamespace(live=lambda: False))
        adapter.envelope = {"source": {"verified": True, "suspicious": False}}
        self.assertFalse(elevate.Elevator._source_allows(me, card))                                # plan not live


class StepInput(Base):                                     # F9
    def test_uv_decided_from_the_actual_step_input(self):
        from agentj import elevate, google_api, owner_grant
        seen = []

        def main_requirement(tool, tool_input=None, **kw):
            seen.append((tool, tool_input))
            return "allow"
        agent = self.host.agent
        with patch.object(owner_grant, "owner_grant_source", return_value=True), \
                patch("agentj.policy_registry.main_requirement", side_effect=main_requirement):
            req = {"t": "google_api", "op": "drive_upload", "file": "/tmp/x.bin"}
            google_api.refusal(agent, None, "drive_upload", req)
            self.assertEqual(seen[-1], ("agentj google drive upload", {"op": "drive_upload", "file": "/tmp/x.bin"}))
            no = google_api.refusal(agent, None, "drive_upload")          # no input → UV (fail closed), never `{}`
            self.assertEqual(no["why"], "owner_grant_scope")
            n = len(seen)
            elevate.external_support(SimpleNamespace(agent=agent), "support", {"t": "host_diagnostics", "op": "support",
                                                                               "text": "hi"})
            self.assertEqual(seen[n:], [("agentj support", {"op": "support", "text": "hi"})])
            self.assertTrue(elevate.external_support(SimpleNamespace(agent=agent), "support"))      # no input → UV


if __name__ == "__main__":
    import unittest
    unittest.main()
