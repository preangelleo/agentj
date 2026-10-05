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
