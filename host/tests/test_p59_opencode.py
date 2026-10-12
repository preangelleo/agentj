"""P59 (ADR-A167): OpenCode auth diagnosis classes, owned-harness restart (credential change / CLI / proxy change) with the
conversation kept, and direct proxy values for harness children. A stand-in `opencode serve` (fakeopencode.py); no real
keys, no network."""
import _hermetic  # noqa: F401,I001
import asyncio
import io
import json
import os
import pathlib
import re
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import agent as agents  # noqa: E402
from agentj import agent_opencode as oc  # noqa: E402
from agentj import fence, preferences as prefs, proxy  # noqa: E402
from agentj.serve import Host  # noqa: E402
from test_l1 import Phone, _host, _ready, _state  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
AGENTJ = HERE.parent / "agentj"


class ProxyValues(unittest.TestCase):
    def test_validation_accepts_local_proxies_and_refuses_credentials(self):
        for url in ("http://127.0.0.1:7890", "socks5h://proxy.lan:1080", "https://[::1]:8443/", "SOCKS5://10.0.0.2:7891"):
            prefs.validate({"proxy": {"https": url}})
        secret = "pw-" + "q" * 12
        with self.assertRaises(prefs.ConfigError) as e:
            prefs.validate({"proxy": {"https": f"http://user:{secret}@127.0.0.1:7890"}})
        self.assertIn("proxy.https_env", e.exception.detail)
        self.assertNotIn(secret, json.dumps(e.exception.result()))
        with self.assertRaises(prefs.ConfigError) as e:
            prefs.validate({"proxy": {"http": f"user:{secret}@127.0.0.1:7890"}})
        self.assertIn("proxy.http_env", e.exception.detail)
        for bad in ("127.0.0.1:7890", "http://127.0.0.1", "ftp://h:21", "http://h:7890/path", "http://h:7890?x=1",
                    "http://h:99999", "http://bad host:1"):
            with self.assertRaises(prefs.ConfigError, msg=bad):
                prefs.validate({"proxy": {"https": bad}})
        prefs.validate({"proxy": {"no_proxy": "localhost,127.0.0.1,::1,.internal,10.0.0.0/8"}})
        for bad in ("a b", "x@y", ",".join(["h"] * 65)):
            with self.assertRaises(prefs.ConfigError, msg=bad):
                prefs.validate({"proxy": {"no_proxy": bad}})

    def test_value_wins_http_follows_both_spellings_and_loopback_bypass(self):
        doc = prefs.validate({"proxy": {"https": "http://127.0.0.1:7890", "https_env": "AJ_P"}})
        env = proxy.environment({"AJ_P": "http://other:1", "https_proxy": "http://stale:2", "PATH": "/bin"}, doc)
        for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
            self.assertEqual(env[k], "http://127.0.0.1:7890", k)
        self.assertEqual((env["NO_PROXY"], env["no_proxy"]), (proxy.LOOPBACK, proxy.LOOPBACK))
        # an existing NO_PROXY from the host stays; an explicit http / no_proxy value is kept apart
        env = proxy.environment({"NO_PROXY": "corp"}, prefs.validate({"proxy": {"https": "http://h:1"}}))
        self.assertEqual(env["NO_PROXY"], "corp")
        env = proxy.environment({}, prefs.validate({"proxy": {"https": "http://h:1", "http": "socks5://s:2",
                                                              "no_proxy": "localhost"}}))
        self.assertEqual((env["HTTP_PROXY"], env["NO_PROXY"]), ("socks5://s:2", "localhost"))
        # nothing configured: the host's environment exactly as it is
        base = {"https_proxy": "keep", "PATH": "/bin"}
        self.assertEqual(proxy.environment(base, prefs.defaults()), base)
        # a name-only http_env keeps P49 semantics (http does not follow https then)
        env = proxy.environment({"AJ_H": "http://n:3"}, prefs.validate({"proxy": {"https": "http://h:1", "http_env": "AJ_H"}}))
        self.assertEqual(env["HTTP_PROXY"], "http://n:3")

    def test_only_harness_launches_read_the_proxy(self):
        users = sorted(p.name for p in AGENTJ.glob("*.py")
                       if p.name != "proxy.py" and re.search(r"from \.proxy import environment", p.read_text()))
        self.assertEqual(users, ["agent.py", "agent_codex.py", "agent_opencode.py", "peer_session.py", "shared.py"])  # P71: friend turns
        for name in ("asr.py", "asr_worker.py", "voice.py", "update.py", "service.py"):
            self.assertNotIn("proxy.https", (AGENTJ / name).read_text(), name)

    def test_cli_config_set_and_alias_without_serve(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": d + "/cfg",
                                                                             "AGENTJ_STATE_DIR": d + "/state"}):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(prefs.command(["set", "代理", "http://127.0.0.1:7890", "--json"]), 0)
            res = json.loads(out.getvalue())
            self.assertEqual((res["key"], res["new"]), ("proxy.https", "http://127.0.0.1:7890"))
            secret = "pw-" + "z" * 12
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(prefs.command(["set", "proxy.https", f"http://u:{secret}@127.0.0.1:7890"]), 1)
            self.assertNotIn(secret, out.getvalue())
            self.assertIn("proxy.https_env", out.getvalue())
            self.assertNotIn(secret, prefs.path().read_text())
            self.assertEqual(prefs.get(prefs.effective(), "proxy.https"), "http://127.0.0.1:7890")
            hist = pathlib.Path(d, "state", "config-history")
            self.assertFalse(any(secret in p.read_text() for p in hist.glob("*")) if hist.exists() else False)


class Classes(unittest.TestCase):
    def test_provider_error_classes(self):
        r = oc.provider_error_reason
        for name, data, want in [
            ("APIError", {"statusCode": 401}, "login"), ("ProviderAuthError", {}, "login"),
            ("APIError", {"statusCode": 403, "responseBody": '{"error":{"code":"unsupported_country_region_territory"}}'}, "region"),
            ("APIError", {"statusCode": 400, "message": "User location is not supported for the API use."}, "region"),
            ("APIError", {"statusCode": 451}, "region"), ("APIError", {"statusCode": 403}, "access"),
            ("APIError", {"statusCode": 402}, "balance"),
            ("APIError", {"statusCode": 429, "message": "You exceeded your current quota", "responseBody": "insufficient_quota"}, "quota_window"),
            ("APIError", {"statusCode": 429}, "rate_limit"), ("ProviderModelNotFoundError", {}, "model"),
            ("APIError", {"statusCode": 404}, "model"), ("APIError", {"message": "proxy connection refused"}, "network"),
            ("APIError", {"message": "fetch failed"}, "network"), ("UnknownError", {}, "internal"),
            ("APIError", {"statusCode": 500}, "provider")]:
            self.assertEqual(r(name, data), want, (name, data))
        self.assertTrue(set(oc.PROVIDER_NOTES) >= {"login", "region", "access", "balance", "rate_limit", "model", "network",
                                                   "internal", "provider"})
        self.assertIn("自动重启", oc.PROVIDER_NOTES["login"])
        self.assertIn("proxy.https", oc.PROVIDER_NOTES["region"])

    def test_v2_detect_says_not_supported_not_logged_out(self):
        from agentj import harness
        with mock.patch("agentj.binaries.resolve", return_value={"path": "/var/tmp/opencode", "reason": "test"}), \
                mock.patch("agentj.binaries.installations", return_value=[]), \
                mock.patch("agentj.harness.version_of", return_value="opencode v2.0.23"):
            rec = harness._one("opencode")
        # P60: v2 is supported now (agent_opencode2); still never "not logged in" from a file's existence
        self.assertEqual((rec["installed"], rec["supported"], rec["logged_in"]), (True, True, None))
        self.assertIn("opencode auth switch", rec["note"])

    def test_fingerprint_reads_metadata_only(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_DATA_HOME": d, "XDG_CONFIG_HOME": d + "/c"}):
            a = oc.auth_fingerprint()
            p = pathlib.Path(d, "opencode")
            p.mkdir()
            (p / "auth.json").write_text('{"deepseek":{"type":"api","key":"placeholder"}}')
            b = oc.auth_fingerprint()
            self.assertNotEqual(a, b)
            self.assertEqual(b, oc.auth_fingerprint())
            self.assertNotIn("placeholder", repr(b))
            with mock.patch("builtins.open", side_effect=AssertionError("content must not be read")):
                oc.auth_fingerprint()

    def test_doctor_shows_last_failure_class(self):
        from agentj import doctor
        from agentj.state import State
        with tempfile.TemporaryDirectory() as d:
            st = State(pathlib.Path(d) / "state")
            st.init()
            cfg = {"kind": "opencode", "dir": d, "model": "deepseek/chat"}
            fail = {"known": True, "selected_connected": True, "last_failure": {"reason": "region", "http_status": 403}}
            with mock.patch.object(st, "agent_config", return_value=cfg), \
                    mock.patch("agentj.service.effective_binary_environment", return_value={}), \
                    mock.patch("agentj.binaries.resolve", return_value={"path": "/var/tmp/opencode", "wrapper": False, "reason": "test"}), \
                    mock.patch("agentj.doctor._version_of", return_value="1.18.32"), \
                    mock.patch("agentj.doctor._agent_bin", return_value="/var/tmp/opencode"), \
                    mock.patch("agentj.harness.opencode_login", return_value=("ok", "store hint")), \
                    mock.patch("agentj.names.ctl_call", return_value=fail):
                res = doctor.check_agent_cli(st, {})
            self.assertEqual(res["status"], "fail")
            self.assertIn("last turn failed: region (HTTP 403)", res["summary"])
            self.assertIn("代理", res["hint"])


class RestartUnits(unittest.IsolatedAsyncioTestCase):
    async def test_restart_waits_for_the_next_turn_and_never_touches_a_shared_harness(self):
        logs = []
        host = SimpleNamespace(st=SimpleNamespace(log=lambda ev, **kw: logs.append((ev, kw))))
        a = agents.Agent(host, {"kind": "x", "dir": "/tmp", "_workflow_ceo": True})
        a.kind = "x"
        a.proc = await asyncio.create_subprocess_exec("sleep", "30", start_new_session=True)
        self.assertTrue(a.owns_harness())
        self.assertEqual(a.request_restart("cli"), "next_turn")
        self.assertIsNone(a.proc.returncode, "nothing happens mid-turn")
        p = a.proc
        await a._identity_refresh()               # what Agent.run does right before the next turn
        self.assertIsNotNone(p.returncode)
        self.assertIsNone(a.proc)
        self.assertFalse(a.halting)
        self.assertIn(("agent_restart", {"agent": "x", "reason": "cli"}), logs)
        self.assertEqual(a.request_restart("cli"), "next_start")
        shared = agents.Agent(host, {"kind": "x", "dir": "/tmp", "_workflow_ceo": True, "session_mode": "shared"})
        shared.proc = None
        self.assertFalse(shared.owns_harness())

    async def test_proxy_change_schedules_owned_harness_restart_not_serve(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": d + "/cfg"}):
            st = _state(d)
            prefs.ensure()
            host = Host(st, read_stdin=False)
            host._send_ready = mock.AsyncMock()
            host.agent = mock.Mock(owns_harness=mock.Mock(return_value=True), request_restart=mock.Mock(return_value="next_turn"))
            res = await host.apply_preferences(prefs.edit(prefs.read()[0], "proxy.https", "http://127.0.0.1:7890"))
            self.assertTrue(res["ok"] and res["applied"])
            self.assertEqual((res["needs"], res["harness"]), ([], {"ok": True, "when": "next_turn"}))
            host.agent.request_restart.assert_called_once_with("proxy")
            host.agent.owns_harness.return_value = False
            res = await host.apply_preferences(prefs.edit(prefs.read()[0], "proxy.https", "http://127.0.0.1:7891"))
            self.assertEqual(res["needs"], ["restart the shared harness yourself"])
            res = await host.apply_preferences(prefs.edit(prefs.read()[0], "proxy.https", "http://u:p@h:1"))
            self.assertFalse(res["ok"])
            self.assertEqual(prefs.get(host.preferences, "proxy.https"), "http://127.0.0.1:7891")
            self.assertNotIn("proxy", host.preferences_msg()["value"], "host-only: never broadcast to phones")
            self.assertIn("proxy", host.preferences)
            host.agent = None
            self.assertEqual(host.restart_harness("cli"), {"ok": True, "when": "next_start"})

    def test_cli_agent_restart(self):
        from agentj import cli
        for res, want, code in [({"ok": True, "when": "next_turn"}, "下一条消息之前重启", None),
                                (None, "下一条消息启动时", None),
                                ({"ok": False, "when": "own", "error": "shared"}, "共享会话", 1)]:
            out = io.StringIO()
            with mock.patch("agentj.cli._need_init"), mock.patch("agentj.names.ctl_call", return_value=res) as call, \
                    redirect_stdout(out):
                try:
                    cli.main(["agent", "restart"])
                    got = None
                except SystemExit as e:
                    got, out = 1, io.StringIO(str(e.code))
            call.assert_called_once()
            self.assertEqual(call.call_args.args[1], {"cmd": "agent_restart"})
            self.assertEqual(got, code)
            self.assertIn(want, out.getvalue())


# ------------------------------------------------------------------ the chain with the stand-in `opencode serve`
class Chain(unittest.TestCase):
    shared = False

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        self.st = _state(self.tmp.name)
        self.work = root / "work"
        self.work.mkdir()
        self.log = self.work / ".oc.jsonl"
        self.envlog = self.work / ".oc-env.json"
        wrapper = root / "opencode"
        wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {HERE / 'fakeopencode.py'} \"$@\"\n")
        wrapper.chmod(0o700)
        self.data = root / "data"
        (self.data / "opencode").mkdir(parents=True)
        self.auth = self.data / "opencode" / "auth.json"
        self.auth.write_text('{"opencode":{"type":"api","key":"placeholder-one"}}')
        self.env = {"AGENTJ_OPENCODE_BIN": str(wrapper), "FAKE_OC_LOG": str(self.log), "FAKE_OC_ENV_LOG": str(self.envlog),
                    "FAKE_OC_STORE": str(self.work / ".oc-store.jsonl"), "AGENTJ_TEST_OC_WATCH": "1",
                    "XDG_DATA_HOME": str(self.data), "XDG_CONFIG_HOME": str(root / "cfg")}
        self.saved = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        fenced = fence.problem(self.st, str(self.work)) is None
        self.st.set_agent_config("opencode", str(self.work), fence=fenced)
        prefs.ensure()
        prefs.path().write_text(prefs.edit(prefs.read()[0], "proxy.https", "http://127.0.0.1:7890"))

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def run_chain(self, script):
        ph = Phone(self.st)
        sent = []
        host = _host(self.st, sent)
        if self.shared:
            host.agent_cfg["session_mode"] = "shared"
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

            async def whoami():
                before = len([m for m in msgs() if m.startswith("RUN-ID: ")])
                await say("WHOAMI")
                await wait(lambda: len([m for m in msgs() if m.startswith("RUN-ID: ")]) > before)
                await wait(lambda: host.agent.status == "idle")
                return [m for m in msgs() if m.startswith("RUN-ID: ")][-1]
            try:
                await script(SimpleNamespace(host=host, msgs=msgs, wait=wait, say=say, whoami=whoami))
            finally:
                host.stopping.set()
                await run
        asyncio.run(go())

    def env_of_serve(self):
        return json.loads(self.envlog.read_text())

    def test_key_change_restarts_owned_serve_same_conversation_and_cli_and_proxy(self):
        async def script(c):
            first = await c.whoami()
            sid = c.host.agent.sid
            self.assertEqual(self.env_of_serve()["HTTPS_PROXY"], "http://127.0.0.1:7890")
            self.assertEqual(self.env_of_serve()["NO_PROXY"], proxy.LOOPBACK)
            # 1. nothing changed → the same process answers
            self.assertEqual(await c.whoami(), first)
            # 2. `opencode auth login` replaced the key → restarted before the next message, the same session
            time.sleep(0.01)
            self.auth.write_text('{"opencode":{"type":"api","key":"placeholder-two-longer"}}')
            second = await c.whoami()
            self.assertNotEqual(second, first)
            self.assertEqual(c.host.agent.sid, sid)
            self.assertEqual(self.st.agent_session("opencode"), sid)
            # 3. the main Agent asks for it (`agentj agent restart` → ctl) → next message, same session
            self.assertEqual(c.host.restart_harness("cli"), {"ok": True, "when": "next_turn"})
            third = await c.whoami()
            self.assertNotEqual(third, second)
            self.assertEqual(c.host.agent.sid, sid)
            # 4. the owner (via the Agent) sets another proxy → the next serve gets it, same session
            res = await c.host.apply_preferences(prefs.edit(prefs.read()[0], "proxy.https", "socks5h://127.0.0.1:7891"))
            self.assertEqual(res["harness"], {"ok": True, "when": "next_turn"})
            self.assertNotEqual(await c.whoami(), third)
            self.assertEqual(self.env_of_serve()["HTTPS_PROXY"], "socks5h://127.0.0.1:7891")
            self.assertEqual(c.host.agent.sid, sid)
            # 5. a provider 401 → one phone line with the class; doctor sees the class, never the message
            await c.say("FAIL: 401 secret-provider-detail")
            await c.wait(lambda: any("模型与 Key" in m for m in c.msgs()))
            self.assertEqual(c.host.agent.last_provider_fail["reason"], "login")
            await c.say("hello")
            await c.wait(lambda: any(m == "ECHO: hello" for m in c.msgs()))
            await c.wait(lambda: c.host.agent.status == "idle")
            self.assertIsNone(c.host.agent.last_provider_fail, "a clean turn clears it")
        self.run_chain(script)
        log = self.st.log_path.read_text()
        self.assertIn('"reason": "credentials_changed"', log)
        self.assertNotIn("placeholder", log)
        self.assertNotIn("secret-provider-detail", log)
        posts = [json.loads(x) for x in self.log.read_text().splitlines()]
        self.assertEqual(len([r for r in posts if r["method"] == "POST" and r["path"] == "/session"]), 1,
                         "one conversation across four serve processes")


class SharedOwnedChain(Chain):
    """Default session_mode (shared) without an owner server: Agent J starts an ordinary `opencode serve` itself. A key
    change restarts that one, re-attaching the same ordinary session (before P59 a respawn asked for a port)."""
    shared = True

    def test_key_change_restarts_owned_serve_same_conversation_and_cli_and_proxy(self):
        async def script(c):
            first = await c.whoami()
            sid = c.host.agent.sid
            self.assertTrue(c.host.agent.owned_server and c.host.agent.owns_harness())
            time.sleep(0.01)
            self.auth.write_text('{"opencode":{"type":"api","key":"placeholder-two-longer"}}')
            second = await c.whoami()
            self.assertNotEqual(second, first)
            self.assertEqual(c.host.agent.sid, sid)
            self.assertEqual(c.host.restart_harness("cli"), {"ok": True, "when": "next_turn"})
            self.assertNotEqual(await c.whoami(), second)
            self.assertEqual(c.host.agent.sid, sid)
        self.run_chain(script)
        posts = [json.loads(x) for x in self.log.read_text().splitlines()]
        self.assertEqual(len([r for r in posts if r["method"] == "POST" and r["path"] == "/session"]), 1)

    def test_attached_owner_server_is_never_restarted(self):
        from agentj.shared import SharedOpenCodeAgent, _ExternalServer
        a = object.__new__(SharedOpenCodeAgent)
        a.cfg, a.owned_server, a.proc, a.auth_fp = {"session_mode": "shared"}, False, _ExternalServer(), ()
        self.assertFalse(a.owns_harness())
        asyncio.run(a._creds_check())             # returns at once: no pid, not ours
        self.assertIsInstance(a.proc, _ExternalServer)


if __name__ == "__main__":
    unittest.main()
