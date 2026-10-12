"""P142 item 6 (GAP-0182-opencode-language): every OpenCode v1 phone turn (owned and shared, main or attached owner) carries
the owner's language twice — in `system` as before, and as a hidden (synthetic) LAST part of the message, so OpenCode's own
long build-agent prompt cannot outweigh it. The visible text part stays the owner's words, first and unchanged."""
import _hermetic  # noqa: F401,I001
import ast
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import agent_opencode, main_identity  # noqa: E402


class TurnBody(unittest.TestCase):
    def test_zh_main(self):
        cfg = {"language": "zh", "kind": "opencode", "dir": "/tmp"}
        b = agent_opencode.turn_body("Hello. Please greet me briefly.", cfg, True)
        self.assertEqual(b["parts"][0], {"type": "text", "text": "Hello. Please greet me briefly."})
        # P159: the main Agent's hidden last part is the identity reminder (address, delegate triage, language)
        self.assertEqual(b["parts"][-1], {"type": "text", "text": main_identity.IDENTITY_REMINDER["zh"], "synthetic": True})
        self.assertIn("中文", b["parts"][-1]["text"])
        self.assertEqual(b["system"], main_identity.prompt(cfg))
        self.assertIn(main_identity.LANGUAGE_LINE["zh"].strip(), b["system"])

    def test_en_attached_owner_keeps_own_identity(self):
        b = agent_opencode.turn_body("你好", {"language": "en", "kind": "opencode", "dir": "/tmp"}, False)
        self.assertEqual(b["system"], main_identity.LANGUAGE_LINE["en"])          # never Agent J's constitution
        self.assertIn("English", b["parts"][-1]["text"])
        self.assertTrue(b["parts"][-1]["synthetic"])
        self.assertEqual(b["parts"][0]["text"], "你好")

    def test_reminder_is_not_in_the_hashed_prompt(self):
        cfg = {"language": "zh", "kind": "opencode", "dir": "/tmp"}
        self.assertNotIn(main_identity.LANGUAGE_REMINDER["zh"], main_identity.prompt(cfg))
        self.assertNotIn(main_identity.IDENTITY_REMINDER["zh"], main_identity.prompt(cfg))

    def test_both_v1_turn_paths_use_turn_body(self):
        for mod in ("agent_opencode.py", "shared.py"):
            src = (pathlib.Path(agent_opencode.__file__).parent / mod).read_text()
            calls = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)
                     and getattr(n.func, "id", getattr(n.func, "attr", None)) == "turn_body"]
            self.assertTrue(calls, mod)

    def test_synthetic_reminder_never_shown_as_a_reply(self):
        # the reply reader skips synthetic parts (agent_opencode: part.get("synthetic") → skipped)
        src = pathlib.Path(agent_opencode.__file__).read_text()
        self.assertIn('part.get("synthetic")', src)


if __name__ == "__main__":
    unittest.main()
