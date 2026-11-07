"""Compaction (AGENTJ-CORE-SPEC 2.2 `core/compact`, 2.4 「压缩交接」; Pi compaction + deepseek-harness result trimming; P431 C5).

Order of means: the loop first trims old tool outputs (loop._trim); when the conversation is still above
`context − RESERVE` it summarizes everything but the most recent ~KEEP tokens (cut at a user-turn boundary, so a tool call is
never separated from its result) with one tool-less model call. The summary is a kernel step, not an Agent turn:
- the session file gets a `compact` record (summary + how many tail messages stay) — loading it again IS the compacted
  conversation, so a restart or resume continues from the summary (session.py);
- the summary names every file the old part touched (from the structured tool arguments, never guessed from text);
- the adapter writes the same text as the handover file (`<working root>/.agentj/handover/compact.md`, compactprep's path)
  so a harness switch or a person can read it.
A model error that says the context is too long triggers one compaction and one retry (loop._model).
"""
from __future__ import annotations

import json
import re

from . import prompt

RESERVE = 16_000            # Pi: compact when the conversation passes context − 16k
KEEP = 20_000               # Pi: keep the most recent ~20k tokens verbatim
SUMMARY_OUT = 4_000         # tokens the summary may use
INPUT_MAX_SHARE = 0.6       # the transcript sent for summarizing is at most this share of the window
TOOL_SNIP = 1_500           # characters of each old tool output shown to the summarizer
MARK = "〔压缩摘要〕"

SYSTEM = ("You compact a coding-agent conversation into a handover for yourself. Write plain Markdown with these sections:\n"
          "## Owner's words — the owner's requests and decisions, quoted verbatim where short\n"
          "## Done — what was completed, with evidence (commands run, results)\n"
          "## Open — what is unfinished, the next step first\n"
          "## Decisions — choices made and why\n"
          "## Pitfalls — errors hit and how they were solved\n"
          "Keep absolute paths, ids, commands and numbers exactly. No secrets: if a key, token or password appears, write "
          "[redacted]. Do not invent anything that is not in the transcript. At most 900 words.")

_PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$|^\*\*\* Move to: (.+)$", re.M)


def size(system: str, msgs: list[dict]) -> int:
    return prompt.tokens(system) + sum(prompt.tokens(json.dumps(m, ensure_ascii=False)) for m in msgs)


def files_touched(msgs: list[dict]) -> list[str]:
    """Paths from the tool calls (read / write / edit / ls / find / grep `path`, apply_patch headers), first seen first."""
    seen: dict[str, None] = {}
    for m in msgs:
        for c in m.get("tool_calls") or []:
            f = c.get("function") or {}
            try:
                a = json.loads(f.get("arguments") or "{}")
            except (ValueError, RecursionError):
                continue
            if not isinstance(a, dict):
                continue
            for k in ("path", "file_path"):
                if isinstance(a.get(k), str) and a[k].strip():
                    seen.setdefault(a[k].strip()[:300], None)
            if f.get("name") == "apply_patch" and isinstance(a.get("patch") or a.get("input"), str):
                for g in _PATCH_FILE.finditer(a.get("patch") or a.get("input")):
                    seen.setdefault((g.group(1) or g.group(2)).strip()[:300], None)
        if m.get("role") == "user" and isinstance(m.get("content"), str) and m["content"].startswith(MARK):
            for line in m["content"].splitlines():          # files of an earlier summary carry over
                if line.startswith("- `") and line.endswith("`"):
                    seen.setdefault(line[3:-1], None)
    return list(seen)[:200]


def cut(msgs: list[dict], keep: int = KEEP) -> int:
    """Index where the kept tail starts: the earliest user-text turn whose tail is ≤ keep tokens (0 = nothing to cut)."""
    starts = [i for i, m in enumerate(msgs) if m.get("role") == "user"]
    best = 0
    total = 0
    sizes = [prompt.tokens(json.dumps(m, ensure_ascii=False)) for m in msgs]
    tail = {}
    for i in range(len(msgs) - 1, -1, -1):
        total += sizes[i]
        tail[i] = total
    for i in starts:
        if i > 0 and tail[i] <= keep:
            best = i
            break
    if not best and len(starts) > 1:
        best = starts[-1]                                  # even the last turn is big: keep only it
    return best


def transcript(msgs: list[dict], limit_tokens: int) -> str:
    """The old part as text for the summarizer; tool outputs shortened; the oldest dropped first if it is too long."""
    lines = []
    for m in msgs:
        role = m.get("role")
        if role == "tool":
            body = str(m.get("content") or "")
            lines.append("[tool result] " + (body[:TOOL_SNIP] + (" …" if len(body) > TOOL_SNIP else "")))
            continue
        text = str(m.get("content") or "")
        if text:
            lines.append(f"[{role}] {text}")
        for c in m.get("tool_calls") or []:
            f = c.get("function") or {}
            lines.append(f"[tool call] {f.get('name')} {str(f.get('arguments') or '')[:TOOL_SNIP]}")
    out = "\n\n".join(lines)
    while prompt.tokens(out) > limit_tokens and len(out) > 2000:
        out = "[… earliest part dropped]\n" + out[len(out) // 5:]
    return out


def summary_message(text: str, files: list[str], where: str) -> dict:
    body = f"{MARK}The earlier conversation was compacted. Handover written by you (the full log stays in {where}):\n\n" \
           + text.strip()
    if files:
        body += "\n\n## Files touched\n" + "\n".join(f"- `{p}`" for p in files)
    return {"role": "user", "content": body}


def handover_text(text: str, files: list[str]) -> str:
    return "# 压缩交接 · Compaction handover\n\n" + text.strip() + (
        "\n\n## Files touched\n" + "\n".join(f"- `{p}`" for p in files) if files else "") + "\n"


def breakdown(system: str, specs: list[dict], msgs: list[dict]) -> dict:
    """/context: exact estimate per part (the same estimator the budgets use)."""
    parts = {"system": prompt.tokens(system), "tools": prompt.tokens(json.dumps(specs or [], ensure_ascii=False)),
             "user": 0, "assistant": 0, "tool_output": 0, "summary": 0}
    for m in msgs:
        n = prompt.tokens(json.dumps(m, ensure_ascii=False))
        if m.get("role") == "tool":
            parts["tool_output"] += n
        elif m.get("role") == "user" and str(m.get("content") or "").startswith(MARK):
            parts["summary"] += n
        elif m.get("role") in ("user", "assistant"):
            parts[m["role"]] += n
    parts["total"] = sum(v for k, v in parts.items())
    return parts
