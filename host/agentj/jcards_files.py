"""P432 (#66 Cards phase 2, spec AGENTJ-CARDS-SPEC §8 文件管理, PROTOCOL §34): the official Files card — browse folders of
the Agent J host from the phone, download a document through the existing §13 media channel, or put `@<path>` in the
message box so the Agent reads it here.

Roots: the Agent's working folder (`agentj agent … --dir`), plus folders the owner adds on the computer with
`agentj card files add <folder>` (`<state>/cards/files-roots.json` — outside agentperm, so a fenced Agent cannot widen it).
A root is never `/`, never the state dir or a folder holding it. Every listing / download walks from the root one directory
at a time with O_NOFOLLOW (a link anywhere → refused) and never enters or shows: links, hidden names (`.ssh`, `.env`,
`.git`, `.agentj` …), credential folders / files (media.KEY_DIRS, media._denied, jcards.DENY_RE), the state dir. A listing
is ≤ LIST_MAX rows (dirs first); a download is Media.check(owner=True) against that root — the same type sniffing, caps,
key-file / key-content refusals and (dev, inode, size, mtime, sha256) re-checks as a reply's attachment — then
Media.register → `media_get`. Nothing is written except the owner's roots file; nothing runs.
P432 security review: a root is opened once and its real path read back from the fd (/proc/self/fd, F_GETPATH) — a folder
swapped for a link after the check is refused, not followed; everything the Agent fence hides (fence.protected_paths: this
state dir and its preferences, other instances' state / preferences, other seats' folders, the shared ASR folder) is never a
root, never entered and never listed; /proc, /sys, /dev are never roots; a download must resolve to the very path walked.
"""
from __future__ import annotations

import json
import os
import pathlib
import stat

from . import jcards, media as media_mod

LIST_MAX = 500
ROOTS_MAX = 8
SEG_MAX, DEPTH_MAX = 255, 32
_DIRFLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


def _roots_file(state_root) -> pathlib.Path:
    return pathlib.Path(state_root) / "cards" / "files-roots.json"


def _short(p: str) -> str:
    home = os.path.realpath(os.path.expanduser("~"))
    return "~" + p[len(home):] if p == home or p.startswith(home + os.sep) else p


def _under(p: str, r: str) -> bool:
    return p == r or p.startswith(r.rstrip(os.sep) + os.sep)


def hidden(state_root) -> list:
    """Real paths the fence hides from the Agent; the cards never show them either."""
    out = [os.path.realpath(str(state_root))]
    try:
        from .fence import protected_paths
        from .state import State
        out += [p for p in protected_paths(State(pathlib.Path(state_root))) if p not in out]
    except Exception:  # noqa: BLE001 — the state dir alone still holds
        pass
    return out


def fd_path(fd: int) -> str | None:
    """The real path of an open directory (Linux /proc/self/fd, macOS F_GETPATH); None when the platform cannot say."""
    try:
        return os.readlink(f"/proc/self/fd/{fd}")
    except OSError:
        pass
    try:
        import fcntl
        if hasattr(fcntl, "F_GETPATH"):
            return fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024)).split(b"\0", 1)[0].decode()
    except (OSError, ValueError):
        pass
    return None


def _bad_real(state_root, real: str) -> bool:
    """The rules on a real path (no filesystem lookup): never /, /proc /sys /dev, the state dir or a folder holding it,
    anything the fence hides, a credential / Agent J folder on the way."""
    state = os.path.realpath(str(state_root))
    return (real == os.sep or real == state or _under(state, real) or any(_under(real, h) for h in hidden(state_root))
            or any(_under(real, s) for s in ("/proc", "/sys", "/dev")) or any(s.lower() in media_mod.DENY_DIRS for s in real.split(os.sep) if s))


def _root_problem(state_root, path: str) -> str | None:
    if not os.path.isabs(path) or not os.path.isdir(path):
        return "not_a_folder"
    real = os.path.realpath(path)
    if _bad_real(state_root, real):
        return "refused"
    if any(s.lower() in media_mod.DENY_DIRS for s in real.split(os.sep) if s):
        return "refused"
    return None


def extra_roots(state_root) -> list:
    try:
        obj = json.loads(_roots_file(state_root).read_text(encoding="utf-8"))
        rows = obj.get("roots") if isinstance(obj, dict) else None
        return [r for r in rows if isinstance(r, str)][:ROOTS_MAX] if isinstance(rows, list) else []
    except (OSError, ValueError):
        return []


def roots(state_root, cfg) -> list:
    """[{id, path (real), label}] — r0 = the working folder, then the owner's own; a vanished / refused one is left out."""
    out, seen = [], set()
    cand = ([cfg["dir"]] if cfg and isinstance(cfg.get("dir"), str) else []) + extra_roots(state_root)
    for p in cand:
        p = os.path.expanduser(p)
        if _root_problem(state_root, p):
            continue
        real = os.path.realpath(p)
        if real in seen:
            continue
        seen.add(real)
        out.append({"id": f"r{len(out)}", "path": real, "label": _short(real)})
    return out


def change_roots(state_root, op: str, folder: str | None = None) -> dict:
    """`agentj card files add|remove <folder>` (the owner's own extra roots)."""
    cur = extra_roots(state_root)
    if op == "list":
        return {"ok": True, "roots": cur}
    folder = os.path.realpath(os.path.expanduser(folder or ""))
    if op == "add":
        why = _root_problem(state_root, folder)
        if why:
            return {"ok": False, "why": why}
        if folder not in cur:
            if len(cur) >= ROOTS_MAX:
                return {"ok": False, "why": "too_many"}
            cur.append(folder)
    elif folder in cur:
        cur.remove(folder)
    else:
        return {"ok": False, "why": "not_found"}
    p = _roots_file(state_root)
    p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = p.with_name(f".files-roots.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"roots": cur}, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    return {"ok": True, "roots": cur}


def _hidden(name: str, is_dir: bool) -> bool:
    low = name.lower()
    if name.startswith(".") or low in media_mod.DENY_DIRS:
        return True
    return not is_dir and (media_mod._denied([name]) or bool(jcards.DENY_RE.search(name)))


def _parts(rel) -> list | None:
    if rel in (None, ""):
        return []
    if not isinstance(rel, str) or len(rel) > 4096 or "\x00" in rel:
        return None
    parts = [s for s in rel.split("/") if s]
    if len(parts) > DEPTH_MAX or any(s in (".", "..") or len(s) > SEG_MAX for s in parts):
        return None
    return parts


def _same(fd: int, real: str) -> bool:
    p = fd_path(fd)
    if p is not None:
        return p == real
    try:                                      # no fd path on this platform: the same directory by (dev, inode)
        a, b = os.fstat(fd), os.stat(real)
        return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)
    except OSError:
        return False


def _open_dir(root: str, parts: list, state_root=None) -> int:
    """fd of root/parts… : the root is the very folder that was checked (its fd's real path = `root`), no link at any level
    below it, nothing hidden on the way, nothing the fence hides. Raises OSError / LookupError."""
    fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
    try:
        if not _same(fd, root) or (state_root is not None and _bad_real(state_root, root)):
            raise LookupError("refused")
        for s in parts:
            if _hidden(s, True):
                raise LookupError("refused")
            nd = os.open(s, _DIRFLAGS, dir_fd=fd)
            os.close(fd)
            fd = nd
        here = fd_path(fd)
        if state_root is not None and here is not None and any(_under(here, h) for h in hidden(state_root)):
            raise LookupError("refused")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _pick_root(state_root, cfg, rid):
    rs = roots(state_root, cfg)
    if not rs:
        return rs, None
    return rs, next((r for r in rs if r["id"] == rid), None) if rid else rs[0]


def listing(state_root, cfg, rid=None, rel=None) -> dict:
    """§34 jcard_files → {ok, roots, root, path, label, entries:[{name, kind, bytes?, mtime, dl?}], more?} | {ok:false, why}."""
    rs, r = _pick_root(state_root, cfg, rid)
    view = [{"id": x["id"], "label": x["label"]} for x in rs]
    if r is None:
        return {"ok": False, "why": "no_root" if not rs else "not_found", "roots": view}
    parts = _parts(rel)
    if parts is None:
        return {"ok": False, "why": "refused", "roots": view}
    state = os.path.realpath(str(state_root))
    here = os.path.join(r["path"], *parts)
    if here == state or here.startswith(state + os.sep):
        return {"ok": False, "why": "refused", "roots": view}
    try:
        fd = _open_dir(r["path"], parts, state_root)
    except LookupError:
        return {"ok": False, "why": "refused", "roots": view}
    except OSError as e:                      # a link on the way (ELOOP / ENOTDIR) is refused, a missing folder is gone
        return {"ok": False, "why": "gone" if isinstance(e, FileNotFoundError) else "refused", "roots": view}
    rows, hide = [], set(hidden(state_root))
    real_here = fd_path(fd) or here
    try:
        for name in os.listdir(fd):
            try:
                s = os.lstat(name, dir_fd=fd)
            except OSError:
                continue
            d = stat.S_ISDIR(s.st_mode)
            if not (d or stat.S_ISREG(s.st_mode)) or _hidden(name, d) or os.path.join(real_here, name) in hide:
                continue                                  # links, devices, FIFOs, hidden and credential names never show
            row = {"name": name, "kind": "dir" if d else "file", "mtime": int(s.st_mtime)}
            if not d:
                ext = os.path.splitext(name)[1].lower()
                row.update({"bytes": s.st_size, "dl": ext in media_mod.KINDS and s.st_size <= media_mod.CAPS[media_mod.KINDS[ext][0]]})
            rows.append(row)
    finally:
        os.close(fd)
    rows.sort(key=lambda x: (x["kind"] != "dir", x["name"].lower()))
    out = {"ok": True, "roots": view, "root": r["id"], "path": "/".join(parts), "label": _short(here), "entries": rows[:LIST_MAX]}
    if len(rows) > LIST_MAX:
        out["more"] = len(rows) - LIST_MAX
    return out


def fetch(state_root, cfg, med, rid, rel) -> dict:
    """§34 jcard_file → {ok, item: the §13 media item} | {ok:false, why}. The same checks as a reply's attachment."""
    rs, r = _pick_root(state_root, cfg, rid)
    parts = _parts(rel)
    if r is None or not parts:
        return {"ok": False, "why": "not_found"}
    if any(_hidden(s, i < len(parts) - 1) for i, s in enumerate(parts)):
        return {"ok": False, "why": "refused"}
    try:                                                  # every folder on the way and the file: no link, nothing hidden
        d = _open_dir(r["path"], parts[:-1], state_root)
        try:
            walked = os.lstat(parts[-1], dir_fd=d)
            if not stat.S_ISREG(walked.st_mode):
                return {"ok": False, "why": "refused"}
        finally:
            os.close(d)
    except FileNotFoundError:
        return {"ok": False, "why": "gone"}
    except (OSError, LookupError):
        return {"ok": False, "why": "refused"}
    path = os.path.join(r["path"], *parts)
    try:
        rec, why = med.check(media_mod.Ref(path, path, True), owner=True, root=r["path"])
    except OSError:
        rec, why = None, "gone"
    if why or rec is None:
        why = {"key_file": "secret", "key_content": "secret"}.get(why, why) or "type"
        return {"ok": False, "why": why, **({"bytes": rec["bytes"]} if rec and why == "too_big" else {})}
    if rec.get("root") != r["path"] or list(rec.get("parts") or []) != parts \
            or (rec.get("dev"), rec.get("ino")) != (walked.st_dev, walked.st_ino):   # the very file walked, not one swapped in
        return {"ok": False, "why": "refused"}
    return {"ok": True, "item": med.register(rec, 0)}
