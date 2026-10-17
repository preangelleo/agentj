# Synthetic fixtures retain identical runtime bytes; compose them so static hygiene never treats them as shipped keys.
"""P329: DeepSeek Harness (`dsh --profile acp`, the engine inside Olares' Lares) as the sixth harness on P305's ACP adapter
(agent_dsh.py) against the stand-in fakeacp.py with dsh 0.2.0-rc.2's measured shapes (reports/qa/p329/): no ACP modes —
the process is started read-only + ask, so escalations come to the phone; tool titles mapped onto the danger list; the
model option's JSON-pair values shown as plain ids; the identity as text (no embedded resources); the process starts in
DSH_HOME, never the working folder (dsh loads `<process cwd>/.env`); the route file holds no key; tasks never get one."""
import _hermetic  # noqa: F401
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
FAKE = str(HERE / "fakeacp.py")
os.chmod(FAKE, os.stat(FAKE).st_mode | 0o111)   # the public export writes files without the exec bit; the stand-in must run
SENTINEL = ('sk-' + 'p329-sentinel-0123456789abcdef')

from agentj import acp, agent_dsh, fence, harness  # noqa: E402
from test_p305_kimi import AcpCase, FakeHost, FakeSt  # noqa: E402


class Route(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {"HOME": self.tmp.name, "DSH_HOME": os.path.join(self.tmp.name, ".dsh")}
        p = mock.patch("agentj.service.env_file", lambda name: os.path.join(self.tmp.name, "none.env"))
        p.start()
        self.addCleanup(p.stop)
        self.state = Path(self.tmp.name) / "state"
        self.state.mkdir()
        p = mock.patch("agentj.state.state_dir", lambda *a, **k: self.state)
        p.start()
        self.addCleanup(p.stop)

    def test_route_file_has_no_key_and_reads_back(self):
        p = agent_dsh.write_route("https://router.example.olares.com/v1/", "Olares/qwen-test:27b-q4",
                                  environ=self.env)
        self.assertEqual(oct(p.stat().st_mode & 0o777), "0o600")
        t = p.read_text()
        self.assertIn("apiKeyEnv: DSH_ROUTER_API_KEY", t)
        self.assertNotIn("sk-", t)
        self.assertEqual(agent_dsh.route(self.env), {"base_url": "https://router.example.olares.com/v1",
                                                     "model": "Olares/qwen-test:27b-q4",
                                                     "host": "router.example.olares.com"})
        self.assertEqual(agent_dsh.argv_for("dsh", self.env), ["dsh", "--profile", "acp", "--patch", str(p)])

    def test_context_window_from_the_servers_own_list(self):
        import http.server
        import threading
        seen = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append((self.path, self.headers.get("Authorization")))
                body = json.dumps({"data": [{"id": "other", "context_size": 4096},
                                            {"id": "Olares/Qwen3.8-27B", "context_size": 104448}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        base = f"http://127.0.0.1:{srv.server_port}/v1"
        self.assertEqual(agent_dsh.server_context(base, "Olares/Qwen3.8-27B", SENTINEL), 104448)
        self.assertEqual(seen[-1], ("/v1/models", "Bearer " + SENTINEL))
        self.assertIsNone(agent_dsh.server_context(base, "missing", None))
        self.assertIsNone(agent_dsh.server_context("http://127.0.0.1:9/v1", "m", None, timeout=2))
        p = agent_dsh.write_route(base, "Olares/Qwen3.8-27B", environ=self.env, context=104448)
        self.assertIn("contextWindow: 104448", p.read_text())
        self.assertEqual(agent_dsh.route(self.env)["model"], "Olares/Qwen3.8-27B")
        self.assertNotIn(SENTINEL, p.read_text())

    def test_redirects_never_carry_the_key(self):
        import http.server
        import threading
        seen = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append((self.path, self.headers.get("Authorization")))
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/elsewhere")
                self.end_headers()

            def log_message(self, *a):
                pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        self.assertIsNone(agent_dsh.server_context(f"http://127.0.0.1:{srv.server_port}/v1", "m", SENTINEL))
        self.assertEqual([p for p, _ in seen], ["/v1/models"])                  # not followed

    def test_a_server_that_wants_a_key_says_so(self):
        """P329 live (Olares Router): /models answers 401 without a key — the route is saved with needs_key, doctor warns
        with the exact key-card line, and the phone hint says the server refused (not "no route")."""
        import http.server
        import threading

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                ok = self.headers.get("Authorization") == "Bearer " + SENTINEL
                self.send_response(200 if ok else 401)
                self.end_headers()
                self.wfile.write(json.dumps({"data": [{"id": "m", "context_size": 8192}]}).encode() if ok else b"{}")

            def log_message(self, *a):
                pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        base = f"http://127.0.0.1:{srv.server_port}/v1"
        self.assertEqual(agent_dsh.server_probe(base, "m", None), (None, True))
        self.assertEqual(agent_dsh.server_probe(base, "m", "wrong"), (None, False))      # a key was sent: not "needs one"
        self.assertEqual(agent_dsh.server_probe(base, "m", SENTINEL), (8192, False))
        agent_dsh.write_route(base, "m", environ=self.env, needs_key=True)
        self.assertTrue(agent_dsh.saved_route()["needs_key"])
        st, where = agent_dsh.login(self.env)
        self.assertEqual(st, "warn")
        self.assertIn("agentj secret request --name DSH_ROUTER_API_KEY", where)
        self.assertIn(f"--verify-url {base}/models", where)
        with mock.patch("agentj.agent_dsh.fresh_keys", lambda base: {"DSH_ROUTER_API_KEY": SENTINEL}):
            st, where = agent_dsh.login(self.env)
        self.assertEqual(st, "ok")
        self.assertNotIn(SENTINEL, where)
        with mock.patch.dict(os.environ, self.env):
            for lang in ("zh", "en"):
                t = agent_dsh.login_text(lang, routed=True)
                self.assertIn("--dest 'env:", t)
                self.assertNotIn("还没有模型路线", t)
                self.assertNotIn("no model route", t)
        self.assertIsNone(agent_dsh.saved_route()["context"])
        self.assertIsNone(agent_dsh.prepare(self.env))                                       # no key yet: nothing asked
        self.assertIsNone(agent_dsh.saved_route()["context"])
        with mock.patch("agentj.agent_dsh.fresh_keys", lambda base: {"DSH_ROUTER_API_KEY": SENTINEL}):
            self.assertIsNone(agent_dsh.prepare(self.env))                                   # key card saved: asked once
        self.assertEqual(agent_dsh.saved_route()["context"], 8192)
        self.assertIn("contextWindow: 8192", agent_dsh.route_path(self.env).read_text())
        self.assertNotIn(SENTINEL, agent_dsh.route_path(self.env).read_text() + (self.state / "dsh-route.json").read_text())
        with mock.patch("agentj.agent_dsh.server_probe", side_effect=AssertionError("asked again")), \
                mock.patch("agentj.agent_dsh.fresh_keys", lambda base: {"DSH_ROUTER_API_KEY": SENTINEL}):
            self.assertIsNone(agent_dsh.prepare(self.env))
        agent_dsh.write_route(base, "m", environ=self.env)                                   # a keyless server
        self.assertFalse(agent_dsh.saved_route()["needs_key"])
        self.assertEqual(agent_dsh.login(self.env)[0], "ok")

    def test_route_source_of_truth_is_agentj_state(self):
        agent_dsh.write_route("https://router.example.com/v1", "m1", environ=self.env, context=4096)
        rp = agent_dsh.route_path(self.env)
        self.assertEqual(json.loads((self.state / "dsh-route.json").read_text())["base_url"], "https://router.example.com/v1")
        rp.write_text(rp.read_text().replace("router.example.com", "evil.example.net"))   # the fenced Agent rewrites it
        self.assertIsNone(agent_dsh.prepare(self.env))
        self.assertEqual(agent_dsh.route(self.env)["host"], "router.example.com")         # regenerated from the state copy
        self.assertIn("apiKeyEnv: DSH_DEEPSEEK_API_KEY", (Path(self.env["DSH_HOME"]) / "agentj-deepseek.yml").read_text())
        (self.state / "dsh-route.json").unlink()
        self.assertIn("no saved copy", agent_dsh.prepare(self.env))                     # a route nobody saved: refused

    def test_owner_layers_that_touch_approvals_refuse_the_start(self):
        h = Path(self.env["DSH_HOME"])
        (h / "profiles" / "acp").mkdir(parents=True)
        self.assertIsNone(agent_dsh.prepare(self.env))
        ok = ("# mine\n- id: llm-pi-ai\n  config:\n    providers:\n      x:\n        apiKeyEnv: X\n"
              "- id: agent-default-model\n  config:\n    provider: x\n")
        (h / "cordis.patch.yml").write_text(ok)
        self.assertIsNone(agent_dsh.config_problem(self.env))
        for bad, why in (("- id: approval\n  config:\n    policy: never\n", "row:approval"),
                         ("- id: sandbox-policy\n  config:\n    mode: danger-full-access\n", "row:sandbox-policy"),
                         ("- id: llm-pi-ai\n  config:\n    x: !!js process.exit(0)\n", "yaml_code_or_alias"),
                         ("- id: llm-pi-ai\n  disabled: true\n", "entry_key"),
                         ("- id: llm-pi-ai\n  name: '@evil/plugin'\n", "entry_key"),
                         ("- name: '@evil/plugin'\n", "entry_not_by_id"),
                         ("- id: acp\n  config: &a\n    x: 1\n", "yaml_code_or_alias"),
                         ("[{id: approval}]\n", "not_a_list")):
            (h / "cordis.patch.yml").write_text(bad)
            self.assertEqual(agent_dsh.config_problem(self.env), "cordis.patch.yml: " + why, bad)
        (h / "cordis.patch.yml").write_text("[]\n")
        (h / "profiles" / "acp" / "cordis.patch.yml").write_text("- id: approval\n  config:\n    policy: never\n")
        self.assertEqual(agent_dsh.config_problem(self.env), "profiles/acp/cordis.patch.yml: row:approval")
        (h / "profiles" / "acp" / "cordis.patch.yml").write_text("[]\n")
        pkg = {"name": "dsh-profile-acp", "dependencies": {}, "dsh": {"profile": {"bundles": agent_dsh.DEFAULT_BUNDLES}}}
        (h / "profiles" / "acp" / "package.json").write_text(json.dumps(pkg))
        self.assertIsNone(agent_dsh.config_problem(self.env))
        pkg["dependencies"] = {"dsh-plugin-autoapprove": "1.0.0"}
        (h / "profiles" / "acp" / "package.json").write_text(json.dumps(pkg))
        self.assertEqual(agent_dsh.config_problem(self.env), "profiles/acp/package.json: plugins")
        self.assertTrue(agent_dsh.own_facts(self.env)["loose"])

    def test_dsh_files_always_ask_the_phone(self):
        from agentj import danger
        for p in ("~/.dsh/cordis.patch.yml", "/srv/u/.dsh/profiles/acp/package.json", "~/.dsh/agentj-route.yml",
                  "~/.dsh/.credentials.yaml"):
            self.assertTrue(danger.hook_path(p), p)
        self.assertIsNone(danger.hook_path("~/.dsh/sessions/a.jsonl"))

    def test_bad_addresses_and_models_are_refused(self):
        for url, why in (("ftp://x/v1", "bad_url"), ("http://router.example.com/v1", "http_not_local"),
                         ("http://olares.local/v1", "http_not_local"),
                         ("https://u:p@x.example/v1", "bad_url"), ("https://x.example/v1?k=1", "bad_url"),
                         ("https://x.example/v1\n- id: evil", "bad_url")):
            with self.assertRaises(ValueError) as e:
                agent_dsh.write_route(url, "m", environ=self.env)
            self.assertEqual(str(e.exception), why)
        with self.assertRaises(ValueError):
            agent_dsh.write_route("https://x.example/v1", 'm"\n- id: evil', environ=self.env)
        for ok in ("http://127.0.0.1:11434/v1", "http://10.20.30.40:8080/v1", "https://olares.local/v1"):
            self.assertIsNone(agent_dsh.check_url(ok))
        self.assertIsNone(agent_dsh.route(self.env))

    def test_login_states_and_key_environment(self):
        self.assertEqual(agent_dsh.login(self.env)[0], "warn")
        self.assertEqual(agent_dsh.key_environment(self.env, source={}), {})
        env = {**self.env, "DEEPSEEK_API_KEY": SENTINEL}
        st, where = agent_dsh.login(env)
        self.assertEqual(st, "env")
        self.assertNotIn(SENTINEL, where)
        self.assertEqual(agent_dsh.key_environment(env, source=env), {"DSH_DEEPSEEK_API_KEY": SENTINEL})   # a DSH_* name:
        # dsh drops inherited DSH_* from the model's shell; its DeepSeek plugins read it through agentj-deepseek.yml
        self.assertEqual(agent_dsh.argv_for("dsh", env, {"DSH_DEEPSEEK_API_KEY": "x"})[-1],
                         str(Path(self.env["DSH_HOME"]) / "agentj-deepseek.yml"))
        h = Path(self.env["DSH_HOME"])
        h.mkdir()
        (h / ".credentials.yaml").write_text("deepseek: x\n")           # the owner's own dsh login wins
        self.assertEqual(agent_dsh.login(env)[0], "ok")
        self.assertEqual(agent_dsh.key_environment(env, source=env), {})
        agent_dsh.write_route("http://127.0.0.1:8080/v1", "qwen3.5:2b", environ=self.env)
        self.assertEqual(agent_dsh.key_environment(self.env, source={}), {"DSH_ROUTER_API_KEY": agent_dsh.NO_KEY})
        self.assertEqual(agent_dsh.key_environment(self.env, source={"DSH_ROUTER_API_KEY": SENTINEL}),
                         {"DSH_ROUTER_API_KEY": SENTINEL})
        st, where = agent_dsh.login(self.env)
        self.assertEqual(st, "ok")
        self.assertIn("127.0.0.1", where)

    def test_owner_files_are_facts_by_name(self):
        h = Path(self.env["DSH_HOME"])
        h.mkdir()
        (h / ".env").write_text(f"DEEPSEEK_API_KEY={SENTINEL}\n")
        (h / "cordis.patch.yml").write_text("- id: approval\n  config:\n    policy: never\n")
        f = agent_dsh.own_facts(self.env)  # noqa: F841 — loose = config_problem refuses it
        self.assertEqual(f, {"credentials": False, "dotenv_key": True, "providers": False, "loose": True})
        self.assertNotIn(SENTINEL, json.dumps(f))

    def test_fence_keeps_dsh_settings_not_secrets(self):
        env = fence.launch_environment({"PATH": "/usr/bin", "HOME": "/h", "DSH_HOME": "/h/.dsh", "DSH_ROUTER_API_KEY": SENTINEL,
                                        "DEEPSEEK_API_KEY": SENTINEL}, kind="dsh")
        self.assertEqual(env.get("DSH_HOME"), "/h/.dsh")
        self.assertNotIn(SENTINEL, json.dumps(env))


class DshAcp(AcpCase):
    def setUp(self):
        super().setUp()
        os.environ.update({"AGENTJ_DSH_BIN": FAKE, "FAKE_ACP_DSH": "1", "DSH_HOME": str(self.d / "home" / ".dsh"),
                           "DSH_PERMISSION_MODE": "danger-full-access"})          # the owner's shell says full access
        for k in ("DEEPSEEK_API_KEY", "DSH_ROUTER_API_KEY"):
            os.environ.pop(k, None)
        p = mock.patch("agentj.service.env_file", lambda name: str(self.d / "none.env"))
        p.start()
        self.addCleanup(p.stop)
        (self.d / "work" / ".env").write_text(f"DEEPSEEK_API_KEY={SENTINEL}\n")   # a project's keys in the working folder
        self.state = self.d / "state"
        self.state.mkdir()
        p = mock.patch("agentj.state.state_dir", lambda *a, **k: self.state)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch("agentj.agent_dsh.login", lambda environ=None: ("ok", "the owner's own dsh setup"))   # the stand-in needs none
        p.start()
        self.addCleanup(p.stop)

    def make(self, session=None, cfg=None, **host):
        st = FakeSt(session)
        st.sessions["dsh"] = session
        self.host = FakeHost(st, **host)
        self.host.set_model = lambda m: self.host.__dict__.setdefault("models_set", []).append(m)
        self.agent = agent_dsh.DshAgent(self.host, {"kind": "dsh", "dir": str(self.d / "work"), "fence": False, **(cfg or {})})
        return self.agent

    async def denv(self, a):
        await a.turn("DENV")
        return json.loads(self.host.texts[-1][6:])

    async def test_read_only_preset_telemetry_off_and_process_outside_the_working_folder(self):
        a = self.make()
        got = await self.denv(a)
        self.assertEqual((got["perm"], got["telemetry"]), ("read-only", "DISABLED"))
        self.assertEqual(os.path.realpath(got["cwd"]), os.path.realpath(os.environ["DSH_HOME"]))   # never the working folder
        self.assertEqual(self.sent("session/new")[0]["params"]["cwd"], str(self.d / "work"))     # the session still is
        self.assertFalse(got["deepseek_key"])
        self.assertEqual(got["argv"], ["--profile", "acp"])
        self.assertEqual(self.sent("session/set_mode"), [])        # dsh has no ACP modes: nothing to set, nothing failed

    async def test_identity_as_text_blocks(self):
        a = self.make()
        await a.turn("hello")
        first = self.sent("session/prompt")[0]["params"]["prompt"]
        self.assertEqual([b["type"] for b in first], ["text", "text", "text"])          # embeddedContext false
        self.assertIn("IDENTITY-TEXT", first[0]["text"])
        self.assertTrue(first[0]["text"].startswith("<agentj://identity>"))
        self.assertEqual(self.host.texts[-1], "ECHO: hello")

    async def test_escalation_goes_to_the_phone_as_bash_once(self):
        a = self.make()
        await a.turn("DTOOL: rm -rf build")
        tool, inp, _ = self.host.asked[0]
        self.assertEqual((tool, inp["command"]), ("Bash", "rm -rf build"))
        self.assertIn("PICKED: allow-once", self.host.texts[-1])
        await a.stop()
        b = self.make(allow=False)
        await b.turn("DTOOL: ls")
        self.assertIn("PICKED: reject-once", self.host.texts[-1])

    async def test_models_plain_ids_and_switch(self):
        a = self.make()
        await a.turn("hi")
        self.assertEqual([m["id"] for m in a.models_cache], ["Olares/Qwen3.8-27B", "deepseek-flash", "deepseek-v4-pro"])
        self.assertEqual(a.cur_model(), "Olares/Qwen3.8-27B")
        r = await a.cmd_model("deepseek-v4-pro")
        self.assertEqual(r.kind, "ok")
        opt = self.sent("session/set_config_option")[-1]["params"]
        self.assertEqual((opt["configId"], opt["value"]), ("model", '["deepseek-official","deepseek-v4-pro"]'))
        self.assertEqual(self.host.models_set, ["deepseek-v4-pro"])
        self.assertEqual((await a.cmd_model("gpt-6")).kind, "error")

    async def test_stored_model_applied_on_open(self):
        a = self.make(cfg={"model": "deepseek-flash"})
        await a.turn("hi")
        opt = self.sent("session/set_config_option")[0]["params"]
        self.assertEqual(opt["value"], '["deepseek-official","deepseek-flash"]')

    async def test_route_and_key_card_key_reach_dsh_only_in_memory(self):
        agent_dsh.write_route("https://router.example.olares.com/v1", "Olares/Qwen3.8-27B")
        os.environ["DSH_ROUTER_API_KEY"] = SENTINEL
        a = self.make()
        got = await self.denv(a)
        self.assertTrue(got["router_key"] is True)
        self.assertEqual(got["argv"][:3], ["--profile", "acp", "--patch"])
        self.assertNotIn(SENTINEL, self.log.read_text())
        self.assertNotIn(SENTINEL, agent_dsh.route_path().read_text())

    async def test_task_runner_never_gets_the_key(self):
        os.environ["DEEPSEEK_API_KEY"] = SENTINEL
        self.host = FakeHost(FakeSt())
        self.agent = agent_dsh.DshAgent(self.host, {"kind": "dsh", "dir": str(self.d / "work"), "fence": False}, persist=False)
        text, code = await self.agent.run_once("DENV")
        self.assertEqual(code, 0)
        got = json.loads(text[6:])
        self.assertEqual((got["deepseek_key"], got["perm"]), (False, "read-only"))
        agent_dsh.write_route("http://127.0.0.1:8080/v1", "qwen3.5:2b")       # a keyless local route: the placeholder only
        os.environ["DSH_ROUTER_API_KEY"] = SENTINEL
        self.agent = agent_dsh.DshAgent(self.host, {"kind": "dsh", "dir": str(self.d / "work"), "fence": False}, persist=False)
        os.environ.pop("DSH_ROUTER_API_KEY")       # the fence's filter drops it for tasks; here: not in serve's env at all
        text, code = await self.agent.run_once("DENV")
        self.assertEqual(json.loads(text[6:])["router_key"], "placeholder")
        self.agent = None

    async def test_cancel_compact_usage(self):
        a = self.make()
        t = asyncio.create_task(a.turn("SLEEP: 20"))
        for _ in range(100):
            if a.prompt_fut is not None:
                break
            await asyncio.sleep(.05)
        await a.interrupt_request(None)
        await asyncio.wait_for(t, 5)
        self.assertEqual(a.stop_reason, "cancelled")
        self.assertEqual((await a.cmd_compact("")).kind, "info")
        self.assertEqual(len(self.sent("session/prompt")), 1)          # /compact is not sent as a prompt
        self.assertIn("DeepSeek API", (await a.cmd_usage("")).text)

    async def test_no_route_no_key_is_a_login_hint_without_starting(self):
        a = self.make()
        with mock.patch("agentj.agent_dsh.login", lambda environ=None: ("warn", "none")):
            await a.turn("hi")
        self.assertIsNone(a.proc)
        self.assertEqual(self.sent("initialize"), [])
        self.assertTrue(any("DSH_ROUTER_API_KEY" in n for n in self.host.notices))

    async def test_missing_key_error_is_a_login_hint(self):
        a = self.make()
        await a.turn("hi")                                           # started (the stand-in needs no key)
        from agentj.acp import RPCError
        fut = asyncio.get_running_loop().create_future()
        fut.set_result({"error": {"code": -32603, "message": 'Internal error: turn failed: llm-deepseek: no API key for '
                                  'provider route "deepseek-official"; store DEEPSEEK_API_KEY …'}})
        with mock.patch.object(agent_dsh.DshAgent, "deliver", lambda self, fn: asyncio.sleep(0, fut)):
            self.host.notices.clear()
            await a.turn("again")
        self.assertTrue(any("DEEPSEEK_API_KEY" in n and "agentj agent dsh" in n for n in self.host.notices), self.host.notices)
        self.assertIsNotNone(RPCError)

    async def test_a_key_card_saved_while_dsh_runs_starts_it_again(self):
        """P329 live: dsh reads its environment once — the first message (no key yet) started it with the placeholder;
        after the key card the next message must reach a dsh that has the key, not the old process."""
        agent_dsh.write_route("https://router.example.olares.com/v1", "Olares/Qwen3.8-27B")
        a = self.make()
        got = await self.denv(a)
        self.assertEqual(got["router_key"], "placeholder")
        first = a.proc
        os.environ["DSH_ROUTER_API_KEY"] = SENTINEL                  # the key card (fresh_keys reads the env file)
        got = await self.denv(a)
        self.assertIs(got["router_key"], True)
        self.assertIsNot(a.proc, first)
        self.assertIn("agent_key_changed", [e for e, _ in self.host.st.logs])
        self.assertNotIn(SENTINEL, repr(self.host.st.logs) + self.log.read_text())
        again = a.proc
        await self.denv(a)
        self.assertIs(a.proc, again)                                 # same key: the same process

    async def test_unsafe_owner_layer_refuses_the_start(self):
        h = Path(os.environ["DSH_HOME"])
        h.mkdir(parents=True, exist_ok=True)
        (h / "cordis.patch.yml").write_text("- id: approval\n  config:\n    policy: never\n")
        a = self.make()
        await a.turn("hi")
        self.assertIsNone(a.proc)
        self.assertEqual(self.sent("initialize"), [])
        self.assertTrue(any("row:approval" in n for n in self.host.notices), self.host.notices)

    async def test_deepseek_key_reaches_dsh_under_a_dsh_name(self):
        os.environ["DEEPSEEK_API_KEY"] = SENTINEL
        a = self.make()
        got = await self.denv(a)
        self.assertTrue(got["deepseek_inner"])
        self.assertEqual(got["argv"][-1], str(Path(os.environ["DSH_HOME"]) / "agentj-deepseek.yml"))
        self.assertNotIn(SENTINEL, self.log.read_text())

    def test_fence_chdir_is_dsh_home(self):
        a = self.make()
        work = os.path.realpath(str(self.d / "work"))
        wrapped = ["bwrap", "--dev-bind", "/", "/", "--chdir", work, "--", "dsh", "--profile", "acp"]
        with mock.patch.object(acp.AcpAgent, "launch_argv", lambda self, argv: wrapped):
            out = a.launch_argv(["dsh", "--profile", "acp"])
        self.assertEqual(out[out.index("--chdir") + 1], os.path.realpath(os.environ["DSH_HOME"]))
        self.agent = None


class OlaresSsh(unittest.TestCase):
    """P329 live: Olares' sshd runs without PAM — SSH logins have no XDG_RUNTIME_DIR, the lingering user manager is still
    there at /run/user/<uid>/bus."""
    def test_runtime_dir_from_the_managers_own_socket(self):
        import stat
        from agentj import service
        uid = os.getuid()
        sock = mock.Mock(st_uid=uid, st_mode=stat.S_IFSOCK | 0o600)
        with mock.patch("agentj.service.platform", lambda: "linux"):
            with mock.patch("os.lstat", lambda p: sock if p == f"/run/user/{uid}/bus" else (_ for _ in ()).throw(OSError())):
                env = {}
                self.assertEqual(service.user_runtime_dir(env), f"/run/user/{uid}")
                self.assertEqual(env, {"XDG_RUNTIME_DIR": f"/run/user/{uid}"})
                env = {"XDG_RUNTIME_DIR": "/elsewhere"}                       # a login that has one keeps it
                self.assertIsNone(service.user_runtime_dir(env))
                self.assertEqual(env["XDG_RUNTIME_DIR"], "/elsewhere")
            other = mock.Mock(st_uid=uid + 1, st_mode=stat.S_IFSOCK | 0o600)  # someone else's socket: never
            with mock.patch("os.lstat", lambda p: other):
                self.assertIsNone(service.user_runtime_dir({}))
            with mock.patch("os.lstat", side_effect=OSError()):               # no user manager: nothing set
                env = {}
                self.assertIsNone(service.user_runtime_dir(env))
                self.assertEqual(env, {})


class DshProbe(unittest.TestCase):
    def test_probe_over_acp(self):
        with tempfile.TemporaryDirectory() as d, mock.patch("agentj.service.env_file", lambda name: os.path.join(d, "n.env")):
            env = {"PATH": os.environ.get("PATH", ""), "HOME": d, "DSH_HOME": os.path.join(d, ".dsh"), "FAKE_ACP_DSH": "1"}
            self.assertIn("ECHO", agent_dsh.probe(FAKE, env, timeout=20))

    def test_registered_everywhere(self):
        from agentj import binaries, state, switch_harness, taskspec, workflows, main_identity, docsrule
        self.assertIn("dsh", harness.SUPPORTED)
        self.assertIn("dsh", binaries.NAMES)
        self.assertIn("dsh", state.State.AGENT_KINDS)
        self.assertIn("dsh", switch_harness.KINDS)
        self.assertIn("dsh", taskspec.HARNESS_NAMES)
        self.assertEqual(main_identity.MECHANISMS["dsh"], "acp.text")
        self.assertEqual(docsrule.MEMORY_FILES["dsh"], "~/.dsh/AGENTS.md")
        self.assertTrue(any(p.endswith(".agents/skills") for p in workflows.global_dirs("dsh")))
        self.assertIn("dsh", switch_harness.LOGIN_HINT["zh"])
        self.assertIn("dsh", switch_harness.LOGIN_HINT["en"])
        self.assertIs(acp.AcpAgent, agent_dsh.DshAgent.__mro__[1])
        self.assertEqual(harness.LABEL["dsh"], "DeepSeek Harness")


if __name__ == "__main__":
    unittest.main()
