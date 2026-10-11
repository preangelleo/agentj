"""P127 追加5: the owner channel (shared mode) keeps file paths readable; real secrets — slash-bearing ones included —
are still redacted; the strict gate (feedback / plaza) is unchanged."""
import _hermetic  # noqa: F401,I001
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from agentj import privacy, shared, shared_opencode2  # noqa: E402

R = privacy.R


class OwnerPaths(unittest.TestCase):
    def test_layer1_cases(self):
        cases = privacy.load_layer1_cases()
        self.assertGreaterEqual(len(cases), 5)
        for c in cases:
            with self.subTest(c["id"]):
                out = privacy.redact(c["text"], paths=True)
                for k in c["keep"]:
                    self.assertIn(k, out)
                for g in c["gone"]:
                    self.assertNotIn(g, out)
                self.assertEqual(privacy.redact(out, paths=True), out)          # idempotent

    def test_leo_scratchpad_path(self):
        t = "读 /tmp/claude-1000/-home-sample-workspace/7ec0779a-aaef-439e-9073-78dd8e730755/scratchpad/COMPACT_HANDOFF.md"
        self.assertEqual(shared.public_text(t), t)
        self.assertEqual(shared_opencode2.public_text(t), t)

    def test_secrets_never_loosened(self):
        for c in privacy.load_layer1_cases():
            for g in c["gone"]:
                with self.subTest(c["id"]):
                    self.assertNotIn(g, privacy.redact(c["text"]))               # strict too
        key = privacy.FAKE_SECRETS["openrouter"]
        self.assertNotIn(key, shared.public_text(f"OPENROUTER_API_KEY={key} in /srv/app/.env"))
        self.assertIn("/srv/app/.env", shared.public_text(f"OPENROUTER_API_KEY={key} in /srv/app/.env"))

    def test_strict_unchanged(self):
        t = "读 /tmp/claude-1000/-home-sample-workspace/7ec0779a-aaef-439e-9073-78dd8e730755/scratchpad/COMPACT_HANDOFF.md"
        self.assertIn(R, privacy.redact(t))     # feedback / plaza keep the server scan's whole-token rule


if __name__ == "__main__":
    unittest.main()
