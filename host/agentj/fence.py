"""The agent fence (L2 / G-A8, G-A24; F14 0.15): `agentj serve` starts the customer's agent (Claude Code / Codex) inside a
bubblewrap mount + PID namespace that hides Agent J's *data* — and only that. Same OS user, same login, same settings
(Invariant 11). F14: Agent J adds no lock of its own; what the fence keeps is the data-safety floor:

- this host's state directory is an empty tmpfs — keys, allowlist, control socket, passphrase hash, approvals.log, cloud
  link, paired devices are invisible; only `agentperm/` (the permission tool's socket) is bound back in; the preferences
  folder and an older state directory likewise;
- credentials of other programs: ssh-agent / keyring sockets (the runtime dir stays private apart from what is bound back),
  SSH_AUTH_SOCK / XAUTHORITY are unset, ~/.ssh/authorized_keys and ~/.Xauthority are read-only;
- other sessions' control sockets (G-A56: herdr, GNU screen, wezterm, emacs / Jupyter folders, tmux, every listening socket
  this user owns at start) and — unless the owner allowed it — the docker / podman / containerd / lxd / libvirt sockets;
- a private PID namespace and /proc: agentj's processes (and /proc/<pid>/root, which would lead around the tmpfs) do not exist.

What the Agent may do in its own independent session (F14): change the owner's general configuration (shell start-up files,
autostart, ~/.config/systemd, environment.d, compositor configs), write Agent J's own code and venv, and manage its own
services — the user's systemd manager (`$XDG_RUNTIME_DIR/systemd/private`) and session bus (`$XDG_RUNTIME_DIR/bus`) are
bound back into the private runtime dir (plain `systemctl --user` still refuses the private socket from inside the PID
namespace — its peer has no PID there — so `agentj service …` falls back to the bus, service.py; `busctl --user` works for
any unit). Consequence, disclosed: a process started through the user's service manager runs
outside the fence, so on Linux the fence is a data-visibility layer, not a boundary against a determined Agent; the
approval signatures (approvals / permtool) and pairing (gate.py) are checked by serve, outside the Agent's reach.

Linux = bubblewrap ≥ 0.4 + unprivileged user namespaces (L2). macOS = `sandbox-exec` with an SBPL profile generated per
start (L3, `sbpl_profile`): the same rules expressed as a deny-list on top of `(allow default)` — the state directory is
unreadable and unconnectable except `agentperm/`, authorized_keys is read-only, signals and process information only within
the Agent's own sandbox, no Apple Events, no LaunchServices, no tmux / ssh-agent sockets, no herdr / screen / zellij / wezterm
/ emacs / nvim / Jupyter / VS Code / kitty sockets and (unless allowed) no container engine sockets; launchd jobs and
~/Library/LaunchAgents are the Agent's own to manage (F14). macOS has no PID or mount namespace: the Agent still sees the
process list's *argv* via sysctl, shares /tmp and $TMPDIR, and shares the network (as on Linux). Elsewhere, or when the
fence cannot start, the legacy independent Agent.launch_argv path runs unfenced with the harness's own permissions
(F14: degraded, one notice on the phone). P67's Agent J-owned shared OpenCode path instead refuses startup when requested
isolation cannot start (provider_runtime.shared_launch); it never silently degrades. An explicitly selected unfenced mode
remains unfenced. A server the owner started is attached as owner-managed; Agent J cannot rewrap that existing process.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys

_HIDE_ENV = ("WAYLAND_DISPLAY", "DISPLAY", "XAUTHORITY", "HYPRLAND_INSTANCE_SIGNATURE",
             "SWAYSOCK", "I3SOCK", "TMUX", "TMUX_PANE", "STY", "SSH_AUTH_SOCK", "GNOME_KEYRING_CONTROL", "KITTY_LISTEN_ON",
             "AGENTJ_STATE_DIR", "AGENTJARVIS_STATE_DIR", "EMACS_SOCKET_NAME", "SCREENDIR", "JUPYTER_RUNTIME_DIR")
# terminal multiplexers / editors that let a client run something in *their* (unfenced) panes: every variable they export
# (herdr: HERDR_SOCKET_PATH, HERDR_PANE_ID …; zellij: ZELLIJ_SESSION_NAME …; wezterm: WEZTERM_UNIX_SOCKET …; kitty; nvim;
# VS Code's IPC handles). Unset by prefix, so a new variable of the same tool is covered too (fixed after L3, G-A56).
_HIDE_PREFIX = ("HERDR_", "ZELLIJ", "WEZTERM_", "KITTY_", "TMUX", "NVIM", "VSCODE_IPC_HOOK", "VSCODE_GIT_IPC")
_CONTAINER_ENV = ("DOCKER_HOST", "CONTAINER_HOST", "DOCKER_CONTEXT", "PODMAN_HOST")
# Control sockets outside the runtime dir and /tmp (G-A56): a client of any of these can make an unfenced process run a
# command (herdr / screen / wezterm / emacs / Jupyter) or, for the container engines, mount the host's files (docker group =
# root). Directories are hidden whole (Linux: an empty tmpfs on top; macOS: no read, no write, no unix-socket connect).
_CTL_DIRS = (".config/herdr", "Library/Application Support/herdr", ".screen", ".local/share/wezterm", ".emacs.d/server",
             ".config/emacs/server", ".local/share/jupyter/runtime", "Library/Jupyter/runtime")
_CONTAINER_DIRS = (".docker/run", ".docker/desktop", ".colima", ".lima", ".orbstack/run", ".rd",
                   ".local/share/containers/podman/machine")
_CONTAINER_SOCKETS = ("/run/docker.sock", "/var/run/docker.sock", "/run/containerd/containerd.sock", "/run/podman/podman.sock",
                      "/run/buildkit/buildkitd.sock", "/var/snap/lxd/common/lxd/unix.socket", "/var/lib/lxd/unix.socket",
                      "/var/lib/incus/unix.socket", "/run/libvirt/libvirt-sock", "/var/run/libvirt/libvirt-sock")
_SYSTEM_SOCKETS = ("/run/ssh-unix-local/socket",)   # systemd's AF_UNIX sshd entry: a login as this user, outside the fence
MAX_SOCKETS = 256                                   # bound on the per-start socket list (argv size)
# F14: only credential files stay read-only (who may log in as this user, the X cookie). Shell start-up files, autostart,
# systemd user units, environment.d and compositor configs are the owner's general configuration: the Agent may change them.
_RO_FILES = (".ssh/authorized_keys", ".ssh/authorized_keys2", ".Xauthority")
# F14: the user's service manager and session bus, bound back into the otherwise private runtime dir (service management)
_RUNTIME_BACK = ("systemd", "bus")


def _real(p: str) -> str:
    return os.path.realpath(os.path.expanduser(p))


def code_paths() -> list[str]:
    """agentj's own code as this process runs it, wherever it was installed (L3): the package, its source checkout (host/
    with the launcher), the venv (`uv tool` → ~/.local/share/uv/tools/agentj, pipx → ~/.local/[share/]pipx/venvs/…,
    or the checkout's .venv) and the interpreter that venv points at when the user owns it (uv-managed / Homebrew Python):
    everything that runs inside `serve` next time. Only paths this user can write matter (the rest is read-only anyway)."""
    pkg = os.path.dirname(os.path.abspath(__file__))
    out = [pkg]
    parent = os.path.dirname(pkg)
    if os.path.exists(os.path.join(parent, "agentj")) and os.path.exists(os.path.join(parent, "pyproject.toml")):
        out.append(parent)          # source checkout: host/ (launcher + .venv + tests)
    if sys.prefix != sys.base_prefix:
        out.append(sys.prefix)      # the venv agentj runs in (a dependency there would run in serve too)
    base, home = _real(sys.base_prefix), _real("~")
    if not _under(home, base) and base not in (_real("~/.local"), _real("~/.local/share")):
        out.append(base)            # its interpreter + stdlib, when that is a dedicated directory (kept only if writable)
    paths = sorted({_real(p) for p in out if os.access(p, os.W_OK)})
    return [p for p in paths if not any(q != p and _under(p, q) for q in paths)]   # a parent covers its children


def other_states(st, home: str | None = None) -> list[str]:
    """The default state path and the ≤ 0.9 one (`~/.local/state/agentjarvis-alpha`) when they hold something other than
    st.root: the legacy symlink left by the 0.10 move resolves to st.root and needs nothing more (hidden with it); a real
    old directory next to a newer one (a second identity: keys, devices) is hidden as well. Real paths, outermost only."""
    from .state import default_state, legacy_state
    root = _real(str(st.root))
    out = []
    for p in (legacy_state(home), default_state(home)):
        if not os.path.lexists(p):
            continue
        r = _real(str(p))
        if os.path.isdir(r) and not _under(r, root) and not _under(root, r) and r not in out:
            out.append(r)
    return out


def state_aliases(st, home: str | None = None) -> list[str]:
    """Unresolved paths that lead into a state directory (the legacy symlink): for rules that match a path literally."""
    from .state import default_state, legacy_state
    return [str(p) for p in (legacy_state(home), default_state(home)) if os.path.islink(p)]


def protected_paths(st) -> list[str]:
    """What the agent must not see or change; its working folder may not be inside any of these."""
    from .preferences import path as preferences_path
    return sorted({_real(str(st.root)), _real(str(preferences_path().parent))} | set(other_states(st)))


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def hide_env(environ=None, allow_docker: bool = False) -> list[str]:
    """Names of the variables the Agent must not inherit: the fixed list, everything a multiplexer / editor exports (by
    prefix) and, unless the human allowed it, where the container engine lives."""
    env = os.environ if environ is None else environ
    out = list(_HIDE_ENV)
    out += sorted(k for k in env if k.startswith(_HIDE_PREFIX) and k not in out)
    if not allow_docker:
        out += [k for k in _CONTAINER_ENV if k not in out]
    return out


def _env_dirs(env) -> list[str]:
    """Socket directories named by the environment (a herdr / screen / tmux / Jupyter started with a custom location)."""
    out = []
    for k in ("HERDR_SOCKET_PATH", "HERDR_CLIENT_SOCKET_PATH"):
        if env.get(k, "").startswith("/"):
            out.append(os.path.dirname(env[k]))
    for k in ("SCREENDIR", "JUPYTER_RUNTIME_DIR"):
        if env.get(k, "").startswith("/"):
            out.append(env[k])
    if env.get("TMUX_TMPDIR", "").startswith("/"):
        out.append(os.path.join(env["TMUX_TMPDIR"], f"tmux-{os.getuid()}"))
    if env.get("XDG_CONFIG_HOME", "").startswith("/"):
        out.append(os.path.join(env["XDG_CONFIG_HOME"], "herdr"))
    return out


def control_dirs(home: str, environ=None, allow_docker: bool = False, workdir: str | None = None,
                 mac: bool | None = None, uid: int | None = None) -> list[str]:
    """Directories (real paths) that hold another program's control sockets and are hidden from the Agent whole. Never the
    home directory itself, nor a directory that contains the Agent's folder (those sockets are hidden one by one instead)."""
    env = os.environ if environ is None else environ
    cand = [os.path.join(home, rel) for rel in _CTL_DIRS] + _env_dirs(env)
    if not allow_docker:
        cand += [os.path.join(home, rel) for rel in _CONTAINER_DIRS]
    if (sys.platform == "darwin") if mac is None else mac:   # no mount namespace: /tmp and $TMPDIR are shared, so their per-user socket folders too
        uid, tmpd = os.getuid() if uid is None else uid, env.get("TMPDIR") or "/private/tmp"
        user = env.get("USER") or str(uid)
        cand += ["/private/tmp/uscreens", "/private/tmp/screens", f"/private/tmp/zellij-{uid}", os.path.join(tmpd, f"zellij-{uid}"),
                 f"/private/tmp/emacs{uid}", os.path.join(tmpd, f"emacs{uid}"), os.path.join(tmpd, f"nvim.{user}")]
        if not allow_docker:   # sockets as paths: SBPL's subpath also matches the file itself
            cand += list(_CONTAINER_SOCKETS)
    else:
        cand.append("/run/screen")
    work = _real(workdir) if workdir else None
    out = []
    for c in cand:
        r = _real(c)
        if r in ("/", _real(home)) or (work and _under(work, r)) or r in out:
            continue
        out.append(r)
    return out


def control_sockets(environ=None, allow_docker: bool = False) -> list[str]:
    """Single socket files to hide (Linux): the container engines' (unless allowed) and systemd's local sshd entry when this
    user can connect to them, a unix: DOCKER_HOST, and every listening socket this user owns right now (from /proc/net/unix —
    a tmux `-S`, an emacs / nvim server, an ssh-agent, … in any folder). Sockets created after the Agent started are not in
    this list (their usual directories are hidden whole: control_dirs, the runtime dir, /tmp)."""
    env = os.environ if environ is None else environ
    out: list[str] = []

    def add(p):
        try:
            r = _real(p)
            if os.path.exists(r) and stat.S_ISSOCK(os.stat(r).st_mode) and r not in out:
                out.append(r)
        except OSError:
            pass
    for p in _SYSTEM_SOCKETS:
        if os.access(p, os.W_OK):
            add(p)
    if not allow_docker:
        for p in _CONTAINER_SOCKETS:
            if os.access(p, os.W_OK):
                add(p)
        for k in _CONTAINER_ENV:
            v = env.get(k, "")
            if v.startswith("unix://"):
                add(v[len("unix://"):])
    uid = os.getuid()
    try:
        with open("/proc/net/unix", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()[1:]
    except OSError:
        lines = []
    for ln in lines:
        f = ln.split(None, 7)
        if len(f) < 8 or not f[7].startswith("/") or f[5] != "01" or int(f[3], 16) & 0x10000 == 0:
            continue                                    # only bound, listening, path (not abstract) sockets
        try:
            if os.lstat(f[7]).st_uid == uid:
                add(f[7])
        except (OSError, ValueError):
            pass
    return out


def ancestors(paths) -> list[str]:
    """Directories above the protected paths that this user could rename (their parent is writable), parents first.
    Renaming one would move agentj's code / state / a start-up file out of the way and let the Agent plant a new one at the
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


def bwrap_argv(st, workdir: str, home: str | None = None, runtime: str | None = None, allow_docker: bool = False,
               environ=None) -> list[str]:
    """The bubblewrap prefix (ends with "--"). Order matters: later mounts sit on top of earlier ones, so the state
    directory is hidden *after* everything that is put back, and only the permission folder returns on top of it."""
    env = os.environ if environ is None else environ
    home = _real(home or os.path.expanduser("~"))
    runtime = runtime if runtime is not None else env.get("XDG_RUNTIME_DIR", "")
    root, perm, work = _real(str(st.root)), _real(str(st.perm_dir)), _real(workdir)
    from .preferences import path as preferences_path
    prefs = _real(str(preferences_path().parent))
    a = [shutil.which("bwrap") or "bwrap", "--dev-bind", "/", "/", "--unshare-pid", "--proc", "/proc",
         "--die-with-parent", "--new-session"]
    hidden = [d for d in ("/tmp", "/var/tmp") if os.path.isdir(d)]
    for rt in (runtime, f"/run/user/{os.getuid()}"):    # also when XDG_RUNTIME_DIR is not set (a bare service / ssh)
        if rt and os.path.isdir(rt) and _real(rt) not in hidden:
            hidden.append(_real(rt))
    for d in hidden:
        a += ["--tmpfs", d]
    for rt in (d for d in hidden if d not in ("/tmp", "/var/tmp")):   # the runtime dir(s)
        for rel in _RUNTIME_BACK:
            p = os.path.join(rt, rel)
            if os.path.exists(p):
                a += ["--bind", p, p]
    back = [p for p in (home, work) if any(_under(p, d) for d in hidden)]   # a HOME or folder under /tmp (tests)
    for p in sorted(set(back), key=len):
        a += ["--bind", p, p]
    visible = lambda p: not any(_under(p, d) for d in hidden) or any(_under(p, b) for b in back)  # noqa: E731
    ro = [p for p in (os.path.join(home, rel) for rel in _RO_FILES) if os.path.lexists(p) and visible(_real(p))]
    code = code_paths()  # F14: bind back writable when installed under private /tmp.
    for d in ancestors([_real(p) for p in ro] + code + [root, prefs]):   # pinned: cannot be renamed away (still writable inside)
        if visible(d):
            a += ["--bind", d, d]
    for p in ro:
        a += ["--ro-bind", p, p]
    for p in code:
        a += ["--bind", p, p]
    # other programs' control sockets (G-A56): whole folders become an empty tmpfs, single sockets a read-only /dev/null
    dirs = [d for d in control_dirs(home, env, allow_docker, work) if os.path.isdir(d) and visible(d) and not _under(d, root)]
    for d in dirs:
        a += ["--tmpfs", d]
    socks = [p for p in control_sockets(env, allow_docker)
             if visible(p) and not _under(p, root) and not any(_under(p, d) for d in dirs)]
    for p in socks[:MAX_SOCKETS]:
        a += ["--ro-bind", "/dev/null", p]
    for d in other_states(st, home):     # a second (old) state directory: hidden whole; the legacy symlink resolves to root
        if visible(d):
            a += ["--tmpfs", d]
    if os.path.isdir(prefs) and visible(prefs):
        a += ["--tmpfs", prefs]
    a += ["--tmpfs", root, "--bind", perm, perm]
    for k in hide_env(env, allow_docker):
        a += ["--unsetenv", k]
    a += ["--chdir", work, "--"]
    return a


SANDBOX_EXEC = "/usr/bin/sandbox-exec"
_MAC_RO = (".ssh/environment",)   # plus _RO_FILES (~/.ssh/environment is read by sshd only when PermitUserEnvironment)
_LAUNCHD_SOCKETS = r"^/private/tmp/com\.apple\.launchd\.[^/]+/"   # ssh-agent (SSH_AUTH_SOCK) and other launchd sockets
# VS Code's CLI IPC and kitty's remote control in the shared per-user temp folders (paths with a random part: a regex)
_MAC_TMP_SOCKETS = r"^/private/(tmp|var/folders/[^/]+/[^/]+/T)/(vscode-ipc-|vscode-git-|kitty)"


def sbpl_profile(st, workdir: str, home: str | None = None, uid: int | None = None, allow_docker: bool = False,
                 environ=None) -> tuple[str, dict]:
    """(profile text, -D parameters) for `sandbox-exec` (macOS). Paths travel as parameters, never spliced into the profile,
    so a folder name cannot change the rules. Later rules win in SBPL: the permission folder is allowed back after the state
    directory is denied, exactly like the bwrap mount order."""
    home = _real(home or os.path.expanduser("~"))
    uid = os.getuid() if uid is None else uid
    from .preferences import path as preferences_path
    params = {"PREFERENCES": _real(str(preferences_path().parent)), "STATE": _real(str(st.root)), "PERM": _real(str(st.perm_dir)), "TMUX": f"/private/tmp/tmux-{uid}"}
    alt = list(dict.fromkeys(other_states(st, home) + [p for p in state_aliases(st, home) if p != params["STATE"]]))
    for i, p in enumerate(alt):
        params[f"STATE_ALT_{i}"] = p
    ro = [os.path.join(home, rel) for rel in _RO_FILES + _MAC_RO]
    ro = [_real(p) if os.path.lexists(p) else p for p in ro]   # a start-up file that does not exist yet stays protected
    ro = list(dict.fromkeys(ro))
    for i, p in enumerate(ro):
        params[f"RO_{i}"] = p
    pin = ancestors(ro + [params["STATE"], params["PREFERENCES"]])
    for i, p in enumerate(pin):
        params[f"PIN_{i}"] = p
    n_ro = len(ro)
    ctl = control_dirs(home, environ, allow_docker, workdir, mac=True, uid=uid)   # absent ones too: a server may start later
    for i, p in enumerate(ctl):
        params[f"CTL_{i}"] = p
    lines = [
        "(version 1)",
        "(allow default)",
        '(deny file-read* file-write* (subpath (param "PREFERENCES")))',
        ";; 1. the host's state directory: invisible and unconnectable, except the permission tool's folder",
        '(deny file-read* file-write* (subpath (param "STATE")))',
        '(deny network-outbound (remote unix-socket (subpath (param "STATE"))))',
        *[r for i in range(len(alt)) for r in (f'(deny file-read* file-write* (subpath (param "STATE_ALT_{i}")))',
                                               f'(deny network-outbound (remote unix-socket (subpath (param "STATE_ALT_{i}"))))')],
        '(allow file-read* file-write* (subpath (param "PERM")))',
        '(allow network-outbound (remote unix-socket (subpath (param "PERM"))))',
        ";; 2. credential files (authorized_keys, .Xauthority, .ssh/environment): read-only (also when absent)",
        "(deny file-write*",
        *[f'  (subpath (param "RO_{i}"))' for i in range(n_ro)],
        ")",
        ";; ... and the directories above them cannot be renamed away (creating files inside them stays allowed)",
        "(deny file-write*",
        *[f'  (literal (param "PIN_{i}"))' for i in range(len(pin))],
        ")",
        ";; 3. agentj's processes (and every other process outside this sandbox): no signals, no process information",
        "(deny signal)",
        "(allow signal (target same-sandbox))",
        "(deny process-info*)",
        "(allow process-info* (target same-sandbox))",
        ";; 4. other sessions' terminals and apps (launchd jobs are the Agent's own to manage, F14)",
        "(deny appleevent-send)",
        "(deny lsopen)",
        '(deny network-outbound (remote unix-socket (subpath (param "TMUX"))))',
        f'(deny network-outbound (remote unix-socket (regex #"{_LAUNCHD_SOCKETS}")))',
        ";; 5. other programs' control sockets (herdr, screen, zellij, wezterm, emacs, nvim, Jupyter; the container engines",
        ";;    unless the human allowed them): not readable, not writable, not connectable (G-A56)",
        *([] if not ctl else ["(deny file-read* file-write*", *[f'  (subpath (param "CTL_{i}"))' for i in range(len(ctl))], ")",
                              "(deny network-outbound (remote unix-socket", *[f'  (subpath (param "CTL_{i}"))' for i in range(len(ctl))],
                              "))"]),
        f'(deny network-outbound (remote unix-socket (regex #"{_MAC_TMP_SOCKETS}")))',
    ]
    return "\n".join(lines) + "\n", params


def sandbox_argv(st, workdir: str, home: str | None = None, allow_docker: bool = False, environ=None) -> list[str]:
    """The sandbox-exec prefix (macOS). The display / session variables are unset with /usr/bin/env, like bwrap's --unsetenv."""
    prof, params = sbpl_profile(st, workdir, home, allow_docker=allow_docker, environ=environ)
    a = [SANDBOX_EXEC, "-p", prof]
    for k, v in params.items():
        a += ["-D", f"{k}={v}"]
    a.append("/usr/bin/env")
    for k in hide_env(environ, allow_docker):
        a += ["-u", k]
    return a


def fence_argv(st, workdir: str, allow_docker: bool = False) -> list[str]:
    return (sandbox_argv(st, workdir, allow_docker=allow_docker) if sys.platform == "darwin"
            else bwrap_argv(st, workdir, allow_docker=allow_docker))


def kind() -> str:
    return "sandbox-exec" if sys.platform == "darwin" else "bubblewrap"


_probe_cache: dict[str, str | None] = {}


def apparmor_userns_policy() -> bool | None:
    """Public kernel policy only; None when unavailable, never changes it."""
    if sys.platform != "linux":
        return None
    try:
        with open("/proc/sys/kernel/apparmor_restrict_unprivileged_userns") as f:
            value = f.read(16).strip()
            return value == "1" if value in ("0", "1") else None
    except OSError:
        return None


def apparmor_userns_restricted() -> bool:
    return apparmor_userns_policy() is True


def apparmor_hint() -> str:
    """Per-binary Ubuntu 24.04+ fix; administrators review existing profiles first."""
    return ("Ubuntu 24.04+：保持 kernel.apparmor_restrict_unprivileged_userns=1。由管理员检查已有 bwrap profile；"
            "若缺失，在 /etc/apparmor.d/bwrap-agentj 放下列配置（仅 /usr/bin/bwrap 获 userns，不覆盖已有 profile）：\n"
            "abi <abi/4.0>,\ninclude <tunables/global>\n"
            "profile bwrap-agentj /usr/bin/bwrap flags=(unconfined) {\n  userns,\n}\n"
            "sudo apparmor_parser -r /etc/apparmor.d/bwrap-agentj；然后 agentj doctor --offline。"
            " / Keep the system restriction enabled; an administrator can permit userns only for /usr/bin/bwrap "
            "with this profile and reload it. Review existing profiles first; rerun doctor after repair.")


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
                _probe_cache[key] = None if r.returncode == 0 else ("apparmor_userns" if apparmor_userns_restricted() else "bwrap_failed")
        except (OSError, subprocess.TimeoutExpired):
            _probe_cache[key] = "sandbox_failed" if sys.platform == "darwin" else "bwrap_failed"
    return _probe_cache[key]


def wrap(st, argv: list[str], workdir: str, allow_docker: bool = False) -> list[str]:
    if sys.platform == "darwin":
        return sandbox_argv(st, workdir, allow_docker=allow_docker) + argv
    a = bwrap_argv(st, workdir, allow_docker=allow_docker)
    exe = _real(shutil.which(argv[0]) or argv[0])
    if os.path.isfile(exe) and any(_under(exe, d) for d in ("/tmp", "/var/tmp")):
        a[-1:-1] = ["--ro-bind", exe, exe]   # an agent binary under /tmp (tests): only that file comes back, read-only
    return a + argv


REASONS = {
    "unsupported_os": "这个系统上还没有 Agent 隔离（支持 Linux 与 macOS；Windows 请用 WSL2）",
    "no_bwrap": "没找到 bubblewrap（bwrap），Agent 隔离起不来：装上它（apt / dnf / pacman install bubblewrap）",
    "apparmor_userns": "bubblewrap 起不来且 AppArmor userns 限制开启；请用 agentj doctor 查看仅 /usr/bin/bwrap 的修法",
    "bwrap_failed": "bubblewrap 起不来（多半是系统禁止了非特权用户命名空间，或在容器里），Agent 隔离起不来",
    "no_sandbox_exec": "这台 Mac 上没有 /usr/bin/sandbox-exec，Agent 隔离起不来",
    "sandbox_failed": "macOS 沙箱（sandbox-exec）起不来（Agent J 自己是不是已经在别的沙箱里运行？），Agent 隔离起不来",
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
