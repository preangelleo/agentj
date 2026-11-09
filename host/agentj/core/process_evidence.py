"""Bounded PID evidence only: never inspect argv or enumerate processes."""
from __future__ import annotations
import os
import socket
import struct
import subprocess
import sys
from .. import codex_procs


def peer_capable():
    return sys.platform.startswith('linux') or sys.platform == 'darwin'


def peer_pid(writer):
    try:
        sock = writer.get_extra_info('socket')
        if sys.platform.startswith('linux'):
            return struct.unpack('3i', sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[0]
        if sys.platform == 'darwin':
            # Apple XNU bsd/sys/un.h: SOL_LOCAL=0, LOCAL_PEERPID=0x002.
            return struct.unpack('i', sock.getsockopt(0, 0x002, 4))[0]
    except (AttributeError, OSError, TypeError, ValueError, struct.error):
        pass
    return None


def peer_uid(sock):
    """Kernel uid of a unix-socket peer. Linux SO_PEERCRED; macOS LOCAL_PEERCRED (struct xucred: cr_version, cr_uid).

    0.30.2 (#0302): macOS Python has no SO_PEERCRED, so every gate comparing the peer uid with ours used to see None
    and refused the owner's own calls. None still means "unknown" and callers keep refusing on None.
    """
    try:
        if sys.platform.startswith('linux'):
            return struct.unpack('3i', sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1]
        if sys.platform == 'darwin':
            # Apple XNU bsd/sys/un.h: SOL_LOCAL=0, LOCAL_PEERCRED=0x001; XUCRED_VERSION=0.
            raw = sock.getsockopt(0, 0x001, 76)
            if len(raw) >= 8 and struct.unpack_from('I', raw, 0)[0] == 0:
                return struct.unpack_from('I', raw, 4)[0]
    except (AttributeError, OSError, TypeError, ValueError, struct.error):
        pass
    return None


def uid(pid):
    if type(pid) is not int or pid <= 1:
        return None
    try:
        r = subprocess.run(['ps', '-o', 'uid=', '-p', str(pid)], capture_output=True,
                           text=True, timeout=codex_procs.PS_TIMEOUT)
        return int(r.stdout.strip()) if r.returncode == 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def snapshot(pid):
    if type(pid) is not int or pid <= 1:
        return None
    token = codex_procs.start_token(pid)
    owner, parent = uid(pid), codex_procs.parent(pid)
    if not token or owner != os.getuid() or type(parent) is not int or parent < 0:
        return None
    if codex_procs.start_token(pid) != token:
        return None
    return token, owner, parent


def descendant(peer, anchor, token):
    """Every hop must be same UID, stable start/parent evidence, ending at the frozen anchor."""
    if not token or type(anchor) is not int or type(peer) is not int:
        return False
    seen, chain, pid = set(), [], peer
    while pid > 1 and pid not in seen and len(seen) < 128:
        seen.add(pid)
        row = snapshot(pid)
        if row is None:
            return False
        chain.append((pid, row))
        if pid == anchor:
            return row[0] == token and all(snapshot(p) == evidence for p, evidence in reversed(chain))
        pid = row[2]
    return False


def linux_row(pid):
    """/proc: (parent, start in CLOCK_BOOTTIME seconds, raw starttime ticks token) for a live same-uid process, or None."""
    if not sys.platform.startswith('linux') or type(pid) is not int or pid <= 1:
        return None
    try:
        if os.stat(f'/proc/{pid}').st_uid != os.getuid():
            return None
        with open(f'/proc/{pid}/stat', 'rb') as fh:
            s = fh.read().decode('utf-8', 'replace').rsplit(')', 1)[1].split()
        if s[0] in ('Z', 'X'):
            return None
        return int(s[1]), int(s[19]) / os.sysconf('SC_CLK_TCK'), s[19]     # boottime seconds (same clock as now())
    except (OSError, ValueError, IndexError):
        return None


def now():
    """The clock row() start times are in: CLOCK_BOOTTIME on Linux (immune to wall-clock jumps), wall clock on macOS."""
    import time
    if sys.platform.startswith('linux') and hasattr(time, 'CLOCK_BOOTTIME'):
        return time.clock_gettime(time.CLOCK_BOOTTIME)
    return time.time()


def row(pid):
    """Platform process row (parent, start epoch seconds, start token)."""
    return darwin_row(pid) if sys.platform == 'darwin' else linux_row(pid)


_LSTART = '%a %b %e %H:%M:%S %Y'   # `ps -o lstart` / ctime layout


def start_matches(expected, row):
    """The one predicate for "this registered session start (`procStart`) is the live process in row(pid)".

    Linux: exact token (raw /proc starttime ticks), unchanged. macOS: the token rendered in local time, or the same second
    rendered in UTC. ADR-03031: Claude Code (2.1.317, checked on a real Mac 2026-11-09) writes ~/.claude/sessions/<pid>.json
    `procStart` as `ps -o lstart` under TZ=UTC, while darwin_row renders local time, so a non-UTC Mac never matched its own
    owner session. Exactly these two renderings of the kernel start second; no other format, no tolerance.
    None / empty / non-string / no row is False.
    """
    if not isinstance(expected, str) or not expected or not row or len(row) < 3:
        return False
    if expected == str(row[2]):
        return True
    if sys.platform != 'darwin':
        return False
    try:
        import time
        return expected == time.strftime(_LSTART, time.gmtime(int(row[1])))
    except (TypeError, ValueError, OverflowError, OSError):
        return False


def darwin_row(pid):
    """XNU PROC_PIDTBSDINFO: stable same-user parent/start evidence, no argv or fork.

    Layout: apple-oss-distributions/xnu bsd/sys/proc_info.h, struct proc_bsdinfo.
    Returns (parent, exact start seconds, compatible lstart token), or None.
    """
    if sys.platform != 'darwin' or type(pid) is not int or pid <= 1:
        return None
    try:
        import ctypes
        import time
        from .. import shared_hook
        if shared_hook._LIBPROC is None:
            shared_hook._LIBPROC = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
        buf = ctypes.create_string_buffer(136)
        if shared_hook._LIBPROC.proc_pidinfo(pid, 3, ctypes.c_uint64(0), buf, 136) != 136:
            return None
        status, actual, parent, uid = struct.unpack_from('4I', buf.raw, 4)[0], *struct.unpack_from('3I', buf.raw, 12)
        real, saved = struct.unpack_from('I', buf.raw, 28)[0], struct.unpack_from('I', buf.raw, 36)[0]
        sec, usec = struct.unpack_from('2Q', buf.raw, 120)
        if actual != pid or status in (0, 5) or {uid, real, saved} != {os.getuid()} or not sec or usec >= 1000000:
            return None
        return parent, sec + usec / 1000000, time.strftime(_LSTART, time.localtime(sec))
    except (OSError, AttributeError, TypeError, ValueError, struct.error):
        return None
