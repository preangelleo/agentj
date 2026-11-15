"""0.30.23: a 0.30.21 /update job checks health in its own process after the install. Modules it loaded before the install
are the old ones; a new file first imported afterwards must still import (the failure cancelled the service restart)."""
import _hermetic  # noqa: F401
import importlib
import sys
import types
import unittest


class OldProcessImport(unittest.TestCase):
    def test_voice_imports_while_the_predecessor_preference_checks_is_in_memory(self):
        import agentj
        import agentj.preference_checks as real
        old = types.ModuleType("agentj.preference_checks")           # 0.30.21's module: everything but tts_options
        old.__dict__.update({k: v for k, v in real.__dict__.items() if k != "tts_options"})
        names = ("agentj.preference_checks", "agentj.voice")
        saved = {n: sys.modules.get(n) for n in names}
        attrs = {n: getattr(agentj, n.split(".")[1], None) for n in names}
        sys.modules["agentj.preference_checks"] = old
        sys.modules.pop("agentj.voice", None)
        try:
            with self.assertRaises(ImportError):
                from agentj.preference_checks import tts_options  # noqa: F401 — the in-memory module really lacks it
            voice = importlib.import_module("agentj.voice")
            self.assertEqual(voice.tts_options({}), ("", {}))
            self.assertEqual(voice.asr_endpoint.__name__, "asr_endpoint")
            self.assertIs(sys.modules["agentj.preference_checks"], old)   # the old module was not swapped behind its users
        finally:
            for n in names:
                if saved[n] is None: sys.modules.pop(n, None)
                else: sys.modules[n] = saved[n]
                if attrs[n] is not None: setattr(agentj, n.split(".")[1], attrs[n])
                elif hasattr(agentj, n.split(".")[1]): delattr(agentj, n.split(".")[1])

    def test_voice_uses_the_loaded_module_when_it_is_current(self):
        from agentj import preference_checks, voice
        self.assertIs(voice.tts_options, preference_checks.tts_options)


if __name__ == "__main__":
    unittest.main()
