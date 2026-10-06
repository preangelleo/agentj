"""P60: OpenCode ⇄ Agent J end to end — the root causes found with the real OpenCode 1.18.32 / 2.0.23 + OpenRouter
(reports/qa/p60/, the opt-in live suite agentjarvis/tests/e2e_p60_opencode_live.mjs), each pinned here without a model:

1. a provider without a key in serve's view → `UnknownError: Model not found` (v1) / `provider.no-route` (v2) was shown as
   「OpenCode 内部错误」 → now `no_key`, naming the provider;
2. serve keeps the provider state and model clients from its start → a fixed key kept failing until a manual restart →
   after a key / provider failure an owned serve ends once the turn is over;
3. OpenCode v2 (what the official installer ships now) was refused → agent_opencode2 speaks its /api protocol (the stand-in
   fakeopencode2.py answers every v1 route with HTML, like 2.0.23);
4. a third-party base URL + key → `agentj provider add` (names only, never the key).
No network, no real keys."""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import agent_opencode as oc  # noqa: E402
from agentj import agent_opencode2 as oc2  # noqa: E402
from agentj import fence, opencode_provider as ocp  # noqa: E402
from test_l1 import Phone, _host, _ready, _state  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent


# ------------------------------------------------------------------ classification (measured error shapes)
class Classify(unittest.TestCase):
    def test_v1_model_not_found_without_key_is_no_key(self):
        # what 1.18.32 sends when the provider has no key in serve's environment (measured with OpenRouter)
        data = {"message": "Model not found: openrouter/deepseek/deepseek-v4-flash. Did you mean: deepseek/…"}
        self.assertEqual(oc.provider_error_reason("UnknownError", data, ["opencode"], "openrouter"), "no_key")
        self.assertEqual(oc.provider_error_reason("UnknownError", data, ["opencode", "openrouter"], "openrouter"), "model")
        self.assertEqual(oc.provider_error_reason("UnknownError", data), "model")          # unknown connection: never no_key
        self.assertEqual(oc.provider_error_reason("ProviderModelNotFoundError", {}, [], "x"), "no_key")
        self.assertEqual(oc.provider_error_reason("UnknownError", {"message": "git probe failed"}), "internal")
        self.assertEqual(oc.provider_error_reason("APIError", {"statusCode": 401, "message": "User not found."}), "login")

    def test_v2_errors(self):
        auth = {"type": "provider.auth", "message": "User not found.", "status": 401, "response": {"body": "{}"}}
        noroute = {"type": "provider.no-route", "message": "Model unavailable: openrouter/deepseek/deepseek-v4-flash"}
        self.assertEqual(oc.provider_error_reason(*oc2.v2_error(auth)), "login")
        self.assertEqual(oc.provider_error_reason(*oc2.v2_error(noroute), ["opencode"], "openrouter"), "no_key")
        self.assertEqual(oc.provider_error_reason(*oc2.v2_error(noroute), ["openrouter"], "openrouter"), "model")
        self.assertEqual(oc.provider_error_reason(*oc2.v2_error({"type": "x", "status": 429})), "rate_limit")
        for r in oc.RESTART_AFTER:
            self.assertIn(r, oc.PROVIDER_NOTES)
        self.assertIn("{provider}", oc.PROVIDER_NOTES["no_key"])

    def test_session_error_names_provider_and_restarts_only_an_owned_serve(self):
        notices = []
        for owned in (True, False):
            a = object.__new__(oc.OpenCodeAgent)
            a.cfg = {"model": "openrouter/deepseek/deepseek-v4-flash", "session_mode": "independent" if owned else "shared"}
            a.sid, a.connected_at_start, a.provider_fail_noted, a.restart_after_turn = "ses_1", ["opencode"], False, False
            a.host = SimpleNamespace(st=SimpleNamespace(log=mock.Mock()), agent_notice=notices.append)
            a.on_event({"type": "session.error", "properties": {"sessionID": "ses_1", "error": {
                "name": "UnknownError", "data": {"message": "Model not found: openrouter/deepseek/deepseek-v4-flash"}}}})
            self.assertEqual(a.restart_after_turn, owned)
            self.assertEqual(a.last_provider_fail["reason"], "no_key")
        self.assertIn("openrouter", notices[0])
        self.assertNotIn("内部错误", notices[0])


class V2Pieces(unittest.TestCase):
    def test_rules_round_trip_shell_is_bash(self):
        v2 = [{"action": "*", "resource": "*", "effect": "allow"}, {"action": "shell", "resource": "rm *", "effect": "deny"}]
        v1 = oc2.rules_from_v2(v2)
        self.assertEqual(v1[1], {"permission": "bash", "pattern": "rm *", "action": "deny"})
        self.assertEqual(oc2.rules_to_v2(v1), v2)
        self.assertEqual(oc2.rules_from_v2([{"action": 1}, "x"]), [])
        # research: our rules (v1 logic) after the translation keep the human's deny and turn asks into denies
        r = oc2.rules_to_v2(oc.session_rules(v1, research=True))
        self.assertIn({"action": "shell", "resource": "rm *", "effect": "deny"}, r)
        self.assertNotIn("ask", {x["effect"] for x in r})

    def test_permission_request_shape(self):
        req = oc2.to_v1_request({"id": "per_1", "action": "shell", "resources": ["rm -rf x"], "save": ["rm *"]})
        self.assertEqual(oc.to_tool(req, "/w"), ("Bash", {"command": "rm -rf x"}))
        req = oc2.to_v1_request({"id": "per_2", "action": "read", "resources": [".env"]})
        self.assertEqual(oc.to_tool(req, "/w"), ("Read", {"file_path": "/w/.env"}))

    def test_forms_choices_only(self):
        raw, keys = oc2.form_questions({"title": "Pick", "fields": [
            {"key": "db", "type": "multiselect", "title": "Which DB?", "maxItems": 1,
             "options": [{"value": "pg", "label": "Postgres"}, {"value": "my", "label": "MySQL"}]}]})
        self.assertEqual(raw[0]["question"], "Which DB?")
        self.assertFalse(raw[0]["multiSelect"])
        self.assertEqual(keys[0][2]["Postgres"], "pg")
        self.assertEqual(oc2.form_questions({"fields": [{"key": "n", "type": "string"}]}), (None, []))

    def test_protocol_switch_and_cache(self):
        a = object.__new__(oc.OpenCodeAgent)
        a.host = SimpleNamespace(st=SimpleNamespace(log=mock.Mock()))
        with tempfile.NamedTemporaryFile() as exe, mock.patch("agentj.agent_opencode._bin", return_value=exe.name), \
                mock.patch("agentj.harness.version_of", return_value="opencode v2.0.23") as ver:
            self.assertTrue(asyncio.run(a._protocol()))
            self.assertIs(type(a), oc2.OpenCodeV2Agent)
            self.assertFalse(asyncio.run(a._protocol()))           # same executable, same mtime: no second --version
            self.assertEqual(ver.call_count, 1)
            os.utime(exe.name, ns=(1, time.time_ns() + 10**9))     # upgraded in place → asked again
            ver.return_value = "1.18.34"
            self.assertTrue(asyncio.run(a._protocol()))
            self.assertIs(type(a), oc.OpenCodeAgent)


# ------------------------------------------------------------------ agentj provider (F25)
class Provider(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {"XDG_CONFIG_HOME": self.tmp.name}
        self.file = pathlib.Path(self.tmp.name) / "opencode" / "opencode.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_list_remove_names_only(self):
        self.file.parent.mkdir(parents=True)
        self.file.write_text(json.dumps({"instructions": ["x.md"], "provider": {"other": {"npm": "y"}}}))
        r = ocp.add("myrelay", "https://api.example.com/v1/", ["deepseek/deepseek-v4-flash"], environ=self.env)
        cfg = json.loads(self.file.read_text())
        self.assertEqual(cfg["instructions"], ["x.md"])
        self.assertIn("other", cfg["provider"])
        b = cfg["provider"]["myrelay"]
        self.assertEqual((b["npm"], b["options"]["baseURL"], b["options"]["apiKey"]),
                         ("@ai-sdk/openai-compatible", "https://api.example.com/v1", "{env:MYRELAY_API_KEY}"))
        self.assertEqual(r["models"], ["myrelay/deepseek/deepseek-v4-flash"])
        self.assertTrue(r["next"][0].startswith("agentj secret request --name MYRELAY_API_KEY "))
        self.assertIn("--verify-url https://api.example.com/v1/models", r["next"][0])
        self.assertTrue((self.file.parent / "opencode.json.agentj-bak").exists())
        a = ocp.add("claude-relay", "https://relay.example/anthropic", ["claude-x"], api="anthropic", environ=self.env)
        self.assertEqual(json.loads(self.file.read_text())["provider"]["claude-relay"]["npm"], "@ai-sdk/anthropic")
        self.assertIn("x-api-key: {value}", a["next"][0])
        ls = ocp.listing({**self.env, "MYRELAY_API_KEY": "set"})
        me = next(p for p in ls if p["provider"] == "myrelay")
        self.assertEqual((me["key_env"], me["key_set_here"], me["inline_key"]), ("MYRELAY_API_KEY", True, False))
        self.assertTrue(ocp.remove("myrelay", self.env)["removed"])
        self.assertNotIn("myrelay", json.loads(self.file.read_text())["provider"])

    def test_refusals_leave_the_file_alone(self):
        for args in [("OpenRouter", "https://x/v1"), ("openrouter", "https://x/v1"), ("ok", "http://remote.example/v1"),
                     ("ok", "https://u:p@x/v1"), ("ok", "ftp://x"), ("ok", "https://x/v1?k=1")]:
            with self.assertRaises(ocp.ProviderError, msg=args):
                ocp.add(*args, ["m"], environ=self.env)
        with self.assertRaises(ocp.ProviderError):
            ocp.add("ok", "https://x/v1", [], environ=self.env)
        self.assertFalse(self.file.exists())
        ocp.add("local", "http://127.0.0.1:8080/v1", ["m"], dry_run=True, environ=self.env)
        self.assertFalse(self.file.exists())
        self.file.parent.mkdir(parents=True)
        self.file.write_text('{ // a comment\n "x": 1 }')
        with self.assertRaises(ocp.ProviderError):
            ocp.add("ok", "https://x/v1", ["m"], environ=self.env)
        self.assertEqual(self.file.read_text(), '{ // a comment\n "x": 1 }')


# ------------------------------------------------------------------ the v2 chain with the stand-in serve
class V2Chain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        self.st = _state(self.tmp.name)
        self.work = root / "work"
        self.work.mkdir()
        self.log = self.work / ".oc2.jsonl"
        wrapper = root / "opencode"
        wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {HERE / 'fakeopencode2.py'} \"$@\"\n")
        wrapper.chmod(0o700)
        self.env = {"AGENTJ_OPENCODE_BIN": str(wrapper), "FAKE_OC_LOG": str(self.log), "AGENTJ_TEST_OC_WATCH": "1",
                    "FAKE_OC2_CONNECTED": "opencode", "XDG_DATA_HOME": str(root / "data"),
                    "XDG_CONFIG_HOME": str(root / "cfg")}
        self.saved = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        fenced = fence.problem(self.st, str(self.work)) is None
        self.st.set_agent_config("opencode", str(self.work), model="deepseek/chat", fence=fenced)

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def requests(self):
        return [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []

    def run_chain(self, script, ttl=6):
        ph = Phone(self.st)
        sent = []
        host = _host(self.st, sent)
        host.ask_ttl = ttl
        oc.WATCH = 1.0

        def msgs():
            return [o["text"] for _, o in sent if o["t"] == "msg" and o.get("from") in ("agent", "notice")]

        async def wait(pred, ms=20000):
            t0 = time.monotonic()
            while not pred():
                if time.monotonic() - t0 > ms / 1000:
                    raise AssertionError(f"timeout; sent={sent[-6:]}\nlog={self.st.log_path.read_text()[-1500:]}")
                await asyncio.sleep(0.02)

        async def go():
            run = asyncio.create_task(host.run())
            await wait(lambda: host.agent is not None)
            s = _ready(host, ph)
            n = [0]

            async def say(text):
                n[0] += 1
                await host._app(s, {"t": "msg", "id": f"{n[0]:016x}", "text": text, "ts": 0})
            c = SimpleNamespace(host=host, sent=sent, msgs=msgs, wait=wait, say=say, s=s, ph=ph,
                                asks=lambda: [o for _, o in sent if o["t"] == "ask"],
                                idle=lambda: wait(lambda: host.agent.status == "idle"))
            try:
                await script(c)
            finally:
                host.stopping.set()
                await run
        asyncio.run(go())

    def test_v2_message_card_stop_clear_and_failures(self):
        async def script(c):
            # 1. a message on v2: the reply arrives; only /api routes were used; the identity rides as an instruction entry
            await c.say("你好")
            await c.wait(lambda: "ECHO: 你好" in c.msgs())
            await c.idle()
            self.assertIs(type(c.host.agent), oc2.OpenCodeV2Agent)
            paths = [r["p"] for r in self.requests()]
            self.assertTrue(paths and all(p.startswith("/api/") for p in paths), paths)
            self.assertTrue(any(p.endswith("/instructions/entries/agentj.identity") for p in paths))
            prompt = next(r for r in self.requests() if r["p"].endswith("/prompt"))
            self.assertEqual(prompt["b"], {"text": "你好"})
            created = next(r for r in self.requests() if r["p"] == "/api/session" and r["m"] == "POST")
            self.assertEqual(created["b"]["model"], {"id": "chat", "providerID": "deepseek"})
            sid = c.host.agent.sid
            # 2. a shell request → a card on the phone → approve → it runs; reject → the model is told
            await c.say("RUN: echo hi-v2")
            await c.wait(lambda: len(c.asks()) == 1)
            self.assertEqual((c.asks()[-1]["tool"], c.asks()[-1]["summary"]), ("Bash", "echo hi-v2"))
            await c.host._app(c.s, c.ph.answer(c.asks()[-1], True))
            await c.wait(lambda: "OUT: hi-v2" in c.msgs())
            await c.idle()
            await c.say("RUN: echo no")
            await c.wait(lambda: len(c.asks()) == 2)
            await c.host._app(c.s, c.ph.answer(c.asks()[-1], False))
            await c.wait(lambda: any(m.startswith("REJECTED: ") for m in c.msgs()))
            replies = [r for r in self.requests() if "/permission/" in r["p"]]
            self.assertEqual([r["b"]["decision"] for r in replies], ["once", "reject"])
            await c.idle()
            # 3. /stop during a long turn → POST …/interrupt; the next message works
            await c.say("SLEEP: 30")
            await c.wait(lambda: c.host.agent.status == "working")
            await asyncio.sleep(0.5)
            res = await c.host.stop_turn("phone")
            self.assertTrue(res.text.startswith("已停下这一轮"), res.text)
            await c.idle()
            self.assertTrue(any(r["p"].endswith("/interrupt") for r in self.requests()))
            self.assertNotIn("SLEPT", c.msgs())
            await c.say("still here")
            await c.wait(lambda: "ECHO: still here" in c.msgs())
            await c.idle()
            # 4. /clear → the next message opens a new v2 session
            r = await c.host.agent.command("clear", "")
            self.assertTrue(r.undo)
            await c.say("after clear")
            await c.wait(lambda: "ECHO: after clear" in c.msgs())
            await c.idle()
            self.assertNotEqual(c.host.agent.sid, sid)
            # 5. no key for the provider (v2: provider.no-route) → no_key notice naming it; serve restarted after the turn
            before = await self._whoami(c)
            await c.say("NOKEY")
            await c.wait(lambda: any("「deepseek」的账户授权或 API Key 不可用" in m for m in c.msgs()))
            await c.idle()
            self.assertNotEqual(await self._whoami(c), before, "a fresh serve after a key failure")
            # 6. 401 → single-language phone Models & Key path
            previous_key_notices = sum("模型与 Key" in m for m in c.msgs())
            await c.say("BADKEY")
            await c.wait(lambda: sum("模型与 Key" in m for m in c.msgs()) > previous_key_notices)
            await c.idle()
            ev = [json.loads(x) for x in self.st.log_path.read_text().splitlines()]
            self.assertIn("v2", [e.get("version") for e in ev if e.get("ev") == "agent_protocol"])
            self.assertEqual([e["reason"] for e in ev if e.get("ev") == "agent_provider_fail"], ["no_key", "login"])
            self.assertEqual([e["reason"] for e in ev if e.get("ev") == "agent_restart"], ["provider_failure"] * 2)
        self.run_chain(script)

    def test_v2_slash_commands(self):
        async def script(c):
            await c.say("hello")
            await c.wait(lambda: "ECHO: hello" in c.msgs())
            await c.idle()
            a = c.host.agent
            r = await a.command("context", "")
            self.assertIn("上下文", r.text)
            self.assertIn("deepseek/chat", r.text)
            r = await a.command("cost", "")
            self.assertTrue(r.text.startswith("本会话花费"), r.text)
            r = await a.command("status", "")
            self.assertIn("OpenCode 2.0.23", r.text)
            r = await a.command("model", "")
            ids = [m["id"] for m in r.models or []]
            self.assertEqual(ids, ["opencode/chat"], "only providers with a key are offered (fixture: opencode only)")
            r = await a.command("model", "opencode/chat")
            self.assertTrue(r.text.startswith("已切换到 opencode/chat"), r.text)
            await c.say("after switch")
            await c.wait(lambda: "ECHO: after switch" in c.msgs())
            await c.idle()
            switch = [x for x in self.requests() if x["p"].endswith("/model") and x["m"] == "POST"]
            self.assertEqual(switch[-1]["b"], {"model": {"id": "chat", "providerID": "opencode"}})
            r = await a.command("compact", "")
            self.assertTrue(r.text.startswith("已压缩"), r.text)
            self.assertNotIn("SUMMARY (not a reply)", c.msgs(), "the compaction summary is not a reply")
            self.assertTrue(any(x["p"].endswith("/compact") for x in self.requests()))
        self.run_chain(script)

    async def _whoami(self, c):
        n = len([m for m in c.msgs() if m.startswith("RUN-ID: ")])
        await c.say("WHOAMI")
        await c.wait(lambda: len([m for m in c.msgs() if m.startswith("RUN-ID: ")]) > n)
        await c.idle()
        return [m for m in c.msgs() if m.startswith("RUN-ID: ")][-1]


if __name__ == "__main__":
    unittest.main()
