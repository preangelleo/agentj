"""P540: WeChat avatar on Codex — real executor path, visible failure reasons, actionable fallback text.

A stand-in `codex` (features list + exec --json, behaviour from a mode file) runs behind AGENTJ_CODEX_BIN through the
WeChat avatar's real execution chain: Sessions.turn → native Codex (unavailable here: no auth.json) → the fenced
PeerSessions Codex turn. Covers: plain text reply, a probe that passes while the turn fails, a CLI-contract failure,
a failed feature probe that must not be cached, an executor crash, and what `agentj wechat status` exposes.
"""
import asyncio
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _hermetic  # noqa: E402,F401
from agentj import fence, peer_guard, peer_session  # noqa: E402
from agentj.peer_session import PeerSessions, TurnResult  # noqa: E402
from agentj.state import State  # noqa: E402

FAKE = r'''
import json, os, sys
args = sys.argv[1:]
home = os.environ.get("HOME", "")
try:
    m = json.load(open(os.path.join(home, "codex-mode.json")))
except (OSError, ValueError):
    m = {}
if args[:2] == ["features", "list"]:
    if m.get("features_fail"):
        sys.exit(1)
    for f in ("shell_tool", "unified_exec", "apps", "plugins", "image_generation", "multi_agent", "view_image", "code_mode_host"):
        print(f"{f:40} stable             true")
    sys.exit(0)
sys.stdin.read()
with open(os.path.join(home, "codex-argv.jsonl"), "a") as fh:
    fh.write(json.dumps(args) + "\n")
def out(ev):
    print(json.dumps(ev), flush=True)
if m.get("crash"):
    print(m["crash"], file=sys.stderr)
    sys.exit(2)
out({"type": "thread.started", "thread_id": "0190aaaa-bbbb-7ccc-8ddd-eeeeffff0001"})
if m.get("notice"):
    out({"type": "error", "message": "Reconnecting... 1/5 (stream disconnected before completion)"})
out({"type": "turn.started"})
if m.get("fail"):
    out({"type": "turn.failed", "error": {"message": m["fail"]}})
    sys.exit(1)
out({"type": "item.completed", "item": {"id": "i1", "type": "agent_message",
                                        "text": '{"decision":"reply","text":"你好，我在","topic":""}'}})
out({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}})
'''


def fence_ok() -> bool:
    d = tempfile.mkdtemp(prefix="ajp540f-")
    try:
        st = State(pathlib.Path(d) / "s")
        st.root.mkdir()
        return fence.problem(st, d) is None
    finally:
        shutil.rmtree(d, ignore_errors=True)


class Parsing(unittest.TestCase):
    def test_notice_error_before_completed_reply_is_not_a_failure(self):
        out = "\n".join(json.dumps(e) for e in (
            {"type": "thread.started", "thread_id": "t"},
            {"type": "error", "message": "Reconnecting... 1/5 (stream disconnected before completion)"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "{}"}},
            {"type": "turn.completed"}))
        r = PeerSessions.parse_codex(out, "")
        self.assertIsNone(r["error"])
        self.assertEqual(r["text"], "{}")

    def test_notice_error_never_launders_a_tool_event(self):
        out = "\n".join(json.dumps(e) for e in (
            {"type": "error", "message": "Reconnecting... 1/5"},
            {"type": "item.started", "item": {"type": "command_execution", "command": "ls ~/.ssh"}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "{}"}},
            {"type": "turn.completed"}))
        r = PeerSessions.parse_codex(out, "")
        self.assertEqual(r["tools"], ["command_execution"])     # the turn still fails policy on its tool event

    def test_turn_failed_keeps_error_with_fixed_detail(self):
        for message, detail, error in (("You've hit your usage limit.", "quota", "harness"),
                                       ("401 Unauthorized", "login", "login"),
                                       ("The model `gpt-x` does not exist", "model", "harness"),
                                       ("something odd", "other", "harness")):
            out = "\n".join(json.dumps(e) for e in ({"type": "turn.started"},
                                                    {"type": "turn.failed", "error": {"message": message}}))
            r = PeerSessions.parse_codex(out, "")
            self.assertEqual((r["error"], r["detail"]), (error, detail), message)

    def test_error_without_completion_still_fails(self):
        r = PeerSessions.parse_codex(json.dumps({"type": "error", "message": "stream disconnected"}), "")
        self.assertEqual((r["error"], r["detail"]), ("harness", "network"))

    def test_failure_detail_never_echoes_text(self):
        secret = "sk-proj-" + "x" * 40
        self.assertIn(peer_session.failure_detail("weird " + secret), ("other",))
        self.assertEqual(peer_session.failure_detail("error: unexpected argument '--ignore-rules' found"), "cli_contract")
        noisy = "ERROR skills: Permission denied (os error 13)\n" * 5 + "Error: unexpected argument '--x' found\nUsage: codex exec"
        self.assertEqual(peer_session.failure_detail(noisy, tail=3), "cli_contract")


class ProbeCache(unittest.TestCase):
    def setUp(self):
        peer_session._probe.clear()
        self.addCleanup(peer_session._probe.clear)

    def test_failed_feature_probe_is_retried_not_cached(self):
        listing = "shell_tool                               stable             true\n"
        with mock.patch.object(peer_session, "_run_help", side_effect=["", listing]) as run:
            self.assertEqual(peer_session.codex_features("/x/codex"), set())
            self.assertEqual(peer_session.codex_features("/x/codex"), {"shell_tool"})
            self.assertEqual(peer_session.codex_features("/x/codex"), {"shell_tool"})   # success is cached
        self.assertEqual(run.call_count, 2)

    def test_failed_help_probes_are_not_cached(self):
        with mock.patch.object(peer_session, "_run_help", side_effect=["", "--tools --strict-mcp-config --mcp-config --system-prompt"]):
            self.assertEqual(peer_session.claude_flags("/x/claude"), set())
            self.assertIn("--tools", peer_session.claude_flags("/x/claude"))
        with mock.patch.object(peer_session, "_run_help", side_effect=["", "--agent --format --dir"]):
            self.assertEqual(peer_session.opencode_flags("/x/opencode"), set())
            self.assertIn("--dir", peer_session.opencode_flags("/x/opencode"))


class Copy(unittest.TestCase):
    def channel(self, lang, error, detail=None, kind="codex"):
        from agentj.wechat import WeChat
        ch = object.__new__(WeChat)
        ch.h = NS(lang=lang)
        ch.sessions = NS(cfg=lambda: {"kind": kind})
        ch.assistant_error, ch.assistant_detail = error, detail
        return ch

    def test_every_waiting_reason_says_why_and_what_next(self):
        cases = {("login", None): ("重新登录 Codex", "Sign in to Codex"),
                 ("harness", "quota"): ("额度", "quota"),
                 ("harness", "model"): ("模型", "model"),
                 ("no_harness", "not_installed"): ("找不到", "not installed"),
                 ("no_tool_off", "probe_failed"): ("升级 Codex", "Update Codex"),
                 ("timeout", None): ("超时", "timed out"),
                 ("harness", "exit_2"): ("harness · exit_2", "harness · exit_2"),
                 ("fence", "sandbox_failed"): ("fence · sandbox_failed", "fence · sandbox_failed"),
                 ("internal", "KeyError"): ("internal · KeyError", "internal · KeyError")}
        for (error, detail), (zh, en) in cases.items():
            z = self.channel("zh", error, detail).waiting_reply()
            e = self.channel("en", error, detail).waiting_reply()
            self.assertIn(zh, z, (error, detail))
            self.assertIn(en, e, (error, detail))
            self.assertNotIn("暂时不可用", z)
            self.assertNotIn("已转给", z)
            self.assertTrue(e.isascii() or "Agent J" in e, e)

    def test_generic_reason_drops_non_words(self):
        z = self.channel("zh", "harness", "/srv/me/secret path").waiting_reply()
        self.assertNotIn("/srv", z)
        self.assertIn("原因：harness", z)
        self.assertIn("agentj wechat status", z)


@unittest.skipUnless(fence_ok(), "bubblewrap / sandbox-exec not usable here")
class WeChatCodex(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from agentj.wechat import WeChat
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="ajp540-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        (self.home / ".codex").mkdir(parents=True)
        # P591 readonly parent views permit existing sibling writes, not new HOME entries.
        # Seed only the stand-in's output log; its execution still crosses the real fence.
        (self.home / "codex-argv.jsonl").write_text("")
        work = self.tmp / "work"
        work.mkdir()
        exe = self.tmp / "bin" / "codex"
        exe.parent.mkdir()
        exe.write_text(f'#!{shutil.which("python3")}\n' + FAKE)    # one file: an exe under /tmp is the only thing bound back
        exe.chmod(0o755)
        patcher = mock.patch.dict(os.environ, {"HOME": str(self.home), "AGENTJ_CODEX_BIN": str(exe), "LANG": "C.UTF-8"})
        patcher.start()
        self.addCleanup(patcher.stop)
        peer_session._probe.clear()
        self.addCleanup(peer_session._probe.clear)
        self.st = State(self.tmp / "state")
        self.st.init()
        self.host = NS(st=self.st, agent_cfg={"kind": "opencode", "dir": str(work)}, preferences={}, lang="zh",
                       stopped=lambda: False)
        self.ch = WeChat(self.host)
        # The owner chose Codex for the avatar while the main conversation stays on OpenCode (the customer's setup).
        self.ch.store.data["avatar"] = {"asked": True, "config": {"kind": "codex"}}
        self.ch.index = peer_guard.SecretIndex()
        self.mode({})

    async def asyncTearDown(self):
        self.ch.closed = True
        await self.ch.sessions.stop_all()
        self.ch.store.close()

    def mode(self, m):
        (self.home / "codex-mode.json").write_text(json.dumps(m))

    def argvs(self):
        p = self.home / "codex-argv.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    async def test_plain_text_reaches_codex_and_replies(self):
        self.mode({"notice": True})
        reply = await self.ch.assistant("你好")
        self.assertEqual(reply, "你好，我在")
        self.assertEqual(self.argvs()[-1][0], "exec")      # really executed by Codex, not another harness
        status = self.ch.status()["assistant"]
        self.assertEqual((status["ok"], status["error"], status["kind"]), (True, None, "codex"))

    async def test_probe_passes_but_turn_fails_is_visible_and_actionable(self):
        self.mode({"fail": "You've hit your usage limit. Upgrade or try again later."})
        self.assertIn("shell_tool", peer_session.codex_features(os.environ["AGENTJ_CODEX_BIN"]))   # the probe side passes
        self.assertIsNone(await self.ch.assistant("你好"))
        self.assertTrue(self.ch.assistant_waiting)
        status = self.ch.status()["assistant"]
        self.assertEqual((status["ok"], status["error"], status["detail"], status["kind"]), (False, "harness", "quota", "codex"))
        self.assertIn("额度", self.ch.waiting_reply())
        self.assertNotIn("You've hit", json.dumps(self.ch.status(), ensure_ascii=False))   # fixed words only

    async def test_cli_contract_break_names_the_stage(self):
        self.mode({"crash": "error: unexpected argument '--ignore-rules' found"})
        self.assertIsNone(await self.ch.assistant("你好"))
        status = self.ch.status()["assistant"]
        self.assertEqual((status["error"], status["detail"]), ("harness", "cli_contract"))
        self.assertIn("cli_contract", self.ch.waiting_reply())
        self.assertNotIn("--ignore-rules", json.dumps(self.ch.status()))

    async def test_failed_feature_probe_recovers_without_restart(self):
        self.mode({"features_fail": True})
        self.assertIsNone(await self.ch.assistant("你好"))
        self.assertEqual((self.ch.status()["assistant"]["error"], self.ch.status()["assistant"]["detail"]), ("no_tool_off", "probe_failed"))
        self.assertIn("升级 Codex", self.ch.waiting_reply())
        self.mode({})
        self.assertEqual(await self.ch.assistant("你好"), "你好，我在")
        self.assertTrue(self.ch.status()["assistant"]["ok"])

    async def test_executor_crash_waits_with_reason(self):
        async def boom(*_):
            raise KeyError("x")
        self.ch.model = boom
        self.assertIsNone(await self.ch.assistant("你好"))
        self.assertTrue(self.ch.assistant_waiting)
        self.assertEqual((self.ch.status()["assistant"]["error"], self.ch.status()["assistant"]["detail"]), ("internal", "KeyError"))

    async def test_status_keeps_only_words(self):
        async def odd(*_):
            return TurnResult("ask_owner", "", "", None, "harness", parsed=False, detail="path /srv/me")
        self.ch.model = odd
        await self.ch.assistant("你好")
        self.assertIsNone(self.ch.status()["assistant"]["detail"])


if __name__ == "__main__":
    unittest.main()
