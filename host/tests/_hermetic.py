"""Imported first by every test module that runs the CLI. Since 0.10 every `agentj` command first runs the state migration
(agentj/migrate.py) on the default state path; a test that forgets AGENTJ_STATE_DIR must never move — or even look at — the
developer's real ~/.local/state. Default: a throw-away state dir for the whole test process (and every child it starts).
Tests that exercise the migration (test_rename.py) build their own environment with a temporary HOME."""
import atexit
import os
import shutil
import tempfile

# P316: the developer's real home is the passwd entry, not $HOME (`HOME=$tmp python -m unittest` keeps a desktop-exported
# XDG_CONFIG_HOME=~/.config). A state / config dir inside it is replaced, not kept; AGENTJ_TEST=1 tells this process and every
# child it starts that agentj/preferences.guard must refuse the real ~/.config/agentj.
try:
    import pwd
    _REAL_HOME = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)
except (ImportError, KeyError):
    _REAL_HOME = os.path.realpath(os.environ.get("USERPROFILE") or os.path.expanduser("~"))


def _ours(name):
    v = os.environ.get(name)
    return bool(v) and not (os.path.realpath(v) + os.sep).startswith(_REAL_HOME + os.sep)


os.environ["AGENTJ_TEST"] = "1"
if not _ours("AGENTJ_STATE_DIR"):
    _d = tempfile.mkdtemp(prefix="aj-tests-state-")
    os.environ["AGENTJ_STATE_DIR"] = os.path.join(_d, "state")
    atexit.register(shutil.rmtree, _d, True)
# P64: preferences live at $XDG_CONFIG_HOME/agentj/config.json5 (else ~/.config): every in-process State.set_agent_config
# (working_root.record) used to rewrite the developer's REAL ~/.config/agentj/config.json5. A throw-away config home too —
# also when XDG_CONFIG_HOME is set but points into the real home (P316: some desktops always export it).
if not _ours("XDG_CONFIG_HOME"):
    _c = tempfile.mkdtemp(prefix="aj-tests-config-")
    os.environ["XDG_CONFIG_HOME"] = _c
    atexit.register(shutil.rmtree, _c, True)
# P57: `serve` links the bundled skills into ~/.claude/skills etc. (personalize.ensure) — never into the developer's real home
# from a test (the tests run serve with the real HOME); test_p57_agent tests ensure() itself with a patched home.
os.environ.setdefault("AGENTJ_SKILL_LINK", "off")
# P219: `agentj agent` / serve answer the harness's first-run questions in ~/.claude.json, ~/.codex/config.toml — never the
# developer's real ones from a test (test_p219 tests harness_setup itself with a temporary HOME).
os.environ.setdefault("AGENTJ_HARNESS_PREPARE", "off")

# P115: serve never pre-installs the Google CLI from a test (google.ensure_background).
os.environ.setdefault("AGENTJ_GOOGLE_PREINSTALL", "off")
# P131: never probe the developer's own CDP browsers (e.g. a Chromium on 127.0.0.1:9222) from a test; adopt tests use fakes.
os.environ.setdefault("AGENTJ_BROWSER_DETECT", "off")
# P277: a failed upgrade in a test never opens a real support thread (test_p277 drives the report through a seam).
os.environ.setdefault("AGENTJ_UPGRADE_REPORT", "off")
# P175: `agentj agent detect` / doctor never probe api.openai.com from a test (test_p175 patches the opener instead).
os.environ.setdefault("AGENTJ_REACH_PROBE", "off")
# P298: `opencode models` (the free-model list) is never asked of a stand-in opencode; test_p298 turns it on.
os.environ.setdefault("AGENTJ_OC_FREE_LIST", "off")

# P143: P142's diskq hands host.log lines to its writer thread for BUFFER_FOR s after one slow append. On a loaded test machine
# an in-process test that then reads host.log directly found no file yet — or a partial log, where "secret not in the log"
# would pass vacuously. Tests keep host.log synchronous; test_p142_diskq restores DISKQ_BUFFER_FOR to exercise buffering.
import pathlib as _pl
import sys as _sys
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from agentj import diskq as _diskq  # noqa: E402
DISKQ_BUFFER_FOR = _diskq.BUFFER_FOR
_diskq.BUFFER_FOR = 0.0
# P292: the loop never writes host.log itself — it waits ≤ LOOP_WAIT for the log thread to land its line. Tests wait longer
# (a loaded machine running modules in parallel), so a line logged on the loop is on disk when the call returns, as before.
DISKQ_LOOP_WAIT = _diskq.LOOP_WAIT
_diskq.LOOP_WAIT = 10.0
