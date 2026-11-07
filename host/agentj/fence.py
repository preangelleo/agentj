"""Agent J fence; developer reference: documentation/notes/P0262-HOST-REFERENCE.md."""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys

_HIDE_ENV = ("WAYLAND_DISPLAY", "DISPLAY", "XAUTHORITY", "HYPRLAND_INSTANCE_SIGNATURE",
             "SWAYSOCK", "I3SOCK", "TMUX", "TMUX_PANE", "STY", "SSH_AUTH_SOCK", "GNOME_KEYRING_CONTROL", "KITTY_LISTEN_ON",
             "AGENTJ_STATE_DIR", "AGENTJARVIS_STATE_DIR", "EMACS_SOCKET_NAME", "SCREENDIR", "JUPYTER_RUNTIME_DIR",
             "DBUS_SESSION_BUS_ADDRESS", "DBUS_STARTER_ADDRESS", "DBUS_STARTER_BUS_TYPE", "SYSTEMD_BUS_ADDRESS")
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
# environment.d and compositor configs remain general configuration; user unit paths are protected below.
_RO_FILES = (".ssh/authorized_keys", ".ssh/authorized_keys2", ".Xauthority")
# P591: no host session bus or user manager is mounted into an Agent fence.
_RUNTIME_BACK = ()


# P205: set inside every fence this version starts (bwrap --setenv / sandbox-exec's env). The fenced `agentj` reads it to know
# that the host's state directory it sees is the empty mount, not a missing installation (doctor: `unverifiable`, update
# apply: host checks over elevate.sock). Only ever narrows what a process may claim; never a permission.
FENCED_ENV = "AGENTJ_FENCED"
_SOCK_ENV = ("AGENTJ_ELEVATE_SOCK", "AGENTJ_PERM_SOCK", "AGENTJ_CAPABILITY_SOCK", "AGENTJ_RECALL_SOCK")
MOUNTINFO = "/proc/self/mountinfo"


def inside(environ=None) -> bool:
    """Developer reference: documentation/notes/P591-FENCE-REFERENCE.md."""
    env = os.environ if environ is None else environ
    if env.get(FENCED_ENV) == "1":
        return True
    for k in _SOCK_ENV:
        sock = env.get(k) or ""
        if os.path.basename(os.path.dirname(sock)) == "agentperm":
            root = os.path.dirname(os.path.dirname(sock))
            break
    else:
        return False
    if sys.platform == "darwin":
        try:
            os.listdir(root)
        except PermissionError:
            return True
        return False
    real = os.path.realpath(root)
    try:
        with open(MOUNTINFO, encoding="utf-8", errors="replace") as f:
            for line in f:
                head, _, tail = line.partition(" - ")
                parts = head.split()
                if len(parts) > 4 and parts[4].replace("\\040", " ") == real and tail.split()[:1] == ["tmpfs"]:
                    return True
    except OSError:
        pass
    return False


# #467 (ADR-0269-w4-fence): the one answer of a fenced `agentj` command that needs the host's state directory. Inside the
# fence that directory is an empty mount (Linux) or denied (macOS): "not initialised / no Agent / no tasks" there misled an
# Agent into "the host was wiped" (tickets st_am7… / st_lbQ…). Never suggests init or re-pairing.
HIDDEN_STATE = ("在 Agent 的隔离环境里看不到主机状态（这里的状态目录是空的），这不代表任务、配对或设置丢了，不用重新初始化或重新配对。"
                "{also}只读查看：agentj status、agentj tasks list、agentj devices（由主机回答）；要改设置，请主人在手机上操作，"
                "或在 Agent J 主机终端（隔离外）运行这条命令。 / The host's state is hidden inside the Agent's isolation (its folder "
                "is empty here); that does not mean tasks, pairing or settings are lost, and nothing needs re-initialising or "
                "re-pairing. Read-only: agentj status, agentj tasks list, agentj devices (answered by the host); to change "
                "settings, ask the owner on the phone or run this command in a terminal on the Agent J host (outside the fence).")


def hidden_state(also: str = "") -> str:
    """#467: honest fenced replacement for every "not initialised" / "no Agent" answer (bilingual, one line)."""
    return HIDDEN_STATE.format(also=also)


def _real(p: str) -> str:
    return os.path.realpath(os.path.expanduser(p))


def code_paths() -> list[str]:
    """Developer reference: documentation/notes/P591-FENCE-REFERENCE.md."""
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


def code_ro(home: str | None = None) -> list[str]:
    """#455 F3 B / N4-1: an installed agentj's venv + uv interpreter folder, read-only in the fence (not a checkout)."""
    pkg = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(pkg)
    if sys.prefix == sys.base_prefix or (os.path.exists(os.path.join(parent, "agentj"))
                                         and os.path.exists(os.path.join(parent, "pyproject.toml"))):
        return []
    h, venv, base = _real(home or "~"), _real(sys.prefix), _real(sys.base_prefix)
    if not _under(_real(pkg), venv):
        return []
    up = os.path.dirname(base)       # uv: …/uv/python holds the cpython-3.13 link and its target
    out = [venv, up if os.path.basename(up) == "python" and os.path.basename(os.path.dirname(up)) == "uv" else base]
    return sorted({p for p in out if _under(p, h) and p != h and p not in (_real(h + "/.local"), _real(h + "/.local/share"))})


def ro_trees(home: str | None = None, environ=None) -> list[str]:
    """#455 F3 B: harness install trees + the installed agentj (read-only in every fence)."""
    from .binaries import trees
    env = dict(os.environ if environ is None else environ)
    if home:
        env["HOME"] = home
    if not sys.platform.startswith("linux"):
        return []
    return sorted({t for _, t in trees(env)} | set(code_ro(env.get("HOME"))))


SELF_UPDATE = re.compile(r"(?is)(?:auto-?update|self-?update|upgrade|update failed).{0,300}?(?:read-only file system|erofs|eacces|"
                         r"permission denied)|(?:read-only file system|erofs).{0,300}?(?:/installs/|/versions/|\.opencode)")
SELF_UPDATE_TEXT = {"zh": "在隔离环境里不能自更新 {name}（安装目录在隔离里只读）。请在手机发 /update，或让主机更新。",
                    "en": "{name} cannot update itself inside the fence (its install folder is read-only there). "
                          "Send /update from the phone, or let the Agent J host update it."}
_NAMES = {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode"}


def self_update_note(kind: str, text: str, lang: str = "zh") -> str | None:
    """#455 F3 B ②: a harness's own update failed on its read-only tree → one clear line."""
    if not isinstance(text, str) or not SELF_UPDATE.search(text[-4000:]):
        return None
    return SELF_UPDATE_TEXT["en" if lang == "en" else "zh"].format(name=_NAMES.get(kind, kind))


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
    from .instance import foreign_paths   # P180: the other instances' state / preferences, the other seats' workspaces
    out += [p for p in foreign_paths(st, home) if p not in out]
    # P393 (#120): the per-user shared ASR model folder — host data like a state folder, hidden from every instance's Agent.
    # Created (empty, 0700) here outside the fence so it is already hidden before the first install fills it.
    try:
        from .asr import shared_root
        shared = shared_root(home)
        if not inside() and not os.path.lexists(shared):
            os.makedirs(shared, mode=0o700, exist_ok=True)
        r = _real(str(shared))
        h = _real(home or os.path.expanduser("~"))
        if (os.path.isdir(r) and not os.path.islink(shared) and r not in out and not _under(r, root) and not _under(root, r)
                and r != h and not _under(h, r)):
            out.append(r)
    except Exception:  # noqa: BLE001 — never a reason not to start the fence
        pass
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


_MAIN_ENV = frozenset({
    "PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "PYTHONNOUSERSITE", "MCP_TOOL_TIMEOUT", "TERM", "COLORTERM",
    # Existing one-time permission-tool claim in serve.new_perm_env (unchanged).
    "AGENTJ_PERM_SOCK", "AGENTJ_PERM_TOKEN", "AGENTJ_PERM_WAIT", "AGENTJ_PERM_CLAIM",
    # P180: which instance the Agent's own `agentj …` calls belong to (a number; its state stays hidden all the same)
    "AGENTJ_INSTANCE",
    # Already exported by serve.start BEFORE every harness spawn; socket mounts unchanged.
    "AGENTJ_ELEVATE_SOCK", "AGENTJ_CAPABILITY_SOCK", "AGENTJ_RECALL_SOCK", "AGENTJ_FRIENDS_SOCK", "AGENTJ_RECALL_INDEX",
    # Existing per-launch local OpenCode server authentication in opencode_env (unchanged).
    "OPENCODE_SERVER_PASSWORD", "OPENCODE_SERVER_USERNAME", "OPENCODE_CONFIG_CONTENT", "OPENCODE_CONFIG", "OPENCODE_CONFIG_DIR",
    "FAKE_CLAUDE_LOG", "FAKE_CLAUDE_SCRIPT", "FAKE_CLAUDE_USER_SETTINGS", "FAKE_PREP_SLEEP", "FAKE_NO_HANDOVER",
    "FAKE_CTX_USED", "FAKE_FRESH_WINDOWS", "FAKE_CX_LOG", "FAKE_CX_SANDBOX", "FAKE_CX_POLICY", "FAKE_CX_THREADS",
    "FAKE_CX_THREAD_SANDBOX", "FAKE_OC_LOG", "FAKE_OC_AGENT_RULES", "FAKE_OC_STORE", "FAKE_OC_ENV_LOG", "FAKE_OC_SESSIONS",
    "FAKE_OC_SUBAGENTS", "FAKE_OC2_SUBAGENTS", "FAKE_OC2_NO_INHERIT", "FAKE_OC2_GENERAL_RULES",    # P426 stand-ins (tests)
})


def launch_environment(base=None, allow_docker: bool = False, kind: str = "codex") -> dict:
    """Strict subset of the existing child environment; adds no values or authority.

    Reuse the established harness whitelist and the main-session tool claim names.
    Filtering occurs before bwrap exec, not just before its child starts.
    """
    from .peer_session import clean_env, codex_user_model, SECRETISH
    env = dict(os.environ if base is None else base)
    keep = set(_MAIN_ENV | (frozenset(_CONTAINER_ENV) if allow_docker else frozenset()))
    if kind == "opencode":
        # Main-session configuration is constructed before this boundary. Peer
        # sessions build their own configuration after clean_env; their narrower
        # OPENCODE_DISABLE_* carry rule must not erase the main session's config.
        # Preserve original bytes only, never arbitrary secret-looking settings.
        keep.update(name for name in env if name.startswith("OPENCODE_") and not SECRETISH.search(name))
    if kind == "kimi":
        # P305: Kimi's own settings (KIMI_CODE_HOME, KIMI_CODE_OAUTH_HOST …) — names that look like secrets stay out; an API key
        # reaches Kimi only as agent_kimi.key_environment's in-memory model variables, added after this filter.
        keep.update(name for name in env if name.startswith("KIMI_") and not SECRETISH.search(name))
    if kind == "qwen":
        # P310: Qwen's own settings (QWEN_HOME …) — names that look like secrets stay out; a key reaches Qwen only as
        # agent_qwen.key_environment's OPENAI_* variables, added after this filter.
        keep.update(name for name in env if name.startswith("QWEN_") and not SECRETISH.search(name))
    if kind == "dsh":
        # P329: dsh's own settings (DSH_HOME …) — names that look like secrets stay out; a key reaches dsh only as
        # agent_dsh.key_environment's variables, added after this filter (the forced read-only preset too).
        keep.update(name for name in env if name.startswith("DSH_") and not SECRETISH.search(name))
    if kind == "codex":
        # Same owner-selected native provider reference used by peer_session.
        # Only its env_key NAME is added; values still come from the original input.
        import re
        name = (codex_user_model(env).get("provider_table") or {}).get("env_key")
        if isinstance(name, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name):
            keep.add(name)
    clean = clean_env(kind, env, extra_keep=keep)
    hidden = set(hide_env(env, allow_docker))
    return {name: env[name] for name in clean if name in env and name not in hidden}


# P348 (2026-10-16): the owner's own automation — scheduled tasks, workflow CEOs, goal countdowns — may use any plan key
# (Ark / Bailian Coding Plan, Bailian Token Plan, Kimi For Coding membership); a support bot serves other people, so it
# never gets one (bots/provider.py asks for a pay-as-you-go key).
PLAN_KEY_ENV = ("ARK_CODING_PLAN_API_KEY", "BAILIAN_TOKEN_PLAN_API_KEY", "KIMI_API_KEY",
                "ARK_AGENT_PLAN_API_KEY")   # P395: Ark's Agent Plan


def _host_elevate_socket(host_state):
    from .state import State
    from .elevate import SOCK_NAME
    if not isinstance(host_state, State):
        return None
    try:
        raw = os.fspath(host_state.perm_dir)
        if '..' in raw.split(os.sep):
            return None
        directory = os.path.abspath(raw)
        current = directory
        while True:
            if stat.S_ISLNK(os.lstat(current).st_mode):
                return None
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
        folder = os.lstat(directory)
        path = os.path.join(directory, SOCK_NAME)
        endpoint = os.lstat(path)
        if not stat.S_ISDIR(folder.st_mode) or folder.st_uid != os.getuid() or stat.S_IMODE(folder.st_mode) != 0o700:
            return None
        if not stat.S_ISSOCK(endpoint.st_mode) or endpoint.st_uid != os.getuid() or stat.S_IMODE(endpoint.st_mode) != 0o600:
            return None
        return path
    except (OSError, TypeError, ValueError):
        return None


def auxiliary_environment(base=None, kind: str = "claude", extra_keep=(), plan_keys: bool = False, host_state=None) -> dict:
    """Tasks/bots authenticate from native storage, never serve's secret environment — except the plan keys
    (PLAN_KEY_ENV) when `plan_keys` (the owner's own tasks; bots never pass it).

    The permission socket claim is generated per task, not inherited host authority.
    """
    from .peer_session import clean_env
    env = dict(os.environ if base is None else base)
    plans = set(PLAN_KEY_ENV) if plan_keys else set()
    extra_keep = set(extra_keep) | {k for k in _MAIN_ENV if k.startswith("FAKE_")} | {"AGENTJ_INSTANCE"}
    if host_state is not None:
        env.pop("AGENTJ_ELEVATE_SOCK", None)
        extra_keep.discard("AGENTJ_ELEVATE_SOCK")
        endpoint = _host_elevate_socket(host_state)
        if endpoint is not None:
            env["AGENTJ_ELEVATE_SOCK"] = endpoint
            extra_keep.add("AGENTJ_ELEVATE_SOCK")
    clean = clean_env(kind, env, extra_keep=extra_keep | plans)
    hidden = set(hide_env(env))
    return {k: v for k, v in clean.items() if k not in hidden
            and not k.startswith("AGENTSRELAY_")
            and (k in plans or not (k.endswith(("_TOKEN", "_API_KEY")) or k == "CLAUDE_CODE_OAUTH_TOKEN"))
            and (k in extra_keep or k in plans or not k.endswith("_KEY"))}


def _env_dirs(env) -> list[str]:
    """Socket directories named by the environment (a herdr / screen / tmux / Jupyter started with a custom location)."""
    out = []
    for k in ("HERDR_SOCKET_PATH", "HERDR_CLIENT_SOCKET_PATH"):
        if env.get(k, "").startswith("/"):
            out.append(os.path.dirname(env[k]))
    for k in ("SCREENDIR", "JUPYTER_RUNTIME_DIR",
             "DBUS_SESSION_BUS_ADDRESS", "DBUS_STARTER_ADDRESS", "DBUS_STARTER_BUS_TYPE", "SYSTEMD_BUS_ADDRESS"):
        if env.get(k, "").startswith("/"):
            out.append(env[k])
    if env.get("TMUX_TMPDIR", "").startswith("/"):
        out.append(os.path.join(env["TMUX_TMPDIR"], f"tmux-{os.getuid()}"))
    if env.get("XDG_CONFIG_HOME", "").startswith("/"):
        out.append(os.path.join(env["XDG_CONFIG_HOME"], "herdr"))
    return out


def control_dirs(home: str, environ=None, allow_docker: bool = False, workdir: str | None = None,
                 mac: bool | None = None, uid: int | None = None) -> list[str]:
    """Developer reference: documentation/notes/P591-FENCE-REFERENCE.md."""
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
    """Developer reference: documentation/notes/P591-FENCE-REFERENCE.md."""
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
    """Developer reference: documentation/notes/P591-FENCE-REFERENCE.md."""
    out = set()
    for p in paths:
        a = os.path.dirname(p)
        while a and a != os.sep:
            par = os.path.dirname(a)
            if os.path.isdir(a) and os.access(par, os.W_OK):
                out.add(a)
            a = par
    return sorted(out, key=lambda x: (x.count(os.sep), x))


def _manager_unit_paths(environ) -> list[str]:
    """Developer reference: documentation/notes/P591-FENCE-REFERENCE.md."""
    if os.environ.get("AGENTJ_TEST") == "1":
        return []
    canonical = f"/run/user/{os.getuid()}"
    runtime = canonical if any(os.path.exists(os.path.join(canonical, p)) for p in
                               ("bus", "systemd/private")) else environ.get("XDG_RUNTIME_DIR") or canonical
    if not any(os.path.exists(os.path.join(runtime, p)) for p in ("bus", "systemd/private")):
        return []
    busctl = shutil.which("busctl", path="/usr/bin:/bin")
    if not busctl:
        raise OSError("cannot verify user manager unit paths")
    env = {"PATH": "/usr/bin:/bin", "LANG": "C", "XDG_RUNTIME_DIR": runtime}
    import json
    if not os.path.exists(os.path.join(runtime, "systemd/private")):
        probe = subprocess.run([busctl, "--user", "--json=short", "call", "org.freedesktop.DBus",
                                "/org/freedesktop/DBus", "org.freedesktop.DBus", "NameHasOwner", "s",
                                "org.freedesktop.systemd1"], env=env, capture_output=True, text=True, timeout=5)
        try:
            owner = json.loads(probe.stdout) if probe.returncode == 0 and len(probe.stdout) < 1024 else {}
            if owner == {"type": "b", "data": [False]}:
                return []
            if owner != {"type": "b", "data": [True]}:
                raise ValueError
        except (ValueError, TypeError):
            raise OSError("cannot verify user manager presence") from None
    r = subprocess.run([busctl, "--user", "--json=short", "get-property", "org.freedesktop.systemd1",
                        "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager", "UnitPath"],
                       env=env, capture_output=True, text=True, timeout=5)
    import json
    try:
        v = json.loads(r.stdout) if r.returncode == 0 and len(r.stdout) <= 1048576 else {}
        paths = v.get("data")
        if v.get("type") != "as" or not isinstance(paths, list) or len(paths) > 2048 or any(
                not isinstance(p, str) or not p.startswith("/") or len(p) > 4096 for p in paths):
            raise ValueError
        return paths
    except (ValueError, AttributeError):
        raise OSError("cannot verify user manager unit paths") from None


def service_paths(home: str, environ=None) -> list[str]:
    """Developer reference: documentation/notes/P591-FENCE-REFERENCE.md."""
    env = os.environ if environ is None else environ
    roots = [os.path.join(home, ".config/systemd"), os.path.join(home, ".local/share/systemd")]
    roots += [os.path.join(env[k], "systemd") for k in ("XDG_CONFIG_HOME", "XDG_DATA_HOME")
              if env.get(k, "").startswith("/")]
    for key, default in (("XDG_CONFIG_DIRS", "/etc/xdg"), ("XDG_DATA_DIRS", "/usr/local/share:/usr/share")):
        roots += [os.path.join(p, "systemd/user") for p in (env.get(key) or default).split(":") if p.startswith("/")]
    # Empty override entries retain defaults; retaining them even without an empty
    # entry narrows authority and also covers a live manager with an older env.
    roots += [os.path.abspath(p) for p in env.get("SYSTEMD_UNIT_PATH", "").split(":") if p]
    roots += _manager_unit_paths(env)
    return sorted(set(roots + [_real(p) for p in roots]))


def service_roots(home: str, environ=None, visible=lambda p: True) -> list[str]:
    """The user service dirs + the resolved targets of unit symlinks inside them (the fence makes all of them read-only)."""
    roots = [p for p in service_paths(home, environ) if visible(p)]
    if any(p == "/" or _under(_real(p), "/proc") for p in roots):
        raise OSError("unit path conflicts with process isolation")
    # A unit symlink may point to a writable project file outside these directories.
    # Protect its resolved target too; a read-only directory alone cannot do that.
    targets, seen = set(), set()
    pending = list(roots)
    while pending:
        base = pending.pop()
        real = _real(base)
        if real in seen or not os.path.isdir(base):
            continue
        seen.add(real)
        if len(seen) > 1024:
            raise ValueError("user service alias tree too large")
        for entry in os.scandir(base):
            if entry.is_symlink() and (entry.is_file() or entry.is_dir()):
                targets.add(_real(entry.path))
            if entry.is_dir():
                pending.append(entry.path)
    return roots + sorted(p for p in targets if visible(p) and p not in roots)


def service_mounts(home: str, environ=None, visible=lambda p: True) -> list[str]:
    """Developer reference: documentation/notes/P591-FENCE-REFERENCE.md."""
    roots, views, a = service_roots(home, environ, visible), set(), []
    for root in roots:
        p = root
        while not os.path.exists(p):
            p = os.path.dirname(p)
        if p != root:
            views.add(p)
        alias = root
        while alias != os.path.dirname(alias):
            if os.path.islink(alias):
                views.add(os.path.dirname(alias))
            alias = os.path.dirname(alias)
    if "/" in views:
        raise OSError("unit path needs unsupported root directory view")
    views = sorted(p for p in views if not any(q != p and _under(p, q) for q in views))
    def mount(p):
        if p in roots:
            if os.path.exists(p):
                a.extend(["--ro-bind", _real(p), p])
            else:
                a.extend(["--tmpfs", p, "--remount-ro", p])
            return
        a.extend(["--tmpfs", p])
        children = set(os.listdir(p)) if os.path.isdir(p) else set()
        children |= {q[len(p.rstrip("/")) + 1:].split("/", 1)[0] for q in roots if _under(q, p) and q != p}
        if len(children) > 1024:
            raise ValueError("user service directory view too large")
        for name in sorted(children):
            child = os.path.join(p, name)
            if any(_under(q, child) for q in roots):
                mount(child)
            elif os.path.islink(child):
                a.extend(["--symlink", os.readlink(child), child])
            else:
                a.extend(["--bind", child, child])
        a.extend(["--remount-ro", p])
    # Do this before control-socket hiding: ancestor binds must never undo a deny mount.
    pins = set()
    for root in roots:
        p = os.path.dirname(root)
        while p != os.path.dirname(p):
            if os.path.isdir(p) and visible(p):
                pins.add(p)
            p = os.path.dirname(p)
    for p in sorted(pins, key=lambda p: (p.count(os.sep), p)):
        a.extend(["--bind", p, p])
    for p in views:
        mount(p)
    for p in roots:
        if not any(_under(p, view) for view in views):
            mount(p)
    return a


def _tmp_hidden(runtime) -> list[str]:
    hidden = [d for d in ("/tmp", "/var/tmp") if os.path.isdir(d)]
    for rt in (runtime, f"/run/user/{os.getuid()}"):    # also when XDG_RUNTIME_DIR is not set (a bare service / ssh)
        if rt and os.path.isdir(rt) and _real(rt) not in hidden:
            hidden.append(_real(rt))
    return hidden


def guard_parts(st, home: str, environ=None, workdir: str | None = None, allow_docker: bool = False, mac: bool = False,
                visible=lambda p: True, uid: int | None = None, service: bool = True) -> dict:
    """ONE list (#428 round 2, Info2): what the fence hides or makes read-only besides the temp dirs — bwrap_argv mounts it,
    host_write_refusal refuses serve's host writes into it. Paths: root / perm / prefs / other real; ro joined (not resolved)."""
    from .preferences import path as preferences_path
    return {"root": _real(str(st.root)), "perm": _real(str(st.perm_dir)), "prefs": _real(str(preferences_path().parent)),
            "other": other_states(st, home), "ro": [os.path.join(home, r) for r in _RO_FILES + (_MAC_RO if mac else ())],
            "service": service_roots(home, environ, visible) if service else [],
            "control": control_dirs(home, environ, allow_docker, workdir, mac=True if mac else None, uid=uid)}


def _ident(p):
    try:
        s = os.stat(p)
    except (OSError, ValueError):
        return None
    return s.st_dev, s.st_ino


def host_write_refusal(st, path: str, home: str | None = None, environ=None, fold: bool | None = None) -> str | None:
    """#428: serve writes for the Agent (a plaza request) only where the Agent's own fence would let it write: never inside
    guard_parts or a temp / runtime dir (unless under HOME, which the fence binds back). Round 2 (new-H1): compared by
    identity — every existing ancestor of `path` is stat-ed against the (st_dev, st_ino) of each guarded path, so a case /
    Unicode variant (APFS, ext4 casefold) or another name for the same folder is refused too. → "guarded" | "temp" | None."""
    env = os.environ if environ is None else environ
    home = _real(home or os.path.expanduser("~"))
    g = guard_parts(st, home, env, mac=sys.platform == "darwin")
    guarded = [g["root"], g["perm"], g["prefs"], *g["other"], *g["ro"], *g["service"], *g["control"]]
    tmp = _tmp_hidden(env.get("XDG_RUNTIME_DIR", ""))
    import unicodedata
    fold = sys.platform == "darwin" if fold is None else fold      # round 3 (new-L2): APFS names, also not created yet
    norm = (lambda x: unicodedata.normalize("NFC", x).casefold()) if fold else (lambda x: x)
    if any(_under(norm(path), norm(_real(x))) for x in guarded):
        return "guarded"
    if any(_under(path, d) for d in tmp) and not _under(path, home):
        return "temp"
    gid, tid, hid = ({i for x in xs if (i := _ident(x))} for xs in (guarded, tmp, [home]))
    p, in_home = path, False
    while True:
        i = _ident(p)
        if i in gid:
            return "guarded"
        in_home = in_home or i in hid
        if i in tid and not in_home:
            return "temp"
        if p == os.path.dirname(p):
            return None
        p = os.path.dirname(p)


# 0.26.9 (ADR-0269-pty H1): a private /dev with its own devpts instance — the host's terminals (the web terminal's PTY, the
# owner's own terminal windows: /dev/pts/N, 0620 and this user's) cannot be opened from inside; every other device node and
# directory of the host's /dev is bound back (-try: one unplugged in between is skipped), its symlinks are recreated.
_DEV_OWN = frozenset(("pts", "ptmx", "tty", "console", "null", "zero", "full", "random", "urandom", "fd", "stdin", "stdout",
                      "stderr", "core", "shm"))
PRIVATE_DEV = [True]     # problem(): False when this kernel / bwrap cannot mount a devpts instance (then the old --dev-bind)


def dev_args(dev: str = "/dev") -> list[str]:
    if not PRIVATE_DEV[0]:
        return []
    a = ["--dev", "/dev"]
    try:
        names = sorted(os.listdir(dev))
    except OSError:
        return a
    import re
    for n in names:
        # virtual consoles and their screen memory (tty1…, vcs*) stay out too: someone logged in there owns them;
        # w2-sec L-7: so do keyboards / mice / HID (input/, uinput, hidraw*, hiddev*): reading them is a keylogger, uinput
        # types into the owner's desktop
        if n in _DEV_OWN or re.fullmatch(r"tty\d+|vcs[au]?\d*|input|uinput|hidraw\d*|hiddev\d*", n):
            continue
        p = os.path.join(dev, n)
        if os.path.islink(p):
            try:
                a += ["--symlink", os.readlink(p), "/dev/" + n]
            except OSError:
                pass
        else:
            a += ["--dev-bind-try", p, "/dev/" + n]
    return a


def bwrap_argv(st, workdir: str, home: str | None = None, runtime: str | None = None, allow_docker: bool = False,
               environ=None) -> list[str]:
    """Developer reference: documentation/notes/P591-FENCE-REFERENCE.md."""
    env = os.environ if environ is None else environ
    home = _real(home or os.path.expanduser("~"))
    runtime = runtime if runtime is not None else env.get("XDG_RUNTIME_DIR", "")
    root, perm, work = _real(str(st.root)), _real(str(st.perm_dir)), _real(workdir)
    from .preferences import path as preferences_path
    prefs = _real(str(preferences_path().parent))
    a = [shutil.which("bwrap") or "bwrap", "--dev-bind", "/", "/", "--unshare-pid", "--as-pid-1", "--proc", "/proc",
         "--die-with-parent", "--new-session", *dev_args()]
    hidden = _tmp_hidden(runtime)
    for d in hidden:
        a += ["--tmpfs", d]
    back = [p for p in (home, work) if any(_under(p, d) for d in hidden)]   # a HOME or folder under /tmp (tests)
    for p in sorted(set(back), key=len):
        a += ["--bind", p, p]
    visible = lambda p: not any(_under(p, d) for d in hidden) or any(_under(p, b) for b in back)  # noqa: E731
    g = guard_parts(st, home, env, work, allow_docker, visible=visible)    # the same list host_write_refusal refuses
    ro = [p for p in g["ro"] if os.path.lexists(p) and visible(_real(p))]
    code = code_paths()  # F14: bind back writable when installed under private /tmp.
    locked = [t for t in ro_trees(home, env) if visible(t)]   # #455 F3 B: harness trees + installed agentj, read-only
    for d in ancestors([_real(p) for p in ro] + code + locked + [root, prefs]):   # pinned: cannot be renamed away (still writable inside)
        if visible(d):
            a += ["--bind", d, d]
    for p in ro:
        a += ["--ro-bind", p, p]
    for p in code:
        a += ["--bind", p, p]
    a += service_mounts(home, env, visible)
    for p in locked:              # after service_mounts (it binds HOME's children back)
        a += ["--ro-bind", p, p]
    # other programs' control sockets (G-A56): whole folders become an empty tmpfs, single sockets a read-only /dev/null
    dirs = [d for d in g["control"] if os.path.isdir(d) and visible(d) and not _under(d, root)]
    for d in dirs:
        a += ["--tmpfs", d]
    socks = [p for p in control_sockets(env, allow_docker)
             if visible(p) and not _under(p, root) and not any(_under(p, d) for d in dirs)]
    for p in socks[:MAX_SOCKETS]:
        a += ["--ro-bind", "/dev/null", p]
    for d in g["other"]:     # a second (old) state directory: hidden whole; the legacy symlink resolves to root
        if visible(d):
            a += ["--tmpfs", d]
    if os.path.isdir(prefs) and visible(prefs):
        a += ["--tmpfs", prefs]
    a += ["--tmpfs", root, "--bind", perm, perm]
    for k in hide_env(env, allow_docker):
        a += ["--unsetenv", k]
    a += ["--setenv", FENCED_ENV, "1", "--chdir", work, "--"]
    return a


SANDBOX_EXEC = "/usr/bin/sandbox-exec"
_MAC_RO = (".ssh/environment",)   # plus _RO_FILES (~/.ssh/environment is read by sshd only when PermitUserEnvironment)
_LAUNCHD_SOCKETS = r"^/private/tmp/com\.apple\.launchd\.[^/]+/"   # ssh-agent (SSH_AUTH_SOCK) and other launchd sockets
# VS Code's CLI IPC and kitty's remote control in the shared per-user temp folders (paths with a random part: a regex)
_MAC_TMP_SOCKETS = r"^/private/(tmp|var/folders/[^/]+/[^/]+/T)/(vscode-ipc-|vscode-git-|kitty)"


def sbpl_profile(st, workdir: str, home: str | None = None, uid: int | None = None, allow_docker: bool = False,
                 environ=None) -> tuple[str, dict]:
    """Developer reference: documentation/notes/P591-FENCE-REFERENCE.md."""
    home = _real(home or os.path.expanduser("~"))
    uid = os.getuid() if uid is None else uid
    from .preferences import path as preferences_path
    params = {"PREFERENCES": _real(str(preferences_path().parent)), "STATE": _real(str(st.root)), "PERM": _real(str(st.perm_dir)), "TMUX": f"/private/tmp/tmux-{uid}"}
    g = guard_parts(st, home, environ, workdir, allow_docker, mac=True, uid=uid, service=False)   # the one list (round 3)
    alt = list(dict.fromkeys(g["other"] + [p for p in state_aliases(st, home) if p != params["STATE"]]))
    for i, p in enumerate(alt):
        params[f"STATE_ALT_{i}"] = p
    ro = [_real(p) if os.path.lexists(p) else p for p in g["ro"]]   # a start-up file that does not exist yet stays protected
    ro = list(dict.fromkeys(ro))
    for i, p in enumerate(ro):
        params[f"RO_{i}"] = p
    pin = ancestors(ro + [params["STATE"], params["PREFERENCES"]])
    for i, p in enumerate(pin):
        params[f"PIN_{i}"] = p
    n_ro = len(ro)
    ctl = g["control"]   # absent ones too: a server may start later
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
    return a + [f"{FENCED_ENV}=1"]


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
                r = subprocess.run(bwrap_argv(st, workdir) + ["/bin/sh", "-c", "exit 0"], capture_output=True, timeout=10, env=launch_environment())
                if r.returncode != 0 and PRIVATE_DEV[0]:    # 0.26.9: no devpts instance here → the host /dev as before
                    PRIVATE_DEV[0] = False
                    r = subprocess.run(bwrap_argv(st, workdir) + ["/bin/sh", "-c", "exit 0"], capture_output=True, timeout=10,
                                       env=launch_environment())
                    if r.returncode != 0:
                        PRIVATE_DEV[0] = True
                    else:
                        st.log("fence_private_dev", result="unavailable")
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
    "no_bwrap": "没找到 bubblewrap（bwrap），Agent 隔离起不来：在Agent J 主机上装上它（Ubuntu / Debian / WSL：sudo apt-get install -y bubblewrap socat；其它：dnf / pacman install bubblewrap）",
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
