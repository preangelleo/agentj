"""`jarvis agent detect [--json]` (seat setup §4.1, item 9): which agent harnesses this computer has, and which are usable.

For `claude` and `codex`: installed = the binary is on PATH (or AGENTJARVIS_CLAUDE_BIN / AGENTJARVIS_CODEX_BIN, the same
lookup `serve` uses) and `--version` answers; logged in = a credential file / Keychain item *exists* — never opened, never
read. `opencode` is detected but not supported yet. Decision: 0 usable → "none", exactly 1 → "use:<name>", ≥ 2 →
"ask_owner" (the installing agent must ask its human — it never picks one itself, never the one running the install).

Logged-in values: True (file / Keychain item / env var by NAME), False (nothing found), None = cannot tell: Claude Code
with no saved login file while this command runs inside Claude Code itself (CLAUDECODE=1), which hides its own token from
the commands it runs — counted as usable, so the human is asked rather than silently skipped.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

from .service import tilde
from .text import clean_line

LABEL = {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode"}
BIN_ENV = {"claude": "AGENTJARVIS_CLAUDE_BIN", "codex": "AGENTJARVIS_CODEX_BIN"}
SUPPORTED = ("claude", "codex")
NAMES = ("claude", "codex", "opencode")
LATER = "coming in a later version"
VERSION_TIMEOUT = 15


def agent_bin(kind: str) -> str | None:
    env = BIN_ENV.get(kind)
    return (os.environ.get(env) if env else None) or shutil.which(kind)


def version_of(exe: str) -> str | None:
    """First line of `<exe> --version`, cleaned (no control characters, ≤ 40 chars); None when it does not answer."""
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=VERSION_TIMEOUT,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    if r.returncode != 0:
        return None
    out = (r.stdout or r.stderr or "").splitlines()
    line = clean_line(out[0], 200) if out else ""
    return (line if len(line) <= 40 else line[:39] + "…") or "?"


def claude_login() -> tuple[str, str]:
    """(ok | env | warn, where) — existence only, never content."""
    cfg = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    if os.path.isfile(os.path.join(cfg, ".credentials.json")):
        return "ok", f"login {tilde(os.path.join(cfg, '.credentials.json'))}"
    if sys.platform == "darwin" and shutil.which("security"):
        try:   # no -w: asks only whether the Keychain item exists, never its value
            r = subprocess.run(["security", "find-generic-password", "-s", "Claude Code-credentials"],
                               capture_output=True, timeout=10)
            if r.returncode == 0:
                return "ok", "login in Keychain"
        except (OSError, subprocess.TimeoutExpired):
            pass
    for k in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"):
        if os.environ.get(k):
            return "env", f"env {k} (name only)"
    return "warn", "no login found"


def codex_login() -> tuple[str, str]:
    home = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
    if os.path.isfile(os.path.join(home, "auth.json")):
        return "ok", f"login {tilde(os.path.join(home, 'auth.json'))}"
    for k in ("CODEX_API_KEY", "OPENAI_API_KEY"):
        if os.environ.get(k):
            return "env", f"env {k} (name only)"
    return "warn", "no login found"


def _one(name: str) -> dict:
    exe = agent_bin(name)
    version = version_of(exe) if exe else None
    rec = {"name": name, "installed": version is not None, "logged_in": False, "supported": name in SUPPORTED,
           "version": version}
    if name not in SUPPORTED:
        rec.update(logged_in=None, note=LATER)
        return rec
    if not rec["installed"]:
        return rec
    status, _ = claude_login() if name == "claude" else codex_login()
    if status in ("ok", "env"):
        rec["logged_in"] = True
    elif name == "claude" and os.environ.get("CLAUDECODE") == "1":
        rec.update(logged_in=None, note="running inside Claude Code, which hides its own login from commands: ask the human")
    return rec


def detect() -> dict:
    """{"harnesses": [{name, installed, logged_in, supported, version[, note]}], "usable": [...], "decision": ...}."""
    hs = [_one(n) for n in NAMES]
    usable = [h["name"] for h in hs if h["supported"] and h["installed"] and h["logged_in"] is not False]
    decision = "none" if not usable else f"use:{usable[0]}" if len(usable) == 1 else "ask_owner"
    return {"harnesses": hs, "usable": usable, "decision": decision}


def summary(d: dict) -> str:
    """One line for `jarvis doctor`."""
    parts = []
    for h in d["harnesses"]:
        if not h["installed"]:
            parts.append(f"{LABEL[h['name']]} —")
        elif not h["supported"]:
            parts.append(f"{LABEL[h['name']]} (not supported yet)")
        else:
            parts.append(f"{LABEL[h['name']]} " + {True: "✓", False: "not logged in", None: "login ?"}[h["logged_in"]])
    return " · ".join(parts) + f" → {d['decision']}"


DECISION_TEXT = {
    "none": "没有可用的 Agent：先安装并登录 Claude Code 或 Codex / no usable agent: install and log in to Claude Code or Codex first",
    "ask_owner": "有多个可用：问你的人类要用哪一个，不要替他选 / more than one is usable: ask your human which one — never pick one yourself",
}


def main(as_json: bool = False) -> int:
    d = detect()
    if as_json:
        print(json.dumps(d, ensure_ascii=False))
        return 0
    for h in d["harnesses"]:
        state = ("没装 / not installed" if not h["installed"] else
                 f"{h['version']} · " + ("暂不支持 / not supported yet (" + LATER + ")" if not h["supported"] else
                                          {True: "已登录 / logged in", False: "没登录 / not logged in",
                                           None: "登录状态未知 / login unknown"}[h["logged_in"]]))
        print(f"{LABEL[h['name']]:<12} {state}")
    d_ = d["decision"]
    print("→ " + (DECISION_TEXT[d_] if d_ in DECISION_TEXT else
                  f"只有 {LABEL[d_[4:]]} 可用 / only {LABEL[d_[4:]]} is usable: jarvis agent {d_[4:]} --dir <folder>"))
    return 0
