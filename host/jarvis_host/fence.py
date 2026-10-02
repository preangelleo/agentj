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

What it does not stop (disclosed, ARCHITECTURE G-A29): the network namespace is shared (the agent needs the internet), so a
local service that accepts the user's own credentials — sshd with the user's key, an X11 server listening on an abstract
socket — can still lead out. A separate OS user for the agent closes that (L3 hardened mode). The approval passphrase
(gate.py) still stands in front of every device approval.

Linux only in L2 (bubblewrap ≥ 0.4 + unprivileged user namespaces). Elsewhere, or when bwrap cannot start, the agent is not
started unless the human chose `jarvis agent … --unfenced` at the terminal.
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
    for rel in _RO_FILES:
        p = os.path.join(home, rel)
        if os.path.lexists(p) and visible(_real(p)):
            a += ["--ro-bind", p, p]
    for p in code_paths():          # read-only — also when installed under /tmp (a temporary HOME): put back on top
        a += ["--ro-bind", p, p]
    a += ["--tmpfs", root, "--bind", perm, perm]
    for k in _HIDE_ENV:
        a += ["--unsetenv", k]
    a += ["--chdir", work, "--"]
    return a


_probe_cache: dict[str, str | None] = {}


def problem(st, workdir: str) -> str | None:
    """None when the fence starts on this machine, else a short reason (cached per process)."""
    if sys.platform != "linux":
        return "unsupported_os"
    if not shutil.which("bwrap"):
        return "no_bwrap"
    key = f"{st.root}|{workdir}"
    if key not in _probe_cache:
        try:
            st.perm_dir.mkdir(mode=0o700, exist_ok=True)
            r = subprocess.run(bwrap_argv(st, workdir) + ["/bin/sh", "-c", "exit 0"], capture_output=True, timeout=10)
            _probe_cache[key] = None if r.returncode == 0 else "bwrap_failed"
        except (OSError, subprocess.TimeoutExpired):
            _probe_cache[key] = "bwrap_failed"
    return _probe_cache[key]


def wrap(st, argv: list[str], workdir: str) -> list[str]:
    a = bwrap_argv(st, workdir)
    exe = _real(shutil.which(argv[0]) or argv[0])
    if os.path.isfile(exe) and any(_under(exe, d) for d in ("/tmp", "/var/tmp")):
        a[-1:-1] = ["--ro-bind", exe, exe]   # an agent binary under /tmp (tests): only that file comes back, read-only
    return a + argv


REASONS = {
    "unsupported_os": "这个系统上还没有 Agent 隔离（L2 只支持 Linux；macOS 随 L3 上线）",
    "no_bwrap": "没找到 bubblewrap（bwrap），Agent 隔离起不来：装上它（apt / dnf / pacman install bubblewrap）",
    "bwrap_failed": "bubblewrap 起不来（多半是系统禁止了非特权用户命名空间），Agent 隔离起不来",
}


def no_dump() -> None:
    """Make this process non-dumpable: same-user processes cannot ptrace it or read /proc/<pid>/{mem,environ,fd}."""
    if sys.platform != "linux":
        return
    try:
        import ctypes
        ctypes.CDLL(None, use_errno=True).prctl(4, 0, 0, 0, 0)   # PR_SET_DUMPABLE = 4
    except (OSError, AttributeError):
        pass
