"""System prompts (AGENTJ-CORE-SPEC 2.2 `core/prompt`): Pi's short prompt (`system-prompt.ts`, 8efd314f: preamble, tool
snippets, rules) plus one block per role. §11 / §12.8 budget: the harness part — this text and every tool definition —
stays ≤ HARNESS_MAX tokens (test_p359_core); `task` + `dispatch` together ≤ ORCH_MAX. Small-model tier: no `dispatch` (it can
be a CEO, not the main Agent). Project instructions (CLAUDE.md → AGENTS.md → README.md, the first found) and the Agent J
identity are added after the budgeted part and capped separately."""
from __future__ import annotations

import json
from pathlib import Path

HARNESS_MAX = 3000
ORCH_MAX = 500
PROJECT_MAX = 12000          # characters of the project's entry document
ENTRY = ("CLAUDE.md", "AGENTS.md", "README.md")

PREAMBLE = ("You are an expert coding assistant operating inside Agent J Harness. You help by reading files, running "
            "commands, editing code and writing new files.")
SNIPPET = {"read": "Read file contents", "bash": "Execute bash commands (tests, git, builds)",
           "edit": "Make precise file edits with exact text replacement, several disjoint edits in one call",
           "write": "Create or overwrite files", "grep": "Search file contents for patterns",
           "find": "Find files by glob pattern", "ls": "List folder contents",
           "task": "Hand a self-contained piece of work to a sub-agent (several run in parallel)",
           "dispatch": "Send work to a project CEO and follow it (new/status/send/stop/resume/report)",
           "decide": "Log a decision you took and why (the order's decision log)"}
RULES = ["Use read to examine files instead of cat or sed.",
         "Use edit for precise changes (edits[].oldText must match exactly); use write only for new files or full rewrites.",
         "Each edits[].oldText is matched against the original file; keep it small but unique, never overlapping.",
         "For dangerous steps (delete, send out, spend, credentials, prices) just call the tool: Agent J shows the owner an "
         "approval card by itself and runs the step only if approved. If it is refused, do not try it another way (no "
         "script doing the same): say so in your answer.",
         "Never print, copy or ask for keys, tokens or passwords.",
         "Be concise in your responses. Show file paths clearly when working with files."]
ROLE = {
    "main": ("You are the owner's assistant (the chairman's assistant). For work that belongs to a project, prefer "
             "dispatch(action=new) to that project's CEO over doing it yourself; you will be told when it ends. Judge an "
             "order by its report head, never by its exit code alone: VERDICT ok and x/y complete = done; attention/fail or "
             "x<y = not done (say what it needs); no report = resume once asking only for the report. Only read report "
             "sections you need."),
    "ceo": ("You are this project's CEO, working headless on one order from your superior. First read the project's entry "
            "document and follow its rules. Split independent pieces of work into task calls (they run in parallel). Stay "
            "inside the brief's write scope; run its verify commands before you claim success. Use decide for every "
            "non-obvious decision. When new instructions arrive mid-way (〔追加〕), follow them. If the brief says this is a "
            "resume, first answer every open item of the previous report: fixed / not fixed / no longer applies, with "
            "evidence. Your final message IS the report and must start with exactly these lines:\n"
            "完成：<done>/<total>\nready：是|否|不适用\n需要上级：<one line, or 无>\nblocked-by: <order ids, or ->\n"
            "then the body (open items as '- [ ] O1 …'), and end with one line:\nVERDICT: ok|attention|fail — <one sentence>"),
    "sub": ("You are a sub-agent of a project CEO, doing one self-contained piece of work. You cannot start other agents. "
            "Your final message goes back to the CEO verbatim: give the result and the evidence only (≤ 2000 tokens), not the "
            "process. If an output schema was given, end with exactly one JSON object matching it."),
}


def tokens(text: str) -> int:
    """Conservative estimate: ~4 ASCII characters per token, 1 per CJK / other character."""
    a = sum(1 for c in text if ord(c) < 128)
    return a // 4 + (len(text) - a) + 1


def tool_list(role: str, tier: str = "large") -> list[str]:
    base = ["read", "bash", "edit", "write", "grep", "find", "ls"]
    if role == "main":
        return base + ["task"] + (["dispatch"] if tier != "small" else [])
    if role == "ceo":
        return base + ["task", "decide"]
    return base


def specs(role: str, tier: str = "large") -> list[dict]:
    from .tools import SPECS
    from .orders import DISPATCH_SPEC, DECIDE_SPEC
    from .loop import TASK_SPEC
    extra = {"task": TASK_SPEC, "dispatch": DISPATCH_SPEC, "decide": DECIDE_SPEC}
    return [SPECS.get(n) or extra[n] for n in tool_list(role, tier)]


def harness_text(role: str, tier: str = "large") -> str:
    names = tool_list(role, tier)
    tools = "\n".join(f"- {n}: {SNIPPET[n]}" for n in names)
    rules = "\n".join(f"- {r}" for r in RULES)
    return f"{PREAMBLE}\n\n<tools>\n{tools}\n</tools>\n\n<rules>\n{rules}\n</rules>\n\n<role>\n{ROLE[role]}\n</role>"


def harness_tokens(role: str, tier: str = "large") -> int:
    return tokens(harness_text(role, tier)) + tokens(json.dumps(specs(role, tier), ensure_ascii=False))


def project_doc(root: str) -> tuple[str, str] | None:
    for name in ENTRY:
        p = Path(root) / name
        try:
            if p.is_file():
                text = p.read_text(encoding="utf-8", errors="replace")
                if len(text) > PROJECT_MAX:
                    text = text[:PROJECT_MAX] + f"\n[… cut at {PROJECT_MAX} characters; read {name} for the rest]"
                return name, text
        except OSError:
            continue
    return None


def system(role: str, root: str, tier: str = "large", identity: str = "", standing: str = "") -> str:
    parts = [harness_text(role, tier)]
    if identity:
        parts.append(f"<identity>\n{identity}\n</identity>")
    if standing:
        parts.append("<standing_orders>\nYour superior's standing orders for this project (always in force):\n"
                     f"{standing}\n</standing_orders>")
    doc = project_doc(root)
    if doc:
        parts.append(f'<project_instructions path="{doc[0]}">\n{doc[1]}\n</project_instructions>')
    parts.append(f"<cwd>\n{root}\n</cwd>")
    return "\n\n".join(parts)
