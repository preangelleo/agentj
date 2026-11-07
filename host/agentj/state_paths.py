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


