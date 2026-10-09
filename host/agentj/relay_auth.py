"""Read the official AgentsRelay key file without executing shell or copying secrets.

Only the two official variable names are accepted. Values stay in child process
memory; service units and diagnostics never contain them. Owner env wins.
"""
import os
from pathlib import Path
import shlex
import stat

NAMES = frozenset(('AGENTSRELAY_OPENAI_KEY', 'AGENTSRELAY_CLAUDE_KEY'))

def environment(base=None):
    env = dict(os.environ if base is None else base)
    home = Path(env.get('HOME', str(Path.home())))
    path = home / '.config/agentsrelay/env.sh'
    try:
        # Reject symlink parents, non-owner files and group/world-readable credentials.
        if path.parent.resolve() != path.parent.absolute():
            return env
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'r') as f:
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
                return env
            raw = f.read(65537)
        if len(raw) > 65536:
            return env
        values = {}
        for line in raw.splitlines():
            if not line.startswith('export '):
                continue
            words = shlex.split(line[7:], comments=True)
            if len(words) != 1:
                continue
            key, sep, value = words[0].partition('=')
            if sep and key in NAMES and value and '\x00' not in value:
                values[key] = value
        for key, value in values.items():
            env.setdefault(key, value)
    except (OSError, ValueError):
        pass
    return env

def load():
    for key, value in environment().items():
        if key in NAMES:
            os.environ.setdefault(key, value)
