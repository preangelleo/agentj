"""#461f: read-only questions to the owner's shared Codex app-server daemon (`codex app-server daemon`, measured on
codex-cli 0.166.0): a WebSocket JSON-RPC endpoint on `$CODEX_HOME/app-server-control/app-server-control.sock`.

Measured (isolated CODEX_HOME, fake Responses server): `daemon stop` with a turn running waits ~60 s, then ends the
daemon hard and the rollout keeps `task_started` with no `turn_aborted`; a daemon that later resumes that thread keeps the
rollout open for as long as it runs (also after its client left) and answers `thread/read` with status `idle` (the old turn
`interrupted`). An open rollout is then no proof of a live turn: the daemon itself says whether one runs.

Only `initialize` and `thread/read` (metadata, no turns) are sent: nothing is started, resumed, loaded or changed, and the
daemon is never started here. Anything unexpected (no socket, another user's socket, no answer in TIMEOUT s, an odd
frame) is None = unknown, and callers stay conservative."""
from __future__ import annotations

import base64
import contextlib
import json
import os
import re
import socket
import stat
import struct
import sys
import time
from pathlib import Path

TIMEOUT = 3.0             # s for the whole exchange
FRAME_MAX = 1 << 20       # bytes in one answer frame
STATUSES = ("active", "idle", "notLoaded", "systemError")
_VER = re.compile(r"/(\d+\.\d+\.\d+(?:[-.][0-9A-Za-z.]+)?)")


def socket_path() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "app-server-control" / "app-server-control.sock"


def _peer(sock) -> tuple[int | None, int | None]:
    """(pid, uid) of the process serving the socket; None where the platform does not say."""
    if sys.platform.startswith("linux"):
        pid, uid, _ = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
        return pid, uid
    if sys.platform == "darwin":
        pid = struct.unpack("i", sock.getsockopt(0, 0x002, 4))[0]        # SOL_LOCAL, LOCAL_PEERPID
        from .core.process_evidence import peer_uid
        return pid, peer_uid(sock)                                        # #0302: LOCAL_PEERCRED
    return None, None


class _Conn:
    def __init__(self, path: Path, deadline: float):
        self.deadline, self.buf = deadline, b""
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.settimeout(max(0.05, deadline - time.monotonic()))
        self.s.connect(str(path))
        self.pid, uid = _peer(self.s)
        if uid is not None and uid != os.getuid():
            raise OSError("socket served by another user")
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall(("GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        while b"\r\n\r\n" not in self.buf:
            self._fill(4096)
            if len(self.buf) > 16384:
                raise OSError("handshake too long")
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n", 1)[0] + b" ":
            raise OSError("no websocket upgrade")

    def _fill(self, n: int) -> None:
        self.s.settimeout(max(0.05, self.deadline - time.monotonic()))
        if time.monotonic() > self.deadline:
            raise TimeoutError
        d = self.s.recv(n)
        if not d:
            raise EOFError
        self.buf += d

    def _take(self, n: int) -> bytes:
        while len(self.buf) < n:
            self._fill(max(4096, n - len(self.buf)))
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def frame(self, op: int, data: bytes) -> None:
        mask, n = os.urandom(4), len(data)
        head = bytes([0x80 | op]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n))
        self.s.sendall(head + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(data)))

    def send(self, obj: dict) -> None:
        self.frame(1, json.dumps(obj).encode())

    def recv(self) -> dict | None:
        while True:
            b0, b1 = self._take(2)
            n = b1 & 127
            if n == 126:
                n = struct.unpack(">H", self._take(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._take(8))[0]
            if n > FRAME_MAX:
                raise OSError("frame too large")
            mask = self._take(4) if b1 & 0x80 else b""
            data = self._take(n)
            if mask:
                data = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
            op = b0 & 15
            if op == 8:
                raise EOFError
            if op == 9:
                self.frame(10, data)
            elif op == 1:
                with contextlib.suppress(ValueError):
                    obj = json.loads(data)
                    if isinstance(obj, dict):
                        return obj

    def call(self, i: int, method: str, params: dict) -> dict:
        self.send({"id": i, "method": method, "params": params})
        while True:
            m = self.recv()
            if m is not None and m.get("id") == i and "method" not in m:
                return m

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self.frame(8, b"")
        with contextlib.suppress(OSError):
            self.s.close()


def _ours(p: Path) -> bool:
    """Round 4 N4-4: the socket (not a link) and its folder are ours, the folder not group/other-writable — so another
    user cannot plant the endpoint, also on darwin where the peer uid is not available. (Same-uid fenced code still can:
    ADR-#461f GAP.)"""
    try:
        s, d = os.lstat(p), os.lstat(os.path.dirname(p))
    except OSError:
        return False
    uid = os.getuid()
    return stat.S_ISSOCK(s.st_mode) and s.st_uid == uid and stat.S_ISDIR(d.st_mode) and d.st_uid == uid \
        and not d.st_mode & 0o022


def _session(path: Path | None = None, timeout: float = TIMEOUT):
    """A connected, initialized daemon connection plus its initialize result; None = no daemon answering."""
    p = path or socket_path()
    if not _ours(p):
        return None
    c = None
    try:
        c = _Conn(p, time.monotonic() + timeout)
        # measured: the FIRST initialize sets the daemon's process-wide originator (later clients' names are kept only as a
        # suffix of the userAgent). Codex's own default name, so a probe that happens to come first changes nothing.
        init = c.call(1, "initialize", {"clientInfo": {"name": "codex_cli_rs", "version": "0"}, "capabilities": {}})
        if "result" not in init:
            c.close()
            return None
        c.send({"method": "initialized"})
        return c, init.get("result") or {}
    except (OSError, EOFError, TimeoutError, ValueError, struct.error):
        if c is not None:
            c.close()
        return None


def thread_status(sid: str, path: Path | None = None, timeout: float = TIMEOUT) -> dict | None:
    """{"status": active|idle|notLoaded|systemError, "pid": the daemon's pid or None} — None = unknown."""
    got = _session(path, timeout)
    if got is None:
        return None
    c, _ = got
    try:
        r = c.call(2, "thread/read", {"threadId": sid, "includeTurns": False})
    except (OSError, EOFError, TimeoutError, ValueError, struct.error):
        return None
    finally:
        c.close()
    err = r.get("error")
    if isinstance(err, dict):
        # measured: a thread the daemon has not loaded → -32600 "thread not loaded: <id>"
        return {"status": "notLoaded", "pid": c.pid} if "not loaded" in str(err.get("message") or "").lower() else None
    st = ((r.get("result") or {}).get("thread") or {}).get("status")
    kind = st.get("type") if isinstance(st, dict) else None
    return {"status": kind, "pid": c.pid} if kind in STATUSES else None


def server_version(path: Path | None = None, timeout: float = TIMEOUT) -> str | None:
    """The running daemon's Codex version (from its `initialize` userAgent), None = no daemon answering / not said."""
    got = _session(path, timeout)
    if got is None:
        return None
    c, res = got
    c.close()
    m = _VER.search(str(res.get("userAgent") or ""))
    return m.group(1) if m else None


def managed_version() -> str | None:
    """The daemon release Codex manages (`packages/app-server-daemon/current` → `releases/<version>-<target>`), read from
    the link only — works while the daemon is stopped (it starts that release on next use)."""
    with contextlib.suppress(OSError):
        name = Path(os.readlink(socket_path().parent.parent / "packages" / "app-server-daemon" / "current")).name
        m = re.match(r"(\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)[0-9.]*)?)-", name)
        return m.group(1) if m else None
    return None


def version_of(text: str | None) -> str | None:
    """`codex-cli 0.160.0` → `0.160.0`."""
    m = re.search(r"(\d+\.\d+\.\d+(?:[-.][0-9A-Za-z.]+)?)", text or "")
    return m.group(1) if m else None


def mismatch_text(cli: str, daemon: str, zh: bool) -> tuple[str, str]:
    """(what, fix) for doctor: the CLI Agent J runs and the shared daemon are different Codex versions."""
    if zh:
        return (f"Codex 版本不一致：命令行 {cli}，后台服务（app-server daemon）{daemon}",
                "在手机的网页终端运行 `codex app-server daemon stop` 结束后台服务（下次用到时会按命令行版本重启；"
                "先确认终端里的 codex 没有在回答），或把 Codex 命令行升级到同一版本，然后 `agentj service restart`")
    return (f"Codex versions differ: CLI {cli}, background service (app-server daemon) {daemon}",
            "in Web Terminal on your phone run `codex app-server daemon stop` (it restarts on next use; check no terminal codex is "
            "mid-answer first), or upgrade the Codex CLI to the same version, then `agentj service restart`")


TEXT = {
    "daemon_writer": ("Codex 后台服务（codex app-server daemon，PID {pid}）占着这个会话，里面并没有在跑的回合，手机现在只读。"
                      "先确认终端里的 codex 没在回答，再在 Agent J 主机运行 `codex app-server daemon stop`，然后重发。",
                      "The Codex background service (codex app-server daemon, PID {pid}) holds this session with no turn "
                      "running in it; the phone is read-only. Check no terminal codex is mid-answer, run `codex app-server "
                      "daemon stop` on the Agent J host, then resend."),
    "daemon_idle_turn": ("主机上 Codex 后台服务里这一轮早就不在跑了（上一轮没有正常结束）；不等了，直接递您的消息。",
                         "The desktop Codex's last turn never finished and the Codex background service shows nothing "
                         "running; sending your message now."),
}


def text(key: str, zh: bool) -> str:
    z, e = TEXT[key]
    return z if zh else e
