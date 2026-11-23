"""P539 (#300): every remote can set (and shows) its OWN passkey, Computer Use is approvable from any remote, and the
owner can pre-authorize 「这台遥控器上 Computer Use 免问」 — narrow, revocable, never for a red line.

- pk_reg's advisory `aa` / `plat` → pk.local / pk.plat; pk_restore's `aa` (or `att`) → local False / True / unchanged;
  `pk_state` to the remote and `pk` on every devs row; `fa_s` beside every `fa`.
- ask_done `why`; the Agent's timeout line says what to do next.
- computer.control: the computer-use servers' every operation + exact pointer / keyboard names; unknown stays denied.
- 「免问」 pre-authorization (preauth.py) is gone (0372, decision 0300 item 2 option A): the grant could no longer be
  created since 0.30.0; its list / revoke handlers, the module and their tests were removed. Mail picks unchanged.
- Codex: an MCP tool-call approval elicitation (empty form) becomes a normal card; real forms are still declined."""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import approvals, passkey, policy_registry as P, serve, wire  # noqa: E402
from _testtmp import TEST_TMP  # noqa: E402
import test_p57_passkey as P57  # noqa: E402

CU = "mcp__computer-use__click"
JS = "mcp__cua_repl__js"
PLUGIN = "unified-computer-use@openai-bundled"           # pluginId as Codex 0.162.1 emits it (<plugin>@<marketplace>)
BIND = {"kind": "codex_plugin", "id": PLUGIN, "server": "cua_repl"}


# ------------------------------------------------------------------ pure parts
class Units(unittest.TestCase):
    def test_pk_state(self):
        self.assertEqual(passkey.pk_state(None), "none")
        self.assertEqual(passkey.pk_state({"id": "x", "local": True}), "local")
        self.assertEqual(passkey.pk_state({"id": "x", "local": False}), "borrowed")
        self.assertEqual(passkey.pk_state({"id": "x"}), "unknown")
        self.assertEqual(passkey.pk_state({"local": True}), "none")

    def test_pk_where_validates(self):
        self.assertEqual(serve._pk_where({"aa": "platform", "plat": "mac"}), {"local": True, "plat": "mac"})
        self.assertEqual(serve._pk_where({"aa": "cross-platform", "plat": "bogus"}), {"local": False})
        self.assertEqual(serve._pk_where({}), {"local": True}, "registration always asks for platform")
        self.assertEqual(serve._pk_where({"aa": "PLATFORM", "plat": 3}), {"local": True}, "unknown values are ignored")
        self.assertEqual(serve._pk_where({"att": "cross-platform"}), {"local": True}, "`att` is the attestation on pk_reg")

    def test_done_why(self):
        a = mock.Mock(pk_fail=False)
        self.assertEqual(serve._done_why("device", "allow", a), {})
        self.assertEqual(serve._done_why("device", "deny", a), {"why": "denied"})
        self.assertEqual(serve._done_why("timeout", "deny", a), {"why": "timeout"})
        a.pk_fail = True
        self.assertEqual(serve._done_why("timeout", "deny", a), {"why": "passkey"})
        for r, w in (("offline", "nobody"), ("no_device", "nobody"), ("estop", "estop"), ("stopped", "estop"),
                     ("serve_stop", "serve_stop")):
            self.assertEqual(serve._done_why(r, "deny"), {"why": w})
        self.assertEqual(serve._done_why("agent_gone", "deny"), {})

    def test_timeout_text_says_what_next(self):
        zh = serve._timeout_text("zh", 120)
        self.assertIn("秒内没有批准", zh)
        self.assertIn("设置 → Agent 与遥控器 → 本机 Face ID / 指纹", zh)
        self.assertIn("再发起一次", zh)
        en = serve._timeout_text("en", 120, pk_fail=True)
        self.assertIn("passkey", en)
        self.assertNotIn("秒", en)

    def test_computer_control_mapping(self):
        for t in (CU, "mcp__computer-use__screenshot", "mcp__computer_use__type_text", "mcp__unified-computer-use__scroll",
                  "mcp__anything__left_click", "mcp__x__press_key", "computer.control"):
            self.assertEqual(P.tool_action(t), "computer.control", t)
            self.assertEqual(P.main_requirement(t, {}), "hold", t)   # P573: Computer Use is one button
        for t in ("mcp__x__click", "mcp__x__screenshot", "mcp__computer-use__", "mcp____click", "mcp__computer-use__a__b",
                  "computer-use", "click", "mcp__x__unknown"):
            self.assertNotEqual(P.tool_action(t), "computer.control", t)
            self.assertEqual(P.main_requirement(t, {}), "hold" if t in ("mcp__x__click", "mcp__x__screenshot", "mcp__x__unknown") else "deny", t)
        self.assertEqual(P.decision("channel", "computer.control"), "handoff")
        self.assertEqual(P.decision("friend", "computer.control"), "handoff")
        self.assertEqual(P.decision("support", "computer.control"), "deny")

    ELI = {"threadId": "t", "turnId": "u", "serverName": "computer-use", "mode": "form",
           "message": 'Allow the computer-use MCP server to run tool "click"?',
           "requestedSchema": {"type": "object", "properties": {}},
           "_meta": {"codex_approval_kind": "mcp_tool_call", "tool_params": {"x": 999}}}

    def test_elicitation_parse_needs_codex_own_open_item(self):
        """M2: `_meta` and `message` pass through from the server verbatim (probed, codex 0.162.1) — the approval must
        match an open mcpToolCall item Codex announced, once; server / tool / arguments come from that item."""
        from agentj.agent_codex import mcp_tool_approval as f
        p = self.ELI
        calls = lambda: {"i1": {"server": "computer-use", "tool": "click", "arguments": {"x": 1}, "turn": "u"}}  # noqa: E731
        self.assertIsNone(f(p), "no item announced: a server's own look-alike")
        self.assertIsNone(f(p, {}))
        c = calls()
        self.assertEqual(f(p, c), (CU, {"x": 1}, "i1", None), "arguments from the item, not _meta.tool_params")
        self.assertIsNone(f(p, c), "each item is approved at most once (a second look-alike during the call)")
        self.assertIsNone(f({**p, "message": 'Allow the computer-use MCP server to run tool "type_text"?'}, calls()),
                          "the message names another tool than the open item")
        self.assertIsNone(f({**p, "serverName": "evil"}, calls()))
        self.assertIsNone(f({**p, "turnId": "other"}, calls()))
        self.assertIsNone(f({**p, "requestedSchema": {"type": "object", "properties": {"name": {"type": "string"}}}}, calls()))
        self.assertIsNone(f({**p, "_meta": {}}, calls()), "not Codex's tool-call approval")
        self.assertIsNone(f({**p, "mode": "url"}, calls()))
        self.assertIsNone(f({**p, "serverName": "a__b"}, {"i": {"server": "a__b", "tool": "click"}}))
        self.assertIsNone(f({**p, "message": 'Allow x to run tool "a b"?'}, calls()))
        self.assertIsNone(f({"serverName": "x", "message": "fill this", "requestedSchema": {}}, calls()))

    def codex(self):
        from agentj.agent_codex import CodexAgent
        cx = CodexAgent(None, {"kind": "codex", "dir": "/tmp", "model": None})
        cx.tid = "t"
        calls, answers, notes, refused = [], [], [], []
        cx._bg = lambda coro: (coro.close(), calls.append(coro))
        cx._answer = lambda rid, result=None, error=None: answers.append((rid, result))
        cx.local_only = notes.append
        cx.touch = lambda: None
        cx._busy = lambda *a: None
        cx.host = mock.Mock(policy_refused=lambda *a: refused.append(a))
        return cx, answers, notes, refused

    def started(self, cx, iid="i1", tool="click", args=None, method="item/started", status="inProgress"):
        cx._on_note(method, {"threadId": "t", "turnId": "u", "item": {"type": "mcpToolCall", "id": iid, "server": "computer-use",
                                                                      "tool": tool, "status": status,
                                                                      "arguments": {"x": 1} if args is None else args}})

    def test_codex_routes_plain_approval_and_declines_forms(self):
        cx, answers, notes, _ = self.codex()
        self.started(cx)
        with mock.patch.object(cx, "_decide", wraps=lambda *a: asyncio.sleep(0)) as dec:
            cx._on_request({"method": "mcpServer/elicitation/request", "id": 7, "params": self.ELI})
            dec.assert_called_once_with(7, CU, {"x": 1}, {"action": "accept", "content": {}}, {"action": "decline"}, False,
                                        bind=None)
            # the server's own look-alike while its call runs: the item is used → declined like any form
            cx._on_request({"method": "mcpServer/elicitation/request", "id": 9, "params": self.ELI})
            self.assertEqual(dec.call_count, 1)
        self.assertEqual(answers, [(9, {"action": "decline"})])
        self.started(cx, method="item/completed", status="completed")
        self.assertEqual(cx.mcp_open, {})
        cx._on_request({"method": "mcpServer/elicitation/request", "id": 8,
                        "params": {"serverName": "x", "message": "fill this", "requestedSchema": {"type": "object",
                                                                                                 "properties": {"a": {"type": "string"}}}}})
        self.assertEqual(answers[-1], (8, {"action": "decline"}))
        self.assertEqual(len(notes), 2)

    def test_codex_other_thread_and_unannounced_are_declined(self):
        cx, answers, _, _ = self.codex()
        cx._on_note("item/started", {"threadId": "sub", "item": {"type": "mcpToolCall", "id": "i1", "server": "computer-use",
                                                                 "tool": "click", "status": "inProgress", "arguments": {}}})
        with mock.patch.object(cx, "_decide") as dec:
            cx._on_request({"method": "mcpServer/elicitation/request", "id": 1, "params": self.ELI})
            cx._on_request({"method": "mcpServer/elicitation/request", "id": 2, "params": {**self.ELI, "threadId": "sub"}})
            dec.assert_not_called()
        self.assertEqual(answers, [(1, {"action": "decline"}), (2, {"action": "decline"})])

    def test_codex_refuses_arguments_too_long_for_the_card(self):
        from agentj import agent_codex
        cx, answers, _, refused = self.codex()
        self.started(cx, tool="click", args={"text": "x" * (agent_codex.MCP_ARGS_MAX + 10)})
        with mock.patch.object(cx, "_decide") as dec:
            cx._on_request({"method": "mcpServer/elicitation/request", "id": 3, "params": self.ELI})
            dec.assert_not_called()
        self.assertEqual(answers, [(3, {"action": "decline"})])
        tool, inp, why, notice = refused[0]
        self.assertEqual(tool, CU)
        self.assertEqual(set(inp), {"arguments_size"})
        self.assertNotIn("xxxx", repr(refused))
        self.assertIn("参数太长", why + notice)

    # ---------------------------------------------------------- P539: the Codex app's bundled Computer Use plugin
    def test_codex_plugin_binding_is_exact(self):
        b = P.codex_plugin_bind(PLUGIN, "cua_repl")
        self.assertEqual(b, BIND)
        self.assertEqual(P.codex_plugin_bind("computer-use@openai-bundled", "cua_repl")["id"], "computer-use@openai-bundled")
        for pid, srv in (("unified-computer-use@evilmkt", "cua_repl"), ("unified-computer-use@openai-bundled-x", "cua_repl"),
                         ("unified-computer-use", "cua_repl"), (None, "cua_repl"), ("browser@openai-bundled", "cua_repl"),
                         (PLUGIN, "other"), (PLUGIN, None), (["x"], "cua_repl")):
            self.assertIsNone(P.codex_plugin_bind(pid, srv), (pid, srv))
        for t in (JS, "mcp__cua_repl__js_reset"):
            self.assertEqual(P.bound_action(t, BIND), "computer.control", t)
        for t in ("mcp__cua_repl__turn_ended", "mcp__cua_repl__exec", "mcp__other__js", CU, "computer.control"):
            self.assertIsNone(P.bound_action(t, BIND), t)
        self.assertIsNone(P.bound_action(JS, {**BIND, "id": "unified-computer-use@evilmkt"}), "a forged binding")
        self.assertIsNone(P.bound_action(JS, {**BIND, "kind": "mcp_ops"}))
        self.assertIsNone(P.bound_action(JS, None))
        self.assertTrue(P.codex_cu_lifecycle("mcp__cua_repl__turn_ended", BIND))
        self.assertFalse(P.codex_cu_lifecycle("mcp__cua_repl__turn_ended", None))
        self.assertFalse(P.codex_cu_lifecycle(JS, BIND))
        # by name alone the plugin's tools are unknown (deny), as before
        self.assertEqual(P.main_requirement(JS, {"code": "x"}), "hold")   # P573: a name is no proof of the three classes

    def test_claude_code_computer_use_ops(self):
        """Claude Code's own server is `computer-use` (`mcp__computer-use__*`, from the installed claude binary): every
        operation of it maps to the one registered action."""
        for op in ("left_click", "double_click", "type", "key", "scroll", "screenshot", "zoom", "open_application",
                   "app_click", "app_type", "list_apps", "write_clipboard", "release_full_control",
                   "request_access", "request_full_control", "request_teach_access", "teach_step", "teach_batch",
                   "read_clipboard", "computer_batch", "app_batch"):
            self.assertEqual(P.tool_action("mcp__computer-use__" + op), "computer.control", op)

    def plugin_started(self, cx, iid="p1", tool="js", plugin=PLUGIN, server="cua_repl", args=None):
        cx._on_note("item/started", {"threadId": "t", "turnId": "u", "item": {
            "type": "mcpToolCall", "id": iid, "server": server, "tool": tool, "status": "inProgress",
            "arguments": {"code": "await click(1, 2)"} if args is None else args, "pluginId": plugin}})

    def eli(self, tool="js", server="cua_repl"):
        return {**self.ELI, "serverName": server, "message": f'Allow the {server} MCP server to run tool "{tool}"?'}

    def test_codex_plugin_item_routes_with_its_binding(self):
        cx, answers, _, _ = self.codex()
        self.plugin_started(cx)
        with mock.patch.object(cx, "_decide", wraps=lambda *a, **k: asyncio.sleep(0)) as dec:
            cx._on_request({"method": "mcpServer/elicitation/request", "id": 1, "params": self.eli()})
            dec.assert_called_once_with(1, JS, {"code": "await click(1, 2)"}, {"action": "accept", "content": {}},
                                        {"action": "decline"}, False, bind=BIND)
            # wrong marketplace / a config server named cua_repl (pluginId null): no binding → the name decides (unknown)
            for i, plugin in ((2, "unified-computer-use@evilmkt"), (3, None)):
                self.plugin_started(cx, iid=f"p{i}", plugin=plugin)
                cx._on_request({"method": "mcpServer/elicitation/request", "id": i, "params": self.eli()})
                self.assertIsNone(dec.call_args.kwargs["bind"], plugin)
        self.assertEqual(answers, [])

    def test_codex_turn_ended_allowed_only_from_the_plugin(self):
        cx, answers, _, _ = self.codex()
        self.plugin_started(cx, tool="turn_ended", args={"hook_event_name": "Stop"})
        with mock.patch.object(cx, "_decide") as dec:
            cx._on_request({"method": "mcpServer/elicitation/request", "id": 1, "params": self.eli("turn_ended")})
            dec.assert_not_called()
        self.assertEqual(answers, [(1, {"action": "accept", "content": {}})])
        ev = [c for c in cx.host.st.log.call_args_list if c.args[0] == "mcp_lifecycle_allow"]
        self.assertEqual(ev[0].kwargs["plugin"], PLUGIN)
        cx, answers, _, _ = self.codex()
        self.plugin_started(cx, tool="turn_ended", plugin=None)
        with mock.patch.object(cx, "_decide", wraps=lambda *a, **k: asyncio.sleep(0)) as dec:
            cx._on_request({"method": "mcpServer/elicitation/request", "id": 2, "params": self.eli("turn_ended")})
            self.assertIsNone(dec.call_args.kwargs["bind"], "not the plugin: the normal (unknown → deny) path")
        self.assertEqual(answers, [])


# ------------------------------------------------------------------ the passkey record (P57 harness, methods reused)
class Record(unittest.TestCase):
    setUp, tearDown = P57.HostLevel.setUp, P57.HostLevel.tearDown
    run_, to, paired, handshake, restore = (P57.HostLevel.run_, P57.HostLevel.to, P57.HostLevel.paired,
                                            P57.HostLevel.handshake, P57.HostLevel.restore)

    def reg(self, extra):
        kp, _, did = self.paired()
        s = serve.Session(cid=3, state="ready", device=did, pub=kp.pub, p33=True)
        self.host.sessions[3] = s
        self.run_(self.host._pk_offer(s))
        a = P57.Authenticator()
        self.run_(self.host._app(s, {**a.register(wire.unb64u(self.to(3)[-1]["n"])), **extra}))
        return did, s

    def test_pk_reg_stores_local_and_plat_and_sends_state(self):
        did, s = self.reg({"aa": "platform", "plat": "android"})
        pk = self.st.devices()[did]["pk"]
        self.assertEqual((pk["local"], pk["plat"]), (True, "android"))
        self.assertIn({"t": "pk_reg_res", "ok": True}, self.to(3))
        # P569 integration: since P566 (#346, REPORT-566:6) `pk_state` local also carries this device's own fa/fa_ch/n for
        # Settings' 「测试一下」 — only fields were added; the state stays `local` and fa is this device's own credential id
        st = [o for o in self.to(3) if o["t"] == "pk_state"][-1]
        self.assertEqual((st["s"], st.get("fa")), ("local", pk["id"]))
        self.assertLessEqual(set(st), {"t", "s", "fa", "fa_ch", "n"})
        devs = [o for o in self.to(3) if o["t"] == "devs"][-1]
        self.assertEqual(devs["items"][0]["pk"], "local")
        self.assertNotIn(pk["id"], json.dumps(devs), "the list never carries a credential id")
        self.assertNotIn(pk["id"], self.st.log_path.read_text())

    def test_pk_reg_cross_platform_and_bad_plat(self):
        did, _ = self.reg({"aa": "cross-platform", "plat": "playstation"})
        pk = self.st.devices()[did]["pk"]
        self.assertEqual(pk["local"], False)
        self.assertNotIn("plat", pk)
        self.assertEqual(self.st.passkey_state(did), "borrowed")

    def restored(self, auth, extra):
        """A restore whose pk_restore carries `extra` (the advisory attachment) → the new record's pk."""
        orig = P57.Authenticator.assert_

        def with_extra(a, *x, **k):
            return {**orig(a, *x, **k), **extra}
        with mock.patch.object(P57.Authenticator, "assert_", with_extra):
            kp, _, got = self.restore(20 + len(self.sent), auth)
        self.assertEqual(got[0]["t"], "pk_ok", "the attachment never takes part in the check")
        return self.st.devices()[wire.device_id(kp.pub)]["pk"], kp

    def test_restore_attachment_sets_local(self):
        a = P57.Authenticator()
        self.paired(a)
        self.assertEqual(passkey.pk_state(next(iter(self.st.devices().values()))["pk"]), "unknown")
        pk, _ = self.restored(a, {})
        self.assertNotIn("local", pk, "missing → unchanged (legacy unknown)")
        pk, _ = self.restored(a, {"aa": "cross-platform"})
        self.assertIs(pk["local"], False, "a Mac page that borrowed the iPhone's passkey over hybrid")
        pk, _ = self.restored(a, {"att": "platform"})
        self.assertIs(pk["local"], True)
        pk, kp = self.restored(a, {"aa": "nonsense"})
        self.assertIs(pk["local"], True, "an unknown value leaves it unchanged")
        s = serve.Session(cid=99, state="ready", device=wire.device_id(kp.pub), pub=kp.pub, p33=True)
        self.host.sessions[99] = s
        self.run_(self.host.remotes.send_state(s))
        # P569 integration: P566 (#346, REPORT-566:6) adds the 「测试一下」 challenge fields to a `local` pk_state
        last = self.to(99)[-1]
        self.assertEqual((last["t"], last["s"], last.get("fa")), ("pk_state", "local", pk["id"]))
        self.assertLessEqual(set(last), {"t", "s", "fa", "fa_ch", "n"})

    def test_fa_s_on_elev_and_ask_cards(self):
        a = P57.Authenticator()
        kp, _, did = self.paired(a)
        s = serve.Session(cid=4, state="ready", device=did, pub=kp.pub, p33=True)
        c = {"id": "c1", "kind": "login", "term": "t1", "nonce": "n", "epk": b"\0" * 32,
             "deadline": time.monotonic() + 60, "fails": 0}
        m = self.host.elevate.card_msg(c, s)
        # P555: a key with no `local` registered under this very id is this device's own
        self.assertEqual((m["fa"], m["fa_s"]), (wire.b64u(a.id), "local"))
        ask = serve.Ask("r" * 32, CU, "点击", "d" * 64, {}, "i", time.monotonic() + 9, None, require_passkey=True)
        m = self.host._ask_msg(ask, s)
        self.assertEqual((m["fa"], m["fa_s"]), (wire.b64u(a.id), "local"))
        with self.st.devices_lock():
            d = self.st.devices(); d[did]["pk"]["local"] = False
            self.st.write_private(self.st.devices_path, json.dumps(d).encode())
        self.assertEqual(self.host._ask_msg(ask, s)["fa_s"], "borrowed")
        self.assertEqual(self.host.elevate.card_msg(c, s)["fa_s"], "borrowed")


# ------------------------------------------------------------------ the ask flow (test_l1 real-host fixtures)
class Flow(unittest.TestCase):
    def setUp(self):
        from test_l1 import _state
        self.tmp = tempfile.TemporaryDirectory(prefix="p539-", dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.st = _state(self.tmp.name)
        self.sent = []

    def host_phone(self, with_passkey=True):
        from test_l1 import Phone, _owner_host, _ready
        ph = Phone(self.st, with_passkey=with_passkey)
        host = _owner_host(self.st, self.sent)
        s = _ready(host, ph)
        s.p33 = True
        host.ask_ttl = 1
        return host, ph, s

    def cards(self, t="ask"):
        return [o for _, o in self.sent if o.get("t") == t]

    @staticmethod
    def signed(ph, host, ask, picks=None, sign_picks=None, ok=True, fa=True):
        d = approvals.shown_digest(ask["tool"], ask["summary"])
        msg = approvals.signed_message(ph.channel, ph.did, ask["id"], "allow" if ok else "deny", d)
        if picks:
            msg += ("\n" + approvals.allow_add_line(picks if sign_picks is None else sign_picks)).encode()
        obj = {"t": "answer", "id": ask["id"], "ok": ok, "sig": wire.b64u(ph.sk.sign(msg)), **({"allow_add": picks} if picks else {})}
        if ok and fa and hasattr(ph, "auth"):
            a = host.asks[ask["id"]]
            obj["fa"] = ph.auth.assert_(passkey.elevate_challenge(ph.channel, ph.did, a.rid, a.pk_kind, a.rid, a.digest),
                                        b"0", nonce=b"0")
        return obj

    def run_card(self, host, s, tool, inp, answer=None, **kw):
        # P573 (#386 ⑤): Computer Use is a one-button card now; the 免问 pick (a standing grant) is offered only on a card
        # whose caller asks for Face ID, so these flows ask for it explicitly
        kw.setdefault("require_passkey", True)

        async def go():
            n = len(self.cards())
            t = asyncio.create_task(host.ask(tool, inp, batch=False, **kw))
            while len(self.cards()) == n and not t.done():
                await asyncio.sleep(0.01)
            if answer is not None and not t.done():
                obj = answer(self.cards()[-1])
                if obj is not None:
                    await host._app(s, obj)
            res = await t
            await asyncio.sleep(0.05)              # the post queue (ask_done, auto) drains
            return res
        return go()

    def test_unknown_mcp_write_uses_owner_card(self):
        host, ph, s = self.host_phone()
        res = asyncio.run(self.run_card(host, s, "mcp__x__click", {}, lambda ask: self.signed(ph, host, ask, ok=False)))
        # 0.30.7 ADR-0307-offlist: an unknown MCP tool is not on the card list, so the owner's turn runs it at once
        # without a card (audited as off_list); the owner's decline never gets a card to tap.
        self.assertEqual(res["behavior"], "allow")
        self.assertEqual(len(self.cards()), 0)
        self.assertEqual(approvals.read_log(self.st)[-1]["reason"], "off_list")

    def test_ask_done_why_timeout_and_denied(self):
        host, ph, s = self.host_phone()
        res = asyncio.run(self.run_card(host, s, CU, {}))
        self.assertEqual(self.cards("ask_done")[-1]["why"], "timeout")
        self.assertIn("设置 → Agent 与遥控器 → 本机 Face ID / 指纹", res["message"])
        asyncio.run(self.run_card(host, s, CU, {}, lambda ask: self.signed(ph, host, ask, ok=False)))
        self.assertEqual(self.cards("ask_done")[-1]["why"], "denied")
        self.assertEqual(self.cards("ask_done")[-1]["result"], "deny")

    def test_mail_offer_unchanged(self):
        host, ph, s = self.host_phone()
        res = asyncio.run(self.run_card(host, s, "agentj mail send", {"summary": "发邮件给：a@x.example\n标题：s"},
                                        lambda ask: self.signed(ph, host, ask, ["a@x.example"]),
                                        allow_offer=["a@x.example"]))
        self.assertEqual(self.cards()[-1]["allow_offer"], ["a@x.example"], "nothing added to a red-line card's offer")
        self.assertEqual((res["behavior"], res["allow_add"]), ("allow", ["a@x.example"]))



if __name__ == "__main__":
    unittest.main()
