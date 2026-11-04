"""#455 same_host peer proof: kernel facts tied to an owner-signed launch, else external (ARCHITECTURE ADR-#455)."""
from __future__ import annotations

import json
import os
import re
import shlex
import stat
import sys
import threading
import time
from pathlib import Path

WINDOW = 120.0
MARGIN = 1.0
KEEP = 64
TTL = 48 * 3600
MEMBERS = 512
UNIT = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_.@-]{0,120}")
HZ = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
HARNESS = {"claude": ("-p", "--print"), "codex": ("exec",), "opencode": ("run",)}
OPS = frozenset({">", ">>", "&>", "<", ">&"})
CONTROL = frozenset({"CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", "GEMINI.md"})
SECRET_NAMES = frozenset({"approvals.log", "devices.json"})
NOTES = ".jarvis"   # the one writable dot dir: the owner's CEO notes / reports (exact public-name rule, like busy.py)
BUSY_OPTS = frozenset({"--who", "--label", "--kind", "--report", "--harness", "--model", "--workflow", "--role", "--parent"})


_LSTAT = os.lstat   # seam: tests simulate root-owned system dirs
_UID = os.getuid
_PINS: dict = {}    # root-owned real path → (sha256, uid, mode, stat key): first sight in this serve
_REG: dict = {"boot": None, "trees": (), "harness": (), "files": {}}   # F3 B: the host registry (register(), outside the fence)
_KEY = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_uid", "st_mode")


def register(environ=None) -> dict:
    """F3 B trust anchor, outside the fence (ADR-#455 F3 B): read-only trees + harness files by stat identity."""
    from . import binaries, fence
    named = binaries.trees(environ) if sys.platform.startswith("linux") else []
    now = set(fence.ro_trees(environ=environ))
    boot = _REG["boot"]
    trees = tuple(sorted(now if boot is None else set(boot) & now))
    files = {}
    for name, t in named:
        if t not in trees:
            continue
        for d, subs, names in os.walk(t):
            if d.count("/") - t.count("/") >= 4 or len(files) > 4096:
                subs[:] = []
            for x in names:
                f = os.path.join(d, x)
                try:
                    st = os.lstat(f)
                except OSError:
                    continue
                if stat.S_ISREG(st.st_mode) and st.st_mode & 0o111:
                    files[f] = (name, tuple(getattr(st, k) for k in _KEY))
    _REG.update(boot=trees if boot is None else boot, trees=trees, files=files,
                harness=tuple(t for _, t in named if t in trees))
    return {"trees": list(trees), "files": len(files)}


def _mine(s, sticky=False) -> bool:
    return s.st_uid in (0, _UID()) and (stat.S_ISLNK(s.st_mode) or not s.st_mode & 0o022
                                        or sticky and stat.S_ISDIR(s.st_mode) and s.st_mode & stat.S_ISVTX)


def _ok(path, s, rooted):   # → (closed in every fence, root chain)
    if rooted and s.st_uid == 0 and (stat.S_ISLNK(s.st_mode) or not s.st_mode & 0o022):
        return True, True
    t = _REG["trees"]
    if any(_under(path, x) for x in t):                      # inside a read-only bind
        return _mine(s), False
    if stat.S_ISDIR(s.st_mode) and any(_under(x, path) for x in t):   # pinned way to one
        return _mine(s, True), False
    return False, False


def _sealed(p, n=0, want="file") -> bool:
    """F3 / B: every component and hop a root chain or inside / on the pinned way to a read-only tree."""
    if n > 4 or not isinstance(p, str) or not p.startswith("/"):
        return False
    head, todo, hops = "/", p.split("/")[::-1], 0
    try:
        s = _LSTAT("/")
        ok, rooted = _ok("/", s, True)
        if not ok:
            return False
        while todo:
            x = todo.pop()
            if x in ("", "."):
                continue
            if x == "..":
                head = os.path.dirname(head)
                continue
            nxt = os.path.join(head, x)
            s = _LSTAT(nxt)
            ok, rooted = _ok(nxt, s, rooted)
            if not ok:
                return False
            if stat.S_ISLNK(s.st_mode):
                hops, t = hops + 1, os.readlink(nxt)
                if hops > 16:
                    return False
                if t.startswith("/"):
                    head, rooted = "/", True
                todo.extend(t.split("/")[::-1])
            else:
                head = nxt
    except OSError:
        return False
    if want == "dir":
        return stat.S_ISDIR(s.st_mode)
    if not stat.S_ISREG(s.st_mode):
        return False
    try:
        with open(head, "rb") as f:
            line = f.read(256).split(b"\n")[0]
    except OSError:
        return False
    if not line.startswith(b"#!"):
        return True
    it = line[2:].split()   # a script: sealed interpreter, never `env`
    return bool(it) and os.path.basename(it[0]) != b"env" and _sealed(os.fsdecode(it[0]), n + 1)


def _pin(p, seal=True) -> bool:   # sealed + the registry record (B) or the first-sight sha256 (root-owned)
    import hashlib
    real = os.path.realpath(p)
    if seal and not _sealed(p):
        return False
    try:
        s = _LSTAT(real)
        key = tuple(getattr(s, k) for k in _KEY)
        reg = _REG["files"].get(real)
        if reg is not None:
            return reg[1] == key
        if s.st_uid != 0 and any(_under(real, x) for x in _REG["harness"]):
            return False
        rec = _PINS.get(real)
        if rec and rec[3] == key:
            return True
        if s.st_size > 1 << 30 or not stat.S_ISREG(s.st_mode):
            return False
        h = hashlib.sha256()
        with open(real, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                h.update(b)
    except OSError:
        return False
    if rec and rec[:3] != (h.hexdigest(), s.st_uid, s.st_mode):
        return False
    _PINS[real] = (h.hexdigest(), s.st_uid, s.st_mode, key)
    return True


AGENTJ = None       # seam: the host's own `agentj` entry (default: next to serve's interpreter)
_BAD_ENV = ("BASH_ENV", "ENV", "LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP",
            "PYTHONUSERBASE", "PYTHONINSPECT")


def _look(word, path, strict):
    """F3 B: execvp's PATH walk → (hit, None) | (None, why); strict: earlier dirs sealed, else absent now."""
    if not isinstance(path, str) or not path:
        return None, "path_unknown"
    for d in path.split(os.pathsep):
        if not d.startswith("/"):
            return None, "path_relative"
        f = os.path.join(d, word)
        try:
            s = os.stat(f)
        except (FileNotFoundError, NotADirectoryError):
            s = None
        except OSError:
            return None, "path_unreadable"
        if s is not None:
            if stat.S_ISREG(s.st_mode) and os.access(f, os.X_OK):
                return f, None
            return None, "path_shadow"
        if strict and os.path.isdir(d) and not _sealed(d, want="dir"):
            return None, "path_writable"
    return None, "not_found"


def _prove(tok, name, side, ctx):   # → None (proven) | reason
    if os.path.isabs(tok):
        hit, weak = tok, False
    else:
        if side != "caller":
            env = ctx.get("unit")
            if callable(env):
                env = ctx["unit"] = env()
            if not isinstance(env, dict) or side == "pin" and any(env.get(k, "") is None for k in
                                                                  ("HOME", "MISE_DATA_DIR", "ASDF_DATA_DIR")):
                return "unit_env_unknown"   # pin: the tree busy run will find must be the one the host reads
            if any(k in env for k in _BAD_ENV[2 if side == "pin" else 0:]):   # pin: busy run drops BASH_ENV / ENV itself
                return "unit_env"
            path, strict = env.get("PATH"), False
            if side == "pin":   # what busy run's pinned child env finds, every dir before the hit sealed
                from . import binaries
                path, strict = binaries.pinned(ctx.get("pin"), env)["PATH"], True
        else:
            path, strict = ctx.get("caller"), bool(ctx.get("strict"))
        hit, why = _look(tok, path, strict)
        if hit is None:
            return why
        weak = not strict
    target = os.path.realpath(hit) if weak else hit
    if name == "agentj":
        e = AGENTJ or os.path.join(os.path.dirname(sys.executable), "agentj")
        return None if os.path.realpath(hit) == os.path.realpath(e) and _pin(target) else "not_host_entry"
    if name in HARNESS:
        return None if _harness(target, name) else "not_registered"
    return None if _pin(target) else "not_sealed"


def _harness(tok, name):   # a registered file in a read-only tree (B), or root-owned = binaries.resolve
    from . import binaries
    if name not in HARNESS or not _sealed(tok):
        return None
    real = os.path.realpath(tok)
    reg = _REG["files"].get(real)
    if reg is not None:
        return name if reg[0] == name and _pin(tok) else None
    path = binaries.resolve(name).get("path")
    return name if path and real == os.path.realpath(path) and _pin(tok) else None


_PROC = Path("/proc")   # seam: tests read a fake /proc


def session_path(session, proc=None):
    """F3 B: PATH of the verified served pid, else None."""
    proc = _PROC if proc is None else proc
    pid, start = (session or {}).get("pid"), (session or {}).get("procStart")
    if type(pid) is not int or pid <= 1 or not isinstance(start, str) or (_stat(proc, pid) or (0, 0, None))[2] != start:
        return None
    try:
        raw = (proc / str(pid) / "environ").read_bytes()[: 1 << 20]
    except OSError:
        return None
    for kv in raw.split(b"\0"):
        if kv.startswith(b"PATH="):
            return os.fsdecode(kv[5:])
    return None


_MANAGER: list = [0.0, None]


def manager_env(run=None, ttl=30.0):
    """F3 B: what an `agentj-*` unit inherits, read by the host."""
    import subprocess
    if not sys.platform.startswith("linux"):
        return None
    if _MANAGER[1] is not None and time.monotonic() - _MANAGER[0] < ttl:
        return _MANAGER[1]
    try:
        r = (run or subprocess.run)(["systemctl", "--user", "show-environment"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    env = {}
    for line in r.stdout.splitlines():
        k, eq, v = line.partition("=")
        if eq and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k):
            env[k] = None if v.startswith(("$'", "'", '"')) else v
    if env.get("PATH") is None:
        env.pop("PATH", None)
    _MANAGER[:] = [time.monotonic(), env]
    return env


def source(unit: str) -> dict:
    from .provenance import SameHost
    return SameHost(kind="same_host", role="peer", unit=unit)


def _globs(s) -> bool:   # round 3: an unquoted * ? [ { (bash would expand it into words nobody judged)
    q, esc = None, False
    for c in s:
        if esc:
            esc = False
        elif c == "\\" and q != "'":
            esc = True
        elif q:
            q = None if c == q else q
        elif c in "'\"":
            q = c
        elif c in "*?[{":
            return True
    return False


def _plain(s, ops=OPS) -> bool:   # no expansion, no shell operator outside quotes except the redirections in `ops`
    if not isinstance(s, str) or not s.strip() or len(s) > 16384 or any(c in s for c in "$`\\\n\r") or _globs(s):
        return False
    try:
        lx = shlex.shlex(s, posix=True, punctuation_chars=True)
        lx.whitespace_split, lx.commenters = True, ""
        t = list(lx)
    except ValueError:
        return False
    return all(not (set(w) <= set("();<>|&")) or w in ops and (w != ">&" or t[i + 1:i + 2] and t[i + 1].isdigit())
               for i, w in enumerate(t)) and not any(w.startswith("#") for w in t)


def _under(p, d) -> bool:
    return bool(d) and (p == d or p.startswith(d.rstrip("/") + "/"))


def _abs(p, base):
    if not isinstance(p, str) or not p or p.startswith("~") and not (p == "~" or p.startswith("~/")):
        return None
    q = os.path.expanduser(p) if p.startswith("~") else p if os.path.isabs(p) else os.path.join(base, p) if base else None
    return os.path.normpath(q) if q else None


def _real(p, base):
    q = _abs(p, base)
    return os.path.realpath(q) if q else None


def _sys(x) -> bool:
    return bool(x) and x != "/dev/null" and any(_under(x, d) for d in ("/proc", "/sys", "/dev"))


def _magic(q, n=0) -> bool:   # round 3: a symlink on the way into /proc /sys /dev (/dev/stdin, /dev/fd/N: serve's fds, not the harness's)
    head, parts = "/", [x for x in (q or "").split("/") if x]
    for i, x in enumerate(parts):
        nxt = os.path.join(head, x)
        try:
            t = os.path.normpath(os.path.join(head, os.readlink(nxt)))
        except OSError:
            head = nxt
            continue
        return n > 16 or _sys(t) or _magic(os.path.join(t, *parts[i + 1:]), n + 1)
    return False


def _sysdev(p, base) -> bool:   # by the text, the real path and every symlink hop
    return _sys(_abs(p, base)) or _sys(_real(p, base)) or _magic(_abs(p, base))


def _reads(p, base, state) -> bool:      # read-only: anywhere but the agentj state dir and credential stores
    from . import danger
    real = _real(p, base)
    return bool(real) and not any(c in p for c in "*?[") and not danger.cred_path(p) and not danger.cred_path(real) \
        and not _under(real, state) and os.path.basename(real) not in SECRET_NAMES and not _sysdev(p, base)


def _writes(p, base, root, state) -> bool:   # inside the served workspace, no dot dir (but NOTES), no Agent control file
    from . import danger
    if p == "/dev/null":
        return True
    real, top = _real(p, base), os.path.realpath(root) if root else None
    if not real or not top or real == top or not _under(real, top) or _under(real, state):
        return False
    parts = os.path.relpath(real, top).split(os.sep)
    return not (any(x.startswith(".") and x != NOTES for x in parts) or parts[-1] in CONTROL or danger.cred_path(real)
                or danger.hook_path(real) or danger.service_path(real))


def _one(s):
    from . import danger
    c = danger.lex(s) if _plain(s) else []
    return c[0] if len(c) == 1 and not (c[0].subst or c[0].heredoc or c[0].piped) else None


class _Proof:
    def __init__(self, ctx):
        self.ctx, self.why = ctx if isinstance(ctx, dict) else {}, []

    def word(self, tok, name, side) -> bool:   # shape: `name` or …/name; the proof only noted
        if not isinstance(tok, str) or tok != name and not (os.path.isabs(tok) and os.path.basename(tok) == name):
            return False
        why = _prove(tok, name, side, self.ctx)
        if why:
            self.why.append(f"{name}:{why}")
        return True

    def harness(self, tok, side):
        name = os.path.basename(tok) if isinstance(tok, str) else ""
        return name if name in HARNESS and self.word(tok, name, side) else None


def pin_name(argv):
    """ADR-#455 F3 B pin: `<harness> <mode> …` | `bash -c '<harness> <mode> …'` → the harness busy run pins, else None."""
    if len(argv) == 3 and os.path.basename(argv[0]) == "bash" and argv[1] == "-c":
        c = _one(argv[2])
        argv = c.words if c else []
    n = os.path.basename(argv[0]) if argv else ""
    return n if argv[1:2] and argv[1] in HARNESS.get(n, ()) else None


def check(command, root, state=None, ctx=None):
    """ADR-#455 dispatch shape → (unit, "ok" | "<word>:<why>") | (None, "shape"); ctx: caller PATH, strict, unit env."""
    c, top = _one(command), os.path.realpath(root) if isinstance(root, str) and root else None
    state = os.path.realpath(state) if state else None
    pf = _Proof(ctx)
    if c is None or not top or c.ins or not all(_writes(t, None, top, state) for t in c.redirs):
        return None, "shape"
    w, i, seen, unit, wd = c.words, 1, set(), None, None
    if not w or not pf.word(w[0], "systemd-run", "caller"):
        return None, "shape"
    while i < len(w) and w[i].startswith("-"):
        a, v = w[i], w[i + 1] if i + 1 < len(w) else ""
        k = a.split("=", 1)[0] if a.startswith("--unit=") or a.startswith("--property=") else a
        if k in seen and k not in ("--user", "--collect"):
            return None, "shape"
        seen.add(k)
        if a in ("--user", "--collect"):
            pass
        elif a.startswith("--unit="):
            unit = a[7:]
        elif a in ("--unit", "-u"):
            unit, i = v, i + 1
        elif a in ("-p", "--property") and v.startswith("WorkingDirectory="):
            wd, i = v[17:], i + 1
        elif a.startswith("--property=WorkingDirectory="):
            wd = a[28:]
        else:
            return None, "shape"
        i += 1
    if not {"--user", "--collect"} <= seen or not isinstance(unit, str) or not UNIT.fullmatch(unit) \
            or not unit.startswith("agentj-") or len(unit) < 8 or unit.endswith(".scope"):
        return None, "shape"
    if wd is not None and not (os.path.isabs(wd) and _under(os.path.realpath(wd), top)):
        return None, "shape"
    rest, side = w[i:], "caller"
    if rest[1:3] == ["busy", "run"] and pf.word(rest[0], "agentj", "caller"):   # N1: busy.py's own `run` options, then `--`
        j, opts = 3, set()
        while j + 1 < len(rest) and rest[j] in BUSY_OPTS and rest[j] not in opts and not rest[j + 1].startswith("-") \
                and (rest[j] != "--kind" or rest[j + 1] in ("ceo", "temp")) and (rest[j] != "--report" or (
                    _reads(rest[j + 1], wd, state) and _under(_real(rest[j + 1], wd) or "", top))):
            opts.add(rest[j])
            j += 2
        if rest[j:j + 1] != ["--"]:
            return None, "shape"
        rest, side = rest[j + 1:], "pin"
        pf.ctx["pin"] = pin_name(rest)
    if len(rest) == 3 and rest[1] == "-c" and pf.word(rest[0], "bash", side):
        inner = _one(rest[2])
        if inner is None or not all(_reads(t, wd, state) and _under(_real(t, wd) or "", top) if t in inner.ins else
                                    _writes(t, wd, top, state) for t in inner.redirs):
            return None, "shape"
        rest, side = inner.words, "pin" if side == "pin" else "unit"
    if len(rest) < 2 or rest[1] not in HARNESS.get(pf.harness(rest[0], side) or "", ()):   # F3: mode word right after
        return None, "shape"
    unit = unit if unit.endswith(".service") else unit + ".service"
    return unit, pf.why[0] if pf.why else "ok"


def dispatch(command, root, state=None, ctx=None):
    unit, why = check(command, root, state, ctx)
    return unit if why == "ok" else None


WHY = {"not_registered": ("不是主机登记的 {w}", "not the {w} this host registered"),
       "not_host_entry": ("不是本机的 agentj", "not this host's agentj"),
       "not_sealed": ("{w} 在隔离里可写", "{w} is writable in the fence"),
       "path_writable": ("PATH 前面的目录在隔离里可写", "an earlier PATH dir is fence-writable"),
       "path_shadow": ("PATH 前面另有一个 {w}", "an earlier PATH dir holds another {w}"),
       "unit_env": ("服务环境设了 BASH_ENV / LD_* / PYTHON*", "the unit env sets BASH_ENV / LD_* / PYTHON*")}


def unproven(why, lang="zh"):
    """#469 ②: an unproven dispatch names its class (opaque) and why; rule cred.service keeps every floor."""
    from . import danger
    w, _, r = (why or "").partition(":")
    zh, en = WHY.get(r, ("无法确认会运行哪个 {w}（" + (r or why or "?") + "）", "cannot confirm which {w} runs (" + (r or why or "?") + ")"))
    if why == "turn":
        zh, en = "不是主人或已核实的同机会话安排的", "not the owner's or a verified same-computer turn"
    zh = "派 CEO 会话（systemd-run）：" + zh.format(w=w or "程序")
    en = "Dispatches a CEO session (systemd-run): " + en.format(w=w or "program")
    return danger.Verdict(cats=["opaque"], rules=["opaque.dispatch", "cred.service"], why=zh[:200], why_en=en[:200],
                          items=[("opaque", "opaque.dispatch", zh[:200])])


def units(command, root=None, ctx=None) -> list:
    u = dispatch(command, root, ctx=ctx)
    return [u] if u else []


_RO = frozenset({"pwd", "whoami", "id", "uname", "true", "echo", "printf", "which", "nproc", "uptime", "df", "free",
                 "basename", "dirname", "tr"})   # no word here opens a file by an option (round 4: `date` moved to _DATE)
_DATE = re.compile(r"\+.{0,256}|-u|--utc|--universal|-R|--rfc-email|--rfc-2822|-I(date|hours|minutes|seconds|ns)?"
                   r"|--iso-8601(=(date|hours|minutes|seconds|ns))?|--rfc-3339=(date|seconds|ns)")
_DATE_VAL = re.compile(r"[A-Za-z0-9 :+@.,-]{1,64}")   # `-d` text: no '/', no TZ="file", no quotes
_RD = frozenset({"cat", "head", "tail", "wc", "nl", "cut", "stat", "du", "ls", "tree", "diff", "cmp", "md5sum", "sha256sum",
                 "sha1sum", "realpath", "readlink", "file", "sort", "uniq", "grep", "egrep", "fgrep", "find"})
_GREP = ("rRfdD", ("--recursive", "--dereference-recursive", "--file", "--directories", "--devices", "--exclude-from"))
_BAD = {"grep": _GREP, "egrep": _GREP, "fgrep": _GREP,   # round 4: every option whose value is a path read unchecked / a write
        "sort": ("oT", ("--output", "--compress-program", "--temporary-directory")), "tree": ("oR", ()),
        "file": ("CfmM", ("--compile", "--files-from", "--magic-file")), "diff": ("r", ("--recursive",)),
        **{k: ("c", ("--check",)) for k in ("md5sum", "sha256sum", "sha1sum")}}
_BAD_ALL = ("--files0-from",)   # wc / du / sort: a NUL list of names read second-hand
_FIND_FLAG = frozenset({"-print", "-print0", "-not", "!", "-a", "-o", "-and", "-or", "(", ")"})
_FIND_ARG = {"-name": None, "-iname": None, "-path": None, "-ipath": None, "-newer": None, "-type": r"[bcdpfls](,[bcdpfls])*",
             "-maxdepth": r"\d{1,4}", "-mindepth": r"\d{1,4}", "-size": r"[+-]?\d{1,12}[bcwkMG]?", "-mtime": r"[+-]?\d{1,9}",
             "-mmin": r"[+-]?\d{1,9}"}
_GIT = frozenset({"status", "log", "diff", "show", "add", "commit"})


def _bad_opt(name, a) -> bool:   # round 4: GNU takes any unambiguous prefix of a long option (`--fil` = `--file`)
    short, long_ = _BAD.get(name, ("", ()))
    if a.startswith("--"):
        k = a.split("=", 1)[0]
        return len(k) > 2 and any(x.startswith(k) for x in long_ + _BAD_ALL)
    return "/" in a or "~" in a or any(ch in short for ch in a[1:])   # a path glued to a short option (`-f/x`, `-m~/x`)


def _opt_ok(name, a, base, state) -> bool:   # an option word: not refused, and every text it may hold as a path is a read
    if _bad_opt(name, a):
        return False
    vals = [a.split("=", 1)[1]] if a.startswith("--") and "=" in a else []
    vals += [a] + ([a[k:] for k in range(1, len(a))] if not a.startswith("--") else [])   # `-fx`: x may be an attached value
    return all(_reads(v, base, state) for v in vals)


def _date_ok(args) -> bool:   # round 4 B4-1: `date -f/--file/-r/--reference` read a file; only output forms here
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-d", "--date") and i + 1 < len(args) and _DATE_VAL.fullmatch(args[i + 1]):
            i += 2
            continue
        if not (_DATE.fullmatch(a) or a.startswith("--date=") and _DATE_VAL.fullmatch(a[7:])):
            return False
        i += 1
    return True


def _git_ok(w, base, root, state) -> bool:
    from . import danger
    i = 1
    while i < len(w) and w[i] in ("-C", "--no-pager"):
        if w[i] == "-C" and not (i + 1 < len(w) and _under(_real(w[i + 1], base) or "", root)):
            return False
        i += 2 if w[i] == "-C" else 1
    if i >= len(w) or w[i] not in _GIT:
        return False
    args, j = w[i + 1:], 0
    while j < len(args):
        a = args[j]
        if a.startswith("--output") or a in ("--template", "-t") or a.startswith("--template="):
            return False
        if a in ("-m", "--message", "--author") or w[i] == "commit" and a in ("-C", "-c", "--reuse-message", "--reedit-message"):
            j += 2
            continue
        if a in ("-F", "--file"):
            if not (j + 1 < len(args) and _reads(args[j + 1], base, state)):
                return False
            j += 2
            continue
        p = a.split("=", 1)[-1] if a.startswith("-") else a.split(":", 1)[-1]
        r = _real(p, base) or "" if p else ""
        if p and (danger.cred_path(p) or danger.cred_path(r) or _under(r, state) or os.path.basename(p) in SECRET_NAMES
                  or os.path.basename(r) in SECRET_NAMES or _sysdev(p, base)):
            return False
        j += 1
    return True


def _bash_ok(cmd, root, state) -> bool:
    from . import danger
    if not isinstance(cmd, str) or not cmd.strip() or len(cmd) > 8192 or "$" in cmd or "`" in cmd or _globs(cmd):
        return False
    base = os.path.realpath(root)
    for c in danger.lex(cmd):
        w = c.words
        if not w or c.subst or c.heredoc or c.ins or any(t != "/dev/null" for t in c.redirs):
            return False
        n = w[0]
        if n == "cd":
            if len(w) != 2 or not _under(_real(w[1], base) or "", os.path.realpath(root)):
                return False
            base = _real(w[1], base)
        elif n == "git":
            if not _git_ok(w, base, os.path.realpath(root), state):
                return False
        elif n == "agentj":
            if not (w[1:3] == ["busy", "list"] and set(w[3:]) <= {"--json", "--no-units"} or w[1:3] == ["busy", "show"]
                    and len(w) >= 4 and not w[3].startswith("-") and set(w[4:]) <= {"--json", "--no-units"}):
                return False
        elif n == "date":
            if not _date_ok(w[1:]):
                return False
        elif n in _RO:
            continue
        elif n == "sed":                       # only `sed -n <a>[,<b>]p <files>` (sed can write / run commands otherwise)
            if len(w) < 4 or w[1] != "-n" or not re.fullmatch(r"\d+(,\d+)?p", w[2]) or not all(
                    not a.startswith("-") and _reads(a, base, state) for a in w[3:]):
                return False
        elif n in _RD:
            args = w[1:]
            if n == "find":
                while args and re.fullmatch(r"-P|-O\d", args[0]):   # N3: leading -P/-O<n>
                    args = args[1:]
                k = next((x for x, a in enumerate(args) if a.startswith("-") and a != "-" or a in ("(", ")", "!", ",")),
                         len(args))
                paths, ex = args[:k] or ["."], args[k:]   # every start point (none → the cwd) is a read
                while ex:            # round 3 F1: expression ALLOWLIST (no -files0-from, bfs -f, -exec, -H/-L/-follow …)
                    a = ex.pop(0)
                    if a in _FIND_FLAG:
                        continue
                    if a not in _FIND_ARG or not ex or _FIND_ARG[a] and not re.fullmatch(_FIND_ARG[a], ex[0]):
                        return False
                    v = ex.pop(0)
                    if a == "-newer":
                        paths.append(v)
            else:
                if any(a.startswith("-") and a != "-" and not _opt_ok(n, a, base, state) for a in args):
                    return False
                paths, skip, pat = [], False, n in ("grep", "egrep", "fgrep") and not any(a in ("-e", "--regexp") or
                                                                                          a.startswith("--regexp=") for a in args)
                for a in args:
                    if skip:
                        skip = False
                    elif a in ("-e", "--regexp"):
                        skip = True
                    elif not a.startswith("-"):
                        if pat:
                            pat = False
                        else:
                            paths.append(a)
                if n == "uniq" and len(paths) > 1:
                    return False
            if not all(_reads(p, base, state) for p in paths):
                return False
        else:
            return False
    return True


def allowed(tool, inp, root, state) -> bool:   # H1: the same_host no-card ALLOWLIST (ADR-#455 round 1); else the card
    if not isinstance(inp, dict) or not isinstance(root, str) or not root:
        return False
    state = os.path.realpath(state) if state else None
    if tool == "Bash":
        return not set(inp) - {"command", "description", "timeout"} and _bash_ok(inp.get("command"), root, state)
    if tool in ("Read", "NotebookRead", "LS"):
        p = inp.get("file_path") or inp.get("notebook_path") or inp.get("path")
        return isinstance(p, str) and os.path.isabs(p) and _reads(p, None, state)
    if tool == "Grep":
        p = inp.get("path")
        return isinstance(p, str) and os.path.isabs(p) and _reads(p, None, state) and os.path.isfile(p)
    if tool == "Glob":
        p, pat = inp.get("path") or root, inp.get("pattern")
        return isinstance(pat, str) and not pat.startswith(("/", "~")) and ".." not in pat and _reads(p, root, state)
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        p = inp.get("file_path") or inp.get("notebook_path")
        return isinstance(p, str) and os.path.isabs(p) and _writes(p, None, root, state)
    return False


def _stat(proc, pid):
    try:
        s = (proc / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
        return None if s[0] in ("Z", "X") else (s[0], int(s[1]), s[19])
    except (OSError, ValueError, IndexError):
        return None


def _uid(proc, pid):
    try:
        for line in (proc / str(pid) / "status").read_text().splitlines():
            if line.startswith("Uid:"):
                ids = {int(x) for x in line.split()[1:5]}
                return ids.pop() if len(ids) == 1 else None
    except (OSError, ValueError):
        pass
    return None


def _cgroup(proc, pid):
    try:
        for line in (proc / str(pid) / "cgroup").read_text().splitlines():
            if line.startswith("0::/"):
                return line[3:].strip()
    except OSError:
        pass
    return None


def _ns(proc=Path("/proc")):
    try:
        return os.readlink(proc / "self" / "ns" / "pid").split(":", 1)[1]
    except (OSError, IndexError):
        return None


class Launches:

    def __init__(self, root):
        self.path = Path(root) / "busy" / "peer_launch.json"
        self.lock = threading.Lock()
        self.units, self.dirty = {}, False
        try:
            d = json.loads(self.path.read_text())
            self.units = {str(k): ([float(v), None, None] if not isinstance(v, list) else
                                   [float(v[0]), v[1] if type(v[1]) is int else None, v[2] if isinstance(v[2], str) else None])
                          for k, v in (d.get("units") or {}).items() if UNIT.fullmatch(str(k))}
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def note(self, command, root=None, at=None, ctx=None) -> list:
        names = units(command, root, ctx)        # M1: only the whitelisted dispatch shape, lexed (never a word anywhere)
        if names:
            at = time.time() if at is None else at
            with self.lock:
                for n in names:
                    self.units.pop(n, None)
                    self.units[n] = [at, None, None]
                old = at - TTL
                self.units = dict([kv for kv in self.units.items() if kv[1][0] >= old][-KEEP:])
                self.dirty = True
        return names

    def save(self):
        with self.lock:
            if not self.dirty:
                return
            body, self.dirty = json.dumps({"units": self.units}).encode(), False
        try:
            from .state import _write_private
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            _write_private(self.path, body)
        except OSError:
            pass

    def verify(self, origin, session, proc=Path("/proc"), cgroup=Path("/sys/fs/cgroup"), uid=None, ns=None):
        if not sys.platform.startswith("linux"):
            return None, "platform"
        if not isinstance(origin, dict) or origin.get("kind") != "peer":
            return None, "shape"
        if "hopChain" in origin or origin.get("fromMode") != "bypass":
            return None, "relayed_or_mode"
        pid, start = origin.get("verifiedPeerPid"), origin.get("verifiedPeerProcStart")
        if type(pid) is not int or pid <= 1 or not isinstance(start, str) or not start.isdigit():
            return None, "unverified_peer"
        uid = os.getuid() if uid is None else uid
        ns = _ns(proc) if ns is None else ns
        rpid, rstart, dom = (session or {}).get("pid"), (session or {}).get("procStart"), (session or {}).get("pidDomain")
        if (type(rpid) is not int or not isinstance(rstart, str) or not isinstance(dom, str) or ":pid:" not in dom
                or ns is None or dom.rsplit(":pid:", 1)[1] != ns or (_stat(proc, rpid) or (0, 0, None))[2] != rstart):
            return None, "receiver_namespace"
        me = _stat(proc, pid)
        if me is None or me[2] != start or pid == os.getpid():
            return None, "sender_gone"
        if _uid(proc, pid) != uid:
            return None, "sender_uid"
        path = _cgroup(proc, pid)
        name = path.rsplit("/", 1)[-1] if path else ""
        if not path or path != f"/user.slice/user-{uid}.slice/user@{uid}.service/app.slice/{name}":   # M1: exact, no nesting
            return None, "sender_cgroup"
        with self.lock:
            rec = self.units.get(name)
        if rec is None:
            return None, "no_owner_launch"
        at = rec[0]
        try:
            raw = (cgroup / path.lstrip("/") / "cgroup.procs").read_text().split()
        except OSError:
            return None, "unit_unreadable"
        if len(raw) > MEMBERS:
            return None, "unit_too_large"
        try:
            members = {int(p) for p in raw}
        except ValueError:
            return None, "unit_unreadable"
        rows = {p: _stat(proc, p) for p in members}
        rows = {p: r for p, r in rows.items() if r is not None}
        if pid not in rows:
            return None, "sender_cgroup"
        roots = [p for p, r in rows.items() if r[1] not in rows]
        if len(roots) != 1:
            return None, "unit_not_one_tree"
        try:
            up = float((proc / "uptime").read_text().split()[0])
        except (OSError, ValueError, IndexError):
            return None, "clock"
        began = time.time() - (up - int(rows[roots[0]][2]) / HZ)
        if not at - MARGIN <= began <= at + WINDOW:
            return None, "unit_outside_launch"
        with self.lock:                     # M1: the first verified root is pinned (pid + start ticks); a later root is refused
            if rec[1] is None:
                rec[1], rec[2], self.dirty = roots[0], rows[roots[0]][2], True
            elif (rec[1], rec[2]) != (roots[0], rows[roots[0]][2]):
                return None, "unit_root_changed"
        return name, "ok"
