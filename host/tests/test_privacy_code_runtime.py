"""Regression from dc4e7364dc: untrusted code delimiters must not stall owner delivery."""
import _hermetic  # noqa: F401
import time
import unittest
from agentj import privacy


class CodeRuntime(unittest.TestCase):
    def test_hostile_backtick_runs_stay_fast(self):
        for text in (("`" * 40 + " ") * 1100, "`" * 45000,
                     "".join("`" * (i % 50 + 1) + "x" for i in range(1800)),
                     "` " * 22500, "```\n" * 11250, ("`" * 30 + "\n") * 1500,
                     "~" * 45000):
            text = text[:45000]
            started = time.monotonic()
            privacy.owner_text(text)
            self.assertLess(time.monotonic() - started, 1.0, repr(text[:20]))

    def test_inline_and_fenced_code_still_recognised(self):
        for text, span in (("x `a` y", "`a`"), ("``a`b``", "``a`b``"),
                           ("```\ncode\n```\nafter", "```\ncode\n```"),
                           ("~~~\nx\n~~~", "~~~\nx\n~~~")):
            self.assertEqual([m.group(0) for m in privacy._CODE.finditer(text)], [span], text)
