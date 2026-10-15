"""P316: tests and gates never write the developer's real ~/.config/agentj/config.json5.

10-15: bare `python -m unittest test_opencode …` and `HOME=$tmp python -m unittest …` both rewrote the owner's config with
/tmp working roots — some desktops export XDG_CONFIG_HOME=~/.config, and _hermetic only isolated it when it was unset. Two layers:
_hermetic replaces a config / state dir inside the real (passwd) home, and preferences.guard refuses the real path in any test
process (AGENTJ_TEST=1, or unittest's runner / pytest loaded), so a test that slips past the first layer fails instead of writing.
Nothing here reads, writes or even stats the real config file: the guard raises before any file access.
"""
import _hermetic  # noqa: F401  (first: isolates XDG_CONFIG_HOME / AGENTJ_STATE_DIR, sets AGENTJ_TEST)
import os
import pathlib
import pwd
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HOST = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOST))
from agentj import preferences, provider_profiles  # noqa: E402

REAL_HOME = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)
REAL_CONFIG = os.path.join(REAL_HOME, ".config")


def _inside_real_home(p):
    return (os.path.realpath(p) + os.sep).startswith(REAL_HOME + os.sep)


class Hermetic(unittest.TestCase):
    def test_config_and_state_are_never_the_real_home(self):
        self.assertEqual(os.environ.get("AGENTJ_TEST"), "1")
        for name in ("XDG_CONFIG_HOME", "AGENTJ_STATE_DIR"):
            self.assertFalse(_inside_real_home(os.environ[name]), f"{name} must be a throw-away dir in a test process")
        self.assertFalse(_inside_real_home(preferences.path()))

    def test_exported_xdg_config_home_is_replaced(self):
        """`HOME=$tmp python -m unittest` with XDG_CONFIG_HOME=~/.config exported (the 10-15 P290 runs)."""
        with tempfile.TemporaryDirectory() as d:
            env = {k: v for k, v in os.environ.items() if k not in ("AGENTJ_TEST", "AGENTJ_STATE_DIR")}
            env.update(HOME=d, XDG_CONFIG_HOME=REAL_CONFIG, AGENTJ_STATE_DIR=os.path.join(REAL_HOME, ".local/state/agentj"))
            code = ("import _hermetic, os, sys; sys.path.insert(0, %r); from agentj import preferences;"
                    "print(os.environ['XDG_CONFIG_HOME']); print(os.environ['AGENTJ_STATE_DIR']); print(preferences.path())"
                    % str(HOST))
            out = subprocess.run([sys.executable, "-c", code], cwd=str(HOST / "tests"), env=env, capture_output=True,
                                 text=True, timeout=60)
            self.assertEqual(out.returncode, 0, out.stderr)
            for line in out.stdout.split():
                self.assertFalse(_inside_real_home(line), line)


class Guard(unittest.TestCase):
    def test_refuses_the_real_config_in_a_test_process(self):
        for n, leaf in ((1, "agentj"), (2, "agentj-2")):
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": REAL_CONFIG}), \
                    mock.patch("agentj.instance.current", return_value=n):
                with self.assertRaisesRegex(RuntimeError, "test process refused the real"):
                    preferences.path()
                with self.assertRaises(RuntimeError):
                    preferences.read()
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": REAL_CONFIG}):
                with self.assertRaises(RuntimeError):
                    provider_profiles.root()

    def test_refuses_through_HOME_when_XDG_is_unset(self):
        env = {k: v for k, v in os.environ.items() if k != "XDG_CONFIG_HOME"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(pathlib.Path, "home", return_value=pathlib.Path(REAL_HOME)):
            with self.assertRaises(RuntimeError):
                preferences.path()

    def test_other_real_config_dirs_and_temp_dirs_pass(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": d}):
                self.assertEqual(preferences.path(), pathlib.Path(d) / "agentj" / "config.json5")
        self.assertEqual(preferences.guard(pathlib.Path(REAL_CONFIG) / "opencode" / "opencode.json"),
                         pathlib.Path(REAL_CONFIG) / "opencode" / "opencode.json")

    def test_markers(self):
        with mock.patch.dict(os.environ, {"AGENTJ_TEST": "1"}):
            self.assertTrue(preferences.under_test())
        with mock.patch.dict(os.environ, {"AGENTJ_TEST": ""}):
            self.assertTrue(preferences.under_test())       # unittest.runner is loaded in this very process

    def test_agentj_never_loads_a_test_runner(self):
        """The marker is only sound while agentj's own modules never import unittest / pytest (serve, cli, preferences)."""
        env = {k: v for k, v in os.environ.items() if k != "AGENTJ_TEST"}
        code = ("import sys; sys.path.insert(0, %r); import agentj.serve, agentj.cli, agentj.preferences as p;"
                "print(sorted(m for m in sys.modules if m.split('.')[0] in ('unittest', 'pytest')), p.under_test())" % str(HOST))
        out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "[] False")

    def test_a_gate_style_c_runner_is_caught(self):
        """`python -c "import unittest; …discover…"` (the p44 named gates) with a module that forgets _hermetic."""
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "test_leak.py").write_text(
                "import sys, unittest\nsys.path.insert(0, %r)\nfrom agentj import preferences\n"
                "class T(unittest.TestCase):\n    def test(self):\n        preferences.path()\n" % str(HOST))
            env = {k: v for k, v in os.environ.items() if k != "AGENTJ_TEST"}
            env["XDG_CONFIG_HOME"] = REAL_CONFIG
            code = ("import unittest; s=unittest.defaultTestLoader.discover('.', pattern='test_leak.py');"
                    "r=unittest.TextTestRunner().run(s); raise SystemExit(not r.wasSuccessful())")
            out = subprocess.run([sys.executable, "-c", code], cwd=d, env=env, capture_output=True, text=True, timeout=60)
            self.assertNotEqual(out.returncode, 0)
            self.assertIn("test process refused the real", out.stderr)

    def test_the_owner_s_own_agentj_is_not_guarded(self):
        """Outside a test process (a real `agentj`, serve) the real path resolves as before — computed only, nothing written."""
        env = {k: v for k, v in os.environ.items() if k != "AGENTJ_TEST"}
        env["XDG_CONFIG_HOME"] = REAL_CONFIG
        code = "import sys; sys.path.insert(0, %r); from agentj import preferences; print(preferences.path())" % str(HOST)
        out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), os.path.join(REAL_CONFIG, "agentj", "config.json5"))

    def test_a_bare_unittest_run_is_caught(self):
        """A module that forgets _hermetic, run as `python -m unittest` (no AGENTJ_TEST): the unittest-runner marker still refuses."""
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "test_leak.py").write_text(
                "import sys, unittest\nsys.path.insert(0, %r)\nfrom agentj import preferences\n"
                "class T(unittest.TestCase):\n    def test(self):\n        preferences.path()\n" % str(HOST))
            env = {k: v for k, v in os.environ.items() if k != "AGENTJ_TEST"}
            env["XDG_CONFIG_HOME"] = REAL_CONFIG
            out = subprocess.run([sys.executable, "-m", "unittest", "test_leak"], cwd=d, env=env, capture_output=True,
                                 text=True, timeout=60)
            self.assertNotEqual(out.returncode, 0)
            self.assertIn("test process refused the real", out.stderr)


if __name__ == "__main__":
    unittest.main()
