"""P151 追加 1/2 (owner report 2026-10-12): after a compaction (or a /clear with a clear handoff) the shared Claude session
must recover by itself — the host sends one 〔Agent J 系统提示〕 message pointing at a human-readable handoff path — instead of
waiting for the owner's next message. The phone /compact first asks for the handoff when it is missing or stale."""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
import socket
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from agentj import compactprep, handoff, privacy, provenance, shared

SID = "15115115-1511-1511-1511-15115115aaaa"


class Host:
    lang = "zh"

    def __init__(self):
        self.st = Mock()
        self.desktop, self.phone, self.notices, self.ends = [], [], [], 0

    def desktop_input(self, text): pass
    def desktop_text(self, text): self.desktop.append(text)
    def desktop_end(self): self.ends += 1
    def desktop_cmd_done(self, name): pass
    def proactive_turn(self, via): pass
    def agent_text(self, text): self.phone.append(text)
    def agent_notice(self, text): self.notices.append(text)
    def agent_status(self, status): pass
    def stopped(self): return False
    def turn_failed(self): pass
    def turn_queued_note(self, on): pass
    def shared_clear(self): return True

    def logged(self, ev):
        return [c.kwargs for c in self.st.log.call_args_list if c.args and c.args[0] == ev]


class Handoff(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p151h-")
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.work = root / "work"
        self.work.mkdir()
        cc = root / "claude"
        (cc / "sessions").mkdir(parents=True)
        env = patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(cc), "AGENTJ_STATE_DIR": str(root / "state")})
        env.start()
        self.addCleanup(env.stop)
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.bind(str(root / "peer.sock"))
        self.addCleanup(self.sock.close)
        self.session = {"pid": os.getpid(), "cwd": str(self.work), "sessionId": SID, "kind": "interactive",
                        "messagingSocketPath": str(root / "peer.sock"), "status": "idle", "updatedAt": 10}
        self.registry = cc / "sessions" / "owner.json"
        self.registry.write_text(json.dumps(self.session))
        self.host = Host()
        for target, attr, val in ((shared.SharedClaudeAgent, "prepare_channel", AsyncMock()),
                                  (handoff, "RECOVER_DELAY", 0.05)):
            p = patch.object(target, attr, val)
            p.start()
            self.addCleanup(p.stop)
        self.sent = []
        p = patch.object(shared, "claude_send", side_effect=lambda s, text, src=None: self.sent.append((text, src)))
        p.start()
        self.addCleanup(p.stop)
        self.a = shared.SharedClaudeAgent(self.host, {"kind": "claude", "dir": str(self.work), "_workflow_ceo": True,
                                                      "session_mode": "shared", "high_risk_warnings": False})
        self.a.session = dict(self.session)
        self.a.loop = asyncio.get_running_loop()

    async def ev(self, name, **kw):
        return await self.a.hook_event({"hook_event_name": name, "session_id": SID, **kw})

    def write_handoff(self, name="compact.md", text="# 交接\n- 下一步：继续画设计稿\n"):
        handoff.ensure()
        handoff.path(name).write_text(text)

    async def settle(self, t=0.5):
        await asyncio.sleep(t)

    async def test_compact_then_silence_the_host_sends_the_recovery_itself(self):
        await self.ev("UserPromptSubmit", prompt="继续")
        await self.ev("Stop", last_assistant_message="好")
        self.write_handoff()
        await self.ev("PreCompact", trigger="manual")
        await self.ev("SessionStart", source="compact")
        await self.ev("PostCompact", trigger="manual")
        await self.settle()
        self.assertEqual(len(self.sent), 1, "PostCompact + SessionStart(compact) = one recovery")
        text, src = self.sent[0]
        self.assertTrue(text.startswith("〔Agent J 系统提示〕压缩完成。先读交接文件 "), text)
        self.assertIn(str(handoff.path("compact.md")).replace(os.path.expanduser("~"), "~"), text)
        self.assertEqual(provenance.from_name(src), provenance.SYSTEM)
        self.assertEqual(self.host.notices, ["压缩完成，Agent 正在恢复上下文。"])
        self.assertEqual(self.host.logged("shared_precompact")[0]["handoff"], True)

    async def test_no_handoff_at_precompact_says_so_after(self):
        await self.ev("UserPromptSubmit", prompt="做事")
        await self.ev("Stop", last_assistant_message="好")
        await self.ev("PreCompact", trigger="manual")
        await self.ev("PostCompact", trigger="manual")
        await self.settle()
        self.assertEqual(len(self.sent), 1)
        self.assertIn("本次没有交接文件", self.sent[0][0])
        self.assertIn("agentj-handoff", self.sent[0][0])
        self.assertEqual(self.host.logged("shared_precompact")[0]["handoff"], False)

    async def test_a_stale_handoff_from_before_the_turn_does_not_count(self):
        self.write_handoff()
        old = time.time() - 600
        os.utime(handoff.path("compact.md"), (old, old))
        await self.ev("UserPromptSubmit", prompt="新任务")
        await self.ev("Stop", last_assistant_message="好")
        await self.ev("PreCompact", trigger="manual")
        await self.ev("PostCompact", trigger="manual")
        await self.settle()
        self.assertIn("本次没有交接文件", self.sent[0][0])

    async def test_an_owner_message_within_the_delay_wins(self):
        self.write_handoff()
        await self.ev("PostCompact", trigger="manual")
        await self.ev("UserPromptSubmit", prompt="压缩完了吗")
        await self.settle()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.host.logged("shared_handoff_recover")[0]["result"], "new_turn")

    async def test_automatic_compaction_inside_a_running_turn_is_left_alone(self):
        self.write_handoff()
        await self.ev("UserPromptSubmit", prompt="长任务")
        self.a.native_turn = True                   # the turn goes on after Claude Code's own compaction
        await self.ev("PreCompact", trigger="auto")
        await self.ev("PostCompact", trigger="auto")
        await self.settle(6.5)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.host.logged("shared_handoff_recover")[0]["result"], "busy")

    async def test_session_start_context_names_the_handoff_too(self):
        self.write_handoff()
        out = await self.ev("SessionStart", source="compact")
        ctx = (out.get("hookSpecificOutput") or {}).get("additionalContext", "")
        self.assertIn("先读交接文件", ctx)

    async def test_clear_handoff_applies_once(self):
        self.write_handoff("clear.md", "# clear 交接\n")
        await self.ev("SessionStart", source="clear")
        await self.settle()
        self.assertEqual(len(self.sent), 1)
        self.assertIn("对话已清空。先读交接文件 ", self.sent[0][0])
        self.assertIn("clear-used.md", self.sent[0][0])
        self.assertFalse(handoff.path("clear.md").exists())
        self.assertTrue(handoff.path("clear-used.md").exists())
        self.assertEqual(self.host.notices, ["已清空，Agent 正在读交接文件。"])
        await self.ev("SessionStart", source="clear")
        await self.settle()
        self.assertEqual(len(self.sent), 1, "used once")

    async def test_clear_without_handoff_sends_nothing(self):
        await self.ev("SessionStart", source="clear")
        await self.settle()
        self.assertEqual(self.sent, [])

    async def test_phone_compact_prep_asks_for_the_handoff_and_waits_for_its_turn(self):
        loop = asyncio.get_running_loop()

        def agent_writes(s, text, src=None):
            self.sent.append((text, src))
            if "马上要压缩上下文" in text:
                time.sleep(0.02)
                self.write_handoff()
                loop.call_soon_threadsafe(self.a._finish_stop, "交接写好了")
        with patch.object(shared, "claude_send", side_effect=agent_writes):
            ok = await self.a._handoff_prep()
        self.assertTrue(ok)
        self.assertIn("agentj-handoff", self.sent[0][0])
        self.assertIn("compact.md", self.sent[0][0])
        self.assertEqual(provenance.from_name(self.sent[0][1]), provenance.SYSTEM)
        self.assertEqual(self.host.desktop, ["交接写好了"])

    async def test_phone_compact_skips_prep_when_the_handoff_is_fresh(self):
        await self.ev("UserPromptSubmit", prompt="继续")
        await self.ev("Stop", last_assistant_message="好")
        self.write_handoff()
        self.assertTrue(handoff.fresh(self.a.turn_began_at))

    async def test_without_a_known_turn_start_an_old_handoff_is_stale(self):
        self.write_handoff()
        self.assertTrue(handoff.fresh(None))
        old = time.time() - 2 * handoff.FRESH
        os.utime(handoff.path("compact.md"), (old, old))
        self.assertFalse(handoff.fresh(None))


class Paths(unittest.TestCase):
    def test_default_path_is_human_and_survives_the_phone_scrubber(self):
        with tempfile.TemporaryDirectory() as h, patch.dict(os.environ, {"HOME": h}):
            os.environ.pop("AGENTJ_STATE_DIR", None)
            os.environ.pop("AGENTJARVIS_STATE_DIR", None)
            shown = handoff.shown(handoff.path(handoff.COMPACT))
            self.assertEqual(shown, "~/.local/state/agentj/handoff/compact.md")
            line = handoff.recover_text("zh", "compact", True)
            self.assertIn(shown, privacy.redact(line, paths=True))
            self.assertIn("~/.local/state/agentj/handoff/clear-used.md",
                          handoff.recover_text("zh", "clear", True, handoff.path(handoff.CLEAR_USED)))

    def test_system_envelope_is_marked_and_not_the_owners_words(self):
        env = provenance.envelope("/x.sock", handoff.recover_text("zh", "compact", True), provenance.system())
        self.assertIn('from-name="' + provenance.SYSTEM + '"', env)
        self.assertIn("不是主人原话", env)
        self.assertIn(provenance.SYSTEM, provenance.FROM_NAMES)
        self.assertTrue(provenance.is_owner(provenance.system()))

    def test_owned_handover_is_compact_md_without_a_session_id(self):
        a = Mock(cfg={"dir": "/w"}, kind="claude")
        self.assertEqual(compactprep.handover_path(a, "claude-" + SID), "/w/.agentj/handover/compact.md")

    def test_bundled_skill_matches_the_host_paths(self):
        t = (Path(shared.__file__).parent / "skills" / "agentj-handoff" / "SKILL.md").read_text()
        for needle in ("name: agentj-handoff", "~/.local/state/agentj/handoff/compact.md",
                       "~/.local/state/agentj/handoff/clear.md", ".agentj/handover/compact.md", "〔Agent J 系统提示〕"):
            self.assertIn(needle, t)
        self.assertNotIn("Leo", t)


if __name__ == "__main__":
    unittest.main()
