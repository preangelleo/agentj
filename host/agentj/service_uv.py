"""Expose only the pinned website-repair uv on the daemon's command search path."""
import hashlib
import os
from pathlib import Path
import shutil
import stat
import tempfile

from .upgrade_uv import PINS, target_platform


def store(home):
    return home / '.local/share/agentj/upgrade-bin'


def reserved(path, home):
    """Never import the mixed repair store (or a rejected service bin) from PATH."""
    root = store(home)
    candidate = Path(path)
    return candidate == root or root in candidate.parents


def _safe_root(root, home):
    if not home.is_absolute() or any(c in str(home) for c in ':\r\n'):
        return False
    owner = os.getuid()
    for part in (root, *root.parents):
        if part == home.parent:
            break
        info = part.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner or info.st_mode & 0o022:
            return False
    for part in home.resolve().parents:
        info = part.stat()
        if info.st_uid not in (0, owner) or (info.st_mode & 0o022
                and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX)):
            return False
    return True


def _valid(directory, digest):
    info = directory.lstat()
    if not (stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o500 and os.listdir(directory) == ['uv']):
        return False
    fd = os.open(directory / 'uv', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(fd)
        return (stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1
                and stat.S_IMODE(info.st_mode) == 0o500 and 0 < info.st_size <= 128_000_000
                and hashlib.file_digest(stream, 'sha256').hexdigest() == digest)


def executable(home, *, provision=True):
    """Create an immutable uv-only PATH entry from an older bootstrap seed, without network access."""
    pin = PINS.get(target_platform())
    if not pin:
        return None
    digest = pin[1]
    root = store(home)
    directory = root / ('service-' + digest)
    try:
        if not _safe_root(root, home):
            return None
        if os.path.lexists(directory):
            return str(directory/'uv') if _valid(directory, digest) else None
        if not provision:
            return None
        fd = os.open(root/'uv', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as source:
            info = os.fstat(fd)
            if not (stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                    and not info.st_mode & 0o022 and 0 < info.st_size <= 128_000_000):
                return None
            with tempfile.TemporaryDirectory(prefix='.service-', dir=root) as tmp:
                staged = Path(tmp)
                binary = staged/'uv'
                with binary.open('xb') as destination:
                    shutil.copyfileobj(source, destination, length=1024*1024)
                binary.chmod(0o500)
                staged.chmod(0o500)
                if not _valid(staged, digest):
                    return None
                try:
                    staged.rename(directory)
                except OSError:
                    # Another caller may have published the same pinned view.
                    if not _valid(directory, digest):
                        return None
        return str(directory/'uv')
    except (OSError, ValueError):
        return None
