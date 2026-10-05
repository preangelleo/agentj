"""P59 lane `telegram` (0.15.3, ADR-A165): F24 for Codex's own (automatic) compaction, with the stand-in fakecodex.

- The context meter at the end of a user turn ≥ compactprep.AUTO_PREP_AT and no fresh handover in this epoch → the
  preparation turn runs right away (its 「交接写好了」 is not shown to the phone), once per epoch, nothing compacted by us.
- Codex compacts by itself inside a turn (`contextCompaction` item; the older `thread/compacted` too, counted once) → a new
  epoch: the next user message carries the "read the handover" host line once; a high meter again → prepared again.
- Below the threshold nothing happens; Claude Code / OpenCode are not proactive (AUTO_KINDS).
"""
import _hermetic  # noqa: F401,I001
import os
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import compactprep  # noqa: E402
from test_p57_agent import _Prep  # noqa: E402


class _Codex(_Prep):
    KIND = "codex"

    def preps(self):
        return [x for x in self.logged() if x.get("method") == "turn/start"
                and any(str(p.get("text", "")).startswith(compactprep.MARK) for p in x["params"]["input"])]

    def handover_notes(self):
        return [n for n in self.notes() if n.startswith("（Agent J：压缩前的交接")]

    async def talk(self, c, t):
        await c["say"](t)
        await c["wait"](lambda: any(m["text"] == f"ECHO: {t}" for m in c["msgs"]()))
        await c["idle"]()


class CodexProactive(_Codex):
    ENV = {"FAKE_CTX_USED": "230000"}          # 230 000 / 258 400 = 89 % ≥ 85 %

    def test_threshold_prepares_once_per_epoch_and_auto_compaction_brings_the_note(self):
        async def script(c):
            await self.talk(c, "一")
            self.assertEqual(len(self.preps()), 1, "the meter crossed the threshold: prepared at the end of the turn")
            self.assertIn("Codex 很快会自动压缩", self.preps()[0]["params"]["input"][0]["text"])
            [ho] = self.handovers()
            self.assertTrue(ho.read_text().startswith("# Handover"))
            self.assertFalse(any(m["text"] in ("交接写好了", "没写交接") for m in c["msgs"]()), "the preparation is not a reply")
            await self.talk(c, "二")
            self.assertEqual(len(self.preps()), 1, "once per epoch")
            self.assertEqual(self.handover_notes(), [])
            await self.talk(c, "AUTOCOMPACT")         # Codex compacts by itself inside this turn; the meter drops
            self.assertEqual(len(self.preps()), 1, "low meter after the compaction: nothing to prepare")
            await self.talk(c, "三")
            self.assertEqual(self.handover_notes(), [compactprep.NOTE["zh"].format(path=ho)], "read the handover first")
            self.assertEqual(len(self.preps()), 2, "a new epoch, the meter high again: prepared again")
            await self.talk(c, "四")
            self.assertEqual(len(self.handover_notes()), 1, "the note once")
            self.assertEqual(len(self.preps()), 2)
        self.run_chain(script)
        log = self.st.log_path.read_text()
        self.assertEqual(log.count('"ev": "codex_auto_compact"'), 1)
        self.assertEqual(log.count('"ev": "compact_auto_prep"'), 2)
        self.assertIn('"result": "auto"', log)
        for leak in ("Handover", "交接写好了", ".agentj/handover"):
            self.assertNotIn(leak, log)

    def test_legacy_event_counts_once_and_a_failed_preparation_is_not_retried_in_the_epoch(self):
        os.environ["FAKE_NO_HANDOVER"] = "1"

        async def script(c):
            await self.talk(c, "一")
            await self.talk(c, "二")
            self.assertEqual(len(self.preps()), 1, "failed once: not retried every turn")
            self.assertEqual(self.handovers(), [])
            await self.talk(c, "AUTOCOMPACT legacy")  # contextCompaction item AND thread/compacted for one compaction
            await self.talk(c, "三")
            self.assertEqual(self.handover_notes(), [], "no handover: no note")
        self.run_chain(script)
        log = self.st.log_path.read_text()
        self.assertEqual(log.count('"ev": "codex_auto_compact"'), 1)
        self.assertEqual(compactprep.load(self.st)[compactprep.session_key(self._agent)]["n"], 1)

    def run_chain(self, script):
        async def wrapped(c):
            self._agent = c["host"].agent
            await script(c)
        super().run_chain(wrapped)


class CodexBelowThreshold(_Codex):
    ENV = {"FAKE_CTX_USED": "200000"}          # 77 %: the reminder only, no preparation

    def test_no_preparation_below_the_threshold(self):
        async def script(c):
            for t in ("一", "二", "三"):
                await self.talk(c, t)
            self.assertEqual(self.preps(), [])
        self.run_chain(script)
        self.assertNotIn("compact_auto_prep", self.st.log_path.read_text())


class ClaudeNotProactive(_Prep):
    KIND = "claude"
    ENV = {"FAKE_CTX_USED": "190000"}          # 95 % of Claude Code's 200 000

    def test_claude_code_is_not_proactive(self):
        async def script(c):
            for t in ("一", "二"):
                await c["say"](t)
                await c["wait"](lambda t=t: any(m["text"] == f"ECHO: {t}" for m in c["msgs"]()))
                await c["idle"]()
            self.assertEqual(self.handovers(), [])
        self.run_chain(script)
        self.assertNotIn("compact_auto_prep", self.st.log_path.read_text())


if __name__ == "__main__":
    unittest.main()
