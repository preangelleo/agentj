"""`jarvis doctor [--json]` (L3-min): one line per check — ✓ ok · ! warn · ✗ fail — with a fix hint; exit 0 unless a ✗.

Never prints a secret: credential files are only tested for existence, environment variables only by NAME, paths under
the home directory are shown with `~`. Network checks are short (≤ 6 s each) and side-effect free: the relay probe opens a
device socket on a random, unused channel (no host, no pairing, no state) and expects the relay's `{"t":"host"}` greeting;
the API probe posts an empty body and expects `400 bad_request` (no ledger is written for a malformed envelope).
"""
from __future__ import annotations

import json
import os
import pathlib
import platform as _pf
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

from . import DIST, __version__, cloud, fence, gate, names, service, wire
from .state import DEFAULT_RELAY, State

OK, WARN, FAIL = "ok", "warn", "fail"
MARK = {OK: "✓", WARN: "!", FAIL: "✗"}
NET_TIMEOUT = 6


def _c(cid: str, status: str, summary: str, hint: str = "") -> dict:
    return {"id": cid, "status": status, "summary": summary, "hint": hint}


tilde = service.tilde


def _short(s: str, n: int = 60) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _distro() -> str:
    try:
        with open("/etc/os-release") as f:
            kv = dict(line.rstrip("\n").split("=", 1) for line in f if "=" in line)
    except OSError:
        return ""
    return (kv.get("ID", "") + " " + kv.get("ID_LIKE", "")).replace('"', "").lower()


def _bwrap_hint() -> str:
    d = _distro()
    if any(x in d for x in ("debian", "ubuntu")):
        return "sudo apt install bubblewrap"
    if any(x in d for x in ("fedora", "rhel", "centos")):
        return "sudo dnf install bubblewrap"
    if "arch" in d:
        return "sudo pacman -S bubblewrap"
    if "suse" in d:
        return "sudo zypper install bubblewrap"
    return "install bubblewrap: apt / dnf / pacman install bubblewrap"


def _is_wsl() -> bool:
    try:
        with open("/proc/version") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


# ------------------------------------------------------------------ individual checks
def check_version() -> dict:
    return _c("version", OK, f"{DIST} {__version__}")


def check_python() -> dict:
    v = ".".join(map(str, sys.version_info[:3]))
    where = tilde(sys.prefix)
    if sys.version_info < (3, 11):
        return _c("python", FAIL, f"Python {v} ({where})", "需要 Python ≥ 3.11 / needs Python 3.11+: `uv tool install --python 3.13 …`")
    return _c("python", OK, f"Python {v} · {where}")


def check_platform() -> dict:
    if sys.platform.startswith("linux"):
        return _c("platform", OK, f"Linux {_pf.machine()}" + (" (WSL2)" if _is_wsl() else ""))
    if sys.platform == "darwin":
        return _c("platform", OK, f"macOS {_pf.mac_ver()[0] or ''} {_pf.machine()}".replace("  ", " "))
    if sys.platform.startswith(("win", "cygwin", "msys")):
        return _c("platform", FAIL, f"{sys.platform}", "Windows：请在 WSL2 里安装和运行 / Windows: use WSL2")
    return _c("platform", WARN, sys.platform, "未测试的系统 / untested OS (Linux, macOS, WSL2 are supported)")


def check_state(st: State) -> dict:
    if not st.exists():
        return _c("state", FAIL, f"没初始化 / not initialised ({tilde(str(st.root))})", "jarvis init")
    try:
        st.check_perms()
    except PermissionError as e:
        return _c("state", FAIL, "权限不对 / wrong permissions", f"chmod 700 {tilde(str(st.root))}; files 600 ({tilde(str(e))})")
    return _c("state", OK, f"{tilde(str(st.root))} · 通道 / channel {st.config().get('channel')}")


def _relay_url(st: State) -> str:
    try:
        return st.config().get("relay") or DEFAULT_RELAY
    except (OSError, ValueError):
        return DEFAULT_RELAY


def probe_relay(relay: str, timeout: float = NET_TIMEOUT) -> tuple[bool, str]:
    """A device socket on a random channel nobody owns: the relay answers with {"t":"host","up":false}. Nothing is paired,
    nothing is stored, no host is involved."""
    from websockets.sync.client import connect
    ch = wire.b64u(os.urandom(16))
    url = f"{relay.rstrip('/')}/v1/dev/{ch}"
    try:
        with connect(url, open_timeout=timeout, close_timeout=2, max_size=2**12) as ws:
            first = ws.recv(timeout=timeout)
    except Exception as e:  # noqa: BLE001 — any failure is "not reachable"; the kind is enough
        return False, type(e).__name__
    try:
        m = json.loads(first)
    except (TypeError, ValueError):
        return False, "unexpected_answer"
    return (True, "ok") if isinstance(m, dict) and m.get("t") == "host" else (False, "unexpected_answer")


def check_relay(st: State) -> dict:
    relay = _relay_url(st)
    ok, why = probe_relay(relay)
    if ok:
        return _c("relay", OK, f"{relay} (TLS + WebSocket)")
    return _c("relay", FAIL, f"{relay} 连不上 / unreachable ({why})",
              "检查网络 / 代理（HTTPS_PROXY）；公司防火墙要放行 wss:// / check network, proxy, firewall (wss://)")


def _http(url: str, data: bytes | None, timeout: float) -> int:
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET",
                                 headers={"content-type": "application/json", "user-agent": cloud.AGENT})
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def check_dashboard(st: State) -> dict:
    try:
        api, app = cloud.api_url(st), cloud.app_url(st)
    except cloud.CloudError:
        return _c("dashboard", FAIL, "API / Dashboard 地址不是 https:// / not https://", "检查 config.json / AGENTJARVIS_API_URL")
    try:
        code = _http(api.rstrip("/") + "/v1/host/poll", b"{}", NET_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        return _c("dashboard", FAIL, f"{api} 连不上 / unreachable ({type(e).__name__})", "检查网络 / 代理 / check network, proxy")
    if code != 400:
        return _c("dashboard", FAIL, f"{api} 答复异常 / unexpected answer (HTTP {code})",
                  "控制面暂时不可用，稍后再试 / control plane unavailable, retry later")
    try:
        app_code = _http(app, None, NET_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        return _c("dashboard", WARN, f"API ok · Dashboard {app} 连不上 / unreachable ({type(e).__name__})", "")
    if app_code >= 500:
        return _c("dashboard", WARN, f"API ok · Dashboard {app} HTTP {app_code}", "")
    return _c("dashboard", OK, f"API {api} · Dashboard {app}")


AGENT_LABEL = {"claude": "Claude Code", "codex": "Codex"}
BIN_ENV = {"claude": "AGENTJARVIS_CLAUDE_BIN", "codex": "AGENTJARVIS_CODEX_BIN"}


def _agent_bin(kind: str) -> str | None:
    return os.environ.get(BIN_ENV[kind]) or shutil.which(kind)


def check_agent(st: State) -> dict:
    c = st.agent_config() if st.exists() else None
    if not c:
        return _c("agent", WARN, "还没接 Agent / no Agent configured", "jarvis agent claude --dir <folder>   (or: codex)")
    if not os.path.isdir(c["dir"]):
        return _c("agent", FAIL, f"{AGENT_LABEL[c['kind']]} · 目录不存在 / folder missing {tilde(c['dir'])}",
                  f"jarvis agent {c['kind']} --dir <folder>")
    fz = "fenced" if c.get("fence", True) else "UNFENCED (--unfenced)"
    return _c("agent", OK, f"{AGENT_LABEL[c['kind']]} · {tilde(c['dir'])} · {fz}")


def _version_of(exe: str) -> str | None:
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return _short((r.stdout or r.stderr).splitlines()[0] if (r.stdout or r.stderr) else "?", 40) if r.returncode == 0 else None


def _claude_login() -> tuple[str, str]:
    """(status, where) — existence only, never content."""
    cfg = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    if os.path.isfile(os.path.join(cfg, ".credentials.json")):
        return OK, f"login {tilde(os.path.join(cfg, '.credentials.json'))}"
    if sys.platform == "darwin" and shutil.which("security"):
        try:   # no -w: asks only whether the Keychain item exists, never its value
            r = subprocess.run(["security", "find-generic-password", "-s", "Claude Code-credentials"],
                               capture_output=True, timeout=10)
            if r.returncode == 0:
                return OK, "login in Keychain"
        except (OSError, subprocess.TimeoutExpired):
            pass
    for k in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"):
        if os.environ.get(k):
            return "env", f"env {k} (name only)"
    return WARN, "no login found"


def _codex_login() -> tuple[str, str]:
    home = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
    if os.path.isfile(os.path.join(home, "auth.json")):
        return OK, f"login {tilde(os.path.join(home, 'auth.json'))}"
    for k in ("CODEX_API_KEY", "OPENAI_API_KEY"):
        if os.environ.get(k):
            return "env", f"env {k} (name only)"
    return WARN, "no login found"


def check_agent_cli(st: State, svc: dict) -> dict:
    c = st.agent_config() if st.exists() else None
    kinds = [c["kind"]] if c else ["claude", "codex"]
    found = []
    for k in kinds:
        exe = _agent_bin(k)
        if not exe:
            continue
        ver = _version_of(exe)
        if ver is None:
            return _c("agent_cli", FAIL, f"{AGENT_LABEL[k]} {tilde(exe)} 运行失败 / does not run (`{k} --version`)",
                      f"重装 {AGENT_LABEL[k]} / reinstall {AGENT_LABEL[k]}")
        login, where = (_claude_login() if k == "claude" else _codex_login())
        found.append((k, exe, ver, login, where))
    if not found:
        if c:
            return _c("agent_cli", FAIL, f"{AGENT_LABEL[c['kind']]} 不在 PATH / `{c['kind']}` not on PATH",
                      "安装后重开终端；装了服务的话重新 `jarvis service install` 记下新 PATH / install it, then re-run "
                      "`jarvis service install` so the service gets the new PATH")
        return _c("agent_cli", WARN, "没找到 claude / codex / neither `claude` nor `codex` on PATH",
                  "安装 Claude Code（https://claude.com/claude-code）或 Codex / install Claude Code or Codex")
    k, exe, ver, login, where = found[0]
    s = f"{AGENT_LABEL[k]} {ver} · {tilde(os.path.abspath(exe))} · {where}"
    if login == WARN:
        return _c("agent_cli", WARN if not c else FAIL, s, f"运行 `{k}` 登录一次 / run `{k}` once and log in")
    if login == "env":
        if svc.get("installed"):
            return _c("agent_cli", WARN, s + " — 服务看不到 shell 里的变量 / the service cannot see it",
                      service.token_hint(svc.get("name") or service.DEFAULT_UNIT))
        return _c("agent_cli", OK, s)
    return _c("agent_cli", OK, s)


def check_fence(st: State) -> dict:
    c = st.agent_config() if st.exists() else None
    if c and not c.get("fence", True):
        return _c("fence", WARN, "你选了不隔离运行 / you chose --unfenced", "jarvis agent " + c["kind"] + " --dir <folder>  (fenced again)")
    if sys.platform == "darwin":
        return _c("fence", WARN, "macOS：还没有 Agent 隔离 / not fenced yet on macOS",
                  "start with `jarvis agent claude --dir <folder> --unfenced` (asks the passphrase); Mac fence = later L3")
    if not sys.platform.startswith("linux"):
        return _c("fence", WARN if not c else FAIL, "不支持 / unsupported", "Linux / WSL2")
    bad = FAIL if c else WARN
    if not shutil.which("bwrap"):
        return _c("fence", bad, "没有 bubblewrap（bwrap） / bubblewrap missing", _bwrap_hint())
    workdir = c["dir"] if c and os.path.isdir(c["dir"]) else None
    if st.exists() and workdir:
        why = fence.problem(st, workdir)
    else:
        with tempfile.TemporaryDirectory() as d:     # not initialised / no folder yet: probe with throwaway state
            tst = State(pathlib.Path(d) / "s")
            tst.root.mkdir(mode=0o700)
            why = fence.problem(tst, d)
            fence._probe_cache.pop(f"{tst.root}|{d}", None)
    if why is None:
        return _c("fence", OK, "bubblewrap 可用 / bubblewrap works (unprivileged user namespaces)")
    return _c("fence", bad, f"bubblewrap 起不来 / bubblewrap cannot start ({why})",
              "系统禁了非特权用户命名空间：Ubuntu 24.04+ `sudo sysctl kernel.apparmor_restrict_unprivileged_userns=0`（或给 bwrap 一份 "
              "AppArmor profile）；Debian 旧版 `sudo sysctl kernel.unprivileged_userns_clone=1` / unprivileged user namespaces are off")


def check_passphrase(st: State) -> dict:
    if not st.exists():
        return _c("passphrase", WARN, "未设置 / not set", "jarvis init, then jarvis passphrase set")
    if gate.is_set(st):
        left = gate.lock_left(st)
        return _c("passphrase", OK, "已设置 / set" + (f" (locked {max(1, left // 60)} min)" if left else ""))
    return _c("passphrase", WARN, "未设置 / not set", "jarvis passphrase set   (自己在终端里输 / type it yourself, never via an agent)")


def check_bound(st: State) -> dict:
    link = cloud.read_cloud(st) if st.exists() else None
    if link:
        return _c("bound", OK, f"Dashboard 公司账号 / company {link['tenant']['slug']}")
    return _c("bound", WARN, "未绑定 Dashboard / not bound", "jarvis login")


def check_serve(st: State) -> dict:
    if not st.exists():
        return _c("serve", WARN, "没在运行 / not running", "jarvis init first")
    try:
        res = names.ctl_call(st, {"cmd": "status"}, 5)
    except names.ServeBusy:
        return _c("serve", FAIL, "在运行但不响应 / running but not answering", "systemctl --user restart agentjarvis  (or restart `jarvis serve`)")
    if res is None:
        return _c("serve", WARN, "没在运行 / not running", "jarvis service install   (or `jarvis serve` in a terminal)")
    up = res.get("relay_up")
    return _c("serve", OK if up else WARN, "在运行 / running · relay " + ("connected" if up else "reconnecting"),
              "" if up else "检查网络 / check the network")


def check_service(svc: dict) -> dict:
    if svc.get("kind") == "none":
        return _c("service", WARN, f"没有可用的服务管理器 / no service manager ({svc.get('active')})",
                  "run `jarvis serve` under tmux / your own supervisor")
    n = svc.get("name")
    if not svc.get("installed"):
        return _c("service", WARN, "没安装 / not installed", "jarvis service install")
    if svc.get("active") == "active":
        return _c("service", OK, f"{n} 已安装、运行中 / installed, active")
    hint = (f"journalctl --user -u {n} -n 50" if svc.get("kind") == "systemd" else "cat <state dir>/service.log")
    return _c("service", FAIL, f"{n} 已安装但没在运行 / installed but {svc.get('active')}", hint)


def run(st: State | None = None, offline: bool = False) -> list[dict]:
    st = st or State()
    svc = service.status()
    out = [check_version(), check_python(), check_platform(), check_state(st)]
    if offline:
        out += [_c("relay", WARN, "跳过 / skipped (--offline)"), _c("dashboard", WARN, "跳过 / skipped (--offline)")]
    else:
        out += [check_relay(st), check_dashboard(st)]
    out += [check_agent(st), check_agent_cli(st, svc), check_fence(st), check_passphrase(st), check_bound(st),
            check_serve(st), check_service(svc)]
    return out


def main(as_json: bool = False, offline: bool = False) -> int:
    checks = run(offline=offline)
    failed = any(c["status"] == FAIL for c in checks)
    if as_json:
        print(json.dumps({"tool": DIST, "version": __version__, "ok": not failed, "checks": checks}, ensure_ascii=False, indent=1))
    else:
        for c in checks:
            print(f"{MARK[c['status']]} {c['id']:<10} {c['summary']}" + (f"\n    → {c['hint']}" if c["hint"] and c["status"] != OK else ""))
        print(("✗ 有问题要修 / something to fix" if failed else "✓ 没有阻塞问题 / nothing blocking") + f"  ({DIST} {__version__})")
    return 1 if failed else 0
