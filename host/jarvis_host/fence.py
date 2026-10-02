"""The agent fence (L2 / G-A8, G-A24): `jarvis serve` starts the customer's agent (Claude Code / Codex) inside a
bubblewrap mount + PID namespace where jarvis itself is out of reach. Same OS user, same login, same settings (Invariant 11);
what changes is only what the agent's process tree can *see*:

- this host's state directory is an empty tmpfs — keys, allowlist, control socket, passphrase hash, approvals.log, cloud
  link are invisible; only `agentperm/` (the permission tool's socket) is bound back in;
- jarvis's own code (the package, its venv, the launcher) is read-only, so nothing can be planted for the next start;
- a private PID namespace and /proc: jarvis's processes (and /proc/<pid>/root, which would lead around the tmpfs) do not exist;
- a private /tmp and $XDG_RUNTIME_DIR: tmux / herdr / X11 / Wayland / compositor / D-Bus / systemd-user sockets — the usual
  ways to make an *unfenced* process run something — are gone; their environment variables are unset;
- shell start-up files, ~/.ssh/authorized_keys and user autostart / systemd units are read-only (no planting for later);
- bubblewrap sets no_new_privs: setuid helpers (crontab, at, sudo, pkexec) gain nothing.

What it does not stop on Linux (disclosed, ARCHITECTURE G-A29): the network namespace is shared (the agent needs the internet), so a
local service that accepts the user's own credentials — sshd with the user's key, an X11 server listening on an abstract
socket — can still lead out. A separate OS user for the agent closes that (L3 hardened mode). The approval passphrase
(gate.py) still stands in front of every device approval.

Linux = bubblewrap ≥ 0.4 + unprivileged user namespaces (L2). macOS = `sandbox-exec` with an SBPL profile generated per
start (L3, `sbpl_profile`): the same rules expressed as a deny-list on top of `(allow default)` — the state directory is
unreadable and unconnectable except `agentperm/`, jarvis's code / shell start-up files / LaunchAgents / authorized_keys are
read-only (also when they do not exist yet), signals and process information only within the Agent's own sandbox, no launchd
jobs (`launchctl submit`), no Apple Events, no LaunchServices (`open -a Terminal x.command`), no tmux / ssh-agent sockets;
setuid programs (sudo, crontab, at) do not run inside any sandbox. macOS has no PID or mount namespace: the Agent still sees
the process list's *argv* via sysctl (not environments: the kernel withholds those), shares /tmp and $TMPDIR, and shares the
network (as on Linux). Elsewhere, or when the fence cannot start, the agent is not started unless the human chose
`jarvis agent … --unfenced` at the terminal.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

_HIDE_ENV = ("DBUS_SESSION_BUS_ADDRESS", "WAYLAND_DISPLAY", "DISPLAY", "XAUTHORITY", "HYPRLAND_INSTANCE_SIGNATURE",
             "SWAYSOCK", "I3SOCK", "TMUX", "TMUX_PANE", "STY", "SSH_AUTH_SOCK", "GNOME_KEYRING_CONTROL", "KITTY_LISTEN_ON",
             "AGENTJARVIS_STATE_DIR")
_RO_FILES = (".bashrc", ".bash_profile", ".bash_login", ".profile", ".bash_logout", ".zshrc", ".zprofile", ".zshenv",
             ".zlogin", ".config/fish", ".ssh/authorized_keys", ".ssh/authorized_keys2", ".ssh/rc", ".ssh/config",
             ".config/autostart", ".config/systemd", ".local/share/systemd", ".config/environment.d", ".pam_environment",
             ".Xauthority", ".xprofile", ".xinitrc", ".config/hypr", ".config/sway", ".config/i3",
             "Library/LaunchAgents")   # macOS: where `jarvis service install` puts its plist (L3)


def _real(p: str) -> str:
    return os.path.realpath(os.path.expanduser(p))


def code_paths() -> list[str]:
    """jarvis's own code as this process runs it, wherever it was installed (L3): the package, its source checkout (host/
    with the launcher), the venv (`uv tool` → ~/.local/share/uv/tools/agentjarvis-host, pipx → ~/.local/[share/]pipx/venvs/…,
    or the checkout's .venv) and the interpreter that venv points at when the user owns it (uv-managed / Homebrew Python):
    everything that runs inside `serve` next time. Only paths this user can write matter (the rest is read-only anyway)."""
    pkg = os.path.dirname(os.path.abspath(__file__))
    out = [pkg]
    parent = os.path.dirname(pkg)
    if os.path.exists(os.path.join(parent, "jarvis")) and os.path.exists(os.path.join(parent, "pyproject.toml")):
        out.append(parent)          # source checkout: host/ (launcher + .venv + tests)
    if sys.prefix != sys.base_prefix:
        out.append(sys.prefix)      # the venv jarvis runs in (a dependency there would run in serve too)
    base, home = _real(sys.base_prefix), _real("~")
    if not _under(home, base) and base not in (_real("~/.local"), _real("~/.local/share")):
        out.append(base)            # its interpreter + stdlib, when that is a dedicated directory (kept only if writable)
    paths = sorted({_real(p) for p in out if os.access(p, os.W_OK)})
    return [p for p in paths if not any(q != p and _under(p, q) for q in paths)]   # a parent covers its children


def protected_paths(st) -> list[str]:
    """What the agent must not see or change; its working folder may not be inside any of these."""
    return sorted({_real(str(st.root))} | set(code_paths()))


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def ancestors(paths) -> list[str]:
    """Directories above the protected paths that this user could rename (their parent is writable), parents first.
    Renaming one would move jarvis's code / state / a start-up file out of the way and let the Agent plant a new one at the
    old path (`mv ~/.local/share/uv/tools x && mkdir … && plant`): the fence pins each of them (bwrap: a bind mount on itself,
    which makes rename fail with EBUSY; SBPL: no write on that exact directory). Creating files inside them stays allowed."""
    out = set()
    for p in paths:
        a = os.path.dirname(p)
        while a and a != os.sep:
            par = os.path.dirname(a)
            if os.path.isdir(a) and os.access(par, os.W_OK):
                out.add(a)
            a = par
    return sorted(out, key=lambda x: (x.count(os.sep), x))


def bwrap_argv(st, workdir: str, home: str | None = None, runtime: str | None = None) -> list[str]:
    """The bubblewrap prefix (ends with "--"). Order matters: later mounts sit on top of earlier ones, so the state
    directory is hidden *after* everything that is put back, and only the permission folder returns on top of it."""
    home = _real(home or os.path.expanduser("~"))
    runtime = runtime if runtime is not None else os.environ.get("XDG_RUNTIME_DIR", "")
    root, perm, work = _real(str(st.root)), _real(str(st.perm_dir)), _real(workdir)
    a = [shutil.which("bwrap") or "bwrap", "--dev-bind", "/", "/", "--unshare-pid", "--proc", "/proc",
         "--die-with-parent", "--new-session"]
    hidden = [d for d in ("/tmp", "/var/tmp") if os.path.isdir(d)]
    if runtime and os.path.isdir(runtime):
        hidden.append(_real(runtime))
    for d in hidden:
        a += ["--tmpfs", d]
    back = [p for p in (home, work) if any(_under(p, d) for d in hidden)]   # a HOME or folder under /tmp (tests)
    for p in sorted(set(back), key=len):
        a += ["--bind", p, p]
    visible = lambda p: not any(_under(p, d) for d in hidden) or any(_under(p, b) for b in back)  # noqa: E731
    ro = [p for p in (os.path.join(home, rel) for rel in _RO_FILES) if os.path.lexists(p) and visible(_real(p))]
    code = code_paths()
    for d in ancestors([_real(p) for p in ro] + code + [root]):   # pinned: cannot be renamed away (still writable inside)
        if visible(d):
            a += ["--bind", d, d]
    for p in ro:
        a += ["--ro-bind", p, p]
    for p in code:                  # read-only — also when installed under /tmp (a temporary HOME): put back on top
        a += ["--ro-bind", p, p]
    a += ["--tmpfs", root, "--bind", perm, perm]
    for k in _HIDE_ENV:
        a += ["--unsetenv", k]
    a += ["--chdir", work, "--"]
    return a


SANDBOX_EXEC = "/usr/bin/sandbox-exec"
_MAC_RO = (".ssh/environment",)   # plus _RO_FILES (~/.ssh/environment is read by sshd only when PermitUserEnvironment)
_LAUNCHD_SOCKETS = r"^/private/tmp/com\.apple\.launchd\.[^/]+/"   # ssh-agent (SSH_AUTH_SOCK) and other launchd sockets


def sbpl_profile(st, workdir: str, home: str | None = None, uid: int | None = None) -> tuple[str, dict]:
    """(profile text, -D parameters) for `sandbox-exec` (macOS). Paths travel as parameters, never spliced into the profile,
    so a folder name cannot change the rules. Later rules win in SBPL: the permission folder is allowed back after the state
    directory is denied, exactly like the bwrap mount order."""
    home = _real(home or os.path.expanduser("~"))
    uid = os.getuid() if uid is None else uid
    params = {"STATE": _real(str(st.root)), "PERM": _real(str(st.perm_dir)), "TMUX": f"/private/tmp/tmux-{uid}"}
    ro = list(code_paths()) + [os.path.join(home, rel) for rel in _RO_FILES + _MAC_RO]
    ro = [_real(p) if os.path.lexists(p) else p for p in ro]   # a start-up file that does not exist yet stays protected
    ro = list(dict.fromkeys(ro))
    for i, p in enumerate(ro):
        params[f"RO_{i}"] = p
    pin = ancestors(ro + [params["STATE"]])
    for i, p in enumerate(pin):
        params[f"PIN_{i}"] = p
    n_ro = len(ro)
    lines = [
        "(version 1)",
        "(allow default)",
        ";; 1. the host's state directory: invisible and unconnectable, except the permission tool's folder",
        '(deny file-read* file-write* (subpath (param "STATE")))',
        '(deny network-outbound (remote unix-socket (subpath (param "STATE"))))',
        '(allow file-read* file-write* (subpath (param "PERM")))',
        '(allow network-outbound (remote unix-socket (subpath (param "PERM"))))',
        ";; 2. jarvis's code, shell start-up files, autostart, authorized_keys: read-only (also when absent: no planting)",
        "(deny file-write*",
        *[f'  (subpath (param "RO_{i}"))' for i in range(n_ro)],
        ")",
        ";; ... and the directories above them cannot be renamed away (creating files inside them stays allowed)",
        "(deny file-write*",
        *[f'  (literal (param "PIN_{i}"))' for i in range(len(pin))],
        ")",
        ";; 3. jarvis's processes (and every other process outside this sandbox): no signals, no process information",
        "(deny signal)",
        "(allow signal (target same-sandbox))",
        "(deny process-info*)",
        "(allow process-info* (target same-sandbox))",
        ";; 4. the usual ways to make an unfenced process run something",
        "(deny job-creation)",
        "(deny appleevent-send)",
        "(deny lsopen)",
        '(deny network-outbound (remote unix-socket (subpath (param "TMUX"))))',
        f'(deny network-outbound (remote unix-socket (regex #"{_LAUNCHD_SOCKETS}")))',
    ]
    return "\n".join(lines) + "\n", params


def sandbox_argv(st, workdir: str, home: str | None = None) -> list[str]:
    """The sandbox-exec prefix (macOS). The display / session variables are unset with /usr/bin/env, like bwrap's --unsetenv."""
    prof, params = sbpl_profile(st, workdir, home)
    a = [SANDBOX_EXEC, "-p", prof]
    for k, v in params.items():
        a += ["-D", f"{k}={v}"]
    a.append("/usr/bin/env")
    for k in _HIDE_ENV:
        a += ["-u", k]
    return a


def fence_argv(st, workdir: str) -> list[str]:
    return sandbox_argv(st, workdir) if sys.platform == "darwin" else bwrap_argv(st, workdir)


def kind() -> str:
    return "sandbox-exec" if sys.platform == "darwin" else "bubblewrap"


_probe_cache: dict[str, str | None] = {}


def problem(st, workdir: str) -> str | None:
    """None when the fence starts on this machine, else a short reason (cached per process). macOS: the probe also checks
    that the profile really applies (the state directory must be unreadable from inside)."""
    if sys.platform == "darwin":
        if not os.access(SANDBOX_EXEC, os.X_OK):
            return "no_sandbox_exec"
    elif sys.platform != "linux":
        return "unsupported_os"
    elif not shutil.which("bwrap"):
        return "no_bwrap"
    key = f"{st.root}|{workdir}"
    if key not in _probe_cache:
        try:
            st.perm_dir.mkdir(mode=0o700, exist_ok=True)
            if sys.platform == "darwin":
                probe = ["/bin/sh", "-c", 'ls "$1" >/dev/null 2>&1 && exit 3; exit 0', "probe", _real(str(st.root))]
                r = subprocess.run(sandbox_argv(st, workdir) + probe, capture_output=True, timeout=10, cwd=workdir)
                _probe_cache[key] = None if r.returncode == 0 else "sandbox_failed"
            else:
                r = subprocess.run(bwrap_argv(st, workdir) + ["/bin/sh", "-c", "exit 0"], capture_output=True, timeout=10)
                _probe_cache[key] = None if r.returncode == 0 else "bwrap_failed"
        except (OSError, subprocess.TimeoutExpired):
            _probe_cache[key] = "sandbox_failed" if sys.platform == "darwin" else "bwrap_failed"
    return _probe_cache[key]


def wrap(st, argv: list[str], workdir: str) -> list[str]:
    if sys.platform == "darwin":
        return sandbox_argv(st, workdir) + argv
    a = bwrap_argv(st, workdir)
    exe = _real(shutil.which(argv[0]) or argv[0])
    if os.path.isfile(exe) and any(_under(exe, d) for d in ("/tmp", "/var/tmp")):
        a[-1:-1] = ["--ro-bind", exe, exe]   # an agent binary under /tmp (tests): only that file comes back, read-only
    return a + argv


REASONS = {
    "unsupported_os": "这个系统上还没有 Agent 隔离（支持 Linux 与 macOS；Windows 请用 WSL2）",
    "no_bwrap": "没找到 bubblewrap（bwrap），Agent 隔离起不来：装上它（apt / dnf / pacman install bubblewrap）",
    "bwrap_failed": "bubblewrap 起不来（多半是系统禁止了非特权用户命名空间，或在容器里），Agent 隔离起不来",
    "no_sandbox_exec": "这台 Mac 上没有 /usr/bin/sandbox-exec，Agent 隔离起不来",
    "sandbox_failed": "macOS 沙箱（sandbox-exec）起不来（jarvis 自己是不是已经在别的沙箱里运行？），Agent 隔离起不来",
}


def no_dump() -> None:
    """Make this process non-dumpable: same-user processes cannot ptrace it or read /proc/<pid>/{mem,environ,fd} (Linux);
    on macOS refuse debugger attachment (PT_DENY_ATTACH)."""
    try:
        import ctypes
        if sys.platform == "linux":
            ctypes.CDLL(None, use_errno=True).prctl(4, 0, 0, 0, 0)   # PR_SET_DUMPABLE = 4
        elif sys.platform == "darwin":
            ctypes.CDLL(None, use_errno=True).ptrace(31, 0, None, 0)   # PT_DENY_ATTACH = 31
    except (OSError, AttributeError):
        pass
