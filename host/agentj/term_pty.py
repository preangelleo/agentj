"""0.26.9 (#463, ADR-0269-pty, PROTOCOL §38.15): the web terminal's PTY mode — a real pseudo-terminal, the owner's login shell.

Everything P408 guarantees stays (terminal.py: Face ID per session, the login card and cache, limits, e-stop, notices, audit,
the checked `sudo -k -A`, the scrubbed environment); this module adds what a PTY changes:
  - the pair (`open_pair`): the slave is 0600, IUTF8 on, the size set before the shell starts; the shell gets it as its
    controlling terminal (setsid by the spawn + TIOCSCTTY in a two-line shim that then execs the shell);
  - the owner's login shell (bash / zsh; anything else → bash on Linux, zsh on macOS) with Agent J's setup ONLY (w2-sec H-2:
    no owner start-up files, INPUTRC=/dev/null) from a file serve writes (`rc_files`): the OSC 133 prompt marker (a
    per-session nonce) that also drops sudo's ticket, the private history file, history expansion off, `sudo` = the checked
    sudo with `-k -A`, line-running keys unbound, and a key binding that reports the line being edited (`QUERY_KEY` →
    OSC 133;L;nonce;len;line BEL) so the host knows the exact line at Enter;
  - `VtMasker`: escape sequences pass through untouched (OSC 52 clipboard writes are dropped, other OSC / DCS payloads go
    through the owner masking); token runs and PEM blocks are masked on the VISIBLE text, so a key split by colour codes or by
    frames is still one run;
  - the input gate (`PtyDesk`): at the shell prompt the host keeps its own copy of the line and holds every Enter (w2-sec H-1:
    and every other key that is not plain editing — KEY_OK); the shell's
    report (or, without one, the host's copy when it is certain) is classified exactly like a line-mode line; dangerous →
    `term_confirm`, the signed answer releases the Enter, a refusal / new input / 120 s sends Ctrl+E Ctrl+U. While a program
    is in front nothing is classified (stated residual). Input bytes are never recorded (cast = output + resizes);
  - fan-out (contract §B): `Term.viewers` (sessions that get term_out) and `Term.writers` (devices whose term_in is taken).
"""
from __future__ import annotations

import asyncio
import codecs
import contextlib
import os
import re
import secrets
import shlex
import shutil
import signal
import struct
import sys
import time

try:                                     # native Windows has neither: pty_ok() is False there
    import fcntl
    import termios
except ImportError:                      # pragma: no cover
    fcntl = termios = None

CAP = "term_pty1"
COLS, ROWS = (20, 400), (5, 200)
IN_FRAME, IN_RATE = 8 * 1024, 64 * 1024           # term_in: bytes per frame / per second
OUT_BUF = 64 * 1024                               # unread input buffered for the PTY before term_in is refused
QUERY_KEY = "\x18\x0c"                            # Ctrl+X Ctrl+L: the shell's binding prints the line it is editing
QUERY_WAIT, AFTER_ENTER, IDLE_FLUSH = 1.5, 0.3, 0.1
TYPED_KEEP = 4096                                 # recent typed text, memory only (never while a program reads echo-off)
KILL_LINE = "\x05\x15"                            # Ctrl+E Ctrl+U: end of line, then erase it (bash's C-u erases only left)
ESC_HOLD = 16 * 1024                              # an unterminated escape longer than this is dropped
PASTE_ON, PASTE_OFF = "\x1b[200~", "\x1b[201~"
LIVE: dict[int, str] = {}                         # slave st_rdev → tty name of every open web-terminal PTY (ctl_auth.has_tty)
# w2-sec H-1: at the prompt only these keys pass straight through; any other C0 control / ESC key (Ctrl+O, Ctrl+X …, Esc a,
# Home, F-keys …) is held and the line classified first, exactly like Enter. Ctrl+C is the line discipline's SIGINT (it
# discards the line, never runs it).
KEY_OK = frozenset(("\x08", "\x7f", "\x15", "\x17", "\x01", "\x05", "\t", "\x03",
                    "\x1b[A", "\x1b[B", "\x1b[C", "\x1b[D", "\x1bOA", "\x1bOB", "\x1bOC", "\x1bOD"))
_KEY = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|O.|.)?|\x18.?", re.S)     # one key: CSI / SS3 / Meta+char / lone ESC / C-x chord
SIGNAL_KEYS = ("\x03", "\x1c", "\x1a")                                   # Ctrl+C / Ctrl+\ / Ctrl+Z: never queued behind a hold
# w2-sec H-1: a line that re-runs history (`fc`, zsh/ksh `r`, bash quick substitution `^old^new`) — what it runs is not
# the text that was classified → always a confirm
_HIST_RERUN = re.compile(r"^\s*\^|(?:^|[;&|(\n`{]|\$\()\s*(?:\w+=\S*\s+)*(?:(?:builtin|command|exec|noglob|nocorrect)\s+)*"
                         r"\\?(?:fc|r)(?=\s|$|[;&|)])")
_ALT = re.compile(r"\x1b\[\?([0-9;]*)([hl])")                                # DECSET / DECRST (alternate screen: 47 1047 1049)
# Only complete native xterm colour/cursor reports; no command, newline or arbitrary OSC payload.
_REPLY = re.compile(r"(?:\x1b\[0n|\x1b\](?:(?:10|11|12)|4;(?P<index>[0-9]{1,3}));rgb:[0-9a-fA-F]{1,4}/[0-9a-fA-F]{1,4}/[0-9a-fA-F]{1,4}(?:\x07|\x1b\\)|\x1b\[\??[1-9][0-9]{0,3};[1-9][0-9]{0,3}R)")


def terminal_reply(text: str) -> bool:
    m = _REPLY.fullmatch(text) if len(text) <= 128 else None
    return m is not None and (m["index"] is None or int(m["index"]) <= 255)


REFUSED_EVERY = 60.0                                                         # w2-sec L-4: one control_refused per minute

# w2-sec N-3: before the shell starts, the shim moves itself into the session's own cgroup (`cg_make`; AJ_TERM_CG = its
# cgroup.procs) so every process the web terminal ever starts — setsid, script, a new tmux server, env -i — stays tagged
CG_MOVE = ("p=os.environ.pop('AJ_TERM_CG','')\n"
           "if p:\n"
           "    try:\n"
           "        f=open(p,'w'); f.write('0'); f.close()\n"
           "    except OSError: pass\n")
SHIM = ("import fcntl,os,sys,termios\n" + CG_MOVE +
        "fcntl.ioctl(0,termios.TIOCSCTTY,0)\n"
        "os.execv(sys.argv[1],sys.argv[1:])")
CG_SHIM = "import os,sys\n" + CG_MOVE + "os.execv(sys.argv[1],sys.argv[1:])"      # line mode (no controlling terminal)
CG_PREFIX = "agentj-term-"
CG_FS = "/sys/fs/cgroup"
CG_STALE = 30.0           # w2-sec R3-3: an empty leftover is removed only when older than this (a concurrent open is younger)
LIVE_CG: set[str] = set()  # this process's open session cgroups (cgroup.procs paths) — never removed as leftovers
# w2-sec R3-4: root-owned system directories go first on the web terminal's PATH (an owner ~/bin tool that shadows a system
# name — or one a fenced Agent planted there — no longer wins); the rest of PATH follows unchanged
SYS_PATH = ("/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin")
SYS_PATH_MAC = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")       # /usr/local and /opt/homebrew are admin/user-writable there


def pty_ok() -> bool:
    """PTY mode on this platform: Linux / WSL and macOS (not native Windows)."""
    return (sys.platform.startswith("linux") or sys.platform == "darwin") and termios is not None and \
        hasattr(termios, "TIOCSCTTY") and hasattr(os, "openpty")


def size_ok(cols, rows) -> bool:
    return type(cols) is int and type(rows) is int and COLS[0] <= cols <= COLS[1] and ROWS[0] <= rows <= ROWS[1]


def set_size(fd: int, cols: int, rows: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def open_pair(cols: int, rows: int) -> tuple[int, int]:
    """(master, slave): slave 0600 (no `write` / `wall` from the tty group), IUTF8, the size set; master non-blocking."""
    master, slave = os.openpty()
    try:
        os.fchmod(slave, 0o600)
        a = termios.tcgetattr(slave)
        if hasattr(termios, "IUTF8"):
            a[0] |= termios.IUTF8
        termios.tcsetattr(slave, termios.TCSANOW, a)
        set_size(master, cols, rows)
        os.set_blocking(master, False)
    except Exception:
        os.close(master)
        os.close(slave)
        raise
    return master, slave


def password_mode(fd: int) -> bool:
    """A program is reading a line with echo off (getpass: canonical, no ECHO) — readline at the prompt is non-canonical."""
    with contextlib.suppress(Exception):
        lflag = termios.tcgetattr(fd)[3]
        return bool(lflag & termios.ICANON) and not lflag & termios.ECHO
    return False


def utf8_locale(env: dict) -> dict:
    cur = env.get("LC_ALL") or env.get("LC_CTYPE") or env.get("LANG") or ""
    if "UTF-8" not in cur.upper().replace("UTF8", "UTF-8"):
        env.pop("LC_ALL", None)
        env["LANG"] = env["LC_CTYPE"] = "en_US.UTF-8" if sys.platform == "darwin" else "C.UTF-8"
    return env


# ---------------------------------------------------------------- non-blocking master I/O
class PtyIO:
    def __init__(self, fd: int):
        self.fd, self.out, self.closed = fd, bytearray(), False
        self.loop = asyncio.get_running_loop()
        self._writing = False

    async def read(self, n: int, timeout: float) -> bytes | None:
        """→ bytes, b"" at the end (EIO once the slave side is gone), None after `timeout` with nothing to read."""
        while not self.closed:
            try:
                return os.read(self.fd, n)
            except BlockingIOError:
                pass
            except OSError:
                return b""
            fut = self.loop.create_future()
            self.loop.add_reader(self.fd, lambda: fut.done() or fut.set_result(None))
            try:
                await asyncio.wait_for(fut, timeout)
            except asyncio.TimeoutError:
                return None
            finally:
                with contextlib.suppress(Exception):
                    self.loop.remove_reader(self.fd)
        return b""

    def write(self, data: bytes) -> bool:
        """Queue and write without waiting; False (nothing queued) when > OUT_BUF is still unread by the program."""
        if self.closed:
            return False
        if len(self.out) + len(data) > OUT_BUF:
            return False
        self.out += data
        self._flush()
        return True

    def _flush(self) -> None:
        while self.out and not self.closed:
            try:
                k = os.write(self.fd, self.out)
            except BlockingIOError:
                break
            except OSError:
                self.out.clear()
                return
            del self.out[:k]
        if self.out and not self._writing and not self.closed:
            self._writing = True
            self.loop.add_writer(self.fd, self._on_writable)
        elif not self.out and self._writing:
            self._writing = False
            with contextlib.suppress(Exception):
                self.loop.remove_writer(self.fd)

    def _on_writable(self) -> None:
        self._flush()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        for f in (self.loop.remove_reader, self.loop.remove_writer):
            with contextlib.suppress(Exception):
                f(self.fd)
        with contextlib.suppress(OSError):
            os.close(self.fd)


# ---------------------------------------------------------------- the VT-aware masker
_VT = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[PX^_][^\x1b]*\x1b\\|\x1b[ -/]*[0-~]"
                 r"|[\x00-\x08\x0b\x0c\x0e-\x1a\x1c-\x1f\x7f]")
_VT_PREFIX = re.compile(r"\x1b(?:\[[0-?]*[ -/]*|\][^\x07\x1b]*\x1b?|[PX^_][^\x1b]*\x1b?|[ -/]*)?")


def _string_seq(seq: str, mask) -> str:
    """OSC / DCS / APC / PM payloads: OSC 52 (a program writing the phone's clipboard) is dropped; any other payload goes
    through the owner masking (a token in a window title or a hyperlink)."""
    if seq.startswith("\x1b]"):
        body = seq[2:-1] if seq.endswith("\x07") else seq[2:-2]
        end = seq[len(body) + 2:]
        if body.split(";", 1)[0] == "52":
            return ""
        m = mask(body)
        return seq if m == body else "\x1b]" + m + end
    if seq[:2] in ("\x1bP", "\x1bX", "\x1b^", "\x1b_"):
        body = seq[2:-2]
        m = mask(body)
        return seq if m == body else seq[:2] + m + "\x1b\\"
    return seq


class VtMasker:
    """The P408 masker over a VT stream: escapes are taken out, the visible text runs through `term_mask.Masker(raw=True)`
    (token runs, PEM blocks, owner_text with a 4 KiB left context), and each escape is put back at its place when the text
    around it came out unchanged — otherwise (something was masked there) after the masked text, so no part of a key that a
    colour code or a frame boundary cut survives. Pure, no I/O."""

    def __init__(self, mask=None):
        from .term_mask import Masker, _owner
        self.mask = mask or _owner
        self.m = Masker(self.mask, raw=True)
        self.esc = ""          # an escape cut at the end of the last chunk
        self.vis = ""          # visible text fed and not yet resolved (starts at absolute index self.done)
        self.pend: list[tuple[int, str]] = []   # (absolute visible index it precedes, escape) not emitted yet
        self.vpos = self.done = 0

    @property
    def carriage_count(self) -> int:
        return self.m.carriage_count

    def feed(self, text: str) -> str:
        text, self.esc = self.esc + text, ""
        i = text.rfind("\x1b")
        if i >= 0 and _VT_PREFIX.fullmatch(text, i):
            text, rest = text[:i], text[i:]
            self.esc = rest if len(rest) <= ESC_HOLD else ""     # an endless unterminated string is dropped
        parts, pos = [], 0
        for m in _VT.finditer(text):
            seg = text[pos:m.start()]
            parts.append(seg)
            self.vpos += len(seg)
            seq = _string_seq(m.group(), self.mask)
            if seq:
                self.pend.append((self.vpos, seq))
            pos = m.end()
        seg = text[pos:]
        parts.append(seg)
        self.vpos += len(seg)
        visible = "".join(parts)
        self.vis += visible
        return self._emit(self.m.feed(visible))

    def flush(self, force: bool = False) -> str:
        return self._emit(self.m.flush(force))

    def held(self) -> str:
        return self.m.held

    def close(self) -> str:
        out = self._emit(self.m.close(), final=True)
        self.esc = ""
        return out

    def _emit(self, out: str, final: bool = False) -> str:
        new_done = self.vpos if final else self.vpos - len(self.m.held)
        raw = self.vis[:new_done - self.done]
        now = [p for p in self.pend if p[0] <= new_done]
        self.pend = [p for p in self.pend if p[0] > new_done]
        n = len(raw)
        p = 0                                    # unchanged head / tail: their escapes go back exactly in place
        while p < min(n, len(out)) and raw[p] == out[p]:
            p += 1
        q = 0
        while q < min(n, len(out)) - p and raw[n - 1 - q] == out[len(out) - 1 - q]:
            q += 1
        if p == n and len(out) == n:
            p, q = n, 0
        rel = [(off - self.done, seq) for off, seq in now]

        def weave(a: int, b: int, items) -> str:
            res, k = [], a
            for off, seq in items:
                res.append(raw[k:off])
                res.append(seq)
                k = off
            res.append(raw[k:b])
            return "".join(res)
        head = weave(0, p, [r for r in rel if r[0] <= p])
        mid = out[p:len(out) - q] + "".join(seq for off, seq in rel if p < off < n - q)   # the masked part: escapes after it
        tail = weave(n - q, n, [r for r in rel if r[0] >= n - q and r[0] > p])
        res = head + mid + tail
        self.vis = self.vis[new_done - self.done:]
        self.done = new_done
        return res


def hist_rerun(line: str) -> bool:
    """w2-sec H-1: the line re-runs a history entry (`fc …`, `r`, `^a^b`)."""
    return bool(_HIST_RERUN.search(line or ""))


def main_screen(x, data: str) -> str:
    """w2-sec M-4: the part of masked VT output written on the MAIN screen; while the alternate screen is on (vim, less,
    top, a pager redrawing a secret) nothing goes to the transcript / the 「!」 result the Agent can read. `x.alt` carries
    the state across chunks (VtMasker never cuts an escape)."""
    out, pos = [], 0
    for m in _ALT.finditer(data):
        if not x.alt:
            out.append(data[pos:m.start()])
        if {"47", "1047", "1049"} & set(m.group(1).split(";")):
            x.alt = m.group(2) == "h"
        pos = m.end()
    if not x.alt:
        out.append(data[pos:])
    return "".join(out)


def plain(text: str) -> str:
    """Masked VT output → text for the transcript / the 「!」 result / sudo's message (no escapes, CR as a line end)."""
    return _VT.sub("", text).replace("\r\n", "\n").replace("\r", "\n")


# ---------------------------------------------------------------- the shell and its start-up files
def pick_shell(override: str | None = None) -> str:
    """The owner's login shell when it is bash or zsh; otherwise bash on Linux, zsh on macOS (the OSC 133 / line-report
    setup is written for these two)."""
    if override:
        return override
    from .term_mac import login_shell
    name = os.path.basename(login_shell() or "")
    if name in ("bash", "zsh") and (shutil.which(name) or os.path.exists(f"/bin/{name}")):
        return name
    return "zsh" if sys.platform == "darwin" and os.path.exists("/bin/zsh") else "bash"


def shell_path(name: str) -> str:
    for p in (f"/bin/{name}", f"/usr/bin/{name}"):
        if os.path.exists(p):
            return p
    return shutil.which(name) or name


def _setup(shell: str, hist: str, sudo: str | None, helper: bool, mark: str, ignore: str, nopy: str) -> list[str]:
    """Agent J's own start-up lines — the only ones the PTY shell reads (w2-sec H-2: no owner start-up files)."""
    q = shlex.quote
    out = ["unalias sudo 2>/dev/null", "unset PS0 MAIL MAILPATH 2>/dev/null"]    # w2-sec R3-1 (also dropped from the env)
    # w2-sec M-3: `sudo -k` on EVERY prompt too, so a direct `\sudo` / `command sudo` / `/usr/bin/sudo` that prompted on the
    # PTY leaves no ticket for the next line (the function keeps -k on every call through it)
    drop = f"{q(sudo)} -k 2>/dev/null; " if sudo else ""
    if sudo and helper:     # R3 (H-2) kept: -k on every call; -A: the password comes from the phone's card, never a keystroke
        out += [f"sudo() {{ {q(sudo)} -k -A \"$@\"; }}", f"{q(sudo)} -k 2>/dev/null"]
    elif sudo:
        out += [f"sudo() {{ printf '%s\\n' {q(nopy)} >&2; return 1; }}", f"{q(sudo)} -k 2>/dev/null"]
    if shell == "zsh":
        pat = "(" + ignore.replace(":", "|") + ")"
        out += [f"HISTFILE={q(hist)}; HISTSIZE=2000; SAVEHIST=2000; HISTORY_IGNORE={q(pat)}",
                "setopt HIST_IGNORE_SPACE HIST_IGNORE_DUPS INC_APPEND_HISTORY NO_SHARE_HISTORY",
                "setopt NO_BANG_HIST",          # w2-sec H-1: no `!!` / `^a^b` re-running an altered line unseen
                # w2-sec H-1: keys that run the line without Enter (accept-line-and-down-history, accept-and-hold,
                # edit-command-line) are unbound; the host also holds any such key (PtyDesk._pty_feed)
                "for __aj_m in emacs viins vicmd; do bindkey -M $__aj_m -r '^O' '^[a' '^[A' '^X^E' 2>/dev/null; done; "
                "bindkey -M vicmd -r 'v' 2>/dev/null; unset __aj_m",
                "PROMPT='%1~ %# '",
                f"__aj_p() {{ local s=$?; {drop}print -n $'\\e]133;A;{mark}\\a'; return $s; }}",
                "precmd_functions=(${precmd_functions:#__aj_p} __aj_p)",
                f"__aj_q() {{ print -rn -- $'\\e]133;L;{mark};'\"${{#BUFFER}};${{BUFFER}}\"$'\\a'; }}",
                "zle -N __aj_q",
                "for __aj_m in emacs viins vicmd; do bindkey -M $__aj_m '^X^L' __aj_q; done; unset __aj_m",
                f"print -n $'\\e]133;R;{mark}\\a'"]
    else:
        out += [f"HISTFILE={q(hist)}; HISTSIZE=2000; HISTFILESIZE=2000; HISTCONTROL=ignorespace:ignoredups; HISTIGNORE={q(ignore)}",
                "set +H",                       # w2-sec H-1: history expansion off (`!!`, `!rm:s/…/`, `^a^b`)
                # w2-sec H-1: operate-and-get-next, edit-and-execute-command (emacs C-x C-e, vi `v`) unbound; the host also
                # holds any such key (PtyDesk._pty_feed)
                "for __aj_m in emacs vi-insert vi-command; do bind -m $__aj_m -r '\\C-o' 2>/dev/null; "
                "bind -m $__aj_m -r '\\C-x\\C-e' 2>/dev/null; done; bind -m vi-command -r v 2>/dev/null; unset __aj_m",
                "PS1='\\W \\$ '; PS2='> '",            # short: the owner's own prompt is not read (H-2)
                f"__aj_p() {{ local s=$?; {drop}printf '\\033]133;A;{mark}\\007'; return $s; }}",
                'if [[ "$(declare -p PROMPT_COMMAND 2>/dev/null)" == "declare -a"* ]]; then PROMPT_COMMAND+=(__aj_p); '
                "else PROMPT_COMMAND=\"${PROMPT_COMMAND:+$PROMPT_COMMAND$'\\n'}__aj_p\"; fi",
                # bash ≥ 4 only (READLINE_LINE); bash 3.2 (macOS) has no report: the host's own copy decides (fail closed)
                f"if ((BASH_VERSINFO[0] >= 4)); then __aj_q() {{ printf '\\033]133;L;{mark};%s;%s\\007' "
                "\"${#READLINE_LINE}\" \"$READLINE_LINE\"; }; "
                "for __aj_m in emacs vi-insert vi-command; do bind -m $__aj_m -x '\"\\C-x\\C-l\": __aj_q'; done; unset __aj_m; fi",
                f"printf '\\033]133;R;{mark}\\007'"]
    return out


def rc_files(d, shell: str, setup: list[str], env: dict) -> list[str]:
    """Write the start-up file(s) into the fresh 0700 dir `d` → the shell's argv (env is updated in place for zsh).

    w2-sec H-2 (security review round 1, option b): the PTY shell reads Agent J's own setup ONLY — never ~/.bashrc,
    ~/.profile, ~/.zshrc …, /etc/profile (it pulls ~/.bash_completion through bash-completion) or ~/.inputrc
    (INPUTRC=/dev/null, `_pty_spawn`): a fenced Agent can write those files, and the web terminal runs as the owner,
    unfenced. bash: `--norc --noprofile` + a one-shot PROMPT_COMMAND (N-1, below). zsh still reads /etc/zshenv (root-owned;
    zsh cannot skip it — a residual only if an administrator made it source user files) and nothing else global
    (NO_GLOBAL_RCS); no compinit, so no completion function is loaded from anywhere."""
    path = shell_path(shell)

    def put(name: str, text: str) -> str:
        p = os.path.join(d, name)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    body = "\n".join(setup) + "\n"
    if shell == "zsh":
        env.pop("AJ_USER_ZDOTDIR", None)
        env.pop("FPATH", None)                             # w2-sec N-1: no function path from outside (no compinit either)
        env["ZDOTDIR"] = str(d)
        put(".zshenv", "setopt NO_GLOBAL_RCS\n")          # read after /etc/zshenv: no /etc/zprofile, /etc/zshrc, /etc/zlogin
        put(".zshrc", "unset ZDOTDIR\n" + body)           # interactive, not a login shell: .zprofile / .zlogin are not read
        return [path, "-i"]
    # w2-sec N-1 (security round 2): `--rcfile` does not stop a SYS_BASHRC build (Arch and its derivatives) from reading
    # /etc/bash.bashrc first, which loads bash-completion → ~/.bash_completion and the user's completions directory (both
    # Agent-writable). So bash starts with NOTHING (`--norc --noprofile`) and Agent J's file is read by a one-shot
    # PROMPT_COMMAND at the first prompt (the file unsets it, then installs __aj_p and prints the first prompt marker
    # itself); anything typed before that waits (M-2: the shell in front without a prompt). bash-completion, should the
    # owner load it by hand, is pointed at nothing of theirs, and no completion spec is left from anywhere.
    nodir = os.path.join(d, "no-completions")
    os.mkdir(nodir, 0o700)
    env.update(BASH_COMPLETION_USER_FILE="/dev/null", BASH_COMPLETION_USER_DIR=nodir)
    rc = put("rc", "unset PROMPT_COMMAND\n" + body + "complete -r 2>/dev/null\n__aj_p\n")
    env["PROMPT_COMMAND"] = ". " + shlex.quote(rc)
    return [path, "--norc", "--noprofile", "-i"]


# ---------------------------------------------------------------- the host's own copy of the line at the prompt
def edit_apply(x, text: str) -> None:
    """Follow simple line editing; anything else (arrows, TAB, Ctrl+R …) makes the copy uncertain until the next prompt."""
    i = 0
    while i < len(text):
        if text.startswith(PASTE_ON, i) or text.startswith(PASTE_OFF, i):
            i += len(PASTE_ON)
            continue
        c = text[i]
        i += 1
        if c in ("\x7f", "\x08"):
            x.edit = x.edit[:-1]
        elif c in ("\x15", "\x03"):
            x.edit = ""
        elif c == "\x17":
            x.edit = re.sub(r"\S+\s*$", "", x.edit.rstrip(" ")) if x.edit.strip() else ""
        elif c in "\r\n" and x.paste:
            x.edit += "\n"
        elif ord(c) < 32 or c == "\x1b":
            x.edit_ok = False
            return
        else:
            x.edit += c
    if len(x.edit) > 8192:
        x.edit_ok = False


# ---------------------------------------------------------------- terminal.Desk's PTY half
class PtyDesk:
    """Mixed into terminal.Desk (`self.h`, `self.st`, `self.clock`, `_frame`, `_cast`, `close`, `_log`, `go_match` …)."""

    def pty_supported(self) -> bool:
        ok = getattr(self, "pty_ok", None)
        return self.supported() and (pty_ok() if ok is None else bool(ok)) and not self.pty_blocked()

    def pty_blocked(self) -> str | None:
        """w2-sec H-3: Linux, the Agent fenced (isolation on) and this kernel / bwrap gave the fence no private /dev (fence.problem
        fell back to the host's /dev): the fenced Agent could open the web terminal's /dev/pts/N (read the keystrokes, a sudo
        password included; write fake output) → no PTY mode; line mode (a socketpair, nothing on /dev/pts) stays. The
        flag is set by the same probe that decides the fence argv in this serve process, so it is authoritative here."""
        if not sys.platform.startswith("linux") or not (getattr(self.h, "agent_cfg", None) or {}).get("fence"):
            return None
        from . import fence
        if fence.PRIVATE_DEV[0] is not False:
            return None
        return ("这台 Agent J 主机的隔离环境没有自己的 /dev，Agent 能打开终端设备，所以只提供逐行模式 / this Agent J host's isolation has "
                "no private /dev (the Agent could open terminal devices), so only line mode is offered")

    # ------------------------------------------------------------ start
    async def _pty_spawn(self, x, cwd: str, env: dict, hist: str, sudo, helper: bool, mark: str, ignore: str, nopy: str,
                         shell: str) -> None:
        from . import term_store as store
        d = store.root(self.st) / ("sh-" + secrets.token_hex(8))
        d.mkdir(mode=0o700)
        x.rc_dir = d
        env = utf8_locale(env)
        env["INPUTRC"] = "/dev/null"                    # w2-sec H-2: no ~/.inputrc macro rewrites a line after it was checked
        # Native colour/cursor reports now reach foreground programs immediately; keep Nvim's theme detection enabled.
        env.update(TERM="xterm-256color", COLORTERM="truecolor", SHELL=shell_path(shell))
        argv = rc_files(str(d), shell, _setup(shell, hist, sudo, helper, mark, ignore, nopy), env)
        master, slave = open_pair(x.cols, x.rows)
        try:
            st = os.fstat(slave)
            x.tty = (st.st_rdev, os.path.basename(os.ttyname(slave)))
            x.proc = await asyncio.create_subprocess_exec(sys.executable, "-I", "-S", "-c", SHIM, *argv, stdin=slave,
                                                          stdout=slave, stderr=slave, cwd=cwd, env=env,
                                                          start_new_session=True)
        except Exception:
            os.close(master)
            raise
        finally:
            os.close(slave)
        LIVE[x.tty[0]] = x.tty[1]
        x.io = PtyIO(master)

    # ------------------------------------------------------------ output
    async def _pty_pump(self, x) -> None:
        dec, buf, why = codecs.getincrementaldecoder("utf-8")("replace"), "", "owner"
        try:
            while not x.closing:
                raw = await x.io.read(16384, IDLE_FLUSH)
                if raw is None:                               # idle: P408's short-run rule; the shell's own editor in front
                    await self._pty_out(x, x.vmask.flush(force=x.at_prompt and x.hold is None), False)
                    continue
                if not raw:
                    break
                buf = await self._pty_text(x, buf + dec.decode(raw))
                h = x.vmask.held()
                if h and x.typed and h in x.typed:            # the echo of what the owner typed: shown at once
                    await self._pty_out(x, x.vmask.flush(force=True), False)
        except Exception as ex:  # noqa: BLE001
            from . import bg
            bg.swallowed(self.st.log, "term_pump_failed", ex)
            why = "host"
        if not x.closing:
            await self.close(why)

    async def _pty_text(self, x, text: str) -> str:
        """Take Agent J's own OSC 133 markers out of the stream (prompt A, ready R, line report L); → a cut marker to hold."""
        marks = (x.prompt, x.ready, x.lpre)
        while text:
            hits = [(text.find(m), m) for m in marks]
            hits = [h for h in hits if h[0] >= 0]
            if not hits:
                j = text.rfind("\x1b", max(0, len(text) - 64))
                if j >= 0 and any(m.startswith(text[j:]) for m in marks):
                    await self._pty_out(x, x.vmask.feed(text[:j]), False)
                    return text[j:]
                await self._pty_out(x, x.vmask.feed(text), False)
                return ""
            i, m = min(hits)
            await self._pty_out(x, x.vmask.feed(text[:i]), m is x.prompt)
            rest = text[i + len(m):]
            if m is x.lpre:
                k = rest.find(";")
                if k < 0 or not rest[:k].isdigit():
                    if len(rest) < 12 and (k < 0 and rest.isdigit() or not rest):
                        return text[i:]                       # cut inside the length
                    text = rest                               # malformed: dropped, the decision fails closed
                    continue
                n = int(rest[:k])
                if len(rest) < k + 1 + n + 1:
                    if len(rest) > 64 * 1024:
                        text = ""                             # an endless report: dropped (fails closed)
                        continue
                    return text[i:]
                line, end = rest[k + 1:k + 1 + n], rest[k + 1 + n]
                text = rest[k + 2 + n:]
                if end == "\x07":
                    self._pty_report(x, line)
                continue
            text = rest
            if m is x.prompt:
                self._pty_prompt(x)
            elif x.rc_dir is not None:                        # ready: the start-up files were read
                shutil.rmtree(x.rc_dir, ignore_errors=True)
                x.rc_dir = None
        return ""

    async def _pty_out(self, x, data: str, prompt: bool) -> None:
        if not data and not prompt:
            return
        now = self.clock()
        if data:
            x.last = now                                      # PTY idle = no input AND no output
        if now - x.win >= 1.0:
            x.win, x.win_bytes = now, 0
        size = len(data.encode("utf-8", "replace"))
        if x.win_bytes + size > IN_RATE and x.win_bytes:     # back-pressure: stop reading the master until the next second
            await asyncio.sleep(max(0.0, 1.0 - (self.clock() - x.win)))
            x.win, x.win_bytes = self.clock(), 0
        x.win_bytes += size
        text = plain(data)
        x.recent = (x.recent + text)[-2048:]                  # N4: sudo's own "Sorry, try again" (masked text)
        text = plain(main_screen(x, data))                    # w2-sec M-4: no alternate-screen redraws to the Agent
        self.tr_add(x, text)
        self.bang_out(x, text, prompt)
        await self._cast(x, "o", data)
        from .terminal import FRAME_CHARS
        for k in range(0, max(1, len(data)), FRAME_CHARS):
            await self._frame(x, data[k:k + FRAME_CHARS], prompt and k + FRAME_CHARS >= len(data))

    # ------------------------------------------------------------ prompt / report
    def _pty_prompt(self, x) -> None:
        x.at_prompt, x.running, x.edit, x.edit_ok, x.paste = True, False, "", True, False
        if x.hold == "after":
            x.hold = None
        early, x.early = x.early, None
        if early is not None:                                 # a line sent before the first prompt (a 「!」 right after open)
            self.h._bg(self.on_line(x, *early), "term_line")
        elif x.pending and x.hold is None:
            pend, x.pending = x.pending, ""
            self._pty_feed(x, pend)

    def _pty_report(self, x, line: str) -> None:
        f = x.report
        if f is not None and not f.done():
            f.set_result(line)

    # ------------------------------------------------------------ input
    async def on_term_in(self, x, s, obj: dict) -> None:
        d = obj.get("d")
        try:
            b = d.encode("utf-8") if isinstance(d, str) else None
        except UnicodeEncodeError:
            b = None
        if x.kind != "pty" or not b or len(b) > IN_FRAME:
            self.refused(s.device, "term_in", "shape")
            return
        now = self.clock()
        if now - x.in_win >= 1.0:
            x.in_win, x.in_bytes, x.in_note = now, 0, False
        if x.in_bytes + len(b) > IN_RATE:
            self.refused(s.device, "term_in", "rate")
            if not x.in_note:
                x.in_note = True
                await self._frame(x, "\r\n[输入太快，这一段没有送出 / input too fast: not sent]\r\n", False)
            return
        x.in_bytes += len(b)
        if "reply" in obj:
            # A report is not owner typing: never cancel a confirmation, consume an Enter sequence,
            # enter the echo ring or wait in typeahead for a later shell prompt.
            if (obj["reply"] is not True or not terminal_reply(d) or x.closing or
                    x.disconnected_at is not None or x.at_prompt or x.hold not in (None, "after") or
                    not self.program_front(x)):
                self.refused(s.device, "term_in", "reply")
                return
            self._pty_write(x, d)
            return
        x.last = now
        if x.io is not None and not password_mode(x.io.fd):  # echo recognition only; an echo-off answer is never kept
            x.typed = (x.typed + plain(d).replace("\n", ""))[-TYPED_KEEP:]
        x.enter_seq = obj.get("seq") if type(obj.get("seq")) is int else 0
        self.owner_acted()
        if x.hold == "confirm":                               # typing over an open confirm = 「取消」 (the line is erased)
            self._pty_cancel(x, "input")
        if x.hold == "after" and any(k in d for k in SIGNAL_KEYS):   # w2-sec M-2: Ctrl+C / Ctrl+\ / Ctrl+Z are never kept
            x.pending = ""                                    # (the queued typeahead goes with the interrupt)
            self._pty_write(x, "".join(c for c in d if c in SIGNAL_KEYS))
            return
        if x.hold is not None:                                # waiting for the line report / the next prompt: keep order
            if len(x.pending) + len(d) > OUT_BUF:
                self.refused(s.device, "term_in", "full")
                return
            x.pending += d
            return
        self._pty_feed(x, d)

    def _pty_write(self, x, text: str) -> bool:
        if x.disconnected_at is not None:
            return False
        ok = x.io is not None and x.io.write(text.encode("utf-8"))
        if not ok and x.io is not None and not x.io.closed:
            self.refused(x.device, "term_in", "full")
        return ok

    def refused(self, device, action: str, reason: str) -> None:
        """w2-sec L-4: `control_refused` for term_in / term_resize at most once per REFUSED_EVERY s per (device, action,
        reason); the next line says how many were not logged."""
        seen = self.__dict__.setdefault("_refused_at", {})
        key, now = (device, action, reason), self.clock()
        at, n = seen.get(key, (None, 0))
        if at is not None and now - at < REFUSED_EVERY:
            seen[key] = (at, n + 1)
            return
        seen[key] = (now, 0)
        self.st.log("control_refused", device=device, action=action, reason=reason, **({"more": n} if n else {}))

    def shell_front(self, x, *, strict=False) -> bool:
        """The shell itself owns the terminal's foreground (its prompt, a builtin, between two commands) — not a program it
        started (job control: every job is its own process group). Unknown → True (the stricter side: keep / classify)."""
        try:
            if os.tcgetpgrp(x.io.fd) != x.proc.pid:
                return False
        except (OSError, AttributeError, TypeError):
            return not strict
        name = proc_name(x.proc.pid)                       # `exec prog` keeps the pid: the front is the shell only by name
        return (name is None and not strict) or (name is not None and os.path.basename(name).lstrip("-") in ("bash", "zsh", x.shell))

    def program_front(self, x) -> bool:
        """Positive foreground-program evidence; unlike shell_front, unknown never authorizes a reply."""
        try:
            if os.tcgetpgrp(x.io.fd) != x.proc.pid:
                return True
        except (OSError, AttributeError, TypeError):
            return False
        name = proc_name(x.proc.pid)
        return name is not None and os.path.basename(name).lstrip("-") not in ("bash", "zsh", x.shell)

    def _pty_feed(self, x, d: str) -> None:
        if x.io is None or x.closing:
            return
        front = self.shell_front(x)
        if not x.at_prompt and front and not password_mode(x.io.fd):
            # w2-sec M-2: the shell itself is busy (a builtin, between commands, before its first prompt): what is typed
            # now would reach its line editor unchecked — keep it for the prompt (checked there) or for a program
            # that takes the foreground (`_pty_after` polls)
            x.pending += d
            if x.hold is None:
                x.hold = "after"
            self._after(x)
            return
        if not x.at_prompt or not front or password_mode(x.io.fd):   # a program in front: its input, never classified or kept
            self._pty_write(x, d)
            return
        i, start = 0, 0
        while i < len(d):
            if d.startswith(PASTE_ON, i):
                x.paste, i = True, i + len(PASTE_ON)
                continue
            if d.startswith(PASTE_OFF, i):
                x.paste, i = False, i + len(PASTE_OFF)
                continue
            c = d[i]
            if not x.paste and (c in "\r\n" or c == "\x1b" or ord(c) < 32):
                if c in "\r\n":
                    key = "\r\n" if c == "\r" and d[i + 1:i + 2] == "\n" else c
                else:
                    key = _KEY.match(d, i).group() if c in "\x1b\x18" else c
                    if key in KEY_OK:
                        i += len(key)
                        continue
                # w2-sec H-1: Enter and every key that is not plain editing — Ctrl+O, Ctrl+X Ctrl+E, Esc a, vi `v` after
                # ESC … — waits until the exact line is classified; held keys go to the shell only after that
                seg = d[start:i]
                self._pty_write(x, seg)
                edit_apply(x, seg)
                x.pending = d[i + len(key):]
                self._pty_enter(x, "\r" if key in ("\r", "\n", "\r\n") else key)
                return
            i += 1
        self._pty_write(x, d[start:])
        edit_apply(x, d[start:])

    def _pty_enter(self, x, key: str = "\r") -> None:
        """Hold the Enter (or another key that may run the line): ask the shell for the exact line, then decide
        (`_pty_decide`, in the background)."""
        x.hold, x.hold_key = "query", key
        x.report = asyncio.get_running_loop().create_future()
        self._pty_write(x, QUERY_KEY)
        self.h._bg(self._pty_decide(x, x.report), "term_enter")

    async def _pty_decide(self, x, fut) -> None:
        try:
            line = await asyncio.wait_for(fut, QUERY_WAIT)
        except asyncio.TimeoutError:
            line = None
        if x.closing or self.term is not x or x.report is not fut:
            return
        x.report = None
        sure = line is not None or x.edit_ok
        line = x.edit if line is None else line
        from .term_bang import line_problem
        from .term_shell import classify
        agent = sure and bool(line.strip()) and self.go_match(line)
        if not sure:
            bad = ("看不出这一行的确切内容（用了方向键、补全或历史）", "cannot tell the exact line (arrow keys, completion or history)")
        elif not line.strip():
            bad = None
        elif line_problem(line):
            bad = ("这一行含有看不见的字符或一长串空格", "the line has invisible characters or a long run of spaces")
        elif agent:
            bad = ("Agent 填写的命令", "filled in by the Agent")
        elif hist_rerun(line):
            bad = ("这一行会重新运行历史里的命令（fc / r / ^）", "re-runs a command from history (fc / r / ^)")
        else:
            bad = classify(line)
        if bad is None:
            return self._pty_release(x, line)
        await self._pty_confirm(x, line, bad, agent)

    async def _pty_confirm(self, x, line: str, bad, agent: bool) -> None:
        from . import passkey, wire
        from .term_shell import _sha
        from .terminal import CONFIRM_TTL, line_needs_face
        n = secrets.token_hex(16)
        uv = line_needs_face(line)
        x.confirms = {k: v for k, v in x.confirms.items() if v["exp"] > self.clock()}
        x.confirms[n] = {"line": line, "exp": self.clock() + CONFIRM_TTL, "uv": uv, "pty": "enter"}
        x.hold, x.hold_n = "confirm", n
        ch = passkey.elevate_challenge(self.h.channel, x.device, x.id, "term_line", n, _sha(line))
        self._log("term_line", x.device, "confirm:agent" if agent else "confirm", {"id": x.id, "line": line})
        with contextlib.suppress(Exception):
            await self.h.send_app(x.s, {"t": "term_confirm", "id": x.id, "seq": x.enter_seq, "line": line, "why": bad[0],
                                        "why_en": bad[1], "n": n, "fa_ch": wire.b64u(ch), "uv": 1 if uv else 0,
                                        **({"agent": 1} if agent else {})})

    def _pty_release(self, x, line: str) -> None:
        """Send the held Enter. What was typed after it waits for the next prompt (then it is checked like any line) or
        for a program in the foreground (then it is that program's input — `_pty_after`)."""
        x.confirms.pop(x.hold_n, None) if x.hold_n else None
        key, x.hold_key = x.hold_key or "\r", "\r"
        if key != "\r":                                      # w2-sec H-1: another key, now checked: the shell gets it
            x.hold_n, x.hold, x.edit_ok = None, None, False   # (still at the prompt; its effect on the line is unknown)
            self._pty_write(x, key)
            pend, x.pending = x.pending, ""
            if pend:
                self._pty_feed(x, pend)
            return
        x.hold_n, x.hold = None, "after"
        x.at_prompt, x.running, x.line, x.edit, x.edit_ok = False, True, line, "", True
        self._pty_write(x, "\r")
        self._after(x)

    def _after(self, x) -> None:
        t = x.after_task
        if t is None or t.done():
            x.after_task = self.h._bg(self._pty_after(x), "term_after")

    async def _pty_after(self, x) -> None:
        """w2-sec M-2: typeahead behind a released Enter goes to the PTY only once a program (not the shell) owns the
        foreground; while the shell itself is in front it waits for the prompt, where `_pty_prompt` checks it."""
        seen = None
        while True:
            await asyncio.sleep(AFTER_ENTER)
            if x.closing or self.term is not x or x.hold != "after":
                return
            if password_mode(x.io.fd):                        # a program reads a password: its input now
                break
            if self.shell_front(x):
                seen = None
                continue
            try:
                g = os.tcgetpgrp(x.io.fd)
            except OSError:
                break
            if g != seen:                                     # the same program in front on two polls (≥ AFTER_ENTER s):
                seen = g                                      # a short one (the prompt's own `sudo -k`, a quick command)
                continue                                      # never gets the typeahead — the prompt checks it
            break
        x.hold = None
        if x.pending:
            pend, x.pending = x.pending, ""
            self._pty_feed(x, pend)

    def _pty_cancel(self, x, why: str) -> None:
        """Refused / expired / typed over: erase the held line; what was queued behind it goes too."""
        if x.hold_n:
            x.confirms.pop(x.hold_n, None)
            self._log("term_line", x.device, "refused:" + why, {"id": x.id})
        x.hold = x.hold_n = None
        x.hold_key = "\r"
        x.pending, x.edit, x.edit_ok = "", "", True
        self._pty_write(x, KILL_LINE)

    def pty_tick(self, x) -> None:
        if x.hold == "confirm" and x.hold_n and (x.hold_n not in x.confirms or x.confirms[x.hold_n]["exp"] <= self.clock()):
            self._pty_cancel(x, "expired")

    async def pty_line(self, x, line: str, bang) -> None:
        """A whole line (「!」, the page's own line, or a confirmed one) at an EMPTY prompt: type it, Enter.
        w2-sec L-3: Ctrl+E Ctrl+U does not clear the line in vi command mode (or under another binding), so a line is
        only typed when the host's copy says nothing was typed since the prompt (and no key made the copy uncertain)."""
        if x.edit != "" or not x.edit_ok or not self.shell_front(x):
            self.refused(x.device, "term_line", "edited")
            if bang is not None:
                return await self.bang_refuse(x, bang, "edited")
            return await self._frame(x, "\r\n[输入行里还有内容，先清空再发 / the input line is not empty: clear it first]\r\n", False)
        if bang is not None:
            self.bang_start(x, bang, line)
        x.at_prompt, x.running, x.line, x.edit, x.edit_ok = False, True, line, "", True
        self._pty_write(x, line + "\r")

    async def on_resize(self, x, s, obj: dict) -> None:
        cols, rows = obj.get("cols"), obj.get("rows")
        now = self.clock()
        if x.kind != "pty" or not size_ok(cols, rows) or now - x.resized < 0.05:
            self.refused(s.device, "term_resize", "shape")
            return
        x.resized, x.cols, x.rows = now, cols, rows
        with contextlib.suppress(OSError):
            set_size(x.io.fd, cols, rows)                     # the kernel sends SIGWINCH to the foreground group
            if sys.platform == "darwin":
                os.killpg(os.tcgetpgrp(x.io.fd), signal.SIGWINCH)
        await self._cast(x, "r", f"{cols}x{rows}")

    def pty_closed(self, x) -> None:
        x.typed = x.edit = x.pending = ""
        cg_drop(getattr(x, "cg", None))                       # w2-sec N-3: empty after the sweep (else the next open removes it)
        if getattr(x, "io", None) is not None:
            x.io.close()
        if getattr(x, "tty", None):
            LIVE.pop(x.tty[0], None)
        if getattr(x, "rc_dir", None) is not None:
            shutil.rmtree(x.rc_dir, ignore_errors=True)
            x.rc_dir = None


def proc_name(pid: int) -> str | None:
    """The process's command name (Linux /proc/<pid>/comm, macOS kinfo_proc p_comm); None = unknown."""
    try:
        with open(f"/proc/{int(pid)}/comm", "rb") as f:
            return f.read().decode("utf-8", "replace").strip()
    except OSError:
        pass
    if sys.platform == "darwin":
        with contextlib.suppress(Exception):
            from .term_mac import proc_info
            info = proc_info(pid)
            return str(info["name"]) if info else None
    return None


# ---------------------------------------------------------------- w2-sec N-3: the web terminal's own cgroup (Linux, cgroup v2)
def _cg_path(pid) -> str | None:
    """The cgroup v2 path of `pid` ("0::/…" line), None = unknown."""
    try:
        with open(f"/proc/{pid}/cgroup", "rb") as f:
            for ln in f.read().decode("utf-8", "replace").splitlines():
                if ln.startswith("0::"):
                    return ln[3:].strip()
    except (OSError, ValueError):
        pass
    return None


def cg_make(tag: str, fs: str = CG_FS) -> str | None:
    """A child cgroup of serve's own (only where serve may write there: a systemd user service or any delegated cgroup v2
    subtree; never root's) → its cgroup.procs path; None = not available here (then only the environment token tags the
    session's processes). Empty leftovers of earlier sessions are removed first."""
    if not sys.platform.startswith("linux") or not os.path.exists(os.path.join(fs, "cgroup.controllers")):
        return None
    me = _cg_path("self")
    if not me or any(c.startswith(CG_PREFIX) for c in me.split("/")):
        return None
    root = os.path.join(fs, me.lstrip("/"))
    if not (os.access(root, os.W_OK) and os.access(os.path.join(root, "cgroup.procs"), os.W_OK)):
        return None
    now = time.time()
    with contextlib.suppress(OSError):
        for n in os.listdir(root):
            p = os.path.join(root, n)
            if not n.startswith(CG_PREFIX) or os.path.join(p, "cgroup.procs") in LIVE_CG:
                continue
            with contextlib.suppress(OSError):
                if now - os.stat(p).st_mtime >= CG_STALE:     # R3-3: never a concurrent session's fresh, still empty one
                    os.rmdir(p)                              # only an empty one goes
    d = os.path.join(root, CG_PREFIX + tag)
    try:
        os.mkdir(d, 0o755)
    except OSError:
        return None
    procs = os.path.join(d, "cgroup.procs")
    if not os.access(procs, os.W_OK):
        with contextlib.suppress(OSError):
            os.rmdir(d)
        return None
    LIVE_CG.add(procs)
    return procs


def cg_pids(procs: str | None) -> list[int]:
    if not procs:
        return []
    try:
        with open(procs) as f:
            return [int(v) for v in f.read().split() if v.isdigit()]
    except OSError:
        return []


def cg_kill(procs: str | None) -> bool:
    """w2-sec R3-2: end every process in the session cgroup. cgroup.kill (Linux ≥ 5.14) kills the whole cgroup atomically
    (nothing forks past it) → True; without it the pid list is read and each one SIGKILLed → False."""
    if not procs:
        return False
    kill = os.path.join(os.path.dirname(procs), "cgroup.kill")
    if os.path.exists(kill):
        try:
            with open(kill, "w") as f:
                f.write("1")
            return True
        except OSError:
            pass
    for p in cg_pids(procs):
        if p != os.getpid():
            with contextlib.suppress(OSError):
                os.kill(p, signal.SIGKILL)
    return False


def cg_drop(procs: str | None) -> None:
    if procs:
        LIVE_CG.discard(procs)
        with contextlib.suppress(OSError):
            os.rmdir(os.path.dirname(procs))


def system_first(path: str | None) -> str:
    """w2-sec R3-4: PATH with the existing root-owned, not group/other-writable system directories first, then the rest
    of `path` in its order (duplicates dropped)."""
    want = SYS_PATH_MAC if sys.platform == "darwin" else SYS_PATH
    head = []
    for d in want:
        with contextlib.suppress(OSError):
            st = os.stat(d)
            real = os.path.realpath(d)
            if st.st_uid == 0 and not st.st_mode & 0o022 and os.path.isdir(d) and \
                    all(os.path.realpath(h) != real for h in head):
                head.append(d)
    rest = [p for p in (path or "").split(os.pathsep) if p and p not in head]
    return os.pathsep.join(head + list(dict.fromkeys(rest)))


def in_web_cgroup(pid) -> bool:
    """`pid` lives in a web-terminal cgroup (this or an earlier session's, of any instance)."""
    path = _cg_path(pid)
    return bool(path) and any(c.startswith(CG_PREFIX) for c in path.split("/"))


def live_tty(rdev: int | None = None, name: str | None = None) -> bool:
    """ctl_auth: this terminal device is a web terminal's PTY (never "a terminal at the computer")."""
    return (rdev is not None and rdev in LIVE) or (bool(name) and name.rsplit("/", 1)[-1] in LIVE.values())
