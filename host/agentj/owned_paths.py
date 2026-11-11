"""Inventory of Agent J-owned locations for local ownership diagnostics.

Only Agent J's own files are recursive repair targets. Shared harness config files
are inspected individually; their parent folders may contain customer data.
This module never creates paths, reads file contents or performs a repair.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def inventory(home: str, environ: dict, state_root=None) -> tuple[Path, ...]:
    """Concrete owned paths; missing paths are harmless, existing parents are checked by upgrade_owner."""
    h = Path(home)
    config = Path(environ.get('XDG_CONFIG_HOME') or h / '.config')
    cache = Path(environ.get('XDG_CACHE_HOME') or h / '.cache')
    state = Path(state_root) if state_root is not None else Path(environ.get('AGENTJ_STATE_DIR') or
        Path(environ.get('XDG_STATE_HOME') or h / '.local/state') / 'agentj')
    if os.name == 'nt':
        return ()
    paths = [state, state / 'agentperm', state / 'host.log',
             cache / 'agentj-upgrade', cache / 'agentj-upgrade' / 'upgrade-cache',
             Path(environ.get('UV_CACHE_DIR') or cache / 'uv'),
             Path(environ.get('UV_TOOL_DIR') or Path(environ.get('XDG_DATA_HOME') or h / '.local/share') / 'uv/tools'),
             Path(environ.get('UV_TOOL_BIN_DIR') or environ.get('XDG_BIN_HOME') or h / '.local/bin') / 'agentj',
             Path(environ.get('CLAUDE_CONFIG_DIR') or h / '.claude') / 'settings.json',
             Path(environ.get('CLAUDE_CONFIG_DIR') or h / '.claude') / 'agentj-statusline.json',
             Path(environ.get('CODEX_HOME') or h / '.codex') / 'config.toml',
             config / 'opencode/opencode.json',
             config / 'opencode/opencode.jsonc']
    if sys.platform == 'darwin':
        paths.extend((h / 'Library/LaunchAgents/net.agentj.host.plist',
                      h / 'Library/LaunchAgents/net.agentj.host.env',
                      h / 'Library/Application Support/Agent J/jobs'))
    else:
        paths.extend((config / 'systemd/user/net.agentj.host.service',
                      config / 'systemd/user/net.agentj.host.env'))
    return tuple(paths)
