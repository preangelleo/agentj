"""P63: the DEFAULT settings (agent.session_mode = shared, no owner server) with the OpenCode the official installer ships (v2).

0.15.3a1 refused it: the shared session's owned ordinary server asked `opencode --version` and said 「OpenCode v2 的 /api 协议尚未
接入」 — only the independent mode (what P60's e2e ran) spoke v2. The host fixtures force the independent mode (test_l1._host),
so these chains put the default back on purpose: `State.agent_config()["session_mode"]` as a fresh install has it.

- SharedV2Chain: default config + v2 stand-in (fakeopencode2.py: every v1 route answers HTML) — message, card, /stop, /clear,
  key failures + restart, slash commands; shared native permissions kept; P67 owned fence and main identity;
- OwnerV2: an owner-started v2 server (`shared_opencode_port`) → attached since P64 (text, cards, desktop mirroring; never a
  new session / PATCH / model change / kill);
- SharedV1Owned: the same owned server on v1 now honours the model, /clear, /model and the key-failure restart (before P63 it
  ignored `--model` and refused every slash command).
No external network or real keys; a local HTTP fixture exercises subscription usage."""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
import pathlib
import socket
import subprocess
import sys
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import shared, shared_opencode2 as soc2  # noqa: E402
import test_p59_opencode as p59  # noqa: E402
import test_p60_opencode as p60  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent


def _default_mode_host(extra=None):
    """test_l1._host forces the independent mode; put back what a fresh install has (the default), plus `extra`."""
    def make(st, sent):
        host = ORIG_HOST(st, sent)
        default = st.agent_config()["session_mode"]
        assert default == "shared", default
        host.agent_cfg["session_mode"] = default
        host.agent_cfg.update(extra or {})
        return host
    return make


ORIG_HOST = p60._host


class _DefaultMode:
    extra: dict = {}

    def setUp(self):
        super().setUp()
        p60._host = _default_mode_host(self.extra)

    def tearDown(self):
        p60._host = ORIG_HOST
        super().tearDown()


class SharedV2Chain(_DefaultMode, p60.V2Chain):
    test_v2_message_card_stop_clear_and_failures = None     # (inherited fixtures only; those run in test_p60_opencode)
    test_v2_slash_commands = None

    def test_default_shared_v2_message_card_stop_clear_and_failures(self):
        async def script(c):
            await c.say("你好")
            await c.wait(lambda: "ECHO: 你好" in c.msgs())
            await c.idle()
            a = c.host.agent
            self.assertIs(type(a), soc2.SharedOpenCodeV2Agent)
            self.assertTrue(a.owned_server and a.owns_harness())
            reqs = self.requests()
            self.assertTrue(reqs and all(r["p"].startswith("/api/") for r in reqs), [r["p"] for r in reqs])
            created = [r for r in reqs if r["p"] == "/api/session" and r["m"] == "POST"]
            self.assertEqual(len(created), 1)
            self.assertEqual(created[0]["b"]["title"], "Agent J shared")
            self.assertEqual(created[0]["b"]["model"], {"id": "chat", "providerID": "deepseek"})
            self.assertNotIn("permissions", created[0]["b"], "shared: OpenCode's own rules, none of ours")
            self.assertTrue(any("/instructions/entries/agentj.identity" in r["p"] for r in reqs), "P67 owned shared identity")
            self.assertFalse(any(r["m"] == "PATCH" for r in reqs), "shared native permission rules stay")
            ev = [json.loads(x) for x in self.st.log_path.read_text().splitlines()]
            self.assertIn(True, [e.get("isolation_effective") for e in ev if e.get("ev") == "agent_start"], "P67 owned shared is fenced")
            self.assertIn("v2", [e.get("version") for e in ev if e.get("ev") == "agent_protocol"])
            self.assertFalse(any("尚未接入" in m for m in c.msgs()))
            sid = a.sid
            self.assertEqual((self.st.agent_session("opencode"), a.cfg["shared_session_id"]), (sid, sid))
            # OpenCode's own question → a card on the phone; approve runs it, deny tells the model
            await c.say("RUN: echo hi-shared")
            await c.wait(lambda: len(c.asks()) == 1)
            await c.host._app(c.s, c.ph.answer(c.asks()[-1], True))
            await c.wait(lambda: "OUT: hi-shared" in c.msgs())
            await c.idle()
            await c.say("RUN: echo no")
            await c.wait(lambda: len(c.asks()) == 2)
            await c.host._app(c.s, c.ph.answer(c.asks()[-1], False))
            await c.wait(lambda: any(m.startswith("REJECTED: ") for m in c.msgs()))
            await c.idle()
            # /stop during a long turn → interrupt; the next message works
            await c.say("SLEEP: 30")
            await c.wait(lambda: c.host.agent.status == "working")
            await asyncio.sleep(0.5)
            res = await c.host.stop_turn("phone")
            self.assertTrue(res.text.startswith("已停下这一轮"), res.text)
            await c.idle()
            self.assertTrue(any(r["p"].endswith("/interrupt") for r in self.requests()))
            # key failures: no key → names the provider, serve restarted, SAME ordinary session; 401 → phone Models & Key path
            before = await self._whoami(c)
            await c.say("NOKEY")
            await c.wait(lambda: any("「deepseek」的账户授权或 API Key 不可用" in m for m in c.msgs()))
            await c.idle()
            self.assertNotEqual(await self._whoami(c), before, "a fresh serve after a key failure")
            # the new serve is asked for the same ordinary session first (the stand-in keeps sessions in memory only, so it
            # answers 404 and a new one opens; the real OpenCode keeps them in its database — e2e_p60 --mode shared)
            self.assertGreaterEqual(len([r for r in self.requests() if r["p"] == f"/api/session/{sid}" and r["m"] == "GET"]), 2)
            previous_key_notices = sum("模型与 Key" in m for m in c.msgs())
            await c.say("BADKEY")
            await c.wait(lambda: sum("模型与 Key" in m for m in c.msgs()) > previous_key_notices)
            await c.idle()
            # /clear → a new ordinary session
            r = await c.host.agent.command("clear", "")
            self.assertTrue(r.undo, r.text)
            await c.say("after clear")
            await c.wait(lambda: "ECHO: after clear" in c.msgs())
            await c.idle()
            self.assertNotEqual(c.host.agent.sid, sid)
            self.assertEqual(c.host.agent.cfg["shared_session_id"], c.host.agent.sid)
            ev = [json.loads(x) for x in self.st.log_path.read_text().splitlines()]
            self.assertEqual([e["reason"] for e in ev if e.get("ev") == "agent_provider_fail"], ["no_key", "login"])
            self.assertEqual([e["reason"] for e in ev if e.get("ev") == "agent_restart"], ["provider_failure"] * 2)
        self.run_chain(script)

    def test_default_shared_v2_slash_commands(self):
        async def script(c):
            await c.say("hello")
            await c.wait(lambda: "ECHO: hello" in c.msgs())
            await c.idle()
            a = c.host.agent
            self.assertIn("上下文", (await a.command("context", "")).text)
            self.assertTrue((await a.command("cost", "")).text.startswith("本会话花费"))
            self.assertIn("OpenCode 2.0.23", (await a.command("status", "")).text)
            r = await a.command("model", "")
            self.assertEqual([m["id"] for m in r.models or []], ["opencode/chat"])
            r = await a.command("model", "opencode/chat")
            self.assertTrue(r.text.startswith("已切换到 opencode/chat"), r.text)
            await c.say("after switch")
            await c.wait(lambda: "ECHO: after switch" in c.msgs())
            await c.idle()
            switch = [x for x in self.requests() if x["p"].endswith("/model") and x["m"] == "POST"]
            self.assertEqual(switch[-1]["b"], {"model": {"id": "chat", "providerID": "opencode"}})
            r = await a.command("compact", "")
            self.assertTrue(r.text.startswith("已压缩"), r.text)
            self.assertNotIn("SUMMARY (not a reply)", c.msgs())
        self.run_chain(script)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class OwnerV2(_DefaultMode, p60.V2Chain):
    """P64: `shared_opencode_port` names a v2 server the OWNER started → attached: the named session only, text both ways,
    phone cards, desktop mirroring, the desktop's own approvals; never a new session, a PATCH, an identity, a model change or a
    kill. (Before P64: a plain notice only.)"""
    test_v2_message_card_stop_clear_and_failures = None
    test_v2_slash_commands = None

    def setUp(self):
        self.port = _free_port()
        self.extra = {"shared_opencode_port": self.port, "shared_session_id": ""}
        super().setUp()
        env = {k: v for k, v in os.environ.items() if not k.startswith("OPENCODE_SERVER_")}
        self.owner = subprocess.Popen([sys.executable, str(HERE / "fakeopencode2.py"), "serve", "--port", str(self.port)],
                                      cwd=str(self.work), env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.owner.stdout.readline()                    # "server listening on …"
        self.saved_pw = os.environ.pop("OPENCODE_SERVER_PASSWORD", None)
        sid = self.owner_call("POST", "/api/session", {"title": "mine", "location": {"directory": str(self.work)}})["data"]["id"]
        self.extra["shared_session_id"] = sid
        self.owner_sid = sid
        self.log.write_text("")                         # only what Agent J sends from here on

    def tearDown(self):
        if self.saved_pw is not None:
            os.environ["OPENCODE_SERVER_PASSWORD"] = self.saved_pw
        self.owner.kill()
        self.owner.wait()
        self.owner.stdout.close()
        super().tearDown()

    def owner_call(self, method, path, body=None):
        """The owner's own client (the desktop TUI) talking to the owner's server — not Agent J."""
        import base64
        import urllib.request
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method,
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"content-type": "application/json",
                                              "authorization": "Basic " + base64.b64encode(b"opencode:").decode()})
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
        return json.loads(raw) if raw else None

    def test_owner_v2_server_attached_text_cards_desktop_mirror(self):
        async def script(c):
            mirror = []
            h = c.host
            orig = (h.desktop_input, h.desktop_text, h.desktop_end)
            h.desktop_input = lambda t: (mirror.append(("in", t)), orig[0](t))
            h.desktop_text = lambda t: (mirror.append(("text", t)), orig[1](t))
            h.desktop_end = lambda: (mirror.append(("end",)), orig[2]())
            await c.say("你好")
            await c.wait(lambda: "ECHO: 你好" in c.msgs())
            await c.idle()
            a = c.host.agent
            self.assertIs(type(a), soc2.OwnerOpenCodeV2Agent)
            self.assertFalse(a.owned_server or a.owns_harness())
            self.assertEqual(a.sid, self.owner_sid)
            self.assertFalse(any(m for m in c.msgs() if "没关系" in m or "尚未接入" in m))
            # a card on the phone; approve runs it
            await c.say("RUN: echo hi-owner")
            await c.wait(lambda: len(c.asks()) == 1)
            await c.host._app(c.s, c.ph.answer(c.asks()[-1], True))
            await c.wait(lambda: "OUT: hi-owner" in c.msgs())
            await c.idle()
            # the owner types on the desktop: mirrored as desktop input + reply, not as a phone turn
            self.owner_call("POST", f"/api/session/{self.owner_sid}/prompt", {"text": "typed at the desk"})
            await c.wait(lambda: ("text", "ECHO: typed at the desk") in mirror)
            self.assertIn(("in", "typed at the desk"), mirror)
            await c.wait(lambda: mirror[-1] == ("end",))
            self.assertNotIn("ECHO: typed at the desk", c.msgs())
            # the desktop approves a request the phone was also shown: the card resolves, no tampering, still attached
            self.owner_call("POST", f"/api/session/{self.owner_sid}/prompt", {"text": "RUN: echo desk-ok"})
            await c.wait(lambda: len(c.asks()) == 2)
            pending = self.owner_call("GET", f"/api/session/{self.owner_sid}/permission")["data"]
            self.owner_call("POST", f"/api/session/{self.owner_sid}/permission/{pending[0]['id']}/reply", {"decision": "once"})
            await c.wait(lambda: ("text", "OUT: desk-ok") in mirror)
            await asyncio.sleep(0.3)
            self.assertIs(c.host.agent.proc is not None, True)
            # slash commands stay on the desktop
            r = await a.command("model", "opencode/chat")
            self.assertEqual(r.kind, "error")
            await c.say("again")
            await c.wait(lambda: "ECHO: again" in c.msgs())
            await c.idle()
        self.run_chain(script)
        reqs = self.requests()
        self.assertTrue(reqs and all(r["p"].startswith("/api/") for r in reqs), [r["p"] for r in reqs])
        agentj = [r for r in reqs if not (r["p"].endswith("/prompt") and r["b"] and r["b"].get("text") in
                                          ("typed at the desk", "RUN: echo desk-ok"))]
        self.assertFalse([r for r in agentj if r["p"] == "/api/session" and r["m"] == "POST"], "never a new session")
        self.assertFalse([r for r in agentj if r["m"] == "PATCH" or (r["m"] == "PUT" and not r["p"].endswith("/agentj.language"))], "owner keeps rules and identity; phone language entry only")
        self.assertFalse([r for r in agentj if r["p"].endswith("/model") and r["m"] == "POST"], "the owner's model stays")
        ev = [json.loads(x) for x in self.st.log_path.read_text().splitlines()]
        self.assertFalse([e for e in ev if e.get("ev") == "agent_tamper"], "the desktop is a legitimate approver")
        self.assertIsNone(self.owner.poll(), "the owner's server is never killed")

    def test_owner_v2_wrong_session_or_risk_layer_refused(self):
        self.extra["shared_session_id"] = "ses_not_there"
        async def script(c):
            await c.say("hi")
            await c.wait(lambda: any("不能附着" in m for m in c.msgs()))
        self.run_chain(script)
        self.assertFalse([r for r in self.requests() if r["m"] != "GET"], "nothing written to the owner's server")


    def test_owner_server_with_a_password_agentj_lacks_says_how_to_hand_it_over(self):
        port = _free_port()
        env = {**{k: v for k, v in os.environ.items() if not k.startswith("OPENCODE_SERVER_")}, "OPENCODE_SERVER_PASSWORD": "owner-pw-only-here"}
        locked = subprocess.Popen([sys.executable, str(HERE / "fakeopencode2.py"), "serve", "--port", str(port)],
                                  cwd=str(self.work), env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            locked.stdout.readline()
            self.extra["shared_opencode_port"] = port
            async def script(c):
                await c.say("hi")
                await c.wait(lambda: any("OPENCODE_SERVER_PASSWORD" in json.dumps(o, ensure_ascii=False) for _, o in c.sent))
                note = next(json.dumps(o, ensure_ascii=False) for _, o in c.sent if "OPENCODE_SERVER_PASSWORD" in json.dumps(o, ensure_ascii=False))
                self.assertIn("agentj secret request --name OPENCODE_SERVER_PASSWORD", note)
                self.assertIn("agentj service restart", note)
                self.assertNotIn("owner-pw-only-here", note)
            self.run_chain(script)
        finally:
            locked.kill(); locked.wait(); locked.stdout.close()


class SharedV1Owned(p59.Chain):
    """The default mode's owned server on v1: the owner's model on every prompt, /clear, /model, restart after a key failure."""
    shared = True
    test_key_change_restarts_owned_serve_same_conversation_and_cli_and_proxy = None

    def setUp(self):
        super().setUp()
        self.st.set_agent_config("opencode", str(self.work), model="opencode/big-pickle",
                                 fence=self.st.config()["agent"].get("fence", True))

    def test_owned_v1_model_clear_model_command_and_key_failure_restart(self):
        async def script(c):
            first = await c.whoami()
            a = c.host.agent
            self.assertIs(type(a), shared.SharedOpenCodeAgent)
            self.assertTrue(a.owned_server)
            sid = a.sid
            prompts = [r for r in self.posts() if r["path"].endswith("/prompt_async")]
            self.assertEqual(prompts[-1]["body"]["model"], {"providerID": "opencode", "modelID": "big-pickle"})
            # a provider 401 → the class on the phone, the owned serve restarted after the turn, same conversation
            await c.say("FAIL: 401 nope")
            await c.wait(lambda: c.host.agent.last_provider_fail is not None and c.host.agent.status == "idle")
            self.assertEqual(c.host.agent.last_provider_fail["reason"], "login")
            self.assertNotEqual(await c.whoami(), first)
            self.assertEqual(c.host.agent.sid, sid)
            # /model lists and switches; the next prompt carries it
            r = await c.host.agent.command("model", "")
            self.assertNotEqual(r.kind, "error", r.text)
            # /clear → a new ordinary session (before P63: 「共享模式目前仅验证文字投递」)
            r = await c.host.agent.command("clear", "")
            self.assertTrue(r.undo, r.text)
            await c.whoami()
            self.assertNotEqual(c.host.agent.sid, sid)
            self.assertEqual(self.st.agent_session("opencode"), c.host.agent.sid)
            ev = [json.loads(x) for x in self.st.log_path.read_text().splitlines()]
            self.assertIn("provider_failure", [e.get("reason") for e in ev if e.get("ev") == "agent_restart"])
        self.run_chain(script)
        titles = [r["body"].get("title") for r in self.posts() if r["path"] == "/session"]
        self.assertEqual(titles, ["Agent J shared"] * 2)

    def test_owned_v1_phone_language_context_and_subscription_reach_wire(self):
        import dataclasses
        import http.server
        import threading
        from unittest import mock
        from agentj import main_identity, opencode_provider, provider_runtime, __version__
        requests = []
        class Usage(http.server.BaseHTTPRequestHandler):
            def do_GET(inner):
                requests.append((inner.path, inner.headers.get('Authorization') == 'Bearer fixture-p67-usage', inner.headers.get('User-Agent'), inner.headers.get('Accept')))
                value = {'subscription': {'weekly_usage_usd': 2, 'weekly_limit_usd': 10, 'monthly_usage_usd': 4, 'monthly_limit_usd': 20}}
                body = json.dumps(value).encode()
                inner.send_response(200); inner.send_header('Content-Length',str(len(body))); inner.end_headers(); inner.wfile.write(body)
            def log_message(inner, *args):
                pass
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Usage)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        file=pathlib.Path(opencode_provider.config_file());file.parent.mkdir(parents=True,exist_ok=True)
        file.write_text(json.dumps({'provider':{'opencode':{'npm':'@ai-sdk/openai-compatible','options':{'baseURL':'https://fixture.invalid/v1','apiKey':'{env:P67_FAKE_USAGE_KEY}'},'models':{'big-pickle':{'name':'Big Pickle','limit':{'context':200000}}}}}}))
        try:
            async def script(c):
                c.host.language_changed('zh')
                session=next(iter(c.host.sessions.values()))
                c.host.sessions[12]=dataclasses.replace(session,cid=12,p33=True)
                meters=[]; original=c.host.send_app
                async def capture(s,obj):
                    if obj.get('t')=='meter': meters.append(obj)
                    return await original(s,obj)
                c.host.send_app=capture
                await c.say('hello')
                await c.wait(lambda:any('ECHO: hello' in x for x in c.msgs()))
                await c.wait(lambda:any(x.get('ctx')=={'used':8124,'max':200000} and len(x.get('quota_windows') or [])==2 for x in meters))
                prompts=[x['body'] for x in self.posts() if x['path'].endswith('/prompt_async')]
                self.assertEqual(prompts[-1]['model'],{'providerID':'opencode','modelID':'big-pickle'})
                self.assertEqual(prompts[-1]['system'],main_identity.prompt(c.host.agent.cfg))
                self.assertIn('只用中文',prompts[-1]['system'])
                self.assertNotIn('permissions',prompts[-1])
                self.assertEqual(requests[-1],('/v1/usage',True,'agentj/'+__version__,'application/json'))
                c.host.language_changed('en')
                await c.say('again')
                await c.wait(lambda:any('ECHO: again' in x for x in c.msgs()))
                prompts=[x['body'] for x in self.posts() if x['path'].endswith('/prompt_async')]
                self.assertIn('Reply to the owner only in English',prompts[-1]['system'])
                self.assertNotIn('只用中文',prompts[-1]['system'])
            with mock.patch.dict(os.environ,{'P67_FAKE_USAGE_KEY':'fixture-p67-usage'}), mock.patch.object(provider_runtime,'public_url',side_effect=lambda base,suffix:f'http://127.0.0.1:{server.server_port}/v1/{suffix}'):
                self.run_chain(script)
        finally:
            server.shutdown();server.server_close();thread.join()

    def posts(self):
        return [json.loads(x) for x in self.log.read_text().splitlines() if '"POST"' in x]


class Pieces(unittest.TestCase):
    def test_switch_keeps_the_shared_family(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        a = object.__new__(shared.SharedOpenCodeAgent)
        a.host, a.kind = SimpleNamespace(st=SimpleNamespace(log=Mock())), "opencode"
        self.assertTrue(a._switch("opencode v2.0.23"))
        self.assertIs(type(a), soc2.SharedOpenCodeV2Agent)
        self.assertFalse(a._switch("2.0.24"))
        self.assertTrue(a._switch("1.18.32"))
        self.assertIs(type(a), shared.SharedOpenCodeAgent)

    def test_owned_protocol_only_for_our_own_server(self):
        a = object.__new__(shared.SharedOpenCodeAgent)
        a.owned_server = False
        self.assertFalse(a._own_protocol())
        a.owned_server = True
        self.assertTrue(a._own_protocol())


if __name__ == "__main__":
    unittest.main()
