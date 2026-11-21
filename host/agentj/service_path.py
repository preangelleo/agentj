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


def npm_prefix_bin(environ, home, *, read_config=False):
    prefix = environ.get('NPM_CONFIG_PREFIX') or environ.get('npm_config_prefix')
    if not prefix and read_config:
        config = Path(environ.get('NPM_CONFIG_USERCONFIG') or environ.get('npm_config_userconfig')
                      or home / '.npmrc')
        if not config.is_file():
            return None
        try:
            with config.open() as file:
                for line in file.read(64 * 1024).splitlines():
                    key, sep, value = line.strip().partition('=')
                    if sep and key.strip() == 'prefix':
                        prefix = value.strip().strip('"').strip("'")
        except (OSError, UnicodeError):
            return None
    if prefix:
        prefix = prefix.replace('${HOME}', str(home))
        if prefix.startswith('~/'):
            prefix = str(home / prefix[2:])
        if stable_entry(prefix):
            return Path(prefix) / 'bin'
    return None


NPM_BIN_DIRS = ('.npm-global/bin', '.npm/bin', '.local/share/npm/bin')


def build(environ: dict, *, install=False) -> str:
    from . import service_uv
    home = Path(environ.get('HOME') or str(Path.home()))
    common = [home / p for p in ('.local/bin', 'bin', '.local/share/mise/shims', '.asdf/shims',
                                 *NPM_BIN_DIRS, '.opencode/bin')]
    prefix = npm_prefix_bin(environ, home)
    if prefix:
        common.append(prefix)
    # The invoking App's PATH is intentionally never a source.
    candidates = login_path(environ).split(':') + [str(p) for p in common] + ['/opt/homebrew/bin', '/opt/homebrew/sbin']
    candidates += BASE.split(':')
    uv = service_uv.executable(home, provision=install)
    out = [str(Path(uv).parent)] if uv else []
    for entry in candidates:
        entry = os.path.normpath(entry) if entry.startswith('/') else entry
        if stable_entry(entry) and entry not in out and not service_uv.reserved(entry, home):
            out.append(entry)
    return ':'.join(out)
