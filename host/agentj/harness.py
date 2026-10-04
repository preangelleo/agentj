"""`agentj agent detect [--json]` (seat setup §4.1, item 9): which agent harnesses this computer has, and which are usable.

For `claude`, `codex` and `opencode`: installed = the binary is on PATH (or AGENTJ_CLAUDE_BIN / AGENTJ_CODEX_BIN /
AGENTJ_OPENCODE_BIN, the same lookup `serve` uses) and `--version` answers; logged in = a credential file / Keychain item
*exists* — never opened, never read (OpenCode: its own credential store `<XDG_DATA_HOME or ~/.local/share>/opencode/auth.json`,
written by `opencode auth login`, or a model vendor's API-key variable set, by NAME). Decision: 0 usable → "none", exactly 1 →
"use:<name>", ≥ 2 → "ask_owner" (the installing agent must ask its human — it never picks one itself, never the one running
the install). OpenCode counts like the other two.

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
from .envcompat import getenv
from .text import clean_line

LABEL = {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode"}
BIN_ENV = {"claude": "AGENTJ_CLAUDE_BIN", "codex": "AGENTJ_CODEX_BIN", "opencode": "AGENTJ_OPENCODE_BIN"}
SUPPORTED = ("claude", "codex", "opencode")
# API-key variables of the model vendors OpenCode knows (its bundled models.dev list, 1.18.32): checked by NAME only
OPENCODE_KEY_ENV = ("ZHIPU_API_KEY", "DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "MOONSHOT_API_KEY", "ARK_API_KEY",
                    "MINIMAX_API_KEY", "SILICONFLOW_CN_API_KEY", "OPENROUTER_API_KEY", "OPENCODE_API_KEY", "OPENAI_API_KEY",
                    "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY")
NAMES = ("claude", "codex", "opencode")
LATER = "coming in a later version"
VERSION_TIMEOUT = 15


def agent_bin(kind: str) -> str | None:
    env = BIN_ENV.get(kind)
    from .binaries import resolve
    return resolve(kind)["path"] if env else None


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


def opencode_login(version: str | None = None) -> tuple[str, str]:
    """Storage existence is a hint, never proof of a usable key. No DB/content read."""
    data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    root = os.path.join(data, "opencode")
    is_v2 = bool(version and version.lstrip("v").startswith("2."))
    stores = [("opencode.db", "v2 SQLite store exists; key validity unverified"),
              ("auth.json", "v1 credential store exists; key validity unverified")]
    if version and not is_v2:
        stores.reverse()
    for filename, description in stores:
        if version and ((is_v2 and filename != "opencode.db") or (not is_v2 and filename != "auth.json")):
            continue
        p = os.path.join(root, filename)
        if os.path.isfile(p):
            return "ok", f"{description}: {tilde(p)}"
    for k in OPENCODE_KEY_ENV:
        if os.environ.get(k):
            return "env", f"env {k} (name only)"
    return "warn", "no model key store found (opencode auth login); restart Agent J after changing keys"


def login_of(name: str) -> tuple[str, str]:
    return {"claude": claude_login, "codex": codex_login, "opencode": opencode_login}[name]()


def _one(name: str, environ=None) -> dict:
    from .binaries import resolve, installations, wrapper
    resolution = resolve(name, environ)
    exe = resolution["path"]
    version = version_of(exe) if exe else None
    versions = {exe: version} if exe else {}
    installs = []
    for path in installations(name, environ):
        wrapped = wrapper(path)
        real = os.path.realpath(path)
        if not wrapped and real not in versions:
            versions[real] = version_of(real)
        installs.append({"path": path, "wrapper": wrapped, "selected": bool(exe and real == exe),
                         "version": None if wrapped else versions.get(real)})
    rec = {"name": name, "installed": version is not None, "logged_in": False, "supported": name in SUPPORTED,
           "version": version, "executable": exe, "resolution": resolution,
           "installations": installs}
    if name not in SUPPORTED:
        rec.update(logged_in=None, note=LATER)
        return rec
    if not rec["installed"]:
        return rec
    status, _ = opencode_login(version) if name == "opencode" else login_of(name)
    if status in ("ok", "env"):
        rec["logged_in"] = True
    elif name == "claude" and os.environ.get("CLAUDECODE") == "1":
        rec.update(logged_in=None, note="running inside Claude Code, which hides its own login from commands: ask the human")
    return rec


def detect(environ=None) -> dict:
    """{"harnesses": [{name, installed, logged_in, supported, version[, note]}], "usable": [...], "decision": ...}."""
    hs = [_one(n, environ) for n in NAMES]
    usable = [h["name"] for h in hs if h["supported"] and h["installed"] and h["logged_in"] is not False]
    decision = "none" if not usable else f"use:{usable[0]}" if len(usable) == 1 else "ask_owner"
    return {"harnesses": hs, "usable": usable, "decision": decision}


def summary(d: dict) -> str:
    """One line for `agentj doctor`."""
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
    "none": "没有可用的 Agent：已有 Claude Code / Codex 就先登录；都没有就装 OpenCode 并配好模型（install.md 第 3 步）/ no usable "
            "agent: log in to Claude Code or Codex, or install OpenCode and set up a model (install.md Step 3)",
    "ask_owner": "有多个可用：问你的人类要用哪一个，不要替他选 / more than one is usable: ask your human which one — never pick one yourself",
}


def main(as_json: bool = False) -> int:
    d = detect()
    if as_json:
        def local_paths(value):
            if isinstance(value, dict): return {k: local_paths(v) for k, v in value.items()}
            if isinstance(value, list): return [local_paths(v) for v in value]
            if isinstance(value, str):
                home = os.path.expanduser("~")
                return "~" + value[len(home):] if value == home or value.startswith(home + os.sep) else value
            return value
        print(json.dumps(local_paths(d), ensure_ascii=False))
        return 0
    for h in d["harnesses"]:
        state = ("没装 / not installed" if not h["installed"] else
                 f"{h['version']} · " + ("暂不支持 / not supported yet (" + LATER + ")" if not h["supported"] else
                                          {True: "已登录 / logged in", False: "没登录 / not logged in",
                                           None: "登录状态未知 / login unknown"}[h["logged_in"]]))
        print(f"{LABEL[h['name']]:<12} {state}")
    d_ = d["decision"]
    print("→ " + (DECISION_TEXT[d_] if d_ in DECISION_TEXT else
                  f"只有 {LABEL[d_[4:]]} 可用 / only {LABEL[d_[4:]]} is usable: agentj agent {d_[4:]} --dir <folder>"))
    return 0
