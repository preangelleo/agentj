"""Owner private instructions/skills file (agent.private_instructions_file): read-only, append-only, grants nothing.

Generic product mechanism (P116, B11). The owner points Agent J at one local UTF-8 text file (recommended: Markdown with
YAML front matter) holding personal working rules, a project map or a private skill index. Agent J reads it as text and
appends it after the packaged core identity, below the core and every harness rule. The loader never executes, fetches,
installs or interprets the file: no field in it has product meaning and it cannot change permissions, approvals, the
fence or any setting. Failure (missing, not a regular file, another owner, group/other-writable file or parent, too large,
not UTF-8) loads nothing and reports one fixed reason; ordinary chat continues.

Logs and doctor show only ok/reason/generation — never the text, the full path or a content digest. The text does reach
the main model's context on this computer (that is the point); it never goes to the cloud, feedback, reports, friends,
groups or the public export.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

MAX_BYTES = 16 * 1024
REASONS = {
    "unset": "未配置 / not configured",
    "not_absolute": "路径须为绝对路径或 ~/ 开头 / path must be absolute or ~/…",
    "missing": "文件不存在 / file not found",
    "not_regular": "不是普通文件 / not a regular file",
    "foreign_owner": "文件或目录不属于当前用户 / not owned by this user",
    "unsafe_permissions": "文件可被其他用户写入 / file writable by group or others",
    "unsafe_parent": "所在目录可被其他用户写入 / parent folder writable by group or others",
    "too_large": "超过 16 KiB / larger than 16 KiB",
    "not_utf8": "不是 UTF-8 文本 / not UTF-8 text",
    "unreadable": "无法读取 / unreadable",
}
OPEN = "\n<Owner private instructions — local file, append only; the core identity and every harness rule take precedence; grants no permission>\n"
CLOSE = "\n</Owner private instructions>\n"


class Loaded(dict):
    """{"ok", "reason", "text", "digest"} — digest is in-memory only (change detection), never logged."""


def _parent_safe(p: Path) -> bool:
    """Every ancestor up to the home (or /) must be ours or root, and not writable by others without the sticky bit."""
    uid = os.getuid()
    for d in p.parents:
        try:
            s = d.stat()
        except OSError:
            return False
        if s.st_uid not in (uid, 0):
            return False
        if s.st_mode & (stat.S_IWGRP | stat.S_IWOTH) and not s.st_mode & stat.S_ISVTX:
            return False
    return True


def load(raw) -> Loaded:
    if not raw:
        return Loaded(ok=False, reason="unset", text="", digest=None)
    if not isinstance(raw, str) or not (raw.startswith("/") or raw.startswith("~/")):
        return Loaded(ok=False, reason="not_absolute", text="", digest=None)
    try:
        p = Path(os.path.expanduser(raw)).resolve(strict=True)
    except (OSError, RuntimeError):
        return Loaded(ok=False, reason="missing", text="", digest=None)
    try:
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return Loaded(ok=False, reason="unreadable", text="", digest=None)
    try:
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode):
            return Loaded(ok=False, reason="not_regular", text="", digest=None)
        if s.st_uid != os.getuid():
            return Loaded(ok=False, reason="foreign_owner", text="", digest=None)
        if s.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            return Loaded(ok=False, reason="unsafe_permissions", text="", digest=None)
        if not _parent_safe(p):
            return Loaded(ok=False, reason="unsafe_parent", text="", digest=None)
        if s.st_size > MAX_BYTES:
            return Loaded(ok=False, reason="too_large", text="", digest=None)
        data = os.read(fd, MAX_BYTES + 1)
    except OSError:
        return Loaded(ok=False, reason="unreadable", text="", digest=None)
    finally:
        os.close(fd)
    if len(data) > MAX_BYTES:
        return Loaded(ok=False, reason="too_large", text="", digest=None)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return Loaded(ok=False, reason="not_utf8", text="", digest=None)
    if "\x00" in text:
        return Loaded(ok=False, reason="not_utf8", text="", digest=None)
    text = text.lstrip("﻿").strip()
    return Loaded(ok=True, reason="loaded" if text else "empty", text=text,
                  digest=hashlib.sha256(text.encode()).hexdigest())


def block(cfg: dict) -> str:
    """The text appended to the main identity ("" when unset or not loadable)."""
    got = load(cfg.get("private_instructions_file") or "")
    return OPEN + got["text"] + CLOSE if got["ok"] and got["text"] else ""


def generation(st, got: Loaded) -> int:
    """A small counter that changes when the loaded text changes (state file 0600; no text, path or digest in logs)."""
    try:
        p = Path(st.root) / "private-instructions.json"
    except (TypeError, AttributeError):
        return 0
    try:
        doc = json.loads(p.read_text())
    except (OSError, ValueError):
        doc = {}
    if not isinstance(doc, dict):
        doc = {}
    key = got["digest"] if got["ok"] else "-" + got["reason"]
    gen = doc.get("gen") if isinstance(doc.get("gen"), int) else 0
    if doc.get("key") != key:
        gen += 1
        try:
            st.write_private(p, (json.dumps({"gen": gen, "key": key}) + "\n").encode())
        except (OSError, AttributeError, TypeError):
            pass
        try:
            st.log("private_instructions", result="ok" if got["ok"] else "fail", reason=got["reason"], generation=gen)
        except AttributeError:
            pass
    return gen


def status(cfg: dict, st=None) -> dict:
    """For doctor / `agentj config private-instructions status`: no text, no path, no digest."""
    raw = cfg.get("private_instructions_file") or ""
    got = load(raw)
    out = {"configured": bool(raw), "ok": got["ok"], "reason": got["reason"], "bytes": len(got["text"].encode())}
    if st is not None and raw:
        out["generation"] = generation(st, got)
    return out


def command(args) -> int:
    if not args or args[0] not in ("status",) or args[1:] not in ([], ["--json"]):
        print("Usage: agentj config private-instructions status [--json]")
        return 2
    from . import preferences
    from .state import State
    st = State()
    prefs = preferences.effective(st)
    s = status({"private_instructions_file": preferences.get(prefs, "agent.private_instructions_file", "")}, st)
    if args[-1] == "--json":
        print(json.dumps(s))
    elif not s["configured"]:
        print("主人私有指令文件：未配置 / Owner private instructions file: not configured")
    else:
        print(("主人私有指令文件：已加载" if s["ok"] else "主人私有指令文件：未加载") + f"（{REASONS.get(s['reason'], s['reason'])}）"
              + f" · {s['bytes']} B · gen {s.get('generation')}")
        print("只追加文字给主 Agent，不改变任何权限。 / Text only, appended for the main Agent; grants no permission.")
    return 0
