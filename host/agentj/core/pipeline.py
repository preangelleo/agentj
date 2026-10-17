"""The pre-execute gate (AGENTJ-CORE-SPEC 2.4 / 12.7; order of steps from deepseek-harness): every tool call of an Agent J
Harness session passes here before the toolbox sees it. Fail closed: any exception, an unknown tool or a missing approval
channel = refused, with a sentence the model can act on.

    0  stopped (the owner's stop / this order's stop)          → refused
    1  research mode: only provably read-only calls             (danger.readonly)
    2  write scope: write / edit only inside the project and the order's scope.write globs (pstack ②, deterministic)
    3  refused before: a step the owner denied in this order is refused for every session of it (O8, D16)
    4  danger list (danger.classify on the Claude-shaped call): low risk → runs; a hit → the owner's card
    5  no approval channel (no phone link): a card cannot be shown → refused; and C14 (omp #10124): a session without that
       channel never runs code at all — `bash` is refused even when it looks harmless
The card itself is serve's Host.ask (origin = the chain the HOST built, never model text); allow returns the input unchanged.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os

from .. import danger
from .tools import READ_ONLY, WRITES, resolve, ToolError

CLAUDE = {"bash": "Bash", "write": "Write", "edit": "Edit", "read": "Read", "grep": "Grep", "find": "Glob", "ls": "LS"}
EXEC = ("bash",)


def claude_shape(name: str, args: dict, root: str) -> tuple[str, dict]:
    """Our call → the shape danger.py / the phone card already understand (Claude Code's tool names and fields)."""
    tool = CLAUDE.get(name, name)
    if name == "bash":
        return tool, {"command": str(args.get("command", ""))}
    p = args.get("path")
    full = str(resolve(root, p)) if isinstance(p, str) or name in ("ls", "grep", "find") else ""
    if name == "write":
        return tool, {"file_path": full, "content": str(args.get("content", ""))[:4000]}
    if name == "edit":
        e = (args.get("edits") or [{}])[0] if isinstance(args.get("edits"), list) else {}
        return tool, {"file_path": full, "old_string": str(e.get("oldText", ""))[:2000],
                      "new_string": str(e.get("newText", ""))[:2000]}
    if name == "read":
        return tool, {"file_path": full}
    if name == "grep":
        return tool, {"pattern": str(args.get("pattern", "")), "path": full}
    return tool, {"path": full, **({"pattern": str(args["pattern"])} if "pattern" in args else {})}


def digest(name: str, args: dict) -> str:
    return hashlib.sha256((name + "\n" + json.dumps(args, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()[:24]


class Gate:
    """approver: async (tool, tool_input, who) -> {"behavior": "allow"|"deny", "message"?}; None = no phone link.
    denied: the order-wide set of refused step digests (shared by the CEO and all its sub-agents; persisted by the caller)."""

    def __init__(self, root: str, approver=None, *, research: bool = False, scope_write=None, denied: set | None = None,
                 stopped=None, extra=None, on_denied=None):
        self.root = os.path.realpath(root)
        self.approver, self.research = approver, research
        self.scope = [s for s in (scope_write or []) if isinstance(s, str) and s.strip()]
        self.denied = denied if denied is not None else set()
        self.stopped = stopped or (lambda: False)
        self.extra, self.on_denied = extra, on_denied

    def _in_scope(self, full: str) -> bool:
        if not self.scope:
            return True
        rel = os.path.relpath(full, self.root)
        return any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(rel, g.rstrip("/") + "/*") or rel == g.rstrip("/")
                   or rel.startswith(g.rstrip("/*").rstrip("/") + "/") for g in self.scope)

    @staticmethod
    def _shows_credentials(name: str, inp: dict) -> bool:
        if name in ("read", "grep"):
            return bool(danger.cred_path(inp.get("file_path") or inp.get("path") or ""))
        if name == "bash":
            return bool(danger._shared_cred_read(inp.get("command", "")))
        return False

    async def check(self, name: str, args: dict, who: str | None = None) -> tuple[bool, str]:
        try:
            return await self._check(name, args, who)
        except ToolError as e:
            return False, str(e)
        except Exception as e:  # noqa: BLE001 — fail closed
            return False, f"Agent J refused this step (check failed: {type(e).__name__})."

    async def _check(self, name, args, who):
        if self.stopped():
            return False, "Stopped by the owner: nothing more runs. Write your report now."
        if name not in CLAUDE or not isinstance(args, dict):
            return False, f"Unknown tool {name}."
        tool, inp = claude_shape(name, args, self.root)
        if self.research:
            ok, why = (True, "") if name in READ_ONLY else danger.readonly(tool, inp)
            if not ok:
                return False, f"This is a research (read-only) task: {why or 'not read-only'}. Do not try another way."
        if name in WRITES and not self._in_scope(inp["file_path"]):
            return False, (f"{os.path.relpath(inp['file_path'], self.root)} is outside this order's write scope "
                           f"({', '.join(self.scope)}). Report it as an open item instead of writing it.")
        d = digest(name, args)
        if d in self.denied:
            return False, ("The owner already refused exactly this step in this order. Do not retry it, not in other words "
                           "and not through another session; report it instead.")
        v = danger.classify(tool, inp, self.extra)
        if not v.danger and self._shows_credentials(name, inp):
            v = danger.Verdict(cats=["credentials"], why="查看密钥文件")   # §6.1: reading .env / keys asks, too
        if not v.danger:
            if name in EXEC and self.approver is None:
                return False, ("No approval channel to the owner's phone is connected, so this session does not run "
                               "commands (C14). Report what you would run.")
            return True, ""
        if self.approver is None:
            return False, "This step needs the owner's approval and no approval channel is connected: refused."
        ans = await self.approver(tool, inp, who)
        if isinstance(ans, dict) and ans.get("behavior") == "allow":
            return True, ""
        self.denied.add(d)
        if self.on_denied:
            self.on_denied(d)
        msg = ans.get("message") if isinstance(ans, dict) else None
        return False, (msg or "The owner refused this step.") + " Do not retry it in another way; report it."
