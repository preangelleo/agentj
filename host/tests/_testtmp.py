"""P202: where tests put throw-away fixture folders. Tests used to write `dir="/var/tmp"`, which is on the system disk; on the
developer's machine that was 98 % of all disk writes (the release gate runs the suite many times a day). Order:

1. $AGENTJ_TEST_TMP — an explicit choice (the P44 gate sets it to its own throw-away root, so every fixture is removed with it);
2. /tmp, when it is a memory filesystem (tmpfs) with at least NEED bytes free for this user (its free space and, if /tmp has a
   per-user quota, the quota headroom);
3. $XDG_RUNTIME_DIR under the same rule (small, shared with the desktop session's sockets: only a fallback);
4. /var/tmp (on disk, as before) — macOS, or a /tmp that is on disk or nearly full. Memory is never filled to make room.

Short roots only: AF_UNIX socket paths are capped at 108 bytes and tests put sockets under these folders."""
import ctypes
import os
import platform
import tempfile

NEED = 2 << 30          # 2 GiB: no single test fixture is near it (the whole P44 gate root peaked at 1.05 GB, P202)
MAX_ROOT = 40           # longer roots risk the 108-byte AF_UNIX limit (the gate's own root is ~36 characters)


def _fstype(path):
    """Filesystem type of the mount that holds `path` (Linux /proc/self/mounts; None elsewhere)."""
    try:
        real, best, kind = os.path.realpath(path), "", None
        with open("/proc/self/mounts") as f:
            for line in f:
                parts = line.split()
                mnt = parts[1].replace("\\040", " ")
                if (real == mnt or real.startswith(mnt.rstrip("/") + "/")) and len(mnt) >= len(best):
                    best, kind = mnt, parts[2]
        return kind
    except OSError:
        return None


def _quota_left(path):
    """Bytes left in this user's quota on `path`'s filesystem, or None when there is no quota / it cannot be read."""
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "aarch64"):
        return None

    class Dqblk(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("bhard", "bsoft", "cur", "ihard", "isoft", "icur", "btime", "itime")] \
            + [("valid", ctypes.c_uint32)]
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        fd = os.open(path, os.O_RDONLY)
        try:
            q = Dqblk()
            if libc.syscall(443, fd, (0x800007 << 8) | 0, os.getuid(), ctypes.byref(q)) != 0:   # quotactl_fd(Q_GETQUOTA, USRQUOTA)
                return None
        finally:
            os.close(fd)
        limit = q.bhard or q.bsoft
        return None if not limit else max(0, limit * 1024 - q.cur)
    except (OSError, AttributeError):
        return None


def free_bytes(path):
    s = os.statvfs(path)
    free = s.f_bavail * s.f_frsize
    left = _quota_left(path)
    return free if left is None else min(free, left)


def in_memory(path, need=NEED):
    """`path` is a writable tmpfs folder with room for `need` more bytes."""
    try:
        return (bool(path) and os.path.isdir(path) and os.access(path, os.W_OK | os.X_OK)
                and _fstype(path) in ("tmpfs", "ramfs") and free_bytes(path) >= need)
    except OSError:
        return False


def test_tmp_root(environ=None, need=NEED):
    environ = os.environ if environ is None else environ
    chosen = environ.get("AGENTJ_TEST_TMP")
    if chosen:
        if not os.path.isabs(chosen) or not os.path.isdir(chosen):
            raise ValueError("AGENTJ_TEST_TMP must be an existing absolute folder")
        if len(chosen) > MAX_ROOT:
            raise ValueError(f"AGENTJ_TEST_TMP must be at most {MAX_ROOT} characters (AF_UNIX socket paths are capped)")
        return chosen
    for d in ("/tmp", environ.get("XDG_RUNTIME_DIR")):
        if d and len(d) <= MAX_ROOT and in_memory(d, need):
            return d
    return "/var/tmp" if os.path.isdir("/var/tmp") else tempfile.gettempdir()


TEST_TMP = test_tmp_root()
