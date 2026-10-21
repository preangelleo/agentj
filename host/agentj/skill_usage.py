"""P426 #181-3 (PROTOCOL §39): skill health — when each skill was last used and how often, and the owner's archive.

The ledger <state>/skill-usage.json keeps, per skill NAME, a count, the last use time and the count per source — nothing of
the conversation. Only events Agent J sees exactly are counted (never estimated):

- `phone`  — the owner sent `/<skill>` from a remote (any harness; serve._accept);
- `claude` — Claude Code ran the skill: its `Skill` tool (shared session hook PostToolUse, the independent session's
  stream-json tool_use) or a `/<skill>` typed on the desktop of the shared session (UserPromptSubmit).

Codex, OpenCode and the other harnesses report no skill event Agent J can see: there only `/<skill>` from a remote counts and
the page shows 「—」 where nothing was seen; a skill is never called "long unused" there (that would be a guess). One use =
the same name once within DEDUP_S (a phone `/x` reaches the session's own prompt hook as well).

`seen` = when this Agent J host first listed the skill (the honest "installed for N days" — the folder's dates can be copies).
Stale = tracked harness, seen ≥ `skills.stale_days` (default 30) ago and no use within that many days. Never automatic: the
owner archives on the phone (or `agentj skills archive`); bundled / official (`agentj-*` and the plaza's reserved names)
skills are never suggested and refused. Archive = move the folder next to its skills folder, `<…>/skills-archive/<dir>`
(the harness does not read it; same file system, so a rename); restore moves it back when the old place is free. Nothing is
ever deleted. The index is `<state>/skill-archive.json` (serve's own copy — never a file the Agent can write), and every move
walks no-follow directory fds from / (renameat): a link at any level, or a place inside / around the state directory or
another protected path, refuses (B1, ADR-P426S)."""
from __future__ import annotations

import contextlib
import errno
import json
import os
import re
import stat
import time
from pathlib import Path

LEDGER = "skill-usage.json"
LOCK = "skill-usage"
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
DIR_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
SOURCES = ("phone", "claude")
TRACKED = ("claude",)            # harnesses whose own skill runs Agent J sees exactly
DEDUP_S = 30
MAX_NAMES = 400
DAY = 86400
DEFAULT_DAYS = 30
ARCHIVE = "skills-archive"
STATE_INDEX = "skill-archive.json"   # in the state directory, never next to the archive
ILOCK = "skill-archive"
OFFICIAL = re.compile(r"^(agentj|agent-j|agentjarvis|agent-jarvis|jarvis|official|admin|moderator|plaza)(-|$)")   # = market.RESERVED


# ------------------------------------------------------------------ the ledger
def _path(st) -> Path:
    return Path(st.root) / LEDGER


def load(st) -> dict:
    try:
        d = json.loads(_path(st).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        d = {}
    if not isinstance(d, dict):
        d = {}
    use = d.get("skills") if isinstance(d.get("skills"), dict) else {}
    seen = d.get("seen") if isinstance(d.get("seen"), dict) else {}
    return {"v": 1, "skills": {k: v for k, v in use.items() if isinstance(k, str) and NAME.fullmatch(k) and isinstance(v, dict)},
            "seen": {k: int(v) for k, v in seen.items() if isinstance(k, str) and NAME.fullmatch(k) and type(v) in (int, float)}}


def _save(st, d: dict) -> None:
    for key in ("skills", "seen"):           # bounded: the oldest names go first
        m = d[key]
        if len(m) > MAX_NAMES:
            order = sorted(m, key=lambda k: (m[k].get("last", 0) if isinstance(m[k], dict) else m[k]))
            for k in order[:len(m) - MAX_NAMES]:
                m.pop(k, None)
    st.write_private(_path(st), (json.dumps(d, ensure_ascii=False, sort_keys=True) + "\n").encode())


def note(st, name, src: str, now: float | None = None) -> bool:
    """One use of skill `name` from `src` (SOURCES). False when refused (shape) or folded into the previous use (DEDUP_S)."""
    if not isinstance(name, str) or not NAME.fullmatch(name) or src not in SOURCES:
        return False
    now = int(time.time() if now is None else now)
    with st.file_lock(LOCK):
        d = load(st)
        r = d["skills"].get(name) or {}
        last = r.get("last") if type(r.get("last")) is int else 0
        if now - last < DEDUP_S and last <= now:
            return False
        by = r.get("src") if isinstance(r.get("src"), dict) else {}
        by = {k: int(v) for k, v in by.items() if k in SOURCES and type(v) is int}
        by[src] = by.get(src, 0) + 1
        d["skills"][name] = {"n": (r.get("n") if type(r.get("n")) is int else 0) + 1, "last": now, "src": by}
        d["seen"].setdefault(name, now)
        _save(st, d)
    return True


def mark_seen(st, names, now: float | None = None) -> dict:
    """First-listed time per name; writes only when a name is new. → the seen map."""
    now = int(time.time() if now is None else now)
    names = [n for n in names if isinstance(n, str) and NAME.fullmatch(n)]
    d = load(st)
    if all(n in d["seen"] for n in names):
        return d["seen"]
    with st.file_lock(LOCK):
        d = load(st)
        for n in names:
            d["seen"].setdefault(n, now)
        _save(st, d)
    return d["seen"]


def stale_days(prefs=None, st=None) -> int:
    try:
        from . import preferences
        doc = prefs if isinstance(prefs, dict) else preferences.effective(st)
        v = preferences.get(doc, "skills.stale_days", DEFAULT_DAYS)
        return v if type(v) is int and 7 <= v <= 365 else DEFAULT_DAYS
    except Exception:  # noqa: BLE001 — a broken config never hides the page
        return DEFAULT_DAYS


def official(s: dict) -> bool:
    return s.get("scope") == "bundled" or bool(OFFICIAL.match(str(s.get("name") or "")))


def annotate(st, skills: list, kind: str | None, prefs=None, now: float | None = None) -> dict:
    """Adds to each S of the §30 list: `use` {n, last} (only when seen used), `stale` (true only on a tracked harness),
    `arch` (the owner may archive it: a global, non-official skill that is a real folder), `link` (Jarvis ruling 4: a global
    skill reached through a symbolic link — never archivable, the phone hides 「归档」 and says why). → `skill_use` {tracked, days}."""
    now = int(time.time() if now is None else now)
    days = stale_days(prefs, st)
    tracked = kind in TRACKED
    try:
        seen = mark_seen(st, [s.get("name") for s in skills], now)
        use = load(st)["skills"]
    except OSError:
        seen, use = {}, {}
    links = None
    for s in skills:
        r = use.get(s.get("name"))
        if r and type(r.get("n")) is int and type(r.get("last")) is int:
            s["use"] = {"n": r["n"], "last": r["last"]}
        if official(s):
            continue
        if s.get("scope") == "global":
            if links is None:
                links = _linked(kind)
            if s.get("name") in links:
                s["link"] = True
            else:
                s["arch"] = True
        first = seen.get(s.get("name"))
        if tracked and first and now - first >= days * DAY and (not r or now - int(r.get("last") or 0) >= days * DAY):
            s["stale"] = True
    return {"tracked": tracked, "days": days}


# ------------------------------------------------------------------ installed names (for the phone's `/name` and #181-4)
_NAMES: dict = {}
NAMES_TTL = 300


def installed(kind: str | None, cwd: str | None = None, now: float | None = None) -> set:
    """Skill names (+ aliases) installed for this harness: bundled, global, and the Agent folder's own. Cached NAMES_TTL s."""
    now = time.monotonic() if now is None else now
    key = (kind, cwd)
    hit = _NAMES.get(key)
    if hit and now - hit[0] < NAMES_TTL:
        return hit[1]
    from . import workflows
    out: set = set()
    bases = [str(workflows.SKILLS)] + workflows.global_dirs(kind)
    if cwd:
        bases += [os.path.join(cwd, ".claude", "skills"), os.path.join(cwd, ".agents", "skills")]
    for b in bases:
        with contextlib.suppress(OSError):
            for n in os.listdir(b):
                if not n.startswith((".", "_")) and os.path.isfile(os.path.join(b, n, "SKILL.md")):
                    out.add(n)
    for name, al in workflows.alias_table().items():
        if name in out:
            out.update(a for a in al if isinstance(a, str))
    _NAMES.clear()
    _NAMES[key] = (now, out)
    return out


def canonical(name: str) -> str:
    """An alias from the bundled table → its skill's name (the ledger counts the skill once)."""
    from . import workflows
    for n, al in workflows.alias_table().items():
        if name in al:
            return n
    return name


# ------------------------------------------------------------------ archive / restore
class ArchiveError(Exception):
    def __init__(self, why: str):
        super().__init__(why)
        self.why = why


def _roots(kind: str | None) -> list[Path]:
    from . import workflows
    seen, out = set(), []
    for d in workflows.global_dirs(kind):
        p = Path(d)
        if str(p) not in seen:
            seen.add(str(p))
            out.append(p)
    return out


def _skill_name(d: Path) -> str:
    from . import workflows
    meta = workflows._meta(str(d / "SKILL.md"))
    return meta["name"] if isinstance(meta.get("name"), str) and meta.get("name") else d.name


def _linked(kind: str | None) -> set:
    """Names of global skills that archive() would refuse as "link": the folder, its skills root or the root's parent is a
    symbolic link (one listing per root, not per skill)."""
    out = set()
    for root in _roots(kind):
        try:
            entries = sorted(os.listdir(root))
        except OSError:
            continue
        up = os.path.islink(root) or os.path.islink(root.parent)
        for n in entries:
            d = root / n
            if n.startswith((".", "_")) or not (d / "SKILL.md").is_file():
                continue
            if up or os.path.islink(d):
                out.update((n, _skill_name(d)))
    return out


def _find(name: str, kind: str | None) -> Path | None:
    from . import workflows
    bundled = str(workflows.SKILLS.resolve()) + os.sep
    for root in _roots(kind):
        try:
            entries = sorted(os.listdir(root))
        except OSError:
            continue
        for n in entries:
            d = root / n
            if n.startswith((".", "_")) or not (d / "SKILL.md").is_file():
                continue
            if os.path.realpath(d).startswith(bundled):
                continue                         # a bundled skill linked into the harness folder
            if n == name or _skill_name(d) == name:
                return d
    return None


_DIRFLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _walk(real: str) -> int:
    """fd of the symlink-free absolute path `real`, opened from / one level at a time, never following a link (OSError when
    a component is one — e.g. swapped in after the realpath)."""
    fd = os.open("/", _DIRFLAGS)
    try:
        for part in (p for p in real.split(os.sep) if p):
            nxt = os.open(part, _DIRFLAGS, dir_fd=fd)
            os.close(fd)
            fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


def _sub(parent: int, name: str, create: bool, mode: int = 0o700) -> int:
    try:
        return os.open(name, _DIRFLAGS, dir_fd=parent)
    except FileNotFoundError:
        if not create:
            raise
        with contextlib.suppress(FileExistsError):
            os.mkdir(name, mode, dir_fd=parent)
        return os.open(name, _DIRFLAGS, dir_fd=parent)


def _protected(st) -> list[str]:
    from . import fence
    return fence.protected_paths(st)


@contextlib.contextmanager
def _places(st, root: Path, create_root: bool):
    """(fd of the skills root, fd of its archive) for a move that never leaves `root.parent` and never touches a hidden
    directory: root.parent / root / the archive must be real directories (no link at any level, opened by no-follow fds
    from /), where their realpath says, and neither inside nor around the state directory or any other protected path
    (fence.protected_paths). Raises ArchiveError("link" | "protected") — OSError for anything else."""
    from . import fence
    root = Path(os.path.abspath(root))
    if os.path.islink(root.parent) or os.path.islink(root):
        raise ArchiveError("link")
    pr = os.path.realpath(root.parent)
    rr, ar = os.path.join(pr, root.name), os.path.join(pr, ARCHIVE)
    if os.path.lexists(root) and os.path.realpath(root) != rr:
        raise ArchiveError("link")
    prot = [os.path.realpath(p) for p in _protected(st)]
    if any(fence._under(x, q) or fence._under(q, x) for x in (rr, ar) for q in prot) or any(fence._under(pr, q) for q in prot):
        raise ArchiveError("protected")
    ids = set()
    for q in prot:
        with contextlib.suppress(OSError):
            s = os.stat(q)
            ids.add((s.st_dev, s.st_ino))
    fds: list[int] = []
    try:
        try:
            fds.append(_walk(pr))
            fds.append(_sub(fds[0], root.name, create_root, 0o755))
            fds.append(_sub(fds[0], ARCHIVE, True))
        except OSError as e:
            if e.errno in (errno.ELOOP, errno.ENOTDIR):
                raise ArchiveError("link") from None
            raise
        for fd in fds:
            s = os.fstat(fd)
            if (s.st_dev, s.st_ino) in ids:
                raise ArchiveError("protected")
        yield fds[1], fds[2]
    finally:
        for fd in fds:
            os.close(fd)


def _is_dir(name: str, dfd: int) -> bool | None:
    """True = a real directory entry, False = something else (a link …), None = nothing there."""
    try:
        return stat.S_ISDIR(os.stat(name, dir_fd=dfd, follow_symlinks=False).st_mode)
    except FileNotFoundError:
        return None


# The archive's index lives in the state directory (written by serve / the CLI outside the fence): a file next to the
# archive would be the Agent's to forge.
def _ipath(st) -> Path:
    return Path(st.root) / STATE_INDEX


def _load_index(st) -> list[dict]:
    try:
        d = json.loads(_ipath(st).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = d.get("items") if isinstance(d, dict) else None
    return [r for r in items or [] if isinstance(r, dict) and isinstance(r.get("root"), str) and isinstance(r.get("dir"), str)
            and DIR_NAME.fullmatch(r["dir"]) and isinstance(r.get("name"), str) and NAME.fullmatch(r["name"])]


def _save_index(st, items: list[dict]) -> None:
    st.write_private(_ipath(st), (json.dumps({"v": 1, "items": items[-MAX_NAMES:]}, ensure_ascii=False, sort_keys=True)
                                  + "\n").encode())


def archive(st, name, kind: str | None, now: float | None = None) -> dict:
    """Move a global, non-official skill out of the harness's skills folder (restorable). → {ok, name, to}."""
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ArchiveError("shape")
    if official({"name": name}) or (Path(__file__).with_name("skills") / name / "SKILL.md").is_file():
        raise ArchiveError("official")
    src = _find(name, kind)
    if src is None:
        raise ArchiveError("not_found")
    root = src.parent
    with st.file_lock(ILOCK), _places(st, root, False) as (rfd, afd):
        if _is_dir(src.name, rfd) is not True:
            raise ArchiveError("link" if os.path.lexists(src) else "not_found")
        dest, i = src.name, 2
        while _is_dir(dest, afd) is not None:
            dest, i = f"{src.name}-{i}", i + 1
        os.rename(src.name, dest, src_dir_fd=rfd, dst_dir_fd=afd)
        items = [r for r in _load_index(st) if not (r["root"] == str(root) and r["dir"] == dest)]
        items.append({"root": str(root), "dir": dest, "name": name, "from": src.name, "at": int(time.time() if now is None else now)})
        _save_index(st, items)
    return {"ok": True, "name": name, "to": str(root.parent / ARCHIVE / dest)}


def archived(st, kind: str | None) -> list[dict]:
    roots = {str(r) for r in _roots(kind)}
    out = []
    for r in _load_index(st):
        if r["root"] in roots and os.path.lexists(Path(r["root"]).parent / ARCHIVE / r["dir"]):
            out.append({"name": r["name"], "at": r.get("at") if type(r.get("at")) is int else 0, "dir": r["dir"],
                        "from": str(Path(r["root"]) / (r.get("from") if isinstance(r.get("from"), str)
                                                      and DIR_NAME.fullmatch(r["from"]) else r["dir"]))})
    return sorted(out, key=lambda a: (a["name"], a["dir"]))


def restore(st, name, kind: str | None) -> dict:
    """Move an archived skill back to where it was. → {ok, name, to}; refused when that place is taken."""
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ArchiveError("shape")
    roots = {str(r) for r in _roots(kind)}
    with st.file_lock(ILOCK):
        items = _load_index(st)
        for rec in sorted((r for r in items if r["name"] == name and r["root"] in roots),
                          key=lambda r: -(r.get("at") if type(r.get("at")) is int else 0)):
            root = Path(rec["root"])
            back = rec.get("from") if isinstance(rec.get("from"), str) and DIR_NAME.fullmatch(rec["from"]) else rec["dir"]
            with _places(st, root, True) as (rfd, afd):
                there = _is_dir(rec["dir"], afd)
                if there is None:
                    continue
                if there is False:
                    raise ArchiveError("link")
                if _is_dir(back, rfd) is not None:
                    raise ArchiveError("exists")
                os.rename(rec["dir"], back, src_dir_fd=afd, dst_dir_fd=rfd)
            _save_index(st, [r for r in items if r is not rec])
            return {"ok": True, "name": name, "to": str(root / back)}
    raise ArchiveError("not_found")


# ------------------------------------------------------------------ `agentj skills health` (CLI / elevate.sock)
def health(st, kind: str | None, root=None, lang: str = "zh", prefs=None, now: float | None = None) -> dict:
    from . import workflows
    now = int(time.time() if now is None else now)
    if root is not None:
        pic = workflows.Scanner(st).scan(root, kind, lang)
        skills = [dict(s) for s in pic["tail"]["skills"]]
    else:
        skills = [dict(s) for s in workflows.common_skills(kind)[0]]
        for s in skills:
            s.pop("_path", None)
            s.pop("_al", None)
    info = annotate(st, skills, kind, prefs, now)
    rows = [{"name": s["name"], "scope": s["scope"], **({"folder": s["folder"]} if s.get("folder") else {}),
             "uses": s["use"]["n"] if s.get("use") else None, "last": s["use"]["last"] if s.get("use") else None,
             "stale": bool(s.get("stale")), "archivable": bool(s.get("arch")), "official": official(s),
             **({"link": True} if s.get("link") else {})} for s in skills]
    return {"ok": True, "harness": kind, "tracked": info["tracked"], "days": info["days"], "skills": rows,
            "archived": [{k: a[k] for k in ("name", "at")} for a in archived(st, kind)]}


def ago(ts, now: float | None = None, lang: str = "zh") -> str:
    if not ts:
        return "—"
    d = max(0, int(((time.time() if now is None else now) - ts) // DAY))
    if lang == "en":
        return "today" if d == 0 else f"{d} day{'s' if d != 1 else ''} ago"
    return "今天" if d == 0 else f"{d} 天前"


__all__ = ["note", "annotate", "installed", "canonical", "archive", "restore", "archived", "health", "ArchiveError", "ago"]
