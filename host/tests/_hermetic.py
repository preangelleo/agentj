"""Imported first by every test module that runs the CLI. Since 0.10 every `agentj` command first runs the state migration
(agentj/migrate.py) on the default state path; a test that forgets AGENTJ_STATE_DIR must never move — or even look at — the
developer's real ~/.local/state. Default: a throw-away state dir for the whole test process (and every child it starts).
Tests that exercise the migration (test_rename.py) build their own environment with a temporary HOME."""
import atexit
import os
import shutil
import tempfile

if not os.environ.get("AGENTJ_STATE_DIR"):
    _d = tempfile.mkdtemp(prefix="aj-tests-state-")
    os.environ["AGENTJ_STATE_DIR"] = os.path.join(_d, "state")
    atexit.register(shutil.rmtree, _d, True)
# P64: preferences live at $XDG_CONFIG_HOME/agentj/config.json5 (else ~/.config): every in-process State.set_agent_config
# (working_root.record) used to rewrite the developer's REAL ~/.config/agentj/config.json5. A throw-away config home too.
if not os.environ.get("XDG_CONFIG_HOME"):
    _c = tempfile.mkdtemp(prefix="aj-tests-config-")
    os.environ["XDG_CONFIG_HOME"] = _c
    atexit.register(shutil.rmtree, _c, True)
# P57: `serve` links the bundled skills into ~/.claude/skills etc. (personalize.ensure) — never into the developer's real home
# from a test (the tests run serve with the real HOME); test_p57_agent tests ensure() itself with a patched home.
os.environ.setdefault("AGENTJ_SKILL_LINK", "off")

# P115: serve never pre-installs the Google CLI from a test (google.ensure_background).
os.environ.setdefault("AGENTJ_GOOGLE_PREINSTALL", "off")
# P131: never probe the developer's own CDP browsers (e.g. a Chromium on 127.0.0.1:9222) from a test; adopt tests use fakes.
os.environ.setdefault("AGENTJ_BROWSER_DETECT", "off")

# P143: P142's diskq hands host.log lines to its writer thread for BUFFER_FOR s after one slow append. On a loaded test machine
# an in-process test that then reads host.log directly found no file yet — or a partial log, where "secret not in the log"
# would pass vacuously. Tests keep host.log synchronous; test_p142_diskq restores DISKQ_BUFFER_FOR to exercise buffering.
import pathlib as _pl
import sys as _sys
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from agentj import diskq as _diskq  # noqa: E402
DISKQ_BUFFER_FOR = _diskq.BUFFER_FOR
_diskq.BUFFER_FOR = 0.0
