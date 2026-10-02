"""`jarvis service install|uninstall|status` (L3-min): keep `jarvis serve` running after logout / reboot.

Linux = a systemd *user* unit (`~/.config/systemd/user/agentjarvis.service`); macOS = a LaunchAgent
(`~/Library/LaunchAgents/net.agentjarvis.host.plist`). Both start the absolute path of the jarvis that ran `install`
(inside its venv, which the fence keeps read-only), with `serve --events quiet --no-stdin`: no terminal, and the log
(journal / `service.log` in the state dir) gets metadata lines only — never a message, a reply or a command.

Never a secret in the unit / plist: only PATH (captured at install so `claude` / `codex` / `bwrap` resolve) and, when set,
AGENTJARVIS_STATE_DIR / AGENTJARVIS_CLAUDE_BIN / AGENTJARVIS_CODEX_BIN (paths). The Agent uses its own login (~/.claude,
~/.codex). If the only Claude Code login is the CLAUDE_CODE_OAUTH_TOKEN variable, the human puts it into an environment
file they create themselves (0600, next to the unit, read-only for the fenced Agent) — we print how, we never copy it.

`AGENTJARVIS_SERVICE_NAME` overrides the unit name / launchd label (tests); `vibe-remote*` names are refused.
"""
from __future__ import annotations

import contextlib
import os
import plistlib
import re
import shutil
import subprocess
import sys

DEFAULT_UNIT = "agentjarvis"
DEFAULT_LABEL = "net.agentjarvis.host"
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
CAPTURED_ENV = ("AGENTJARVIS_STATE_DIR", "AGENTJARVIS_CLAUDE_BIN", "AGENTJARVIS_CODEX_BIN")
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
    n = os.environ.get("AGENTJARVIS_SERVICE_NAME") or (DEFAULT_UNIT if platform() == "linux" else DEFAULT_LABEL)
    if not _NAME_RE.fullmatch(n) or n.lower().startswith("vibe-remote") or n.endswith(".service"):
        raise ServiceError("bad_name", n)
    return n


def jarvis_argv() -> list[str]:
    """How the service starts this very jarvis: the venv's console script (`<venv>/bin/jarvis`, an absolute path inside
    the read-only venv — not the ~/.local/bin shim, which any same-user process could replace), else this interpreter
    with `-P -m jarvis_host.cli`."""
    exe = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "jarvis")
    if os.path.isfile(exe) and os.access(exe, os.X_OK):
        return [exe]
    return [os.path.abspath(sys.executable), "-P", "-m", "jarvis_host.cli"]


SERVE_ARGS = ["serve", "--events", "quiet", "--no-stdin"]


def service_env(environ: dict | None = None) -> tuple[dict, list[str]]:
    """(variables written into the unit, notes for the human). Values are paths only; proxies only without credentials."""
    e = os.environ if environ is None else environ
    path = os.pathsep.join(p for p in (e.get("PATH") or "").split(os.pathsep) if p.startswith("/"))
    out = {"PATH": path or "/usr/local/bin:/usr/bin:/bin"}
    if "AGENTJARVIS_STATE_DIR" in e:
        out["AGENTJARVIS_STATE_DIR"] = os.path.abspath(os.path.expanduser(e["AGENTJARVIS_STATE_DIR"]))
    for k in CAPTURED_ENV[1:]:
        if e.get(k):
            out[k] = e[k]
    notes = []
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


# ------------------------------------------------------------------ Linux: systemd --user
def _sd_quote(v: str) -> str:
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def unit_dir(environ: dict | None = None) -> str:
    e = os.environ if environ is None else environ
    base = e.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "systemd", "user")


def env_file(n: str, environ: dict | None = None) -> str:
    """The optional environment file the human may create (0600). Lives next to the unit: read-only for the fenced Agent."""
    return os.path.join(unit_dir(environ), f"{n}.env")


def unit_text(n: str, argv: list[str], env: dict, environ: dict | None = None) -> str:
    lines = ["# agentjarvis host — written by `jarvis service install`. No secrets here, by design.",
             "[Unit]", "Description=agentjarvis host (jarvis serve): your phone <-> your own Agent",
             "StartLimitIntervalSec=300", "StartLimitBurst=20", "",
             "[Service]", "Type=simple",
             "ExecStart=" + " ".join(_sd_quote(x) for x in argv + SERVE_ARGS),
             "Restart=on-failure", "RestartSec=5", "TimeoutStopSec=20", "StandardInput=null"]
    lines += [f"Environment={_sd_quote(f'{k}={v}')}" for k, v in env.items()]
    lines += ["# optional, created by you (0600) — e.g. CLAUDE_CODE_OAUTH_TOKEN=… ; the leading '-' = may be absent",
              "EnvironmentFile=-" + env_file(n, environ).replace("%", "%%"), "", "[Install]", "WantedBy=default.target", ""]
    return "\n".join(lines)


def _systemctl(*args: str, check: bool = False, timeout: int = 30) -> subprocess.CompletedProcess:
    exe = shutil.which("systemctl")
    if not exe:
        raise ServiceError("no_systemctl")
    r = subprocess.run([exe, "--user", *args], capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise ServiceError("systemctl_failed", f"systemctl --user {' '.join(args)}: {(r.stderr or r.stdout).strip()[:300]}")
    return r


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
    return plistlib.dumps({"Label": n, "ProgramArguments": argv + SERVE_ARGS, "EnvironmentVariables": env,
                           "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 5, "ProcessType": "Background",
                           "StandardInPath": "/dev/null", "StandardOutPath": log_path, "StandardErrorPath": log_path})


def _launchctl(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=timeout)


def _gui() -> str:
    return f"gui/{os.getuid()}"


# ------------------------------------------------------------------ the three verbs
def install(st) -> dict:
    """Write + enable + start. → {"kind", "name", "path", "argv", "notes"}; raises ServiceError."""
    n, plat = name(), platform()
    argv = jarvis_argv()
    env, notes = service_env()
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
        _write(path, unit_text(n, argv, env).encode(), 0o644)
        _systemctl("daemon-reload", check=True)
        _systemctl("enable", f"{n}.service", check=True)
        _systemctl("restart", f"{n}.service", check=True)   # starts it, or picks up a rewritten unit (reinstall / upgrade)
        if _linger() == "no":
            notes.append("服务器 / 无人登录也要运行：`loginctl enable-linger $USER`（否则退出登录后 serve 会停） / "
                         "on a server run `loginctl enable-linger $USER`, or serve stops when you log out")
        return {"kind": "systemd", "name": n, "path": path, "argv": argv + SERVE_ARGS, "notes": notes}
    if plat == "macos":
        path = plist_path(n)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _write(path, plist_bytes(n, argv, env, str(st.root / "service.log")), 0o644)
        _launchctl("bootout", f"{_gui()}/{n}")
        r = _launchctl("bootstrap", _gui(), path)
        if r.returncode != 0:
            raise ServiceError("launchctl_failed", (r.stderr or r.stdout).strip()[:300])
        _launchctl("enable", f"{_gui()}/{n}")
        return {"kind": "launchd", "name": n, "path": path, "argv": argv + SERVE_ARGS, "notes": notes}
    raise ServiceError("unsupported_os")


def uninstall() -> dict:
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
        return {"kind": "launchd", "name": n, "path": path, "removed": existed}
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
    "bad_name": "服务名不合规（AGENTJARVIS_SERVICE_NAME） / invalid service name",
    "no_systemctl": "没有 systemctl：这台 Linux 不用 systemd，自己用 tmux / supervisord 跑 `jarvis serve` / no systemd here",
    "no_user_manager": "systemd 用户实例不可用（没有登录会话？）：先 `loginctl enable-linger $USER` 再重新登录 / "
                       "no systemd user manager: run `loginctl enable-linger $USER` and log in again",
    "systemctl_failed": "systemctl 失败 / systemctl failed",
    "launchctl_failed": "launchctl 失败 / launchctl failed",
    "unsupported_os": "这个系统还不支持（Windows 请在 WSL2 里装） / unsupported OS (Windows: use WSL2)",
    "no_home": "没有 HOME / HOME is not set",
}
