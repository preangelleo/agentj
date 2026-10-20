"""P408 (ADR-P408-terminal M1 C1 H1 L3, PROTOCOL §38): the web terminal's pure parts — which lines need the owner's signed
confirmation, the platform and sudo checks, the scrubbed shell environment, and one running terminal's state (`Term`).

classify(line): `danger.classify("Bash")` + `approval_level.redline` + this module's own list — bare interpreters / shells,
`source` `.` `eval` `exec`, an interpreter given a file or code, `ssh` / `su`. It catches slips, not someone who already
holds the session (a command can be assembled across lines); the docs say so.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import re
import secrets
import shlex
import stat
import sys

from .term_mask import Masker

_SHELLS = re.compile(r"(?:ba|z|da|k|mk|c|tc|fi|a|r?ba)?sh|busybox|python[0-9.]*|pypy[0-9.]*|node(?:js)?|perl[0-9.]*|"
                     r"ruby[0-9.]*|irb|php[0-9.]*|lua[0-9.]*|tclsh|deno|bun|pwsh|osascript|Rscript")
_WRAPPERS = {"sudo", "env", "nohup", "time", "command", "builtin", "nice", "ionice", "stdbuf", "timeout", "setsid", "xargs",
             "doas", "chrt", "taskset", "unbuffer"}
_SAFE_ARGS = {"--version", "-V", "--help", "-h", "-v"}
DROP_ENV = {"BASH_ENV", "ENV", "PROMPT_COMMAND", "LD_PRELOAD", "LD_LIBRARY_PATH", "PS1", "PS2", "PS4", "HISTFILE", "SHELLOPTS",
            "BASHOPTS", "CDPATH", "GLOBIGNORE", "SUDO_ASKPASS", "INPUTRC"}
_DROP_ENV = re.compile(r"(?:_KEY|_TOKEN|_SECRET)$|PASSWORD|^AGENTJ_|^BASH_FUNC_|^DYLD_|^LD_|^GCONV_PATH$", re.I)   # R3: every LD_* and GCONV_PATH
REDLINE = {"injection": ("下载后直接运行，或者像是注入的指令", "downloads and runs code, or looks injected"),
           "credentials": ("会读出或发出密码、密钥", "reads or sends a password or key"),
           "spend": ("可能花钱或改价格", "may spend money or change a price")}

# ---------------------------------------------------------------- what is dangerous (M1)
def _own(line: str):
    try:
        lx = shlex.shlex(line, posix=True, punctuation_chars=";&|()<>")
        lx.whitespace_split = True
        toks = list(lx)
    except ValueError:
        return ("这一行没写完（引号没配对）", "the line is unfinished (unbalanced quotes)")
    cmds, cur = [], []
    for t in toks:
        if t and set(t) <= set(";&|()<>"):
            cmds.append(cur)
            cur = []
        else:
            cur.append(t)
    cmds.append(cur)
    for c in cmds:
        c = list(c)
        while c and (re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", c[0], re.S) or os.path.basename(c[0]) in _WRAPPERS
                     or (c[0].startswith("-") and len(c) > 1)):
            c.pop(0)
        if not c:
            continue
        w = os.path.basename(c[0])
        if w in ("source", ".", "eval", "exec"):
            return ("运行别处的代码（source / . / eval / exec）", "runs code from elsewhere (source / . / eval / exec)")
        if w in ("ssh", "su", "mosh", "telnet", "machinectl", "pkexec"):
            return ("登录到别的账户或机器（ssh / su）", "logs in to another account or machine (ssh / su)")
        if _SHELLS.fullmatch(w):
            rest = c[1:]
            if not rest:
                return ("打开交互解释器或 shell：之后输入的内容不再逐行检查", "opens an interpreter or shell: what you type "
                        "next is not checked line by line")
            if any(a not in _SAFE_ARGS for a in rest):
                return ("用解释器运行文件或代码", "runs a file or code with an interpreter")
    return None


def classify(line: str):
    """None = harmless; else (why, why_en). Never raises (cannot tell → dangerous)."""
    try:
        from . import approval_level, danger
        v = danger.classify("Bash", {"command": line})
        if v.cats:
            return (v.why or "危险命令", v.why_en or "a risky command")
        r = approval_level.redline("Bash", {"command": line}, v)
        if r:
            return REDLINE.get(r, ("需要你确认", "needs your confirmation"))
    except Exception:  # noqa: BLE001
        return ("无法判断这一行会做什么", "cannot tell what this line does")
    return _own(line)


# ---------------------------------------------------------------- environment, platform, sudo
def platform_ok() -> bool:
    return sys.platform.startswith("linux") and os.path.isdir("/proc/self")


def checked_sudo() -> str | None:
    """An absolute sudo that is root-owned, setuid and not writable by this user (or None)."""
    for p in ("/usr/bin/sudo", "/bin/sudo", "/usr/local/bin/sudo"):
        with contextlib.suppress(OSError):
            st = os.stat(p)
            if st.st_uid == 0 and st.st_mode & stat.S_ISUID and not st.st_mode & 0o022 and not os.access(p, os.W_OK):
                return p
    return None


def service_keys() -> set:
    names = set()
    with contextlib.suppress(Exception):
        from . import service
        path = os.environ.get("AGENTJ_SERVICE_ENV_FILE") or service.env_file(service.name())
        with open(path, encoding="utf-8", errors="replace") as f:
            for ln in f:
                m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", ln)
                if m:
                    names.add(m.group(1))
    with contextlib.suppress(Exception):
        from . import service
        names |= service._service_env_names()
    return names


def shell_env(base: dict, drop: set) -> dict:
    from .ctl_auth import AGENT_ENV
    return {k: v for k, v in base.items() if k not in drop and k not in DROP_ENV and k not in AGENT_ENV
            and not _DROP_ENV.search(k)}


def local_ok(h) -> bool:
    """`local` cache mode: only while the Agent is fenced AND independent (H2, N2)."""
    from . import preferences
    p, a = getattr(h, "preferences", None) or {}, getattr(h, "agent_cfg", None) or {}
    return preferences.get(p, "agent.isolation", True) is not False and a.get("fence", True) is not False and \
        preferences.get(p, "agent.session_mode", "independent") != "shared" and a.get("session_mode", "independent") != "shared"


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


# ---------------------------------------------------------------- one running terminal
class Term:
    def __init__(self, s, r, mode, pw, minutes, clock, wall):
        self.id, self.s, self.device, self.r, self.mode, self.pw = secrets.token_hex(8), s, s.device, r, mode, pw
        self.t0, self.expires = clock(), clock() + minutes * 60
        self.expires_at, self.last = int(wall() + minutes * 60), clock()
        self.token = secrets.token_urlsafe(32)
        self.proc = self.helper = self.server = self.cast = self.cast_name = None
        self.masker, self.seq, self.at_prompt, self.running, self.line = Masker(), 0, False, False, ""
        self.confirms, self.sudo, self.seen, self.recent = {}, {}, {}, ""
        self.win, self.win_bytes, self.dropped, self.hidden, self.closing = 0.0, 0, 0, None, False
        self.tasks: list[asyncio.Task] = []
        self.reader = self.writer = None      # R3 (M-1): our end of the shell's socketpair (no /proc-reopenable pipe)
        mark = secrets.token_hex(8)           # R3: the OSC 133 markers carry a per-session nonce PS1 holds (never exported)
        self.prompt, self.ready = f"\x1b]133;A;{mark}\x07", f"\x1b]133;R;{mark}\x07"
