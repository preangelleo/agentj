"""ADR-0308-fingerprint-scope C (chairman 2026-11-11): native deletion in the main thread goes to the Agent J trashbin.

The main session (owner and heartbeat turns, its sub-agents) and the host-dispatched background jobs Agent J launches
(orders, `agentj busy run` native jobs) delete through `agentj delete <paths…>` (moved into the trashbin, 24 h). A
pre-tool hook that sees a top-level `rm` / `rmdir` / `unlink` (danger.lex/unwrap: wrappers, `sh -c`, `xargs`,
`find -exec` expanded; `ssh host rm` = remote, not ours) DENIES it with that hint — never a card, never a rewrite of
arbitrary shell. User-created, tested automations (scheduled tasks and their workflow steps) keep real deletion:
the host marks those launches `_delete_mode = "real"` in the adapter cfg it builds itself (tasks._exec only; orders
are never marked — 0.30.8 security r1 M1). The key is never read from config.json (state.agent_config builds a closed dict), a tool field or
model text; anything without the host's mark is "trash" (the stricter reading).

A speed bump against the model's own `rm`, not a boundary: `python -c 'os.remove(…)'`, `find -delete`, `git clean`,
a script or a variable-built argv are not recognised. IRREVERSIBLE shapes (uv_list) are checked first everywhere.
"""
from __future__ import annotations

import os

PROGRAMS = frozenset({'rm', 'rmdir', 'unlink'})
REAL, TRASH = 'real', 'trash'
REASON = 'native_delete_use_trash'
_SHELL_OPS = ('bash', 'exec_command', 'shell_command', 'execute_command', 'shell')

HINT = ('Agent J：不要用原生 {prog} 删除。请改用 `agentj delete <路径…>`：文件移进 Agent J 回收站（保留 24 小时，可恢复），'
        '不弹卡。这次没有执行。 / Agent J: do not delete with native {prog}. Use `agentj delete <paths…>` instead: it moves '
        'the files into the Agent J trashbin (kept 24 h, restorable), no card. Not run.')


def mode(cfg) -> str:
    """'real' only when host code marked this launch a user automation; everything else 'trash'."""
    return REAL if isinstance(cfg, dict) and cfg.get('_delete_mode') == REAL else TRASH


def native_delete(tool, tool_input) -> str | None:
    """The program name ('rm' | 'rmdir' | 'unlink') of a local native deletion in this shell call, or None."""
    from . import danger, policy_registry
    if not isinstance(tool, str) or not isinstance(tool_input, dict):
        return None
    operation = policy_registry.harness_tool(tool).rsplit('__', 1)[-1].rsplit('.', 1)[-1].lower()
    if operation not in _SHELL_OPS:
        return None
    text = tool_input.get('command', tool_input.get('cmd', ''))
    if isinstance(text, list):
        import shlex
        text = shlex.join(str(w) for w in text)
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        for command in danger.lex(text):
            for call in danger.unwrap(command):
                if 'remote' in call.flags:
                    continue
                prog = os.path.basename(call.name or '')
                if prog in PROGRAMS:
                    return prog
    except Exception:  # noqa: BLE001 — an unparseable command is not guessed at (the harness decides)
        return None
    return None


def hint(prog: str) -> str:
    return HINT.format(prog=prog if prog in PROGRAMS else 'rm')


def denial(cfg, tool, tool_input) -> str | None:
    """The deny text for a pre-tool hook, or None (not a native delete, or a user automation)."""
    if mode(cfg) == REAL:
        return None
    prog = native_delete(tool, tool_input)
    return hint(prog) if prog else None
