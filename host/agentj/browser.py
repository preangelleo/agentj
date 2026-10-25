"""P114 (P90 design, ADR-A194): Agent J's own browser — installed by default, a private profile, loopback-only CDP.

- `setup` downloads the Chrome for Testing build named in `browser_manifest.json` (it ships inside the signed host package,
  so the package signature covers every URL, size and SHA-256). Playwright's Chrome-for-Testing CDN (cdn.playwright.dev,
  `official` below) first; a mirror only serves the same bytes
  (`browser.download_source` auto | official | approved-mirror, `browser.mirror` for an owner's own mirror). Two retries,
  a five-minute budget, ≥ 1.5 GB free. A failure never fails the Agent J install: the browser is `attention` and only the
  work that needs it waits.
- `run` is the resident supervisor (systemd --user `<service>-browser` / LaunchAgent `<label>.browser`): starts the browser
  on a private profile (`<state>/browser/profile`, 0700) with `--remote-debugging-port=0` bound to 127.0.0.1, reads the
  real port from DevToolsActivePort, proves ownership (pid + start time + executable + profile) and that nothing listens
  on another address, then writes `runtime.json` (0600). No display (a server): headless, QR sign-in still works.
- CDP has no authentication. Loopback keeps it off the network; a malicious process of the same OS user can still use it
  and read the profile — the profile isolates data, it is not a sandbox. The endpoint never leaves the Agent J host: no
  tunnel, no forwarding, never on the relay. Cookies, storage, passwords and auth headers are never read into logs or chat.
- `enabled=false` stops the service and the checks; the profile stays (deleting it is the owner's explicit delete).
- P131 adopt: an owner who already runs a CDP browser on the Agent J host (e.g. a long-lived Chromium on 127.0.0.1:9222 with
  every site signed in) can register it instead — `agentj browser adopt --endpoint http://127.0.0.1:<port>` only proposes it
  (loopback only, verified to listen on loopback only); the owner confirms once on the paired phone's checklist (signed
  `setup_mark adopt_browser`). Adopted, it is Agent J's browser for status / check / sign-in, but Agent J never installs a
  second one, never starts / kills / restarts it, never touches its profile or reads its cookies: it only opens and closes its
  own tabs. Not answering = "the browser you registered is not running". `setup` that finds such a browser asks first.
- P521: a per-profile start.lock serializes launch and runtime publication; live pid/starttime/CDP is reused without a new
  Chrome invocation. Window exit never restarts the service; observer restarts preserve the browser. Upgrade refreshes old
  service policy without waking a closed browser; only exact former labels for the same state are removed.
- P521 multi-seat: instances 2+ default to headless, even after a display arrives. Explicit open/host sign-in may
  show that seat with its own profile; checks and QR cards stay headless.
- P418 (#183 ⑤), amended by P521: an explicit action that needs the browser (`open`, `check` without `--if-stale`,
  `endpoint`) first starts Agent J's own service once (`systemctl --user reset-failed` + `start`, launchd `kickstart`; never
  enable / install / restart), waits for a verified runtime (WAKE_WAIT), carries on and says so (`restarted: true`). It never
  starts an adopted browser: the answer tells the owner to open it, or how to switch to Agent J's own. A failed start keeps
  the action's own error and adds one `next` step. Status, doctor and background/stale checks never wake a closed window.
- P131 recognisable: Agent J's own window carries the title "Agent J 专用浏览器", its own start page and a brand-colour
  theme, and the checklist's "open and sign in" brings it to the front (Hyprland / X11 / macOS; elsewhere it says so).
"""
from __future__ import annotations

import contextlib
import errno
import fcntl
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
from .instance import current as current_instance

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
    loopback only. None = not running (or not ours). With an adopted browser (P131): that endpoint, re-verified each time."""
    ad = adopted(st)
    if ad:
        info = _probe_adopted(ad["port"])
        if not info:
            return None
        return {"pid": info.get("pid"), "port": ad["port"], "ws": info["ws"], "product": info["product"], "headless": False,
                "source": "adopted", "adopted": True, "version": "adopted", "started_at": ad.get("adopted_at")}
    rec = _read_json(runtime_path(st))
    if not isinstance(rec, dict) or type(rec.get("pid")) is not int or type(rec.get("port")) is not int:
        return None
    if not 0 < rec["port"] < 65536 or rec.get("profile") != str(profile_dir(st)):
        return None
    if rec["pid"] <= 0 or not rec.get("starttime") or proc_start_time(rec["pid"]) != rec["starttime"]:
        return None
    if sys.platform.startswith("linux"):
        try:
            # Chrome rewrites argv into one process-title string; scripts keep NUL-separated argv.
            args = b" " + pathlib.Path(f"/proc/{rec['pid']}/cmdline").read_bytes().replace(b"\0", b" ") + b" "
            if (b" " + os.fsencode(f"--user-data-dir={profile_dir(st)}") + b" " not in args
                    or (rec.get("process_exe") and os.readlink(f"/proc/{rec['pid']}/exe") != rec["process_exe"])):
                return None
        except OSError:
            return None
    try:
        with _LOCAL.open(f"http://{LOOPBACK}:{rec['port']}/json/version", timeout=3) as r:
            info = json.loads(r.read(65536))
    except (OSError, ValueError):
        return None
    ws = info.get("webSocketDebuggerUrl") or ""
    if not ws.startswith(f"ws://{LOOPBACK}:{rec['port']}/devtools/browser/"):
        return None
    if not loopback_only(listen_addresses(rec["port"], rec["pid"])):
        return None
    return {**rec, "ws": ws, "product": info.get("Browser", "")}


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


def _launch_args(exe: str, profile: pathlib.Path, headless: bool, start: str = "about:blank") -> list[str]:
    args = [exe, f"--user-data-dir={profile}", "--remote-debugging-port=0", f"--remote-debugging-address={LOOPBACK}",
            "--no-first-run", "--no-default-browser-check", "--disable-background-networking-for-update-checks",
            f"--window-size={HEADLESS_SIZE}", f"--window-name={WINDOW_NAME}", f"--class={WINDOW_CLASS}"]
    if headless:
        args += ["--headless=new"]
    if system() in ("linux", "wsl2") and os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("DISPLAY"):
        args += ["--ozone-platform=wayland"]
    return args + [start]


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
    """Reuse without sending Chrome another launch request (which would open another window)."""
    with start_lock(st):
        rt = runtime(st)
        if rt:
            return None, rt
        rec = _read_json(runtime_path(st))
        if (isinstance(rec, dict) and type(rec.get("pid")) is int and rec["pid"] > 0
                and rec.get("starttime") and proc_start_time(rec["pid"]) == rec["starttime"]):
            raise BrowserError("start_failed", "existing browser is alive but CDP is unavailable; no second launch")
        # Older supervisors can leave Chrome's profile singleton alive without a runtime record.
        # Do not forward a welcome-page launch to it. Never touch another host's singleton.
        singleton = profile_dir(st) / "SingletonLock"
        if singleton.is_symlink():
            target = os.readlink(singleton)
            host, _, pid = target.rpartition("-")
            if host == socket.gethostname() and pid.isdigit() and proc_start_time(int(pid)):
                raise BrowserError("start_failed", "profile is already in use; no second launch")
        return _start_browser(st, headless)


def visible_runtime(st: State) -> dict | None:
    """Explicit host sign-in/open only: switch our headless browser using its own profile.

    Background checks/endpoints never call this. Keep an active QR session intact.
    """
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


def _start_browser(st: State, headless: bool | None = None) -> tuple[subprocess.Popen, dict]:
    have = installed(st)
    if not have:
        raise BrowserError("not_installed", "run `agentj browser setup`")
    prof = profile_dir(st)
    prof.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(prof, 0o700)
    with contextlib.suppress(FileNotFoundError):
        (prof / "DevToolsActivePort").unlink()
    if headless is None:
        headless = current_instance() != 1 or not display_env()
    exe, source = choose_executable(st, have)
    brand_profile(prof)
    log = open(bdir(st) / "browser.log", "ab", buffering=0)
    os.chmod(bdir(st) / "browser.log", 0o600)
    proc = subprocess.Popen(_launch_args(exe, prof, headless, start_page(st)), stdin=subprocess.DEVNULL, stdout=log, stderr=log,
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
    if sys.platform.startswith("linux"):
        with contextlib.suppress(OSError):
            rec["process_exe"] = os.readlink(f"/proc/{proc.pid}/exe")  # distro launchers may exec a different binary
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
    """Observe one browser. Window exit is final; service restarts preserve the existing browser."""
    st = st or State()
    if not settings(st).get("enabled") or adopted(st):
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
            if proc.poll() is not None:
                st.log("browser_exited", code=proc.returncode)
                return 0
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


def cleanup_legacy_services(st: State) -> list[str]:
    """Only known former default labels, only files written for this state. No wildcard process/service killing."""
    from . import service
    old, plat = service.legacy_name(), service.platform()
    if not old:
        return []
    n = old + ("-browser" if plat == "linux" else ".browser")
    path = (pathlib.Path(service.unit_dir()) / f"{n}.service" if plat == "linux" else
            pathlib.Path.home() / "Library" / "LaunchAgents" / f"{n}.plist")
    if not path.is_file():
        return []
    if plat == "linux":
        text = path.read_text()
        # Exact generated marker plus browser command; an explicit state must match.
        if not text.startswith("# Agent J browser — written by `agentj browser setup`.") or '"browser" "run"' not in text:
            return []
        instance = os.environ.get("AGENTJ_INSTANCE", "1")
        if "AGENTJ_INSTANCE=" in text:
            if service._sd_quote(f"AGENTJ_INSTANCE={instance}") not in text:
                return []
        elif instance != "1":
            return []
        if "AGENTJ_STATE_DIR=" in text and service._sd_quote(f"AGENTJ_STATE_DIR={st.root}") not in text:
            return []
        if "AGENTJ_STATE_DIR=" not in text and os.environ.get("AGENTJ_STATE_DIR"):
            return []
        service._systemctl("disable", "--now", f"{n}.service", check=True)
        path.unlink()
        service._systemctl("daemon-reload", check=True)
        service._systemctl("reset-failed", f"{n}.service")
    elif plat == "macos":
        rec = plistlib.loads(path.read_bytes())
        env = rec.get("EnvironmentVariables", {})
        if rec.get("Label") != n or rec.get("ProgramArguments", [])[-2:] != ["browser", "run"]:
            return []
        if str(env.get("AGENTJ_INSTANCE", "1")) != os.environ.get("AGENTJ_INSTANCE", "1"):
            return []
        if env.get("AGENTJ_STATE_DIR") != str(st.root) and (env.get("AGENTJ_STATE_DIR") or os.environ.get("AGENTJ_STATE_DIR")):
            return []
        if not service._bootout_wait(n):
            raise BrowserError("no_service_manager", "old browser service did not stop")
        path.unlink()
    else:
        return []
    return [n]


def refresh_service(st: State) -> None:
    """Upgrade/reinstall: migrate an existing browser service's policy without starting a closed browser."""
    from . import service
    plat, n = service.platform(), svc_name()
    path = (pathlib.Path(service.unit_dir()) / f"{n}.service" if plat == "linux" else
            pathlib.Path.home() / "Library" / "LaunchAgents" / f"{n}.plist")
    legacy = cleanup_legacy_services(st)
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
    u = urlsplit(url if "://" in url else "http://" + url)
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


def probe_endpoint(port: int, timeout: float = 2.0) -> dict | None:
    """Read-only: is a CDP browser answering on 127.0.0.1:<port>, listening on loopback only? Reads /json/version, nothing
    else (no tabs, no cookies, no pages). → {port, ws, product, pid} or None."""
    try:
        with _LOCAL.open(f"http://{LOOPBACK}:{int(port)}/json/version", timeout=timeout) as r:
            info = json.loads(r.read(65536))
    except (OSError, ValueError):
        return None
    ws = info.get("webSocketDebuggerUrl") if isinstance(info, dict) else None
    if not (isinstance(ws, str) and ws.startswith(f"ws://{LOOPBACK}:{int(port)}/devtools/browser/")):
        return None
    addrs = listen_addresses(int(port))
    if addrs and not loopback_only(addrs):
        return None                                 # also on the network: never adopt it
    return {"port": int(port), "ws": ws, "product": str(info.get("Browser", ""))[:80], "pid": _pid_of_port(int(port))}


def _probe_adopted(port: int) -> dict | None:
    """The registered browser: one slow /json/version (it is busy with someone else's work) is not "not running" — read twice."""
    return probe_endpoint(port) or (time.sleep(0.5), probe_endpoint(port, timeout=3.0))[1]


def detect_existing(st: State, ports=None) -> list[dict]:
    """CDP browsers already running on the Agent J host's usual debugging ports (not Agent J's own). Read-only (/json/version).
    AGENTJ_BROWSER_DETECT=off turns detection off (tests; an owner who never wants the question)."""
    if os.environ.get("AGENTJ_BROWSER_DETECT") == "off":
        return []
    own = _read_json(runtime_path(st))
    own_port = own.get("port") if isinstance(own, dict) else None
    out = []
    for port in ADOPT_PORTS if ports is None else ports:
        if port == own_port:
            continue
        info = probe_endpoint(port, timeout=1.0)
        if info:
            out.append({k: info[k] for k in ("port", "product")})
    return out


def adopted(st: State) -> dict | None:
    rec = _read_json(adopted_path(st))
    if isinstance(rec, dict) and type(rec.get("port")) is int and 1 <= rec["port"] <= 65535:
        return rec
    return None


def offer(st: State) -> dict | None:
    """A proposed (not yet confirmed) adoption: from `adopt` or from `setup` finding a browser. declined = the owner said no."""
    rec = _read_json(offer_path(st))
    if isinstance(rec, dict) and type(rec.get("port")) is int:
        return rec
    return None


def _set_offer(st: State, port: int, product: str, by: str) -> dict:
    rec = {"port": port, "product": product, "by": by, "at": int(time.time()), "declined": False}
    _write_json(st, offer_path(st), rec)
    return rec


def adopt(st: State, endpoint, dry_run: bool = False, sites=None, _cdp=None) -> dict:
    """Propose an existing loopback CDP browser. dry_run: probe only (optionally a read-only sign-in check of template sites
    in tabs it opens and closes), write nothing. Otherwise a pending offer the owner confirms once on the phone."""
    try:
        port = parse_endpoint(endpoint)
    except BrowserError as e:
        return {"ok": False, "reason": e.reason, "detail": e.detail}
    info = probe_endpoint(port)
    if not info:
        return {"ok": False, "reason": "adopt_not_running", "port": port, "hint": HINTS["adopt_not_running"]}
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
    """The owner's signed "use it" (setup_mark adopt_browser). Re-probes, then Agent J's own browser service (if any) stops —
    the profile stays. The adopted browser itself is never started, stopped, killed or changed."""
    o = offer(st)
    if not o or o.get("declined"):
        found = detect_existing(st)
        if not found:
            return {"ok": False, "reason": "no_offer"}
        o = _set_offer(st, found[0]["port"], found[0]["product"], "checklist")
    info = probe_endpoint(o["port"])
    if not info:
        return {"ok": False, "reason": "adopt_not_running", "port": o["port"]}
    if installed(st) or service_status().get("installed"):
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


def source(st: State) -> dict:
    """What the checklist shows: whose browser Agent J uses. kind: adopted | own | offer | none."""
    ad = adopted(st)
    if ad:
        return {"kind": "adopted", "port": ad["port"], "running": bool(_probe_adopted(ad["port"]))}
    o = offer(st)
    if installed(st):
        return {"kind": "own"}
    if o and not o.get("declined"):
        return {"kind": "offer", "port": o["port"]}
    if not o:                                    # nothing installed, never asked: is one already running here?
        found = detect_existing(st)
        if found:
            return {"kind": "offer", "port": found[0]["port"]}
    return {"kind": "none"}


# ---------------------------------------------------------------- P131: bring the window to the front
def raise_window(pid: int | None, title: str | None = WINDOW_NAME) -> dict:
    """Best effort per desktop: Hyprland (hyprctl), X11 (xdotool / wmctrl), macOS (System Events). Other Wayland desktops,
    WSL and Windows cannot be forced from here — the answer says so ({"ok": False, "method": "unsupported"})."""
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
def setup(st: State, opener=None, start_service: bool = True, own: bool = False) -> dict:
    """Idempotent: respect a disabled browser, download/verify once, (re)write the service, wait for a verified start.
    P131: an adopted browser is never doubled; a CDP browser already running here is offered first (own=True: install anyway)."""
    cfg = settings(st)
    if not cfg["enabled"]:
        return {"ok": True, "result": "disabled", "note": "browser.enabled=false — `agentj browser enable` turns it back on"}
    ad = adopted(st)
    if ad:
        running = bool(probe_endpoint(ad["port"]))
        return {"ok": running, "result": "adopted", "port": ad["port"], **({} if running else {
            "reason": "adopt_not_running", "hint": HINTS["adopt_not_running"]})}
    if own:
        decline_offer(st)
    elif not installed(st):
        o = offer(st)
        found = [] if o and o.get("declined") else detect_existing(st)
        if found:
            _set_offer(st, found[0]["port"], found[0]["product"], "setup")
            return {"ok": False, "result": "existing_found", "port": found[0]["port"], "product": found[0]["product"],
                    "hint": HINTS["existing_found"].format(port=found[0]["port"])}
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
    ad = adopted(st)
    if ad and cfg["enabled"]:
        attention = [] if rt else ["adopt_not_running"]
    state = "disabled" if not cfg["enabled"] else "ready" if rt else "attention"
    out = {"ok": state != "attention", "state": state, "enabled": cfg["enabled"], "system": system(),
           "platform": platform_key(), "installed": {"version": have["version"], "platform": have["platform"]} if have else None,
           "service": svc, "attention": attention, "hints": [HINTS[a] for a in attention if a in HINTS],
           "source": source(st)}
    if rt:
        out["runtime"] = {"pid": rt["pid"], "headless": rt.get("headless"), "loopback_only": True, "source": rt.get("source", "bundled"),
                          "version": rt.get("version"), "started_at": rt.get("started_at"), "host_login": not rt.get("headless")}
    return out


def endpoint(st: State) -> dict:
    """The local CDP HTTP endpoint for tools on the Agent J host (Playwright connect_over_cdp). Never send it anywhere."""
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


ENDPOINT_NOTE = ("Local only: connect with Playwright `chromium.connect_over_cdp(endpoint)` on the Agent J host. Never send it to "
                 "chat, the cloud or another machine; open your own tabs and close only the tabs you opened. / 只在本机使用。")

HINTS = {
    "unsupported": "这台Agent J 主机的系统/架构不在支持列表（macOS、Linux x64/arm64、Windows 的 WSL2+WSLg）/ unsupported OS or CPU",
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
    "adopt_not_running": "你登记的浏览器没在运行：请在Agent J 主机上把它打开（Agent J 不会替你启动它）/ the browser you registered is not running; start it on the Agent J host",
    "existing_found": "发现你已有一个浏览器（端口 {port}），要直接用它吗？主人在手机清单里点「用它」；要另装专用的：agentj browser setup --own / "
                      "found a browser you already run (port {port}); use it from the phone checklist, or install a separate one with agentj browser setup --own",
    "shape": "用法：agentj browser adopt --endpoint http://127.0.0.1:<端口> / usage: --endpoint http://127.0.0.1:<port>",
}


# ---------------------------------------------------------------- P418 (#183 ⑤): start the browser again when it is needed
CDP_ACTIONS = ("open", "check", "endpoint")      # the actions that talk to the browser itself
WAKE_WAIT = 20.0                                 # seconds for a verified runtime after the start
RESTARTED_NOTE = "浏览器刚才没开，已重新打开。/ The browser was closed; Agent J opened it again."
ADOPT_DOWN = ("你的 Chrome 现在没开（或没开调试端口）：请在电脑上打开它；或者改用 Agent J 自己的浏览器：agentj browser adopt --forget，"
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
    """Before an action that needs the browser. None = nothing to do (running, disabled, not installed: the action answers);
    {"restarted": True}; {"reason": adopt_not_running | wake_failed, "next": …}."""
    if not settings(st).get("enabled"):
        return None
    ad = adopted(st)
    if ad:
        if _probe_adopted(ad["port"]):
            return None
        st.log("browser_wake", result="adopted_down")
        return {"reason": "adopt_not_running", "next": ADOPT_DOWN}     # the owner's browser: never started by Agent J
    if runtime(st) or not installed(st):
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


def execute(req: dict, st: State | None = None) -> dict:
    """The host side of `agentj browser …` (serve's elevate.sock, or directly when serve is not running)."""
    st = st or State()
    a = req.get("action")
    w = wake(st) if a in CDP_ACTIONS and not (a == "check" and req.get("if_stale")) else None        # P418 (#183 ⑤)
    return _woke(_execute(a, req, st), w)


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


LOGIN_MSG = {
    "sent": "已发登录二维码卡到主人的已配对手机（{ttl} 秒内有效）。请主人在手机上扫码；成功由浏览器自己确认，不要相信「扫好了」这句话。"
            "/ A sign-in QR card is on the owner's paired phone ({ttl} s). Success is confirmed by the browser itself.",
    "done": "已登录，体检已确认。/ Signed in; confirmed by a check.",
    "already": "这个网站已经是登录状态。/ Already signed in.",
    "host_login": "这个网站不支持扫码：已在这台Agent J 主机的 Agent J 浏览器打开登录页，请主人在Agent J 主机前登录，然后运行 agentj browser check --site {site}。"
                  "/ No QR for this site: the sign-in page is open on the Agent J host; the owner signs in there.",
    "host_login_unavailable": "这台Agent J 主机没有屏幕，这个网站又不支持扫码：请在有屏幕的Agent J 主机上登录，或换支持扫码的网站。/ No screen and no QR sign-in.",
    "no_qr": "登录页上没找到二维码（页面可能改版或要求别的验证）：请主人在Agent J 主机前登录。/ No QR found on the sign-in page.",
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
    for name, h in (("setup", "下载校验并启动（幂等）/ download, verify, start (idempotent)"),
                    ("status", "浏览器与各网站登录状态 / browser and site sign-in status"),
                    ("enable", "打开并启动 / turn on"), ("disable", "关闭，保留登录资料 / turn off, keep sign-ins"),
                    ("endpoint", "本机 CDP 地址（只在本机用）/ local CDP endpoint (the Agent J host only)"),
                    ("sandbox-fix", "Ubuntu 23.10+ 沙箱受限时，经手机密码卡安装 AppArmor 规则 / AppArmor profile via the phone card"),
                    ("run", "（服务内部用）常驻浏览器 / (service) keep the browser running")):
        sp = b.add_parser(name, help=h)
        sp.add_argument("--json", action="store_true")
        if name == "status":
            sp.add_argument("--registry", action="store_true", help="also print the P86 capability entries")
        if name == "setup":
            sp.add_argument("--own", action="store_true",
                            help="已有本机浏览器时仍另装专用的 / install Agent J's own even when one is already running here")
    sp = b.add_parser("adopt", help="登记你已有的本机 CDP 浏览器（只限 127.0.0.1，主人在手机确认）/ use a browser you already run")
    sp.add_argument("--endpoint", help="http://127.0.0.1:<port>")
    sp.add_argument("--dry-run", action="store_true", help="只读：探测并（加 --site）只读查登录，不登记 / probe only, register nothing")
    sp.add_argument("--site", action="append", help="with --dry-run: read-only sign-in check of a template site (repeatable)")
    sp.add_argument("--forget", action="store_true", help="不再用登记的浏览器（它继续运行）/ stop using it (it keeps running)")
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
