"""P305: Kimi Code CLI as the fourth harness — the generic ACP adapter (acp.py) against a stand-in ACP agent (fakeacp.py),
the Kimi specifics (agent_kimi.py: key variables, config facts, the tool-free probe), the device-code login rescue
(auth_rescue.KimiFlow), and the switch / route / task-spec plumbing."""
import _hermetic  # noqa: F401
import asyncio
import contextlib
import json
import os
import stat
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
FAKE = str(HERE / "fakeacp.py")
os.chmod(FAKE, os.stat(FAKE).st_mode | 0o111)   # the public export writes files without the exec bit; the stand-in must run

from agentj import acp, agent_kimi, auth_rescue, fence  # noqa: E402


class FakeSt:
    def __init__(self, session=None):
        self.sessions = {"kimi": session}
        self.logs = []

    def log(self, ev, **kw):
        self.logs.append((ev, kw))

    def agent_session(self, kind):
        return self.sessions.get(kind)

    def set_agent_session(self, kind, sid):
        self.sessions[kind] = sid


class FakeHost:
    def __init__(self, st, allow=True, pick=None):
        self.st, self.allow, self.pick = st, allow, pick
        self.texts, self.notices, self.asked, self.questions, self.meters = [], [], [], [], []
        self.lang = "zh"
        self.auth_rescue = mock.Mock()
        self.preferences = {}

    def agent_text(self, t):
        self.texts.append(t)

    def agent_notice(self, t):
        self.notices.append(t)

    def agent_status(self, s):
        pass

    def meter_update(self, **kw):
        self.meters.append(kw)

    async def ask(self, tool, inp, gone=None, batch=True, **kw):
        self.asked.append((tool, inp, batch))
        return {"behavior": "allow" if self.allow else "deny"}

    async def question(self, card, gone, task=None):
        self.questions.append(card)
        return ("answer", [[self.pick]]) if self.pick else ("timeout", None)


class AcpCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.log = self.d / "acp.jsonl"
        self.env = mock.patch.dict(os.environ, {"AGENTJ_KIMI_BIN": FAKE, "FAKE_ACP_LOG": str(self.log),
                                                "HOME": str(self.d / "home")})
        self.env.start()
        for k in [k for k in os.environ if k.startswith(("FAKE_ACP_AUTH", "FAKE_ACP_MODE", "FAKE_ACP_SESSIONS", "KIMI_",
                                                          "MOONSHOT_"))]:
            os.environ.pop(k)
        (self.d / "work").mkdir()
        self.patches = [mock.patch("agentj.main_identity.working_root", lambda cfg: Path(cfg["dir"])),
                        mock.patch("agentj.main_identity.prompt", lambda cfg, harness=None: "IDENTITY-TEXT"),
                        mock.patch("agentj.main_identity.turn_reminder", lambda cfg, main=True: "REMINDER"),
                        mock.patch("agentj.main_identity.audit", lambda *a, **k: None),
                        mock.patch("agentj.approval_jev.low_risk_pass", lambda *a, **k: False),
                        mock.patch.object(acp.AcpAgent, "launch_argv", lambda self, argv: argv)]
        for p in self.patches:
            p.start()

    async def asyncTearDown(self):
        a = getattr(self, "agent", None)
        if a is not None:
            await a.stop()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.env.stop()
        self.tmp.cleanup()

    def make(self, session=None, **host):
        st = FakeSt(session)
        self.host = FakeHost(st, **host)
        self.agent = agent_kimi.KimiAgent(self.host, {"kind": "kimi", "dir": str(self.d / "work"), "fence": False})
        return self.agent

    def sent(self, method=None):
        out = []
        for line in self.log.read_text().splitlines() if self.log.exists() else []:
            m = json.loads(line).get("in") or {}
            if method is None or m.get("method") == method:
                out.append(m)
        return out


class Turns(AcpCase):
    async def test_turn_text_in_one_piece_session_stored_identity_once(self):
        a = self.make()
        await a.turn("hello")
        self.assertEqual(self.host.texts, ["ECHO: hello"])          # chunks joined, sent once at the end of the turn
        sid = self.host.st.sessions["kimi"]
        self.assertTrue(sid and sid.startswith("sess-"))
        first = self.sent("session/prompt")[0]["params"]["prompt"]
        self.assertEqual([b["type"] for b in first], ["resource", "resource", "text"])
        self.assertEqual(first[0]["resource"]["text"], "IDENTITY-TEXT")
        await a.turn("again")
        second = self.sent("session/prompt")[1]["params"]["prompt"]
        self.assertEqual([b["type"] for b in second], ["resource", "text"])   # the reminder only
        self.assertEqual(second[0]["resource"]["text"], "REMINDER")
        self.assertEqual(self.host.texts[-1], "ECHO: again")
        init = self.sent("initialize")[0]["params"]["clientCapabilities"]
        self.assertEqual(init, {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False})
        self.assertTrue(any(m.get("ctx") == {"used": 1200, "max": 262144} for m in self.host.meters))

    async def test_session_forced_to_manual_mode(self):
        os.environ["FAKE_ACP_MODE"] = "auto"                     # the owner's default_permission_mode = auto
        a = self.make()
        await a.turn("hi")
        modes = self.sent("session/set_mode")
        self.assertEqual(modes[0]["params"]["modeId"], "default")
        await a.turn("YOLO")                                     # the agent switches itself to yolo → put back at once
        for _ in range(50):
            if len(self.sent("session/set_mode")) >= 2:
                break
            await asyncio.sleep(.05)
        self.assertEqual(self.sent("session/set_mode")[-1]["params"]["modeId"], "default")
        self.assertIn(("agent_mode_reset", {"agent": "kimi", "mode": "yolo"}), self.host.st.logs)

    async def test_resume_of_the_stored_session(self):
        os.environ["FAKE_ACP_SESSIONS"] = "sess-old"
        a = self.make(session="sess-old")
        await a.turn("hi")
        self.assertEqual(self.sent("session/resume")[0]["params"]["sessionId"], "sess-old")
        self.assertEqual(self.sent("session/new"), [])
        self.assertEqual(self.host.st.sessions["kimi"], "sess-old")

    async def test_gone_session_starts_a_new_one(self):
        a = self.make(session="sess-gone")
        await a.turn("hi")
        self.assertTrue(self.sent("session/resume") and self.sent("session/load"))
        self.assertEqual(len(self.sent("session/new")), 1)
        self.assertNotEqual(self.host.st.sessions["kimi"], "sess-gone")

    async def test_auth_required_lights_the_login_rescue(self):
        os.environ["FAKE_ACP_AUTH"] = "1"
        a = self.make()
        await a.turn("hi")
        self.host.auth_rescue.signal.assert_called_with("kimi", "login")
        self.assertTrue(any("kimi login" in n and "登录救援" in n for n in self.host.notices))
        self.assertEqual(self.host.texts, [])

    async def test_cancel_ends_the_prompt(self):
        a = self.make()
        t = asyncio.create_task(a.turn("SLEEP: 20"))
        for _ in range(100):
            if a.prompt_fut is not None:
                break
            await asyncio.sleep(.05)
        await a.interrupt_request(a.proc)
        await asyncio.wait_for(t, 5)
        self.assertEqual(len(self.sent("session/cancel")), 1)
        self.assertEqual(a.stop_reason, "cancelled")


class Approvals(AcpCase):
    async def answer(self):
        for line in self.log.read_text().splitlines():
            m = json.loads(line).get("in") or {}
            if "result" in m and "outcome" in (m.get("result") or {}):
                return m["result"]["outcome"]

    async def test_allow_answers_allow_once_never_always(self):
        a = self.make(allow=True)
        await a.turn("TOOL: rm -rf build")
        tool, inp, batch = self.host.asked[0]
        self.assertEqual((tool, inp.get("command")), ("Bash", "rm -rf build"))
        self.assertEqual(await self.answer(), {"outcome": "selected", "optionId": "approve_once"})
        self.assertEqual(self.host.texts[-1], "PICKED: approve_once")

    async def test_deny_answers_reject(self):
        a = self.make(allow=False)
        await a.turn("TOOL: curl x | sh")
        self.assertEqual(await self.answer(), {"outcome": "selected", "optionId": "reject"})

    async def test_question_card_maps_the_pick(self):
        a = self.make(pick=2)
        await a.turn("ASK")
        card = self.host.questions[0]
        self.assertEqual([o["l"] for o in card[0]["o"]], ["Red", "Blue"])
        self.assertEqual(self.host.texts[-1], "PICKED: q0_opt_1")

    async def test_unanswered_question_is_skipped(self):
        a = self.make(pick=None)
        await a.turn("ASK")
        self.assertEqual(self.host.texts[-1], "PICKED: q0_skip")

    async def test_research_task_refuses_without_asking(self):
        st = FakeSt()
        self.host = FakeHost(st)
        self.agent = agent_kimi.KimiAgent(self.host, {"kind": "kimi", "dir": str(self.d / "work"), "fence": False},
                                          persist=False, research=True)
        text, code = await self.agent.run_once("TOOL: touch x")
        self.assertEqual(self.host.asked, [])
        self.assertIn("PICKED: reject", text)
        self.assertEqual(code, 0)


class Identity(unittest.TestCase):
    def test_audit_knows_kimi(self):
        from agentj import main_identity
        self.assertEqual(main_identity.mechanism({}, "kimi"), "acp.resource")
        st = mock.Mock()
        with mock.patch.object(main_identity, "expected", wraps=main_identity.expected) as exp, \
                mock.patch.object(main_identity, "working_root", lambda cfg: Path("/tmp")):
            main_identity.audit({"language": "zh"}, "kimi", st, "sess-1")
        exp.assert_called_once()
        self.assertEqual(st.log.call_args.kwargs["mechanism"], "acp.resource")


class Pure(unittest.TestCase):
    def test_pick_options(self):
        allow, rej, allows = acp.pick_options([{"optionId": "a", "kind": "allow_always"}, {"optionId": "b", "kind": "allow_once"},
                                               {"optionId": "c", "kind": "reject_once"}])
        self.assertEqual((allow, rej, len(allows)), ("b", "c", 1))
        allow, rej, _ = acp.pick_options([{"optionId": "a", "kind": "allow_always"}, {"optionId": "r", "kind": "reject_always"}])
        self.assertEqual((allow, rej), (None, "r"))              # no "this once" option: never yes

    def test_tool_of_maps_kinds_and_paths(self):
        self.assertEqual(acp.tool_of("Write", "edit", {"path": "/a"}), ("Write", {"path": "/a", "file_path": "/a"}))
        self.assertEqual(acp.tool_of("run it", "execute", {"cmd": "ls"})[0], "Bash")
        self.assertEqual(acp.tool_of("run it", "execute", {"cmd": "ls"})[1]["command"], "ls")
        self.assertEqual(acp.tool_of("fetch page", "fetch", {"uri": "https://x"})[1]["url"], "https://x")

    def test_version_gate(self):
        self.assertTrue(agent_kimi.version_ok("kimi 2.2.0"))
        self.assertTrue(agent_kimi.version_ok("2.10.1"))
        self.assertFalse(agent_kimi.version_ok("2.1.9"))
        self.assertFalse(agent_kimi.version_ok(None))


class KimiEnv(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = {"HOME": str(self.home), "PATH": "/usr/bin"}

    def tearDown(self):
        self.tmp.cleanup()

    def kimi(self, *parts):
        p = self.home / ".kimi-code"
        p.mkdir(exist_ok=True)
        return p.joinpath(*parts)

    def test_key_variables_only_without_a_kimi_login(self):
        src = {"KIMI_API_KEY": "sk-test-a"}
        with mock.patch("agentj.agent_kimi.fresh_keys", lambda base: {k: base[k] for k in base if k.endswith("_API_KEY")}):
            out = agent_kimi.key_environment(self.env, "global", None, source=src)
            self.assertEqual(out["KIMI_MODEL_BASE_URL"], "https://api.kimi.ai/coding/v1")
            self.assertEqual(out["KIMI_MODEL_NAME"], "kimi-for-coding")
            out = agent_kimi.key_environment(self.env, None, None, source={"MOONSHOT_API_KEY": "sk-test-b"})
            self.assertEqual(out["KIMI_MODEL_BASE_URL"], "https://api.moonshot.cn/v1")
            (self.kimi("credentials")).mkdir()
            (self.kimi("credentials", "kimi-code.json")).write_text("{}")
            self.assertEqual(agent_kimi.key_environment(self.env, None, None, source=src), {})   # its own login wins
        self.assertEqual(agent_kimi.login(self.env)[0], "ok")

    def test_login_states(self):
        self.assertEqual(agent_kimi.login(self.env)[0], "warn")
        self.assertEqual(agent_kimi.login({**self.env, "KIMI_API_KEY": "x"})[0], "env")
        self.kimi("config.toml").write_text('[providers.mine]\ntype = "kimi"\n')
        self.assertEqual(agent_kimi.login(self.env)[0], "ok")

    def test_config_facts_names_only(self):
        self.kimi("config.toml").write_text(textwrap.dedent('''
            default_permission_mode = "auto"
            [permission]
            dangerous_command_guard = false
            [[permission.rules]]
            decision = "allow"
            pattern = "Bash(git *)"
            [[permission.rules]]
            decision = "deny"
            pattern = "Bash(rm *)"
        '''))
        f = agent_kimi.config_facts(self.env)
        self.assertEqual(f, {"providers": False, "allow_rules": 1, "default_mode": "auto", "guard_off": True})

    def test_child_env_forces_guard_and_fence_drops_secret_names(self):
        base = {"HOME": str(self.home), "PATH": "/usr/bin", "KIMI_CODE_HOME": str(self.home / "k"), "KIMI_API_KEY": "sk-x",
                "MOONSHOT_API_KEY": "sk-y"}
        env = fence.launch_environment(base, False, "kimi")
        self.assertEqual(env.get("KIMI_CODE_HOME"), str(self.home / "k"))
        self.assertNotIn("KIMI_API_KEY", env)
        self.assertNotIn("MOONSHOT_API_KEY", env)
        a = agent_kimi.KimiAgent.__new__(agent_kimi.KimiAgent)
        a.cfg = {"_workflow_ceo": True}
        self.assertEqual(a.child_env({})[agent_kimi.GUARD_ENV], "true")

    def test_login_text_names_both_sites(self):
        t = agent_kimi.login_text("global")
        self.assertIn("kimi login --region global", t)
        self.assertIn("kimi.com", t)
        self.assertIn("kimi.ai", t)
        self.assertIn("KIMI_API_KEY", t)


class Probe(unittest.TestCase):
    def test_probe_plan_mode_and_reply(self):
        with tempfile.TemporaryDirectory() as d:
            env = {"HOME": d, "PATH": os.environ["PATH"], "FAKE_ACP_LOG": str(Path(d) / "log")}
            self.assertEqual(agent_kimi.probe(FAKE, env, 20), "ECHO: " + agent_kimi.PROBE_PROMPT)
            sent = [json.loads(x)["in"] for x in (Path(d) / "log").read_text().splitlines()]
            self.assertIn({"sessionId": sent[2]["params"]["sessionId"], "modeId": "plan"},
                          [m["params"] for m in sent if m.get("method") == "session/set_mode"])
            self.assertEqual([m for m in sent if m.get("method") == "session/prompt"][0]["params"]["prompt"][0]["text"],
                             agent_kimi.PROBE_PROMPT)

    def test_probe_not_logged_in(self):
        with tempfile.TemporaryDirectory() as d:
            env = {"HOME": d, "PATH": os.environ["PATH"], "FAKE_ACP_AUTH": "1"}
            with self.assertRaises(agent_kimi.ProbeError) as e:
                agent_kimi.probe(FAKE, env, 20)
            self.assertEqual(e.exception.args[0], "not_logged_in")

    def test_probe_missing_binary(self):
        with self.assertRaises(agent_kimi.ProbeError) as e:
            agent_kimi.probe("/nonexistent/kimi", {"PATH": "/usr/bin"}, 5)
        self.assertEqual(e.exception.args[0], "native_executable_missing")


FAKE_LOGIN = """#!/bin/sh
# stand-in `kimi login`: the 2.2.0 lines on stderr, then writes a credential (exit $FAKE_RC)
echo "" >&2
echo "Opening browser for Kimi device login: https://www.kimi.com/code/authorize_device?user_code=ABCD-1234" >&2
echo "If the browser did not open, paste the URL above and enter code: ABCD-1234" >&2
echo "Code expires in 600s." >&2
echo "Waiting for authorization to complete…" >&2
sleep "${FAKE_SLEEP:-0.5}"
mkdir -p "$HOME/.kimi-code/credentials"
echo '{"new":1}' > "$HOME/.kimi-code/credentials/kimi-code.json"
echo 'new = 1' > "$HOME/.kimi-code/config.toml"
exit "${FAKE_RC:-0}"
"""


class LoginRescue(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.bin = self.home / "kimi"
        self.bin.write_text(FAKE_LOGIN)
        self.bin.chmod(self.bin.stat().st_mode | stat.S_IEXEC)
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.home), "AGENTJ_KIMI_BIN": str(self.bin)})
        self.env.start()
        os.environ.pop("KIMI_CODE_HOME", None)
        k = self.home / ".kimi-code"
        k.mkdir()
        (k / "config.toml").write_text("old = 1\n")

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_device_code_parsed_and_success_kept(self):
        f = auth_rescue.KimiFlow("global")
        url, code, ttl = f.start()
        self.assertEqual((url.split("?")[0], code, ttl), ("https://www.kimi.com/code/authorize_device", "ABCD-1234", 600))
        self.assertEqual(f.wait(), "ok")
        self.assertTrue((self.home / ".kimi-code/credentials/kimi-code.json").exists())
        f.restore()                                             # a failed live check: everything back as it was
        self.assertFalse((self.home / ".kimi-code/credentials/kimi-code.json").exists())
        self.assertEqual((self.home / ".kimi-code/config.toml").read_text(), "old = 1\n")

    def test_failed_login_restores(self):
        os.environ["FAKE_RC"] = "1"
        f = auth_rescue.KimiFlow(None)
        self.assertEqual(f.region, "mainland-cn")
        f.start()
        self.assertEqual(f.wait(), "failed")
        self.assertFalse((self.home / ".kimi-code/credentials/kimi-code.json").exists())
        self.assertEqual((self.home / ".kimi-code/config.toml").read_text(), "old = 1\n")

    def test_cancel_restores(self):
        import threading
        os.environ["FAKE_SLEEP"] = "5"
        f = auth_rescue.KimiFlow(None)
        f.start()
        ev = threading.Event()
        ev.set()
        self.assertEqual(f.wait(ev), "cancelled")
        self.assertEqual((self.home / ".kimi-code/config.toml").read_text(), "old = 1\n")

    def test_routes_and_expiry(self):
        self.assertIn("kimi", auth_rescue.KINDS)
        self.assertEqual(auth_rescue.expiry_kind("kimi", {}), "auto")
        self.assertEqual(auth_rescue.LABEL["kimi"], "Kimi Code")


class Plumbing(unittest.TestCase):
    def test_kinds_everywhere(self):
        from agentj import harness, switch_harness, state, taskspec, binaries, compactprep, market
        self.assertIn("kimi", harness.SUPPORTED)
        self.assertIn("kimi", switch_harness.KINDS)
        self.assertIn("kimi", switch_harness.LOGIN_HINT["zh"])
        self.assertIn("kimi", state.State.AGENT_KINDS)
        self.assertIn("kimi", binaries.NAMES)
        from agentj import capability, docsrule, handover, session_choice, workflows
        for table in (capability.HARNESS_TITLE, handover.AGENT_LABEL, session_choice.LABEL):
            self.assertEqual(table["kimi"], "Kimi Code")
        self.assertTrue(docsrule.memory_file("kimi").endswith("/.kimi-code/AGENTS.md"))
        self.assertTrue(any(d.endswith("/.agents/skills") for d in workflows.global_dirs("kimi")))
        spec = {"v": 1, "id": "x", "title": "t", "schedule": "0 9 * * 1", "tz": "UTC", "prompt_file": "p.md",
                "mode": "normal", "enabled": True, "needs": [], "outputs": ["reports/x.md"]}
        self.assertFalse([x for x in taskspec.problems({**spec, "harness": ["kimi", "codex"]}) if "harness" in x])
        self.assertFalse([x for x in taskspec.problems({**spec, "harness": "kimi"}) if "harness" in x])
        self.assertTrue([x for x in taskspec.problems({**spec, "harness": "gemini"}) if "harness" in x])
        a = mock.Mock(kind="kimi", cfg={}, persist=True, cmd_compact=lambda: None)
        self.assertTrue(compactprep.applies(a))
        st = mock.Mock()
        st.agent_config.return_value = {"kind": "kimi"}
        self.assertEqual(market.resolve_harness(None, st), "codex")   # Kimi reads ~/.agents/skills

    def test_cli_accepts_kimi_and_region(self):
        from agentj import cli
        seen = {}
        with mock.patch.object(cli, "cmd_agent", lambda a: seen.update(mode=a.mode, region=a.region)):
            cli.main(["agent", "kimi", "--region", "global"])
        self.assertEqual(seen, {"mode": "kimi", "region": "global"})

    def test_route_offers_kimi_cli(self):
        from agentj import ai_route
        res = ai_route.run(None, signals={"anthropic": "region", "openai": "region", "kimi": "ok"}, use_platform=False)
        self.assertEqual(res["route"], "kimi")
        self.assertEqual(res["harness_options"], ["opencode", "kimi"])
        for lang, needle in (("zh", "Kimi Code CLI"), ("en", "Kimi Code CLI")):
            text = ai_route.render(res, lang)
            self.assertIn(needle, text)
            self.assertIn("agentj agent switch kimi", text)
            self.assertIn("agentj agent switch opencode", text)     # the OpenCode route stays
        res = ai_route.run(None, signals={"anthropic": "ok", "openai": "ok", "kimi": "ok"}, use_platform=False)
        self.assertEqual(res["harness_options"], [])


if __name__ == "__main__":
    unittest.main()
