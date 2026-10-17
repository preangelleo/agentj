"""P362: keys this computer already has (backlog #71) — for the 「模型和 API Key」 page (keys.py). Names, and at most the last
4 characters of a long value, go to the phone; a value never leaves the computer and is never logged.

Sources, in this order (the service key file, agentj.env, is the managed one, not a source):
- envd    every ~/.config/environment.d/*.conf (systemd's user environment), sorted as systemd reads them;
- systemd the systemd user manager's environment (`systemctl --user show-environment`, Linux) — only names no file explains;
- file    the owner's own extra files, `api_keys.extra_env_files` (none by default; e.g. ~/projects/.env).

A file is read only when every folder on its path — walked from / by no-follow descriptors — belongs to this user or root
and is not writable by others (sticky folders excepted), and the file itself is a regular file of this user, not writable
by group / others, at most 256 KiB. A symlink anywhere on the path, another user's folder or file, Agent J's own state
folder → that source is skipped with one fixed reason; nothing in it is read. A value that refers to another variable ($…)
is not offered: systemd / dotenv would expand it and we would copy the wrong text.

「收进 Agent J 管理」 (keys.py, after Face ID) copies one value into agentj.env and — only when the owner asks — comments out
that NAME's lines in the source file (same walk, temp + rename in the same folder, the file's mode kept).
"""
from __future__ import annotations

import contextlib
import errno
import hashlib
import hmac
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys

MAX_BYTES = 256 * 1024
MAX_EXTRA = 8
MAX_FOUND = 6                  # source lines per row
SRC_RE = re.compile(r"systemd|s[0-9a-f]{16}")
REASONS = ("missing", "symlink", "foreign_owner", "unsafe_permissions", "unsafe_parent", "not_regular", "too_large",
           "unreadable", "not_absolute", "state_dir", "hard_link", "system_dir")
SYSTEM_DIRS = ("/dev", "/proc", "/sys")       # devices / kernel files are never a key source (a tty would become ours)
_D = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
_F = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOCTTY", 0)


class Unsafe(Exception):
    def __init__(self, why: str):
        super().__init__(why)
        self.why = why


# ---------------------------------------------------------------- the walk (no-follow descriptors, from /)
def _dir_ok(s, uid: int) -> None:
    if s.st_uid not in (uid, 0):
        raise Unsafe("foreign_owner")
    if s.st_mode & (stat.S_IWGRP | stat.S_IWOTH) and not s.st_mode & stat.S_ISVTX:
        raise Unsafe("unsafe_parent")


def _why_not(name: str, dfd: int) -> str:
    try:
        s = os.lstat(name, dir_fd=dfd)
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unreadable"
    return "symlink" if stat.S_ISLNK(s.st_mode) else "not_regular" if not stat.S_ISDIR(s.st_mode) else "unreadable"


def open_dir(path: str) -> int:
    """→ a no-follow descriptor of the folder `path` itself (every folder on the way checked). Raises Unsafe."""
    p = os.path.normpath(path)
    if not os.path.isabs(p):
        raise Unsafe("not_absolute")
    uid = os.getuid()
    fd = os.open("/", _D)
    try:
        for comp in [x for x in p.split(os.sep) if x]:
            try:
                nfd = os.open(comp, _D, dir_fd=fd)
            except OSError as e:
                raise Unsafe("missing" if e.errno == errno.ENOENT else _why_not(comp, fd)) from None
            os.close(fd)
            fd = nfd
            _dir_ok(os.fstat(fd), uid)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _open_file(dfd: int, name: str):
    """Regular files only, checked before the open (a tty / FIFO is never opened; O_NOCTTY covers the race) and after it."""
    try:
        pre = os.lstat(name, dir_fd=dfd)
    except FileNotFoundError:
        raise Unsafe("missing") from None
    except OSError:
        raise Unsafe("unreadable") from None
    if stat.S_ISLNK(pre.st_mode):
        raise Unsafe("symlink")
    if not stat.S_ISREG(pre.st_mode):
        raise Unsafe("not_regular")
    try:
        fd = os.open(name, _F, dir_fd=dfd)
    except OSError as e:
        raise Unsafe("missing" if e.errno == errno.ENOENT else _why_not(name, dfd)) from None
    s = os.fstat(fd)
    why = ("not_regular" if not stat.S_ISREG(s.st_mode) else "foreign_owner" if s.st_uid != os.getuid()
           else "unsafe_permissions" if s.st_mode & (stat.S_IWGRP | stat.S_IWOTH) else "too_large" if s.st_size > MAX_BYTES
           else "hard_link" if s.st_nlink > 1 else None)
    if why:
        os.close(fd)
        raise Unsafe(why)
    return fd, s


def _checked(path: str) -> str:
    p = os.path.normpath(path)
    if not os.path.isabs(p):
        raise Unsafe("not_absolute")
    if any(p == d or p.startswith(d + os.sep) for d in SYSTEM_DIRS):
        raise Unsafe("system_dir")
    return p


def read_file(path: str) -> str:
    """The text of one safe file (the walk above). Raises Unsafe."""
    p = _checked(path)
    dfd = open_dir(os.path.dirname(p))
    try:
        fd, _ = _open_file(dfd, os.path.basename(p))
    finally:
        os.close(dfd)
    with os.fdopen(fd, "rb") as f:
        data = f.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise Unsafe("too_large")
    return data.decode("utf-8", errors="replace")


def parse(text: str) -> dict:
    """NAME → literal value (the last assignment wins). Values with $ (expansion) or empty are left out."""
    from .keys import _LINE, _unquote
    out: dict = {}
    for line in text.splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        v = _unquote(m.group(2).strip())
        if v and "$" not in v and "\n" not in v and "\x00" not in v:
            out[m.group(1)] = v
        else:
            out.pop(m.group(1), None)
    return out


def comment_out(path: str, name: str) -> bool:
    """Prefix every `NAME=` / `export NAME=` line of the source file with "# "; every other byte kept; same mode; temp +
    rename inside the same (checked) folder. → False when no line had the name. Raises Unsafe / OSError."""
    p = _checked(path)
    base = os.path.basename(p)
    dfd = open_dir(os.path.dirname(p))
    try:
        fd, s = _open_file(dfd, base)
        with os.fdopen(fd, "rb") as f:
            raw = f.read(MAX_BYTES + 1)
            again = os.fstat(f.fileno())
        if len(raw) > MAX_BYTES or len(raw) != s.st_size or (again.st_size, again.st_mtime_ns) != (s.st_size, s.st_mtime_ns):
            raise Unsafe("too_large" if len(raw) > MAX_BYTES else "changed")   # growing / being written: touch nothing
        old = raw.decode("utf-8")
        pat = re.compile(r"^(\s*)((?:export\s+)?" + re.escape(name) + r"\s*=)")
        lines = old.splitlines(keepends=True)
        new = [pat.sub(r"\1# \2", ln, count=1) for ln in lines]
        if new == lines:
            return False
        tmp = "." + base + ".agentj-" + secrets.token_hex(4)
        tfd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=dfd)
        try:
            with os.fdopen(tfd, "wb") as f:
                f.write("".join(new).encode("utf-8"))
                f.flush()
                os.fsync(f.fileno())
                os.fchmod(f.fileno(), stat.S_IMODE(s.st_mode) & 0o755)
            now = os.lstat(base, dir_fd=dfd)
            if (now.st_dev, now.st_ino, now.st_size, now.st_mtime_ns) != (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns):
                raise Unsafe("missing")              # the file was replaced meanwhile: write nothing
            os.replace(tmp, base, src_dir_fd=dfd, dst_dir_fd=dfd)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp, dir_fd=dfd)
            raise
        return True
    finally:
        os.close(dfd)


# ---------------------------------------------------------------- the sources
def _tilde(p: str) -> str:
    from .service import tilde
    return tilde(p)


def _sid(kind: str, path: str) -> str:
    return "s" + hashlib.sha256(f"{kind}\0{path}".encode()).hexdigest()[:16]


def envd_dir() -> str:
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "environment.d")


def manager_env() -> dict:
    """The systemd user manager's environment (Linux). {} on other systems, without systemctl, or under a test runner (the
    test's fake HOME would still reach the real user manager)."""
    from .preferences import under_test
    if not sys.platform.startswith("linux") or under_test() or not shutil.which("systemctl"):
        return {}
    try:
        r = subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True, timeout=5, text=True,
                           errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if r.returncode != 0:
        return {}
    out = {}
    for line in r.stdout.splitlines():
        k, sep, v = line.partition("=")
        if sep and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k) and v and not v.startswith("$'") and "$" not in v:
            out[k] = v
    return out


def extra_files(prefs) -> list:
    from .preferences import get
    v = get(prefs or {}, "api_keys.extra_env_files", []) if isinstance(prefs, dict) else []
    return [x for x in v if isinstance(x, str)][:MAX_EXTRA] if isinstance(v, list) else []


def sources(host, managed: str) -> list:
    """[{id, label, kind, path?, values (host only), why?}] — `values` never leaves this module's callers."""
    out = []
    root = os.path.realpath(str(getattr(getattr(host, "st", None), "root", "") or "")) or None
    d = envd_dir()
    try:
        dfd = open_dir(d)
    except Unsafe as e:
        if e.why != "missing":
            out.append({"id": _sid("envd", d), "label": _tilde(d), "kind": "envd", "values": {}, "why": e.why})
    else:
        try:
            names = sorted(n for n in os.listdir(dfd) if n.endswith(".conf") and not n.startswith("."))[:64]
        finally:
            os.close(dfd)
        for n in names:
            p = os.path.join(d, n)
            out.append(_file_source("envd", p))
    files = []
    seen = {os.path.normpath(managed)}
    for raw in extra_files(getattr(host, "preferences", None)):
        p = os.path.normpath(os.path.expanduser(raw))
        if p in seen:
            continue
        seen.add(p)
        if not os.path.isabs(p):
            files.append({"id": _sid("file", p), "label": raw[:200], "kind": "file", "values": {}, "why": "not_absolute"})
        elif root and (p == root or p.startswith(root + os.sep)):
            files.append({"id": _sid("file", p), "label": _tilde(p), "kind": "file", "values": {}, "why": "state_dir"})
        else:
            files.append(_file_source("file", p))
    explained = {(k, v) for s in out + files for k, v in s["values"].items()}   # a file says more than the manager
    mgr = {k: v for k, v in manager_env().items() if (k, v) not in explained}
    if mgr:
        out.append({"id": "systemd", "label": "systemd", "kind": "systemd", "values": mgr})
    return out + files


def _file_source(kind: str, p: str) -> dict:
    s = {"id": _sid(kind, p), "label": _tilde(p), "kind": kind, "path": p, "values": {}}
    try:
        s["values"] = parse(read_file(p))
    except Unsafe as e:
        s["why"] = e.why
    except (OSError, UnicodeError):
        s["why"] = "unreadable"
    return s


def public(srcs: list) -> list:
    """The phone's view of the sources: id, label, kind, how many names, a reason when skipped."""
    out = []
    for s in srcs:
        m = {"id": s["id"], "label": s["label"], "kind": s["kind"], "count": len(s["values"])}
        if s.get("why"):
            m["why"] = s["why"]
        out.append(m)
    return out


def same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


_FP_KEY = secrets.token_bytes(32)          # this process only: a fingerprint means nothing elsewhere and cannot be brute-forced


def fingerprint(tid: str, name: str, src: str, value: str) -> str:
    """Which exact value the owner saw (bound into the import's Face ID): HMAC under a per-process random key, 16 hex."""
    return hmac.new(_FP_KEY, f"{tid}\0{name}\0{src}\0{value}".encode(), hashlib.sha256).hexdigest()[:16]


# ---------------------------------------------------------------- which login a harness really uses
CLOUD_ENV = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")


def claude_active(environ: dict) -> tuple[str, str | None]:
    """Claude Code's own order (its authentication docs): a cloud provider · ANTHROPIC_AUTH_TOKEN · ANTHROPIC_API_KEY ·
    apiKeyHelper · CLAUDE_CODE_OAUTH_TOKEN · the /login credentials. `env` in settings.json counts as environment.
    → (cloud | key | helper | sub | login | none, NAME or None)."""
    env = dict(environ)
    cfg = env.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    settings = {}
    with contextlib.suppress(Exception):
        import json
        from .provider_profiles import secure_read
        settings = json.loads(secure_read(os.path.join(cfg, "settings.json")))
    if isinstance(settings, dict) and isinstance(settings.get("env"), dict):
        env.update({k: v for k, v in settings["env"].items() if isinstance(v, str)})
    for k in CLOUD_ENV:
        if env.get(k) not in (None, "", "0", "false"):
            return "cloud", k
    for k in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"):
        if env.get(k):
            return "key", k
    if isinstance(settings, dict) and isinstance(settings.get("apiKeyHelper"), str) and settings["apiKeyHelper"].strip():
        return "helper", None
    if env.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return "sub", "CLAUDE_CODE_OAUTH_TOKEN"
    from . import harness
    bare = {k: v for k, v in env.items() if k not in (*CLOUD_ENV, "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY",
                                                         "CLAUDE_CODE_OAUTH_TOKEN")}
    with contextlib.suppress(Exception):
        r = harness.claude_login(environ=bare)
        if r[0] == "ok" and str(r[1]).startswith("login"):
            return "login", None
    return "none", None


def codex_active(environ: dict) -> tuple[str, str | None]:
    """Codex: the owner's own provider's env_key · CODEX_API_KEY · the /login file (auth.json) · OPENAI_API_KEY."""
    from . import harness
    with contextlib.suppress(Exception):
        from .peer_session import codex_user_model
        n = (codex_user_model(environ).get("provider_table") or {}).get("env_key")
        if isinstance(n, str) and environ.get(n):
            return "key", n
    if environ.get("CODEX_API_KEY"):
        return "key", "CODEX_API_KEY"
    with contextlib.suppress(Exception):
        if harness.codex_login()[0] == "ok":
            return "login", None
    if environ.get("OPENAI_API_KEY"):
        return "key", "OPENAI_API_KEY"
    return "none", None
