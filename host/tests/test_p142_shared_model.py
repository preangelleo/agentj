"""P142 item 7 (host side): shared Claude's `/model` (what the phone's model label sends when it has no catalogue) answers
with one button per observed model plus the owner's distinct default (P579: no speculative fallback). Each button is the same `/model <id>` self-slash path; and the
catalogue is built as soon as the shared session attaches, so the label can cycle models from the start."""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
    # Actual native picker labels captured in P579; fixture data, not a live model test.
    NATIVE_ROWS = ['Default (Sonnet 5.5)', 'Sonnet 5.5', 'Fable 5.1', 'Opus 5.5', 'Haiku 5.5']
    NATIVE_IDS = ['sonnet-5.5', 'fable-5.1', 'opus-5.5', 'haiku-5.5']

    def cache_rows(self, rows=None):
        # Exercise the persisted v2 input path, not just the in-memory learner.
        (self.h.st.root / 'claude-picker-rows.json').write_text(
            json.dumps({'v': 2, 'rows': self.NATIVE_ROWS if rows is None else rows}))

    def setUp(self):
        d = tempfile.mkdtemp(prefix="aj-p142sm-")
        self.addCleanup(shutil.rmtree, d, True)
        self.h = H(Path(d) / "st")
        self.a = shared.SharedClaudeAgent(self.h, {"kind": "claude", "dir": "/nonexistent", "_workflow_ceo": True, "language": "zh"})

    def test_model_without_arg_lists_buttons_and_default(self):
        self.cache_rows()
        r = asyncio.run(self.a.command("model", ""))
        ids = [m["id"] for m in r.models]
        self.assertEqual(ids, self.NATIVE_IDS)
        # Default is already Sonnet 5.5: do not offer the same entity twice.
        self.assertNotIn("default", ids)
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
        self.cache_rows()
        asyncio.run(self.a.refresh_models())
        self.assertEqual([m['id'] for m in self.a.models_cache], self.NATIVE_IDS)
        # A new adapter must restore the same observed directory from disk.
        restored = shared.SharedClaudeAgent(self.h, self.a.cfg)
        asyncio.run(restored.refresh_models())
        self.assertEqual(restored.models_cache, self.a.models_cache)

    def test_unknown_directory_never_invents_legacy_models(self):
        # This is the original P142 no-cache/no-native scenario. P579 explicitly
        # removed its speculative five-model fallback (including unobserved 1M).
        asyncio.run(self.a.refresh_models())
        self.assertEqual(self.a.models_cache, [])
        r = asyncio.run(self.a.command('model', ''))
        self.assertEqual([m['id'] for m in r.models], ['default'])
        self.assertFalse({ms.slug(x) for x in ms.LEGACY} & {m['id'] for m in r.models})

    def test_measured_current_model_is_available_without_picker_cache(self):
        native = {'model': 'claude-haiku-5-5', 'model_name': 'Haiku 5.5', 'effort': 'medium'}
        with patch.object(self.a, '_read_native', return_value=native):
            r = asyncio.run(self.a.command('model', ''))
        self.assertEqual([m['id'] for m in self.a.models_cache], ['haiku-5.5'])
        self.assertEqual([(m['id'], m['cur']) for m in r.models], [('haiku-5.5', True), ('default', False)])

    def test_cached_duplicate_and_current_model_are_single_entities(self):
        self.cache_rows(self.NATIVE_ROWS + ['Sonnet 5.5'])
        native = {'model': 'claude-sonnet-5-5', 'model_name': 'Sonnet 5.5', 'effort': 'medium'}
        with patch.object(self.a, '_read_native', return_value=native):
            r = asyncio.run(self.a.command('model', ''))
        self.assertEqual([m['id'] for m in r.models], self.NATIVE_IDS)
        self.assertEqual([m['id'] for m in r.models if m['cur']], ['sonnet-5.5'])

    def test_current_model_outside_cached_picker_is_not_lost(self):
        self.cache_rows(['Default (recommended)', 'Sonnet 5.5'])
        native = {'model': 'claude-opus-5-5', 'model_name': 'Opus 5.5', 'effort': 'high'}
        with patch.object(self.a, '_read_native', return_value=native):
            r = asyncio.run(self.a.command('model', ''))
        self.assertEqual([m['id'] for m in r.models], ['opus-5.5', 'sonnet-5.5', 'default'])
        self.assertEqual([m['id'] for m in r.models if m['cur']], ['opus-5.5'])


if __name__ == "__main__":
    unittest.main()
