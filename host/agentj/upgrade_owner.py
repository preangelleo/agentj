"""#475: program files another account owns, and the one step that gives them back.

A paid macOS host (0.25.6a1 → 0.26.8a1) could not upgrade: its uv tool folder belonged to root (an earlier
`sudo uv tool install`), so the program directory was "read-only" and nothing said why. This finds such paths — the tool
environment (bounded walk), its parent folder, the `agentj` entry point and uv's cache — and the highest folder under the
owner's home that another account owns, then writes the one fix line: an `agentj sudo` card on the paired phone (the owner
types the password there; nothing is done silently). Only local paths in the fix line; support only ever sees owner_kind.
"""
from __future__ import annotations

import os
import shlex
import sys

SCAN_MAX = 4000          # entries walked inside the tool environment (a uv env holds ~2-3k files)
LSTAT = os.lstat         # seam (tests fake a root owner; no real sudo)
TOP_MAX = 32             # folders _top may climb (security A-M1: always terminates)


def _pw_home(uid):
    try:
        import pwd
        return pwd.getpwuid(uid).pw_dir
    except (ImportError, KeyError):
        return ""


HOME_OF = _pw_home       # security A-M1: the account database, never $HOME (seam for tests)


def _inside(path, home):
    """Strictly below home (never home itself, its parent or anything above); both already real paths."""
    try:
        return path != home and os.path.commonpath([path, home]) == home
    except ValueError:
        return False


def _real(path, *, link=False):
    """Resolved path; `link`: resolve the folder but keep the last component (lstat / chown -h act on the link itself)."""
    path = os.path.normpath(os.path.abspath(os.path.expanduser(path)))
    if link:
        return os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path))
    return os.path.realpath(path)
WHY = "把 Agent J 的程序目录交还给你自己的账号 / give Agent J's program folders back to your own account"


def _owner(path, lstat):
    try:
        return lstat(path).st_uid
    except OSError:
        return None


def _foreign_inside(prefix, uid, lstat, limit):
    n = 0
    for root, dirs, files in os.walk(prefix):        # never follows symlinks; lstat sees the link itself
        for name in dirs + files:
            n += 1
            if n > limit:
                return None
            o = _owner(os.path.join(root, name), lstat)
            if o is not None and o != uid:
                return o
    return None


def _top(path, home, uid, lstat):
    """The highest folder above `path`, strictly inside home, that another account owns (bounded, stops at /)."""
    top, p = path, os.path.dirname(path)
    if home in ("", os.sep):
        return top
    for _ in range(TOP_MAX):
        if p == os.path.dirname(p) or not _inside(p, home):
            break
        o = _owner(p, lstat)
        if o is None or o == uid:
            break
        top, p = p, os.path.dirname(p)
    return top


def _entry_point(environ, home):
    d = environ.get("UV_TOOL_BIN_DIR") or environ.get("XDG_BIN_HOME") or os.path.join(home, ".local", "bin")
    return os.path.join(os.path.expanduser(d), "agentj")


def _uv_cache(environ, home):
    if environ.get("UV_CACHE_DIR"):
        return os.path.expanduser(environ["UV_CACHE_DIR"])
    base = environ.get("XDG_CACHE_HOME") if os.path.isabs(environ.get("XDG_CACHE_HOME") or "") else os.path.join(home, ".cache")
    return os.path.join(base, "uv")


def _upgrade_cache(environ, home):
    """#0307: where `agentj update` keeps the verified wheel (update.upgrade_cache_root) — a root-owned one refuses with EACCES."""
    xdg = environ.get("XDG_CACHE_HOME") or ""
    return os.path.join(os.path.expanduser(xdg) if xdg else os.path.join(home, ".cache"), "agentj-upgrade")


def _user(uid):
    try:
        import pwd
        return pwd.getpwuid(uid).pw_name
    except (ImportError, KeyError):
        return str(uid)


def blocked(prefix=None, *, uid=None, home=None, environ=None, lstat=None, limit=SCAN_MAX, user=None, state_root=None) -> dict | None:
    """{"owner_kind": "root"|"other", "paths": [...], "fix": "agentj sudo … -- chown -R -h <user> <paths>"} when program
    files under home belong to another account; None otherwise (fine, Windows, running as root, or installed outside home)."""
    if os.name != "posix":
        return None
    lstat = lstat or LSTAT
    uid = os.getuid() if uid is None else uid
    if uid == 0:
        return None
    environ = os.environ if environ is None else environ
    home = home if home is not None else HOME_OF(uid)
    if not home or not os.path.isabs(home) or os.path.normpath(home) == os.sep:
        return None                                   # security A-M1: no account home → no chown target at all
    home = os.path.realpath(home)
    if home == os.sep:
        return None
    explicit = prefix is not None
    prefix = _real(prefix or sys.prefix)
    if explicit and not _inside(prefix, home):
        return None
    found = []                                        # (path, owner uid) — every one real and strictly inside home
    if _inside(prefix, home):
        o = _owner(prefix, lstat)
        inner = o if o is not None and o != uid else _foreign_inside(prefix, uid, lstat, limit)
        if inner is not None:
            found.append((prefix, inner))
    from .owned_paths import inventory
    from .state_paths import state_dir
    # Shared harness configuration and service directories are inspected by name only;
    # never recursively chown an unrelated customer's files inside them.
    owned = inventory(home, environ, state_root if state_root is not None else state_dir())
    recursive = {_real(str(p), link=True) for p in (owned[0], owned[1], owned[3], owned[4])}
    for p in (os.path.dirname(prefix), *(str(x) for x in owned)):
        p = _real(p, link=True)
        if not _inside(p, home):
            continue
        o = _owner(p, lstat)
        if o is not None and o != uid:
            found.append((p, o))
        elif p in recursive and os.path.isdir(p) and not os.path.islink(p):
            inner = _foreign_inside(p, uid, lstat, limit)
            if inner is not None:
                found.append((p, inner))
    if not found:
        return None
    tops = []
    shared_files = {_real(str(p), link=True) for p in owned if str(p).endswith((
        'settings.json', 'agentj-statusline.json', 'config.toml'))}
    shared_files.update(_real(str(p), link=True) for p in owned if str(p).endswith(('opencode.json', 'opencode.jsonc', '.service', '.env', '.plist')))
    shared_files.add(_real(str(owned[0] / 'host.log'), link=True))
    shared_files.add(_real(_entry_point(environ, home), link=True))
    # A foreign state/cache ancestor is not permission to recursively change
    # unrelated files in ~/.local or ~/.config. Only the uv program tree retains
    # the historical highest-foreign-ancestor repair contract.
    shared_roots = {_real(str(p), link=True) for p in owned}
    for p, _ in found:
        # Never promote an individual shared configuration file to its parent:
        # ~/.claude, ~/.codex and ~/.config can hold unrelated customer files.
        t = p if p in shared_files or p in shared_roots else _top(p, home, uid, lstat)
        if not _inside(t, home):
            continue
        if not any(t == x or t.startswith(x + os.sep) for x in tops):
            tops = [x for x in tops if not x.startswith(t + os.sep)] + [t]
    if not tops:
        return None
    name = user or _user(uid)
    fix = (f"agentj sudo --why {shlex.quote(WHY)} -- chown -R -h {shlex.quote(name)} "
           + " ".join(shlex.quote(t) for t in tops))
    return {"owner_kind": "root" if any(o == 0 for _, o in found) else "other", "paths": tops, "fix": fix}


def sentence(res: dict, lang: str = "zh") -> str:
    """One plain sentence for the owner: what happened and the one step (an admin card on the phone)."""
    who = "root" if res.get("owner_kind") == "root" else ("another account" if lang == "en" else "另一个账号")
    if lang == "en":
        return (f"Agent J's program folder belongs to {who} (probably installed with sudo earlier), so this account cannot "
                "replace it and nothing was changed. One step fixes it: I run the fix line, your phone shows one "
                "administrator-password card, you approve it, then I upgrade again.")
    return (f"Agent J 的程序目录属于 {who}（多半是以前用 sudo 装的），这个账号改不了它，所以这次什么都没动。修复只要一步："
            "我运行 fix 那一行，手机上会弹一张管理员密码卡，你批准后我再升级一次。")
