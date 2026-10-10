"""P114 (P90 design, ADR-A194): Agent J's own browser — installed by default, a private profile, loopback-only CDP.

- `setup` downloads the Chrome for Testing build named in `browser_manifest.json` (it ships inside the signed host package,
  so the package signature covers every URL, size and SHA-256). Official CDN first; a mirror only serves the same bytes
  (`browser.download_source` auto | official | approved-mirror, `browser.mirror` for an owner's own mirror). Two retries,
  a five-minute budget, ≥ 1.5 GB free. A failure never fails the Agent J install: the browser is `attention` and only the
  work that needs it waits.
- `run` is the resident supervisor (systemd --user `<service>-browser` / LaunchAgent `<label>.browser`): starts the browser
  on a private profile (`<state>/browser/profile`, 0700) with `--remote-debugging-port=0` bound to 127.0.0.1, reads the
  real port from DevToolsActivePort, proves ownership (pid + start time + executable + profile) and that nothing listens
  on another address, then writes `runtime.json` (0600). No display (a server): headless, QR sign-in still works.
- CDP has no authentication. Loopback keeps it off the network; a malicious process of the same OS user can still use it
  and read the profile — the profile isolates data, it is not a sandbox. The endpoint never leaves this computer: no
  tunnel, no forwarding, never on the relay. Cookies, storage, passwords and auth headers are never read into logs or chat.
- `enabled=false` stops the service and the checks; the profile stays (deleting it is the owner's explicit delete).
"""
from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
import pathlib
import platform as _platform
import plistlib
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
import urllib.request
import zipfile

from .state import State

MANIFEST_PATH = pathlib.Path(__file__).with_name("browser_manifest.json")
DOWNLOAD_BUDGET = 300          # seconds for the whole first download (design: 5 minutes, 2 retries)
RETRIES = 2
START_WAIT = 25                # seconds for DevToolsActivePort after a start
HEADLESS_SIZE = "1920,1080"
LOOPBACK = "127.0.0.1"
_LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # loopback never goes through a proxy


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
    return bdir(st) / "profile"


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


def manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text())


def manifest_digest() -> str:
    return hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest()


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
    """macos | linux | wsl2 | wsl1 | unsupported."""
    if sys.platform == "darwin":
        return "macos"
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
    return ("mac-" if s == "macos" else "linux-") + arch


def display_env(environ: dict | None = None) -> bool:
    """A screen this browser can show a window on (Linux / WSLg: DISPLAY or WAYLAND_DISPLAY; macOS: always)."""
    if system() == "macos":
        return True
    e = os.environ if environ is None else environ
    return bool(e.get("DISPLAY") or e.get("WAYLAND_DISPLAY"))


# ---------------------------------------------------------------- the Linux sandbox (Ubuntu 23.10+ AppArmor userns restriction)
SYSTEM_BROWSERS = ("/opt/google/chrome/chrome", "/opt/google/chrome-beta/chrome", "/usr/lib/chromium/chromium",
                   "/usr/lib/chromium-browser/chromium-browser")
APPARMOR_DIR = pathlib.Path("/etc/apparmor.d")


def userns_restricted() -> bool:
    """Ubuntu 23.10+: unprivileged user namespaces need an AppArmor profile, otherwise Chrome has "No usable sandbox"."""
    try:
        return pathlib.Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns").read_text().strip() == "1"
    except OSError:
        return False


def apparmor_allows(exe: str) -> bool:
    """Is there an AppArmor profile that gives this executable (or a glob covering it) `userns`? Read-only scan."""
    import fnmatch
    try:
        files = [f for f in APPARMOR_DIR.iterdir() if f.is_file()]
    except OSError:
        return False
    for f in files:
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        if "userns" not in text:
            continue
        for m in re.finditer(r"^\s*profile\s+\S+\s+(\S+)", text, re.M):
            if fnmatch.fnmatch(exe, m.group(1)):
                return True
    return False


def apparmor_profile(st: State) -> tuple[str, str]:
    """(path, text) of the profile `agentj browser sandbox-fix` installs — only for this user's bundled browser."""
    import pwd
    user = pwd.getpwuid(os.getuid()).pw_name
    glob = str((bdir(st) / "bin").resolve()) + "/*/chrome-linux*/chrome"
    name = "agentj-browser-" + re.sub(r"[^A-Za-z0-9_.-]", "_", user)
    text = ("# Agent J browser: lets this user's Chrome for Testing use its own sandbox (unprivileged user namespaces).\n"
            "# Written by `agentj browser sandbox-fix` after the owner's phone approval. Remove this file to undo.\n"
            "abi <abi/4.0>,\ninclude <tunables/global>\n\n"
            f"profile {name} {glob} flags=(unconfined) {{\n  userns,\n\n  include if exists <local/{name}>\n}}\n")
    return str(APPARMOR_DIR / name), text


def choose_executable(st: State, have: dict) -> tuple[str, str]:
    """(path, source). The verified download, unless this Linux blocks its sandbox: then an OS-packaged Chrome / Chromium
    whose AppArmor profile allows it (with Agent J's own private profile — never the owner's everyday browser data).
    Never `--no-sandbox`."""
    exe = have["path"]
    if system() in ("linux", "wsl2") and userns_restricted() and not apparmor_allows(exe):
        for alt in SYSTEM_BROWSERS:
            if os.path.isfile(alt) and os.access(alt, os.X_OK) and apparmor_allows(alt):
                return alt, "system"
        raise BrowserError("sandbox_blocked", "this Linux restricts user namespaces; run `agentj browser sandbox-fix`")
    return exe, "bundled"


def sandbox_fix(st: State) -> dict:
    """Install the AppArmor profile through the paired-phone password card (`agentj sudo`), then restart the browser."""
    if system() not in ("linux", "wsl2") or not userns_restricted():
        return {"ok": True, "result": "not_needed"}
    path, text = apparmor_profile(st)
    if apparmor_allows(str(bdir(st) / "bin" / "x" / "chrome-linux64" / "chrome")):
        return {"ok": True, "result": "present", "profile": path}
    from . import elevate
    script = f"umask 022; printf '%s' \"$1\" > {path} && apparmor_parser -r {path}"
    res = elevate.client_request(st, {"t": "sudo", "argv": ["/bin/sh", "-c", script, "agentj-sandbox-fix", text], "cwd": "/",
                                      "timeout": 60, "why": "Agent J browser sandbox / 让 Agent J 浏览器能使用系统沙箱",
                                      "effect": f"writes {path} (AppArmor userns for this user's Agent J browser only) / 只为本用户的 Agent J 浏览器开启 userns"})
    if res.get("result") != "done" or res.get("code") != 0:
        return {"ok": False, "result": res.get("result"), "detail": res.get("why") or res.get("detail") or "", "profile": path}
    with contextlib.suppress(FileNotFoundError):
        (bdir(st) / "last-error.json").unlink()
    return {**setup(st), "profile": path}


# ---------------------------------------------------------------- install (download, verify, unpack)
def installed(st: State) -> dict | None:
    rec = _read_json(install_path(st))
    if not isinstance(rec, dict):
        return None
    exe = bdir(st) / rec.get("executable", "")
    if not rec.get("executable") or not exe.is_file() or not os.access(exe, os.X_OK):
        return None
    return {**rec, "path": str(exe)}


def _sources(build: dict, cfg: dict, man: dict) -> list[str]:
    official = [build["url"]]
    mirrors = [m.rstrip("/") + "/" + build["path"] for m in man.get("approved_mirrors") or [] if isinstance(m, str) and m.startswith("https://")]
    own = cfg.get("mirror") or ""
    if own.startswith("https://"):
        mirrors.append(own.rstrip("/") + "/" + build["path"])
    src = cfg.get("download_source", "auto")
    if src == "official":
        return official
    if src == "approved-mirror":
        return mirrors
    return official + mirrors


def _download(url: str, dest: pathlib.Path, size: int, digest: str, deadline: float, opener=None) -> None:
    """Stream to dest; raises BrowserError. TLS verification stays on; no redirect to http."""
    class _HttpsOnly(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not newurl.startswith("https://"):
                return None
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    op = opener or urllib.request.build_opener(_HttpsOnly)
    h = hashlib.sha256()
    got = 0
    try:
        left = max(5.0, deadline - time.monotonic())
        with op.open(urllib.request.Request(url, headers={"User-Agent": "agentj-browser-setup"}), timeout=min(60.0, left)) as r, \
                open(dest, "wb") as f:
            while True:
                if time.monotonic() > deadline:
                    raise BrowserError("download_timeout", "five-minute download budget used up")
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                got += len(chunk)
                if got > size:
                    raise BrowserError("download_corrupt", "more bytes than the signed manifest says")
                h.update(chunk)
                f.write(chunk)
    except BrowserError:
        raise
    except OSError as e:
        if e.errno in (errno.ENOSPC, errno.EDQUOT):
            raise BrowserError("no_space", type(e).__name__) from None
        raise BrowserError("download_failed", type(e).__name__) from None
    if got != size or h.hexdigest() != digest:
        raise BrowserError("download_corrupt", f"size/sha256 mismatch ({got} bytes)")


def _extract(zip_path: pathlib.Path, dest: pathlib.Path) -> None:
    """Unpack keeping executable bits and symlinks (macOS app bundles need both); no path escapes."""
    if sys.platform == "darwin" and shutil.which("ditto"):
        r = subprocess.run(["ditto", "-x", "-k", str(zip_path), str(dest)], capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            raise BrowserError("extract_failed", (r.stderr or "")[:200])
        return
    root = dest.resolve()
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            target = (dest / info.filename).resolve()
            if root != target and root not in target.parents:
                raise BrowserError("extract_failed", "path outside the destination")
            mode = (info.external_attr >> 16) & 0o177777
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if stat.S_ISLNK(mode):
                link = z.read(info).decode()
                if link.startswith("/") or ".." in pathlib.PurePosixPath(link).parts and \
                        root not in (target.parent / link).resolve().parents:
                    raise BrowserError("extract_failed", "symlink outside the destination")
                with contextlib.suppress(FileNotFoundError):
                    target.unlink()
                os.symlink(link, target)
                continue
            with z.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)
            os.chmod(target, (mode & 0o777) or 0o644)


def missing_libs(exe: str) -> list[str]:
    """Linux only: shared libraries the browser cannot find (`ldd`). Empty = fine or unknown."""
    if system() == "macos" or not shutil.which("ldd"):
        return []
    try:
        r = subprocess.run(["ldd", exe], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return sorted({ln.split("=>")[0].strip() for ln in r.stdout.splitlines() if "not found" in ln})[:40]


def install(st: State, opener=None, now=time.monotonic) -> dict:
    """Download + verify + unpack the manifest's build (idempotent: an existing verified install is kept)."""
    man = manifest()
    key = platform_key()
    if key is None or key not in man["builds"]:
        s = system()
        raise BrowserError("unsupported", "WSL1: upgrade to WSL2 with WSLg" if s == "wsl1" else f"{s}/{_platform.machine()}")
    build = man["builds"][key]
    have = installed(st)
    if have and have.get("version") == man["version"] and have.get("sha256") == build["sha256"]:
        return {"result": "present", "version": man["version"], "platform": key}
    d = ensure_dir(st)
    free = shutil.disk_usage(d).free
    if free < man.get("min_free_bytes", 1_500_000_000):
        raise BrowserError("no_space", f"{free // (1 << 20)} MiB free, need ≥ {man['min_free_bytes'] // (1 << 20)} MiB")
    sources = _sources(build, settings(st), man)
    if not sources:
        raise BrowserError("no_source", "browser.download_source=approved-mirror but no approved mirror is configured")
    deadline = now() + DOWNLOAD_BUDGET
    tmp = d / f".download-{os.getpid()}.zip"
    last = None
    try:
        for attempt in range(RETRIES + 1):
            url = sources[attempt % len(sources)]
            try:
                _download(url, tmp, build["size"], build["sha256"], deadline, opener)
                last = None
                break
            except BrowserError as e:
                last = e
                if e.reason == "download_timeout" or now() > deadline:
                    break
        if last is not None:
            raise last
        unpack = d / f".unpack-{os.getpid()}"
        shutil.rmtree(unpack, ignore_errors=True)
        unpack.mkdir(mode=0o700)
        try:
            _extract(tmp, unpack)
            final = d / "bin" / man["version"]
            final.parent.mkdir(mode=0o700, exist_ok=True)
            shutil.rmtree(final, ignore_errors=True)
            os.replace(unpack, final)
        except OSError as e:
            raise BrowserError("no_space" if e.errno in (errno.ENOSPC, errno.EDQUOT) else "extract_failed", type(e).__name__) from None
        finally:
            shutil.rmtree(unpack, ignore_errors=True)
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
    rel = str(pathlib.PurePosixPath("bin", man["version"], *build["executable"]))
    exe = d / rel
    if not exe.is_file():
        raise BrowserError("extract_failed", "browser executable missing after unpack")
    os.chmod(exe, os.stat(exe).st_mode | 0o100)
    for old in (d / "bin").iterdir():           # an older version is replaced, never kept around
        if old.name != man["version"]:
            shutil.rmtree(old, ignore_errors=True)
    rec = {"version": man["version"], "platform": key, "sha256": build["sha256"], "executable": rel,
           "installed_at": int(time.time()), "manifest_sha256": manifest_digest()}
    _write_json(st, install_path(st), rec)
    st.log("browser_installed", version=man["version"], platform=key)
    return {"result": "installed", "version": man["version"], "platform": key}


# ---------------------------------------------------------------- the process
def proc_start_time(pid: int) -> str | None:
    """A token that changes when the pid is reused (Linux: /proc start ticks; macOS: ps lstart)."""
    if sys.platform.startswith("linux"):
        try:
            raw = pathlib.Path(f"/proc/{pid}/stat").read_text()
            return raw.rsplit(")", 1)[1].split()[19]
        except (OSError, IndexError):
            return None
    try:
        r = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def listen_addresses(port: int, pid: int | None = None) -> set[str]:
    """Local addresses listening on this TCP port (Linux /proc/net/tcp{,6}; macOS lsof)."""
    out: set[str] = set()
    if sys.platform.startswith("linux"):
        for name, v6 in (("tcp", False), ("tcp6", True)):
            try:
                rows = pathlib.Path(f"/proc/net/{name}").read_text().splitlines()[1:]
            except OSError:
                continue
            for row in rows:
                f = row.split()
                if len(f) < 4 or f[3] != "0A":           # LISTEN
                    continue
                addr, p = f[1].split(":")
                if int(p, 16) != port:
                    continue
                if v6:
                    out.add("::1" if addr == "00000000000000000000000001000000" else
                            "::" if addr == "0" * 32 else
                            "127.0.0.1" if addr == "0000000000000000FFFF00000100007F" else addr)
                else:
                    b = bytes.fromhex(addr)[::-1]
                    out.add(".".join(str(x) for x in b))
        return out
    try:
        r = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fn"] + (["-a", "-p", str(pid)] if pid else []),
                           capture_output=True, text=True, timeout=10)
        for ln in r.stdout.splitlines():
            if ln.startswith("n"):
                out.add(ln[1:].rsplit(":", 1)[0].strip("[]"))
    except (OSError, subprocess.TimeoutExpired):
        pass
    return out


def loopback_only(addrs: set[str]) -> bool:
    return bool(addrs) and all(a in ("127.0.0.1", "::1", "localhost") for a in addrs)


def runtime(st: State) -> dict | None:
    """runtime.json, verified: the recorded pid is alive, the same process (start time), the endpoint answers and listens on
    loopback only. None = not running (or not ours)."""
    rec = _read_json(runtime_path(st))
    if not isinstance(rec, dict) or type(rec.get("pid")) is not int or type(rec.get("port")) is not int:
        return None
    if proc_start_time(rec["pid"]) != rec.get("starttime"):
        return None
    try:
        with _LOCAL.open(f"http://{LOOPBACK}:{rec['port']}/json/version", timeout=3) as r:
            info = json.loads(r.read(65536))
    except (OSError, ValueError):
        return None
    ws = info.get("webSocketDebuggerUrl") or ""
    if not ws.startswith(f"ws://{LOOPBACK}:{rec['port']}/devtools/browser/"):
        return None
    return {**rec, "ws": ws, "product": info.get("Browser", "")}


def _launch_args(exe: str, profile: pathlib.Path, headless: bool) -> list[str]:
    args = [exe, f"--user-data-dir={profile}", "--remote-debugging-port=0", f"--remote-debugging-address={LOOPBACK}",
            "--no-first-run", "--no-default-browser-check", "--disable-background-networking-for-update-checks",
            f"--window-size={HEADLESS_SIZE}"]
    if headless:
        args += ["--headless=new"]
    if system() in ("linux", "wsl2") and os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("DISPLAY"):
        args += ["--ozone-platform=wayland"]
    return args + ["about:blank"]


def _wait_port(profile: pathlib.Path, proc: subprocess.Popen, wait: float) -> tuple[int, str]:
    f = profile / "DevToolsActivePort"
    end = time.monotonic() + wait
    while time.monotonic() < end:
        if proc.poll() is not None:
            raise BrowserError("start_failed", f"browser exited with {proc.returncode}")
        with contextlib.suppress(OSError, ValueError, IndexError):
            lines = f.read_text().splitlines()
            if len(lines) >= 2 and lines[0].isdigit():
                return int(lines[0]), lines[1]
        time.sleep(0.2)
    raise BrowserError("start_failed", "no DevToolsActivePort")


def start_browser(st: State, headless: bool | None = None) -> tuple[subprocess.Popen, dict]:
    have = installed(st)
    if not have:
        raise BrowserError("not_installed", "run `agentj browser setup`")
    prof = profile_dir(st)
    prof.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(prof, 0o700)
    with contextlib.suppress(FileNotFoundError):
        (prof / "DevToolsActivePort").unlink()
    if headless is None:
        headless = not display_env()
    exe, source = choose_executable(st, have)
    log = open(bdir(st) / "browser.log", "ab", buffering=0)
    os.chmod(bdir(st) / "browser.log", 0o600)
    proc = subprocess.Popen(_launch_args(exe, prof, headless), stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                            start_new_session=True)
    log.close()
    try:
        port, path = _wait_port(prof, proc, START_WAIT)
        addrs = set()
        for _ in range(10):
            addrs = listen_addresses(port, proc.pid)
            if addrs:
                break
            time.sleep(0.2)
        if not loopback_only(addrs):
            raise BrowserError("not_loopback", ",".join(sorted(addrs)) or "no listener")
    except BrowserError:
        stop_proc(proc)
        raise
    rec = {"pid": proc.pid, "starttime": proc_start_time(proc.pid), "port": port, "exe": exe, "source": source,
           "profile": str(prof), "headless": headless, "version": have.get("version") if source == "bundled" else "system",
           "started_at": int(time.time()), "supervisor": os.getpid()}
    _write_json(st, runtime_path(st), rec)
    st.log("browser_started", headless=headless, source=source, version=rec["version"])
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
        for k in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"):
            if env.get(k):
                os.environ[k] = env[k]
        return True
    return False


def run(st: State | None = None) -> int:
    """The service's main process: keep one browser alive on the private profile. Exit 0 when disabled (the service
    manager then leaves it stopped); a crash exits non-zero so systemd / launchd restart it with their back-off."""
    st = st or State()
    if not settings(st).get("enabled"):
        return 0
    ensure_dir(st)
    stopping = {"now": False}

    def _term(*_):
        stopping["now"] = True
    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)
    try:
        proc, rec = start_browser(st)
    except BrowserError as e:
        st.log("browser_start_failed", reason=e.reason)
        _write_json(st, bdir(st) / "last-error.json", {"reason": e.reason, "detail": e.detail[:200], "at": int(time.time())})
        return 3
    with contextlib.suppress(FileNotFoundError):
        (bdir(st) / "last-error.json").unlink()
    last_look = time.monotonic()
    try:
        while not stopping["now"]:
            if proc.poll() is not None:
                st.log("browser_exited", code=proc.returncode)
                return 4
            if rec["headless"] and time.monotonic() - last_look > 30:
                last_look = time.monotonic()
                if _manager_has_display() and not login_in_progress(st):
                    st.log("browser_display_arrived")
                    stop_proc(proc)
                    proc, rec = start_browser(st, headless=False)
            time.sleep(1)
    finally:
        stop_proc(proc)
        cur = _read_json(runtime_path(st))
        if isinstance(cur, dict) and cur.get("pid") == proc.pid:
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
    if e.get("AGENTJ_STATE_DIR"):
        env["AGENTJ_STATE_DIR"] = e["AGENTJ_STATE_DIR"]
    q = service._sd_quote
    lines = ["# Agent J browser — written by `agentj browser setup`. Loopback-only; no secrets here, by design.",
             "[Unit]", "Description=Agent J browser (agentj browser run): private profile, CDP on 127.0.0.1 only",
             "After=graphical-session.target", "StartLimitIntervalSec=600", "StartLimitBurst=10", "",
             "[Service]", "Type=simple", "ExecStart=" + " ".join(q(x) for x in argv + ["browser", "run"]),
             "Restart=on-failure", "RestartSec=10", "TimeoutStopSec=20", "StandardInput=null"]
    lines += [f"Environment={q(f'{k}={v}')}" for k, v in env.items()]
    lines += ["", "[Install]", "WantedBy=default.target", ""]
    return "\n".join(lines)


def plist_bytes(label: str, argv: list[str], log_path: str, environ: dict | None = None) -> bytes:
    e = os.environ if environ is None else environ
    env = {k: e[k] for k in ("AGENTJ_STATE_DIR",) if e.get(k)}
    return plistlib.dumps({"Label": label, "ProgramArguments": argv + ["browser", "run"], "EnvironmentVariables": env,
                           "RunAtLoad": True, "KeepAlive": {"SuccessfulExit": False}, "ThrottleInterval": 10,
                           "LimitLoadToSessionType": "Aqua", "ProcessType": "Interactive",
                           "StandardInPath": "/dev/null", "StandardOutPath": log_path, "StandardErrorPath": log_path})


def service_install(st: State) -> dict:
    from . import service
    plat = service.platform()
    n = svc_name()
    argv = service.agentj_argv()
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
        service._systemctl("restart", f"{n}.service", check=True)
        return {"kind": "systemd", "name": n, "path": path}
    if plat == "macos":
        path = os.path.join(os.path.expanduser("~"), "Library", "LaunchAgents", f"{n}.plist")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        service._write(path, plist_bytes(n, argv, str(bdir(st) / "service.log")), 0o644)
        r = service._bootstrap(n, path)
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
    rec = _read_json(runtime_path(st))     # a browser this computer's `run` started (verified pid) also goes
    if isinstance(rec, dict) and type(rec.get("pid")) is int and proc_start_time(rec["pid"]) == rec.get("starttime"):
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


# ---------------------------------------------------------------- the commands' logic (shared by CLI and serve)
def setup(st: State, opener=None, start_service: bool = True) -> dict:
    """Idempotent: respect a disabled browser, download/verify once, (re)write the service, wait for a verified start."""
    cfg = settings(st)
    if not cfg["enabled"]:
        return {"ok": True, "result": "disabled", "note": "browser.enabled=false — `agentj browser enable` turns it back on"}
    out = {"ok": False, "result": "attention"}
    try:
        out["install"] = install(st, opener)
    except BrowserError as e:
        st.log("browser_setup_failed", reason=e.reason)
        _write_json(st, bdir(st) / "last-error.json", {"reason": e.reason, "detail": e.detail[:200], "at": int(time.time())})
        return {**out, "reason": e.reason, "detail": e.detail, "hint": HINTS.get(e.reason, "")}
    have = installed(st)
    libs = missing_libs(have["path"]) if have else []
    if libs:
        _write_json(st, bdir(st) / "last-error.json", {"reason": "missing_libs", "detail": ",".join(libs)[:200], "at": int(time.time())})
        return {**out, "reason": "missing_libs", "libs": libs, "hint": HINTS["missing_libs"]}
    try:
        _, source = choose_executable(st, have)
    except BrowserError as e:
        _write_json(st, bdir(st) / "last-error.json", {"reason": e.reason, "detail": e.detail[:200], "at": int(time.time())})
        return {**out, "reason": e.reason, "hint": HINTS[e.reason]}
    out["source"] = source
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
    cfg = settings(st)
    have = installed(st)
    rt = runtime(st) if cfg["enabled"] else None
    svc = service_status()
    err = _read_json(bdir(st) / "last-error.json")
    attention = []
    if cfg["enabled"]:
        if not have:
            attention.append(err.get("reason") if isinstance(err, dict) and err.get("reason") else "not_installed")
        elif not rt:
            attention.append(err.get("reason") if isinstance(err, dict) and err.get("reason") else "not_running")
    state = "disabled" if not cfg["enabled"] else "ready" if rt else "attention"
    out = {"ok": state != "attention", "state": state, "enabled": cfg["enabled"], "system": system(),
           "platform": platform_key(), "installed": {"version": have["version"], "platform": have["platform"]} if have else None,
           "service": svc, "attention": attention, "hints": [HINTS[a] for a in attention if a in HINTS]}
    if rt:
        out["runtime"] = {"pid": rt["pid"], "headless": rt.get("headless"), "loopback_only": True, "source": rt.get("source", "bundled"),
                          "version": rt.get("version"), "started_at": rt.get("started_at"), "host_login": not rt.get("headless")}
    return out


def endpoint(st: State) -> dict:
    """The local CDP HTTP endpoint for tools on THIS computer (Playwright connect_over_cdp). Never send it anywhere."""
    rt = runtime(st)
    if not rt:
        return {"ok": False, "reason": "not_running", "hint": HINTS["not_running"]}
    return {"ok": True, "endpoint": f"http://{LOOPBACK}:{rt['port']}", "note": ENDPOINT_NOTE}


def set_enabled(st: State, on: bool) -> dict:
    res = _set_pref(st, "browser.enabled", on)
    if not res.get("ok", True):
        return {"ok": False, "reason": "config", "detail": str(res.get("error") or res)[:200]}
    if not on:
        service_stop(st)
        return {"ok": True, "enabled": False, "note": "service stopped; profile and sign-ins kept"}
    return {**setup(st), "enabled": True}


def _set_pref(st: State, key: str, value) -> dict:
    """Edit the user override file with the configuration command's own writer (lock, comments kept, schema-checked,
    history + last-good), exactly like `agentj config set`."""
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


ENDPOINT_NOTE = ("Local only: connect with Playwright `chromium.connect_over_cdp(endpoint)` on this computer. Never send it to "
                 "chat, the cloud or another machine; open your own tabs and close only the tabs you opened. / 只在本机使用。")

HINTS = {
    "unsupported": "这台电脑的系统/架构不在支持列表（macOS、Linux x64/arm64、Windows 的 WSL2+WSLg）/ unsupported OS or CPU",
    "no_space": "空间不够：浏览器约需 1.5 GB，清理后运行 agentj browser setup / free about 1.5 GB, then agentj browser setup",
    "download_failed": "下载失败：检查网络或代理，稍后运行 agentj browser setup（国内网络可设 browser.mirror 为同字节镜像）/ download failed",
    "download_timeout": "下载超过 5 分钟：稍后运行 agentj browser setup / the download took too long",
    "download_corrupt": "下载的文件校验不对，已丢弃，没有使用 / the download did not match the signed manifest and was discarded",
    "no_source": "没有可用的下载源：把 browser.download_source 改回 auto / no download source",
    "extract_failed": "解压失败：运行 agentj browser setup 重试 / unpacking failed",
    "missing_libs": "缺少系统库：用 `agentj sudo --why '安装浏览器依赖' -- apt-get install -y <包>`（或系统对应的包管理器），装好后 agentj browser setup / missing system libraries",
    "no_service_manager": "没有可用的用户服务管理器（Linux: loginctl enable-linger $USER；WSL: 开启 systemd）/ no user service manager",
    "not_installed": "浏览器还没装：agentj browser setup / not installed yet",
    "not_running": "浏览器没在运行：agentj browser setup（会重启服务）/ not running: agentj browser setup",
    "start_failed": "浏览器启动失败：看 agentj browser status 和 agentj doctor / the browser did not start",
    "sandbox_blocked": "这台 Linux（Ubuntu 23.10+ 等）限制了浏览器沙箱：运行 agentj browser sandbox-fix，主人在手机密码卡上批准后只为本用户的 Agent J 浏览器开启；绝不用 --no-sandbox / this Linux blocks the browser sandbox: agentj browser sandbox-fix (phone password card)",
    "not_loopback": "调试端口不只监听本机，已停止（安全）/ the debugging port was not loopback-only and was stopped",
}


def execute(req: dict, st: State | None = None) -> dict:
    """The host side of `agentj browser …` (serve's elevate.sock, or directly when serve is not running)."""
    from . import browser_sites as bs
    st = st or State()
    a = req.get("action")
    if a == "status":
        out = status(st)
        out["sites"] = bs.summary(st)
        if req.get("registry"):
            out["registry"] = bs.registry(st)
        return out
    if a == "setup":
        return setup(st)
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


LOGIN_MSG = {
    "sent": "已发登录二维码卡到主人的已配对手机（{ttl} 秒内有效）。请主人在手机上扫码；成功由浏览器自己确认，不要相信「扫好了」这句话。"
            "/ A sign-in QR card is on the owner's paired phone ({ttl} s). Success is confirmed by the browser itself.",
    "done": "已登录，体检已确认。/ Signed in; confirmed by a check.",
    "already": "这个网站已经是登录状态。/ Already signed in.",
    "host_login": "这个网站不支持扫码：已在这台电脑的 Agent J 浏览器打开登录页，请主人在电脑前登录，然后运行 agentj browser check --site {site}。"
                  "/ No QR for this site: the sign-in page is open on this computer; the owner signs in there.",
    "host_login_unavailable": "这台电脑没有屏幕，这个网站又不支持扫码：请在有屏幕的电脑上登录，或换支持扫码的网站。/ No screen and no QR sign-in.",
    "no_qr": "登录页上没找到二维码（页面可能改版或要求别的验证）：请主人在电脑前登录。/ No QR found on the sign-in page.",
    "expired": "二维码已过期，没有扫码。主人可以在卡片上点「刷新」，或稍后再说「早上好」。不要反复发。/ The QR expired unscanned. Do not resend repeatedly.",
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
                if r.get("result") not in ("pending", "unavailable"):
                    res = {**res, **r}
                    break
                time.sleep(2)
        if a.json:
            print(json.dumps(res, ensure_ascii=False))
        else:
            msg = LOGIN_MSG.get(res.get("result"), res.get("detail") or res.get("why") or "")
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
    for name, h in (("setup", "下载校验并启动（幂等）/ download, verify, start (idempotent)"),
                    ("status", "浏览器与各网站登录状态 / browser and site sign-in status"),
                    ("enable", "打开并启动 / turn on"), ("disable", "关闭，保留登录资料 / turn off, keep sign-ins"),
                    ("endpoint", "本机 CDP 地址（只在本机用）/ local CDP endpoint (this computer only)"),
                    ("sandbox-fix", "Ubuntu 23.10+ 沙箱受限时，经手机密码卡安装 AppArmor 规则 / AppArmor profile via the phone card"),
                    ("run", "（服务内部用）常驻浏览器 / (service) keep the browser running")):
        sp = b.add_parser(name, help=h)
        sp.add_argument("--json", action="store_true")
        if name == "status":
            sp.add_argument("--registry", action="store_true", help="also print the P86 capability entries")
    sp = b.add_parser("open", help="在本机浏览器打开网页（主人在电脑前登录用）/ open a page on this computer")
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
    sp = b.add_parser("telegram-qr", help="登录二维码是否也发 Telegram：status / off（打开只能由主人在手机卡片上点）")
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
