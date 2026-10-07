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
from pathlib import Path
import subprocess
import sys

from .envcompat import getenv, isset

DEFAULT_UNIT = "agentj"
DEFAULT_LABEL = "net.agentj.host"
LEGACY_UNIT = "agentjarvis"                # ≤ 0.9
LEGACY_LABEL = "net.agentjarvis.host"
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
CAPTURED_ENV = ("AGENTJ_STATE_DIR", "AGENTJ_CLAUDE_BIN", "AGENTJ_CODEX_BIN", "AGENTJ_OPENCODE_BIN")   # written with the new names; read either
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
    notes = []
    from .binaries import resolve, ENV
    for kind, key in ENV.items():
        resolution = resolve(kind, e)
        if resolution["path"]:
            out[key] = resolution["path"]
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
    saved = binary_env(path)
    e.update(saved)
    env, notes = service_env(e)
    selected = {k: saved[k] if k in saved else env[k] for k in ENV.values() if k in env or k in saved}
    previous = installed_binary_env(n) or previous or {}
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


def load_launch_binary_env() -> None:
    # Launchd has no EnvironmentFile directive. Only our service sets this path;
    # loading binary assignments here avoids baking them into the plist.
    path = os.environ.get("AGENTJ_SERVICE_ENV_FILE")
    if path:
        os.environ.update(binary_env(path))


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
    lines += [f"Environment={_sd_quote(f'{k}={v}')}" for k, v in env.items() if k not in CAPTURED_ENV[1:]]
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


def _systemctl(*args: str, check: bool = False, timeout: int = 30) -> subprocess.CompletedProcess:
    exe = shutil.which("systemctl")
    if not exe:
        raise ServiceError("no_systemctl")
    r = subprocess.run([exe, "--user", *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0 and "via local transport" in (r.stderr or ""):   # F14: inside the Agent's fence → the bus
        r = _bus_systemctl(args, timeout) or r
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


def plist_bytes(n: str, argv: list[str], env: dict, log_path: str) -> bytes:
    return plistlib.dumps({"Label": n, "ProgramArguments": argv + SERVE_ARGS, "EnvironmentVariables": {k: v for k, v in env.items() if k not in CAPTURED_ENV[1:]},
                           "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 5, "ProcessType": "Background",
                           "StandardInPath": "/dev/null", "StandardOutPath": log_path, "StandardErrorPath": log_path})


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
            raise ServiceError("no_user_manager", (r.stderr or "").strip()[:300])
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
        if _linger() == "no" and linger_needed():
            notes.append("服务器 / 无人登录也要运行：`loginctl enable-linger $USER`（否则退出登录后 serve 会停） / "
                         "on a server run `loginctl enable-linger $USER`, or serve stops when you log out")
        return {"kind": "systemd", "name": n, "path": path, "argv": argv + SERVE_ARGS, "notes": notes}
    if plat == "macos":
        path = plist_path(n)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        env, selected_notes = prepare_binary_env(n, previous=previous_binary_selection(st, n))
        notes.extend(selected_notes)
        old = remove_legacy()
        if old:
            notes.append(f"旧服务 {old['name']} 已停止并删除 / the old service {old['name']} was stopped and removed")
        _write(path, plist_bytes(n, argv, env, str(st.root / "service.log")), 0o644)
        # `launchctl enable` writes a permanent record into launchd's override database that only root can remove: call it
        # only when the label is disabled there (a bootstrap would fail), remember that, and put it back on uninstall
        if launchd_disabled(n) is True:
            _launchctl("enable", f"{_gui()}/{n}")
            _remember_enabled(st, n)
        r = _bootstrap(n, path)
        if r.returncode != 0:
            raise ServiceError("launchctl_failed", (r.stderr or r.stdout).strip()[:300])
        remember_binary_selection(st, n)
        return {"kind": "launchd", "name": n, "path": path, "argv": argv + SERVE_ARGS, "notes": notes}
    raise ServiceError("unsupported_os")


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
    return {"name": n}


def uninstall(st=None) -> dict:
    n, plat = name(), platform()
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
        return {"kind": "launchd", "name": n, "path": path, "removed": existed, "restored_disabled": restored}
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
    if platform() == "linux":
        f = env_file(n)
        return (f"只在环境变量里有 {TOKEN_ENV}：服务看不到它（我们不会把它抄进服务文件）。二选一：① 运行 `claude` 登录一次"
                f"（或 `claude setup-token`），登录保存在 ~/.claude；② 自己建 {tilde(f)}（chmod 600），写一行 "
                f"{TOKEN_ENV}=<你的 token>，再 `systemctl --user restart {n}` / {TOKEN_ENV} is only in your shell: the service "
                f"cannot see it and we never copy it. Either log in with `claude` once, or create {tilde(f)} yourself "
                f"(chmod 600) with one line {TOKEN_ENV}=<token>, then restart the service.")
    return (f"只在环境变量里有 {TOKEN_ENV}：LaunchAgent 看不到它。运行 `claude` 登录一次（保存在钥匙串）再重启服务 / "
            f"{TOKEN_ENV} is only in your shell: log in with `claude` once (Keychain), then reinstall the service.")


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
    "bad_binary_env": "AGENTJ_*_BIN 路径格式无效：检查服务 env 文件 / invalid harness path in service environment file",
    "bad_name": "服务名不合规（AGENTJ_SERVICE_NAME） / invalid service name",
    "no_systemctl": "没有 systemctl：这台 Linux 不用 systemd，自己用 tmux / supervisord 跑 `agentj serve` / no systemd here",
    "no_user_manager": "systemd 用户实例不可用（没有登录会话？）：先 `loginctl enable-linger $USER` 再重新登录 / "
                       "no systemd user manager: run `loginctl enable-linger $USER` and log in again",
    "systemctl_failed": "systemctl 失败 / systemctl failed",
    "launchctl_failed": "launchctl 失败 / launchctl failed",
    "unsupported_os": "这个系统还不支持（Windows 请在 WSL2 里装） / unsupported OS (Windows: use WSL2)",
    "no_home": "没有 HOME / HOME is not set",
}
