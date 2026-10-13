"""P219: the one-time "first run" answers a harness would otherwise ask in a terminal nobody is watching.

Measured on Claude Code 2.1.296 (fresh HOME, Ubuntu 24.04 container, root and non-root): Agent J's own `claude -p
--input-format stream-json` never stops for the trust-this-folder dialog, the onboarding / theme picker, the bypass-mode
confirmation or the custom API key question — those are interactive-terminal screens. They DO stop the owner's own
terminal session, which is what a shared session (and the owner's next `claude`) attaches to, so `agentj agent <harness>`
and serve's start answer them once, ahead of time:

  Claude Code  `<CLAUDE_CONFIG_DIR or ~>/.claude.json`: hasCompletedOnboarding, a theme, and the working folder's
               projects[<dir>].hasTrustDialogAccepted / hasCompletedProjectOnboarding;
               `<CLAUDE_CONFIG_DIR or ~/.claude>/settings.json`: skipDangerousModePermissionPrompt (only the confirmation
               screen of a bypass mode the owner turns on themselves — it grants nothing).
  Codex        `<CODEX_HOME or ~/.codex>/config.toml`: [projects."<dir>"] trust_level = "trusted".
  OpenCode     nothing to answer.

Merge only: a key the owner set is never changed (an explicit Codex "untrusted" stays), nothing is removed, a symlink or
a file that is not a JSON object / valid TOML is left alone and reported. API keys are never read: Claude Code's custom
API key approval stores part of the key, so it is not pre-answered (and `-p` uses the key without asking).

What DID stop the first message (measured, the reasons this module also exists):
  * root + `permissions.defaultMode: bypassPermissions` (setup-agents' full-access default) → Claude Code exits at once
    ("--dangerously-skip-permissions cannot be used with root/sudo privileges") — `root_bypass()`; the adapter then starts
    it with `--permission-mode default` (stricter, never wider).
  * the model service unreachable / failing → Claude Code retries silently up to 10 times (≈ 3 min, only `api_retry`
    events) — the adapter turns those into a phone card (agent.py).
  * no bubblewrap (stock WSL2 Ubuntu) → the fence refuses; the phone gets the 「本次不隔离运行」 card — `fence_deps()`.
  * a model the account / relay does not serve (400 "Claude Fable 5.1 is not supported") — DEFAULT_CLAUDE_MODEL.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

DEFAULT_CLAUDE_MODEL = "claude-opus-5-5"     # 2026-10-13: Agent J's Claude Code defaults to Opus 5.5 · medium
DEFAULT_CLAUDE_EFFORT = "medium"
ENV_SWITCH = "AGENTJ_HARNESS_PREPARE"         # "off" in tests (they run with the developer's real HOME)


def enabled() -> bool:
    return os.environ.get(ENV_SWITCH, "on") != "off"


# ------------------------------------------------------------------ paths
def claude_global(environ=None) -> Path:
    e = os.environ if environ is None else environ
    d = e.get("CLAUDE_CONFIG_DIR")
    return Path(d) / ".claude.json" if d else Path(e.get("HOME") or os.path.expanduser("~")) / ".claude.json"


def claude_settings(environ=None) -> Path:
    e = os.environ if environ is None else environ
    d = e.get("CLAUDE_CONFIG_DIR") or os.path.join(e.get("HOME") or os.path.expanduser("~"), ".claude")
    return Path(d) / "settings.json"


def codex_config(environ=None) -> Path:
    e = os.environ if environ is None else environ
    return Path(e.get("CODEX_HOME") or os.path.join(e.get("HOME") or os.path.expanduser("~"), ".codex")) / "config.toml"


# ------------------------------------------------------------------ safe merge
class Skip(Exception):
    """args[0]: a fixed reason token (symlink / not_object / invalid / changed)."""


def _read(p: Path) -> bytes | None:
    if p.is_symlink():
        raise Skip("symlink")
    try:
        return p.read_bytes()
    except FileNotFoundError:
        return None


def _replace(p: Path, old: bytes | None, data: bytes) -> None:
    """Atomic, 0600, and only if nobody (Claude Code rewrites ~/.claude.json often) changed the file meanwhile."""
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".agentj-p219-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o600 if old is None else (p.stat().st_mode & 0o777) or 0o600)
        if _read(p) != old:
            raise Skip("changed")
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _merge_json(p: Path, change) -> list[str]:
    old = _read(p)
    try:
        doc = json.loads(old) if old else {}
    except (ValueError, UnicodeDecodeError):
        raise Skip("invalid") from None
    if not isinstance(doc, dict):
        raise Skip("not_object")
    done = change(doc)
    if done:
        _replace(p, old, (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode())
    return done


def _real(d: str) -> str:
    return os.path.realpath(os.path.expanduser(d))


# ------------------------------------------------------------------ Claude Code
def _claude_global_change(workdir: str | None):
    def change(d: dict) -> list[str]:
        done = []
        if d.get("hasCompletedOnboarding") is not True:     # false = not finished yet, never the owner's "no"
            d["hasCompletedOnboarding"] = True
            done.append("onboarding")
        if "theme" not in d:
            d["theme"] = "dark"
            done.append("theme")
        if workdir:
            projects = d.setdefault("projects", {})
            if not isinstance(projects, dict):
                return done
            entry = projects.setdefault(_real(workdir), {})
            if not isinstance(entry, dict):
                return done
            # Claude Code stores false until the dialog is answered (a "no" exits instead of being saved), so false = not asked yet
            if entry.get("hasTrustDialogAccepted") is not True:
                entry["hasTrustDialogAccepted"] = True
                done.append("trust")
            if "hasCompletedProjectOnboarding" not in entry:
                entry["hasCompletedProjectOnboarding"] = True
        return done
    return change


def _claude_settings_change(d: dict) -> list[str]:
    if "skipDangerousModePermissionPrompt" in d:
        return []
    d["skipDangerousModePermissionPrompt"] = True
    return ["bypass_confirm"]


def _trusted(projects: dict, workdir: str) -> bool:
    """Claude Code trusts a folder when it or one of its parents was accepted."""
    p = Path(_real(workdir))
    for q in (p, *p.parents):
        e = projects.get(str(q))
        if isinstance(e, dict) and e.get("hasTrustDialogAccepted") is True:
            return True
    return False


def claude_status(workdir: str | None, environ=None) -> dict:
    """{onboarding, trusted, bypass_confirm, root_bypass} as booleans (None = cannot tell: missing / unreadable file)."""
    out = {"onboarding": None, "trusted": None, "bypass_confirm": None, "root_bypass": root_bypass(workdir, environ)}
    try:
        raw = _read(claude_global(environ))
        d = json.loads(raw) if raw else {}
        if isinstance(d, dict):
            out["onboarding"] = d.get("hasCompletedOnboarding") is True
            if workdir:
                out["trusted"] = _trusted(d.get("projects") if isinstance(d.get("projects"), dict) else {}, workdir)
    except (Skip, ValueError, UnicodeDecodeError, OSError):
        pass
    try:
        raw = _read(claude_settings(environ))
        d = json.loads(raw) if raw else {}
        if isinstance(d, dict):
            out["bypass_confirm"] = d.get("skipDangerousModePermissionPrompt") is True
    except (Skip, ValueError, UnicodeDecodeError, OSError):
        pass
    return out


def _is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def root_bypass(workdir: str | None, environ=None) -> bool:
    """True when Claude Code would refuse to start: running as root (no IS_SANDBOX) and the owner's or the folder's settings
    ask for `permissions.defaultMode: bypassPermissions`. Settings are read as data; nothing else in them is used."""
    e = os.environ if environ is None else environ
    if not _is_root() or e.get("IS_SANDBOX") == "1":
        return False
    files = [claude_settings(e)]
    if workdir:
        files += [Path(workdir) / ".claude" / n for n in ("settings.json", "settings.local.json")]
    mode = None
    for p in files:                       # later = more specific (user < project < local)
        try:
            d = json.loads(p.read_text())
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        perms = d.get("permissions") if isinstance(d, dict) else None
        if isinstance(perms, dict) and isinstance(perms.get("defaultMode"), str):
            mode = perms["defaultMode"]
    return mode == "bypassPermissions"


# ------------------------------------------------------------------ Codex
def _codex_trust(workdir: str, environ=None) -> list[str]:
    import tomlkit
    p = codex_config(environ)
    old = _read(p)
    try:
        doc = tomlkit.parse(old.decode()) if old else tomlkit.document()
    except Exception:  # noqa: BLE001 — tomlkit raises several types; never rewrite what we cannot parse
        raise Skip("invalid") from None
    projects = doc.get("projects")
    if projects is None:
        projects = tomlkit.table(is_super_table=True)
        doc["projects"] = projects
    if not hasattr(projects, "get"):
        raise Skip("not_object")
    key = _real(workdir)
    entry = projects.get(key)
    if entry is not None and (not hasattr(entry, "get") or "trust_level" in entry):
        return []                             # the owner's own answer (trusted or untrusted) stays
    if entry is None:
        entry = tomlkit.table()
        projects[key] = entry
    entry["trust_level"] = "trusted"
    _replace(p, old, tomlkit.dumps(doc).encode())
    return ["trust"]


def codex_trusted(workdir: str, environ=None) -> bool | None:
    try:
        import tomllib
        raw = _read(codex_config(environ))
        d = tomllib.loads(raw.decode()) if raw else {}
    except Exception:  # noqa: BLE001
        return None
    projects = d.get("projects") if isinstance(d.get("projects"), dict) else {}
    p = Path(_real(workdir))
    for q in (p, *p.parents):
        e = projects.get(str(q))
        if isinstance(e, dict) and "trust_level" in e:
            return e["trust_level"] == "trusted"
    return False


# ------------------------------------------------------------------ entry points
def prepare(kind: str, workdir: str | None, environ=None) -> dict:
    """Answer the harness's one-time questions for `workdir`. → {"done": [...], "skipped": {file: reason}}. Never raises."""
    out: dict = {"done": [], "skipped": {}}
    if kind == "claude":
        steps = [("claude.json", lambda: _merge_json(claude_global(environ), _claude_global_change(workdir))),
                 ("settings.json", lambda: _merge_json(claude_settings(environ), _claude_settings_change))]
    elif kind == "codex" and workdir:
        steps = [("config.toml", lambda: _codex_trust(workdir, environ))]
    else:
        steps = []
    for name, fn in steps:
        try:
            out["done"] += fn()
        except Skip as e:
            out["skipped"][name] = str(e.args[0])
        except (OSError, ImportError) as e:
            out["skipped"][name] = type(e).__name__
    return out


def fence_deps() -> dict:
    """Linux: {"bwrap": bool, "socat": bool, "hint": install command or ""}. bwrap is Agent J's fence; socat is what Claude
    Code's own sandbox (/sandbox) needs next to it. macOS / others: {} (sandbox-exec is part of the system)."""
    if not sys.platform.startswith("linux"):
        return {}
    have = {"bwrap": bool(shutil.which("bwrap")), "socat": bool(shutil.which("socat"))}
    missing = [{"bwrap": "bubblewrap"}.get(k, k) for k, v in have.items() if not v]
    return {**have, "hint": install_hint(missing) if missing else ""}


def install_hint(packages: list[str]) -> str:
    try:
        with open("/etc/os-release") as f:
            d = f.read().lower()
    except OSError:
        d = ""
    pk = " ".join(packages)
    if "debian" in d or "ubuntu" in d:
        return f"sudo apt-get install -y {pk}"
    if "fedora" in d or "rhel" in d or "centos" in d:
        return f"sudo dnf install -y {pk}"
    if "arch" in d:
        return f"sudo pacman -S --needed {pk}"
    if "suse" in d:
        return f"sudo zypper install -y {pk}"
    return f"install: {pk}"


def summary(res: dict, lang: str = "zh") -> str:
    """One line for `agentj agent` (empty when nothing changed and nothing was skipped)."""
    names = {"onboarding": ("首次引导", "onboarding"), "theme": ("主题", "theme"), "trust": ("信任工作目录", "trusted the folder"),
             "bypass_confirm": ("危险模式确认", "bypass confirmation")}
    en = lang == "en"
    parts = [names.get(x, (x, x))[1 if en else 0] for x in res.get("done", [])]
    line = ""
    if parts:
        line = ("✓ One-time setup answered ahead (no terminal prompt in the background): " if en else
                "✓ 已预先完成首次设置（后台不会再停在终端确认）：") + ("、" if not en else ", ").join(parts)
    if res.get("skipped"):
        sk = ", ".join(f"{k} ({v})" for k, v in res["skipped"].items())
        line += ("\n" if line else "") + ("! Left unchanged (not a plain file / not valid): " if en else "! 没有改（不是普通文件或内容无效）：") + sk
    return line
