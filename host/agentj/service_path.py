"""Stable, credential-free daemon PATH discovery (P529). Never inherit App PATH."""
from __future__ import annotations

import os
from pathlib import Path
import pwd
import subprocess

BASE = '/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'


def stable_entry(value: str) -> bool:
    p = value.lower()
    return (value.startswith('/') and not any(c in value for c in '\n\r\0')
            and not any(x in p for x in ('/var/folders/', '/private/var/folders/', '/.codex/tmp/', '.app/contents/', '/codex-runtimes/')))


def login_path(environ: dict) -> str:
    """Read only PATH from the owner's login shell, with no inherited secret/env hooks.

    Startup chatter stays private; a NUL-delimited marker isolates our output. A
    stalled/broken dotfile has a short deadline and falls back to known directories.
    """
    home = environ.get('HOME') or str(Path.home())
    try:
        user = pwd.getpwuid(os.getuid())
        # Synthetic HOME must never execute this machine owner's startup files.
        if Path(home) != Path(user.pw_dir):
            return ''
        shell = user.pw_shell
        if shell not in ('/bin/bash', '/bin/zsh', '/bin/sh', '/usr/bin/bash', '/usr/bin/zsh'):
            return ''
        env = {'HOME': home, 'USER': user.pw_name, 'LOGNAME': user.pw_name, 'SHELL': shell, 'PATH': BASE}
        result = subprocess.run([shell, '-lc', "printf '\\0agentj-path\\0%s\\0' \"$PATH\""],
                                env=env, cwd=home, capture_output=True, timeout=3)
        return result.stdout.split(b'\0agentj-path\0', 1)[1].split(b'\0', 1)[0].decode() if result.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired, IndexError, UnicodeError, KeyError):
        return ''


def build(environ: dict) -> str:
    home = Path(environ.get('HOME') or str(Path.home()))
    common = [home / p for p in ('.local/bin', 'bin', '.local/share/mise/shims', '.asdf/shims',
                                 '.npm-global/bin', '.npm/bin', '.local/share/npm/bin', '.opencode/bin')]
    prefix = environ.get('NPM_CONFIG_PREFIX') or environ.get('npm_config_prefix')
    if prefix:
        common.append(Path(prefix).expanduser() / 'bin')
    # The invoking App's PATH is intentionally never a source.
    candidates = login_path(environ).split(':') + [str(p) for p in common] + ['/opt/homebrew/bin', '/opt/homebrew/sbin']
    candidates += BASE.split(':')
    out = []
    for entry in candidates:
        entry = os.path.normpath(entry) if entry.startswith('/') else entry
        if stable_entry(entry) and entry not in out:
            out.append(entry)
    return ':'.join(out)
