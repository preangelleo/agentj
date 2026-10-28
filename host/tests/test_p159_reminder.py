"""P159 (0.20.0): one hidden per-turn identity line on all three harnesses + core v11 (~10 % shorter) + INLINE_MAX 9500.

- main_identity.IDENTITY_REMINDER: the three rules models drift from in a long system prompt (address, delegate triage,
  language); attached owners (own identity) get only LANGUAGE_REMINDER. Never inside the hashed prompt.
- Claude owned: a UserPromptSubmit hook in the `--settings` serve already passes (printf of a fixed answer); shared: the
  same line on every main-session UserPromptSubmit (phone and desktop), never for a sub-Agent prompt.
- Codex: one developer item through `thread/inject_items` right before turn/start (owned: identity; shared: language).
- OpenCode v1: the synthetic last part (turn_body); v2: `POST …/synthetic {resume: false}` right before the prompt.
- The zh identity stays inline in a shared Claude session: prompt ≤ INLINE_MAX and the whole hook answer (provenance +
  identity + reminder) ≤ HOOK_MAX (Claude Code 2.1.296 swaps a longer additionalContext for a 2 KB preview + a path)."""
import _hermetic  # noqa: F401,I001
from _testtmp import TEST_TMP  # noqa: E402  P202: fixtures on tmpfs, not the system disk
import asyncio
import json
import os
from pathlib import Path
import pathlib
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import agent_codex, agent_opencode, main_identity, provenance, shared  # noqa: E402
from agentj.agent import ClaudeAgent, with_reminder  # noqa: E402


class Text(unittest.TestCase):
    def test_reminder_carries_the_three_drifting_rules(self):
        zh, en = main_identity.IDENTITY_REMINDER["zh"], main_identity.IDENTITY_REMINDER["en"]
        for needle in ("董事长", "先判三类", "派 CEO", "派子 Agent", "已交代给 X，有结论再报", "回到待命", "只用中文回复", "不要附英文译文",
                       "主人看不到", "不要提起"):
            self.assertIn(needle, zh)
        for needle in ('"Chairman"', "dispatch to the CEO", "sub-Agent", "Handed to X; I will report when there is a result",
                       "back to standby", "reply only in English", "hidden from the owner", "never mention it"):
            self.assertIn(needle, en)
        for lang in ("zh", "en"):
            self.assertLess(len(main_identity.IDENTITY_REMINDER[lang]), 600, "one line, not a second constitution")

    def test_main_or_attached_and_never_in_the_hashed_prompt(self):
        for lang in ("zh", "en"):
            cfg = {"language": lang}
            self.assertEqual(main_identity.turn_reminder(cfg), main_identity.IDENTITY_REMINDER[lang])
            self.assertEqual(main_identity.turn_reminder(cfg, False), main_identity.LANGUAGE_REMINDER[lang])
            self.assertNotIn(main_identity.IDENTITY_REMINDER[lang], main_identity.prompt(cfg))


class Core(unittest.TestCase):
    def test_v11_and_compressed(self):
        self.assertEqual(main_identity.VERSION, 23)                       # P244: v12 (@folder dispatch + /skill naming)
        self.assertEqual(main_identity.verify_core()["version"], 23)
        # P159: v10 was zh 4359 / en 13148 characters; the same rules ~10 % shorter
        # P186 added the chairman philosophy and moved bot / auto-update / long-task detail to their skills: zh stays ~flat
        # (the phone-turn additionalContext must stay inline under HOOK_MAX), en grows a little (it goes to the file anyway)
        # P244 (v12): + the @<folder>（CEO 名） dispatch rule and `/skill` naming — zh 3987 → 4098, en 12452 → 12727 (measured;
        # the zh prompt stays inline: 9049 ≤ INLINE_MAX), bounds raised by that much, to ratify
        # P253 v13 moves support consent into the pinned core; the shared hook cap remains unchanged.
        self.assertLessEqual(len((main_identity.DATA / "core.zh.md").read_text()), 4410)
        self.assertLessEqual(len((main_identity.DATA / "core.en.md").read_text()), 13980)

    def test_zh_stays_inline_under_the_claude_hook_cap(self):
        self.assertEqual((shared.SharedClaudeAgent.INLINE_MAX, shared.SharedClaudeAgent.HOOK_MAX), (9500, 10000))
        zh = main_identity.prompt({"language": "zh"}, harness="claude")   # P165: what the shared-Claude hook inlines (no Codex-only line)
        self.assertLessEqual(len(zh), shared.SharedClaudeAgent.INLINE_MAX)
        self.assertGreater(len(main_identity.prompt({"language": "en"})), shared.SharedClaudeAgent.INLINE_MAX)   # en → file


class SharedClaude(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(prefix="p159-", dir=TEST_TMP); self.addCleanup(self.t.cleanup)
        self.root = Path(self.t.name); os.chmod(self.root, 0o700)

    def agent(self, **cfg):
        host = Mock(); host.st.root = self.root; host.stopped.return_value = False
        host.st.write_private.side_effect = lambda p, b: (Path(p).write_bytes(b), os.chmod(p, 0o600))
        a = shared.SharedClaudeAgent(host, {"kind": "claude", "dir": str(self.root), "language": "zh", "session_mode": "shared", **cfg})
        a.session = {"sessionId": "owner", "pid": 1}
        return a

    def hook(self, a, **ev):
        return asyncio.run(a.hook_event({"session_id": "owner", **ev}))

    def ctx(self, out):
        return (out.get("hookSpecificOutput") or {}).get("additionalContext")

    def test_phone_turn_inline_identity_and_reminder_under_the_cap(self):
        a = self.agent()
        a.phone_turn, a.pending_text = True, "hi"
        c = self.ctx(self.hook(a, hook_event_name="UserPromptSubmit", prompt="hi"))
        self.assertIn("Agent J 主 Agent 宪法", c, "zh core inline (no Read step)")
        self.assertNotIn("shared-identity.md", c)
        self.assertTrue(c.endswith(main_identity.IDENTITY_REMINDER["zh"]))
        self.assertLessEqual(len(c), shared.SharedClaudeAgent.HOOK_MAX)
        self.assertEqual(a.identity_marker[3], "hook.additionalContext")
        # next phone turn: identity already read → only provenance + the reminder
        a.phone_turn, a.pending_text = True, "again"
        c = self.ctx(self.hook(a, hook_event_name="UserPromptSubmit", prompt="again"))
        self.assertNotIn("Agent J 主 Agent 宪法", c)
        self.assertTrue(c.startswith(provenance.hook_context(a.pending_source)))
        self.assertTrue(c.endswith(main_identity.IDENTITY_REMINDER["zh"]))

    def test_over_the_cap_the_identity_goes_to_the_file_and_the_reminder_still_rides(self):
        a = self.agent()
        a.HOOK_MAX = 9000          # what a long private-instructions file does to the real cap
        a.phone_turn, a.pending_text = True, "hi"
        c = self.ctx(self.hook(a, hook_event_name="UserPromptSubmit", prompt="hi"))
        self.assertIn("shared-identity.md", c)
        self.assertEqual(a.identity_marker[3], "hook.additionalContext+Read")
        self.assertTrue(c.endswith(main_identity.IDENTITY_REMINDER["zh"]))

    def test_desktop_turn_gets_the_line_subagents_and_unidentified_sessions_do_not(self):
        a = self.agent()
        self.assertEqual(self.ctx(self.hook(a, hook_event_name="UserPromptSubmit", prompt="typed on the desktop")),
                         main_identity.IDENTITY_REMINDER["zh"])
        self.assertIsNone(self.ctx(self.hook(a, hook_event_name="UserPromptSubmit", prompt="x", agent_id="sub-1")))
        for cfg in ({"shared_identity": False}, {"_workflow_ceo": True}):
            b = self.agent(**cfg)
            self.assertIsNone(self.ctx(self.hook(b, hook_event_name="UserPromptSubmit", prompt="typed")), cfg)

    def test_foreign_session_untouched(self):
        a = self.agent()
        self.assertEqual(asyncio.run(a.hook_event({"session_id": "other", "hook_event_name": "UserPromptSubmit", "prompt": "x"})), {})


class OwnedClaude(unittest.TestCase):
    def settings(self, **cfg):
        a = ClaudeAgent(SimpleNamespace(st=Mock()), {"kind": "claude", "dir": "/tmp", **cfg})
        argv = a.argv(None)
        return json.loads(argv[argv.index("--settings") + 1])

    def test_hook_prints_the_reminder_and_adds_nothing_else(self):
        for lang in ("zh", "en"):
            st = self.settings(language=lang)
            self.assertEqual(set(st), {"hooks", "disableAllHooks"}, "mandatory policy hook only; no permission or mode")
            self.assertIs(st["disableAllHooks"], False)
            self.assertEqual(set(st["hooks"]), {"PreToolUse", "UserPromptSubmit"})
            [entry] = st["hooks"]["UserPromptSubmit"]
            cmd = entry["hooks"][0]["command"]
            out = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True, check=True).stdout
            self.assertEqual(json.loads(out), {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                                      "additionalContext": main_identity.IDENTITY_REMINDER[lang]}})
            self.assertTrue(cmd.isascii(), "escaped JSON: no locale dependence")

    def test_danger_hook_kept_and_workflow_ceo_without_reminder(self):
        st = self.settings(language="zh", high_risk_warnings=True)
        self.assertIn("PreToolUse", st["hooks"]); self.assertIn("UserPromptSubmit", st["hooks"])
        self.assertIs(st["disableAllHooks"], False)
        ceo = self.settings(_workflow_ceo=True)
        self.assertEqual(set(ceo), {"hooks", "disableAllHooks"})
        self.assertEqual(set(ceo["hooks"]), {"PreToolUse"})
        self.assertIs(ceo["disableAllHooks"], False)
        self.assertEqual(with_reminder({"x": 1}, {"_workflow_ceo": True}), {"x": 1})


class Codex(unittest.TestCase):
    def agent(self, **cfg):
        host = SimpleNamespace(st=Mock())
        a = agent_codex.CodexAgent(host, {"kind": "codex", "dir": "/tmp", "language": "zh", **cfg})
        a.tid = "t1"
        a.call = AsyncMock(return_value={})
        return a

    def test_developer_item_main_and_language_only(self):
        for main, want in ((True, main_identity.IDENTITY_REMINDER["zh"]), (False, main_identity.LANGUAGE_REMINDER["zh"])):
            a = self.agent()
            asyncio.run(a.remind(main))
            method, params = a.call.await_args.args
            self.assertEqual(method, "thread/inject_items")
            self.assertEqual(params, {"threadId": "t1", "items": [
                {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": want}]}]})

    def test_unknown_method_only_logs(self):
        a = self.agent()
        a.call = AsyncMock(side_effect=agent_codex.RPCError({"code": -32601, "message": "unknown method"}))
        asyncio.run(a.remind(True))
        ev = a.host.st.log.call_args
        self.assertEqual(ev.args[0], "agent_reminder_fail")
        self.assertNotIn("Agent J", json.dumps(ev.kwargs), "metadata only")

    def test_shared_codex_sends_the_language_line(self):
        src = (Path(agent_codex.__file__).parent / "shared_codex.py").read_text()
        self.assertIn("await self.remind(False)", src)
        i = src.index("await self.remind(False)")
        self.assertLess(i, src.index("self.call('turn/start'", i), "before turn/start")


class OpenCodeV1(unittest.TestCase):
    def test_turn_body_last_part(self):
        b = agent_opencode.turn_body("hi", {"language": "en"}, True)
        self.assertEqual(b["parts"][-1], {"type": "text", "text": main_identity.IDENTITY_REMINDER["en"], "synthetic": True})
        b = agent_opencode.turn_body("hi", {"language": "en"}, False)
        self.assertEqual(b["parts"][-1]["text"], main_identity.LANGUAGE_REMINDER["en"])


import test_p57_agent as p57  # noqa: E402  (module import: the loader must not collect their TestCases here)
import test_p60_opencode as p60  # noqa: E402


def _own_tests_only(cls):
    for name in dir(cls):
        if name.startswith("test_") and name not in cls.__dict__:
            setattr(cls, name, None)          # inherited stand-in tests already run in their own module
    return cls


@_own_tests_only
class CodexChain(p57._Prep):
    KIND = "codex"

    def test_inject_items_right_before_every_turn_start(self):
        async def script(c):
            for t in ("一", "二"):
                await c["say"](t)
                await c["wait"](lambda t=t: any(m["text"] == f"ECHO: {t}" for m in c["msgs"]()))
                await c["idle"]()
            calls = [x for x in self.logged() if x.get("method") in ("thread/inject_items", "turn/start")]
            self.assertEqual([x["method"] for x in calls], ["thread/inject_items", "turn/start"] * 2)
            for x in calls[::2]:
                [item] = x["params"]["items"]
                self.assertEqual(item["role"], "developer")
                self.assertIn(item["content"][0]["text"], main_identity.IDENTITY_REMINDER.values())
            self.assertFalse(any(main_identity.IDENTITY_REMINDER["zh"] in m["text"] or main_identity.IDENTITY_REMINDER["en"] in m["text"]
                                 for m in c["msgs"]()), "never on the phone")
        self.run_chain(script)


@_own_tests_only
class OpenCodeV2(p60.V2Chain):
    def test_synthetic_right_before_the_prompt(self):
        async def script(c):
            await c.say("你好")
            await c.wait(lambda: "ECHO: 你好" in c.msgs())
            await c.idle()
            reqs = [r for r in self.requests() if r["m"] == "POST" and r["p"].endswith(("/synthetic", "/prompt"))]
            self.assertEqual([r["p"].rsplit("/", 1)[1] for r in reqs], ["synthetic", "prompt"])
            self.assertEqual(reqs[0]["b"], {"text": main_identity.turn_reminder(c.host.agent.cfg), "resume": False})
            self.assertEqual(reqs[1]["b"], {"text": "你好"})
            self.assertFalse(any("Agent J" in m and "提醒" in m for m in c.msgs()))
        self.run_chain(script)


if __name__ == "__main__":
    unittest.main()
