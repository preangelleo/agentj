"""Agent J emergency rescue (P332): one command a non-technical helper runs when a computer's Agent J is offline.

  curl -fsSL https://agentj.app/rescue | sh            (rescue.sh embeds this file; it never needs `agentj serve`)

1. diagnose  read-only: system, agentj, service (systemd user / launchd, linger, ProcessType), relay reachability
             (DNS / TLS / proxy / clock), host.log summary, disk / memory / load, suspend and idle settings, `agentj doctor`
2. repair    low-risk and reversible only, each step announced in plain words: linger, reset-failed / start / enable the
             service, re-install the service definition, `agentj doctor --reconnect`
3. report    redacted on THIS machine (agentj.privacy when installed + the rules below), shown as a local copy, then sent over
             plain HTTPS to the Agent J Father support desk (install-session channel, FEEDBACK_API.md §Support) — works while the
             relay tunnel is down. The thread id is the ticket number the helper passes on.

Never reads or sends a key, token or password; never deletes a file; never uses sudo; never kills a process.
Stdlib only, Python ≥ 3.8 (macOS system python 3.9, Agent J's own interpreter, any distro python3).
Exit: 0 healthy (after repairs) · 3 still broken, report sent · 4 still broken and the report could not be sent · 2 usage.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import pathlib
import platform
import re
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

VERSION = "1"
MARKER = "AGENTJ-RESCUE v" + VERSION           # first line of every report: the support desk files it as label `rescue`
API = os.environ.get("AGENTJ_RESCUE_API", "https://agentj.app/api").rstrip("/")
SITE_HOST = "agentj.app"
RELAY_HOST = os.environ.get("AGENTJ_RESCUE_RELAY_HOST", "relay.agentj.app")
HOME = pathlib.Path.home()
DIR = pathlib.Path(os.environ.get("AGENTJ_RESCUE_DIR") or HOME / ".agentj-rescue")
MAX_BODY, MAX_ATTACH = 7800, 15800              # server: body ≤ 8000, attach ≤ 16000
INTERESTING = ("loop_stall", "relay_down", "relay_up", "serve_start", "serve_stop", "report_fail", "telegram_fail",
               "agent_read_failed", "bg_task_failed", "upgrade_wait", "seat_cert")
OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"
MARK = {OK: "✓", WARN: "!", FAIL: "✗", INFO: "·"}
_THREAD = re.compile(r"st_[A-Za-z0-9_-]{22}")
LANG = "zh"


def T(zh: str, en: str) -> str:
    """One bilingual line: the helper's language first, the other after a slash (short)."""
    return zh if LANG == "zh-only" else en if LANG == "en" else f"{zh} / {en}"


_QUIET = threading.local()       # P332: help_flow / report_only inside serve print nothing (thread-local, never sys.stdout)


def say(line: str = "") -> None:
    if not getattr(_QUIET, "on", False):
        print(line, flush=True)


# ------------------------------------------------------------------ small runners (never raise, never a shell string)
def run(argv, timeout=20, env=None):
    """(exit code, stdout+stderr text). 127 = not installed, 124 = timed out."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return 124, out
    except OSError as e:
        return 126, type(e).__name__


def has(cmd: str) -> bool:
    return shutil.which(cmd) is not None


SERVICE_MANAGERS = ("systemctl", "loginctl", "launchctl")


def foreign_manager(argv) -> bool:
    """The per-user service manager is per uid, not per HOME: a test or a simulation with a throw-away HOME (or AGENTJ_TEST=1)
    would otherwise start / enable units in the developer's real one (P332: a stand-in `agentj-p…` unit showed up in the real
    `agentj busy`). Then only a stand-in binary outside /usr and /bin may change it; the real one is refused."""
    if not argv or argv[0] not in SERVICE_MANAGERS:
        return False
    try:
        import pwd
        real = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, KeyError):
        return False
    if os.environ.get("AGENTJ_TEST") != "1" and os.path.realpath(str(HOME)) == real:
        return False
    w = os.path.realpath(shutil.which(argv[0]) or "")
    return not w or w.startswith(("/usr/", "/bin/", "/sbin/"))


def os_kind() -> str:
    s = platform.system()
    if s == "Darwin":
        return "macos"
    if s == "Linux":
        try:
            if "microsoft" in pathlib.Path("/proc/version").read_text().lower():
                return "wsl"
        except OSError:
            pass
        return "linux"
    return s.lower() or "unknown"


def service_name() -> str:
    n = os.environ.get("AGENTJ_SERVICE_NAME")
    if n and re.fullmatch(r"[A-Za-z0-9_.@-]{1,80}", n) and not n.endswith(".service"):
        return n
    return "net.agentj.host" if os_kind() == "macos" else "agentj"


def state_dir() -> pathlib.Path:
    d = os.environ.get("AGENTJ_STATE_DIR") or os.environ.get("AGENTJARVIS_STATE_DIR")
    return pathlib.Path(d).expanduser() if d else HOME / ".local/state/agentj"


def agentj_bin() -> str | None:
    for c in (os.environ.get("AGENTJ_RESCUE_AGENTJ"), shutil.which("agentj"), str(HOME / ".local/bin/agentj")):
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


# ------------------------------------------------------------------ redaction (layer 1 on this machine)
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_IPV6 = re.compile(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{1,4}(?![0-9A-Fa-f:])")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_MAC = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")
_PREFIXED = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|sk-ant-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[abpr]-[A-Za-z0-9-]{10,}|"
                       r"AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|aj[a-z]_[A-Za-z0-9_-]{20,}|AJI-[0-9a-fA-F]{32}|"
                       r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|[0-9]{8,10}:[A-Za-z0-9_-]{35})")
_KV = re.compile(r"(?i)((?:api[_-]?key|apikey|secret|token|password|passwd|pwd|pass|auth|credentials?|access[_-]?key|"
                 r"[A-Za-z0-9_]*(?:_key|_secret|_token|_pass))[\"']?\s*[:=]\s*)([\"']?)[^\s\"'<>,]{4,}")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_LONG = re.compile(r"(?<![A-Za-z0-9_/-])[A-Za-z0-9_+/=-]{28,}(?![A-Za-z0-9_/-])")
_URLCRED = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@")
_QUERY = re.compile(r"(?i)([?&](?:key|token|sig|signature|code|secret|auth|pass)[^=&\s]*=)[^&\s]+")
_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_ID = re.compile(r"\b(?:st|sm|fb|tk|dv|hk)_[A-Za-z0-9_-]{16,}\b")
_PHONE = re.compile(r"(?<![\w.:-])\+?\d[\d ()-]{8,}\d(?![\w.:])")
_HANDLE = re.compile(r"(?<![\w.@/])@[A-Za-z][A-Za-z0-9_]{3,31}\b")     # @bot / @user handles (Telegram, …)
_CHANNEL_KW = re.compile(r"(?i)(channel\s*[:=]?\s*)[A-Za-z0-9_-]{22}(?![A-Za-z0-9_-])")   # P332: `agentj doctor` prints it
_KEEP_IP = {"127.0.0.1", "0.0.0.0", "255.255.255.255"}
# default / generic machine names (a distribution's own default hostname, e.g. the distro's ID from /etc/os-release) say
# nothing about the owner and are what the diagnosis needs: never replaced by <host>
_GENERIC_HOSTS = {"localhost", "archlinux", "arch", "ubuntu", "debian", "fedora", "linux", "macbook", "wsl"}
_DISTRO = None


def _distro_names() -> set:
    """ID / ID_LIKE / NAME words of /etc/os-release, lower-case (cached; empty on macOS)."""
    global _DISTRO
    if _DISTRO is None:
        names = set()
        try:
            for line in pathlib.Path("/etc/os-release").read_text().splitlines():
                k, _, v = line.partition("=")
                if k in ("ID", "ID_LIKE", "NAME"):
                    names |= {w.lower() for w in v.strip().strip('"').split() if len(w) >= 3}
        except OSError:
            pass
        _DISTRO = names
    return _DISTRO


def _generic_host(name) -> bool:
    n = (name or "").lower()
    return n in _GENERIC_HOSTS or n in _distro_names()


def _ip6(m) -> str:
    """Clock times (09:01:33) and plain numbers with colons are not IPv6: an address has a hex letter, '::' or ≥ 4 colons."""
    t = m.group(0)
    if "::" in t or t.count(":") >= 4 or re.search(r"[A-Fa-f]", t):
        return "<ip6>"
    return t


def _entropic(tok: str) -> bool:
    if not (re.search(r"[A-Z]", tok) and re.search(r"[a-z]", tok) and re.search(r"\d", tok)) and not re.fullmatch(r"[0-9a-f]{32,}", tok):
        return False
    import math
    from collections import Counter
    n = len(tok)
    return -sum(c / n * math.log2(c / n) for c in Counter(tok).values()) >= 3.5


def redact(text: str) -> str:
    """Deterministic, conservative: credentials, emails, IPs, MACs, UUIDs, phone-like numbers, home paths, user / host
    names. Agent J's own privacy.redact runs first when the Agent J host has it (same rules as `agentj support`)."""
    user, host = _identity()
    try:
        from agentj import privacy as _p  # type: ignore[import-not-found]
        text = _p.redact(text, host=None if _generic_host(host) else host, user=user, paths=True)
    except Exception:   # noqa: BLE001 — not installed / older version: the local rules below still apply
        pass
    text = _URLCRED.sub(r"\1<redacted>@", text)
    text = _QUERY.sub(r"\1<redacted>", text)
    text = _BEARER.sub(r"\1 <redacted>", text)
    text = _PREFIXED.sub("<redacted-key>", text)
    text = _KV.sub(lambda m: m.group(1) + m.group(2) + "<redacted>", text)
    text = _EMAIL.sub("<email>", text)
    ch = _channel()
    if ch:
        text = text.replace(ch, "<channel>")
    text = _CHANNEL_KW.sub(r"\1<channel>", text)
    text = _HANDLE.sub("<handle>", text)
    text = _MAC.sub("<mac>", text)
    text = _UUID.sub("<uuid>", text)
    text = _ID.sub("<id>", text)
    text = _IPV4.sub(lambda m: m.group(0) if m.group(0) in _KEEP_IP else "<ip>", text)
    text = _IPV6.sub(_ip6, text)
    text = _LONG.sub(lambda m: "<redacted>" if _entropic(m.group(0)) else m.group(0), text)
    text = _PHONE.sub(lambda m: "<num>" if len(re.sub(r"\D", "", m.group(0))) >= 10 and not re.fullmatch(r"\d{10}", m.group(0).strip()) else m.group(0), text)
    for p in sorted({str(HOME), os.path.realpath(str(HOME))}, key=len, reverse=True):
        if len(p) > 1:
            text = text.replace(p, "~")
    text = re.sub(r"(/(?:home|Users)/)[^/\s:]+", r"\1<user>", text)
    for name, tag in ((user, "<user>"), (host, "<host>")):
        if name and len(name) >= 3 and not (tag == "<host>" and _generic_host(name)):
            text = re.sub(r"(?i)(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])", tag, text)
    return text


def _channel() -> str | None:
    """the Agent J host's relay channel id (state config.json), so no report carries it (P332 security review MEDIUM-3)."""
    try:
        ch = json.loads((state_dir() / "config.json").read_text()).get("channel")
    except (OSError, ValueError, AttributeError):
        return None
    return ch if isinstance(ch, str) and len(ch) >= 16 else None


def _identity():
    try:
        user = getpass.getuser()
    except Exception:   # noqa: BLE001
        user = os.environ.get("USER")
    host = socket.gethostname().split(".")[0] or None
    return user, host


# ------------------------------------------------------------------ checks
class Report:
    def __init__(self):
        self.checks = []        # (id, status, text)
        self.details = []       # (title, redacted-later text)
        self.facts = {}

    def add(self, cid: str, status: str, zh: str, en: str):
        self.checks.append((cid, status, T(zh, en)))
        say(f"  {MARK[status]} {T(zh, en)}")

    def get(self, cid: str):
        for c in self.checks:
            if c[0] == cid:
                return c[1]
        return None

    def detail(self, title: str, text: str):
        if text and text.strip():
            self.details.append((title, text.strip()))


def check_system(r: Report):
    k = os_kind()
    r.facts["os_kind"] = k
    pretty = platform.platform()
    if k == "macos":
        _, v = run(["sw_vers", "-productVersion"], 5)
        pretty = f"macOS {v.strip()} {platform.machine()}"
    else:
        try:
            for line in pathlib.Path("/etc/os-release").read_text().splitlines():
                if line.startswith("PRETTY_NAME="):
                    pretty = line.split("=", 1)[1].strip('"') + f" {platform.machine()}"
        except OSError:
            pass
    r.facts.update(os=pretty)   # PRETTY_NAME already names the distribution (and its desktop edition)
    r.add("os", INFO, f"系统：{r.facts['os']}，Python {platform.python_version()}", f"system: {r.facts['os']}")
    up = None
    try:
        up = float(pathlib.Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError):
        rc, out = run(["sysctl", "-n", "kern.boottime"], 5)
        m = re.search(r"sec = (\d+)", out)
        if m:
            up = time.time() - int(m.group(1))
    if up is not None:
        r.facts["uptime_h"] = round(up / 3600, 1)
        r.add("uptime", INFO, f"开机已 {up / 3600:.1f} 小时", f"up {up / 3600:.1f} h")
    try:
        l1, l5, _ = os.getloadavg()
        n = os.cpu_count() or 1
        st = WARN if l5 > n * 2 else OK
        r.add("load", st, f"负载 {l1:.1f}/{l5:.1f}（{n} 核）{'，偏高' if st == WARN else ''}", f"load {l1:.1f}/{l5:.1f} on {n} CPUs")
    except OSError:
        pass
    mem = _memory(k)
    if mem:
        total, avail = mem
        st = FAIL if avail < 200 * 2**20 else WARN if avail < 600 * 2**20 else OK
        r.add("memory", st, f"内存可用 {avail / 2**30:.1f} / {total / 2**30:.1f} GB", f"memory free {avail / 2**30:.1f} of {total / 2**30:.1f} GB")
    for label, path in (("home", HOME), ("tmp", pathlib.Path(os.environ.get("TMPDIR") or "/tmp"))):
        try:
            du = shutil.disk_usage(path)
        except OSError:
            continue
        free = du.free / 2**30
        st = FAIL if free < 0.5 else WARN if free < 2 or du.free / du.total < 0.03 else OK
        r.add(f"disk_{label}", st, f"磁盘（{'家目录' if label == 'home' else '临时目录'}）剩 {free:.1f} GB" + ("，快满了" if st != OK else ""),
              f"disk ({label}) free {free:.1f} GB")


def _memory(k):
    try:
        info = dict(l.split(":", 1) for l in pathlib.Path("/proc/meminfo").read_text().splitlines() if ":" in l)
        kb = lambda key: int(info[key].split()[0]) * 1024   # noqa: E731
        return kb("MemTotal"), kb("MemAvailable")
    except (OSError, KeyError, ValueError):
        pass
    if k == "macos":
        _, t = run(["sysctl", "-n", "hw.memsize"], 5)
        _, vm = run(["vm_stat"], 5)
        try:
            page = int(re.search(r"page size of (\d+)", vm).group(1))
            free = sum(int(re.search(rf"{n}:\s+(\d+)", vm).group(1)) for n in ("Pages free", "Pages inactive", "Pages speculative"))
            return int(t.strip()), free * page
        except (AttributeError, ValueError):
            return None
    return None


def check_sleep(r: Report):
    k = r.facts["os_kind"]
    if k == "linux":
        rc, out = run(["journalctl", "-b", "--since", "-24h", "-q", "--no-pager", "-o", "short-iso",
                       "-g", "PM: suspend (entry|exit)|Suspending system|System returned from sleep|Lid closed"], 15)
        if rc in (0, 1) and rc != 127:
            n_sleep = len(re.findall(r"PM: suspend entry|Suspending system", out))
            resumes = re.findall(r"^(\S+).*(?:PM: suspend exit|System returned from sleep)", out, re.M)
            r.facts["suspends_24h"] = n_sleep
            if n_sleep:
                r.add("suspend", WARN, f"过去 24 小时挂起过 {n_sleep} 次，最近一次醒来 {resumes[-1] if resumes else '未知'}——挂起期间手机必然离线",
                      f"suspended {n_sleep}× in 24 h; the phone is offline while the Agent J host sleeps")
            else:
                r.add("suspend", OK, "过去 24 小时没有挂起", "no suspend in the last 24 h")
            r.detail("suspend / resume (system journal, 24 h)", "\n".join(_sleep_lines(out)[-12:]))
        else:
            r.add("suspend", INFO, "读不到系统日志里的挂起记录（权限）", "cannot read suspend records (permission)")
        conf = ""
        for p in [pathlib.Path("/etc/systemd/logind.conf"), *sorted(pathlib.Path("/etc/systemd/logind.conf.d").glob("*.conf"))]:
            try:
                conf += "\n".join(l for l in p.read_text().splitlines() if re.match(r"\s*(IdleAction|HandleLid\w*|HandleSuspend\w*)\s*=", l)) + "\n"
            except OSError:
                pass
        if conf.strip():
            r.detail("logind.conf (non-default lines)", conf)
            if re.search(r"^\s*IdleAction\s*=\s*(suspend|hibernate|hybrid-sleep|suspend-then-hibernate)", conf, re.M):
                r.add("logind_idle", WARN, "系统设置了空闲自动挂起（logind IdleAction）", "logind suspends when idle (IdleAction)")
        hyp = HOME / ".config/hypr/hypridle.conf"
        if hyp.is_file():
            txt = hyp.read_text(errors="replace")
            blocks = re.findall(r"listener\s*\{([^}]*)\}", txt)
            susp = [b for b in blocks if re.search(r"on-timeout\s*=.*(systemctl\s+suspend|suspend)", b) and not re.search(r"^\s*#", b)]
            secs = [int(m.group(1)) for b in susp for m in [re.search(r"timeout\s*=\s*(\d+)", b)] if m]
            if secs:
                r.add("idle_suspend", WARN, f"桌面空闲 {min(secs) // 60} 分钟后会自动挂起（hypridle）",
                      f"the desktop suspends after {min(secs) // 60} idle minutes (hypridle)")
            else:
                r.add("idle_suspend", OK, "桌面空闲不会自动挂起（hypridle 没有 suspend）", "hypridle has no suspend step")
            r.detail("hypridle.conf listeners", "\n".join(_listener(b) for b in blocks))
        rc, out = run(["systemd-inhibit", "--list", "--no-pager", "--no-legend"], 8)
        if rc == 0:   # only the count and whether Agent J holds one: the list names other programs and users (MEDIUM-3)
            aj = any("agentj" in l.lower() for l in out.splitlines())
            r.facts["inhibitors"] = len(out.strip().splitlines())
            r.detail("sleep inhibitors", f"count {r.facts['inhibitors']} · agentj {'yes' if aj else 'no'}")
            if aj:
                r.add("inhibit", OK, "Agent J 正在阻止系统挂起", "Agent J holds a sleep inhibitor")
        sid = os.environ.get("XDG_SESSION_ID")
        if sid and re.fullmatch(r"[A-Za-z0-9_-]{1,32}", sid):   # this session only; never the list of everyone's sessions
            rc, out = run(["loginctl", "show-session", sid, "-p", "IdleHint", "-p", "LockedHint", "-p", "State"], 5)
            if rc == 0:
                kv = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
                vals = [f"{k}={kv[k].strip()}" for k in ("IdleHint", "LockedHint", "State")
                        if k in kv and re.fullmatch(r"[a-z-]{1,20}", kv[k].strip())]
                if vals:
                    r.detail("logind session", " ".join(vals))
    elif k == "macos":
        rc, out = run(["pmset", "-g"], 8)
        if rc == 0:   # numeric settings only (the "(sleep prevented by …)" notes name other programs)
            r.detail("pmset -g", "\n".join(l.strip() for l in out.splitlines() if re.fullmatch(r"\s*[A-Za-z]+\s+\d+\s*", l)))
            m = re.search(r"^\s*sleep\s+(\d+)", out, re.M)
            if m and int(m.group(1)) > 0:
                r.add("idle_suspend", WARN, f"这台 Mac 空闲 {m.group(1)} 分钟后会睡眠", f"this Mac sleeps after {m.group(1)} idle minutes")
            elif m:
                r.add("idle_suspend", OK, "这台 Mac 不会因空闲睡眠", "this Mac never sleeps on idle")
        rc, out = run(["pmset", "-g", "assertions"], 8)
        if rc == 0 and re.search(r"agentj|caffeinate", out, re.I):
            r.add("inhibit", OK, "有程序在阻止睡眠（caffeinate / agentj）", "something holds a sleep assertion")
        rc, out = run(["sh", "-c", "pmset -g log | grep -E ' (Sleep|Wake|DarkWake) ' | tail -8"], 25)
        if rc == 0 and out.strip():
            r.detail("pmset log (last sleep / wake)", "\n".join(_pmset_lines(out)))


# P332 security review MEDIUM-3: raw command output is never a detail; each source is cut down to allowlisted fields
_UNIT_PROPS = ("LoadState", "ActiveState", "SubState", "UnitFileState", "NRestarts", "Result", "ExecMainStatus",
               "ActiveEnterTimestamp")
_SAFE = re.compile(r"[A-Za-z0-9_.:-]{1,40}")
HOSTLOG_KEYS = ("ts", "ev", "code", "phase", "why", "status", "count", "ms", "result")
_JOURNAL = re.compile(r"^(\S+)\s+\S+\s+(\S+?)(?:\[\d+\])?:\s+(.*)$")
_MANAGER_MSG = re.compile(r"(?i)\b(?:start|stop|fail|exit|restart|deactivat|result|status=|code=|kill|consumed|dump)")


def _sleep_lines(out: str) -> list:
    """System journal suspend records → `<time> suspend entry|exit` (no host name, no other message text)."""
    res = []
    for l in out.splitlines():
        m = _JOURNAL.match(l)
        if not m:
            continue
        kind = ("suspend entry" if re.search(r"PM: suspend entry|Suspending system", l) else
                "suspend exit" if re.search(r"PM: suspend exit|System returned from sleep", l) else
                "lid closed" if "Lid closed" in l else None)
        if kind:
            res.append(f"{m.group(1)} {kind}")
    return res


def _listener(block: str) -> str:
    """A hypridle listener → its timeout and what kind of step it is (never the command line itself)."""
    m = re.search(r"timeout\s*=\s*(\d+)", block)
    cmd = " ".join(re.findall(r"on-timeout\s*=\s*(.*)", block))
    kind = ("suspend" if "suspend" in cmd else "lock" if "lock" in cmd else "screen" if re.search(r"dpms|brightness", cmd) else
            "other" if cmd else "none")
    return f"listener timeout={m.group(1) if m else '?'} action={kind}"


def _pmset_lines(out: str) -> list:
    """`pmset -g log` sleep / wake lines → `<date time> Sleep|Wake|DarkWake` (the reasons can name apps and networks)."""
    res = []
    for l in out.splitlines():
        m = re.match(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s+\S+\s+(Sleep|Wake|DarkWake)\b", l)
        if m:
            res.append(f"{m.group(1)} {m.group(2)}")
    return res


def _manager_lines(out: str) -> list:
    """The service's journal → only the service manager's own lines (systemd[…]) about start / stop / failure / exit codes;
    never the program's output (which can carry titles, names, networks)."""
    res = []
    for l in out.splitlines():
        m = _JOURNAL.match(l)
        if m and m.group(2) == "systemd" and _MANAGER_MSG.search(m.group(3)):
            res.append(f"{m.group(1)} systemd: {m.group(3)[:160]}")
    return res


def hostlog_line(e) -> str | None:
    """One host.log record → the allowlisted keys only; values are numbers or short plain tokens, anything else dropped."""
    if not isinstance(e, dict):
        return None
    out = {}
    for k in HOSTLOG_KEYS:
        v = e.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = v
        elif isinstance(v, str) and _SAFE.fullmatch(v):
            out[k] = v
    return json.dumps(out, ensure_ascii=True) if out else None


def check_agentj(r: Report):
    b = agentj_bin()
    r.facts["agentj"] = bool(b)
    if not b:
        r.add("agentj", FAIL, "没有找到 agentj 命令：这台Agent J 主机没装 Agent J，或装坏了", "agentj not found (not installed or broken)")
        return
    rc, out = run([b, "--version"], 25)
    ver = out.strip().splitlines()[-1] if out.strip() else ""
    r.facts["agentj_version"] = ver[:80]
    if rc != 0:
        r.add("agentj", FAIL, f"agentj 命令启动失败（退出码 {rc}）", f"agentj fails to start (exit {rc})")
        r.detail("agentj --version", out[-2000:])
    else:
        r.add("agentj", OK, f"Agent J 已安装：{ver}", f"Agent J installed: {ver}")
    sd = state_dir()
    if not sd.exists():
        r.add("state", WARN, "没有 Agent J 数据目录（没初始化过？）", "no Agent J state directory")


def check_service(r: Report):
    k, n = r.facts["os_kind"], service_name()
    r.facts["service"] = n
    if k in ("linux", "wsl"):
        if not has("systemctl"):
            r.add("service", FAIL, "没有 systemd：Agent J 后台服务没法常驻", "no systemd here")
            return
        rc, out = run(["systemctl", "--user", "is-system-running"], 8)
        if rc == 127 or "Failed to connect" in out or "No medium" in out:
            r.add("user_manager", FAIL, "systemd 用户实例连不上（没有登录会话？）", "cannot reach the systemd user manager")
            r.facts["no_user_manager"] = True
        rc, out = run(["systemctl", "--user", "show", f"{n}.service", "-p",
                       "LoadState,ActiveState,SubState,UnitFileState,NRestarts,Result,ExecMainStatus,ActiveEnterTimestamp"], 8)
        props = {k: v for k, v in (l.split("=", 1) for l in out.splitlines() if "=" in l) if k in _UNIT_PROPS}
        r.facts["unit"] = props
        r.detail(f"systemctl --user show {n}.service",
                 "\n".join(f"{k}={v}" for k, v in props.items()) or "no answer: " + out.strip()[:160])
        load, active = props.get("LoadState", "?"), props.get("ActiveState", "?")
        if load == "not-found":
            r.add("service", FAIL, f"后台服务没有安装（{n}.service 不存在）", f"service {n}.service not installed")
        elif active == "active":
            r.add("service", OK, f"后台服务在运行（自 {props.get('ActiveEnterTimestamp', '?')}，重启过 {props.get('NRestarts', '0')} 次）",
                  f"service running since {props.get('ActiveEnterTimestamp', '?')}")
        elif active == "failed":
            r.add("service", FAIL, f"后台服务崩了（{props.get('Result', '?')}）", f"service failed ({props.get('Result', '?')})")
        else:
            r.add("service", FAIL, f"后台服务没在运行（{active}/{props.get('SubState', '?')}）", f"service not running ({active})")
        if props.get("UnitFileState") not in (None, "enabled", "linked", "enabled-runtime") and load != "not-found":
            r.add("service_enabled", WARN, "后台服务没设成开机自启", "service is not enabled at login")
        rc, out = run(["journalctl", "--user", "-u", f"{n}.service", "-n", "200", "--no-pager", "-o", "short-iso"], 10)
        if rc == 0:
            r.detail(f"journal of {n}.service (service manager lines, last 40)", "\n".join(_manager_lines(out)[-40:]))
        if k == "linux":
            user = _identity()[0] or ""
            rc, out = run(["loginctl", "show-user", user, "-p", "Linger"], 5)
            lg = out.strip().split("=", 1)[1] if rc == 0 and "=" in out else ("yes" if os.path.exists(f"/var/lib/systemd/linger/{user}") else "no")
            r.facts["linger"] = lg
            if lg == "yes":
                r.add("linger", OK, "linger 已开：退出登录 / 重启后服务照样跑", "linger on")
            else:
                r.add("linger", WARN, "linger 没开：注销或重启后，没人登录时服务不会运行", "linger off: no service until someone logs in")
    elif k == "macos":
        plist = HOME / "Library/LaunchAgents" / f"{n}.plist"
        uid = os.getuid()
        if not plist.is_file():
            r.add("service", FAIL, "后台服务没有安装（LaunchAgent 不存在）", "LaunchAgent not installed")
            return
        try:
            import plistlib
            pt = plistlib.loads(plist.read_bytes()).get("ProcessType")
            r.facts["process_type"] = pt
            r.add("process_type", INFO if pt in (None, "Background", "Standard", "Interactive") else WARN,
                  f"launchd ProcessType = {pt or '默认'}", f"launchd ProcessType = {pt or 'default'}")
        except Exception:   # noqa: BLE001
            pass
        rc, out = run(["launchctl", "print", f"gui/{uid}/{n}"], 8)
        if rc != 0:
            r.facts["launchd_loaded"] = False
            r.add("service", FAIL, "后台服务没加载到 launchd", "service not loaded in launchd")
            return
        r.facts["launchd_loaded"] = True
        state = (re.search(r"^\s*state = (\S+)", out, re.M) or [None, "?"])[1]
        last = (re.search(r"last exit code = (.+)", out) or [None, "?"])[1]
        runs = (re.search(r"^\s*runs = (\d+)", out, re.M) or [None, "?"])[1]
        r.detail(f"launchctl print gui/<uid>/{n} (summary)", f"state = {state}\nruns = {runs}\nlast exit code = {last}")
        if state == "running":
            r.add("service", OK, f"后台服务在运行（启动过 {runs} 次）", "service running")
        else:
            r.add("service", FAIL, f"后台服务没在运行（{state}，上次退出 {last}）", f"service not running ({state}, last exit {last})")
    else:
        r.add("service", INFO, "这个系统不是 Linux / macOS：Agent J 应该跑在 WSL 里", "not Linux/macOS: Agent J runs inside WSL")
    rc, out = run(["pgrep", "-f", r"agentj.* serve|agentj serve"], 5)
    if rc in (0, 1):
        cnt = len([l for l in out.split() if l.strip().isdigit()])
        r.facts["serve_procs"] = cnt
        if cnt > 1:
            r.add("serve_procs", WARN, f"有 {cnt} 个 agentj serve 进程同时在跑", f"{cnt} agentj serve processes")


def resolve(host: str, timeout: float = 8.0, lookup=None) -> str | None:
    """None = `host` resolves; else the error's class name ("timeout" when it takes longer than `timeout`). The lookup runs
    in a daemon worker thread joined with a timeout: no process-wide socket default changes (this runs inside serve)."""
    box = {}
    fn = lookup or socket.getaddrinfo

    def work():
        try:
            fn(host, 443, type=socket.SOCK_STREAM)
            box["ok"] = True
        except Exception as e:   # noqa: BLE001 — reported as a name only
            box["err"] = type(e).__name__
    t = threading.Thread(target=work, name="rescue-dns", daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return "timeout"
    return None if box.get("ok") else box.get("err", "OSError")


def check_network(r: Report):
    proxies = [k for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy") if os.environ.get(k)]
    r.facts["proxy"] = bool(proxies)
    r.add("proxy", INFO, "终端设置了代理：" + ", ".join(sorted({p.upper() for p in proxies})) if proxies else "终端没设代理",
          "proxy set: " + ", ".join(sorted({p.upper() for p in proxies})) if proxies else "no proxy in this terminal")
    for host, cid in ((SITE_HOST, "dns_site"), (RELAY_HOST, "dns_relay")):
        t0 = time.monotonic()
        err = resolve(host)
        if err is None:
            r.add(cid, OK, f"域名解析 {host} 正常（{(time.monotonic() - t0) * 1000:.0f} ms）", f"DNS {host} ok")
        else:
            r.add(cid, WARN if proxies else FAIL, f"域名解析 {host} 失败（{err}）", f"DNS {host} failed")
    for host, cid in ((SITE_HOST, "tls_site"), (RELAY_HOST, "tls_relay")):
        if r.get("dns_" + cid[4:]) != OK:
            continue
        t0 = time.monotonic()
        try:
            with socket.create_connection((host, 443), timeout=8) as s:
                with ssl.create_default_context().wrap_socket(s, server_hostname=host):
                    pass
            r.add(cid, OK, f"直连 {host}:443（TLS）正常（{(time.monotonic() - t0) * 1000:.0f} ms）", f"TLS {host} ok")
        except ssl.SSLCertVerificationError:
            r.add(cid, FAIL, f"{host} 证书校验失败（系统时间不对，或网络在拦截）", f"{host}: certificate check failed")
        except OSError as e:
            r.add(cid, WARN if proxies else FAIL, f"直连 {host}:443 不通（{type(e).__name__}）", f"cannot reach {host}:443")
    try:   # urllib uses the terminal's proxy settings: the same path the report takes
        t0 = time.monotonic()
        req = urllib.request.Request(f"https://{SITE_HOST}/robots.txt", headers={"user-agent": f"agentj-rescue/{VERSION}"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            date = resp.headers.get("date")
        r.facts["https"] = True
        r.add("https", OK, f"HTTPS 访问 {SITE_HOST} 正常（{(time.monotonic() - t0) * 1000:.0f} ms）", f"HTTPS {SITE_HOST} ok")
        if date:
            from email.utils import parsedate_to_datetime
            skew = time.time() - parsedate_to_datetime(date).timestamp()
            r.facts["clock_skew_s"] = round(skew)
            if abs(skew) > 120:
                r.add("clock", FAIL, f"Agent J 主机时间差了 {skew:+.0f} 秒：签名会被拒，手机连不上", f"clock off by {skew:+.0f} s: signatures are refused")
            else:
                r.add("clock", OK, f"Agent J 主机时间准确（差 {skew:+.0f} 秒）", "clock ok")
    except Exception as e:   # noqa: BLE001
        r.facts["https"] = False
        r.add("https", FAIL, f"HTTPS 访问 {SITE_HOST} 失败（{type(e).__name__}）：报告可能发不出去", f"HTTPS to {SITE_HOST} failed")


def check_hostlog(r: Report, now: float | None = None):
    p = state_dir() / "host.log"
    if not p.is_file():
        r.add("hostlog", INFO, "没有 host.log（服务从没跑起来过？）", "no host.log")
        return
    now = now or time.time()
    counts, last, last_ts = {}, {}, 0
    try:
        with p.open("rb") as f:
            f.seek(max(0, p.stat().st_size - 4 * 2**20))
            lines = f.read().decode(errors="replace").splitlines()
    except OSError:
        return
    tail = []
    for line in lines:
        try:
            e = json.loads(line)
            ts, ev = float(e.get("ts", 0)), str(e.get("ev", ""))
        except (ValueError, TypeError, AttributeError):
            continue
        last_ts = max(last_ts, ts)
        if (ev in INTERESTING or ev.endswith("_fail") or ev.endswith("_failed")) and _SAFE.fullmatch(ev):
            last[ev] = ts
            if now - ts <= 86400:
                counts[ev] = counts.get(ev, 0) + 1
            clean = hostlog_line(e)
            if clean:
                tail.append(clean)
    r.facts["hostlog_24h"] = counts
    fmt = lambda ts: time.strftime("%m-%d %H:%M", time.localtime(ts))   # noqa: E731
    age = (now - last_ts) / 60 if last_ts else None
    stalls, downs = counts.get("loop_stall", 0), counts.get("relay_down", 0)
    st = WARN if stalls > 20 or downs > 5 else OK
    r.add("hostlog", st, f"host.log 24 小时：卡顿 loop_stall {stalls} 次、relay 断开 {downs} 次、重连 {counts.get('relay_up', 0)} 次；"
          f"最后一行 {fmt(last_ts) if last_ts else '无'}（{age:.0f} 分钟前）" if age is not None else "host.log 为空",
          f"host.log 24 h: loop_stall {stalls}, relay_down {downs}, relay_up {counts.get('relay_up', 0)}")
    if last.get("relay_down", 0) > last.get("relay_up", 0):
        r.add("relay_state", FAIL, f"最近一次 relay 状态是「断开」（{fmt(last['relay_down'])}），之后没再连上", "the last relay event is relay_down")
    r.detail("host.log 24 h counts", json.dumps(counts, sort_keys=True))
    r.detail("host.log notable lines (last 30)", "\n".join(tail[-30:]))


def check_doctor(r: Report):
    b = agentj_bin()
    if not b or r.get("agentj") != OK:
        return
    rc, out = run([b, "doctor"], 150)
    r.facts["doctor_exit"] = rc
    r.add("doctor", OK if rc == 0 else WARN, "agentj doctor 全部通过" if rc == 0 else f"agentj doctor 有问题（退出码 {rc}），详情在报告里",
          "agentj doctor passed" if rc == 0 else f"agentj doctor reports problems (exit {rc})")
    r.detail("agentj doctor", out[-9000:])


def diagnose(with_doctor=True) -> Report:
    r = Report()
    say("\n" + T("【1/3】检查这台Agent J 主机（只读，不改任何东西）", "[1/3] Checking the Agent J host (read-only)"))
    for fn in (check_system, check_agentj, check_service, check_network, check_hostlog, check_sleep):
        try:
            fn(r)
        except Exception as e:   # noqa: BLE001 — one broken check never stops the rescue
            r.add(fn.__name__, INFO, f"这一项检查没跑完（{type(e).__name__}）", f"check incomplete ({type(e).__name__})")
    if with_doctor:
        check_doctor(r)
    return r


# ------------------------------------------------------------------ repairs (low-risk, reversible, announced)
def repair(r: Report, dry: bool = False) -> list:
    done = []
    k, n, b = r.facts.get("os_kind"), r.facts.get("service", service_name()), agentj_bin()

    def step(zh, en, argv, timeout=60):
        say(f"  → {T(zh, en)}")
        if dry:
            done.append((T(zh, en), "dry-run"))
            return True
        if foreign_manager(argv):
            say("    " + T("跳过：这是测试用的家目录，不碰这台Agent J 主机真正的服务管理器", "skipped: a test home never touches the real service manager"))
            done.append((T(zh, en), "skipped: test home"))
            return False
        rc, out = run(argv, timeout)
        ok = rc == 0
        say(f"    {'✓ 好了' if ok else f'✗ 没成功（退出码 {rc}）'}" if LANG != "en" else f"    {'✓ done' if ok else f'✗ failed (exit {rc})'}")
        done.append((T(zh, en), "ok" if ok else f"exit {rc}: {redact(out.strip())[-300:]}"))
        return ok

    say("\n" + T("【2/3】自动修复（只做低风险、可撤销的操作）", "[2/3] Repairing (low-risk, reversible steps only)"))
    if k == "linux" and r.facts.get("linger") == "no":
        user = _identity()[0] or ""
        if not step("打开 linger，让服务在没人登录时也能运行", "turning on linger", ["loginctl", "enable-linger", user], 20):
            say("    " + T(f"需要管理员权限：请Agent J 主机主人运行 sudo loginctl enable-linger {user}", "needs admin: sudo loginctl enable-linger <user>"))
    if k in ("linux", "wsl") and not r.facts.get("no_user_manager"):
        unit = r.facts.get("unit") or {}
        if unit.get("LoadState") == "not-found" and b:
            step("重新安装后台服务定义（agentj service install）", "re-installing the service definition", [b, "service", "install"], 90)
            if not dry:   # what the manager sees now (install also starts it): only a unit that really exists is started
                _, out = run(["systemctl", "--user", "show", f"{n}.service", "-p", "LoadState,ActiveState,UnitFileState"], 8)
                unit = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
        if unit.get("ActiveState") == "failed":
            step("清除服务的「失败」标记", "clearing the failed state", ["systemctl", "--user", "reset-failed", f"{n}.service"], 15)
        if unit.get("LoadState") != "not-found" and unit.get("UnitFileState") not in ("enabled", "linked", "enabled-runtime"):
            step("把后台服务设成登录后自动运行", "enabling the service at login", ["systemctl", "--user", "enable", f"{n}.service"], 20)
        if unit.get("LoadState") != "not-found" and unit.get("ActiveState") != "active":
            step("启动后台服务", "starting the service", ["systemctl", "--user", "start", f"{n}.service"], 60)
            if not dry:
                time.sleep(3)
    elif k == "macos":
        plist = HOME / "Library/LaunchAgents" / f"{n}.plist"
        uid = os.getuid()
        if not plist.is_file() and b:
            step("重新安装后台服务（agentj service install）", "re-installing the service", [b, "service", "install"], 90)
        elif plist.is_file() and r.facts.get("launchd_loaded") is False:
            step("把后台服务加载进 launchd", "loading the service into launchd", ["launchctl", "bootstrap", f"gui/{uid}", str(plist)], 20)
        elif r.get("service") == FAIL:
            step("重启后台服务", "restarting the service", ["launchctl", "kickstart", "-k", f"gui/{uid}/{n}"], 30)
    if b and r.get("agentj") == OK and k in ("linux", "wsl", "macos") and not r.facts.get("no_user_manager"):
        # the rescue watchdog (PROTOCOL §33): keeps the phone's 「紧急修复」 working when Agent J itself stops answering
        rc, out = run([b, "rescue", "status", "--json"], 20)
        try:
            rs = json.loads(out.strip().splitlines()[-1]) if rc == 0 else None
        except (ValueError, IndexError):
            rs = None
        if isinstance(rs, dict) and not rs.get("installed"):
            step("装上救援守护：以后Agent J 主机上的 Agent J 没响应时，手机「设置 → 紧急修复」也能修", "installing the rescue watchdog "
                 "(the phone's Settings → Emergency repair then works even when Agent J stops answering)", [b, "rescue", "install"], 60)
        elif rs is None:
            r.facts["no_watchdog"] = True   # an older Agent J: `agentj update` brings the watchdog
    if b and r.get("agentj") == OK:
        step("让手机重新连上（agentj doctor --reconnect：服务、relay、AI 会话逐个检查，坏哪个修哪个）",
             "bringing the phone back (agentj doctor --reconnect)", [b, "doctor", "--reconnect"], 180)
    if r.get("clock") == FAIL:
        say("  ! " + T("Agent J 主机时间不准：请主人在系统设置里打开「自动设置时间」（Linux: sudo timedatectl set-ntp true）",
                       "clock is wrong: turn on automatic time (Linux: sudo timedatectl set-ntp true)"))
    if any(r.get(c) == FAIL for c in ("disk_home", "disk_tmp")):
        say("  ! " + T("磁盘快满了：我不会删任何文件，请主人清理后再运行一次", "disk is almost full: nothing is deleted; free space and run again"))
    if r.get("idle_suspend") == WARN or r.get("logind_idle") == WARN:
        say("  ! " + T("这台Agent J 主机空闲会自动挂起/睡眠，挂起时手机一定离线；要一直在线请在电源设置里关掉自动挂起（我不改系统设置）",
                       "the Agent J host suspends when idle; turn auto-suspend off to stay reachable (not changed by this script)"))
    if not done:
        say("  · " + T("没有需要自动修的项目", "nothing to repair automatically"))
    return done


# ------------------------------------------------------------------ the report
def verdict(r: Report) -> bool:
    core = ("agentj", "service", "user_manager", "https", "clock", "relay_state")
    return not any(r.get(c) == FAIL for c in core) and r.get("tls_relay") in (OK, None) and r.get("dns_relay") in (OK, None, WARN)


def compose(before: Report, fixes: list, after: Report | None, tag: str = "") -> tuple:
    a = after or before
    healthy = verdict(a)
    head = [MARKER + (f" [{tag}]" if tag else ""),
            f"result: {'healthy after rescue' if healthy else 'STILL BROKEN'}",
            f"os: {before.facts.get('os')} · agentj: {before.facts.get('agentj_version') or 'not found'} · service: {before.facts.get('service')}",
            f"uptime_h: {before.facts.get('uptime_h')} · suspends_24h: {before.facts.get('suspends_24h')} · clock_skew_s: {a.facts.get('clock_skew_s')}",
            "", "## before"]
    head += [f"{MARK[s]} [{c}] {t}" for c, s, t in before.checks]
    head += ["", "## repairs"] + ([f"- {t} → {res}" for t, res in fixes] or ["- none"])
    if after:
        head += ["", "## after"] + [f"{MARK[s]} [{c}] {t}" for c, s, t in after.checks if s != INFO]
    body = redact("\n".join(head))
    if len(body) > MAX_BODY:
        body = body[:MAX_BODY - 20] + "\n…(truncated)"
    parts, used = [], 0
    for title, text in before.details + ([(f"after: {t}", x) for t, x in after.details if t.startswith("agentj doctor")] if after else []):
        chunk = f"### {title}\n{redact(text)}\n"
        if used + len(chunk) > MAX_ATTACH:
            chunk = chunk[:max(0, MAX_ATTACH - used - 20)] + "\n…(truncated)\n"
        parts.append(chunk)
        used += len(chunk)
        if used >= MAX_ATTACH:
            break
    return body, "".join(parts), healthy


def _http(method, url, token=None, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
    h = {"user-agent": f"agentj-rescue/{VERSION}", "accept": "application/json"}
    if body is not None:
        h["content-type"] = "application/json"
    if token:
        h["authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read(512 * 1024) or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read(64 * 1024) or b"{}")
        except ValueError:
            return e.code, {}


def _private_write(path: pathlib.Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


def session_token(http=_http) -> str:
    """the Agent J host's rescue install session (FEEDBACK_API.md: no auth to create, 30 days, kept 0600 here — never printed)."""
    p = DIR / "session.json"
    try:
        s = json.loads(p.read_text())
        if s.get("id", "").startswith("aji_") and s.get("until", 0) > time.time() + 3600:
            return s["id"]
    except (OSError, ValueError, AttributeError):
        pass
    code, obj = http("POST", API + "/v1/install-sessions")
    if code == 201 and str(obj.get("install_session_id", "")).startswith("aji_"):
        _private_write(p, json.dumps({"id": obj["install_session_id"], "until": time.time() + 29 * 86400}))
        return obj["install_session_id"]
    raise RuntimeError(f"install session: HTTP {code} {obj.get('error', '')} {obj.get('scope', '')}".strip())


def send(body: str, attach: str, http=_http, thread: str | None = None) -> str:
    """→ the thread id. `thread` (P332): a follow-up on that ticket (FEEDBACK_API.md: `thread` continues one of yours)."""
    tok = session_token(http)
    msg = {"kind": "bug", "body": body, "attach": attach, "lang": "en" if LANG == "en" else "zh", "install_md_version": "rescue-" + VERSION}
    if thread:
        msg["thread"] = thread
    code, obj = http("POST", API + "/v1/support/messages", tok, msg)
    if code == 401 or (code == 429 and obj.get("scope") == "install_total" and not thread):
        # expired session, or its lifetime message cap is used up (FEEDBACK_API: 20): one fresh one → a new ticket
        try:
            (DIR / "session.json").unlink()
        except OSError:
            pass
        tok = session_token(http)
        code, obj = http("POST", API + "/v1/support/messages", tok, msg)
    if code == 422:   # the server's scan still saw something: send only the status lines
        say("  · " + T("服务器的隐私扫描拦下了详情部分，改为只发检查结果", "the server scan refused the details; sending the summary only"))
        msg["attach"] = ""
        msg["body"] = "\n".join(l for l in body.splitlines() if l.startswith((MARKER, "result:", "✓", "✗", "!", "- ", "##")))[:MAX_BODY]
        code, obj = http("POST", API + "/v1/support/messages", tok, msg)
    if code == 201 and _THREAD.fullmatch(str(obj.get("thread", ""))) and thread:
        return obj["thread"]
    if code == 201 and _THREAD.fullmatch(str(obj.get("thread", ""))):
        DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(DIR / "tickets.txt", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)   # 0600 from the first byte
        with os.fdopen(fd, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M')} {obj['thread']}\n")
        return obj["thread"]
    raise RuntimeError(f"support: HTTP {code} {obj.get('error', '')} {obj.get('scope', '')}".strip())


def wait_reply(thread: str, seconds: int = 75, http=_http) -> str | None:
    tok = session_token(http)
    end, after = time.monotonic() + seconds, 0
    while time.monotonic() < end:
        code, obj = http("GET", f"{API}/v1/support/threads/{thread}?after={after}&wait=25", tok, None, 40)
        if code != 200:
            return None
        msgs = [m for m in obj.get("messages", []) if m.get("author") in ("support", "system")]
        if msgs:
            return "\n\n".join(str(m.get("body", "")) for m in msgs)
        after = obj.get("cursor", after) or after
    return None


def print_advice(text: str):
    say(T("—— Agent J Father 的回复（这是建议，不是命令；看不懂就不要照做，把工单号发给帮你的人）——",
          "—— Agent J Father's reply (advice, not orders: if unsure, do nothing and pass on the ticket) ——"))
    for line in text.splitlines():
        say("  │ " + line)
    say("  └──")


def report_only(lang: str = "zh", tag: str = "watchdog", quiet: bool = True) -> tuple:
    """For the rescue watchdog (PROTOCOL §33 `rescue_do report`): diagnose (no repairs, no `agentj doctor` — serve may be
    the stuck part), redact, send. → (ticket or None, healthy, error or ""). Prints nothing when `quiet`."""
    global LANG
    LANG = "en" if lang == "en" else "zh"
    was, _QUIET.on = getattr(_QUIET, "on", False), bool(quiet)   # P332: thread-local — serve's own output is untouched
    try:
        r = diagnose(with_doctor=False)
        body, attach, healthy = compose(r, [], None, tag)
        _private_write(DIR / f"report-{time.strftime('%Y%m%d-%H%M%S')}.txt", body + "\n\n" + attach)
        try:
            return send(body, attach), healthy, ""
        except Exception as e:   # noqa: BLE001
            return None, healthy, str(e)[:200]
    finally:
        _QUIET.on = was


# ------------------------------------------------------------------ P332 ②: Agent J Father help (PROTOCOL §33)
ACTIONS = ("reconnect", "restart", "linger", "rollback", "new_session")   # the ONLY things support may ask a host to run
AUTO_ACTIONS = ("reconnect", "restart")   # what ① does anyway: the only ones that run on support's word (review P332 HIGH-1)
MAX_ACTIONS = 5
_ACTIONS_LINE = re.compile(r"^AGENTJ-ACTIONS: [a-z_,]+$")
CATEGORIES = ("service_down", "relay_down", "agent_down", "sleep", "linger", "clock", "disk", "not_installed", "network", "other")
_CATEGORY = re.compile(r"(?mi)^\s*category:\s*([a-z_]+)\s*$")


def parse_actions(reply_text) -> list:
    """The LAST line that is exactly `AGENTJ-ACTIONS: a,b` → the names on ACTIONS (unknown dropped), deduplicated, in order,
    at most MAX_ACTIONS. Anything else in the reply is advice for a human, never a command."""
    if not isinstance(reply_text, str):
        return []
    lines = [l for l in reply_text.splitlines() if _ACTIONS_LINE.match(l)]
    if not lines:
        return []
    out = []
    for name in lines[-1].split(": ", 1)[1].split(","):
        if name in ACTIONS and name not in out:
            out.append(name)
    return out[:MAX_ACTIONS]


def advice_of(reply_text: str) -> str:
    """Support's words without the AGENTJ-ACTIONS line(s): what the owner reads."""
    return "\n".join(l for l in (reply_text or "").splitlines() if not _ACTIONS_LINE.match(l)).strip()


def _wait_support(thread, after, seconds, http, clock):
    """(text or None, cursor): the next support / system message(s) on `thread` newer than `after`, ≤ `seconds`."""
    tok = session_token(http)
    end = clock() + seconds
    while clock() < end:
        code, obj = http("GET", f"{API}/v1/support/threads/{thread}?after={after}&wait={max(1, min(25, int(end - clock())))}",
                         tok, None, 40)
        if code != 200 or not isinstance(obj, dict):
            return None, after
        cur = obj.get("cursor")
        msgs = [m for m in obj.get("messages", []) if isinstance(m, dict) and m.get("author") in ("support", "system")]
        if isinstance(cur, int) and cur > after:
            after = cur
        if msgs:
            seqs = [m["seq"] for m in msgs if isinstance(m.get("seq"), int)]
            return "\n\n".join(str(m.get("body", "")) for m in msgs), max([after] + seqs)
    return None, after


_H = {   # plain sentences: never relay / serve / harness / session id (§33)
    "diag": ("正在检查这台Agent J 主机（只看不改）…", "Checking the Agent J host (read-only)…"),
    "diag_ok": ("检查完了：{n} 项有问题。", "Checked: {n} problem(s) found."),
    "send": ("正在把去掉隐私的报告发给 Agent J Father…", "Sending a privacy-cleaned report to Agent J Father…"),
    "sent": ("报告已发出，工单号 {t}。", "Report sent, ticket {t}."),
    "send_fail": ("报告没发出去，请稍后再试，或让Agent J 主机旁的人运行救援命令。", "The report could not be sent; try again later, or ask someone at the Agent J host to run the rescue line."),
    "wait": ("正在等 Agent J Father 回复（最多 {m} 分钟）…", "Waiting for Agent J Father (up to {m} minutes)…"),
    "no_reply": ("Agent J Father 还没回复，回复会留在工单 {t} 里。", "No reply yet from Agent J Father; it will arrive on ticket {t}."),
    "advice": ("Agent J Father 的建议：{a}", "Agent J Father suggests: {a}"),
    "act": ("Agent J Father 请Agent J 主机做这一步：{x}", "Agent J Father asked the Agent J host to: {x}"),
    "offer": ("客服建议：{x}（点下面的按钮确认后才会做）", "Support suggests: {x} (tap the button below to confirm; nothing runs before that)"),
    "done_offer": ("客服建议的修复要你点按钮确认后才会做，工单号 {t}。", "Support's suggested repairs run only after you confirm them with the buttons; ticket {t}."),
    "scheduled": ("马上重启Agent J 主机上的 Agent J（报告发出后），大约半分钟后自动连回来。",
                  "Agent J on the Agent J host restarts right after this report; it reconnects by itself in about half a minute."),
    "posted": ("结果已告诉 Agent J Father。", "The results went back to Agent J Father."),
    "done_ok": ("Agent J Father 建议的修复都做完了。", "Every repair Agent J Father suggested is done."),
    "done_advice": ("Agent J Father 给了建议（没有需要Agent J 主机自动做的事），工单号 {t}。", "Agent J Father replied with advice (nothing for the Agent J host to run); ticket {t}."),
    "done_fail": ("还有修复没成功，Agent J Father 会继续看工单 {t}。", "Some repairs did not work; Agent J Father keeps working on ticket {t}."),
    "budget": ("时间到了，Agent J Father 会继续看工单 {t}。", "Time is up; Agent J Father keeps working on ticket {t}."),
    "error": ("出了点意外，请稍后再试。", "Something unexpected happened; please try again later."),
}
ACTION_WORDS = {"reconnect": ("重新连接", "reconnect"), "restart": ("重启Agent J 主机上的 Agent J", "restart Agent J on the Agent J host"),
                "linger": ("让 Agent J 在没人登录时也能运行", "keep Agent J running when nobody is logged in"),
                "rollback": ("退回上一个版本", "go back to the previous version"),
                "new_session": ("开一个新对话", "start a new conversation")}


def help_flow(lang, emit, run_action, *, with_doctor=False, http=_http, rounds=3, budget_s=900, wait_s=180, tag="help",
              clock=time.monotonic, diagnose_fn=None, estop=False) -> dict:
    """§33 ② for serve (and the rescue watchdog): 1 diagnose (read-only, quiet, no repairs) → 2 redact + send
    `AGENTJ-RESCUE v1 [help]` → ticket → 3 wait for support → its advice shown → 4 support's whitelisted actions: only
    AUTO_ACTIONS (reconnect, restart) run (`run_action(name) -> (ok, plain_text)`; ok None = scheduled to run after this
    flow, e.g. a restart); the others (rollback, linger, new_session) are only OFFERED — one plain sentence and
    `offer=[…]` on that step and on the result, the owner's own signed `rescue_do <action>` runs them — and during an
    e-stop (`estop`) every action is offered, none runs → 5 results posted on the same ticket (`[result]`, offered vs run);
    ≤ `rounds`, ≤ `budget_s`. A round that offers something ends the flow (the next move is the owner's).
    `emit(step, state, text, offer=None)` (a 3-argument emit still works when nothing is offered).
    → {"ok", "ticket"?, "category"?, "offer"?, "pending"?, "step", "text"}. Never raises."""
    global LANG
    LANG = "en" if lang == "en" else "zh"
    en = LANG == "en"

    def H(key, **kw):
        return _H[key][1 if en else 0].format(**kw)

    def say_step(step, state, text, offer=None):
        try:
            if offer:
                try:
                    emit(step, state, text, offer=list(offer))
                except TypeError:     # an older 3-argument emit: the sentence still reaches the owner
                    emit(step, state, text)
            else:
                emit(step, state, text)
        except Exception:   # noqa: BLE001 — a lost progress line never stops the rescue
            pass

    t0, step, ticket, category, offered, pending = clock(), 1, None, None, [], []

    def end(ok, text, **kw):
        res = {"ok": ok, "step": step, "text": text, **kw, **_cat(category)}
        if ticket:
            res["ticket"] = ticket
        if offered:
            res["offer"] = list(offered)
        if pending:
            res["pending"] = list(pending)
        return res

    def summary():
        if not all_ok:
            return H("done_fail", t=ticket)
        if ran_any:
            return H("done_ok") + (" " + H("scheduled") if pending else "")
        return H("scheduled") if pending else H("done_advice", t=ticket)

    cursor, ran_any, all_ok = 0, False, True
    was, _QUIET.on = getattr(_QUIET, "on", False), True
    try:
        say_step(1, "run", H("diag"))
        r = (diagnose_fn or diagnose)(with_doctor=with_doctor)
        say_step(1, "ok", H("diag_ok", n=sum(1 for c in r.checks if c[1] == FAIL)))
        step = 2
        say_step(2, "run", H("send"))
        body, attach, _healthy = compose(r, [], None, tag)
        try:
            _private_write(DIR / f"report-{time.strftime('%Y%m%d-%H%M%S')}.txt", body + "\n\n" + attach)
        except OSError:
            pass
        try:
            ticket = send(body, attach, http)
        except Exception:   # noqa: BLE001
            say_step(2, "fail", H("send_fail"))
            return {"ok": False, "step": 2, "text": H("send_fail")}
        say_step(2, "ok", H("sent", t=ticket))
        for _ in range(max(1, int(rounds))):
            step = 3
            left = budget_s - (clock() - t0)
            if left <= 5:
                return end(False, H("budget", t=ticket))
            say_step(3, "run", H("wait", m=max(1, round(min(wait_s, left) / 60))))
            reply, cursor = _wait_support(ticket, cursor, min(wait_s, left), http, clock)
            if not reply:
                say_step(3, "fail", H("no_reply", t=ticket))
                if ran_any or pending:
                    break
                return end(False, H("no_reply", t=ticket))
            m = _CATEGORY.search(reply)
            if m and m.group(1).lower() in CATEGORIES:
                category = m.group(1).lower()
            advice = advice_of(reply)
            if advice:
                say_step(3, "ok", H("advice", a=advice[:1500]))
            names = parse_actions(reply)
            if not names:
                return end(bool(ran_any or pending) and all_ok, summary())
            step, results = 4, []
            auto = [] if estop else [n for n in names if n in AUTO_ACTIONS]
            new_offers = [n for n in names if n not in auto]
            for name in auto:
                w = ACTION_WORDS[name][1 if en else 0]
                say_step(4, "run", H("act", x=w))
                try:
                    ok, plain = run_action(name)
                    ok, plain = (None if ok is None else bool(ok)), str(plain or "")[:300]
                except Exception as e:   # noqa: BLE001
                    ok, plain = False, type(e).__name__
                if ok is None:            # scheduled after this flow (a restart): not reported done before it is tried
                    if name not in pending:
                        pending.append(name)
                    say_step(4, "run", plain or H("scheduled"))
                    results.append(f"- {name}: scheduled — runs after this report")
                    continue
                ran_any, all_ok = True, all_ok and ok
                say_step(4, "ok" if ok else "fail", plain or w)
                results.append(f"- {name}: {'ok' if ok else 'failed'} — {redact(plain)[:200]}")
            if new_offers:
                for n in new_offers:
                    if n not in offered:
                        offered.append(n)
                words = ("、" if not en else ", ").join(ACTION_WORDS[n][1 if en else 0] for n in new_offers)
                say_step(4, "skip", H("offer", x=words), offer=new_offers)
                results += [f"- {n}: offered to the owner (runs only after the owner confirms)" for n in new_offers]
                if estop:
                    results.append("- note: the Agent J host is in stop-everything mode: nothing ran")
            step = 5
            try:
                send(redact("\n".join([MARKER + " [result]"] + results)), "", http, thread=ticket)
                say_step(5, "ok", H("posted"))
            except Exception:   # noqa: BLE001 — the owner still saw every result
                say_step(5, "fail", H("send_fail"))
            if new_offers:            # the next move is the owner's tap: stop waiting for support
                return end(False, H("done_offer", t=ticket) + (" " + H("scheduled") if pending and all_ok else "")
                           if all_ok else H("done_fail", t=ticket))
        return end(bool(ran_any or pending) and all_ok, summary())
    except Exception:   # noqa: BLE001 — never raises at the owner
        return {"ok": False, "step": step, "text": H("error"), **({"ticket": ticket} if ticket else {}),
                **({"offer": list(offered)} if offered else {})}
    finally:
        _QUIET.on = was


def _cat(category):
    return {"category": category} if category else {}


# ------------------------------------------------------------------ main
def tty_ask(prompt: str, timeout: int = 20, default: str = "y") -> str:
    try:
        fd = os.open("/dev/tty", os.O_RDWR)
    except OSError:
        return default
    import select
    with os.fdopen(fd, "r+") as tty:
        tty.write(prompt)
        tty.flush()
        ready, _, _ = select.select([tty], [], [], timeout)
        if not ready:
            tty.write("\n")
            return default
        return (tty.readline().strip() or default).lower()


def main(argv=None) -> int:
    global LANG
    ap = argparse.ArgumentParser(prog="rescue", description="Agent J emergency rescue (read-only diagnosis, safe repairs, redacted report)")
    ap.add_argument("--no-fix", action="store_true", help="diagnose and report only")
    ap.add_argument("--no-send", action="store_true", help="keep the report on the Agent J host")
    ap.add_argument("--dry-run", action="store_true", help="show the repairs without running them; nothing is sent")
    ap.add_argument("--yes", "-y", action="store_true", help="send without the 20-second confirmation")
    ap.add_argument("--en", action="store_true", help="English only")
    ap.add_argument("--ticket", help="read the reply to an earlier ticket (st_…)")
    ap.add_argument("--tag", default=os.environ.get("AGENTJ_RESCUE_TAG", ""), help=argparse.SUPPRESS)
    ap.add_argument("--assist", action="store_true", help="live remote help (stage 2, not open yet)")
    a = ap.parse_args(argv)
    LANG = "en" if a.en else "zh"
    if a.tag and not re.fullmatch(r"[A-Za-z0-9 ._-]{1,40}", a.tag):
        ap.error("--tag")
    if a.ticket:
        if not _THREAD.fullmatch(a.ticket):
            ap.error("ticket looks like st_ + 22 characters")
        try:
            rep = wait_reply(a.ticket, 30)
        except Exception as e:   # noqa: BLE001
            say(T(f"读不到回复：{e}", f"cannot read the reply: {e}"))
            return 4
        print_advice(rep) if rep else say(T("还没有回复，稍后再试", "no reply yet; try again later"))
        return 0
    if a.assist:
        say(T("远程协助（主人当场同意、限时、全程记录）是第二阶段，还没开放。先跑一次不带参数的救援，把工单号发给帮你的人。",
              "Live remote help is stage 2 and not open yet. Run the rescue without options and pass on the ticket number."))
        return 2
    say("=" * 64)
    say(T("Agent J 紧急救援", "Agent J emergency rescue"))
    say(T("会做三件事：① 检查这台Agent J 主机 ② 自动修好能修的 ③ 把去掉隐私的报告发给 Agent J Father。",
          "Three steps: (1) check the Agent J host (2) fix what is safe to fix (3) send a redacted report to Agent J Father."))
    say(T("不会读取或发送任何密码、密钥；不删文件；不用管理员权限。随时按 Ctrl+C 退出。",
          "Never reads or sends passwords or keys; deletes nothing; no admin rights. Ctrl+C stops it at any time."))
    say("=" * 64)
    before = diagnose()
    fixes = [] if a.no_fix else repair(before, dry=a.dry_run)
    after = None
    if fixes and not a.dry_run:
        say("\n" + T("复查一遍：", "Checking again:"))
        after = diagnose(with_doctor=False)
    body, attach, healthy = compose(before, fixes, after, a.tag)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    local = DIR / f"report-{stamp}.txt"
    _private_write(local, body + "\n\n" + attach)
    say("\n" + T("【3/3】报告", "[3/3] Report"))
    say("  " + (T("✓ 现在一切正常。手机上的 Agent J 应该在一分钟内恢复。", "✓ Everything looks healthy now; the phone should recover within a minute.")
                if healthy else T("✗ 还有问题没修好，需要 Agent J Father 看一下。", "✗ Something is still broken; Agent J Father needs to look.")))
    say("  " + T(f"本地报告（已去掉隐私）：{local}", f"local copy (redacted): {local}"))
    if a.no_send or a.dry_run:
        return 0 if healthy else 3
    if not a.yes:
        ans = tty_ask("  " + T("把这份报告发给 Agent J Father？[Y/n]（20 秒后自动发送）", "Send it to Agent J Father? [Y/n] (sends in 20 s) ") + " ")
        if ans.startswith("n"):
            say("  " + T("没有发送。可以把上面的本地报告文件用邮件发到 feedback@agentj.app。", "Not sent. You can email the local copy to feedback@agentj.app."))
            return 0 if healthy else 3
    try:
        thread = send(body, attach)
    except Exception as e:   # noqa: BLE001 — network down: the helper still has the local copy and the address
        say("  ✗ " + T(f"报告发不出去（{e}）。请把本地报告文件发到 feedback@agentj.app，或拍下这个屏幕发给帮你的人。",
                       f"could not send ({e}). Email the local copy to feedback@agentj.app, or photograph this screen."))
        return 0 if healthy else 4
    say("\n  " + "★" * 20)
    say("  " + T(f"工单号：{thread}", f"Ticket: {thread}"))
    say("  " + T("请把这个工单号原样发给帮你的人（或 Leo）。", "Send this ticket number to the person helping you."))
    say("  " + "★" * 20)
    say("  " + T("正在等 Agent J Father 的第一条回复（最多 75 秒，可以直接关掉窗口）…", "Waiting up to 75 s for a first reply (you may close the window)…"))
    try:
        rep = wait_reply(thread)
    except Exception:   # noqa: BLE001
        rep = None
    if rep:
        print_advice(rep)
    else:
        say("  " + T(f"回复稍后会到。之后可以运行：curl -fsSL https://agentj.app/rescue | sh -s -- --ticket {thread}",
                     f"The reply comes later. Read it with: curl -fsSL https://agentj.app/rescue | sh -s -- --ticket {thread}"))
    return 0 if healthy else 3


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        say("\n" + T("已停止。", "Stopped."))
        sys.exit(130)
