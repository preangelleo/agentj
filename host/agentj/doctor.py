"""`agentj doctor [--json]` (L3-min): one line per check — ✓ ok · ! warn · ✗ fail — with a fix hint; exit 0 unless a ✗.

Never prints a secret: credential files are only tested for existence, environment variables only by NAME, paths under
the home directory are shown with `~`. Network checks are short (≤ 6 s each) and side-effect free: the relay probe opens a
device socket on a random, unused channel (no host, no pairing, no state) and expects the relay's `{"t":"host"}` greeting;
the API probe posts an empty body and expects `400 bad_request` (no ledger is written for a malformed envelope).
"""
from __future__ import annotations

import json
import os
import re
import pathlib
import platform as _pf
import shutil
import sys
import tempfile
import urllib.error
import urllib.request

from . import DIST, __version__, cloud, fence, gate, harness, names, service, update, wire
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


def _in_container() -> bool:
    if os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv"):
        return True
    try:
        with open("/proc/1/cgroup") as f:
            return any(x in f.read() for x in ("docker", "containerd", "kubepods", "lxc"))
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
        return _c("state", FAIL, f"没初始化 / not initialised ({tilde(str(st.root))})", "agentj init")
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
              "检查网络和代理（HTTPS_PROXY）；有防火墙的话要放行 wss:// / check network, proxy, firewall (wss://)")


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
        return _c("dashboard", FAIL, "服务器 / 账号后台地址不是 https:// / not https://", "检查 config.json / AGENTJ_API_URL")
    try:
        code = _http(api.rstrip("/") + "/v1/host/poll", b"{}", NET_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        return _c("dashboard", FAIL, f"{api} 连不上 / unreachable ({type(e).__name__})", "检查网络 / 代理 / check network, proxy")
    if code != 400:
        return _c("dashboard", FAIL, f"{api} 答复异常 / unexpected answer (HTTP {code})",
                  "Agent J 服务器暂时不可用，稍后再试 / the Agent J server is unavailable, retry later")
    try:
        app_code = _http(app, None, NET_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        return _c("dashboard", WARN, f"服务器 ok · 账号后台 {app} 连不上 / account dashboard unreachable ({type(e).__name__})", "")
    if app_code >= 500:
        return _c("dashboard", WARN, f"服务器 ok · 账号后台 / account dashboard {app} HTTP {app_code}", "")
    return _c("dashboard", OK, f"服务器 / server {api} · 账号后台 / account dashboard {app}")


AGENT_LABEL = {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode"}
BIN_ENV = harness.BIN_ENV
# one implementation for doctor and `agentj agent detect` (existence checks only, never content)
_agent_bin, _version_of = harness.agent_bin, harness.version_of
_claude_login, _codex_login, _opencode_login = harness.claude_login, harness.codex_login, harness.opencode_login


def _login(k: str) -> tuple[str, str]:
    return {"claude": _claude_login, "codex": _codex_login, "opencode": _opencode_login}[k]()


def check_agent(st: State) -> dict:
    c = st.agent_config() if st.exists() else None
    if not c:
        return _c("agent", WARN, "还没接 Agent / no Agent configured", "agentj agent claude --dir <folder>   (or: codex / opencode)")
    if not os.path.isdir(c["dir"]):
        return _c("agent", FAIL, f"{AGENT_LABEL[c['kind']]} · 目录不存在 / folder missing {tilde(c['dir'])}",
                  f"agentj agent {c['kind']} --dir <folder>")
    fz = "shared (native owner session; outside independent fence)" if c.get("session_mode") == "shared" else "fenced" if c.get("fence", True) else "UNFENCED (--unfenced)"
    if c.get("fence", True) and c.get("docker"):
        return _c("agent", WARN, f"{AGENT_LABEL[c['kind']]} · {tilde(c['dir'])} · fenced, docker allowed (--allow-docker): "
                  "容器引擎能挂载整台电脑的文件 / the container engine can mount every file", f"agentj agent {c['kind']} --dir <folder>  (docker hidden again)")
    return _c("agent", OK, f"{AGENT_LABEL[c['kind']]} · {tilde(c['dir'])} · {fz}")


def check_codex_shared(st: State) -> dict | None:
    """P51: what the first phone message will do in Codex shared mode (never a dead end)."""
    c = st.agent_config() if st.exists() else None
    if not c or c.get("kind") != "codex" or c.get("session_mode") != "shared" or not os.path.isdir(c["dir"]):
        return None
    from . import shared_codex
    sid = c.get("shared_session_id") or st.agent_session("codex") or ""
    if sid:
        try:
            path = shared_codex.selected_rollout(c["dir"], sid)
            meta = shared_codex.first_meta(path) or {}
            if os.path.realpath(meta.get("cwd") or "") != os.path.realpath(c["dir"]):
                raise shared_codex.Refusal("wrong thread/project")
            _, ctx, active = shared_codex.scan(path)
        except (OSError, ValueError) as e:
            zh, en = shared_codex.REASONS.get(shared_codex.brief(e), ("所选会话不可用。", "The selected thread is unavailable."))
            return _c("codex_shared", WARN, f"Codex 共享：{zh}手机第一条消息会新开一个 Codex 会话 / {en} The first phone message starts a new Codex session",
                      "电脑上 `codex resume` 选好会话后在设置里填它的 ID；或者不管它 / pick a thread on the computer, or leave it")
        busy = "；电脑上正在回答，手机消息会排队 / desktop turn running, phone messages queue" if active else ""
        perms = "" if ctx else "；还没有完整一轮，按你的 Codex 默认权限 / no completed turn yet, your Codex defaults apply"
        return _c("codex_shared", OK, f"Codex 共享：接续会话 {sid[:8]}{busy}{perms} / continuing thread {sid[:8]}")
    found = shared_codex.discover(c["dir"])
    if found:
        return _c("codex_shared", OK, f"Codex 共享：没指定会话，接续这个目录最近的 {found[:8]} / no thread selected; continuing the newest one here ({found[:8]})")
    return _c("codex_shared", WARN, "Codex 共享：这个目录还没有 Codex 会话，手机第一条消息会新开一个 / "
              "no Codex thread in this folder yet; the first phone message starts one",
              f"想接着电脑上的会话：在 {tilde(c['dir'])} 里先跑一次 `codex` / to share a desktop session, run `codex` in that folder first")


def check_agent_cli(st: State, svc: dict) -> dict:
    c = st.agent_config() if st.exists() else None
    kinds = [c["kind"]] if c else ["claude", "codex", "opencode"]
    found = []
    try:
        binary_env = service.effective_binary_environment(svc)
    except (OSError, service.ServiceError):
        return _c("agent_cli", FAIL, "Invalid service binary environment", "Check AGENTJ_*_BIN path assignments only; never dump the env file")
    for k in kinds:
        from .binaries import resolve
        resolution = resolve(k, binary_env)
        if resolution["wrapper"] and not resolution["path"]:
            return _c("agent_cli", FAIL if c else WARN,
                      f"{AGENT_LABEL[k]}: mise/asdf wrapper has no unambiguous real executable; wrapper was not run",
                      f"Set {harness.BIN_ENV[k]} to the real executable, then run `agentj service install`")
        exe = resolution["path"] if svc.get("installed") else _agent_bin(k)
        if not exe:
            continue
        ver = _version_of(exe)
        if ver is None:
            return _c("agent_cli", FAIL, f"{AGENT_LABEL[k]} {tilde(exe)} 运行失败 / does not run (`{k} --version`)",
                      f"重装 {AGENT_LABEL[k]} / reinstall {AGENT_LABEL[k]}")
        login, where = harness.opencode_login(ver) if k == "opencode" else _login(k)
        found.append((k, exe, ver, login, where))
    if not found:
        if c:
            return _c("agent_cli", FAIL, f"{AGENT_LABEL[c['kind']]} 不在 PATH / `{c['kind']}` not on PATH",
                      "安装后重开终端；装了服务的话重新 `agentj service install` 记下新 PATH / install it, then re-run "
                      "`agentj service install` so the service gets the new PATH")
        return _c("agent_cli", WARN, "没找到 claude / codex / opencode / none of `claude`, `codex`, `opencode` on PATH",
                  "安装 Claude Code（https://claude.com/claude-code）或 Codex；都用不了就装 OpenCode（install.md 第 3 步）/ "
                  "install Claude Code or Codex, or OpenCode (install.md Step 3)")
    k, exe, ver, login, where = found[0]
    s = f"{AGENT_LABEL[k]} {ver} · {tilde(os.path.abspath(exe))} · {where}"
    s += " · selection: " + resolve(k, binary_env)["reason"]
    if login == WARN and k == "claude" and os.environ.get("CLAUDECODE") == "1":
        # run by Claude Code itself: it is logged in, but it hides CLAUDE_CODE_OAUTH_TOKEN from the commands it runs
        return _c("agent_cli", WARN, s.replace("no login found", "no saved login file (this doctor runs inside Claude Code, "
                  "which hides its own token from commands)"),
                  "人类在自己的终端跑一次 `claude` 登录即可（服务要用）/ the human runs `claude` once in their own terminal "
                  "and logs in, so the always-on service can use it")
    if k == 'opencode' and harness.opencode_v2(ver):
        s += ' · v2 (/api)'                         # P60: supported (agent_opencode2); the live check below asks the server
    if k == "opencode" and c:
        from .names import ctl_call, ServeBusy
        try: auth = ctl_call(st, {'cmd':'opencode_auth'}, timeout=5) or {}
        except (OSError, ValueError, ServeBusy): auth = {}
        # P59: the last turn's failure class (401 / 403-region / quota / rate / model / network …), never its message
        fail = auth.get('last_failure') if isinstance(auth.get('last_failure'), dict) else {}
        from .agent_opencode import PROVIDER_NOTES
        reason = fail.get('reason') if fail.get('reason') in PROVIDER_NOTES else None
        if reason:
            code = fail.get('http_status') if type(fail.get('http_status')) is int else None
            s += f" · last turn failed: {reason}" + (f" (HTTP {code})" if code else "")
        if auth.get('known'):
            connected = auth.get('selected_connected')
            return _c('agent_cli', FAIL if connected is False or reason in ('login', 'no_key', 'region', 'balance', 'model') else WARN,
                      s + (' · selected provider disconnected' if connected is False else ' · provider connection recorded; key validity unverified'),
                      (PROVIDER_NOTES[reason].replace('{provider}', '<provider>') + ' ' if reason else '') +
                      '优先使用 Claude Code。OpenCode /provider.connected 只证明已配置连接；核实选中 provider/model。换 key 后 Agent J 在下一条消息前自动重启它启动的 OpenCode，接着原对话（也可运行 agentj agent restart）；附着的电脑 OpenCode 要你自己重启。 / Prefer Claude Code. Connected is not a live key test. After a key change Agent J restarts its own OpenCode before the next message (or `agentj agent restart`); restart an attached desktop server yourself.')
        return _c('agent_cli', WARN, s + ' · runtime provider authentication unknown',
                  '优先使用 Claude Code；OpenCode 需运行中的 GET /provider.connected 才能确认所选连接。凭据文件或数据库存在不等于已登录；换 key 后 Agent J 会在下一条消息前自动重启它启动的 OpenCode。 / Prefer Claude Code; store existence is not authentication. Check the running provider connection; Agent J restarts its own OpenCode after a key change.')
    if login == WARN and k == "opencode":
        return _c("agent_cli", WARN if not c else FAIL, s, "人类在自己的终端运行 `opencode auth login` 存好模型服务的 key "
                  "（install.md 第 3 步）/ the human runs `opencode auth login` in their own terminal")
    if login == WARN:
        return _c("agent_cli", WARN if not c else FAIL, s, f"运行 `{k}` 登录一次 / run `{k}` once and log in")
    if login == "env":
        # the login is only an environment variable of this shell: the background service (systemd / launchd) does not
        # get it, so the Agent may not be able to log in there. Name only, never the value. A Linux service env file the
        # human made (0600) counts as handled.
        unit = svc.get("name") or service.DEFAULT_UNIT
        if service.platform() == "linux" and os.path.exists(service.env_file(unit)):
            return _c("agent_cli", OK, s + " · 服务用环境文件 / the service uses its environment file")
        hint = (service.token_hint(unit) if k == "claude" else
                f"后台服务看不到这个终端里的环境变量：人类在自己的终端里运行 `{'codex login' if k == 'codex' else 'opencode auth login'}`"
                f" 存好登录，再运行 `agentj service install` / the background service cannot see this shell's variables: the "
                f"human runs `{'codex login' if k == 'codex' else 'opencode auth login'}` in their own terminal, then "
                "`agentj service install`")
        return _c("agent_cli", WARN, s + " — 只在这个终端的环境变量里，后台服务可能登录不了 / only an environment variable "
                  "in this shell: the background service may not be able to log in", hint)
    return _c("agent_cli", OK, s)


def codex_allow_rules() -> int:
    """How many `decision="allow"` prefix rules the human's Codex has (`$CODEX_HOME/rules/*.rules`): counted, never printed."""
    d = os.path.join(os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"), "rules")
    n = 0
    try:
        names = sorted(os.listdir(d))[:50]
    except OSError:
        return 0
    for f in names:
        if f.endswith(".rules"):
            try:
                with open(os.path.join(d, f), encoding="utf-8", errors="replace") as fh:
                    n += sum(1 for ln in fh.read(1 << 20).splitlines()
                             if "prefix_rule" in ln and re.search(r'decision\s*=\s*"allow"', ln))
            except OSError:
                continue
    return n


def check_danger(st: State) -> dict:
    """The danger list (ADR-A47 / A49): built in; config.json can only add. Anything that tries to remove or switch it off is
    ignored — and said here."""
    from . import danger
    try:
        cfg = st.config() if st.exists() else {}
    except (OSError, ValueError):
        cfg = {}
    issues = danger.config_issues(cfg)
    extra = len(danger.parse_extra(cfg.get("danger_extra"))[0])
    c = st.agent_config() if st.exists() else None
    tail = ""
    if c and c["kind"] == "codex":
        n = codex_allow_rules()
        if n:   # Codex runs these outside its sandbox without asking anyone (its docs); a hook of ours cannot turn them into a card
            tail = (f"；但你的 Codex 有 {n} 条「总是允许」规则（$CODEX_HOME/rules），匹配的命令不经手机直接运行 / "
                    f"{n} Codex allow rule(s) run without the phone")
    if issues:
        return _c("danger", WARN, f"危险清单照常生效，配置里有 {len(issues)} 处被忽略 / ignored config: " + issues[0][:160],
                  "危险清单只能加严：用 danger_extra 添加规则，不能删减或关闭 / the list can only grow (danger_extra)")
    return _c("danger", WARN if tail else OK, f"花钱 / 删除 / 对外发送 / 改凭据 / 改价 永远手机逐条批准"
              + (f" + 本机附加 {extra} 条" if extra else "") + tail)


def check_fence(st: State) -> dict:
    c = st.agent_config() if st.exists() else None
    if c and not c.get("fence", True):
        return _c("fence", WARN, "你选了不隔离运行 / you chose --unfenced", "agentj agent " + c["kind"] + " --dir <folder>  (fenced again)")
    mac = sys.platform == "darwin"
    if not mac and not sys.platform.startswith("linux"):
        return _c("fence", WARN if not c else FAIL, "不支持 / unsupported", "Linux / macOS / WSL2")
    bad = WARN   # F14: without a fence the Agent still runs, with the harness's own permissions
    if mac and not os.access(fence.SANDBOX_EXEC, os.X_OK):
        return _c("fence", bad, "没有 /usr/bin/sandbox-exec / sandbox-exec missing",
                  "这台 Mac 缺系统自带的 sandbox-exec：请反馈 / report it (feedback); the Agent runs unfenced meanwhile")
    if not mac and not shutil.which("bwrap"):
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
        if mac:
            return _c("fence", OK, "macOS 沙箱可用 / sandbox-exec works (SBPL profile)")
        return _c("fence", OK, "bubblewrap 可用 / bubblewrap works (unprivileged user namespaces)")
    if mac:
        return _c("fence", bad, f"macOS 沙箱起不来 / sandbox-exec cannot start ({why})",
                  "Agent J 本身是不是在别的沙箱里运行（例如某个 Agent 的沙箱）？在你自己的终端里运行 / run agentj from your own "
                  "terminal, not from inside another sandbox")
    if _in_container():
        return _c("fence", bad, f"bubblewrap 起不来 / bubblewrap cannot start ({why}) — 在容器里 / inside a container",
                  "容器默认禁止用户命名空间：把主机装在虚拟机 / 云服务器本身上，而不是 Docker 里（或由你决定放宽容器的 seccomp）/ "
                  "containers block user namespaces: install the host on the VM / server itself, not inside Docker")
    return _c("fence", bad, f"bubblewrap 起不来 / bubblewrap cannot start ({why})",
              "系统禁了非特权用户命名空间：Ubuntu 24.04+ `sudo sysctl kernel.apparmor_restrict_unprivileged_userns=0`（或给 bwrap 一份 "
              "AppArmor profile）；Debian 旧版 `sudo sysctl kernel.unprivileged_userns_clone=1` / unprivileged user namespaces are off")


def check_passphrase(st: State) -> dict:
    if not st.exists():
        return _c("passphrase", WARN, "未设置 / not set", "agentj init, then agentj passphrase set")
    if gate.is_set(st):
        left = gate.lock_left(st)
        return _c("passphrase", OK, "已设置 / set" + (f" (locked {max(1, left // 60)} min)" if left else ""))
    return _c("passphrase", WARN, "未设置 / not set", "agentj passphrase set   (自己在终端里输 / type it yourself, never via an agent)")


def check_harness(svc=None) -> dict:
    """`agentj agent detect` in one line: which harnesses are usable, and the install decision (seat setup §4.1)."""
    try:
        env = service.effective_binary_environment(svc or {})
    except (OSError, service.ServiceError):
        return _c("harness", FAIL, "Invalid saved binary paths", "Check only AGENTJ_*_BIN assignments")
    d = harness.detect(env)
    hint = {"none": "登录 Claude Code 或 Codex；都没有就装 OpenCode 并配好模型（install.md 第 3 步）/ log in to Claude Code or "
                    "Codex, or install OpenCode with a model (install.md Step 3)",
            "ask_owner": "多个可用：由人类决定接哪一个 / more than one usable: the human decides which one"}.get(d["decision"], "")
    installs = []
    for h in d["harnesses"]:
        for row in h.get("installations", []):
            installs.append(f"{h['name']}: {tilde(row['path'])} {row.get('version') or '—'}" + (" [Agent J uses this]" if row['selected'] else "") + (" [mise/asdf wrapper: never executed]" if row['wrapper'] else ""))
        if h.get("resolution", {}).get("wrapper"):
            hint += f"; {h['name']}: set {harness.BIN_ENV[h['name']]} to a real executable, then agentj service install"
    return _c("harness", WARN if d["decision"] == "none" else OK, harness.summary(d) + ("\n" + "\n".join(installs) if installs else ""), hint)


def check_bound(st: State) -> dict:
    link = cloud.read_cloud(st) if st.exists() else None
    if link:
        via = "用设置码加入 / joined with a setup code" if link["via"] == "seat" else "用 8 位代码加入 / joined with the 8-character code"
        return _c("bound", OK, f"Agent J 账号 / Agent J account {link['tenant']['slug']} · {via}")
    return _c("bound", WARN, "还没加到 Agent J 账号 / not in an Agent J account yet", "agentj login")


def check_serve(st: State) -> dict:
    if not st.exists():
        return _c("serve", WARN, "没在运行 / not running", "agentj init first")
    try:
        res = names.ctl_call(st, {"cmd": "status"}, 5)
    except names.ServeBusy:
        return _c("serve", FAIL, "在运行但不响应 / running but not answering", "systemctl --user restart agentj  (or restart `agentj serve`)")
    if res is None:
        return _c("serve", WARN, "没在运行 / not running", "agentj service install   (or `agentj serve` in a terminal)")
    up = res.get("relay_up")
    return _c("serve", OK if up else WARN, "在运行 / running · relay " + ("connected" if up else "reconnecting"),
              "" if up else "检查网络 / check the network")


def check_service(svc: dict, legacy: dict | None = None) -> dict:
    legacy = legacy or {}
    if legacy.get("installed") and not svc.get("installed"):
        return _c("service", WARN, f"还是旧名字的服务 {legacy.get('name')}（{legacy.get('active')}）/ the service still has its old "
                  f"name {legacy.get('name')}", "运行 agentj service install 换成新名字 / run `agentj service install` to switch to "
                  "the new name")
    if svc.get("kind") == "none":
        return _c("service", WARN, f"没有可用的服务管理器 / no service manager ({svc.get('active')})",
                  "run `agentj serve` under tmux / your own supervisor")
    n = svc.get("name")
    if not svc.get("installed"):
        return _c("service", WARN, "没安装 / not installed", "agentj service install")
    try:
        mismatch = service.binary_env_mismatches(n)
    except (OSError, service.ServiceError):
        return _c("service", FAIL, "Cannot inspect harness path settings", "Check service env path assignments; never dump credentials")
    if mismatch:
        return _c("service", FAIL, "unit/env file harness paths disagree: " + ", ".join(mismatch),
                  "Run `agentj service install` to remove stale unit paths; saved env overrides are preserved")
    if svc.get("active") == "active":
        return _c("service", OK, f"{n} 已安装、运行中 / installed, active")
    hint = (f"journalctl --user -u {n} -n 50" if svc.get("kind") == "systemd" else "cat <state dir>/service.log")
    return _c("service", FAIL, f"{n} 已安装但没在运行 / installed but {svc.get('active')}", hint)


def check_alias() -> dict:
    """The extra command names (alias.py), one row, never a failure: `aj`, and on a migrated computer the old `jarvis`."""
    from . import alias
    try:
        rows = alias.status_all()
    except OSError:
        return _c("alias", OK, "—")
    parts, warn = [], False
    for r in rows:
        n = r["name"]
        if r["state"] == "installed":
            parts.append(f"{n} → agentj ({tilde(r['link'])})" + (" · 下个版本移除 / goes away next version" if n == alias.COMPAT else ""))
        elif r["state"] == "taken":
            parts.append(f"`{n}` 已被 {tilde(r['link'])} 占用，用 agentj / `{n}` is taken: use agentj")
        elif r["state"] == "absent":
            warn = True
            parts.append(f"没有 {n} / no `{n}`")
        else:
            parts.append(f"不装 {n} / no `{n}` (" + {"checkout": "source checkout",
                                                       "no_agentj": "agentj not on PATH"}.get(r["state"], r["state"]) + ")")
    return _c("alias", WARN if warn else OK, " · ".join(parts), "agentj alias install" if warn else "")


def check_migration(st: State) -> dict | None:
    """Only while the 0.9 state directory could not be moved yet (a serve is running on it)."""
    from . import migrate
    if migrate.pending() == "serve_running":
        return _c("migrate", WARN, f"状态目录还在旧路径 / still at the old path ({tilde(str(st.root))})",
                  "停掉旧的 serve：agentj service install（会换掉旧服务），之后自动搬 / stop the old serve (agentj service install)")
    return None


def check_linger(svc: dict, environ=None) -> dict | None:
    """Linux + systemd: is the user manager kept after logout (`loginctl enable-linger`)? Needed on a server, where nobody
    stays logged in; on a desktop session it is fine either way. None = not applicable."""
    if svc.get("kind") != "systemd":
        return None
    e = os.environ if environ is None else environ
    lg = service._linger()
    if lg == "yes":
        return _c("linger", OK, "已开 / on (serve keeps running after logout)")
    if lg is None:
        return None
    if not service.linger_needed(e):
        return _c("linger", OK, "关（桌面会话：登录期间服务照常） / off — fine on a desktop while you are logged in",
                  "")
    return _c("linger", WARN, "关：退出 SSH 登录后服务会停 / off: the service stops when you log out",
              "loginctl enable-linger $USER   (服务器上必须 / needed on a server)")


def check_estop(st: State) -> dict:
    """The stop switch (ADR-A51): on = the Agent and the scheduled tasks do nothing until a human resumes."""
    from . import controls
    if not st.exists():
        return _c("estop", OK, "未急停 / not stopped")
    e = controls.estop_state(st)
    if e["on"]:
        by = e.get("by") or "?"
        if by.startswith("phone:"):
            by = "手机 / phone " + (st.devices().get(by[6:], {}).get("name") or by[6:])
        return _c("estop", WARN, f"⛔ 已急停 / STOPPED ({by})",
                  "确认没事了再恢复 / resume when it is safe: agentj resume   (或手机上「恢复」/ or 恢复 on the phone)")
    return _c("estop", OK, "未急停 / not stopped")


def check_tasks(st: State) -> dict:
    from . import tasks
    c = st.agent_config() if st.exists() else None
    rows = tasks.rows(st, c["dir"]) if c else []
    if not rows:
        return _c("tasks", OK, "没有定时任务 / no scheduled tasks")
    bad = [r for r in rows if r["problems"]]
    on = sum(1 for r in rows if r["enabled"])
    stale = [r for r in rows if r["stale"]]
    s = f"{len(rows)} 个任务，{on} 个已启用 / {len(rows)} tasks, {on} enabled"
    if bad or stale:
        what = (f"{bad[0]['id']}: {bad[0]['problems'][0]}" if bad else f"{stale[0]['id']} 改动后需重新启用 / changed, enable again")
        return _c("tasks", WARN, s + f" · {len(bad)} 无效 / invalid, {len(stale)} 待重新启用 / changed — " + what[:120],
                  "agentj tasks list")
    return _c("tasks", OK, s)


def check_activity(st: State) -> dict:
    from . import activity
    if not st.exists():
        return _c("activity", OK, "—")
    on = activity.enabled(st)
    return _c("activity", OK, ("开 / on" if on else "关 / off") + f" · 本机 {activity.size(st) // 1024} KiB，保留 30 天 / kept 30 days"
              + " (agentj config activity on|off)")


def check_update(offline: bool = False) -> dict:
    if offline:
        return _c("update", WARN, "跳过 / skipped (--offline)")
    r = update.check(timeout=NET_TIMEOUT)
    if r["status"] == "newer":
        return _c("update", WARN, f"有新版本 / newer version {r['latest']} (installed {r['current']})",
                  "Agent 自己升级 / the Agent runs: agentj update apply")
    if r["status"] == "current":
        return _c("update", OK, f"已是最新 / up to date ({r['current']})")
    if r["status"] == "ahead":
        return _c("update", OK, f"比公开仓库最新的发布版还新，不用管 / ahead of the newest public release, nothing to do ({r['current']} > {r['latest']})")
    if r["why"] == "off":
        return _c("update", WARN, "不检查 / check disabled (AGENTJ_UPDATE_URL=off)")
    return _c("update", WARN, f"查不到最新版本 / could not check ({r['why']})",
              "不是错误：多半是没网或 GitHub 连不上，不影响使用 / not an error: offline or GitHub unreachable; agentj still works")


def run(st: State | None = None, offline: bool = False) -> list[dict]:
    st = st or State()
    svc = service.status()
    out = [check_version(), check_python(), check_platform(), check_state(st)]
    if offline:
        out += [_c("relay", WARN, "跳过 / skipped (--offline)"), _c("dashboard", WARN, "跳过 / skipped (--offline)")]
    else:
        out += [check_relay(st), check_dashboard(st)]
    mig = check_migration(st)
    if mig:
        out.append(mig)
    out += [check_agent(st)]
    shared_row = check_codex_shared(st)
    if shared_row:
        out.append(shared_row)
    out += [check_agent_cli(st, svc), check_harness(svc), check_fence(st), check_danger(st), check_passphrase(st), check_bound(st),
            check_serve(st), check_service(svc, service.legacy_status()), check_alias(), check_estop(st), check_tasks(st),
            check_activity(st)]
    out += check_asr(st)
    out += check_preferences(st)
    out += check_main_identity(st)
    out.append(check_skills())
    lg = check_linger(svc)
    if lg:
        out.append(lg)
    out.append(check_update(offline))
    return out


def check_asr(st: State) -> list[dict]:
    """Local transcription (PROTOCOL §10.9): the rows come from asr.py (its engine, model, worker). A failure there is a row,
    never a crash of the doctor."""
    try:
        from . import asr
        from . import preferences
        rows = asr.doctor_rows(st.root,engine_override=preferences.get(preferences.effective(st),"voice.asr.engine"))
    except Exception as e:  # noqa: BLE001
        return [_c("asr", WARN, f"本地语音转写：检查失败（{type(e).__name__}）/ local transcription: check failed")]
    out = []
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict) and isinstance(r.get("id"), str) and r.get("status") in MARK and isinstance(r.get("summary"), str):
            out.append(_c(r["id"][:10], r["status"], r["summary"][:200], str(r.get("hint") or "")[:300]))
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


def check_preferences(st):
    from . import preferences, voice
    rows=[]
    try:
        cfg=preferences.effective(st)
        from .config_migrations import run
        pending=run(st,pending=True)['pending']
        if pending and st.exists():
            rows.append(_c("voice-migration",WARN,"configuration migrations pending; applied automatically at next serve start",
                           "agentj config migrate; agentj doctor --offline --json"))
        _,overrides=preferences.read()
        provider=preferences.get(cfg,'voice.tts.provider')
        model=preferences.get(overrides,'voice.tts.model')
        factory=preferences.SCHEMA['voice.tts.model'].get('providerDefaults',{}).get(provider,preferences.SCHEMA['voice.tts.model']['default']) if provider in ('openai','elevenlabs') else None
        if factory and model and model!=factory:
            old=model in ('gpt-4o-mini-tts','tts-1','tts-1-hd','gpt-4o-mini-tts-2025-03-20','gpt-4o-mini-tts-2025-12-15') and provider=='openai'
            rows.append(_c("voice-model",WARN,
                           "selected speech model retained; OpenAI shutdown 2027-01-06" if old else "custom speech model retained; check provider availability",
                           "agentj config set voice.tts.model "+factory))
        voice.validate_runtime(cfg)
        rows.append(_c("config",OK,"JSON5 valid: "+tilde(str(preferences.path()))))
        if preferences.get(cfg,'voice.tts.mode')=='host' and provider=='command':
            rows.append(_c("voice-command",OK,"local speech command is available; stdout/stderr stay private"))
    except preferences.ConfigError as e:
        rows.append(_c("config",FAIL,str(e),"agentj config validate --json; agentj config rollback"))
    except OSError:
        rows.append(_c("config",FAIL,"configuration unreadable","agentj config validate --json"))
    hw=voice.hardware()
    rows.append(_c("hardware",OK,json.dumps(hw,ensure_ascii=False)))
    return rows


def check_main_identity(st):
    from . import main_identity, preferences, wizard
    rows = []
    try:
        version = main_identity.verify_core()["version"]
        rows.append(_c("main-core", OK, f"Packaged EN/ZH main-Agent core v{version}: pinned hashes verified"))
        cfg = st.agent_config() if st.exists() else None
        pref = preferences.get(preferences.effective(st), "agent.working_root", "")
        old = st.config().get("agent", {}).get("dir") if st.exists() else None
        root = main_identity.working_root(cfg or {"working_root": pref or old or "~/coding"})
        if not root.is_dir():
            rows.append(_c("work-root", FAIL if cfg or pref else WARN, "Working root is missing", "agentj init --working-root ~/coding"))
        else:
            from .fence import protected_paths
            protected = any(root == pathlib.Path(p).resolve() or pathlib.Path(p).resolve() in root.parents for p in protected_paths(st))
            if protected:
                rows.append(_c("work-root", FAIL, "Working root is inside a protected Agent J path", "Choose your coding root"))
            else:
                present = all((root / name).is_file() for name in ("CLAUDE.md", "AGENTS.md"))
                refs = present and all("agentj:main-core" in (root / name).read_text() for name in ("CLAUDE.md", "AGENTS.md"))
                rows.append(_c("work-root", OK if refs and pref else WARN, tilde(str(root)),
                               "agentj init --working-root <existing root>; existing files stay put" if not refs or not pref else ""))
                # An unrelated owner manifest is not an Agent J contract until the
                # root adopts our core reference or explicitly declares main_agent.
                # Migration records its location; it never forces a rewrite.
                adopted = any((root / name).is_file() and "agentj:main-core" in (root / name).read_text()
                              for name in ("CLAUDE.md", "AGENTS.md"))
                try:
                    structure = json.loads((root / "documentation/STRUCTURE.json").read_text())
                    adopted = adopted or isinstance(structure, dict) and structure.get("main_agent") is True
                except (OSError, ValueError):
                    pass
                for check in wizard.doctor(root):
                    name = check.get("name", "")
                    if check.get("status") == FAIL and name.startswith(("main", "workflow", "structure")):
                        rows.append(_c("root-" + name, FAIL if adopted else WARN,
                                       check.get("detail", "Invalid root structure") if adopted else "Existing owner documents retained; review before adopting the Agent J root contract",
                                       check.get("hint", "agentj wizard doctor") if adopted else "Review root entries and CEO roster; no automatic move or overwrite"))
        if cfg:
            ok, reason = main_identity.latest_audit(st, cfg)
            status = OK if ok else WARN if reason.startswith("no verified main Agent launch") else FAIL
            rows.append(_c("main-inject", status, reason, "Restart serve and send a message" if not ok else ""))
            if old and str(pathlib.Path(old).resolve()) != str(root):
                rows.append(_c("root-migrate", WARN, "Old files were kept in the previous work folder", "Review CEO roster and paths; no automatic move"))
    except (main_identity.IdentityError, preferences.ConfigError, OSError, ValueError):
        rows.append(_c("main-core", FAIL, "Main-Agent core or root configuration integrity failed", "Reinstall the trusted package; agentj config validate --json"))
    return rows


def check_skills(home=None) -> dict:
    """P57: every skill the package ships (agentj/skills/*: agentj-config, agentj-recall, agentj-manual …) linked into the
    four harness skill folders. Never ✗: the main Agent works without them (its core names `agentj recall`); a foreign
    copy of the same name is the owner's and stays (a conflict, reported, never replaced)."""
    from . import personalize
    try:
        rows = personalize.status(home)
    except OSError:
        return _c("skills", WARN, "随包技能：检查失败 / bundled skills: check failed")
    names = personalize.bundled()
    missing = sorted({r["skill"] for r in rows if not r["installed"] and not r["conflict"]})
    foreign = sorted({r["skill"] for r in rows if r["conflict"]})
    if not missing and not foreign:
        return _c("skills", OK, f"随包技能已安装 / bundled skills linked: {', '.join(names)}")
    parts = ([f"未安装 / not linked: {', '.join(missing)}"] if missing else []) + \
            ([f"同名的是你自己的，保留 / your own copy kept: {', '.join(foreign)}"] if foreign else [])
    return _c("skills", WARN, "随包技能 / bundled skills — " + "; ".join(parts), "agentj skill install" if missing else "")
