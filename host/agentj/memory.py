"""「它记住了什么」 (PROMPT-26 item 3; ADR-A50): what the configured Agent loads as memory, item by item, and deleting one item
with an undo.

Sources (only these paths, per harness — measured / documented in ARCHITECTURE ADR-A50):
  Claude Code  the folder's CLAUDE.md, CLAUDE.local.md, .claude/CLAUDE.md; the user's <config>/CLAUDE.md
               (<config> = $CLAUDE_CONFIG_DIR or ~/.claude); auto memory <config>/projects/<slug>/memory/*.md, where <slug> is
               the git top level of the folder (else the folder itself) with every character outside [A-Za-z0-9] turned
               into "-" (measured with Claude Code 2.1.285: a memory written from a sub-folder of a repo lands under the repo
               root's slug)
  Codex        $CODEX_HOME/AGENTS.md (+ AGENTS.override.md), the folder's AGENTS.md (+ AGENTS.override.md); the
               consolidated memories $CODEX_HOME/memories/*.md (the `memories` feature, off by default in 0.159)
  OpenCode     the folder's AGENTS.md, ~/.config/opencode/AGENTS.md ($XDG_CONFIG_HOME); when those are absent OpenCode
               falls back to the folder's CLAUDE.md and ~/.claude/CLAUDE.md (its Claude Code compatibility, per its docs)

Items: a file in a memory folder = one item (a memory folder's MEMORY.md index is split like a Markdown file, and deleting a
memory file also removes the index lines that link to it); a Markdown file is split into list items, paragraphs, headings and
fenced blocks (front matter is not an item). Item id = SHA-256 of its text (16 hex) + "~k" for the k-th repeat.

Deleting: the caller gives the file's SHA-256 it saw (optimistic lock: a file changed since → refused, the phone refreshes);
the removed text goes to the trash `<state>/memory_trash/<id>.json` (0600) for 7 days — `agentj memory restore <id>` or the
phone's 「撤销」 puts it back. Never follows a symlink (any component below the source's base, the file itself); one file
≤ 256 KiB, ≤ 200 files per folder, ≤ 2 MiB in total.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
import re
import secrets
import stat
import time

from . import diskq

MAX_FILE = 256 * 1024
MAX_TOTAL = 2 * 1024 * 1024
MAX_DIR_FILES = 200
MAX_ITEMS = 2000
TRASH_DAYS = 7
HARNESSES = ("claude", "codex", "opencode")
LABEL_HARNESS = {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode"}


class MemoryError_(Exception):
    """reason: unknown_source | unknown_item | changed | gone | symlink | too_large | not_found | exists | io."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def tilde(p: str) -> str:
    h = os.path.expanduser("~")
    return "~" + p[len(h):] if p == h or p.startswith(h.rstrip("/") + "/") else p


def slug(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def git_root(d: str) -> str | None:
    cur = os.path.realpath(d)
    while True:
        if os.path.lexists(os.path.join(cur, ".git")):
            return cur
        up = os.path.dirname(cur)
        if up == cur:
            return None
        cur = up


def _claude_dir() -> str:
    return os.path.realpath(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"))


def _codex_dir() -> str:
    return os.path.realpath(os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"))


def _xdg_config() -> str:
    return os.path.realpath(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"))


def source_list(kind: str, workdir: str) -> list[dict]:
    """[{id, label, base, rel, kind: file|dir}] — the only places looked at. `base` is a real path; `rel` must not cross a
    symlink."""
    w = os.path.realpath(workdir)
    out: list[dict] = []

    def f(sid, label, base, rel):
        out.append({"id": sid, "label": label, "base": base, "rel": rel, "kind": "file"})

    def d(sid, label, base, rel):
        out.append({"id": sid, "label": label, "base": base, "rel": rel, "kind": "dir"})

    if kind == "claude":
        c = _claude_dir()
        f("claude.project", "项目记忆 CLAUDE.md", w, "CLAUDE.md")
        f("claude.local", "本机项目记忆 CLAUDE.local.md", w, "CLAUDE.local.md")
        f("claude.dot", "项目记忆 .claude/CLAUDE.md", w, ".claude/CLAUDE.md")
        f("claude.user", "你的全局记忆 ~/.claude/CLAUDE.md", c, "CLAUDE.md")
        d("claude.auto", "自动记忆（Claude 自己记下的）", c, "projects/" + slug(git_root(w) or w) + "/memory")
    elif kind == "codex":
        c = _codex_dir()
        f("codex.user", "你的全局记忆 ~/.codex/AGENTS.md", c, "AGENTS.md")
        f("codex.user_override", "全局覆盖 ~/.codex/AGENTS.override.md", c, "AGENTS.override.md")
        f("codex.project", "项目记忆 AGENTS.md", w, "AGENTS.md")
        f("codex.project_override", "项目覆盖 AGENTS.override.md", w, "AGENTS.override.md")
        d("codex.memories", "Codex 记忆（memories 功能）", c, "memories")
    elif kind == "opencode":
        x = _xdg_config()
        proj = os.path.lexists(os.path.join(w, "AGENTS.md"))
        glob_ = os.path.lexists(os.path.join(x, "opencode", "AGENTS.md"))
        f("opencode.project", "项目记忆 AGENTS.md", w, "AGENTS.md")
        f("opencode.user", "你的全局记忆 ~/.config/opencode/AGENTS.md", x, "opencode/AGENTS.md")
        if not proj:
            f("opencode.project_claude", "项目记忆 CLAUDE.md（OpenCode 兼容读取）", w, "CLAUDE.md")
        if not glob_:
            f("opencode.user_claude", "全局 ~/.claude/CLAUDE.md（OpenCode 兼容读取）", _claude_dir(), "CLAUDE.md")
    return out


# ------------------------------------------------------------------ safe file access (no symlinks, bounded)
def _no_link(base: str, rel: str, missing_ok: bool = False) -> str:
    """base/rel with no symlink in any component of rel (base itself is a resolved real path). missing_ok: the last
    component may not exist (restoring a deleted file)."""
    cur = base
    parts = [p for p in rel.split("/") if p]
    for i, part in enumerate(parts):
        if part in (".", ".."):
            raise MemoryError_("symlink")
        cur = os.path.join(cur, part)
        try:
            if stat.S_ISLNK(os.lstat(cur).st_mode):
                raise MemoryError_("symlink")
        except FileNotFoundError:
            if missing_ok and i == len(parts) - 1:
                return cur
            raise MemoryError_("not_found") from None
    return cur


def check_data_path(path):
    """Use the current host credential inventory and its symlink/hardlink identity check, before data access."""
    from types import SimpleNamespace
    from . import preferences, uv_list
    try:
        _, prefs = preferences.read()
        protected = uv_list.credential_reads("Read", {"file_path": os.fspath(path)},
                                             host=SimpleNamespace(preferences=prefs))
    except Exception:
        raise MemoryError_("credential_inventory_unavailable") from None
    if protected:
        raise MemoryError_("credential_path")


def _check_read_fd(path, fd, before, *, private=False):
    opened = os.fstat(fd)
    if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
        raise MemoryError_("io")
    if private and (opened.st_uid != os.geteuid() or stat.S_IMODE(opened.st_mode) != 0o600):
        raise MemoryError_("unsafe_archive")
    if not os.path.samestat(before, opened):
        raise MemoryError_("changed")
    # The two supported systems expose the open descriptor here. The inventory
    # compares its device/inode as well as the logical source pathname.
    check_data_path(f"/dev/fd/{fd}")
    check_data_path(path)
    try:
        current = os.stat(path, follow_symlinks=False)
    except OSError:
        raise MemoryError_("changed") from None
    if not os.path.samestat(opened, current):
        raise MemoryError_("changed")
    return opened


def read_file(path: str, *, limit: int = MAX_FILE, private: bool = False) -> bytes:
    check_data_path(path)
    try:
        before = os.stat(path, follow_symlinks=False)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        raise MemoryError_("not_found") from None
    except OSError as e:
        raise MemoryError_("symlink" if getattr(e, "errno", 0) in (40, 62) else "io") from None
    try:
        s = _check_read_fd(path, fd, before, private=private)
        if s.st_size > limit:
            raise MemoryError_("too_large")
        data = b""
        while len(data) <= limit:
            chunk = os.read(fd, min(65536, limit + 1 - len(data)))
            if not chunk:
                break
            data += chunk
        if len(data) > limit:
            raise MemoryError_("too_large")
        return data
    finally:
        os.close(fd)


def _write_atomic(path: str, data: bytes, expect_sha: str | None, *, create_mode=0o644) -> None:
    """Replace path with data, but only if it still has expect_sha (re-read right before the rename)."""
    check_data_path(path)
    try:
        mode = stat.S_IMODE(os.lstat(path).st_mode)
    except FileNotFoundError:
        mode = create_mode
    tmp = os.path.join(os.path.dirname(path), f".{os.path.basename(path)}.aj-{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.write(fd, data)
        diskq.fsync(fd)   # P142: never on the event loop
        os.fchmod(fd, mode)
    finally:
        os.close(fd)
    try:
        if expect_sha is not None and sha(read_file(path)) != expect_sha:
            raise MemoryError_("changed")
        check_data_path(path)
        if expect_sha is None:
            os.link(tmp, path, follow_symlinks=False)
            os.unlink(tmp)
        else:
            os.replace(tmp, path)
        diskq.settle(path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ------------------------------------------------------------------ Markdown → items
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_HEAD = re.compile(r"^\s{0,3}#{1,6}(\s|$)")
_LIST = re.compile(r"^\s*([-*+]|\d{1,9}[.)])\s+\S")


def split_items(text: str) -> list[dict]:
    """[{start, end, text, kind}] line ranges (end exclusive) of the items of a Markdown text."""
    lines = text.split("\n")
    items: list[dict] = []
    i, n = 0, len(lines)
    if n and lines[0].strip() == "---":                      # front matter: not an item
        for j in range(1, min(n, 200)):
            if lines[j].strip() in ("---", "..."):
                i = j + 1
                break
    cur: dict | None = None

    def close():
        nonlocal cur
        if cur:
            cur["text"] = "\n".join(lines[cur["start"]:cur["end"]])
            items.append(cur)
            cur = None

    while i < n:
        ln = lines[i]
        if not ln.strip():
            close()
            i += 1
            continue
        m = _FENCE.match(ln)
        if m:
            close()
            j = i + 1
            while j < n and not lines[j].lstrip().startswith(m.group(1)[:3]):
                j += 1
            cur = {"start": i, "end": min(j + 1, n), "kind": "code"}
            close()
            i = min(j + 1, n)
            continue
        if _HEAD.match(ln):
            close()
            cur = {"start": i, "end": i + 1, "kind": "heading"}
            close()
            i += 1
            continue
        if _LIST.match(ln):
            close()
            cur = {"start": i, "end": i + 1, "kind": "list"}
            i += 1
            while i < n and lines[i].strip() and not _LIST.match(lines[i]) and not _HEAD.match(lines[i]) \
                    and not _FENCE.match(lines[i]) and (lines[i].startswith(("  ", "\t"))):
                cur["end"] = i + 1
                i += 1
            close()
            continue
        if cur is None or cur["kind"] != "para":
            close()
            cur = {"start": i, "end": i + 1, "kind": "para"}
        else:
            cur["end"] = i + 1
        i += 1
    close()
    seen: dict[str, int] = {}
    for it in items[:MAX_ITEMS]:
        h = sha(it["text"].encode())[:16]
        k = seen.get(h, 0)
        seen[h] = k + 1
        it["iid"] = h if k == 0 else f"{h}~{k}"
    return items[:MAX_ITEMS]


def _front(text: str) -> tuple[dict, str]:
    """(front matter keys we show, body) of a memory file."""
    lines = text.split("\n")
    meta: dict = {}
    if lines and lines[0].strip() == "---":
        for j in range(1, min(len(lines), 200)):
            if lines[j].strip() in ("---", "..."):
                for ln in lines[1:j]:
                    m = re.match(r"^(name|description):\s*(.*)$", ln)
                    if m:
                        meta[m.group(1)] = m.group(2).strip().strip('"')
                return meta, "\n".join(lines[j + 1:]).strip("\n")
    return meta, text


# ------------------------------------------------------------------ scan
def scan(kind: str | None, workdir: str | None) -> dict:
    """{"harness", "sources": [{id, label, path, kind, problem?, files: [{file, fsha, items: [{iid, text, kind, title?}]}]}]}"""
    res = {"harness": kind, "sources": []}
    if kind not in HARNESSES or not workdir:
        return res
    total = 0
    for s in source_list(kind, workdir):
        path = os.path.join(s["base"], s["rel"])
        src = {"id": s["id"], "label": s["label"], "path": tilde(path), "kind": s["kind"], "files": []}
        res["sources"].append(src)
        try:
            p = _no_link(s["base"], s["rel"])
        except MemoryError_ as e:
            src["problem"] = e.reason
            continue
        if s["kind"] == "file":
            names = [""]
        else:
            if not os.path.isdir(p):
                src["problem"] = "not_found"
                continue
            try:
                names = sorted(n for n in os.listdir(p) if n.endswith(".md") and not n.startswith("."))
            except OSError:
                src["problem"] = "io"
                continue
            if len(names) > MAX_DIR_FILES:
                src["problem"] = "too_many_files"
                names = names[:MAX_DIR_FILES]
            names.sort(key=lambda n: (n != "MEMORY.md", n))
        for name in names:
            fp = os.path.join(p, name) if name else p
            try:
                if name and stat.S_ISLNK(os.lstat(fp).st_mode):
                    src["files"].append({"file": name, "problem": "symlink", "items": []})
                    continue
                data = read_file(fp)
            except MemoryError_ as e:
                if e.reason != "not_found" or name:
                    src["files"].append({"file": name, "problem": e.reason, "items": []})
                elif not name:
                    src["problem"] = "not_found"
                continue
            if total + len(data) > MAX_TOTAL:
                src["files"].append({"file": name, "problem": "total_limit", "items": []})
                continue
            total += len(data)
            text = data.decode("utf-8", "replace")
            ent = {"file": name, "fsha": sha(data), "items": []}
            if s["kind"] == "dir" and name != "MEMORY.md":       # a memory file = one item
                meta, body = _front(text)
                ent["items"].append({"iid": "file", "text": body, "kind": "file",
                                     "title": meta.get("name") or name[:-3], **({"desc": meta["description"]} if meta.get("description") else {})})
            else:
                ent["items"] = [{"iid": it["iid"], "text": it["text"], "kind": it["kind"]} for it in split_items(text)]
            src["files"].append(ent)
    return res


def _resolve(kind: str, workdir: str, src: str, file: str, missing_ok: bool = False) -> tuple[dict, str]:
    for s in source_list(kind, workdir):
        if s["id"] == src:
            break
    else:
        raise MemoryError_("unknown_source")
    if s["kind"] == "file":
        if file:
            raise MemoryError_("unknown_item")
        return s, _no_link(s["base"], s["rel"], missing_ok)
    if not re.fullmatch(r"[^/\\\x00]{1,200}\.md", file or "") or file.startswith("."):
        raise MemoryError_("unknown_item")
    d = _no_link(s["base"], s["rel"])
    return s, _no_link(d, file, missing_ok)


# ------------------------------------------------------------------ trash
def _trash_dir(st):
    return st.root / "memory_trash"


@contextmanager
def _archive_dir(st, create=False):
    check_data_path(_trash_dir(st))
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        parent = os.open(st.root, flags)
        try:
            owner = os.fstat(parent)
            if owner.st_uid != os.geteuid() or owner.st_mode & 0o022:
                raise MemoryError_("unsafe_archive")
            if create:
                try:
                    os.mkdir("memory_trash", 0o700, dir_fd=parent)
                except FileExistsError:
                    pass
            fd = os.open("memory_trash", flags, dir_fd=parent)
        finally:
            os.close(parent)
    except FileNotFoundError:
        raise MemoryError_("not_found") from None
    except OSError:
        raise MemoryError_("unsafe_archive") from None
    try:
        directory = os.fstat(fd)
        if directory.st_uid != os.geteuid() or stat.S_IMODE(directory.st_mode) != 0o700:
            raise MemoryError_("unsafe_archive")
        yield fd
    finally:
        os.close(fd)


def _archive_read(st, directory, name):
    if not re.fullmatch(r"[0-9a-f]{16}\.json", name):
        raise MemoryError_("not_found")
    path = _trash_dir(st) / name
    check_data_path(path)
    before = os.stat(name, dir_fd=directory, follow_symlinks=False)
    fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
    with os.fdopen(fd, "rb") as stream:
        opened = _check_read_fd(path, stream.fileno(), before, private=True)
        if opened.st_size > MAX_TOTAL * 4:
            raise MemoryError_("too_large")
        record = json.loads(stream.read(MAX_TOTAL * 4 + 1))
    if not isinstance(record, dict) or record.get("id") != name[:-5] or not isinstance(record.get("path"), str):
        raise MemoryError_("unknown_item")
    check_data_path(record["path"])
    return record, opened


def _forget(directory, name, identity):
    if os.path.samestat(identity, os.stat(name, dir_fd=directory, follow_symlinks=False)):
        os.unlink(name, dir_fd=directory)


def _put_trash(st, rec: dict) -> str:
    check_data_path(rec["path"])
    if rec.get("index"):
        check_data_path(os.path.join(os.path.dirname(rec["path"]), "MEMORY.md"))
    tid = secrets.token_hex(8)
    record = {"v": 1, "id": tid, "ts": int(time.time()), **rec}
    with _archive_dir(st, create=True) as directory:
        temp = "." + secrets.token_hex(16)
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(json.dumps(record, ensure_ascii=False).encode())
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temp, tid + ".json", src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
        finally:
            os.unlink(temp, dir_fd=directory)
        os.fsync(directory)
    return tid


def _archive_list(st, now):
    out = []
    removed = 0
    try:
        with _archive_dir(st) as directory:
            for name in os.listdir(directory):
                try:
                    r, identity = _archive_read(st, directory, name)
                    if now is not None and not r.get("retained") and r.get("kind") != "tidy" and now - r["ts"] > TRASH_DAYS * 86400:
                        _forget(directory, name, identity)
                        removed += 1
                    else:
                        out.append(r)
                except (OSError, ValueError, KeyError, TypeError, MemoryError_):
                    continue
    except MemoryError_:
        pass
    return sorted(out, key=lambda r: r.get("ts", 0), reverse=True), removed


def purge_trash(st, now: float | None = None) -> int:
    return _archive_list(st, time.time() if now is None else now)[1]


def trash(st, purge: bool = True) -> list[dict]:
    return _archive_list(st, time.time() if purge else None)[0]


def preview(r: dict, n: int = 80) -> str:
    t = " ".join(str(r.get("text", "")).split())
    return t if len(t) <= n else t[: n - 1] + "…"


# ------------------------------------------------------------------ remove / restore
def remove(st, kind: str, workdir: str, src: str, file: str, fsha: str, iid: str, *, retain: bool = False) -> dict:
    """Remove one item if the file still has `fsha`. Returns the trash record (with `id`). Raises MemoryError_."""
    s, path = _resolve(kind, workdir, src, file)
    data = read_file(path)
    if sha(data) != fsha:
        raise MemoryError_("changed")
    text = data.decode("utf-8", "replace")
    if data != text.encode():
        raise MemoryError_("io")                     # not UTF-8: never rewrite it
    label = s["label"] + (f" · {file}" if file else "")
    if s["kind"] == "dir" and file != "MEMORY.md":
        if iid != "file":
            raise MemoryError_("unknown_item")
        idx_lines = []
        idx_path = os.path.join(os.path.dirname(path), "MEMORY.md")
        idx_new = None
        try:
            idx = read_file(idx_path).decode("utf-8")
            if retain:
                from .privacy import owner_text
                if owner_text(idx, mask_access_codes=True) != idx:
                    raise MemoryError_("sensitive_index")
            lines = idx.split("\n")
            keep = []
            for i, ln in enumerate(lines):
                if re.search(r"\]\(\s*(\./)?" + re.escape(file) + r"\s*\)", ln):
                    idx_lines.append({"at": i, "text": ln})
                else:
                    keep.append(ln)
            if idx_lines:
                idx_new = ("\n".join(keep), sha(idx.encode()))
        except MemoryError_ as e:
            if retain and e.reason != "not_found":
                raise
        except UnicodeDecodeError:
            if retain:
                raise MemoryError_("io") from None
        rec = {"harness": kind, "workdir": os.path.realpath(workdir), "src": src, "label": label, "path": path, "file": file,
               "kind": "file", "text": text, "mode": stat.S_IMODE(os.lstat(path).st_mode), "index": idx_lines, **({"retained": True} if retain else {})}
        if sha(read_file(path)) != fsha:
            raise MemoryError_("changed")
        tid = _put_trash(st, rec)
        check_data_path(path)
        os.unlink(path)
        if idx_new:
            try:
                _write_atomic(idx_path, idx_new[0].encode(), idx_new[1])
            except MemoryError_:
                pass
        return {**rec, "id": tid}
    items = split_items(text)
    it = next((x for x in items if x["iid"] == iid), None)
    if it is None:
        raise MemoryError_("unknown_item")
    lines = text.split("\n")
    start, end = it["start"], it["end"]
    # keep the layout tidy: an item between two blank lines takes one of them with it
    if start > 0 and not lines[start - 1].strip() and (end >= len(lines) or not lines[end].strip()):
        start -= 1
    removed = lines[start:end]
    new_lines = lines[:start] + lines[end:]
    rec = {"harness": kind, "workdir": os.path.realpath(workdir), "src": src, "label": label, "path": path, "file": file,
           "kind": "item", "text": it["text"], "lines": removed, "at": start, "before": lines[max(0, start - 3):start],
           "after": lines[end:end + 3]}
    tid = _put_trash(st, rec)
    try:
        _write_atomic(path, "\n".join(new_lines).encode(), fsha)
    except BaseException:
        try:
            with _archive_dir(st) as directory:
                name = tid + ".json"
                _, identity = _archive_read(st, directory, name)
                _forget(directory, name, identity)
        except (OSError, ValueError, MemoryError_):
            pass
        raise
    return {**rec, "id": tid}


def restore(st, tid: str) -> dict:
    """Put a trashed item back where it was (next to the lines that surrounded it), then forget the trash entry."""
    if not re.fullmatch(r"[0-9a-f]{16}", tid or ""):
        raise MemoryError_("not_found")
    with _archive_dir(st) as directory:
        name = tid + ".json"
        try:
            r, identity = _archive_read(st, directory, name)
        except (OSError, ValueError):
            raise MemoryError_("not_found") from None
        _restore_data(st, r)
        _forget(directory, name, identity)
        return r


def _restore_data(st, r):
    if r.get("index"):
        check_data_path(os.path.join(os.path.dirname(r["path"]), "MEMORY.md"))
    if r.get("kind") == "tidy":
        from . import memory_tidy
        return memory_tidy.restore(st, r)
    # the path is re-derived from the source list (never trusted from the file alone)
    s, path = _resolve(r["harness"], r["workdir"], r["src"], r.get("file", ""), missing_ok=True)
    if os.path.realpath(path) != os.path.realpath(r["path"]):
        raise MemoryError_("unknown_source")
    if r["kind"] == "file":
        try:
            cur = read_file(path)
            if cur.decode("utf-8", "replace") != r["text"]:
                raise MemoryError_("exists")
        except MemoryError_ as e:
            if e.reason != "not_found":
                raise
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), r.get("mode", 0o644) & 0o777)
            try:
                os.write(fd, r["text"].encode())
            finally:
                os.close(fd)
        if r.get("index"):
            idx_path = os.path.join(os.path.dirname(path), "MEMORY.md")
            try:
                raw = read_file(idx_path)
                lines = raw.decode("utf-8").split("\n")
                h = sha(raw)
            except (MemoryError_, UnicodeDecodeError):
                lines, h = None, None
            if lines is not None:
                for e in r["index"]:
                    if e["text"] not in lines:
                        lines.insert(min(e["at"], len(lines)), e["text"])
                _write_atomic(idx_path, "\n".join(lines).encode(), h)
    else:
        try:
            raw = read_file(path)
            text = raw.decode("utf-8")
            h = sha(raw)
        except MemoryError_ as e:
            if e.reason != "not_found":
                raise
            text, h = "", None
        lines = text.split("\n") if text else []
        at = min(r.get("at", len(lines)), len(lines))
        before, after = r.get("before") or [], r.get("after") or []
        anchor = None
        if before and before[-1].strip():
            cands = [i + 1 for i, ln in enumerate(lines) if ln == before[-1]]
            anchor = min(cands, key=lambda i: abs(i - at)) if cands else None
        if anchor is None and after and after[0].strip():
            cands = [i for i, ln in enumerate(lines) if ln == after[0]]
            anchor = min(cands, key=lambda i: abs(i - at)) if cands else None
        at = at if anchor is None else anchor
        lines[at:at] = r["lines"]
        if h is None:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
            try:
                os.write(fd, "\n".join(lines).encode())
            finally:
                os.close(fd)
        else:
            _write_atomic(path, "\n".join(lines).encode(), h)
    return r
