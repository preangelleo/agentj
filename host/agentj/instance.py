"""P180 (public since P217, 0.20.2): several seats on one computer under one OS user — one numbered instance per seat.

`agentj --instance N <command>` (or AGENTJ_INSTANCE=N) selects instance N, 1 ≤ N ≤ MAX. Instance 1 is the ordinary install
and stays exactly where it always was (`~/.local/state/agentj`, unit `agentj` / label `net.agentj.host`,
`~/.config/agentj/config.json5`): `--instance 1` and no option are the same thing. Instance N ≥ 2 is a complete second host —
its own identity keys and channel (so its own relay connection and its own remotes), its own preferences, its own service:

    state        ~/.local/state/agentj-N
    preferences  ~/.config/agentj-N/config.json5
    service      agentj-N (systemd --user) / net.agentj.host.N (launchd)
    workspace    <seats root>/N   (seats root: $AGENTJ_SEATS_ROOT, else ~/agentj-seats)

Selecting an instance only sets three variables before anything reads them (AGENTJ_INSTANCE, AGENTJ_STATE_DIR,
AGENTJ_SERVICE_NAME); every module keeps reading its usual ones. The service unit carries AGENTJ_INSTANCE, so `serve`, the
recovery / auto-update jobs and the fenced Agent's own `agentj …` calls resolve the same instance again.

What stays shared on purpose: the installed program (one venv — `agentj update` restarts every instance's service), the
harness logins (~/.claude, ~/.codex, OpenCode: one subscription, one quota), the OS user's files. The fence hides the other
instances' state and preferences folders and the other seats' workspaces from each Agent; it is a data-visibility layer,
not a boundary between people (see the bundled skill agentj-multi-seat).
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

ENV = "AGENTJ_INSTANCE"
MAX = 10
SEATS_ENV = "AGENTJ_SEATS_ROOT"
DEFAULT_SEATS = "~/agentj-seats"
LABEL_FILE = "instance.json"       # {"label": "…"}: a local note for `agentj instances`, never sent anywhere
AUTO_CREATE = ("pair", "setup")    # commands that create a missing instance N ≥ 2 first


class InstanceError(ValueError):
    pass


def _num(v) -> int:
    s = str(v).strip()
    if not s.isdigit() or not 1 <= int(s) <= MAX:
        raise InstanceError(f"实例编号要在 1–{MAX} 之间 / instance must be 1–{MAX}: {v!r}")
    return int(s)


def split_argv(args: list[str]) -> tuple[int | None, list[str]]:
    """Take `--instance N` / `--instance=N` out of the arguments (before a bare `--`). → (N or None, the rest)."""
    out, n, i = [], None, 0
    while i < len(args):
        x = args[i]
        if x == "--":
            out += args[i:]
            break
        if x == "--instance" or x.startswith("--instance="):
            if x == "--instance":
                if i + 1 >= len(args):
                    raise InstanceError("--instance 后面要跟编号 / --instance needs a number")
                v, i = args[i + 1], i + 2
            else:
                v, i = x.split("=", 1)[1], i + 1
            m = _num(v)
            if n is not None and m != n:
                raise InstanceError("--instance 只能给一次 / give --instance once")
            n = m
            continue
        out.append(x)
        i += 1
    return n, out


def _home(home=None) -> str:
    return home or os.path.expanduser("~")


def state_root(n: int, home=None) -> pathlib.Path:
    from .state import DEFAULT_STATE
    base = os.path.join(_home(home), DEFAULT_STATE[2:])
    return pathlib.Path(base if n == 1 else f"{base}-{n}")


def prefs_dir(n: int, environ=None, home=None) -> pathlib.Path:
    e = os.environ if environ is None else environ
    base = pathlib.Path(os.path.join(home, ".config") if home else e.get("XDG_CONFIG_HOME") or pathlib.Path(_home()) / ".config")
    return base / ("agentj" if n == 1 else f"agentj-{n}")


def service_name(n: int, plat: str | None = None) -> str:
    if plat is None:
        plat = "linux" if sys.platform.startswith("linux") else "macos" if sys.platform == "darwin" else "other"
    if plat == "macos":
        return "net.agentj.host" if n == 1 else f"net.agentj.host.{n}"
    return "agentj" if n == 1 else f"agentj-{n}"


def seats_root(environ=None) -> pathlib.Path:
    e = os.environ if environ is None else environ
    return pathlib.Path(os.path.expanduser(e.get(SEATS_ENV) or DEFAULT_SEATS))


def workspace(n: int, environ=None) -> pathlib.Path:
    return seats_root(environ) / str(n)


def current(environ=None) -> int:
    """The selected instance (1 when none or when the variable is not a valid number — never a surprise instance)."""
    e = os.environ if environ is None else environ
    try:
        return _num(e.get(ENV) or 1)
    except InstanceError:
        return 1


def apply(n: int | None, environ=None) -> int:
    """Select instance n for this process (and what it starts). n None = keep what the environment says (a service unit,
    a job, the fenced Agent): AGENTJ_INSTANCE ≥ 2 re-derives the other two; an explicit `--instance` always wins.
    Instance 1 clears all three, so it is exactly the ordinary install."""
    e = os.environ if environ is None else environ
    if n is None:
        raw = e.get(ENV)
        if not raw:
            return 1
        n = _num(raw)
    if n == 1:
        for k in (ENV, "AGENTJ_STATE_DIR", "AGENTJARVIS_STATE_DIR", "AGENTJ_SERVICE_NAME"):
            e.pop(k, None)
        return 1
    e[ENV] = str(n)
    e["AGENTJ_STATE_DIR"] = str(state_root(n))
    e.pop("AGENTJARVIS_STATE_DIR", None)
    e["AGENTJ_SERVICE_NAME"] = service_name(n)
    return n


def exists(n: int, home=None) -> bool:
    r = state_root(n, home)
    return (r / "host_x25519.key").exists() and (r / "config.json").exists()


def numbers(home=None) -> list[int]:
    """Instances present on this computer (1 is listed whenever it exists)."""
    return [n for n in range(1, MAX + 1) if exists(n, home)]


def label(n: int, home=None) -> str:
    try:
        v = json.loads((state_root(n, home) / LABEL_FILE).read_text()).get("label")
        return v if isinstance(v, str) else ""
    except (OSError, ValueError, AttributeError):
        return ""


def set_label(n: int, text: str) -> str:
    from .state import _write_private
    text = " ".join((text or "").split())[:40]
    if any(ord(c) < 32 for c in text):
        raise InstanceError("备注里不能有控制字符 / no control characters")
    root = state_root(n)
    if not exists(n):
        raise InstanceError(f"实例 {n} 还不存在 / instance {n} does not exist")
    _write_private(root / LABEL_FILE, json.dumps({"label": text}, ensure_ascii=False).encode())
    return text


def _workdir_of(n: int, home=None) -> str | None:
    """Instance n's Agent folder (its preferences' agent.working_root, else its state config), read without starting
    anything; None when unknown."""
    try:
        import json5
        w = ((json5.loads((prefs_dir(n, home=home) / "config.json5").read_text()) or {}).get("agent") or {}).get("working_root")
        if isinstance(w, str) and w:
            return os.path.realpath(os.path.expanduser(w))
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    try:
        cfg = json.loads((state_root(n, home) / "config.json").read_text())
        d = (cfg.get("agent") or {}).get("dir")
        return os.path.realpath(d) if isinstance(d, str) and d else None
    except (OSError, ValueError, AttributeError):
        return None


def foreign_paths(st, home=None) -> list[str]:
    """What the fence hides from instance current()'s Agent in addition to its own state: every other instance's state and
    preferences folders and every other instance's Agent folder. Never the home directory (or a folder holding it), never a
    folder that holds this instance's own Agent folder. Real paths of existing directories only."""
    me = current()
    h = os.path.realpath(_home(home))
    root = os.path.realpath(str(st.root))
    try:
        own = (st.agent_config() or {}).get("dir") if st.exists() else None
    except (OSError, ValueError, AttributeError):   # a State stand-in without config (tests)
        own = None
    own = os.path.realpath(own) if own else None
    under = lambda p, r: p == r or p.startswith(r.rstrip(os.sep) + os.sep)   # noqa: E731
    # what the Agent must keep running: Agent J's own code and interpreter, and every PATH folder (the harness binaries)
    from .fence import code_paths
    keep = [os.path.realpath(p) for p in code_paths() + [os.path.dirname(sys.executable), sys.prefix]]
    keep += [os.path.realpath(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p.startswith("/")]
    out = []
    for n in range(1, MAX + 1):
        if n == me:
            continue
        cand = [str(state_root(n, home)), str(prefs_dir(n, home=home))]
        w = _workdir_of(n, home)
        if w and (n >= 2 or me >= 2):    # a seat never sees instance 1's folder either (unless it is HOME or holds the seat)
            cand.append(w)
        for c in cand:
            r = os.path.realpath(c)
            if not os.path.isdir(r) or r == h or under(h, r) or under(root, r) or under(r, root):
                continue
            if own and under(own, r):
                continue
            if any(under(k, r) for k in keep):
                continue
            if r not in out:
                out.append(r)
    return out


# ------------------------------------------------------------------ create · list · remove
def _run(argv: list[str], n: int, *, check=True, tty=False) -> int:
    from .service import agentj_argv
    cmd = agentj_argv() + ["--instance", str(n)] + argv
    r = subprocess.run(cmd, stdin=None if tty else subprocess.DEVNULL)
    if check and r.returncode:
        raise InstanceError(f"`agentj --instance {n} {' '.join(argv)}` 失败（{r.returncode}）/ failed")
    return r.returncode


def default_kind() -> str:
    """The harness a new seat starts with: the same as instance 1's, else Claude Code."""
    try:
        k = (json.loads((state_root(1) / "config.json").read_text()).get("agent") or {}).get("kind")
        return k if k in ("claude", "codex", "opencode", "kimi", "qwen") else "claude"
    except (OSError, ValueError, AttributeError):
        return "claude"


def create(n: int, out=sys.stdout) -> pathlib.Path:
    """New instance n ≥ 2: identity, an independent fenced Agent in <seats root>/n, the service. Idempotent per step."""
    if n == 1:
        raise InstanceError("实例 1 就是本机原来那套，不用创建 / instance 1 is the ordinary install")
    ws = workspace(n)
    ws.mkdir(parents=True, exist_ok=True)
    print(f"→ 创建实例 {n} / creating instance {n}: 工作目录 / workspace {ws}", file=out, flush=True)
    if not exists(n):
        _run(["init", "--working-root", str(ws)], n)
    _run(["agent", default_kind(), "--independent", "--dir", str(ws)], n)
    _run(["service", "install"], n)
    return ws


def remove(n: int) -> dict:
    """Stop and delete instance n ≥ 2 (service, browser service, state, preferences). The workspace stays."""
    import shutil
    if n == 1:
        raise InstanceError("实例 1 不能用这个命令删除 / instance 1 cannot be removed here")
    root = state_root(n)
    if (root / "cloud.json").exists():
        raise InstanceError(f"实例 {n} 还绑着账号席位：先运行 agentj --instance {n} unlink / "
                            f"instance {n} is still on an account seat: run agentj --instance {n} unlink first")
    _run(["browser", "disable"], n, check=False)
    _run(["service", "uninstall"], n, check=False)
    removed = []
    from . import service
    env = dict(os.environ)
    apply(n, env)
    unit = service_name(n)
    for f in (service.env_file(unit, env), os.path.join(service.unit_dir(env), unit + ".recovery.json")):
        if os.path.isfile(f):      # the service's own binary-path file and upgrade receipt (no secret; kept by uninstall)
            os.unlink(f)
            removed.append(f)
    for p in (root, prefs_dir(n)):
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
            removed.append(str(p))
    return {"instance": n, "removed": removed, "workspace": str(workspace(n))}


def describe(n: int) -> dict:
    """One row of `agentj instances`: number, label, Agent name, seat, service, online, remotes, workspace."""
    from .state import State
    from . import service
    st = State(state_root(n))
    row = {"instance": n, "label": label(n), "state": str(st.root), "service": service_name(n)}
    try:
        cfg = st.config()
    except (OSError, ValueError):
        cfg = {}
    row["agent_name"] = cfg.get("agent_name") or cfg.get("name") or ""
    row["workspace"] = (cfg.get("agent") or {}).get("dir") or ""
    row["agent"] = (cfg.get("agent") or {}).get("kind") or ""
    try:
        cloud = json.loads((st.root / "cloud.json").read_text())
        row["seat"] = (cloud.get("tenant") or {}).get("slug") or cloud.get("host_id") or "bound"
    except (OSError, ValueError, AttributeError):
        row["seat"] = ""
    try:
        devs = json.loads((st.root / "devices.json").read_text())
        row["remotes"] = len(devs) if isinstance(devs, dict) else 0
    except (OSError, ValueError):
        row["remotes"] = 0
    env = dict(os.environ)
    apply(n, env)
    old = os.environ.copy()
    try:
        os.environ.clear(); os.environ.update(env)
        s = service.status()
    finally:
        os.environ.clear(); os.environ.update(old)
    row["service_active"] = s.get("active")
    from .names import ServeBusy, ctl_call
    try:
        stat = ctl_call(st, {"cmd": "status"}, 3)
    except ServeBusy:
        stat = {"ok": True}
    except Exception:  # noqa: BLE001 — a listing never fails on one instance
        stat = None
    row["running"] = stat is not None
    row["online"] = bool(stat and stat.get("relay_up"))
    row["remotes_online"] = len([x for x in (stat or {}).get("sessions") or () if x.get("state") == "ready"]) if stat else 0
    return row
