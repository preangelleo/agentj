"""`agentj service install|uninstall|status` (L3-min): keep `agentj serve` running after logout / reboot.

Linux = a systemd *user* unit (`~/.config/systemd/user/agentj.service`); macOS = a LaunchAgent
(`~/Library/LaunchAgents/net.agentj.host.plist`). Both start the absolute path of the agentj that ran `install`
(inside its venv, which the fence keeps read-only), with `serve --events quiet --no-stdin`: no terminal, and the log
(journal / `service.log` in the state dir) gets metadata lines only — never a message, a reply or a command.

Harness binary paths live only in the owner environment file; units/plists never bake in binary overrides.
Never a secret in the unit / plist: only PATH (captured at install so `claude` / `codex` / `bwrap` resolve) and, when set,
AGENTJ_STATE_DIR / AGENTJ_CLAUDE_BIN / AGENTJ_CODEX_BIN (paths). The Agent uses its own login (~/.claude,
~/.codex). If the only Claude Code login is the CLAUDE_CODE_OAUTH_TOKEN variable, the human puts it into an environment
file they create themselves (0600, next to the unit, read-only for the fenced Agent) — we print how, we never copy it.

`AGENTJ_SERVICE_NAME` overrides the unit name / launchd label (tests); `vibe-remote*` names are refused.

0.10 rename: the unit / label used to be `agentjarvis` / `net.agentjarvis.host`. `install` stops, disables and deletes
that old one (only with the default name; tests name a throwaway old unit with AGENTJ_SERVICE_LEGACY_NAME) and installs
the new one, which starts `agentj serve`; `doctor` warns while only the old one exists.
"""
from __future__ import annotations

import contextlib
import os
import plistlib
import re
import shutil
import shlex
import stat
from pathlib import Path
import subprocess
import sys

from .envcompat import getenv, isset
from . import macapp, resource_policy

DEFAULT_UNIT = "agentj"
DEFAULT_LABEL = "net.agentj.host"
LEGACY_UNIT = "agentjarvis"                # ≤ 0.9
LEGACY_LABEL = "net.agentjarvis.host"
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
CAPTURED_ENV = ("AGENTJ_STATE_DIR", "AGENTJ_CLAUDE_BIN", "AGENTJ_CODEX_BIN", "AGENTJ_OPENCODE_BIN", "AGENTJ_GEMINI_BIN", "AGENTJ_KIMI_BIN")   # written with the new names; read either
PROXY_ENV = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy", "ALL_PROXY", "all_proxy")
TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"


class ServiceError(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason)
        self.reason, self.detail = reason, detail


def platform() -> str:
    return "linux" if sys.platform.startswith("linux") else "macos" if sys.platform == "darwin" else "other"


def name() -> str:
    """systemd unit name (without .service) or launchd label."""
    n = getenv("AGENTJ_SERVICE_NAME") or (DEFAULT_UNIT if platform() == "linux" else DEFAULT_LABEL)
    if not _NAME_RE.fullmatch(n) or n.lower().startswith("vibe-remote") or n.endswith(".service"):
        raise ServiceError("bad_name", n)
    return n


def legacy_name() -> str | None:
    """The ≤ 0.9 unit name / label to clean up: only when this install uses the default name (a test service never touches
    a real old one) — or the test override AGENTJ_SERVICE_LEGACY_NAME."""
    t = getenv("AGENTJ_SERVICE_LEGACY_NAME")
    if t:
        return t if _NAME_RE.fullmatch(t) and not t.lower().startswith("vibe-remote") and not t.endswith(".service") else None
    if getenv("AGENTJ_SERVICE_NAME"):
        return None
    return LEGACY_UNIT if platform() == "linux" else LEGACY_LABEL if platform() == "macos" else None


def legacy_status() -> dict:
    """{"name", "path", "installed", "active"} of the old unit / LaunchAgent ("installed": False when there is none)."""
    n, plat = legacy_name(), platform()
    if not n:
        return {"name": None, "installed": False, "active": None}
    if plat == "linux":
        path = os.path.join(unit_dir(), f"{n}.service")
        if not os.path.exists(path):
            return {"name": n, "path": path, "installed": False, "active": None}
        act = "unknown"
        if shutil.which("systemctl"):
            try:
                act = _systemctl("is-active", f"{n}.service", timeout=10).stdout.strip() or "unknown"
            except (OSError, subprocess.TimeoutExpired):
                pass
        return {"name": n, "path": path, "installed": True, "active": act}
    if plat == "macos":
        path = plist_path(n)
        if not os.path.exists(path):
            return {"name": n, "path": path, "installed": False, "active": None}
        try:
            r = _launchctl("print", f"{_gui()}/{n}", timeout=10)
            act = "active" if r.returncode == 0 and re.search(r"^\s*state = running", r.stdout, re.M) else "inactive"
        except (OSError, subprocess.TimeoutExpired):
            act = "unknown"
        return {"name": n, "path": path, "installed": True, "active": act}
    return {"name": n, "installed": False, "active": None}


def remove_legacy() -> dict | None:
    """Stop, disable and delete the ≤ 0.9 unit / LaunchAgent. → {"name", "path"} when one was removed, else None."""
    s = legacy_status()
    if not s.get("installed"):
        return None
    n, path = s["name"], s["path"]
    if platform() == "linux":
        _systemctl("disable", "--now", f"{n}.service")
        os.unlink(path)
        _systemctl("daemon-reload")
        _systemctl("reset-failed", f"{n}.service")
    else:
        _bootout_wait(n)
        os.unlink(path)
    return {"name": n, "path": path}


def agentj_argv() -> list[str]:
    """How the service starts this very agentj: the venv's console script (`<venv>/bin/agentj`, an absolute path inside
    the read-only venv — not the ~/.local/bin shim, which any same-user process could replace), else this interpreter
    with `-P -m agentj.cli`."""
    exe = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "agentj")
    if os.path.isfile(exe) and os.access(exe, os.X_OK):
        return [exe]
    return [os.path.abspath(sys.executable), "-P", "-m", "agentj.cli"]


SERVE_ARGS = ["serve", "--events", "quiet", "--no-stdin"]


def ensure_agentj_on_path(environ: dict | None = None, exe: str | None = None) -> str | None:
    """P155: every bundled skill has the Agent run `agentj …` in its own shell, which inherits serve's PATH. A service PATH
    captured without the install's bin directory (QA-J 10-12: ~/.local/bin missing → 「找不到 agentj 命令」) gets the running
    package's own bin directory APPENDED (never shadows anything). Returns the directory added, else None."""
    e = os.environ if environ is None else environ
    if shutil.which("agentj", path=e.get("PATH", "")):
        return None
    d = os.path.dirname(os.path.abspath(exe or sys.executable))
    launcher = os.path.join(d, "agentj")
    if not (os.path.isfile(launcher) and os.access(launcher, os.X_OK)):
        return None
    e["PATH"] = (e["PATH"] + os.pathsep if e.get("PATH") else "") + d
    return d


def service_env(environ: dict | None = None) -> tuple[dict, list[str]]:
    """(variables written into the unit, notes for the human). Values are paths only; proxies only without credentials."""
    e = os.environ if environ is None else environ
    path = os.pathsep.join(p for p in (e.get("PATH") or "").split(os.pathsep) if p.startswith("/"))
    out = {"PATH": path or "/usr/local/bin:/usr/bin:/bin"}
    if isset("AGENTJ_STATE_DIR", e):
        sd = getenv("AGENTJ_STATE_DIR", "", e)
        if sd:
            out["AGENTJ_STATE_DIR"] = os.path.abspath(os.path.expanduser(sd))
    for k in CAPTURED_ENV[1:]:
        if getenv(k, None, e):
            out[k] = getenv(k, None, e)
    from .instance import ENV as INSTANCE_ENV, current
    if current(e) > 1:   # P180: the unit / job re-selects its instance (state dir, preferences, unit name) from this
        out[INSTANCE_ENV] = str(current(e))
    notes = []
    from .binaries import resolve, ENV
    for kind, key in ENV.items():
        resolution = resolve(kind, e)
        if resolution["path"]:   # P282: the PATH entry itself (…/bin/codex → current/…), not the versioned folder behind it
            out[key] = stable_path(resolution)
        elif resolution["wrapper"]:
            out.pop(key, None)
            notes.append(f"{kind}: wrapper cannot be resolved; set {key} to the real executable, then agentj service install")
    for k in PROXY_ENV:
        v = e.get(k)
        if not v:
            continue
        if "@" in v:
            notes.append(f"{k} 里带账号密码，没写进服务；需要的话写进环境文件（见下） / {k} holds credentials: not copied — "
                         "put it in the environment file below")
        else:
            out[k] = v
    return out, notes


def stable_path(resolution: dict) -> str:
    """P282: what to save for a resolved harness: the PATH entry the owner's shell uses (a symlink a self-updating harness
    moves to its new version: ~/.local/bin/codex → ~/.codex/packages/standalone/current/bin/codex, ~/.local/bin/claude →
    ~/.local/share/claude/versions/<v>), never the versioned folder behind it, which the next update leaves behind. A
    mise / asdf shim keeps the bypassed real path (a shim must never be executed)."""
    via = resolution.get("via") or resolution.get("reason")
    req = resolution.get("requested")
    return req if via == "path" and req and os.path.isabs(req) else resolution["path"]


def heal_binary_pins(n: str, environ: dict | None = None) -> list[dict]:
    """P282: saved harness paths a self-update left behind (binaries.resolve → pin_stale / pin_missing) are rewritten to
    the PATH entry the service finds now. Only those AGENTJ_<NAME>_BIN lines change; every other byte (keys included) is
    kept, 0600. → [{"kind", "old", "new"}] (paths only). Never raises: a file that cannot be read or written heals nothing."""
    from .binaries import ENV, resolve
    path = env_file(n, environ)
    try:
        saved = binary_env(path)
    except ServiceError:
        return []
    e = dict(os.environ if environ is None else environ)
    e.update(saved)
    fixes = {}
    for kind, key in ENV.items():
        if key not in saved:
            continue
        r = resolve(kind, e)
        if r.get("reason") in ("pin_stale", "pin_missing") and r.get("path"):
            new = stable_path(r)
            if new and not any(c in new for c in "\n\r\0"):
                fixes[key] = {"kind": kind, "old": saved[key], "new": new}
    if not fixes:
        return []
    try:
        lines = Path(path).read_bytes().decode().splitlines(keepends=True)
    except (OSError, UnicodeError):
        return []
    out = []
    for line in lines:
        k = line.strip().partition("=")[0].strip()
        if k in fixes and "=" in line:
            v = fixes[k]["new"]
            line = k + '="' + v.replace("\\", "\\\\").replace('"', '\\"') + '"\n'
        out.append(line)
    try:
        _write(path, "".join(out).encode(), 0o600)
    except OSError:
        return []
    return list(fixes.values())


def binary_env(path: str) -> dict:
    """Read path assignments only; never evaluate a shell or return credential entries."""
    try:
        content = Path(path).read_text()
    except FileNotFoundError:
        return {}
    out = {}
    for line in content.splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key in CAPTURED_ENV[1:]:
            try:
                parts = shlex.split(value, comments=False, posix=True)
            except ValueError:
                raise ServiceError("bad_binary_env") from None
            if len(parts) != 1 or not parts[0]:
                raise ServiceError("bad_binary_env")
            out[key] = parts[0]
    return out


def prepare_binary_env(n: str, environ=None, previous=None) -> tuple[dict, list[str]]:
    """Owner's saved override > invoking shell > PATH. Append absent paths only."""
    from .binaries import ENV
    e = dict(os.environ if environ is None else environ)
    path = env_file(n, e)
    healed = heal_binary_pins(n, e)        # P282: a path a self-update left behind follows PATH again
    saved = binary_env(path)
    e.update(saved)
    env, notes = service_env(e)
    selected = {k: saved[k] if k in saved else env[k] for k in ENV.values() if k in env or k in saved}
    previous = installed_binary_env(n) or previous or {}
    for h in healed:
        notes.append(f"{h['kind']}: {tilde(h['old'])} is gone or an older version; now {tilde(h['new'])} / "
                     f"保存的旧路径已失效或是旧版本，改为 {tilde(h['new'])}")
    for kind, key in ENV.items():
        if key in selected:
            origin = "saved owner override" if key in saved else "shell override" if getenv(key, None, e) else "first PATH executable; configured mise/asdf version when PATH is a shim"
            notes.append(f"{kind}: {tilde(selected[key])} ({origin})")
            old = previous.get(key)
            if old and os.path.expanduser(old) != os.path.expanduser(selected[key]):
                notes.append(f"{kind}: selection changed / 选择变化: {tilde(old)} -> {tilde(selected[key])}")
    missing = {k: v for k, v in selected.items() if k not in saved}
    if missing:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Preserve all existing bytes, including unknown owner entries. No secret
        # is copied to units, logs, command arguments or diagnostics.
        prior = Path(path).read_bytes() if Path(path).exists() else b""
        def quote(value):
            if any(c in value for c in "\n\r\0"):
                raise ServiceError("bad_binary_env")
            return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
        addition = ("\n" if prior and not prior.endswith(b"\n") else "") + "".join(k + "=" + quote(v) + "\n" for k, v in missing.items())
        _write(path, prior + addition.encode(), 0o600)
    for key in ENV.values():
        env.pop(key, None)
    if platform() == "macos":
        env["AGENTJ_SERVICE_ENV_FILE"] = path
    return env, notes


def installed_binary_env(n: str) -> dict:
    """Only harness paths from the existing unit/plist; never show other variables."""
    path = Path(unit_dir()) / (n + ".service") if platform() == "linux" else Path(plist_path(n))
    try:
        if platform() == "macos":
            values = plistlib.loads(path.read_bytes()).get("EnvironmentVariables", {})
        else:
            values = {}
            for line in path.read_text().splitlines():
                if line.startswith("Environment="):
                    for item in shlex.split(line.partition("=")[2]):
                        key, sep, value = item.partition("=")
                        if sep and key in CAPTURED_ENV[1:]:
                            values[key] = value.replace("%%", "%")
        return {k: v for k, v in values.items() if k in CAPTURED_ENV[1:]}
    except (OSError, ValueError):
        return {}


def previous_binary_selection(st, n: str) -> dict:
    import json
    try:
        record = json.loads((st.root / 'service-binaries.json').read_text())
        if record.get('name') != n or record.get('platform') != platform(): return {}
        return {k:v for k,v in record.get('paths',{}).items() if k in CAPTURED_ENV[1:] and isinstance(v,str)}
    except (OSError,ValueError,AttributeError): return {}


def remember_binary_selection(st, n: str) -> None:
    import json
    from .binaries import ENV, resolve
    e = dict(os.environ);e.update(binary_env(env_file(n)))
    paths = {key: resolve(kind,e)['path'] for kind,key in ENV.items() if resolve(kind,e)['path']}
    _write(str(st.root / 'service-binaries.json'),json.dumps({'name':n,'platform':platform(),'paths':paths}).encode(),0o600)


def effective_binary_environment(svc: dict) -> dict:
    """Doctor resolves the installed service's saved paths, rather than its shell's PATH."""
    env = dict(os.environ)
    if svc.get('installed') and svc.get('name'):
        env.update(binary_env(env_file(svc['name'])))
    return env


def binary_env_mismatches(n: str) -> list[str]:
    saved = binary_env(env_file(n))
    installed = installed_binary_env(n)
    return [key for key, value in installed.items() if key in saved and value != saved[key]]


CLAUDE_AUTH_ENV = (TOKEN_ENV, "ANTHROPIC_API_KEY")
KNOWN_KEY_ENV = frozenset((*CLAUDE_AUTH_ENV, "ANTHROPIC_AUTH_TOKEN", "CODEX_API_KEY",
    "OPENAI_API_KEY", "OPENROUTER_API_KEY", "TYPESAFE_API_KEY", "ELEVENLABS_API_KEY", "GEMINI_API_KEY",
    "GOOGLE_API_KEY", "AGENTJ_TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN"))
KEY_ENV_RE = re.compile(r"[A-Z][A-Z0-9_]{0,79}")


def referenced_key_env(value) -> set[str]:
    """Walk configuration data only; names never imply copying values into configuration."""
    names = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and key.endswith("key_env") and isinstance(item, str) and KEY_ENV_RE.fullmatch(item):
                names.add(item)
            names.update(referenced_key_env(item))
    elif isinstance(value, list):
        for item in value:
            names.update(referenced_key_env(item))
    return names


def private_env(path: str, allowed, reason="bad_service_env") -> dict:
    """Open without following a symlink; validate the opened inode before reading any bytes.
    No shell execution, partial environment updates, raw parser errors or value diagnostics.
    """
    import stat
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return {}
    except OSError:
        raise ServiceError(reason, MESSAGES[reason]) from None
    try:
        with os.fdopen(fd, "r") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
                raise ServiceError(reason, MESSAGES[reason])
            out = {}
            for line in stream:
                key, sep, value = line.strip().partition("=")
                if not sep or key not in allowed:
                    continue
                try:
                    parts = shlex.split(value, comments=True, posix=True)
                except ValueError:
                    raise ServiceError(reason, MESSAGES[reason]) from None
                if len(parts) != 1 or not parts[0] or "\0" in parts[0]:
                    raise ServiceError(reason, MESSAGES[reason])
                out[key] = parts[0]
            return out
    except (OSError, UnicodeError):
        raise ServiceError(reason, MESSAGES[reason]) from None


def load_launch_binary_env(cfg=None) -> None:
    # Launchd has no EnvironmentFile. The plist contains only the private file's path.
    path = os.environ.get("AGENTJ_SERVICE_ENV_FILE")
    if not path:
        return
    os.environ.update(private_env(path, _service_env_names(cfg)))


def _service_env_names(cfg=None) -> set:
    """The key NAMES the service environment file may set in serve's own environment (never arbitrary names)."""
    from . import preferences, telegram, harness, relay_auth
    from .state import State
    st = State()
    names = set(KNOWN_KEY_ENV) | set(CAPTURED_ENV[1:]) | set(harness.OPENCODE_KEY_ENV) | set(relay_auth.NAMES)
    if st.exists():
        names.update(referenced_key_env(st.config()))
        names.update(referenced_key_env(telegram.enrollment(st)))
    if cfg is None:
        try:
            cfg = preferences.read()[1]
        except preferences.ConfigError:
            cfg = {}  # The configuration validator owns its error; never load arbitrary names.
    names.update(referenced_key_env(cfg))
    return names


def reload_service_env(cfg=None) -> list:
    """P320 `agentj reload`: read the service's private environment file again into serve's own environment, so a key added
    with `agentj service import-key` (or by hand) works without restarting serve. Linux: systemd read the EnvironmentFile only
    at start (the reason the Telegram channel needed a restart, Leo 10-15); macOS: the plist's file. Known names only, values
    never returned or logged. → the NAMES whose value was added or changed (sorted). A missing file changes nothing."""
    path = os.environ.get("AGENTJ_SERVICE_ENV_FILE") or env_file(name())
    fresh = private_env(path, _service_env_names(cfg))
    changed = sorted(k for k, v in fresh.items() if os.environ.get(k) != v)
    os.environ.update(fresh)
    return changed


def claude_env(path: str) -> dict:
    return private_env(path, CLAUDE_AUTH_ENV, "bad_claude_env")


# ------------------------------------------------------------------ P198 (I): a key the shell has but the service does not
JEV_KEY_ENV = "OPENROUTER_API_KEY"     # = approval_jev.KEY_ENV (P157 layer 2); importing approval_jev here is not needed
MAX_KEY_VALUE = 4096


def env_file_has(path: str, key: str) -> bool | None:
    """Whether the service env file assigns `key` a non-empty value (the last assignment wins, as systemd / private_env).
    Names only: the value is parsed, never returned. None = the file exists but cannot be read (a symlink, unreadable)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return False
    except OSError:
        return None
    found = False
    try:
        with os.fdopen(fd, "r", errors="replace") as stream:
            for line in stream:
                k, sep, v = line.strip().partition("=")
                if sep and k.strip() == key:
                    try:
                        parts = shlex.split(v, comments=True, posix=True)
                    except ValueError:
                        parts = []
                    found = len(parts) == 1 and bool(parts[0])
    except OSError:
        return None
    return found


def import_key(n: str, key: str, environ: dict | None = None) -> str:
    """P198 (I): one step, no hand-editing — copy `key` from the invoking shell's environment into the service env file
    (owner's regular file, never through a symlink, 0600; every other byte kept). The value is never printed, logged or put
    on a command line. → "present" (the file already has it) · "imported" · "absent" (this shell does not have it either).
    Raises ServiceError("bad_service_env") for a file that is not safely ours or a value that cannot be one line."""
    import stat
    if not isinstance(key, str) or not KEY_ENV_RE.fullmatch(key):
        raise ServiceError("bad_key_name")
    e = os.environ if environ is None else environ
    path = env_file(n, e if environ is not None else None)
    has = env_file_has(path, key)
    if has is None:
        raise ServiceError("bad_service_env")
    if has:
        return "present"
    value = (e.get(key) or "").strip()
    if not value:
        return "absent"
    if len(value) > MAX_KEY_VALUE or any(c in value for c in "\n\r\0'"):
        raise ServiceError("bad_service_env")
    prior = b""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    except OSError:
        raise ServiceError("bad_service_env") from None
    else:
        with os.fdopen(fd, "rb") as f:
            info = os.fstat(f.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise ServiceError("bad_service_env")
            prior = f.read()
    # single quotes: literal both for systemd's EnvironmentFile and for private_env's shlex (macOS) — no escaping rules differ
    line = ("\n" if prior and not prior.endswith(b"\n") else "") + f"{key}='{value}'\n"
    _write(path, prior + line.encode(), 0o600)
    return "imported"


def key_note(n: str, key: str = JEV_KEY_ENV, environ: dict | None = None) -> str | None:
    """P198 (I): what to tell the owner when the service will not see `key` (default: Jev's, the P157 approval layer 2).
    None when the service env file has it. Names and commands only — never a value."""
    e = os.environ if environ is None else environ
    path = env_file(n, e if environ is not None else None)
    has = env_file_has(path, key)
    if has:
        return None
    if (e.get(key) or "").strip():
        return (f"{key} 在这个终端里有，但后台服务里没有：手机审批的第二层 Jev（P157）不会生效。一步导入（不显示值）："
                f"`agentj service import-key {key}`，再 `agentj service restart`。 / {key} is set in this terminal but not "
                f"in the background service, so Jev (approval layer 2, P157) is off there. Import it in one step (the value "
                f"is never shown): `agentj service import-key {key}`, then `agentj service restart`.")
    target = tilde(path)
    return (f"后台服务没有 {key}：Jev（手机审批第二层，可选）不生效。要开：在Agent J 主机上运行 `agentj secret request --name {key} "
            f"--purpose 'OpenRouter key（Jev 审批第二层）' --dest env:{target}#{key} --verify-url https://openrouter.ai/api/v1/key`，"
            f"在手机的密钥卡片里贴上 key，再 `agentj service restart`。 / The background service has no {key}: Jev (optional "
            f"approval layer 2) is off. To turn it on: run `agentj secret request --name {key} --purpose 'OpenRouter key (Jev)' "
            f"--dest env:{target}#{key} --verify-url https://openrouter.ai/api/v1/key` on the Agent J host, paste the key on the "
            f"phone's secret card, then `agentj service restart`.")


# P423 #140: `import-key NAME` with NAME absent from this shell got Jev's OpenRouter template whatever NAME was. Jev's wording
# stays with Jev's key; a few well-known names get their vendor; any other name gets the neutral phone-card route.
# name → (purpose shown on the phone card, read-only verify URL or None, verify header or None)
KEY_SERVICES = {
    "OPENAI_API_KEY": ("OpenAI API key", "https://api.openai.com/v1/models", None),
    "CODEX_API_KEY": ("OpenAI API key（Codex）", "https://api.openai.com/v1/models", None),
    "ANTHROPIC_API_KEY": ("Anthropic API key（Claude Code）", None, None),
    TOKEN_ENV: ("Claude Code 登录 token（claude setup-token）/ Claude Code sign-in token", None, None),
}


def absent_key_note(n: str, key: str, environ: dict | None = None) -> str:
    """P423 #140: what to tell the owner when `agentj service import-key KEY` finds no KEY in this terminal. Names and commands
    only — never a value. Jev / OpenRouter wording only for Jev's own key (OPENROUTER_API_KEY)."""
    if key == JEV_KEY_ENV:
        return key_note(n, key, environ) or ""
    target = tilde(env_file(n, environ))
    purpose, verify, header = KEY_SERVICES.get(key, (f"{key}", None, None))
    card = f"agentj secret request --name {key} --purpose '{purpose}' --dest env:{target}#{key}"
    if verify:
        card += f" --verify-url {verify}" + (f" --verify-header '{header}'" if header else "")
    if key == TOKEN_ENV:
        return (f"{key} 是 Claude Code 的登录 token：在任意装了 Claude Code 的电脑上运行 `claude setup-token` 生成，再在Agent J 主机上"
                f"运行 `{card}`，在手机的密钥卡片里贴上，然后 `agentj service restart`。 / {key} is Claude Code's sign-in token: make "
                f"one with `claude setup-token` on any computer with Claude Code, run `{card}` on the Agent J host, paste it on "
                f"the phone's secret card, then `agentj service restart`.")
    if key in KEY_SERVICES:
        return (f"要把 {purpose} 存进后台服务：在Agent J 主机上运行 `{card}`，在手机的密钥卡片里贴上，再 `agentj service restart`。 / "
                f"To store the {purpose} for the background service: run `{card}` on the Agent J host, paste it on the phone's "
                f"secret card, then `agentj service restart`.")
    return (f"如果 {key} 是 OpenCode 配置（opencode.json）里引用的 key，先运行 `agentj provider list`，核对 key= 后面的变量名和它一字"
            f"不差。要从手机存进后台服务：在Agent J 主机上运行 `{card}`，在手机的密钥卡片里贴上，再 `agentj service restart`。 / If "
            f"{key} is a key referenced in OpenCode's config (opencode.json), run `agentj provider list` first and check the name "
            f"after key= matches it exactly. To store it for the background service from the phone: run `{card}` on the Agent J "
            f"host, paste it on the phone's secret card, then `agentj service restart`.")


def missing_key_message(key_name: str, environment="CLI") -> str:
    # Names are validated configuration fields; values never enter this message.
    if not isinstance(key_name, str) or not KEY_ENV_RE.fullmatch(key_name):
        key_name = "configured key"
    if environment == "service":
        if platform() == "macos":
            fix = ("write it locally to " + tilde(env_file(name())) +
                   " (owner-owned regular file, chmod 600; no symlink), then run `agentj service restart`")
        else:
            fix = "set it in the service environment file, then run `agentj service restart`"
    else:
        fix = "set it in this terminal's environment, then retry the command"
    return f"{key_name} is absent from the {environment} environment; {fix}; never paste the value in chat"


# ------------------------------------------------------------------ Linux: systemd --user
def _sd_quote(v: str) -> str:
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def unit_dir(environ: dict | None = None) -> str:
    e = os.environ if environ is None else environ
    base = e.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "systemd", "user")


def env_file(n: str, environ: dict | None = None) -> str:
    """The optional environment file the human may create (0600). Lives next to the unit: read-only for the fenced Agent."""
    if platform() == "macos":
        return os.path.join(os.path.expanduser("~"), "Library", "LaunchAgents", f"{n}.env")
    return os.path.join(unit_dir(environ), f"{n}.env")


def unit_text(n: str, argv: list[str], env: dict, environ: dict | None = None) -> str:
    lines = ["# Agent J host — written by `agentj service install`. No secrets here, by design.",
             "[Unit]", "Description=Agent J host (agentj serve): your phone <-> your own Agent",
             "StartLimitIntervalSec=300", "StartLimitBurst=20", "",
             "[Service]", "Type=simple",
             "ExecStart=" + " ".join(_sd_quote(x) for x in argv + SERVE_ARGS),
             "Restart=on-failure", "RestartSec=5", "TimeoutStopSec=20", "StandardInput=null"]
    lines += list(resource_policy.TRANSPORT_UNIT)
    lines += [f"Environment={_sd_quote(f'{k}={v}')}" for k, v in env.items() if k not in (*CAPTURED_ENV[1:], *CLAUDE_AUTH_ENV)]
    lines += ["# optional, created by you (0600) — e.g. CLAUDE_CODE_OAUTH_TOKEN=… ; the leading '-' = may be absent",
              "EnvironmentFile=-" + env_file(n, environ).replace("%", "%%"), "", "[Install]", "WantedBy=default.target", ""]
    return "\n".join(lines)


_SD = ("org.freedesktop.systemd1", "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager")


def _unit_path(unit: str) -> str:
    """D-Bus object path of a unit (systemd's bus_label_escape: every byte outside [A-Za-z0-9] → _xx)."""
    return "/org/freedesktop/systemd1/unit/" + "".join(c if c.isascii() and c.isalnum() else "_%02x" % ord(c) for c in unit)


def _bus_systemctl(args: tuple, timeout: int) -> subprocess.CompletedProcess | None:
    """F14: the same `systemctl --user` verbs over the user's session bus (busctl). Inside the Agent's fence (a private PID
    namespace) systemctl refuses the manager's private socket — its peer has no PID there (ENODATA) — while the bus
    broker relays fine. Only the verbs this module uses; None = not one of them, or no busctl."""
    exe = shutil.which("busctl")
    if not exe or not args:
        return None
    verb, rest = args[0], [a for a in args[1:] if not a.startswith("--")]
    unit = rest[0] if rest else ""

    def call(method, sig="", *vals):
        return subprocess.run([exe, "--user", "call", *_SD, method, *([sig, *map(str, vals)] if sig else [])],
                              capture_output=True, text=True, timeout=timeout)

    def prop(path, iface, name):
        r = subprocess.run([exe, "--user", "get-property", _SD[0], path, iface, name], capture_output=True, text=True, timeout=timeout)
        val = r.stdout.strip().split(" ", 1)[-1].strip('"') if r.returncode == 0 else ""
        return subprocess.CompletedProcess(r.args, r.returncode, val + "\n" if val else "", r.stderr)
    if verb == "show-environment":
        return prop(_SD[1], _SD[2], "Environment")
    if verb == "daemon-reload":
        return call("Reload")
    if verb == "enable" and unit:
        return call("EnableUnitFiles", "asbb", 1, unit, "false", "true")
    if verb == "restart" and unit:
        return call("RestartUnit", "ss", unit, "replace")
    if verb == "reset-failed" and unit:
        return call("ResetFailedUnit", "s", unit)
    if verb == "disable" and unit:
        r = call("DisableUnitFiles", "asb", 1, unit, "false")
        if "--now" in args:
            call("StopUnit", "ss", unit, "replace")
        return r
    if verb in ("is-enabled", "cat") and unit:
        r = call("GetUnitFileState", "s", unit)
        val = r.stdout.strip().split(" ", 1)[-1].strip('"') if r.returncode == 0 else ""
        return subprocess.CompletedProcess(r.args, 0 if val == "enabled" or (verb == "cat" and val) else 1, val + "\n", r.stderr)
    if verb == "is-active" and unit:
        r = prop(_unit_path(unit), "org.freedesktop.systemd1.Unit", "ActiveState")
        return subprocess.CompletedProcess(r.args, 0 if r.stdout.strip() == "active" else 3, r.stdout or "unknown\n", r.stderr)
    return None


# ------------------------------------------------------------------ P278: no systemd (WSL without it, containers, …)
NO_MANAGER = ("no_systemd", "wsl_systemd_off", "wsl_systemd_restart")
NO_MANAGER_EXIT = 78     # EX_CONFIG: `service install` / `--deferred` could not reach a service manager (update.py reads it)
WSL_CONF = "/etc/wsl.conf"
OSRELEASE = "/proc/sys/kernel/osrelease"
# what systemctl / systemd-run say when PID 1 is not systemd or there is no user manager to talk to
_NO_SD = re.compile(r"not been booted with systemd|Failed to connect to (?:user scope )?bus|"
                    r"No medium found|DBUS_SESSION_BUS_ADDRESS|XDG_RUNTIME_DIR", re.I)


def is_wsl(environ: dict | None = None, osrelease: str = OSRELEASE) -> bool:
    e = os.environ if environ is None else environ
    if e.get("WSL_DISTRO_NAME") or e.get("WSL_INTEROP"):
        return True
    try:
        return "microsoft" in Path(osrelease).read_text().lower()
    except OSError:
        return False


def wsl_systemd_enabled(path: str = WSL_CONF) -> bool:
    """/etc/wsl.conf `[boot]` `systemd=true` (WSL ≥ 0.67.6). Missing / unreadable / other value → False."""
    import configparser
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        cp.read_string(Path(path).read_text(errors="replace"))
    except (OSError, configparser.Error):
        return False
    return cp.get("boot", "systemd", fallback="").split("#")[0].strip().strip("\"'").lower() == "true"


def pid1_systemd() -> bool:
    """sd_booted(3): /run/systemd/system exists only when PID 1 is systemd; /proc/1/comm as a second witness."""
    if os.path.isdir("/run/systemd/system"):
        return True
    try:
        return Path("/proc/1/comm").read_text().strip() == "systemd"
    except OSError:
        return False


def classify_no_manager(environ: dict | None = None, wsl_conf: str = WSL_CONF, osrelease: str = OSRELEASE) -> str:
    """Why there is no systemd user manager here (call only after reaching it failed): wsl_systemd_off (WSL, wsl.conf
    does not turn systemd on) · wsl_systemd_restart (it does, but this WSL started before: `wsl --shutdown`) ·
    no_systemd (PID 1 is not systemd: a container, another init) · no_user_manager (systemd runs, the user manager not)."""
    if is_wsl(environ, osrelease):
        return "wsl_systemd_restart" if wsl_systemd_enabled(wsl_conf) and not pid1_systemd() else (
            "wsl_systemd_off" if not pid1_systemd() else "no_user_manager")
    return "no_user_manager" if pid1_systemd() else "no_systemd"


def systemd_problem() -> str | None:
    """None = `systemctl --user` answers (directly or over the bus inside the fence); else classify_no_manager()."""
    if platform() != "linux":
        return None
    try:
        if _systemctl("show-environment", timeout=10).returncode == 0:
            return None
    except ServiceError:
        pass
    except (OSError, subprocess.TimeoutExpired):
        pass
    return classify_no_manager()


def user_runtime_dir(environ=None) -> str | None:
    """P329 live (Olares: its sshd runs without PAM): an SSH login gets no XDG_RUNTIME_DIR although the user manager runs
    (linger), and `systemctl --user` says "No medium found". The manager's own socket /run/user/<uid>/bus (ours, a socket)
    is the standard place: set XDG_RUNTIME_DIR to its folder for this process and its children. → the folder set, or None."""
    import stat
    e = os.environ if environ is None else environ
    if e.get("XDG_RUNTIME_DIR") or platform() != "linux" or not hasattr(os, "getuid"):
        return None
    d = f"/run/user/{os.getuid()}"
    try:
        st = os.lstat(d + "/bus")
    except OSError:
        return None
    if st.st_uid != os.getuid() or not stat.S_ISSOCK(st.st_mode):
        return None
    e["XDG_RUNTIME_DIR"] = d
    return d


def _systemctl(*args: str, check: bool = False, timeout: int = 30) -> subprocess.CompletedProcess:
    user_runtime_dir()
    exe = shutil.which("systemctl")
    if not exe:
        raise ServiceError(classify_no_manager() if platform() == "linux" else "no_systemctl")
    r = subprocess.run([exe, "--user", *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0 and "via local transport" in (r.stderr or ""):   # F14: inside the Agent's fence → the bus
        r = _bus_systemctl(args, timeout) or r
    if check and r.returncode != 0 and _NO_SD.search(r.stderr or ""):
        reason = classify_no_manager()
        if reason in NO_MANAGER:
            raise ServiceError(reason, (r.stderr or "").strip()[:300])
    if check and r.returncode != 0:
        raise ServiceError("systemctl_failed", f"systemctl --user {' '.join(args)}: {(r.stderr or r.stdout).strip()[:300]}")
    return r


def linger_needed(environ=None) -> bool:
    e = os.environ if environ is None else environ
    return not (bool(e.get("DISPLAY") or e.get("WAYLAND_DISPLAY")) and not e.get("SSH_CONNECTION"))


def _linger() -> str | None:
    """"yes" / "no" / None (not a systemd-logind system). `loginctl show-user` fails for a user with no session at all
    (a fresh server before the first login, a container): then logind's own record, /var/lib/systemd/linger/<user>."""
    exe = shutil.which("loginctl")
    user = os.environ.get("USER") or ""
    if not user:
        import getpass
        with contextlib.suppress(Exception):
            user = getpass.getuser()
    if not exe or not user:
        return None
    try:
        r = subprocess.run([exe, "show-user", user, "-p", "Linger", "--value"], capture_output=True, text=True, timeout=10)
        if r.stdout.strip() in ("yes", "no"):
            return r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "yes" if os.path.exists(os.path.join("/var/lib/systemd/linger", user)) else "no"


# ------------------------------------------------------------------ macOS: LaunchAgent
def plist_path(n: str) -> str:
    return os.path.join(os.path.expanduser("~"), "Library", "LaunchAgents", f"{n}.plist")


# P323: one-shot jobs (recovery / update / autoupdate / switch, label net.agentj.<purpose>.<uuid32>) are bootstrapped from
# here, not from ~/Library/LaunchAgents. macOS lists every plist in LaunchAgents under System Settings → Login Items →
# "App Background Activity" (one "agentj · unidentified developer" row each, plus a "Background Items Added" notice), and
# a job left behind there re-ran at every login (RunAtLoad). A plist bootstrapped from this directory runs once and is
# never loaded again at login. Outside the state directory on purpose: a fenced caller (F14) cannot write that one.
JOB_RX = re.compile(r"net\.agentj\.(?:recovery|update|autoupdate|switch)\.[a-f0-9]{32}")
JOB_GRACE = 120.0    # s: a just-written job may not be running yet (bootstrap → spawn); leave it alone


def job_dir() -> str:
    return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Agent J", "jobs")


def job_plist_path(label: str) -> str:
    return os.path.join(job_dir(), f"{label}.plist")


def _job_running(label: str) -> bool:
    try:
        r = _launchctl("print", f"{_gui()}/{label}", timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return True      # cannot tell: keep it
    return r.returncode == 0 and bool(re.search(r"^\s*state = running", r.stdout, re.M))


def sweep_jobs(*, everything: bool = False, now: float | None = None) -> list[str]:
    """P323: unload + delete finished one-shot job plists — those older versions left in ~/Library/LaunchAgents (each one
    a Login Items row) and any in job_dir() whose job died before its own cleanup. Never the running job, never the
    caller's own job, never one younger than JOB_GRACE; `everything` (uninstall) takes running ones too, still not the
    caller's own. → the labels removed. macOS only; never raises."""
    import time
    if platform() != "macos":
        return []
    now = time.time() if now is None else now
    own = os.environ.get("AGENTJ_JOB_LABEL", "")
    removed = []
    for d in (os.path.dirname(plist_path("x")), job_dir()):
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for f in names:
            label = f[:-6] if f.endswith(".plist") else ""
            if not JOB_RX.fullmatch(label) or label == own:
                continue
            p = os.path.join(d, f)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if not everything and (now - st.st_mtime < JOB_GRACE or _job_running(label)):
                continue
            with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                _launchctl("bootout", f"{_gui()}/{label}", timeout=10)
            with contextlib.suppress(OSError):
                os.unlink(p)
                removed.append(label)
    return removed


# P331 (0.22.1a1 掉线): "Background" put serve in darwinbg — PRI 4, the efficiency cores only (2 on an M1 Max), throttled
# disk I/O, coalesced timers. Under the owner's daily load (15–25) a 1 s sleep came back up to 53 s late: every phone heartbeat
# and relay ping missed its deadline (dev_gone / relay_down / reconnect storms). Measured on that Mac, same 50 ms tick:
# Background avg 229 ms / worst 2557 ms, Standard 105 / 249, Interactive 27 / 59. serve answers a person waiting on a phone.
PROCESS_TYPE = "Interactive"


def plist_bytes(n: str, argv: list[str], env: dict, log_path: str, extra: dict | None = None) -> bytes:
    return plistlib.dumps({"Label": n, "ProgramArguments": argv + SERVE_ARGS, "EnvironmentVariables": {k: v for k, v in env.items() if k not in (*CAPTURED_ENV[1:], *CLAUDE_AUTH_ENV)},
                           "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 5, "ProcessType": PROCESS_TYPE,
                           "StandardInPath": "/dev/null", "StandardOutPath": log_path, "StandardErrorPath": log_path, **(extra or {})})


def _launchctl(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=timeout)


def _gui() -> str:
    return f"gui/{os.getuid()}"


# P63: `bootout` returns before launchd has finished unloading the label; a `bootstrap` right after it fails with
# "Bootstrap failed: 5: Input/output error" (a paying customer's upgrade, 2026-10-06). Wait — bounded — until the
# label is really gone.
BOOTOUT_WAIT = 10.0


def _loaded(target: str) -> bool:
    try:
        return _launchctl("print", target, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _bootout_wait(n: str, wait: float | None = None) -> bool:
    """`launchctl bootout gui/<uid>/<label>`, then poll until it is unloaded (at most BOOTOUT_WAIT s). → True = gone."""
    import time
    target = f"{_gui()}/{n}"
    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
        _launchctl("bootout", target)
    end = time.monotonic() + (BOOTOUT_WAIT if wait is None else wait)
    while _loaded(target):
        if time.monotonic() >= end:
            return False
        time.sleep(0.25)
    return True


def _bootstrap(n: str, path: str) -> subprocess.CompletedProcess:
    """bootout → wait until unloaded → bootstrap; a failure (5 = the label was still being torn down) gets one more round."""
    for _ in range(2):
        _bootout_wait(n)
        r = _launchctl("bootstrap", _gui(), path)
        if r.returncode == 0:
            return r
    return r


# ------------------------------------------------------------------ the service verbs
def _browser_note(st, notes):
    from . import browser
    try:
        browser.refresh_service(st)
    except Exception:  # browser failure must not break the host upgrade
        notes.append("浏览器服务需要检查：agentj browser setup / Check the browser service: agentj browser setup")


def _register_pair_protocol(notes):
    try:
        from . import protocol_handler
        from .instance import current
        if current() > 1 and protocol_handler.installed():
            return      # P180: one handler per computer; it finds the instance by the link's channel
        protocol_handler.install()
    except (OSError, ValueError, subprocess.SubprocessError):
        notes.append("本机配对链接注册失败：运行 agentj protocol install 重试；仍可用 agentj admin 配对 / Pairing link registration failed: retry agentj protocol install, or use agentj admin")


def install(st) -> dict:
    """Write + enable + start. → {"kind", "name", "path", "argv", "notes"}; raises ServiceError."""
    n, plat = name(), platform()
    argv = agentj_argv()
    notes = []
    if not os.environ.get("HOME"):
        raise ServiceError("no_home")
    if os.environ.get(TOKEN_ENV):
        notes.append(token_hint(n))
    if plat == "linux":
        r = _systemctl("show-environment", timeout=10)
        if r.returncode != 0:
            raise ServiceError(classify_no_manager(), (r.stderr or "").strip()[:300])
        d = unit_dir()
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"{n}.service")
        env, selected_notes = prepare_binary_env(n, previous=previous_binary_selection(st, n))
        notes.extend(selected_notes)
        old = remove_legacy()         # stops the old serve first: its state directory can then move (migrate.py)
        if old:
            notes.append(f"旧服务 {old['name']} 已停止并删除 / the old service {old['name']} was stopped and removed")
        _write(path, unit_text(n, argv, env).encode(), 0o644)
        _systemctl("daemon-reload", check=True)
        _systemctl("enable", f"{n}.service", check=True)
        _systemctl("restart", f"{n}.service", check=True)   # starts it, or picks up a rewritten unit (reinstall / upgrade)
        remember_binary_selection(st, n)
        _register_pair_protocol(notes)
        _browser_note(st, notes)      # P521: migrate browser policy without opening a window
        _rescue_note(st, notes)       # P332: the rescue watchdog's unit (install and every upgrade pass here)
        if _linger() == "no":
            notes.append("常驻（包括多用户桌面）：`loginctl enable-linger $USER`；需要管理员时用 agentj sudo 手机密码卡 / "
                         "enable linger even on a shared desktop: otherwise logout stops this seat")
        return {"kind": "systemd", "name": n, "path": path, "argv": argv + SERVE_ARGS, "notes": notes}
    if plat == "macos":
        path = plist_path(n)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        env, selected_notes = prepare_binary_env(n, previous=previous_binary_selection(st, n))
        notes.extend(selected_notes)
        old = remove_legacy()
        if old:
            notes.append(f"旧服务 {old['name']} 已停止并删除 / the old service {old['name']} was stopped and removed")
        # P327: through the signed "Agent J.app" launcher when available (Login Items shows "Agent J"), else as before
        argv, extra = macapp.wrap(argv, log=getattr(st, "log", None))
        if not extra:
            notes.append("签名 App 暂不可用，登录项仍可能显示身份不明；联网后运行 agentj service install 重试 / Signed app unavailable; Login Items may still show an unidentified developer. Retry agentj service install when online.")
        extra["Comment"] = "Agent J — Host"
        _write(path, plist_bytes(n, argv, env, str(st.root / "service.log"), extra), 0o644)
        # `launchctl enable` writes a permanent record into launchd's override database that only root can remove: call it
        # only when the label is disabled there (a bootstrap would fail), remember that, and put it back on uninstall
        if launchd_disabled(n) is True:
            _launchctl("enable", f"{_gui()}/{n}")
            _remember_enabled(st, n)
        r = _bootstrap(n, path)
        if r.returncode != 0:
            raise ServiceError("launchctl_failed", (r.stderr or r.stdout).strip()[:300])
        remember_binary_selection(st, n)
        swept = sweep_jobs()          # P323: one-shot jobs older versions left in LaunchAgents (Login Items rows)
        if swept:
            with contextlib.suppress(Exception):
                st.log("launchd_jobs_swept", count=len(swept))
        _register_pair_protocol(notes)
        _browser_note(st, notes)      # P521: migrate browser policy without opening a window
        _rescue_note(st, notes)       # P332
        migrated = migrate_calendar_jobs(st)
        if migrated:
            notes.append(f"已迁移 {len(migrated)} 个每周任务登录项 / Migrated {len(migrated)} weekly task login items")
        notes.append(login_reminder())
        return {"kind": "launchd", "name": n, "path": path, "argv": argv + SERVE_ARGS, "notes": notes}
    raise ServiceError("unsupported_os")


# ------------------------------------------------------------------ P332 (§33): the rescue watchdog's own unit
RESCUE_LABEL = "net.agentj.rescue"
RESCUE_ARGS = ["-P", "-m", "agentj.rescue_watchdog"]
RESCUE_ENV = ("PATH", "AGENTJ_STATE_DIR", "AGENTJ_INSTANCE", "AGENTJ_SERVICE_NAME", *PROXY_ENV)


def rescue_name(n: str | None = None) -> str:
    """agentj → agentj-rescue · agentj-N → agentj-N-rescue · net.agentj.host[.N] → net.agentj.rescue[.N] · other → <n>.rescue."""
    n = n or name()
    if platform() == "macos":
        if n == DEFAULT_LABEL or n.startswith(DEFAULT_LABEL + "."):
            return RESCUE_LABEL + n[len(DEFAULT_LABEL):]
        return n + ".rescue"
    return n + "-rescue"


def rescue_argv() -> list[str]:
    """Linux: this interpreter directly at the watchdog module. macOS: signed-app-compatible CLI fast dispatch,
    before any heavy CLI imports. `-P` on module launches keeps the working directory off sys.path."""
    if platform() == "macos":
        return [*agentj_argv(), "rescue", "watchdog"]
    return [os.path.abspath(sys.executable), *RESCUE_ARGS]


def rescue_env(environ: dict | None = None) -> dict:
    """Paths and credential-free proxies only (service_env), plus the serve unit's name so the watchdog restarts the right one."""
    env, _ = service_env(environ)
    e = os.environ if environ is None else environ
    if getenv("AGENTJ_SERVICE_NAME", None, e):
        env["AGENTJ_SERVICE_NAME"] = name()
    return {k: v for k, v in env.items() if k in RESCUE_ENV}


def rescue_unit_text(n: str, argv: list[str], env: dict) -> str:
    """Independent of the serve unit on purpose (no PartOf / BindsTo / Requires): `systemctl --user stop agentj` must leave
    it running — it exists for the time serve is not."""
    lines = ["# Agent J rescue watchdog — written by `agentj service install` / `agentj rescue install`. No secrets here, by design.",
             "[Unit]", "Description=Agent J rescue watchdog: keeps the phone's emergency repair reachable when Agent J stops answering",
             "StartLimitIntervalSec=0", "",
             "[Service]", "Type=simple",
             "ExecStart=" + " ".join(_sd_quote(x) for x in argv),
             "Restart=always", "RestartSec=10", "TimeoutStopSec=10", "StandardInput=null",
             "TasksMax=64", *resource_policy.TRANSPORT_UNIT]
    lines += [f"Environment={_sd_quote(f'{k}={v}')}" for k, v in env.items()]
    lines += ["", "[Install]", "WantedBy=default.target", ""]
    return "\n".join(lines)


def rescue_plist_bytes(n: str, argv: list[str], env: dict, log_path: str, extra: dict | None = None) -> bytes:
    return plistlib.dumps({"Label": n, "ProgramArguments": argv, "EnvironmentVariables": env,
                           "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 10, "ProcessType": PROCESS_TYPE, **(extra or {}),
                           "StandardInPath": "/dev/null", "StandardOutPath": log_path, "StandardErrorPath": log_path})


def rescue_path(r: str) -> str:
    return plist_path(r) if platform() == "macos" else os.path.join(unit_dir(), f"{r}.service")


def install_rescue(st) -> dict:
    """Write (or rewrite) + enable + (re)start the watchdog unit. Idempotent: `agentj service install`, every upgrade (the
    recovery worker calls install()) and `agentj rescue install` all land here. Raises ServiceError."""
    r, plat = rescue_name(), platform()
    argv, env = rescue_argv(), rescue_env()
    if plat == "linux":
        path = rescue_path(r)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _write(path, rescue_unit_text(r, argv, env).encode(), 0o644)
        _systemctl("daemon-reload", check=True)
        _systemctl("enable", f"{r}.service", check=True)
        _systemctl("reset-failed", f"{r}.service")
        _systemctl("restart", f"{r}.service", check=True)      # picks up new code after an upgrade
        return {"kind": "systemd", "name": r, "path": path}
    if plat == "macos":
        path = rescue_path(r)
        new = not os.path.exists(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        argv, extra = macapp.wrap(argv, log=getattr(st, "log", None))
        extra["Comment"] = "Agent J — Rescue watchdog"
        _write(path, rescue_plist_bytes(r, argv, env, str(st.root / "rescue-service.log"), extra), 0o644)
        if launchd_disabled(r) is True:
            _launchctl("enable", f"{_gui()}/{r}")
            _remember_enabled(st, r)
        res = _bootstrap(r, path)
        if res.returncode != 0:
            raise ServiceError("launchctl_failed", (res.stderr or res.stdout).strip()[:300])
        return {"kind": "launchd", "name": r, "path": path, "new": new}
    raise ServiceError("unsupported_os")


# macOS shows a new login item ("Background Items Added") the first time the watchdog is registered: say what it is.
MAC_LOGIN_ITEM_NOTE = ("macOS 可能提示新增了一个登录项 / 后台项目：那是「Agent J 救援守护」——Agent J 主机上的 Agent J 卡住时，让手机还能救它。"
                       "请保持开启 / macOS may say a login item (background item) was added: it is the Agent J rescue "
                       "watchdog — when Agent J on the Agent J host gets stuck, your phone can still rescue it. Keep it on.")


def uninstall_rescue(st=None) -> dict:
    r, plat = rescue_name(), platform()
    path = rescue_path(r)
    existed = os.path.exists(path)
    if plat == "linux":
        if existed or _systemctl("cat", f"{r}.service", timeout=10).returncode == 0:
            _systemctl("disable", "--now", f"{r}.service")
        if existed:
            os.unlink(path)
        _systemctl("daemon-reload")
        _systemctl("reset-failed", f"{r}.service")
        return {"kind": "systemd", "name": r, "path": path, "removed": existed}
    if plat == "macos":
        _launchctl("bootout", f"{_gui()}/{r}")
        if existed:
            os.unlink(path)
        if st is not None:
            with contextlib.suppress(OSError):
                _restore_disabled(st, r)
        return {"kind": "launchd", "name": r, "path": path, "removed": existed}
    raise ServiceError("unsupported_os")


def rescue_status() -> dict:
    """{"kind", "name", "path", "installed", "active"}; never raises."""
    try:
        r = rescue_name()
    except ServiceError:
        return {"kind": "none", "name": None, "installed": False, "active": "bad_name"}
    plat = platform()
    if plat == "linux":
        path = rescue_path(r)
        if not shutil.which("systemctl"):
            return {"kind": "none", "name": r, "path": path, "installed": os.path.exists(path), "active": "no_systemctl"}
        try:
            act = _systemctl("is-active", f"{r}.service", timeout=10).stdout.strip() or "unknown"
        except (OSError, subprocess.TimeoutExpired, ServiceError):
            act = "unknown"
        return {"kind": "systemd", "name": r, "path": path, "installed": os.path.exists(path), "active": act}
    if plat == "macos":
        path = rescue_path(r)
        try:
            res = _launchctl("print", f"{_gui()}/{r}", timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return {"kind": "launchd", "name": r, "path": path, "installed": os.path.exists(path), "active": "unknown"}
        m = re.search(r"^\s*state = (\S+)", res.stdout, re.M) if res.returncode == 0 else None
        act = "active" if m and m.group(1) == "running" else ("inactive" if res.returncode == 0 else "not_loaded")
        return {"kind": "launchd", "name": r, "path": path, "installed": os.path.exists(path), "active": act}
    return {"kind": "none", "name": r, "installed": False, "active": "unsupported_os"}


def _rescue_stop() -> None:
    """`agentj service stop` is an intentional stop: the watchdog goes quiet too, or it would take the channel over and
    restart serve ~45 s later (review P332 MEDIUM-2). Never raises; the unit stays installed (start brings it back)."""
    with contextlib.suppress(ServiceError, OSError, subprocess.SubprocessError):
        r, plat = rescue_name(), platform()
        if plat == "linux" and os.path.exists(rescue_path(r)):
            _systemctl("stop", f"{r}.service", timeout=20)
        elif plat == "macos" and os.path.exists(rescue_path(r)):
            _launchctl("bootout", f"{_gui()}/{r}")


def _rescue_start() -> None:
    """`service start` / `restart` bring an installed watchdog back (a no-op when it runs). Not installed (or removed with
    `agentj rescue uninstall`) stays so: only `service install` / `rescue install` register it. Never raises."""
    with contextlib.suppress(ServiceError, OSError, subprocess.SubprocessError):
        r, plat = rescue_name(), platform()
        if plat == "linux" and os.path.exists(rescue_path(r)):
            _systemctl("start", f"{r}.service", timeout=20)
        elif plat == "macos" and os.path.exists(rescue_path(r)) and not _loaded(f"{_gui()}/{r}"):
            _bootstrap(r, rescue_path(r))


def _rescue_note(st, notes: list) -> None:
    """install(): the watchdog comes along; its failure never fails the serve install (one plain note instead)."""
    try:
        if install_rescue(st).get("new"):
            notes.append(MAC_LOGIN_ITEM_NOTE)
    except (ServiceError, OSError, subprocess.SubprocessError):
        notes.append("救援守护没装上（不影响正常使用）：运行 agentj rescue install 重试 / the rescue watchdog was not installed "
                     "(Agent J works without it): retry with agentj rescue install")


_DISABLED_RX = r'^\s*"{label}"\s*=>\s*(disabled|enabled|true|false)\s*$'


def launchd_disabled(n: str) -> bool | None:
    """From `launchctl print-disabled gui/<uid>`: True = the label is disabled, False = an explicit "enabled" record,
    None = no record (the default: enabled) or launchctl could not answer. Both formats: `=> disabled|enabled` (current
    macOS) and `=> true|false` (older; true = disabled)."""
    try:
        r = _launchctl("print-disabled", _gui(), timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    m = re.search(_DISABLED_RX.format(label=re.escape(n)), r.stdout or "", re.M)
    return None if not m else m.group(1) in ("disabled", "true")


def _marker(st) -> str:
    return str(st.root / "launchd-enabled.json")   # labels we enabled from "disabled" (to put back on uninstall)


def _remember_enabled(st, n: str) -> None:
    import json
    p = _marker(st)
    try:
        with open(p, encoding="utf-8") as f:
            labels = set(json.load(f).get("labels") or [])
    except (OSError, ValueError, AttributeError):
        labels = set()
    labels.add(n)
    _write(p, json.dumps({"labels": sorted(labels)}).encode(), 0o600)


def _restore_disabled(st, n: str) -> bool:
    """Uninstall: a label we had to enable was disabled before — disable it again (that restores the record that was
    there, it adds none). → True when it did."""
    import json
    p = _marker(st)
    try:
        with open(p, encoding="utf-8") as f:
            labels = set(json.load(f).get("labels") or [])
    except (OSError, ValueError, AttributeError):
        return False
    if n not in labels:
        return False
    _launchctl("disable", f"{_gui()}/{n}")
    labels.discard(n)
    if labels:
        _write(p, json.dumps({"labels": sorted(labels)}).encode(), 0o600)
    else:
        os.unlink(p)
    return True


def restart() -> dict:
    """Restart only the named Agent J service; never a relay or foreign harness."""
    n = name()
    if platform() == "linux":
        _systemctl("restart", f"{n}.service", check=True)
    elif platform() == "macos":
        r = _launchctl("kickstart", "-k", f"{_gui()}/{n}")
        if r.returncode and os.path.exists(plist_path(n)):
            r = _bootstrap(n, plist_path(n))     # P63: not loaded (a failed bootstrap before): load it again, the same way
        if r.returncode:
            raise ServiceError("launchctl_failed", (r.stderr or r.stdout or "").strip()[:300])
    else:
        raise ServiceError("unsupported_os")
    _rescue_start()           # P332: back after an `agentj service stop`
    return {"name": n}


def needs_deferred_install(cgroup=None) -> bool:
    """New CLI protects old apply callers that don't know --deferred yet.

    PID namespaces hide the host's ancestor PID, but not Linux cgroup ownership.
    macOS sandboxed callers cannot inspect outside ancestors: an active existing
    LaunchAgent is always re-installed by a separate manager job.
    """
    if platform() == "macos":
        return any(s.get("installed") and s.get("active") == "active"
                   for s in (status(), legacy_status()))
    if platform() == "linux":
        try:
            text = Path("/proc/self/cgroup").read_text() if cgroup is None else cgroup
        except OSError:
            return False
        units = {name() + ".service"}
        if legacy_name():
            units.add(legacy_name() + ".service")
        return any(units.intersection(line.split(":", 2)[-1].split("/")) for line in text.splitlines())
    return False


def start(st) -> dict:
    """Start an installed unit; otherwise install it. No unload of a running job."""
    n, plat = name(), platform()
    current = status()
    if not current.get("installed"):
        result = install(st)
        _acknowledge_recovery(st)
        return result
    if current.get("active") == "active":
        _rescue_start()       # P332
        _acknowledge_recovery(st)
        return {"name": n}
    if plat == "linux":
        _systemctl("start", f"{n}.service", check=True)
    elif plat == "macos":
        if not _loaded(f"{_gui()}/{n}"):
            if launchd_disabled(n) is True:
                _launchctl("enable", f"{_gui()}/{n}")
                _remember_enabled(st, n)
            r = _launchctl("bootstrap", _gui(), plist_path(n))
        else:
            r = _launchctl("kickstart", f"{_gui()}/{n}")
        if r.returncode:
            raise ServiceError("launchctl_failed")
    else:
        raise ServiceError("unsupported_os")
    _rescue_start()           # P332: `service stop` stopped the watchdog too
    _acknowledge_recovery(st)
    return {"name": n}


def _acknowledge_recovery(st):
    """Manual start acknowledges a completed failure, including rollback to an older version."""
    from . import __version__, service_recovery
    rec = service_recovery.failure(st)
    if rec and status().get("active") == "active":
        service_recovery.record(st, "ok", __version__)
        (st.root / service_recovery.NOTICE).unlink(missing_ok=True)


def stop() -> dict:
    """Stop without deleting installation. launchd KeepAlive requires bootout. The rescue watchdog stops first (an
    intentional stop must not be "repaired" by it; start / restart / install bring it back)."""
    n, plat = name(), platform()
    if plat in ("linux", "macos"):
        _rescue_stop()
    if plat == "linux":
        _systemctl("stop", f"{n}.service", check=True)
    elif plat == "macos":
        if not _bootout_wait(n):
            raise ServiceError("launchctl_failed")
    else:
        raise ServiceError("unsupported_os")
    return {"name": n}


def uninstall(st=None) -> dict:
    n, plat = name(), platform()
    if plat in ("linux", "macos"):
        with contextlib.suppress(ServiceError, OSError, subprocess.SubprocessError):
            uninstall_rescue(st)      # P332: the watchdog goes with the service
    if plat == "linux":
        path = os.path.join(unit_dir(), f"{n}.service")
        existed = os.path.exists(path)
        if existed or _systemctl("cat", f"{n}.service", timeout=10).returncode == 0:
            _systemctl("disable", "--now", f"{n}.service")
        if existed:
            os.unlink(path)
        _systemctl("daemon-reload")
        _systemctl("reset-failed", f"{n}.service")
        return {"kind": "systemd", "name": n, "path": path, "removed": existed}
    if plat == "macos":
        path = plist_path(n)
        _launchctl("bootout", f"{_gui()}/{n}")
        existed = os.path.exists(path)
        if existed:
            os.unlink(path)
        restored = False
        if st is not None:
            with contextlib.suppress(OSError):
                restored = _restore_disabled(st, n)
        jobs = sweep_jobs(everything=True)        # P323: no one-shot job plist outlives the uninstall
        with contextlib.suppress(OSError):
            os.rmdir(job_dir())
        app = macapp.remove_if_unused()           # P327: the launcher app goes with the last LaunchAgent using it
        return {"kind": "launchd", "name": n, "path": path, "removed": existed, "restored_disabled": restored,
                "jobs_removed": len(jobs), "app_removed": app}
    raise ServiceError("unsupported_os")


def status() -> dict:
    """{"kind", "name", "path", "installed": bool, "active": "active" | "inactive" | "failed" | …}. Never raises for
    a missing service manager: kind = "none"."""
    try:
        n = name()
    except ServiceError:
        return {"kind": "none", "name": None, "installed": False, "active": "bad_name"}
    plat = platform()
    if plat == "linux":
        path = os.path.join(unit_dir(), f"{n}.service")
        if not shutil.which("systemctl"):
            return {"kind": "none", "name": n, "path": path, "installed": os.path.exists(path), "active": "no_systemctl"}
        try:
            act = _systemctl("is-active", f"{n}.service", timeout=10).stdout.strip() or "unknown"
            en = _systemctl("is-enabled", f"{n}.service", timeout=10).stdout.strip() or "unknown"
        except (OSError, subprocess.TimeoutExpired):
            act, en = "unknown", "unknown"
        return {"kind": "systemd", "name": n, "path": path, "installed": os.path.exists(path), "active": act, "enabled": en}
    if plat == "macos":
        path = plist_path(n)
        try:
            r = _launchctl("print", f"{_gui()}/{n}", timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return {"kind": "launchd", "name": n, "path": path, "installed": os.path.exists(path), "active": "unknown"}
        m = re.search(r"^\s*state = (\S+)", r.stdout, re.M) if r.returncode == 0 else None
        act = "active" if m and m.group(1) == "running" else ("inactive" if r.returncode == 0 else "not_loaded")
        return {"kind": "launchd", "name": n, "path": path, "installed": os.path.exists(path), "active": act}
    return {"kind": "none", "name": n, "installed": False, "active": "unsupported_os"}


def token_hint(n: str) -> str:
    f = tilde(env_file(n))
    return ("Agent J 主机上的服务没有 Claude 登录：在Agent J 主机终端运行 `claude` 并 /login 一次（macOS 存进钥匙串），"
            "或运行 `claude setup-token` 后自己写入服务专用 env 文件 " + f + "（chmod 600，"
            "CLAUDE_CODE_OAUTH_TOKEN=<token>），再运行 `agentj service restart`。不要把 token 发到聊天里。 / "
            "the Agent J host's service has no Claude login. Run `claude` and /login in a computer terminal "
            "(Keychain on macOS), or run `claude setup-token` and write CLAUDE_CODE_OAUTH_TOKEN=<token> "
            "to the service env file " + f + " yourself (chmod 600), then `agentj service restart`. "
            "Never paste the token into chat.")


def tilde(p: str) -> str:
    h = os.path.expanduser("~").rstrip("/")
    return "~" + p[len(h):] if h and (p == h or p.startswith(h + "/")) else p


def _write(path: str, data: bytes, mode: int) -> None:
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


MESSAGES = {
    "bad_service_env": "Service env must be an owner-owned regular 0600 file with valid assignments; no symlinks. Fix ~/Library/LaunchAgents/net.agentj.host.env locally (chmod 600), then run `agentj service restart`; never paste values in chat",
    "bad_key_name": "变量名不合规（大写字母、数字、下划线）/ not a variable name (A–Z, 0–9, _)",
    "bad_claude_env": "Claude 服务 env 文件须为主人所有的普通 0600 文件，且赋值格式正确 / Claude service env must be an owner-owned regular 0600 file with valid assignments",
    "bad_binary_env": "AGENTJ_*_BIN 路径格式无效：检查服务 env 文件 / invalid harness path in service environment file",
    "bad_name": "服务名不合规（AGENTJ_SERVICE_NAME） / invalid service name",
    "no_systemctl": "没有 systemctl：这台 Linux 不用 systemd，自己用 tmux / supervisord 跑 `agentj serve` / no systemd here",
    # P278: no systemd → one plain sentence + the fix; the fallback is the existing one (`agentj serve` in the foreground)
    "no_systemd": "这台 Linux 没有在用 systemd（容器或别的 init），Agent J 没法装成后台服务。改用前台运行：在 tmux / screen 里运行 "
                  "`agentj serve`，关掉终端前先 detach；升级后在那里 Ctrl-C 再重新运行一次 / This Linux does not run systemd "
                  "(a container or another init), so Agent J cannot be installed as a background service. Run it in the foreground "
                  "instead: `agentj serve` inside tmux / screen (detach before closing the terminal); after an upgrade, Ctrl-C it and "
                  "run it again",
    "wsl_systemd_off": "这个 WSL 没有开启 systemd，Agent J 没法装成后台服务。开启只要两步：① 在 WSL 里运行 "
                       "`printf '[boot]\\nsystemd=true\\n' | sudo tee -a /etc/wsl.conf`；② 在 Windows PowerShell 里运行 "
                       "`wsl --shutdown`，再重新打开 WSL，然后运行 `agentj service install`。暂时不想开：在 tmux 里运行 `agentj serve` "
                       "（前台运行，关窗口前 detach） / systemd is off in this WSL, so Agent J cannot be installed as a background "
                       "service. Two steps turn it on: (1) in WSL run `printf '[boot]\\nsystemd=true\\n' | sudo tee -a "
                       "/etc/wsl.conf`; (2) in Windows PowerShell run `wsl --shutdown`, reopen WSL, then run `agentj service install`. "
                       "Not now: run `agentj serve` inside tmux (foreground; detach before closing the window)",
    "wsl_systemd_restart": "/etc/wsl.conf 已经开启 systemd，但这个 WSL 是在那之前启动的，还没生效。在 Windows PowerShell 里运行 "
                           "`wsl --shutdown`，再重新打开 WSL，然后运行 `agentj service install` / /etc/wsl.conf turns systemd on, but "
                           "this WSL started before that and it is not active yet. In Windows PowerShell run `wsl --shutdown`, reopen "
                           "WSL, then run `agentj service install`",
    "no_user_manager": "systemd 用户实例不可用（没有登录会话？）：先 `loginctl enable-linger $USER` 再重新登录 / "
                       "no systemd user manager: run `loginctl enable-linger $USER` and log in again",
    "systemctl_failed": "systemctl 失败 / systemctl failed",
    "launchctl_failed": "launchctl 失败 / launchctl failed",
    "unsupported_os": "这个系统还不支持（Windows 请在 WSL2 里装） / unsupported OS (Windows: use WSL2)",
    "no_home": "没有 HOME / HOME is not set",
}

MESSAGES.update({
    "recovery_launch_failed": "升级重启任务未启动：运行 agentj service start，再运行 agentj doctor / Could not launch upgrade recovery: run agentj service start, then agentj doctor",
    "recovery_version_mismatch": "升级目标版本不一致：运行 agentj update check / Upgrade target mismatch: run agentj update check",
})


def login_reminder(lang=None):
    """Shown at the Agent J host only (service install, `agentj doctor`): never pushed to the phone (P287). The phone shows its
    own offline line when the Agent J host really is unreachable."""
    zh = "提醒一下：这台 Mac 重启后，要先登录一次您的账户，我才能在后台继续干活；登录后切到别的用户，我照常运行。"
    en = "A reminder: after this Mac restarts, log in to your account once so I can keep working in the background; after that you can switch to another user and I keep running."
    return en if lang == "en" else zh if lang == "zh" else zh + " / " + en


def host_platform() -> str:
    """What the phone is told in `ready` (PROTOCOL §10.0 `plat`): "macos" | "linux" | "windows" (WSL2) | "other"."""
    p = platform()
    if p == "linux":
        with contextlib.suppress(OSError):
            with open("/proc/sys/kernel/osrelease", encoding="ascii", errors="replace") as f:
                if "microsoft" in f.read().lower():
                    return "windows"
    return p


def boot_mode() -> str | None:
    """After an OS restart: "login" = the service waits for this user's login (macOS LaunchAgent, Linux without linger,
    WSL), "auto" = it starts by itself (Linux with linger), None = unknown. May run loginctl: call it off the event loop."""
    plat = host_platform()
    if plat in ("macos", "windows"):
        return "login"
    if plat == "linux":
        return {"yes": "auto", "no": "login"}.get(_linger() or "")
    return None


def migrate_calendar_jobs(st) -> list[str]:
    """P425: installer-only migration; preserve schedule/environment and never run a weekly notification immediately.
    Only regular owner files in our weekly-job namespace, with an Agent J executable, are eligible.
    """
    if platform() != "macos":
        return []
    migrated = []
    for path in Path(plist_path("x")).parent.glob("net.agentj.channel.weekly.*.plist"):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022 or info.st_size > 262144:
                    continue
                with os.fdopen(os.dup(fd), "rb") as stream:
                    old = stream.read(262145)
                if len(old) > 262144:
                    continue
            finally:
                os.close(fd)
            d = plistlib.loads(old)
            argv = d.get("ProgramArguments")
            if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
                continue
            if d.get("Label") != path.stem or not d.get("StartCalendarInterval") or d.get("RunAtLoad") is not False:
                continue
            if argv[0] == str(macapp.exe_path()):
                continue
            target = Path(argv[0])
            if not target.is_absolute() or target.name != "agentj":
                continue
            argv = [str(target.resolve()), *argv[1:]]
            wrapped, extra = macapp.wrap(argv, log=getattr(st, "log", None))
            if not extra:
                continue
            d.update(extra, ProgramArguments=wrapped, Comment="Agent J — Weekly task (" + path.stem.rsplit(".", 1)[-1] + ")")
            _write(str(path), plistlib.dumps(d), stat.S_IMODE(info.st_mode))
            r = _bootstrap(path.stem, str(path))
            if r.returncode:
                _write(str(path), old, stat.S_IMODE(info.st_mode))
                _bootstrap(path.stem, str(path))
                st.log("macapp_migration_failed", reason="bootstrap")
                continue
            migrated.append(path.stem)
        except (OSError, ValueError, plistlib.InvalidFileException, subprocess.SubprocessError):
            with contextlib.suppress(Exception):
                st.log("macapp_migration_failed", reason="unavailable")
    return migrated
