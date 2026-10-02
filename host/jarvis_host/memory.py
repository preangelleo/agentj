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
the removed text goes to the trash `<state>/memory_trash/<id>.json` (0600) for 7 days — `jarvis memory restore <id>` or the
phone's 「撤销」 puts it back. Never follows a symlink (any component below the source's base, the file itself); one file
≤ 256 KiB, ≤ 200 files per folder, ≤ 2 MiB in total.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import time

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


def read_file(path: str) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        raise MemoryError_("not_found") from None
    except OSError as e:
        raise MemoryError_("symlink" if getattr(e, "errno", 0) in (40, 62) else "io") from None
    try:
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode):
            raise MemoryError_("io")
        if s.st_size > MAX_FILE:
            raise MemoryError_("too_large")
        data = b""
        while len(data) <= MAX_FILE:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            data += chunk
        if len(data) > MAX_FILE:
            raise MemoryError_("too_large")
        return data
    finally:
        os.close(fd)


def _write_atomic(path: str, data: bytes, expect_sha: str | None) -> None:
    """Replace path with data, but only if it still has expect_sha (re-read right before the rename)."""
    try:
        mode = stat.S_IMODE(os.lstat(path).st_mode)
    except FileNotFoundError:
        mode = 0o644
    tmp = os.path.join(os.path.dirname(path), f".{os.path.basename(path)}.aj-{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
        os.fchmod(fd, mode)
    finally:
        os.close(fd)
    try:
        if expect_sha is not None and sha(read_file(path)) != expect_sha:
            raise MemoryError_("changed")
        os.replace(tmp, path)
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


def _put_trash(st, rec: dict) -> str:
    d = _trash_dir(st)
    d.mkdir(mode=0o700, exist_ok=True)
    os.chmod(d, 0o700)
    tid = secrets.token_hex(8)
    rec = {"v": 1, "id": tid, "ts": int(time.time()), **rec}
    st.write_private(d / f"{tid}.json", json.dumps(rec, ensure_ascii=False).encode())
    return tid


def purge_trash(st, now: float | None = None) -> int:
    now = time.time() if now is None else now
    n = 0
    for r in trash(st, purge=False):
        if now - r.get("ts", 0) > TRASH_DAYS * 86400:
            try:
                os.unlink(_trash_dir(st) / f"{r['id']}.json")
                n += 1
            except OSError:
                pass
    return n


def trash(st, purge: bool = True) -> list[dict]:
    if purge:
        purge_trash(st)
    out = []
    try:
        names = os.listdir(_trash_dir(st))
    except OSError:
        return out
    for name in names:
        if not re.fullmatch(r"[0-9a-f]{16}\.json", name):
            continue
        try:
            r = json.loads((_trash_dir(st) / name).read_text())
        except (OSError, ValueError):
            continue
        if isinstance(r, dict):
            out.append(r)
    return sorted(out, key=lambda r: r.get("ts", 0), reverse=True)


def preview(r: dict, n: int = 80) -> str:
    t = " ".join(str(r.get("text", "")).split())
    return t if len(t) <= n else t[: n - 1] + "…"


# ------------------------------------------------------------------ remove / restore
def remove(st, kind: str, workdir: str, src: str, file: str, fsha: str, iid: str) -> dict:
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
            lines = idx.split("\n")
            keep = []
            for i, ln in enumerate(lines):
                if re.search(r"\]\(\s*(\./)?" + re.escape(file) + r"\s*\)", ln):
                    idx_lines.append({"at": i, "text": ln})
                else:
                    keep.append(ln)
            if idx_lines:
                idx_new = ("\n".join(keep), sha(idx.encode()))
        except (MemoryError_, UnicodeDecodeError):
            pass
        rec = {"harness": kind, "workdir": os.path.realpath(workdir), "src": src, "label": label, "path": path, "file": file,
               "kind": "file", "text": text, "mode": stat.S_IMODE(os.lstat(path).st_mode), "index": idx_lines}
        if sha(read_file(path)) != fsha:
            raise MemoryError_("changed")
        tid = _put_trash(st, rec)
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
            os.unlink(_trash_dir(st) / f"{tid}.json")
        except OSError:
            pass
        raise
    return {**rec, "id": tid}


def restore(st, tid: str) -> dict:
    """Put a trashed item back where it was (next to the lines that surrounded it), then forget the trash entry."""
    if not re.fullmatch(r"[0-9a-f]{16}", tid or ""):
        raise MemoryError_("not_found")
    tp = _trash_dir(st) / f"{tid}.json"
    try:
        r = json.loads(tp.read_text())
    except (OSError, ValueError):
        raise MemoryError_("not_found") from None
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
    try:
        os.unlink(tp)
    except OSError:
        pass
    return r
