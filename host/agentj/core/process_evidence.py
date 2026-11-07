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
