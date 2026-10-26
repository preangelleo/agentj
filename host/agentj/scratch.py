"""P542 (FEEDBACK #306): throwaway HOMEs / scratch folders for isolated OpenCode runs.

An empty HOME that a killed run left in /tmp (OpenCode's npm plugins: node_modules/.bin executables, 111 MB) tripped a
server's tmp.executables audit for three days. They now live in the owner's private cache (0700; /tmp only when that cache is
unusable), go on every exit path (finally, atexit, serve's forced exit), and serve's start sweeps what a crash left: our
prefixes only, owned by this user, untouched for 6 h and used by no process.
"""
from __future__ import annotations

import atexit
import contextlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

PREFIXES = ('agentj-fallback-home-', 'agentj-free-list-', 'agentj-oc-probe-', 'agentj-oc2-probe-',
            'agentj-oc2-catalogue-', 'agentj-channel-models-')
MAX_AGE = 6 * 3600
_live: set[str] = set()
_lock = threading.Lock()


def root() -> str | None:
    """The private scratch parent: $AGENTJ_SCRATCH_DIR, else $XDG_CACHE_HOME (~/.cache)/agentj-scratch. None = use /tmp."""
    try:
        base = Path(os.environ.get('AGENTJ_SCRATCH_DIR') or
                    Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'agentj-scratch')
        base.mkdir(parents=True, exist_ok=True, mode=0o700)
        s = os.lstat(base)
        if not stat.S_ISDIR(s.st_mode) or (hasattr(os, 'getuid') and s.st_uid != os.getuid()):
            return None
        if s.st_mode & 0o077:
            os.chmod(base, 0o700)
        return str(base)
    except (OSError, RuntimeError):      # read-only cache (a fence), no resolvable home
        return None


def make(prefix: str) -> str:
    if prefix not in PREFIXES:
        raise ValueError(prefix)         # the sweep only knows these
    path = tempfile.mkdtemp(prefix=prefix, dir=root())
    with _lock:
        _live.add(path)
    return path


def remove(path: str) -> bool:
    ok = _rmtree(path)
    with _lock:
        _live.discard(path)
    return ok


@contextlib.contextmanager
def home(prefix: str):
    path = make(prefix)
    try:
        yield path
    finally:
        remove(path)


def cleanup_live() -> None:
    """Interpreter exit / serve's forced exit: whatever this process still holds."""
    with _lock:
        paths = list(_live)
    for p in paths:
        remove(p)


atexit.register(cleanup_live)


def _rmtree(path: str) -> bool:
    def retry(func, p, _exc):            # npm leaves read-only folders; a just-killed child may still be writing
        try:
            os.chmod(os.path.dirname(p), 0o700)
            if not os.path.islink(p) and os.path.isdir(p):
                os.chmod(p, 0o700)
            func(p)
        except OSError:
            pass
    for i in range(3):
        try:
            if sys.version_info >= (3, 12):
                shutil.rmtree(path, onexc=retry)
            else:
                shutil.rmtree(path, onerror=retry)
        except OSError:
            pass
        if not os.path.lexists(path):
            return True
        time.sleep(0.2 * (i + 1))
    return False


def _newest(path: str, s) -> float:
    newest = s.st_mtime
    with contextlib.suppress(OSError), os.scandir(path) as it:
        for e in it:
            with contextlib.suppress(OSError):
                newest = max(newest, e.stat(follow_symlinks=False).st_mtime)
    return newest


def _proc_paths() -> set[str] | None:
    """Linux: cwd / root / exe / open files / HOME-like environment of every process we may read. None = unknown."""
    if not os.path.isdir('/proc/self/fd'):
        return None
    out: set[str] = set()
    me = os.getpid()
    for pid in os.listdir('/proc'):
        if not pid.isdigit() or int(pid) == me:
            continue
        base = f'/proc/{pid}'
        for name in ('cwd', 'root', 'exe'):
            with contextlib.suppress(OSError):
                out.add(os.readlink(f'{base}/{name}'))
        with contextlib.suppress(OSError):
            for fd in os.listdir(f'{base}/fd'):
                with contextlib.suppress(OSError):
                    out.add(os.readlink(f'{base}/fd/{fd}'))
        with contextlib.suppress(OSError), open(f'{base}/environ', 'rb') as f:
            for item in f.read(1 << 20).split(b'\0'):
                key, _, value = item.partition(b'=')
                if key == b'HOME' or key.startswith(b'XDG_'):
                    out.add(os.fsdecode(value))
    return out


def _lsof_paths() -> set[str] | None:
    """macOS: this user's open files and cwds via lsof; unavailable or failed = unknown (nothing is deleted)."""
    exe = shutil.which('lsof')
    if not exe or not hasattr(os, 'getuid'):
        return None
    try:
        r = subprocess.run([exe, '-n', '-P', '-w', '-Fn', '-u', str(os.getuid())], stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if not r.stdout:
        return None
    return {os.fsdecode(line[1:]) for line in r.stdout.splitlines() if line.startswith(b'n')}


def used_paths() -> set[str] | None:
    return _proc_paths() if sys.platform.startswith('linux') else _lsof_paths()


def _used(path: str, used: set[str]) -> bool:
    names = {path, os.path.realpath(path)}
    return any(p == n or p.startswith(n + os.sep) for p in used for n in names)


def sweep(now: float | None = None, max_age: float = MAX_AGE, dirs=None, used=None) -> list[str]:
    """Remove what a crash left. Only our prefixes, real folders owned by us, older than max_age, no process using them;
    an unknown process table removes nothing."""
    now = time.time() if now is None else now
    dirs = dirs if dirs is not None else list(dict.fromkeys(d for d in (root(), tempfile.gettempdir()) if d))
    uid = os.getuid() if hasattr(os, 'getuid') else None
    with _lock:
        live = set(_live)
    gone: list[str] = []
    asked = used is not None
    for d in dirs:
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for name in names:
            if not name.startswith(PREFIXES):
                continue
            path = os.path.join(d, name)
            try:
                s = os.lstat(path)
            except OSError:
                continue
            if (path in live or not stat.S_ISDIR(s.st_mode) or (uid is not None and s.st_uid != uid)
                    or now - _newest(path, s) < max_age):
                continue
            if not asked:
                used, asked = used_paths(), True
            if used is None or _used(path, used):
                continue
            if _rmtree(path):
                gone.append(path)
    return gone
