"""P142 item 7 (host side): shared Claude's `/model` (what the phone's model label sends when it has no catalogue) answers
with one button per picker row plus the owner's default — each button is the same `/model <id>` self-slash path; and the
catalogue is built as soon as the shared session attaches, so the label can cycle models from the start."""
import _hermetic  # noqa: F401,I001
import asyncio
import shutil
import tempfile
import unittest
from pathlib import Path

from agentj import shared, shared_modelswitch as ms
from agentj.state import State


class H:
    lang = "zh"

    def __init__(self, root):
        self.st = State(Path(root))
        self.st.root.mkdir(parents=True, exist_ok=True)
        self.changed = 0

    def agent_status(self, s):
        pass

    def models_changed(self):
        self.changed += 1


class SharedModel(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp(prefix="aj-p142sm-")
        self.addCleanup(shutil.rmtree, d, True)
        self.h = H(Path(d) / "st")
        self.a = shared.SharedClaudeAgent(self.h, {"kind": "claude", "dir": "/nonexistent", "_workflow_ceo": True, "language": "zh"})

    def test_model_without_arg_lists_buttons_and_default(self):
        r = asyncio.run(self.a.command("model", ""))
        ids = [m["id"] for m in r.models]
        self.assertEqual(ids[-1], "default")
        self.assertEqual(ids[:-1], [ms.slug(x) for x in ms.LEGACY if not x.startswith("Default")])
        self.assertEqual(r.kind, "info")
        self.assertIn("/model <", r.text)                   # the typed form is still shown

    def test_model_with_arg_is_still_a_switch_not_a_list(self):
        async def go():
            async def fake(arg):
                return "switched:" + arg
            self.a._model_command = fake
            return await self.a.command("model", "sonnet")
        self.assertEqual(asyncio.run(go()), "switched:sonnet")

    def test_refresh_on_attach_fills_the_catalogue(self):
        self.assertEqual(self.a.models_cache if hasattr(self.a, "models_cache") else [], [])
        src = Path(shared.__file__).read_text()
        attach = src[src.index("    async def attach(self"):src.index("    def start(self):")]
        self.assertIn("await self.refresh_models()", attach)
        self.assertIn("models_changed", attach)
        asyncio.run(self.a.refresh_models())
        self.assertTrue(self.a.models_cache)


if __name__ == "__main__":
    unittest.main()
