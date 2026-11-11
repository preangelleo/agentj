"""Shared state paths and defaults; no State or preferences dependency.

Re-exported by state.py for the old public import paths.
"""
import os
import pathlib
from .envcompat import getenv

DEFAULT_RELAY = "wss://relay.agentj.app"
DEFAULT_WEB = "https://m.agentj.app"
OLD_DEFAULT_RELAY = "wss://alpha-relay.agentjarvis.net"   # ≤ 0.9 defaults: rewritten to the new ones by migrate.py
OLD_DEFAULT_WEB = "https://alpha-web.agentjarvis.net"
DEFAULT_STATE = "~/.local/state/agentj"
LEGACY_STATE = "~/.local/state/agentjarvis-alpha"   # ≤ 0.9; after the move (migrate.py) a symlink to DEFAULT_STATE

def state_dir() -> pathlib.Path:
    """$AGENTJ_STATE_DIR (or, one version cycle, $AGENTJARVIS_STATE_DIR); else ~/.local/state/agentj — except while a
    0.9 state directory has not been moved yet (migrate.py refused: a serve is running on it): then that one."""
    d = getenv("AGENTJ_STATE_DIR")
    if d:
        return pathlib.Path(d)
    new, old = default_state(), legacy_state()
    if not os.path.lexists(new) and old.is_dir() and not old.is_symlink():
        from .legacy_state_probe import serve_running    # P127: only a serve still running on it keeps the old one in use —
        if serve_running(old):                # an old directory nobody chose to adopt is never used silently
            return old
    return new


def default_state(home: str | None = None) -> pathlib.Path:
    return pathlib.Path(os.path.join(home, DEFAULT_STATE[2:]) if home else os.path.expanduser(DEFAULT_STATE))


def legacy_state(home: str | None = None) -> pathlib.Path:
    return pathlib.Path(os.path.join(home, LEGACY_STATE[2:]) if home else os.path.expanduser(LEGACY_STATE))




DEFAULT_TRASH = "~/.local/share/agentj-trash"


def _passwd_home() -> str:
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_dir
    except (ImportError, KeyError, OSError):
        return os.path.expanduser("~")


def account_home() -> str:
    """The passwd home of this uid — never $HOME (0.30.8 security r2 H1: a caller's environment chooses nothing about the
    trashbin or the trash guard's inventory). The one seam tests replace (tests/_hermetic.py, in-process)."""
    return _passwd_home()


def trash_path(home: str | None = None) -> pathlib.Path:
    """Where the trashbin of `home` (default: account_home()) is; pure path, no test refusal (uv_list's read checks)."""
    return pathlib.Path(home or account_home()) / DEFAULT_TRASH[2:]


def trash_dir() -> pathlib.Path:
    """0.30.8 #1: Agent J's own trashbin (trash.py), `<passwd home>/.local/share/agentj-trash` — beside the state
    directory, not inside it: the fence mounts the state root as an empty tmpfs (only agentperm/ comes back), so a fenced
    `agentj delete` writing there would lose the file when the harness exits. One per user, shared by instances.
    Security r2: no environment override (the 0.30.8 draft's $AGENTJ_TRASH_DIR is gone). A test process (AGENTJ_TEST=1,
    as tests/_hermetic sets for itself and its children) that has not replaced account_home() is refused the real one —
    like preferences.guard; setting AGENTJ_TEST only ever takes the trashbin away."""
    import sys
    home = account_home()
    if ((os.environ.get("AGENTJ_TEST") == "1" or "unittest.runner" in sys.modules or "pytest" in sys.modules)
            and os.path.realpath(home) == os.path.realpath(_passwd_home())):
        raise RuntimeError("test process refused the real Agent J trashbin: import tests/_hermetic first")
    return trash_path(home)
