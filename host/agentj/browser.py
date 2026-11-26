"""Verify and manage local browser runtimes. See P0261 in ARCHITECTURE.md.

CDP is loopback-only and unauthenticated. Each seat has its own profile.
Only explicit actions wake the owned service. Adopted browsers stay owner-managed.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import pathlib
import platform as _platform
import plistlib
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request

from .state import State
from .instance import current as current_instance

START_WAIT = 25                # seconds for a verified browser CDP endpoint
HEADLESS_SIZE = "1920,1080"
LOOPBACK = "127.0.0.1"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


class BrowserError(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason)
        self.reason, self.detail = reason, detail


# ---------------------------------------------------------------- where things live
def bdir(st: State) -> pathlib.Path:
    return st.root / "browser"


def ensure_dir(st: State) -> pathlib.Path:
    d = bdir(st)
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def profile_dir(st: State) -> pathlib.Path:
    """The persistent Linux profile. Once created, the replacement stays active across updates."""
    replacement = bdir(st) / "profile-system"
    return replacement if replacement.is_dir() else bdir(st) / "profile"


def runtime_path(st: State) -> pathlib.Path:
    return bdir(st) / "runtime.json"


def install_path(st: State) -> pathlib.Path:
    return bdir(st) / "install.json"


def _read_json(path: pathlib.Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _write_json(st: State, path: pathlib.Path, obj) -> None:
    ensure_dir(st)
    st.write_private(path, (json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n").encode())


def manifest_digest() -> str:
    """Fingerprint the packaged platform dispatcher for the capability registry."""
    return hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()


# ---------------------------------------------------------------- configuration (preferences, user tier)
def settings(st: State | None = None) -> dict:
    from . import preferences as p
    try:
        cfg = p.effective(st)
    except Exception:  # noqa: BLE001 — a broken override file must not stop the browser from reporting
        cfg = p.defaults()
    return {"enabled": p.get(cfg, "browser.enabled", True) is not False,
            "download_source": p.get(cfg, "browser.download_source", "auto"),
            "mirror": p.get(cfg, "browser.mirror", "") or "",
            "check_time": p.get(cfg, "browser.check_time", "08:00") or "08:00"}


# ---------------------------------------------------------------- the platform
def system() -> str:
    """macos | windows | linux | wsl2 | wsl1 | unsupported."""
    if sys.platform == "darwin":
        return "macos"
    if sys.platform == "win32":
        return "windows"
    if sys.platform.startswith("linux"):
        try:
            rel = pathlib.Path("/proc/sys/kernel/osrelease").read_text().lower()
        except OSError:
            rel = ""
        if "microsoft" in rel:
            return "wsl2" if "wsl2" in rel or "standard" in rel else "wsl1"
        return "linux"
    return "unsupported"


def platform_key() -> str | None:
    m = _platform.machine().lower()
    arch = "arm64" if m in ("arm64", "aarch64") else "x64" if m in ("x86_64", "amd64") else None
    s = system()
    if arch is None or s in ("unsupported", "wsl1"):
        return None
    return {"macos": "mac-", "windows": "win-"}.get(s, "linux-") + arch


def backend() -> str | None:
    target = system()
    if target in ("macos", "windows", "wsl2"):
        return "ego-lite"
    return "system-cdp" if target == "linux" else None


def display_env(environ: dict | None = None) -> bool:
    """A screen this browser can show a window on (Linux / WSLg: DISPLAY or WAYLAND_DISPLAY; macOS: always)."""
    if system() == "macos":
        return True
    e = os.environ if environ is None else environ
    return bool(e.get("DISPLAY") or e.get("WAYLAND_DISPLAY"))


# ---------------------------------------------------------------- Linux system browser and profile migration
SYSTEM_BROWSERS = (
    "/usr/bin/google-chrome-stable", "/usr/bin/google-chrome", "/usr/bin/chromium",
    "/usr/bin/chromium-browser", "/opt/google/chrome/chrome", "/snap/bin/chromium",
)
SYSTEM_BROWSER_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


def _version(value) -> tuple[int, ...]:
    if not isinstance(value, str):
        return ()
    match = re.fullmatch(r"\d{1,5}(?:\.\d{1,8}){3}", value.strip())
    return tuple(map(int, match[0].split("."))) if match else ()


def _system_browser_env(st: State) -> dict:
    keep = {"HOME", "USER", "LOGNAME", "LANG", "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY",
            "XDG_RUNTIME_DIR", "XDG_DATA_HOME", "XDG_SESSION_TYPE", "DBUS_SESSION_BUS_ADDRESS"}
    env = {k: v for k, v in os.environ.items() if k in keep or k.startswith("LC_")}
    env.update(PATH=SYSTEM_BROWSER_PATH, XDG_CONFIG_HOME=str(bdir(st) / "system-config"))
    return env


def _system_browser_version(exe: str, st: State) -> str | None:
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, check=True, timeout=4,
                           env=_system_browser_env(st))
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.match(r"^(?:Google Chrome|Chromium) (\d+(?:\.\d+){3})(?:\s|$)", r.stdout)
    return m[1] if m and _version(m[1]) else None


def installed(st: State) -> dict | None:
    """Find an OS Chrome/Chromium executable without installing or launching a browser window."""
    if backend() != "system-cdp":
        return None
    from .elevate_grant import root_safe
    for exe in SYSTEM_BROWSERS:
        if not os.path.isfile(exe) or not os.access(exe, os.X_OK):
            continue
        real = pathlib.Path(exe).resolve()
        if (not root_safe(exe) or not root_safe(str(real))
                or _testing_binary(str(real)) or bdir(st).resolve() in real.parents):
            continue
        version = _system_browser_version(exe, st)
        if version:
            return {"path": exe, "version": version, "platform": platform_key()}
    return None


def install(st: State) -> dict:
    have = installed(st)
    if have is None:
        raise BrowserError("system_browser_required")
    return {"result": "present", "version": have["version"], "platform": have["platform"]}


def sandbox_fix(st: State) -> dict:
    # The AppArmor exception only applied to the retired downloaded build.
    return {"ok": False, "reason": "sandbox_fix_retired", "hint": HINTS["sandbox_fix_retired"]}


def _legacy_install(st: State) -> dict | None:
    rec = _read_json(install_path(st))
    return rec if isinstance(rec, dict) and _version(rec.get("version")) else None


def _last_profile_version(profile: pathlib.Path) -> tuple[int, ...]:
    with contextlib.suppress(OSError, UnicodeError):
        return _version((profile / "Last Version").read_text())
    return ()


def _launch_profile(st: State, have: dict) -> pathlib.Path:
    profile = profile_dir(st)
    version = _version(have.get("version"))
    last = _last_profile_version(profile)
    legacy = _legacy_install(st)
    if profile.name == "profile" and profile.is_dir() and legacy and not last:
        last = _version(legacy["version"])
    if last and (not version or version < last):
        if profile.name == "profile-system" or not legacy:
            raise BrowserError("profile_version", "system browser is older than the active profile")
        return bdir(st) / "profile-system"
    return profile


def _testing_binary(exe) -> bool:
    if not isinstance(exe, str):
        return False
    parts = pathlib.Path(exe).parts
    return any("chrome for testing" in p.lower() or "chrome-for-testing" in p.lower() or
               p in ("chrome-linux64", "chrome-linux-arm64", "chrome-headless-shell") for p in parts)


# ---------------------------------------------------------------- the process
def proc_start_time(pid: int) -> str | None:
    """A token that changes when the pid is reused (Linux: /proc start ticks; macOS: ps lstart)."""
    if sys.platform.startswith("linux"):
        try:
            raw = pathlib.Path(f"/proc/{pid}/stat").read_text()
            fields = raw.rsplit(")", 1)[1].split()
            return None if fields[0] == "Z" else fields[19]
        except (OSError, IndexError):
            return None
    try:
        r = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def listen_addresses(port: int, pid: int | None = None) -> set[str]:
    """Linux listening addresses, optionally restricted to sockets owned by a specific process."""
    owned = None
    if pid is not None:
        owned = set()
        with contextlib.suppress(OSError):
            for fd in pathlib.Path(f"/proc/{pid}/fd").iterdir():
                with contextlib.suppress(OSError):
                    target = os.readlink(fd)
                    if target.startswith("socket:["):
                        owned.add(target[8:-1])
    out = set()
    for name, v6 in (("tcp", False), ("tcp6", True)):
        try:
            rows = pathlib.Path(f"/proc/net/{name}").read_text().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            f = row.split()
            if len(f) < 10 or f[3] != "0A" or (owned is not None and f[9] not in owned):
                continue
            addr, p = f[1].split(":")
            if int(p, 16) != port:
                continue
            if v6:
                out.add("::1" if addr == "00000000000000000000000001000000" else
                        "::" if addr == "0" * 32 else
                        "127.0.0.1" if addr == "0000000000000000FFFF00000100007F" else addr)
            else:
                out.add(".".join(str(x) for x in bytes.fromhex(addr)[::-1]))
    return out


def loopback_only(addrs: set[str]) -> bool:
    return bool(addrs) and all(a in ("127.0.0.1", "::1", "localhost") for a in addrs)


def _owned_runtime(st: State, rec) -> dict | None:
    """Verify a recorded Linux process, its private profile and the loopback socket it owns."""
    if not isinstance(rec, dict) or type(rec.get("pid")) is not int or type(rec.get("port")) is not int:
        return None
    if not 0 < rec["port"] < 65536:
        return None
    profiles = (bdir(st) / "profile", bdir(st) / "profile-system")
    profile = next((p for p in profiles if rec.get("profile") == str(p)), None)
    if profile is None or profile.is_symlink() or not _recorded_process_alive(rec):
        return None
    pid = rec["pid"]
    try:
        # Chrome may rewrite argv into one process title. Its argument and recorded binary must still match.
        args = pathlib.Path(f"/proc/{pid}/cmdline").read_bytes()
        flag = os.fsencode(f"--user-data-dir={profile}")
        if (flag not in args.split(b"\0") and b" " + flag + b" " not in b" " + args.rstrip(b"\0") + b" "):
            return None
        if rec.get("process_exe") and os.readlink(f"/proc/{pid}/exe") != rec["process_exe"]:
            return None
    except OSError:
        return None
    port = rec["port"]
    if listen_addresses(port) != {LOOPBACK} or LOOPBACK not in listen_addresses(port, pid):
        return None
    info = _endpoint_version(port, timeout=3)
    if not info:
        return None
    ws = info["webSocketDebuggerUrl"]
    if rec.get("ws_path") and ws != f"ws://{LOOPBACK}:{port}{rec['ws_path']}":
        return None
    return {**rec, "ws": ws, "product": info.get("Browser", "")}


def _recorded_process_alive(rec) -> bool:
    return (isinstance(rec, dict) and type(rec.get("pid")) is int and rec["pid"] > 0
            and bool(rec.get("starttime")) and proc_start_time(rec["pid"]) == rec["starttime"])


def runtime(st: State) -> dict | None:
    """Return the verified platform runtime. Only the Linux backend exposes a loopback endpoint."""
    kind = backend()
    if kind == "ego-lite":
        from . import browser_ego
        return browser_ego.runtime(st) if settings(st)["enabled"] else None
    if kind != "system-cdp":
        return None
    ad = adopted(st)
    if ad:
        info = _probe_adopted(ad["port"])
        if not info or _testing_endpoint(st, info) or _agentj_endpoint(info):
            return None
        return {"pid": info.get("pid"), "port": ad["port"], "ws": info["ws"], "product": info["product"], "headless": False,
                "backend": kind, "source": "adopted", "adopted": True, "version": "adopted", "started_at": ad.get("adopted_at")}
    rec = _read_json(runtime_path(st))
    if not isinstance(rec, dict) or rec.get("source") != "system" or _testing_binary(rec.get("exe")):
        return None
    verified = _owned_runtime(st, rec)
    return {**verified, "backend": kind} if verified else None


def _retire_legacy_locked(st: State, rec: dict) -> None:
    """Close only the old Agent J owned browser, using its verified loopback socket to flush the profile."""
    exe = rec.get("exe")
    if (not isinstance(exe, str) or (bdir(st) / "bin").resolve() not in pathlib.Path(exe).resolve().parents
            or login_in_progress(st)):
        raise BrowserError("legacy_browser_open")
    rt = _owned_runtime(st, rec)
    if not rt:
        raise BrowserError("legacy_browser_open")
    from .browser_cdp import CDP, CDPError
    with contextlib.suppress(CDPError):
        with CDP(rt["ws"]) as cdp:
            cdp.call("Browser.close")
    end = time.monotonic() + 8
    while _recorded_process_alive(rec):
        if time.monotonic() >= end:
            raise BrowserError("legacy_browser_open")
        time.sleep(0.1)


WINDOW_NAME = "Agent J 专用浏览器 · Agent J browser"
WINDOW_CLASS = "AgentJBrowser"
BRAND_ARGB = 0xFF827DBD                     # brand "magic carpet" (#827dbd): the window's theme colour
START_HTML = """<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>Agent J 专用浏览器 · Agent J browser</title>
<style>html{background:#827dbd;color:#fff;font:18px/1.5 system-ui,sans-serif}main{max-width:40em;margin:12vh auto;padding:0 1em}
h1{font-size:2em;margin:0 0 .4em}p{margin:.4em 0}</style></head><body><main><h1>Agent J 专用浏览器</h1>
<p>这是 Agent J 自己的浏览器，和你平时用的浏览器分开。Agent J 要用的网站（Gmail、YouTube…）请在这个窗口里登录。</p>
<h1>Agent J browser</h1><p>This is Agent J's own browser, separate from your everyday one. Sign in to the sites Agent J needs
(Gmail, YouTube…) in this window.</p></main></body></html>
"""


def start_page(st: State) -> str:
    """A local page that says, in both languages, which browser this is (its tab title is the window's identity)."""
    ensure_dir(st)
    f = bdir(st) / "start.html"
    if not f.exists() or f.read_text() != START_HTML:
        st.write_private(f, START_HTML.encode())
    return f.as_uri()


def brand_profile(profile: pathlib.Path) -> None:
    """Agent J's OWN profile only (never an adopted one): the brand theme colour, before the browser starts. Best effort."""
    pref = profile / "Default" / "Preferences"
    try:
        data = json.loads(pref.read_text()) if pref.exists() else {}
        if not isinstance(data, dict):
            return
        theme = data.setdefault("browser", {}).setdefault("theme", {})
        if theme.get("user_color") == BRAND_ARGB - (1 << 32):
            return
        theme["user_color"] = BRAND_ARGB - (1 << 32)          # Chrome stores the ARGB as a signed 32-bit int
        theme.setdefault("color_variant", 1)
        pref.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = pref.with_name("Preferences.agentj-tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, pref)
    except (OSError, ValueError):
        pass


def _launch_args(exe: str, profile: pathlib.Path, headless: bool, port: int, start: str = "about:blank") -> list[str]:
    args = [exe, f"--user-data-dir={profile}", f"--remote-debugging-port={port}", f"--remote-debugging-address={LOOPBACK}",
            "--no-first-run", "--no-default-browser-check", "--disable-background-networking-for-update-checks",
            f"--window-size={HEADLESS_SIZE}", f"--window-name={WINDOW_NAME}", f"--class={WINDOW_CLASS}"]
    if headless:
        args += ["--headless=new"]
    if system() in ("linux", "wsl2") and os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("DISPLAY"):
        args += ["--ozone-platform=wayland"]
    return args + [start]


def _launch_port() -> int:
    """Pass an unpredictable nonzero port without enabling Chrome's automatic port mode."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
        reservation.bind((LOOPBACK, 0))
        return reservation.getsockname()[1]


def _wait_port(port: int, proc: subprocess.Popen, wait: float) -> str:
    end = time.monotonic() + wait
    while time.monotonic() < end:
        if proc.poll() is not None:
            raise BrowserError("start_failed", f"browser exited with {proc.returncode}")
        addrs = listen_addresses(port)
        if addrs and addrs != {LOOPBACK}:
            raise BrowserError("not_loopback", ",".join(sorted(addrs)))
        if LOOPBACK in addrs and LOOPBACK in listen_addresses(port, proc.pid):
            info = _endpoint_version(port, timeout=0.5)
            if info:
                return info["webSocketDebuggerUrl"].split(f":{port}", 1)[1]
        time.sleep(0.2)
    raise BrowserError("start_failed", "no verified browser listener")


@contextlib.contextmanager
def start_lock(st: State):
    """One launch transaction per state/profile; never unlink the lock inode. macOS/Linux/WSL2."""
    ensure_dir(st)
    fd = os.open(bdir(st) / "start.lock", os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def start_browser(st: State, headless: bool | None = None) -> tuple[subprocess.Popen | None, dict]:
    """Reuse a verified system browser, or launch it once under the persistent profile lock."""
    if backend() != "system-cdp":
        raise BrowserError("unsupported")
    with start_lock(st):
        rt = runtime(st)
        if rt:
            return None, rt
        have = installed(st)
        if not have:
            raise BrowserError("system_browser_required")
        rec = _read_json(runtime_path(st))
        if _recorded_process_alive(rec):
            if rec.get("source") != "bundled":
                raise BrowserError("start_failed", "existing browser is alive but CDP is unavailable; no second launch")
            _retire_legacy_locked(st, rec)
        for path in (bdir(st) / "profile", bdir(st) / "profile-system"):
            if path.is_symlink():
                raise BrowserError("profile_path")
        prof = _launch_profile(st, have)
        # An older supervisor can leave a profile singleton without a runtime record.
        singleton = prof / "SingletonLock"
        if singleton.is_symlink():
            host, _, pid = os.readlink(singleton).rpartition("-")
            if host == socket.gethostname() and pid.isdigit() and proc_start_time(int(pid)):
                raise BrowserError("start_failed", "profile is already in use; no second launch")
        return _start_browser(st, headless, have=have)


def visible_runtime(st: State) -> dict | None:
    """Show the managed browser for explicit sign-in/open, preserving active QR sessions."""
    from .browser_cdp import CDP, CDPError
    rt = runtime(st)
    if not rt or not rt.get("headless") or adopted(st):
        return rt
    if not display_env() and not _manager_has_display():
        return rt
    with start_lock(st):
        rt = runtime(st)
        if not rt or not rt.get("headless") or login_in_progress(st):
            return rt
        # Close gracefully so Chrome flushes sign-ins before reusing the same profile.
        with contextlib.suppress(CDPError):
            with CDP(rt["ws"]) as cdp:
                cdp.call("Browser.close")
        end = time.monotonic() + 10
        while proc_start_time(rt["pid"]) == rt["starttime"]:
            if time.monotonic() >= end:
                raise BrowserError("start_failed", "headless browser did not close; no second launch")
            time.sleep(0.1)
        _start_browser(st, headless=False)
        return runtime(st)


def _start_browser(st: State, headless: bool | None = None, *, have: dict | None = None) -> tuple[subprocess.Popen, dict]:
    have = have or installed(st)
    if not have:
        raise BrowserError("system_browser_required")
    prof = _launch_profile(st, have)
    if prof.is_symlink():
        raise BrowserError("profile_path")
    prof.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(prof, 0o700)
    if headless is None:
        headless = current_instance() != 1 or not display_env()
    exe, version = have["path"], have.get("version") or "system"
    brand_profile(prof)
    try:
        port = _launch_port()
        with open(bdir(st) / "browser.log", "ab", buffering=0) as log:
            os.chmod(bdir(st) / "browser.log", 0o600)
            proc = subprocess.Popen(_launch_args(exe, prof, headless, port, start_page(st)), stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=log, start_new_session=True, env=_system_browser_env(st))
    except OSError as exc:
        raise BrowserError("start_failed", type(exc).__name__) from None
    try:
        path = _wait_port(port, proc, START_WAIT)
        starttime = proc_start_time(proc.pid)
        if not starttime:
            raise BrowserError("start_failed", "browser process no longer available")
        rec = {"pid": proc.pid, "starttime": starttime, "port": port, "exe": exe, "source": "system",
               "profile": str(prof), "headless": headless, "version": version, "ws_path": path,
               "started_at": int(time.time()), "supervisor": os.getpid()}
        with contextlib.suppress(OSError):
            rec["process_exe"] = os.readlink(f"/proc/{proc.pid}/exe")  # distro launchers may exec a different binary
        _write_json(st, runtime_path(st), rec)
    except (BrowserError, OSError) as exc:
        stop_proc(proc)
        if isinstance(exc, BrowserError):
            raise
        raise BrowserError("start_failed", type(exc).__name__) from None
    st.log("browser_started", headless=headless, source="system", version=version)
    return proc, rec


def stop_proc(proc: subprocess.Popen, wait: float = 8.0) -> None:
    if proc.poll() is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(wait)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(5)


def _manager_has_display() -> bool:
    """Linux: has the user manager been given a display since this supervisor started (graphical login after boot)?"""
    if system() == "macos" or not shutil.which("systemctl"):
        return False
    try:
        r = subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    env = dict(ln.split("=", 1) for ln in r.stdout.splitlines() if "=" in ln)
    if display_env(env):
        for k in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "XDG_SESSION_TYPE", "DBUS_SESSION_BUS_ADDRESS"):
            if env.get(k):
                os.environ[k] = env[k]
        return True
    return False


def run(st: State | None = None) -> int:
    """Observe one browser. Window exit is final; service restarts preserve the existing browser."""
    st = st or State()
    if backend() != "system-cdp" or not settings(st).get("enabled") or adopted(st):
        return 0
    ensure_dir(st)
    stopping = {"now": False}

    def _term(*_):
        stopping["now"] = True
    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)
    try:
        proc, rec = start_browser(st)
        if proc is None:
            return 0
    except BrowserError as e:
        st.log("browser_start_failed", reason=e.reason)
        _write_json(st, bdir(st) / "last-error.json", {"reason": e.reason, "detail": e.detail[:200], "at": int(time.time())})
        return 3
    with contextlib.suppress(FileNotFoundError):
        (bdir(st) / "last-error.json").unlink()
    last_look = time.monotonic()
    try:
        while not stopping["now"]:
            exit_code = proc.poll()
            if exit_code is not None:
                st.log("browser_exited", code=exit_code)
                # A clean window close is the owner's choice. A crash must be visible
                # to the service manager, without silently launching another window.
                return 0 if exit_code == 0 else 3
            if current_instance() == 1 and rec["headless"] and time.monotonic() - last_look > 30:
                last_look = time.monotonic()
                if _manager_has_display() and not login_in_progress(st):
                    st.log("browser_display_arrived")
                    stop_proc(proc)
                    replacement, rec = start_browser(st, headless=False)
                    if replacement is None:
                        return 0
                    proc = replacement
            time.sleep(1)
    finally:
        # SIGTERM from upgrade/reinstall stops only the observer. disable/uninstall
        # explicitly terminate the verified browser via service_stop.
        cur = _read_json(runtime_path(st))
        if not stopping["now"] and isinstance(cur, dict) and cur.get("pid") == proc.pid and cur.get("starttime") == rec.get("starttime"):
            with contextlib.suppress(FileNotFoundError):
                runtime_path(st).unlink()
    return 0


def login_in_progress(st: State) -> bool:
    rec = _read_json(bdir(st) / "login-active.json")
    return isinstance(rec, dict) and rec.get("until", 0) > time.time()


# ---------------------------------------------------------------- the resident service
def svc_name() -> str:
    from . import service
    return service.name() + ("-browser" if service.platform() == "linux" else ".browser")


def unit_text(argv: list[str], environ: dict | None = None) -> str:
    from . import service
    e = os.environ if environ is None else environ
    env = {}
    path = os.pathsep.join(p for p in (e.get("PATH") or "").split(os.pathsep) if p.startswith("/"))
    if path:
        env["PATH"] = path
    for k in ("AGENTJ_STATE_DIR", "AGENTJ_INSTANCE"):   # P180: the instance's own preferences too
        if e.get(k):
            env[k] = e[k]
    q = service._sd_quote
    lines = ["# Agent J browser — written by `agentj browser setup`. Loopback-only; no secrets here, by design.",
             "[Unit]", "Description=Agent J browser (agentj browser run): private profile, CDP on 127.0.0.1 only",
             "After=graphical-session.target", "StartLimitIntervalSec=600", "StartLimitBurst=10", "",
             "[Service]", "Type=simple", "ExecStart=" + " ".join(q(x) for x in argv + ["browser", "run"]),
             "Restart=no", "KillMode=process", "TimeoutStopSec=20", "StandardInput=null"]
    lines += [f"Environment={q(f'{k}={v}')}" for k, v in env.items()]
    lines += ["", "[Install]", "WantedBy=default.target", ""]
    return "\n".join(lines)


def plist_bytes(label: str, argv: list[str], log_path: str, environ: dict | None = None, extra: dict | None = None) -> bytes:
    e = os.environ if environ is None else environ
    env = {k: e[k] for k in ("AGENTJ_STATE_DIR", "AGENTJ_INSTANCE") if e.get(k)}
    return plistlib.dumps({"Label": label, "ProgramArguments": argv + ["browser", "run"], "EnvironmentVariables": env,
                           "RunAtLoad": True, "KeepAlive": False, "AbandonProcessGroup": True, "ThrottleInterval": 10,
                           "LimitLoadToSessionType": "Aqua", "ProcessType": "Interactive",
                           "StandardInPath": "/dev/null", "StandardOutPath": log_path, "StandardErrorPath": log_path, **(extra or {})})


def _remove_owned_supervisor(st: State, name: str, plat: str) -> bool:
    """Remove only a generated browser supervisor for this state. Never touch browser data or signal its PID."""
    from . import service
    from .state_paths import state_dir

    current = str(state_dir())
    instance = os.environ.get("AGENTJ_INSTANCE", "1")
    if plat == "linux":
        path = pathlib.Path(service.unit_dir()) / f"{name}.service"
        if not path.is_file():
            return False
        text = path.read_text()
        if not text.startswith("# Agent J browser — written by `agentj browser setup`.") or '"browser" "run"' not in text:
            return False
        if "AGENTJ_INSTANCE=" in text:
            if service._sd_quote(f"AGENTJ_INSTANCE={instance}") not in text:
                return False
        elif instance != "1":
            return False
        if "AGENTJ_STATE_DIR=" in text:
            if service._sd_quote(f"AGENTJ_STATE_DIR={st.root}") not in text:
                return False
        elif os.environ.get("AGENTJ_STATE_DIR") or str(st.root) != current:
            return False
        service._systemctl("disable", "--now", f"{name}.service", check=True)
        path.unlink()
        service._systemctl("daemon-reload", check=True)
        service._systemctl("reset-failed", f"{name}.service")
        return True

    if plat == "macos":
        path = pathlib.Path.home() / "Library" / "LaunchAgents" / f"{name}.plist"
        if not path.is_file():
            return False
        rec = plistlib.loads(path.read_bytes())
        env = rec.get("EnvironmentVariables", {})
        if rec.get("Label") != name or rec.get("ProgramArguments", [])[-2:] != ["browser", "run"]:
            return False
        if str(env.get("AGENTJ_INSTANCE", "1")) != instance:
            return False
        if env.get("AGENTJ_STATE_DIR") != str(st.root) and (
                env.get("AGENTJ_STATE_DIR") or os.environ.get("AGENTJ_STATE_DIR") or str(st.root) != current):
            return False
        if not service._bootout_wait(name):
            raise BrowserError("no_service_manager", "old browser service did not stop")
        path.unlink()
        return True
    return False


def cleanup_legacy_services(st: State) -> list[str]:
    """Remove the exact former default label if its generated definition belongs to this state."""
    from . import service
    old, plat = service.legacy_name(), service.platform()
    if not old:
        return []
    name = old + ("-browser" if plat == "linux" else ".browser")
    return [name] if _remove_owned_supervisor(st, name, plat) else []


def refresh_service(st: State) -> None:
    """Update the Linux supervisor or retire the old supervisor on an Ego Lite host."""
    from . import service
    kind = backend()
    if not kind:
        return
    legacy = cleanup_legacy_services(st)
    if kind == "ego-lite":
        _remove_owned_supervisor(st, svc_name(), service.platform())
        return
    path = pathlib.Path(service.unit_dir()) / f"{svc_name()}.service"
    if path.is_file() or legacy:
        service_install(st, start=False)


def service_install(st: State, start: bool = True) -> dict:
    from . import service
    plat = service.platform()
    n = svc_name()
    argv = service.agentj_argv()
    cleanup_legacy_services(st)
    if plat == "linux":
        if not shutil.which("systemctl"):
            raise BrowserError("no_service_manager", "no systemd: run `agentj browser run` under your own supervisor")
        if service._systemctl("show-environment", timeout=10).returncode != 0:
            raise BrowserError("no_service_manager", "systemd user manager unavailable (loginctl enable-linger $USER)")
        path = os.path.join(service.unit_dir(), f"{n}.service")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        service._write(path, unit_text(argv).encode(), 0o644)
        service._systemctl("daemon-reload", check=True)
        service._systemctl("enable", f"{n}.service", check=True)
        service._systemctl("reset-failed", f"{n}.service")   # an earlier failed start must not hold the start limit
        if start:
            service._systemctl("restart", f"{n}.service", check=True)
        return {"kind": "systemd", "name": n, "path": path}
    if plat == "macos":
        path = os.path.join(os.path.expanduser("~"), "Library", "LaunchAgents", f"{n}.plist")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        argv, extra = service.macapp.wrap(argv)      # P327: Login Items shows "Agent J" (signed launcher), else as before
        normal = plist_bytes(n, argv, str(bdir(st) / "service.log"), extra=extra)
        quiet = plist_bytes(n, argv, str(bdir(st) / "service.log"), extra={**extra, "RunAtLoad": False})
        service._write(path, normal if start else quiet, 0o644)
        try:
            r = service._bootstrap(n, path)
        finally:
            if not start:
                service._write(path, normal, 0o644)  # launchd loaded quiet policy; next login retains normal start
        if r.returncode != 0:
            raise BrowserError("no_service_manager", (r.stderr or r.stdout or "")[:200])
        return {"kind": "launchd", "name": n, "path": path}
    raise BrowserError("unsupported", plat)


def service_stop(st: State, remove: bool = False) -> dict:
    """Stop (and with remove=True delete) the browser service. The profile always stays."""
    from . import service
    plat = service.platform()
    n = svc_name()
    out = {"name": n}
    with contextlib.suppress(Exception):
        if plat == "linux" and shutil.which("systemctl"):
            service._systemctl("disable", "--now", f"{n}.service")
            if remove:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(os.path.join(service.unit_dir(), f"{n}.service"))
                service._systemctl("daemon-reload")
        elif plat == "macos":
            service._bootout_wait(n)
            if remove:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(os.path.join(os.path.expanduser("~"), "Library", "LaunchAgents", f"{n}.plist"))
    rec = _read_json(runtime_path(st))     # a browser the Agent J host's `run` started (verified pid) also goes
    if isinstance(rec, dict) and type(rec.get("pid")) is int and rec["pid"] > 0 and rec.get("starttime") and proc_start_time(rec["pid"]) == rec["starttime"]:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(rec["pid"], signal.SIGTERM)
    with contextlib.suppress(FileNotFoundError):
        runtime_path(st).unlink()
    return out


def service_status() -> dict:
    from . import service
    plat = service.platform()
    try:
        n = svc_name()
    except Exception:  # noqa: BLE001
        return {"kind": "none", "active": "bad_name"}
    if plat == "linux":
        path = os.path.join(service.unit_dir(), f"{n}.service")
        if not shutil.which("systemctl"):
            return {"kind": "none", "name": n, "installed": os.path.exists(path), "active": "no_systemctl"}
        try:
            act = service._systemctl("is-active", f"{n}.service", timeout=10).stdout.strip() or "unknown"
        except (OSError, subprocess.TimeoutExpired, service.ServiceError):
            act = "unknown"
        return {"kind": "systemd", "name": n, "installed": os.path.exists(path), "active": act}
    if plat == "macos":
        path = os.path.join(os.path.expanduser("~"), "Library", "LaunchAgents", f"{n}.plist")
        try:
            r = service._launchctl("print", f"{service._gui()}/{n}", timeout=10)
            act = "active" if r.returncode == 0 and re.search(r"^\s*state = running", r.stdout, re.M) else "inactive"
        except (OSError, subprocess.TimeoutExpired):
            act = "unknown"
        return {"kind": "launchd", "name": n, "installed": os.path.exists(path), "active": act}
    return {"kind": "none", "active": "unsupported_os"}


def wait_running(st: State, wait: float = 30.0) -> dict | None:
    end = time.monotonic() + wait
    while time.monotonic() < end:
        rt = runtime(st)
        if rt:
            return rt
        time.sleep(0.5)
    return None



# ---------------------------------------------------------------- P131: adopt the owner's existing local CDP browser
ADOPT_PORTS = (9222, 9223, 9229, 9333)          # the usual --remote-debugging-port values people pick
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost")


def adopted_path(st: State) -> pathlib.Path:
    return bdir(st) / "adopted.json"


def offer_path(st: State) -> pathlib.Path:
    return bdir(st) / "adopt-offer.json"


def parse_endpoint(url) -> int:
    """http://127.0.0.1:<port> (or localhost) → port. Anything that is not the Agent J host's loopback is refused."""
    from urllib.parse import urlsplit
    if not isinstance(url, str) or len(url) > 200:
        raise BrowserError("shape", "--endpoint http://127.0.0.1:<port>")
    try:
        u = urlsplit(url if "://" in url else "http://" + url)
    except ValueError:
        raise BrowserError("shape", "--endpoint http://127.0.0.1:<port>") from None
    if u.scheme not in ("http", "ws") or (u.hostname or "") not in _LOOPBACK_HOSTS or u.username or u.password:
        raise BrowserError("not_loopback", "only http://127.0.0.1:<port> on the Agent J host can be adopted")
    try:
        port = u.port
    except ValueError:
        port = None
    if not port or not 1 <= port <= 65535:
        raise BrowserError("shape", "--endpoint http://127.0.0.1:<port>")
    return port


def _pid_of_port(port: int) -> int | None:
    """The process (of this OS user) listening on 127.0.0.1:<port> — Linux /proc only; used to raise its window."""
    if not sys.platform.startswith("linux"):
        return None
    inodes = set()
    for name in ("tcp", "tcp6"):
        with contextlib.suppress(OSError):
            for row in pathlib.Path(f"/proc/net/{name}").read_text().splitlines()[1:]:
                f = row.split()
                if len(f) > 9 and f[3] == "0A" and int(f[1].split(":")[1], 16) == port:
                    inodes.add(f"socket:[{f[9]}]")
    if not inodes:
        return None
    uid = os.getuid()
    for d in pathlib.Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        with contextlib.suppress(OSError):
            if d.stat().st_uid != uid:
                continue
            for fd in (d / "fd").iterdir():
                with contextlib.suppress(OSError):
                    if os.readlink(fd) in inodes:
                        return int(d.name)
    return None


def _endpoint_version(port: int, timeout: float) -> dict | None:
    try:
        with _LOCAL.open(f"http://{LOOPBACK}:{port}/json/version", timeout=timeout) as r:
            info = json.loads(r.read(65536))
    except (OSError, ValueError):
        return None
    ws = info.get("webSocketDebuggerUrl") if isinstance(info, dict) else None
    if isinstance(ws, str) and ws.startswith(f"ws://{LOOPBACK}:{port}/devtools/browser/"):
        return info
    return None


def probe_endpoint(port: int, timeout: float = 2.0) -> dict | None:
    """Read only /json/version and local socket metadata to discover a Linux browser."""
    port = int(port)
    info = _endpoint_version(port, timeout)
    if not info or not loopback_only(listen_addresses(port)):
        return None
    pid = _pid_of_port(port)
    if not pid:
        return None
    return {"port": port, "ws": info["webSocketDebuggerUrl"], "product": str(info.get("Browser", ""))[:80], "pid": pid}


def _probe_adopted(port: int) -> dict | None:
    """The registered browser: one slow /json/version (it is busy with someone else's work) is not "not running" — read twice."""
    return probe_endpoint(port) or (time.sleep(0.5), probe_endpoint(port, timeout=3.0))[1]


def _testing_endpoint(st: State, info: dict) -> bool:
    if "for testing" in info.get("product", "").lower():
        return True
    pid = info.get("pid")
    if not pid:
        return False
    with contextlib.suppress(OSError):
        path = os.readlink(f"/proc/{pid}/exe")
        return _testing_binary(path) or (bdir(st) / "bin").resolve() in pathlib.Path(path).resolve().parents
    return False


def _agentj_endpoint(info: dict) -> bool:
    pid = info.get("pid")
    if not pid:
        return False
    with contextlib.suppress(OSError):
        args = pathlib.Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ")
        return b" --class=AgentJBrowser " in b" " + args + b" "
    return False


def detect_existing(st: State, ports=None) -> list[dict]:
    """Read-only detection of other local CDP browsers. Opt out with AGENTJ_BROWSER_DETECT=off."""
    if backend() != "system-cdp" or os.environ.get("AGENTJ_BROWSER_DETECT") == "off":
        return []
    own = _read_json(runtime_path(st))
    own_port = own.get("port") if isinstance(own, dict) else None
    out = []
    for port in ADOPT_PORTS if ports is None else ports:
        if port == own_port:
            continue
        info = probe_endpoint(port, timeout=1.0)
        if info and not _testing_endpoint(st, info) and not _agentj_endpoint(info):
            out.append({k: info[k] for k in ("port", "product")})
    return out


def adopted(st: State) -> dict | None:
    if backend() != "system-cdp":
        return None
    rec = _read_json(adopted_path(st))
    if isinstance(rec, dict) and type(rec.get("port")) is int and 1 <= rec["port"] <= 65535:
        return rec
    return None


def offer(st: State) -> dict | None:
    """Read the pending adoption decision."""
    rec = _read_json(offer_path(st))
    if isinstance(rec, dict) and type(rec.get("port")) is int:
        return rec
    return None


def _set_offer(st: State, port: int, product: str, by: str) -> dict:
    rec = {"port": port, "product": product, "by": by, "at": int(time.time()), "declined": False}
    _write_json(st, offer_path(st), rec)
    return rec


def adopt(st: State, endpoint, dry_run: bool = False, sites=None, _cdp=None) -> dict:
    """Propose a loopback browser for signed phone confirmation. Dry runs only probe."""
    if backend() != "system-cdp":
        return {"ok": False, "reason": "adopt_unsupported", "hint": HINTS["adopt_unsupported"]}
    try:
        port = parse_endpoint(endpoint)
    except BrowserError as e:
        return {"ok": False, "reason": e.reason, "detail": e.detail}
    info = probe_endpoint(port)
    if not info:
        return {"ok": False, "reason": "adopt_not_running", "port": port, "hint": HINTS["adopt_not_running"]}
    if _testing_endpoint(st, info):
        return {"ok": False, "reason": "adopt_testing_browser", "hint": HINTS["adopt_testing_browser"]}
    if _agentj_endpoint(info):
        return {"ok": False, "reason": "adopt_agentj_browser", "hint": HINTS["adopt_agentj_browser"]}
    out = {"ok": True, "port": port, "product": info["product"], "loopback_only": True}
    if dry_run:
        out["result"] = "would_adopt"
        if sites:
            from . import browser_sites as bs
            out["sites"] = bs.dry_check(info["ws"], sites, _cdp=_cdp)
        return out
    cur = adopted(st)
    if cur and cur["port"] == port:
        return {**out, "result": "already"}
    _set_offer(st, port, info["product"], "cli")
    st.log("browser_adopt_offer", port=port)
    return {**out, "result": "confirm_on_phone",
            "note": "主人在手机的首次准备清单里点「用它」后生效 / takes effect when the owner taps \"Use it\" in the phone checklist"}


def confirm_adopt(st: State, decision_ref: str) -> dict:
    """Re-probe after signed adoption. Stop only the owned service and preserve its profile."""
    if backend() == "ego-lite":
        from . import browser_ego, first_run
        with start_lock(st):
            path = _ego_offer_path(st)
            pending = _ego_offer(st)
            if not decision_ref or not pending:
                return {"ok": False, "reason": "no_offer"}
            profile, reason = browser_ego.resolve_profile(st, "Agent J")
            if profile != pending["profile"]:
                return {"ok": False, "reason": reason or "browser_profile_changed"}
            with first_run._lock(st):
                rec = first_run.read(st) or {}
                row = rec.get("items", {}).get("browser") or {}
                action = rec.get("action") or {}
                if (row.get("owner_decision_ref") != decision_ref[:64] or action.get("state") != "running"
                        or action.get("item") != "browser" or action.get("choice") != "adopt_browser"
                        or action.get("ego_offer") != pending["id"]):
                    return {"ok": False, "reason": "no_offer"}
                browser_ego.bind_profile(st, profile)
                path.unlink(missing_ok=True)
            st.log("browser_ego_connected")
            return {"ok": True, "result": "connected", "backend": "ego-lite"}
    if backend() != "system-cdp":
        return {"ok": False, "reason": "adopt_unsupported", "hint": HINTS["adopt_unsupported"]}
    o = offer(st)
    if not o or o.get("declined"):
        found = detect_existing(st)
        if not found:
            return {"ok": False, "reason": "no_offer"}
        o = _set_offer(st, found[0]["port"], found[0]["product"], "checklist")
    info = probe_endpoint(o["port"])
    if not info:
        return {"ok": False, "reason": "adopt_not_running", "port": o["port"]}
    if _testing_endpoint(st, info):
        return {"ok": False, "reason": "adopt_testing_browser", "hint": HINTS["adopt_testing_browser"]}
    if _agentj_endpoint(info):
        return {"ok": False, "reason": "adopt_agentj_browser", "hint": HINTS["adopt_agentj_browser"]}
    if profile_dir(st).is_dir() or service_status().get("installed"):
        with contextlib.suppress(Exception):
            service_stop(st)
    _write_json(st, adopted_path(st), {"port": o["port"], "product": info["product"], "adopted_at": int(time.time()),
                                       "decision_ref": str(decision_ref)[:64]})
    with contextlib.suppress(FileNotFoundError):
        offer_path(st).unlink()
    with contextlib.suppress(FileNotFoundError):
        (bdir(st) / "last-error.json").unlink()
    st.log("browser_adopted", port=o["port"])
    return {"ok": True, "result": "adopted", "port": o["port"]}


def decline_offer(st: State) -> None:
    o = offer(st)
    if o:
        _write_json(st, offer_path(st), {**o, "declined": True})


def forget_adopted(st: State) -> dict:
    """Stop using the adopted browser (it keeps running; nothing in it is touched)."""
    rec = adopted(st)
    with contextlib.suppress(FileNotFoundError):
        adopted_path(st).unlink()
    if rec:
        st.log("browser_adopt_forget", port=rec["port"])
    return {"ok": True, "result": "forgotten" if rec else "not_adopted"}


def _ego_offer_path(st: State) -> pathlib.Path:
    return bdir(st) / "ego-profile-offer.json"


def _ego_offer(st: State) -> dict | None:
    rec = _read_json(_ego_offer_path(st))
    if isinstance(rec, dict) and isinstance(rec.get("profile"), str) and isinstance(rec.get("id"), str):
        return rec
    return None


def propose_ego(st: State) -> dict:
    from . import browser_ego
    if backend() != "ego-lite":
        return {"ok": False, "reason": "unsupported"}
    if not settings(st)["enabled"]:
        return {"ok": False, "reason": "disabled"}
    with start_lock(st):
        if runtime(st):
            return {"ok": True, "result": "already"}
        profile, reason = browser_ego.resolve_profile(st, "Agent J")
        if not profile:
            return {"ok": False, "reason": reason, "hint": browser_ego.HINTS.get(reason, "")}
        offer = _ego_offer(st)
        if not offer or offer["profile"] != profile:
            _write_json(st, _ego_offer_path(st), {"profile": profile, "id": secrets.token_hex(16)})
    return {
        "ok": True, "result": "confirm_on_phone",
        "note": "请在已配对手机的首次准备清单中确认使用 Ego Lite 的 Agent J 浏览器资料。 / "
                "Confirm the Agent J profile in Ego Lite from the setup checklist on your paired phone.",
    }


def source(st: State) -> dict:
    """What the checklist shows: whose browser Agent J uses. kind: adopted | own | offer | none."""
    kind = backend()
    if kind == "ego-lite":
        pending = _ego_offer(st)
        if pending:
            return {"kind": "offer", "backend": kind, "ego_offer": pending["id"]}
        return {"kind": "own" if runtime(st) else "none", "backend": kind}
    if kind != "system-cdp":
        return {"kind": "none"}
    ad = adopted(st)
    if ad:
        return {"kind": "adopted", "port": ad["port"], "running": bool(runtime(st))}
    o = offer(st)
    if profile_dir(st).is_dir() and installed(st):
        return {"kind": "own"}
    if o and not o.get("declined"):
        return {"kind": "offer", "port": o["port"]}
    if not o:
        found = detect_existing(st)
        if found:
            return {"kind": "offer", "port": found[0]["port"]}
    return {"kind": "none"}


# ---------------------------------------------------------------- P131: bring the window to the front
def raise_window(pid: int | None, title: str | None = WINDOW_NAME) -> dict:
    """Raise a window on supported desktops, reporting when that is unavailable."""
    s = system()

    def run_(argv):
        try:
            return subprocess.run(argv, capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False
    if s == "macos":
        if pid and run_(["osascript", "-e", f'tell application "System Events" to set frontmost of (first process whose unix id is {int(pid)}) to true']):
            return {"ok": True, "method": "macos"}
        return {"ok": False, "method": "macos"}
    if s != "linux":
        return {"ok": False, "method": "unsupported"}
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
        for sel in ([f"pid:{int(pid)}"] if pid else []) + ([f"class:^{WINDOW_CLASS}$"] if title == WINDOW_NAME else []):
            # Hyprland ≥ 0.55 (Lua config): hl.dsp.focus({window=…}); older: the `focuswindow` dispatcher
            for argv in (["hyprctl", "dispatch", f'hl.dsp.focus({{ window = "{sel}" }})'], ["hyprctl", "dispatch", "focuswindow", sel]):
                try:
                    p = subprocess.run(argv, capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL)
                except (OSError, subprocess.SubprocessError):
                    continue
                if p.returncode == 0 and (p.stdout or "").strip().lower() == "ok":
                    return {"ok": True, "method": "hyprland"}
        return {"ok": False, "method": "hyprland"}
    if os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        if pid and shutil.which("xdotool") and run_(["xdotool", "search", "--onlyvisible", "--pid", str(int(pid)), "windowactivate"]):
            return {"ok": True, "method": "xdotool"}
        if title and shutil.which("wmctrl") and run_(["wmctrl", "-a", title]):
            return {"ok": True, "method": "wmctrl"}
        return {"ok": False, "method": "x11"}
    return {"ok": False, "method": "unsupported"}


# ---------------------------------------------------------------- the commands' logic (shared by CLI and serve)
def setup(st: State, start_service: bool = True, own: bool = False) -> dict:
    """Check the selected backend and prepare Agent J's private Linux browser if requested."""
    if not settings(st)["enabled"]:
        return {"ok": True, "result": "disabled", "note": "browser.enabled=false — `agentj browser enable` turns it back on"}
    kind = backend()
    if kind == "ego-lite":
        from . import browser_ego
        return browser_ego.setup(st)
    if kind != "system-cdp":
        return {"ok": False, "result": "attention", "reason": "unsupported", "hint": HINTS["unsupported"]}
    ad = adopted(st)
    if ad:
        running = bool(runtime(st))
        return {"ok": running, "result": "adopted", "port": ad["port"], **({} if running else {
            "reason": "adopt_not_running", "hint": HINTS["adopt_not_running"]})}
    if own:
        decline_offer(st)
    elif not profile_dir(st).is_dir():
        o = offer(st)
        found = [] if o and o.get("declined") else detect_existing(st)
        if found:
            _set_offer(st, found[0]["port"], found[0]["product"], "setup")
            return {"ok": False, "result": "existing_found", "port": found[0]["port"], "product": found[0]["product"],
                    "hint": HINTS["existing_found"].format(port=found[0]["port"])}
    out = {"ok": False, "result": "attention", "backend": kind}
    try:
        out["install"] = install(st)
    except BrowserError as e:
        st.log("browser_setup_failed", reason=e.reason)
        _write_json(st, bdir(st) / "last-error.json", {"reason": e.reason, "detail": e.detail[:200], "at": int(time.time())})
        return {**out, "reason": e.reason, "detail": e.detail, "hint": HINTS.get(e.reason, "")}
    out["source"] = "system"
    if not start_service:
        return {**out, "ok": True, "result": "installed"}
    try:
        out["service"] = service_install(st)
    except (BrowserError, Exception) as e:  # noqa: BLE001 — the service manager's own failure kinds
        reason = getattr(e, "reason", "no_service_manager")
        st.log("browser_service_failed", reason=reason)
        return {**out, "reason": reason, "detail": getattr(e, "detail", "")[:200],
                "hint": HINTS.get(reason) or HINTS["no_service_manager"]}
    rt = wait_running(st, START_WAIT + 10)
    if not rt:
        err = _read_json(bdir(st) / "last-error.json") or {}
        return {**out, "reason": err.get("reason", "start_failed"), "hint": HINTS.get(err.get("reason", "start_failed"), "")}
    with contextlib.suppress(FileNotFoundError):
        (bdir(st) / "last-error.json").unlink()
    return {**out, "ok": True, "result": "ready", "headless": rt.get("headless"), "version": rt.get("version")}


def status(st: State) -> dict:
    kind = backend()
    if kind == "ego-lite":
        from . import browser_ego
        return browser_ego.status(st)
    cfg = settings(st)
    have = installed(st)
    rt = runtime(st) if cfg["enabled"] else None
    svc = service_status()
    err = _read_json(bdir(st) / "last-error.json")
    attention = []
    if cfg["enabled"]:
        if not kind:
            attention.append("unsupported")
        elif not have:
            attention.append("system_browser_required")
        elif not rt:
            why = err.get("reason") if isinstance(err, dict) else None
            attention.append(why if why in HINTS else "not_running")
    ad = adopted(st)
    if ad and cfg["enabled"]:
        attention = [] if rt else ["adopt_not_running"]
    state = "disabled" if not cfg["enabled"] else "ready" if rt else "attention"
    out = {"ok": state != "attention", "state": state, "enabled": cfg["enabled"], "system": system(),
           "platform": platform_key(), "backend": kind, "installed": {"version": have["version"], "platform": have["platform"]} if have else None,
           "service": svc, "attention": attention, "hints": [HINTS[a] for a in attention if a in HINTS],
           "source": source(st)}
    if rt:
        out["runtime"] = {"pid": rt["pid"], "headless": rt.get("headless"), "loopback_only": True, "source": rt.get("source", "system"),
                          "version": rt.get("version"), "started_at": rt.get("started_at"), "host_login": not rt.get("headless")}
    return out


def endpoint(st: State) -> dict:
    """The local CDP HTTP endpoint for tools on the Agent J host (Playwright connect_over_cdp). Never send it anywhere."""
    if not settings(st)["enabled"]:
        return {"ok": False, "reason": "disabled", "hint": "agentj browser enable"}
    if backend() == "ego-lite":
        return {"ok": False, "reason": "ego_browser_cli", "backend": "ego-lite", "hint": HINTS["ego_browser_cli"]}
    rt = runtime(st)
    if not rt:
        return {"ok": False, "reason": "not_running", "hint": HINTS["not_running"]}
    return {"ok": True, "endpoint": f"http://{LOOPBACK}:{rt['port']}", "note": ENDPOINT_NOTE}


def set_enabled(st: State, on: bool) -> dict:
    res = _set_pref(st, "browser.enabled", on)
    if not res.get("ok", True):
        return {"ok": False, "reason": "config", "detail": str(res.get("error") or res)[:200]}
    if not on:
        if backend() == "ego-lite":
            return {"ok": True, "enabled": False, "note": "Agent J stopped using Ego Lite; browser and sign-ins kept"}
        service_stop(st)
        return {"ok": True, "enabled": False, "note": "service stopped; profile and sign-ins kept"}
    return {**setup(st), "enabled": True}


def _set_pref(st: State, key: str, value) -> dict:
    """Edit the user override with the same validation and history as `agentj config set`."""
    import fcntl
    from . import preferences as p
    st.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(st.root / "preferences.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        raw, _ = p.read()
        newraw = p.edit(raw, key, value)
        p.parse(newraw)
        return p.transact(st, newraw)
    except p.ConfigError as e:
        return {"ok": False, "error": e.result() if hasattr(e, "result") else str(e)}
    finally:
        os.close(fd)


ENDPOINT_NOTE = ("Local only: connect with Playwright `chromium.connect_over_cdp(endpoint)` on the Agent J host. Never send it to "
                 "chat, the cloud or another machine; open your own tabs and close only the tabs you opened. / 只在本机使用。")

HINTS = {
    "unsupported": "专用浏览器支持 macOS、Linux 和 Windows（Agent J 运行在 WSL2 中）/ supported on macOS, Linux, and Windows (Agent J in WSL2)",
    "system_browser_required": "先通过系统安装 Chrome 或 Chromium，再运行 agentj browser setup / install Chrome or Chromium for your OS, then run agentj browser setup",
    "sandbox_fix_retired": "请使用系统安装的 Chrome 或 Chromium，再运行 agentj browser setup / use OS-packaged Chrome or Chromium, then run agentj browser setup",
    "no_service_manager": "没有可用的 systemd 用户服务管理器 / no systemd user service manager",
    "not_running": "浏览器没在运行，请运行 agentj browser setup / browser is not running, run agentj browser setup",
    "start_failed": "浏览器启动失败，请运行 agentj browser status 和 agentj doctor / the browser did not start, see agentj browser status and agentj doctor",
    "not_loopback": "浏览器的调试端口未通过本机监听检查，已停止 / the browser failed the loopback listener check and was stopped",
    "legacy_browser_open": "请先完成当前登录并关闭旧版 Agent J 浏览器，再运行 agentj browser setup / finish any sign-in and close the old Agent J browser, then run agentj browser setup",
    "profile_version": "请把系统浏览器更新到能打开现有资料的版本，再运行 agentj browser setup / update your system browser to use your existing profile, then run agentj browser setup",
    "profile_path": "浏览器资料目录无法安全使用，请检查 Agent J 的资料目录 / the browser profile directory could not be used safely",
    "ego_browser_cli": "Ego Lite 请用 agentj browser open、check 或 login。 / Use agentj browser open, check or login with Ego Lite.",
    "adopt_unsupported": "这台主机使用 Ego Lite。可以请 Agent 代装，再由 Agent 运行 agentj browser connect。 / "
                         "This host uses Ego Lite. Ask your Agent to install it and run agentj browser connect.",
    "adopt_agentj_browser": "这是另一个 Agent J 实例的专用浏览器。/ This browser belongs to another Agent J seat.",
    "adopt_testing_browser": "请选择系统安装的 Chrome 或 Chromium，再登记。/ Register an OS-installed Chrome or Chromium.",
    "adopt_not_running": "你登记的浏览器没在运行：请在Agent J 主机上把它打开（Agent J 不会替你启动它）/ the browser you registered is not running; start it on the Agent J host",
    "existing_found": "发现你已有一个浏览器（端口 {port}），要直接用它吗？主人在手机清单里点「用它」；要另装专用的：agentj browser setup --own / "
                      "found a browser you already run (port {port}); use it from the phone checklist, or set up a dedicated one with agentj browser setup --own",
    "shape": "用法：agentj browser adopt --endpoint http://127.0.0.1:<端口> / usage: --endpoint http://127.0.0.1:<port>",
}


# ---------------------------------------------------------------- P418 (#183 ⑤): start the browser again when it is needed
CDP_ACTIONS = ("open", "check", "endpoint")      # the actions that talk to the browser itself
WAKE_WAIT = 20.0                                 # seconds for a verified runtime after the start
RESTARTED_NOTE = "浏览器刚才没开，已重新打开。/ The browser was closed; Agent J opened it again."
ADOPT_DOWN = ("你的 Chrome 现在没开（或没开调试端口）：请在 Agent J 主机上打开它；或者改用 Agent J 自己的浏览器：agentj browser adopt --forget，"
              "再运行 agentj browser setup --own / Your Chrome is not open (or has no debugging port): open it on the computer, "
              "or switch to Agent J's own browser: agentj browser adopt --forget, then agentj browser setup --own")
WAKE_FAILED = ("没能重新打开浏览器：运行 agentj browser status 看原因，再运行 agentj browser setup / Could not open the browser "
               "again: see agentj browser status, then run agentj browser setup")


def service_start() -> bool:
    """Start Agent J's own browser service once — only an installed one; never enable, install or restart anything."""
    from . import service
    try:
        n = svc_name()
        plat = service.platform()
        if plat == "linux":
            if not os.path.exists(os.path.join(service.unit_dir(), f"{n}.service")) or not shutil.which("systemctl"):
                return False
            service._systemctl("reset-failed", f"{n}.service", timeout=10)   # a closed window may have hit the start limit
            return service._systemctl("start", f"{n}.service", timeout=15).returncode == 0
        if plat == "macos":
            path = os.path.join(os.path.expanduser("~"), "Library", "LaunchAgents", f"{n}.plist")
            if not os.path.exists(path):
                return False
            if service._launchctl("kickstart", f"{service._gui()}/{n}", timeout=15).returncode == 0:
                return True
            if service._bootstrap(n, path).returncode != 0:
                return False
            return service._launchctl("kickstart", f"{service._gui()}/{n}", timeout=15).returncode == 0
    except Exception:  # noqa: BLE001 — the service manager's own failure kinds: the action reports its own error
        return False
    return False


def wake(st: State) -> dict | None:
    """Wake the managed browser for an action. Return a restart or failure receipt when attempted."""
    if not settings(st).get("enabled"):
        return None
    if backend() == "ego-lite":
        from . import browser_ego
        return browser_ego.wake(st)
    ad = adopted(st)
    if ad:
        if runtime(st):
            return None
        st.log("browser_wake", result="adopted_down")
        return {"reason": "adopt_not_running", "next": ADOPT_DOWN}     # the owner's browser: never started by Agent J
    if runtime(st):
        return None
    if backend() == "system-cdp" and not installed(st):
        return {"reason": "system_browser_required", "next": HINTS["system_browser_required"]}
    if backend() != "system-cdp":
        return None
    rt = wait_running(st, WAKE_WAIT) if service_start() else None
    st.log("browser_wake", result="restarted" if rt else "failed")
    return {"restarted": True} if rt else {"reason": "wake_failed", "next": WAKE_FAILED}


def _woke(res: dict, w: dict | None) -> dict:
    if not w:
        return res
    if w.get("restarted"):
        return {**res, "restarted": True, "restarted_note": RESTARTED_NOTE}
    out = {**res, "next": w["next"]}
    if w["reason"] == "adopt_not_running" and out.get("reason") == "not_running":
        out.update(reason="adopt_not_running", hint=ADOPT_DOWN)
    return out


MIGRATION_NOTICE = {
    "ego-lite": ("Agent J 的专用浏览器改用 Ego Lite。旧资料还在。可以请 Agent 代装并引导导入资料。"
                 "网站仍要求登录时，请在 Ego Lite 里登录。 / Agent J now uses Ego Lite. Your old browser data stays on this computer. "
                 "Ask your Agent to help with installation and import, and sign in again if a site asks."),
    "system-cdp": ("Agent J 的专用浏览器改用系统 Chrome 或 Chromium。旧资料还在。系统浏览器能读取时会沿用原资料，"
                   "仍要求登录的网站请在新浏览器里登录。 / Agent J now uses system Chrome or Chromium. Your old browser data stays on "
                   "this computer. Agent J uses it when the system browser can open it. Sign in again if a site asks."),
}


def _migration_notice(st: State) -> str | None:
    kind = backend()
    if kind not in MIGRATION_NOTICE or not _legacy_install(st) or not settings(st)["enabled"]:
        return None
    marker = bdir(st) / "migration-notice.json"
    with start_lock(st):
        if _read_json(marker) == {"backend": kind}:
            return None
        _write_json(st, marker, {"backend": kind})
    return MIGRATION_NOTICE[kind]


def execute(req: dict, st: State | None = None) -> dict:
    """The host side of `agentj browser …` (serve's elevate.sock, or directly when serve is not running)."""
    st = st or State()
    a = req.get("action")
    w = wake(st) if a in CDP_ACTIONS and not (a == "check" and req.get("if_stale")) else None        # P418 (#183 ⑤)
    result = _woke(_execute(a, req, st), w)
    if a == "check" and not req.get("if_stale") and result.get("ok"):
        note = _migration_notice(st)
        if note:
            return {**result, "migration_note": note}
    return result


def _execute(a, req: dict, st: State) -> dict:
    from . import browser_sites as bs
    if a == "status":
        out = status(st)
        out["sites"] = bs.summary(st)
        if req.get("registry"):
            out["registry"] = bs.registry(st)
        return out
    if a == "setup":
        return setup(st, own=req.get("own") is True)
    if a == "connect":
        return propose_ego(st)
    if a == "adopt":
        if req.get("forget") is True:
            return forget_adopted(st)
        sites = req.get("sites") if isinstance(req.get("sites"), list) else None
        return adopt(st, req.get("endpoint"), dry_run=req.get("dry_run") is True, sites=sites)
    if a == "enable":
        return set_enabled(st, True)
    if a == "disable":
        return set_enabled(st, False)
    if a == "endpoint":
        return endpoint(st)
    if a == "open":
        return bs.open_url(st, req.get("url"))
    if a == "check":
        return bs.check(st, req.get("sites"), if_stale=bool(req.get("if_stale")))
    if a == "sites_list":
        return {"ok": True, "sites": bs.summary(st), "templates": bs.template_list(st)}
    if a == "sites_add":
        return bs.add(st, req.get("site"), req.get("origin"), req.get("url"), req.get("title"))
    if a == "sites_remove":
        return bs.remove(st, req.get("site"))
    if a == "sandbox_fix":
        return sandbox_fix(st)
    if a == "telegram_qr":
        return bs.telegram_qr(st, req.get("value"))
    return {"ok": False, "reason": "shape", "detail": "unknown browser action"}


# ---------------------------------------------------------------- the CLI (runs as the Agent or the owner)
def _ask(req: dict, local_ok: bool = True) -> dict:
    """Through serve (the browser state is outside the Agent fence); without a running serve, directly."""
    from . import elevate
    st = State()
    res = elevate.client_request(st, {"t": "browser", **req})
    if res.get("result") == "unavailable" and local_ok:
        return execute(req, st)
    return res


CHANNELS_OWNER_HINT = "请在原已配对手机的主对话里重新发「恢复视频号登录」，让主 Agent 在这一轮单独运行 agentj browser login channels-weixin --wait --json。"


LOGIN_MSG = {
    "sent": "已发登录二维码卡到主人的已配对手机（{ttl} 秒内有效）。请主人在手机上扫码；成功由浏览器自己确认，不要相信「扫好了」这句话。"
            "/ A sign-in QR card is on the owner's paired phone ({ttl} s). Success is confirmed by the browser itself.",
    "unconfirmed": "登录卡的数据已发出，尚未确认手机显示。若没看到，请在原已配对手机的主对话里发「恢复视频号登录」来获取新码。"
                   " / Login card sent; display on the paired phone is unconfirmed. If it is missing, request a fresh code in the original paired phone chat.",
    "shown": "已配对手机确认显示二维码。扫码后由专用浏览器确认登录。 / The paired phone confirmed display. The dedicated browser checks sign-in after scanning.",
    "done": "已登录，体检已确认。/ Signed in; confirmed by a check.",
    "already": "这个网站已经是登录状态。/ Already signed in.",
    "host_login": "这个网站不支持扫码：已在这台Agent J 主机的 Agent J 浏览器打开登录页，请主人在Agent J 主机前登录，然后运行 agentj browser check --site {site}。"
                  "/ No QR for this site: the sign-in page is open on the Agent J host; the owner signs in there.",
    "host_login_unavailable": "这台Agent J 主机没有屏幕，这个网站又不支持扫码：请在有屏幕的Agent J 主机上登录，或换支持扫码的网站。/ No screen and no QR sign-in.",
    "no_qr": "Agent J 未取得可显示的登录二维码。/ Agent J could not obtain a sign-in QR code to display.",
    "browser_unavailable": "Agent J 未能连接专用浏览器。/ Agent J could not reach its browser.",
    "page_not_ready": "Agent J 未能确认登录页已加载。/ Agent J could not confirm the sign-in page was ready.",
    "qr_not_found": "登录页已加载，Agent J 未识别到可用的二维码。/ The sign-in page loaded, but Agent J could not identify a usable QR code.",
    "capture_timeout": "等待取得稳定的二维码图像超时。/ Timed out waiting for a stable QR image.",
    "capture_blank": "找到了二维码，但未取得可用图像。请点亮主机屏幕，在原已配对手机的主对话里发「恢复视频号登录」。"
                     " / The browser found a QR code without a usable image. Wake the host display, then request a fresh sign-in code from the original paired phone chat.",
    "not_delivered": "未能将登录卡发给原手机。/ The sign-in card could not be sent to the requesting phone.",
    "owner_phone_required": CHANNELS_OWNER_HINT,
    "expired": "二维码已过期，浏览器未确认登录。可从原已配对手机的主对话申请新码。/ The code expired before the browser could confirm sign-in. Request a fresh code from the original paired phone chat.",
    "cancelled": "主人在手机上关掉了登录卡。不要追问。/ The owner closed the card.",
    "no_device": "没有已配对的手机。/ No paired phone.",
    "no_browser": "Agent J 的浏览器没在运行：agentj browser setup。/ The browser is not running.",
    "disabled": "主人关闭了浏览器（browser.enabled=false）。/ The owner turned the browser off.",
    "stopped": "已急停。/ Everything is stopped.",
    "busy": "已经有太多张登录卡。/ Too many open sign-in cards.",
    "gone": "Agent J 重启了，卡片作废；需要的话重新发。/ Agent J restarted; the card is void.",
    "unavailable": "Agent J 没在运行（agentj service status）。/ Agent J is not running.",
}


def cmd(a) -> int:
    act = a.bcmd or "status"
    if act == "run":
        return run()
    if act == "login":
        from . import elevate
        st = State()
        res = elevate.client_request(st, {"t": "browser_login", "site": a.site}, 60)
        if a.wait and res.get("result") == "sent":
            end = time.monotonic() + TTL_WAIT
            while time.monotonic() < end:
                r = elevate.client_request(st, {"t": "browser_login_result", "id": res["id"]}, 10)
                if r.get("result") != "unavailable":
                    res = {**res, **r}
                if r.get("result") not in ("pending", "unavailable"):
                    break
                time.sleep(2)
        if a.json:
            print(json.dumps(res, ensure_ascii=False))
        else:
            display = res.get("display") if res.get("result") in ("sent", "pending") else None
            msg = LOGIN_MSG.get(display) or LOGIN_MSG.get(res.get("reason")) or LOGIN_MSG.get(res.get("why")) or LOGIN_MSG.get(
                res.get("result"), res.get("detail") or res.get("why") or "")
            print(f"BROWSER_LOGIN: {res.get('result')} {res.get('id', '')} — " + msg.format(ttl=res.get("ttl", ""), site=a.site))
        return 0 if res.get("result") in ("sent", "done", "already", "host_login") else 75 if res.get("result") == "pending" else 125
    if act == "sites":
        sa = a.sites_cmd or "list"
        req = {"action": "sites_" + sa}
        if sa in ("add", "remove"):
            req.update(site=a.site, origin=getattr(a, "origin", None), url=getattr(a, "url", None), title=getattr(a, "title", None))
    elif act == "check":
        req = {"action": "check", "sites": a.site or None, "if_stale": a.if_stale}
    elif act == "open":
        req = {"action": "open", "url": a.url}
    elif act == "telegram-qr":
        req = {"action": "telegram_qr", "value": a.value}
    elif act == "adopt":
        if not a.forget and not a.endpoint:
            print(json.dumps({"ok": False, "reason": "shape", "hint": HINTS["shape"]}, ensure_ascii=False))
            return 2
        req = {"action": "adopt", "endpoint": a.endpoint, "dry_run": a.dry_run, "forget": a.forget, "sites": a.site or None}
    elif act == "setup":
        req = {"action": "setup", "own": a.own}
    else:
        req = {"action": act.replace("-", "_"), "registry": getattr(a, "registry", False)}
    res = _ask(req)
    print(json.dumps(res, ensure_ascii=False) if a.json else json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res.get("ok") else 1


TTL_WAIT = 150


def add_parser(sub) -> None:
    p = sub.add_parser("browser", help="Agent J 专用浏览器：setup/status/check/login/sites… / Agent J's own browser")
    p.add_argument("--json", action="store_true")
    b = p.add_subparsers(dest="bcmd")
    for name, h in (("setup", "准备 Agent J 主机的专用浏览器 / set up this computer's dedicated browser"),
                    ("status", "浏览器与各网站登录状态 / browser and site sign-in status"),
                    ("enable", "打开并启动 / turn on"), ("disable", "关闭，保留登录资料 / turn off, keep sign-ins"),
                    ("endpoint", "Linux 本机 CDP 地址 / local CDP endpoint on Linux"),
                    ("sandbox-fix", "系统浏览器安装提示 / guidance for using an OS-installed browser"),
                    ("run", "（服务内部用）常驻浏览器 / (service) keep the browser running")):
        sp = b.add_parser(name, help=h)
        sp.add_argument("--json", action="store_true")
        if name == "status":
            sp.add_argument("--registry", action="store_true", help="also print the P86 capability entries")
        if name == "setup":
            sp.add_argument("--own", action="store_true",
                            help="Linux 上另开专用浏览器 / set up a dedicated Linux browser")
    sp = b.add_parser("adopt", help="登记 Linux 本机 CDP 浏览器（只限 127.0.0.1，主人在手机确认）/ register a Linux loopback CDP browser")
    sp.add_argument("--endpoint", help="http://127.0.0.1:<port>")
    sp.add_argument("--dry-run", action="store_true", help="只读：探测并（加 --site）只读查登录，不登记 / probe only, register nothing")
    sp.add_argument("--site", action="append", help="with --dry-run: read-only sign-in check of a template site (repeatable)")
    sp.add_argument("--forget", action="store_true", help="不再用登记的浏览器（它继续运行）/ stop using it (it keeps running)")
    sp.add_argument("--json", action="store_true")
    sp = b.add_parser("connect", help="申请使用 Ego Lite 的 Agent J 浏览器资料 / request the Agent J profile in Ego Lite")
    sp.add_argument("--json", action="store_true")
    sp = b.add_parser("open", help="在本机浏览器打开网页（主人在Agent J 主机前登录用）/ open a page on the Agent J host")
    sp.add_argument("url")
    sp.add_argument("--json", action="store_true")
    sp = b.add_parser("check", help="只读登录体检 / read-only sign-in check")
    sp.add_argument("--site", action="append", help="repeatable; default all")
    sp.add_argument("--if-stale", action="store_true", help="only sites not confirmed in the last 15 minutes")
    sp.add_argument("--json", action="store_true")
    sp = b.add_parser("login", help="给主人的手机发登录二维码卡，或在本机打开登录页 / sign-in card on the phone")
    sp.add_argument("site")
    sp.add_argument("--wait", action="store_true", help=f"wait up to {TTL_WAIT} s for the result")
    sp.add_argument("--json", action="store_true")
    sp = b.add_parser("telegram-qr", help="旧命令兼容：Telegram 只发文字提醒 / compatibility: Telegram sends text reminders only")
    sp.add_argument("value", nargs="?", choices=["status", "off"], default="status")
    sp.add_argument("--json", action="store_true")
    s = b.add_parser("sites", help="list / add <site> / remove <site>")
    ss = s.add_subparsers(dest="sites_cmd")
    sl = ss.add_parser("list")
    sl.add_argument("--json", action="store_true")
    sa = ss.add_parser("add")
    sa.add_argument("site", help="google · gmail · youtube-studio · bilibili · douyin-creator · mp-weixin · channels-weixin · custom")
    sa.add_argument("--origin", help="custom: https://example.com")
    sa.add_argument("--url", help="custom: a page on the origin that needs sign-in")
    sa.add_argument("--title")
    sa.add_argument("--json", action="store_true")
    sr = ss.add_parser("remove")
    sr.add_argument("site")
    sr.add_argument("--json", action="store_true")
    p.set_defaults(fn=lambda a: sys.exit(cmd(a)))
