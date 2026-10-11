"""P151: compaction / /clear handoff for the SHARED Claude session (the owner's own Claude Code; compactprep.py covers the
main Agent's own conversation).

Owner report (2026-10-12): after a compaction the session only learned about its handoff on the NEXT owner message (SessionStart
additionalContext); with no message it sat idle and looked like it had forgotten everything. Now, after PostCompact /
SessionStart(compact) — or SessionStart(clear) when a clear handoff exists — and RECOVER_DELAY without a new turn, the host
itself sends one 〔Agent J 系统提示〕 message into the session: read the handoff file, then carry on. The phone shows one line
(「压缩完成，Agent 正在恢复上下文」); the turn's reply reaches the phone as an ordinary Agent page.

Paths are fixed and human-readable — `~/.local/state/agentj/handoff/compact.md` / `clear.md` (no session id or hash: the
phone masks id-like strings and the owner could not copy the path). The shared session is the owner's own unfenced harness,
so it can read and write there. A clear handoff counts once: on delivery it is renamed to `clear-used.md`.

Before a compaction: the phone's /compact first asks the session to write the handoff (agentj-handoff skill) when the file
is missing or older than the current turn; a desktop-typed /compact or Claude Code's own automatic compaction cannot be held
(PreCompact cannot wait for a turn) — that is recorded and the recovery message says there is no handoff this time.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from . import state

DIR = "handoff"
COMPACT, CLEAR, CLEAR_USED = "compact.md", "clear.md", "clear-used.md"
RECOVER_DELAY = 1.5         # s after PostCompact / SessionStart without a new turn → the host's recovery message
PREP_WAIT = 300.0           # s: the phone /compact's handoff turn
FRESH = 900.0               # s: with no known turn start, a handoff older than this is stale (compactprep.FRESH)
TAG = {"zh": "〔Agent J 系统提示〕", "en": "[Agent J system note]"}
SKILL = "agentj-handoff"


def folder() -> Path:
    return state.state_dir() / DIR


def path(name: str) -> Path:
    return folder() / name


def shown(p: Path) -> str:
    """`~/…` for the owner (the phone shows it; copyable, nothing to mask)."""
    s, h = str(p), os.path.expanduser("~").rstrip("/")
    return "~" + s[len(h):] if h and s.startswith(h + "/") else s


def ensure() -> None:
    folder().mkdir(mode=0o700, parents=True, exist_ok=True)


def mtime(name: str) -> float | None:
    """mtime of a non-empty regular file (never through a link), else None."""
    try:
        p = path(name)
        if p.is_symlink() or not p.is_file() or p.stat().st_size <= 0:
            return None
        return p.stat().st_mtime
    except OSError:
        return None


def fresh(since: float | None) -> bool:
    """The compact handoff exists and was written at or after `since` (the current turn's start); with no known turn start,
    within the last FRESH seconds."""
    m = mtime(COMPACT)
    return m is not None and m >= (since - 1 if since is not None else time.time() - FRESH)


def take_clear() -> Path | None:
    """The clear handoff, renamed so it applies once; None when there is none."""
    if mtime(CLEAR) is None:
        return None
    try:
        os.replace(path(CLEAR), path(CLEAR_USED))
        return path(CLEAR_USED)
    except OSError:
        return None


def prep_text(lang: str) -> str:
    p = shown(path(COMPACT))
    if lang == "en":
        return (TAG["en"] + " The context is about to be compacted. First write the handoff with the " + SKILL + " skill to "
                + p + " (merge with what is there: the owner's own words, progress, decisions, absolute paths, pitfalls, next "
                "step; no credentials). Then reply with one line \"Handoff written\" and do nothing else.")
    return (TAG["zh"] + "马上要压缩上下文。先按 " + SKILL + " 技能把交接写进 " + p + "（已有就合并重写：主人原话、进度、决定、"
            "绝对路径、坑、下一步；不写任何凭据）。写完只回一句「交接写好了」，不要做别的事。")


def recover_text(lang: str, kind: str, has: bool, where: Path | None = None) -> str:
    """The host's message after a compaction (kind 'compact') or a /clear with a handoff (kind 'clear')."""
    p = shown(where or path(COMPACT))
    if lang == "en":
        if kind == "clear":
            return TAG["en"] + " The conversation was cleared. Read the handoff " + p + " first, then carry on with that task."
        if has:
            return TAG["en"] + " Compaction finished. Read the handoff " + p + " first, then carry on with the task you were doing."
        return (TAG["en"] + " Compaction finished; there was no handoff file this time. Carry on from the compaction summary, "
                "and write a fresh handoff to " + p + " with the " + SKILL + " skill as soon as you can.")
    if kind == "clear":
        return TAG["zh"] + "对话已清空。先读交接文件 " + p + "，再接着做那件事。"
    if has:
        return TAG["zh"] + "压缩完成。先读交接文件 " + p + "，再继续刚才的任务。"
    return (TAG["zh"] + "压缩完成，本次没有交接文件。先按压缩摘要继续刚才的任务，并尽快按 " + SKILL + " 技能补写一份交接到 "
            + p + "。")


def phone_line(lang: str, kind: str) -> str:
    if kind == "clear":
        return "Cleared; the Agent is reading its handoff." if lang == "en" else "已清空，Agent 正在读交接文件。"
    return "Compaction finished; the Agent is restoring its context." if lang == "en" else "压缩完成，Agent 正在恢复上下文。"


def now() -> float:
    return time.time()
