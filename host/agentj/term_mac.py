"""P437 (#202, ADR-P408-terminal §macOS): the web terminal on a macOS host — what Linux reads from /proc, from libproc and
sysctl; the shell (the owner's zsh, else /bin/bash 3.2); the launchd environment (PATH, a UTF-8 locale); the admin-account
check `agentj terminal enable` adds. Every rule of the Linux terminal stays: approval, Face ID, the cast, masking, limits.

Processes (no /proc): sysctl KERN_PROC_PID's `kinfo_proc` (ppid, effective uid, p_comm) for any pid; KERN_PROCARGS2 for argv and
environment (the kernel gives them for this user's processes only — a root sudo's argv falls back to `ps`, shown on the card
only, never trusted); getsid(2) for the session; proc_listallpids for the sweep. `LIB` is the backend; tests replace it.
No fence namespace on macOS (fence.py: sandbox-exec): like Linux, the terminal is serve's child running as the owner — it is
not the Agent and is never fenced; the passkey, the cards and the recording are what bound it.
"""
from __future__ import annotations

import contextlib
import ctypes
import ctypes.util
import grp
import os
import pathlib
import pwd
import struct
import subprocess
import sys

DARWIN = sys.platform == "darwin"
# the Command Line Tools' Python: its whole path chain is root-owned (Xcode's lives under /Applications, writable by every
# admin — so the /usr/bin/python3 stub that runs it is not a safe askpass interpreter, and without the tools that stub would
# pop the「安装命令行开发者工具」dialog on the Mac's screen: it is never run)
PYTHONS = ("/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/Current/bin/python3",
           "/Library/Developer/CommandLineTools/usr/bin/python3")
NO_PYTHON = ("这台 Mac 上找不到只有管理员能改的系统 python3（命令行开发工具里的那一份），网页终端里的 sudo 不能安全地取用记住的密码。"
             "在电脑上运行 xcode-select --install 装上命令行开发工具即可。/ No system python3 that only an administrator can "
             "change (the Command Line Tools one): sudo in the web terminal cannot use the remembered password safely. Run "
             "xcode-select --install at the computer.")
NOT_ADMIN = ("你的 Mac 账户不是管理员（系统设置 → 用户与群组），不能用 sudo：网页终端要一个用密码的 sudo。/ Your Mac account is not an "
             "administrator, so it cannot use sudo; the web terminal needs a password sudo.")
_PATHS = ("/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin")


class Lib:
    """The real backend (libSystem). Each call → a value or None; never raises."""
    CTL_KERN, KERN_ARGMAX, KERN_PROC, KERN_PROC_PID, KERN_PROCARGS2 = 1, 8, 14, 1, 49

    def __init__(self):
        self._c = None

    def _libc(self):
        if self._c is None:
            self._c = ctypes.CDLL(ctypes.util.find_library("c") or "/usr/lib/libSystem.B.dylib", use_errno=True)
        return self._c

    def bsd(self, pid: int) -> dict | None:
        """KERN_PROC_PID → struct kinfo_proc (648 bytes on 64-bit; any user's process, as ps reads it): p_comm @243,
        e_ucred.cr_uid (effective) @420, e_ppid @560 — offsets checked on macOS 26 arm64 against ps."""
        with contextlib.suppress(Exception):
            b = self._sysctl([self.CTL_KERN, self.KERN_PROC, self.KERN_PROC_PID, pid], 1024)
            if b and len(b) == KINFO_SIZE:
                return kinfo_fields(b)
        return None

    def _sysctl(self, mib: list, size: int) -> bytes | None:
        c = self._libc()
        arr = (ctypes.c_int * len(mib))(*mib)
        buf, n = ctypes.create_string_buffer(size), ctypes.c_size_t(size)
        if c.sysctl(arr, len(mib), buf, ctypes.byref(n), None, ctypes.c_size_t(0)) != 0:
            return None
        return buf.raw[:n.value]

    def args(self, pid: int) -> tuple[list, list] | None:
        """(argv, environ) from KERN_PROCARGS2 — this user's processes only."""
        with contextlib.suppress(Exception):
            raw = self._sysctl([self.CTL_KERN, self.KERN_ARGMAX], 4)
            argmax = struct.unpack("i", raw)[0] if raw and len(raw) == 4 else 1 << 20
            data = self._sysctl([self.CTL_KERN, self.KERN_PROCARGS2, pid], max(4096, argmax))
            if data:
                return parse_procargs(data)
        return None

    def ps_argv(self, pid: int) -> list:
        """Display only (a root sudo's argv the kernel will not give this user): `ps` words."""
        with contextlib.suppress(Exception):
            r = subprocess.run(["/bin/ps", "-ww", "-o", "args=", "-p", str(int(pid))], capture_output=True, text=True,
                               timeout=5, env={"LC_ALL": "C"}, stdin=subprocess.DEVNULL)
            if r.returncode == 0:
                return r.stdout.split()
        return []

    def sid(self, pid: int) -> int | None:
        with contextlib.suppress(OSError):
            return os.getsid(pid)
        return None

    def pids(self) -> list[int]:
        with contextlib.suppress(Exception):
            c = self._libc()
            n = c.proc_listallpids(None, 0)
            if n > 0:
                buf = (ctypes.c_int * (n + 64))()
                n = c.proc_listallpids(buf, ctypes.sizeof(buf))
                return [p for p in buf[:max(0, n)] if p > 0]
        return []


LIB = Lib()
KINFO_SIZE = 648


def kinfo_fields(b: bytes) -> dict:
    return {"ppid": struct.unpack_from("i", b, 560)[0], "euid": struct.unpack_from("I", b, 420)[0],
            "name": b[243:260].split(b"\0", 1)[0].decode("utf-8", "replace")}


def parse_procargs(data: bytes) -> tuple[list, list] | None:
    """KERN_PROCARGS2: int argc · exec path \\0 · \\0 padding · argc strings · environment strings · \\0 …"""
    if len(data) < 4:
        return None
    argc = struct.unpack_from("i", data)[0]
    if argc < 0 or argc > 1 << 16:
        return None
    end = data.find(b"\0", 4)
    if end < 0:
        return None
    i = end
    while i < len(data) and data[i] == 0:
        i += 1
    words = data[i:].split(b"\0")
    argv = [w.decode("utf-8", "replace") for w in words[:argc]]
    env = []
    for w in words[argc:]:
        if not w:
            break
        env.append(w.decode("utf-8", "replace"))
    return (argv, env) if len(argv) == argc else None


# ---------------------------------------------------------------- what term_askpass reads from /proc on Linux
def proc_info(pid: int) -> dict | None:
    """The Linux proc_info shape: {"ppid", "sid", "name", "euid", "argv"}. argv: the kernel's (this user's processes), else
    `ps` words for display; `name` (p_comm) and `euid` are what check_peer trusts."""
    b = LIB.bsd(pid)
    sid = LIB.sid(pid) if b else None
    if b is None or sid is None:
        return None
    got = LIB.args(pid)
    argv = got[0] if got else LIB.ps_argv(pid)
    return {"ppid": b["ppid"], "sid": sid, "name": b["name"], "euid": b["euid"], "argv": argv}


def in_session(shell_pid: int, token: str, needle: str) -> list[int]:
    """M2 sweep on macOS: this user's processes whose session is the shell's, or whose environment carries the token."""
    out, mine, me = [], os.getuid(), os.getpid()
    for pid in LIB.pids():
        if pid == me:
            continue
        b = LIB.bsd(pid)
        if b is None or b["euid"] != mine:
            continue
        if LIB.sid(pid) == shell_pid:
            out.append(pid)
            continue
        if token:
            got = LIB.args(pid)
            if got and needle in got[1]:
                out.append(pid)
    return out


def peer_uid(sock) -> int | None:
    """LOCAL_PEERCRED (SOL_LOCAL 0, opt 1): struct xucred {u_int cr_version; uid_t cr_uid; …} — macOS has no SO_PEERCRED."""
    with contextlib.suppress(OSError, AttributeError, struct.error, ValueError):
        raw = sock.getsockopt(0, 1, 76)
        if len(raw) >= 8 and struct.unpack_from("I", raw, 0)[0] == 0:      # XUCRED_VERSION
            return struct.unpack_from("I", raw, 4)[0]
    return None


# ---------------------------------------------------------------- history (#184 on both shells)
def unmetafy(b: bytes) -> bytes:
    """zsh writes its special bytes (NUL, 0x83–0xa2) in a history line as Meta (0x83) + (byte ^ 32)."""
    out, i = bytearray(), 0
    while i < len(b):
        if b[i] == 0x83 and i + 1 < len(b):
            out.append(b[i + 1] ^ 32)
            i += 2
        else:
            out.append(b[i])
            i += 1
    return bytes(out)


def hist_sensitive(line: str) -> bool:
    """#184's test: a line the masks would change (privacy.owner_text, and P414's agent_mask where this tree has it)."""
    from . import term_mask
    from .privacy import owner_text
    am = getattr(term_mask, "agent_mask", None)
    with contextlib.suppress(Exception):
        if am is not None and am(line)[1]:
            return True
    return owner_text(line) != line


def scrub_history(path, zsh: bool = False) -> int:
    """Rewrite the history file without its key / token / password lines, byte for byte otherwise (zsh's metafied bytes
    survive; a `\\`-continued entry goes whole). Atomic, 0600, no symlink followed. → entries dropped (missing file: 0)."""
    import secrets
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return 0
    with os.fdopen(fd, "rb") as f:
        raw = f.read()
    entries, cur = [], []
    for ln in raw.split(b"\n")[:-1] if raw.endswith(b"\n") else raw.split(b"\n"):
        cur.append(ln)
        if not (zsh and ln.endswith(b"\\")):
            entries.append(cur)
            cur = []
    if cur:
        entries.append(cur)
    keep = [e for e in entries if not any(hist_sensitive((unmetafy(x) if zsh else x).decode("utf-8", "replace")) for x in e)]
    if len(keep) == len(entries):
        return 0
    tmp = f"{path}.{secrets.token_hex(4)}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(b"".join(x + b"\n" for e in keep for x in e))
    os.replace(tmp, path)
    return len(entries) - len(keep)


# ---------------------------------------------------------------- the shell
def login_shell() -> str:
    with contextlib.suppress(KeyError, OSError):
        return pwd.getpwuid(os.getuid()).pw_shell or ""
    return ""


def pick_shell(shell: str | None = None, exists=os.path.exists) -> str:
    """"zsh" (macOS's default login shell; /bin/zsh) unless the owner's login shell is bash, or zsh is missing → "bash"."""
    shell = login_shell() if shell is None else shell
    if os.path.basename(shell or "") == "bash" and exists("/bin/bash"):
        return "bash"
    return "zsh" if exists("/bin/zsh") else "bash"


# -f: no rc file (only the root-owned /etc/zshenv, which zsh always reads); HIST_IGNORE_SPACE before the first setup line
ZSH_ARGV = ("/bin/zsh", "-f", "+m", "-o", "HIST_IGNORE_SPACE", "-i")


def zsh_setup(hist: str, sudo: str | None, helper: bool, mark: str, ignore: str, no_python: str) -> list[str]:
    """The zsh twin of terminal._start's bash setup: every line space-led (HIST_IGNORE_SPACE keeps it out of the file), the
    history rules of #184 (INC_APPEND_HISTORY so a killed shell lost nothing; HISTORY_IGNORE = the bash HISTIGNORE shapes),
    no partial-line marker, the same OSC 133 prompt / ready markers and the same checked `sudo -k -A` function."""
    import shlex
    pat = "(" + ignore.replace(":", "|") + ")"
    setup = [" setopt NO_BANG_HIST NO_MONITOR NO_PROMPT_CR NO_PROMPT_SP NO_BEEP NO_HUP NO_CHECK_JOBS PROMPT_PERCENT "
             "NO_PROMPT_SUBST NO_PROMPT_BANG HIST_IGNORE_SPACE HIST_IGNORE_DUPS INC_APPEND_HISTORY NO_SHARE_HISTORY",
             f" HISTFILE={shlex.quote(hist)}; HISTSIZE=2000; SAVEHIST=2000; HISTORY_IGNORE={shlex.quote(pat)}"]
    if sudo and helper:
        setup += [f" sudo() {{ {shlex.quote(sudo)} -k -A \"$@\"; }}", f" {shlex.quote(sudo)} -k 2>/dev/null"]
    elif sudo:
        setup += [f" sudo() {{ printf '%s\\n' {shlex.quote(no_python)} >&2; return 1; }}", f" {shlex.quote(sudo)} -k 2>/dev/null"]
    setup += [f" PS1=$'%{{\\e]133;A;{mark}\\a%}}%~ %# '; PS2='> '; RPS1=''; RPS2=''",
              f" printf '\\033]133;R;{mark}\\007'"]
    return setup


# ---------------------------------------------------------------- the launchd environment
def fix_env(env: dict, etc: str = "/etc", home: str | None = None) -> dict:
    """launchd hands a service PATH=/usr/bin:/bin:/usr/sbin:/sbin and no locale: add what path_helper would (/etc/paths,
    /etc/paths.d/*), Homebrew and ~/.local/bin after the service's own PATH, and a UTF-8 locale when none is set."""
    home = home or os.path.expanduser("~")
    have = [p for p in (env.get("PATH") or "").split(":") if p]
    extra, files = [], [os.path.join(etc, "paths")]
    with contextlib.suppress(OSError):
        files += [os.path.join(etc, "paths.d", n) for n in sorted(os.listdir(os.path.join(etc, "paths.d")))]
    for f in files:
        with contextlib.suppress(OSError, UnicodeDecodeError):
            extra += pathlib.Path(f).read_text(encoding="utf-8").split()
    for p in [*_PATHS, *extra, os.path.join(home, ".local", "bin")]:
        if p not in have and os.path.isabs(p):
            have.append(p)
    env["PATH"] = ":".join(have)
    cur = env.get("LC_ALL") or env.get("LC_CTYPE") or env.get("LANG") or ""
    if "UTF-8" not in cur.upper().replace("UTF8", "UTF-8"):
        env.pop("LC_ALL", None)
        env["LANG"] = "en_US.UTF-8"
        env["LC_CTYPE"] = "en_US.UTF-8"
    env.setdefault("SHELL", login_shell() or "/bin/zsh")
    return env


# ---------------------------------------------------------------- `agentj terminal enable` (macOS extras)
def is_admin(user: str | None = None) -> bool:
    """The account is in the `admin` group (macOS sudoers: %admin ALL=(ALL) ALL)."""
    try:
        pw = pwd.getpwnam(user) if user else pwd.getpwuid(os.getuid())
        gid = grp.getgrnam("admin").gr_gid
    except (KeyError, OSError):
        return False
    with contextlib.suppress(OSError):
        if gid in os.getgrouplist(pw.pw_name, pw.pw_gid):
            return True
    return pw.pw_gid == gid or pw.pw_name in grp.getgrgid(gid).gr_mem


def enable_problem(admin=None, local_auth=None) -> str | None:
    """None, or why `agentj terminal enable` cannot work on this Mac (after the shared password-sudo check)."""
    if not (is_admin() if admin is None else admin):
        return NOT_ADMIN
    from .elevate import LOCAL_AUTH_HINT, local_auth_risk
    if local_auth_risk() if local_auth is None else local_auth:
        return LOCAL_AUTH_HINT        # Touch ID / Watch in sudo's PAM would wait on the Mac's screen, not the phone
    return None
