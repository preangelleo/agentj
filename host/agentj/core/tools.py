"""The seven coding tools (AGENTJ-CORE-SPEC 2.2 `core/tools`), ported from Pi coding-agent `src/core/tools/*.ts`
(earendil-works/pi 8efd314f, MIT): descriptions, parameters, truncation limits (2000 lines / 50 KB) and the multi-edit
exact-replacement `edit` are Pi's. Run only inside the toolbox process (toolbox.py), which runs inside the fence.

Every path is resolved against the session root and must stay under it (symlinks resolved first): the fence hides Agent J's
data, this keeps a session inside its own project. bash: `bash -c` in the root, stdin closed (no interactive hang), its own
process group killed on timeout, output = the last lines. Standard library (`rg` used when present).
"""
from __future__ import annotations

import fnmatch
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

MAX_LINES = 2000
MAX_BYTES = 50 * 1024
GREP_LINE = 500
BASH_TIMEOUT = 120
BASH_MAX = 1800


def _fn(name: str, desc: str, props: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": desc,
                                             "parameters": {"type": "object", "properties": props, "required": required}}}


_S, _N, _B = {"type": "string"}, {"type": "number"}, {"type": "boolean"}


def _d(t: dict, desc: str) -> dict:
    return {**t, "description": desc}


SPECS = {
    "read": _fn("read", f"Read a text file. Output is truncated to {MAX_LINES} lines or {MAX_BYTES // 1024}KB; use offset/limit "
                        "for large files and continue with offset until complete.",
                {"path": _d(_S, "Path (relative to the project or absolute)"), "offset": _d(_N, "1-indexed start line"),
                 "limit": _d(_N, "Max lines")}, ["path"]),
    "write": _fn("write", "Write a file (created or overwritten; parent folders created). Only for new files or full rewrites.",
                 {"path": _S, "content": _S}, ["path", "content"]),
    "edit": _fn("edit", "Edit one file by exact text replacement. Every edits[].oldText must match a unique, non-overlapping "
                        "region of the original file; merge nearby changes into one edit.",
                {"path": _S, "edits": {"type": "array", "items": {"type": "object", "properties": {"oldText": _S, "newText": _S},
                                                                  "required": ["oldText", "newText"]}}}, ["path", "edits"]),
    "bash": _fn("bash", f"Run a bash command in the project folder; stdout+stderr, last {MAX_LINES} lines / "
                        f"{MAX_BYTES // 1024}KB. No stdin. Default timeout {BASH_TIMEOUT}s.",
                {"command": _S, "timeout": _d(_N, "Seconds")}, ["command"]),
    "grep": _fn("grep", "Search file contents (regex; literal=true for plain text). Returns path:line: text, max 100 matches.",
                {"pattern": _S, "path": _S, "glob": _d(_S, "e.g. '*.py'"), "ignoreCase": _B, "literal": _B}, ["pattern"]),
    "find": _fn("find", "Find files by glob pattern, e.g. '**/*.py'. Paths relative to the search folder, max 1000.",
                {"pattern": _S, "path": _S}, ["pattern"]),
    "ls": _fn("ls", "List a folder (sorted, '/' after folders, dotfiles included), max 500 entries.", {"path": _S}, []),
}
READ_ONLY = ("read", "grep", "find", "ls")
WRITES = ("write", "edit")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache"}


class ToolError(Exception):
    pass


def resolve(root: str, p: str | None) -> Path:
    base = Path(root).resolve()
    q = Path(os.path.expanduser(p or ".")) if p else base
    q = (q if q.is_absolute() else base / q).resolve()
    if q != base and base not in q.parents:
        raise ToolError(f"{p} is outside the project folder {base}; only paths inside it can be used.")
    return q


def _head(text: str) -> tuple[str, bool]:
    lines = text.split("\n")
    cut = False
    if len(lines) > MAX_LINES:
        lines, cut = lines[:MAX_LINES], True
    out = "\n".join(lines)
    if len(out.encode()) > MAX_BYTES:
        out, cut = out.encode()[:MAX_BYTES].decode("utf-8", "ignore"), True
    return out, cut


def _tail(text: str) -> tuple[str, bool]:
    lines = text.split("\n")
    cut = False
    if len(lines) > MAX_LINES:
        lines, cut = lines[-MAX_LINES:], True
    out = "\n".join(lines)
    b = out.encode()
    if len(b) > MAX_BYTES:
        out, cut = b[-MAX_BYTES:].decode("utf-8", "ignore"), True
    return out, cut


def t_read(root, path, offset=None, limit=None, **_):
    f = resolve(root, path)
    if not f.is_file():
        raise ToolError(f"No such file: {path}")
    text = f.read_text(encoding="utf-8", errors="replace")
    lines = text.split("\n")
    start = max(1, int(offset or 1))
    if start > len(lines):
        raise ToolError(f"offset {start} is past the end ({len(lines)} lines)")
    end = len(lines) if not limit else min(len(lines), start - 1 + max(1, int(limit)))
    out, cut = _head("\n".join(lines[start - 1:end]))
    shown = start - 1 + out.count("\n") + 1
    if cut or end < len(lines):
        out += f"\n\n[Showing lines {start}-{shown} of {len(lines)}. Use offset={shown + 1} to continue.]"
    return out


def t_write(root, path, content, **_):
    f = resolve(root, path)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content, encoding="utf-8")
    return f"Wrote {len(content.encode())} bytes to {path}"


def t_edit(root, path, edits, **_):
    f = resolve(root, path)
    if not f.is_file():
        raise ToolError(f"No such file: {path}")
    if not isinstance(edits, list) or not edits:
        raise ToolError("edits must be a non-empty list of {oldText, newText}")
    text = f.read_text(encoding="utf-8")
    spans = []
    for i, e in enumerate(edits):
        old, new = (e or {}).get("oldText"), (e or {}).get("newText")
        if not isinstance(old, str) or not isinstance(new, str) or not old:
            raise ToolError(f"edits[{i}] needs a non-empty oldText and a newText")
        n = text.count(old)
        if n == 0:
            raise ToolError(f"edits[{i}].oldText was not found in {path}. Read the file again and copy the exact text "
                            "(whitespace and indentation included).")
        if n > 1:
            raise ToolError(f"edits[{i}].oldText matches {n} places in {path}; add surrounding lines so it is unique.")
        s = text.index(old)
        spans.append((s, s + len(old), new, i))
    spans.sort()
    for a, b in zip(spans, spans[1:]):
        if b[0] < a[1]:
            raise ToolError(f"edits[{a[3]}] and edits[{b[3]}] overlap; merge them into one edit.")
    for s, e, new, _ in reversed(spans):
        text = text[:s] + new + text[e:]
    f.write_text(text, encoding="utf-8")
    return f"Edited {path}: {len(spans)} replacement(s)"


def t_bash(root, command, timeout=None, **_):
    t = min(BASH_MAX, max(1, int(timeout or BASH_TIMEOUT)))
    t0 = time.monotonic()
    p = subprocess.Popen(["bash", "-c", command], cwd=str(Path(root).resolve()), stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        out, _ = p.communicate(timeout=t)
        code = p.returncode
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        out, _ = p.communicate()
        code = None
    text, cut = _tail(out.decode("utf-8", "replace"))
    head = f"[exit {code}]" if code is not None else f"[timed out after {t}s, process group killed]"
    return head + f" ({time.monotonic() - t0:.1f}s)" + (" [output truncated: last lines]" if cut else "") + "\n" + text


def _walk(base: Path):
    for dp, dns, fns in os.walk(base):
        dns[:] = sorted(d for d in dns if d not in SKIP_DIRS)
        for fn in sorted(fns):
            yield Path(dp) / fn


def t_grep(root, pattern, path=None, glob=None, ignoreCase=False, literal=False, **_):
    base = resolve(root, path)
    rg = shutil.which("rg")
    if rg:
        argv = [rg, "-n", "--no-heading", "--color", "never", "-m", "100", "--max-columns", str(GREP_LINE)]
        argv += ["-i"] if ignoreCase else []
        argv += ["-F"] if literal else []
        argv += ["-g", glob] if glob else []
        r = subprocess.run(argv + ["-e", pattern, str(base)], capture_output=True, text=True, timeout=60,
                           stdin=subprocess.DEVNULL)
        lines = r.stdout.splitlines()[:100]
    else:
        rx = re.compile(re.escape(pattern) if literal else pattern, re.I if ignoreCase else 0)
        lines = []
        from ..danger import cred_path
        for f in ([base] if base.is_file() else _walk(base)):
            if glob and not fnmatch.fnmatch(f.name, glob):
                continue
            if cred_path(str(f)):
                lines.append(f"{f}:0:(credential file)")
                continue
            try:
                for n, line in enumerate(f.read_text(encoding="utf-8", errors="strict").split("\n"), 1):
                    if rx.search(line):
                        lines.append(f"{f}:{n}:{line[:GREP_LINE]}")
            except (UnicodeDecodeError, OSError):
                continue
            if len(lines) >= 100:
                break
    rb = str(Path(root).resolve()) + os.sep
    from ..danger import cred_path
    kept, hidden = [], 0
    for x in lines[:100]:
        if cred_path(x.split(":", 1)[0]):        # P359 review M4: a credential file's lines never come back from grep
            hidden += 1
            continue
        kept.append(x[len(rb):] if x.startswith(rb) else x)
    out, _ = _head("\n".join(kept))
    if hidden:
        out += f"\n[{hidden} line(s) from credential files withheld; use read on that file (it asks the owner)]"
    return out or "No matches"


def t_find(root, pattern, path=None, **_):
    base = resolve(root, path)
    pat = pattern[3:] if pattern.startswith("**/") else pattern
    hits = []
    for f in _walk(base):
        rel = str(f.relative_to(base))
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(f.name, pat) or fnmatch.fnmatch(rel, pat):
            hits.append(rel)
            if len(hits) >= 1000:
                break
    out, _ = _head("\n".join(hits))
    return out or "No files found"


def t_ls(root, path=None, **_):
    d = resolve(root, path)
    if not d.is_dir():
        raise ToolError(f"Not a folder: {path}")
    names = sorted(x.name + ("/" if x.is_dir() else "") for x in d.iterdir())[:500]
    return "\n".join(names) or "(empty)"


RUN = {"read": t_read, "write": t_write, "edit": t_edit, "bash": t_bash, "grep": t_grep, "find": t_find, "ls": t_ls}


def run(root: str, name: str, args: dict) -> str:
    fn = RUN.get(name)
    if fn is None:
        raise ToolError(f"unknown tool {name}")
    if not isinstance(args, dict):
        raise ToolError("arguments must be an object")
    return fn(root, **args)
