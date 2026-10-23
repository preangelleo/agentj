"""P484 field delivery: local, bounded diagnostics and read-only WSL preflight.

Export is a strict projection, not a claim that arbitrary free text can be scrubbed.
No upload, raw log, config, identity, URL or caller-selected collection command.
"""
from __future__ import annotations

import collections
import ctypes.util
import json
import os
import platform
from pathlib import Path
import re
import sys
import urllib.request

EVENTS = frozenset(("serve_start", "serve_stop", "relay_up", "relay_down",
                   "upgrade_health_failed", "upgrade_done", "upgrade_failed", "upgrade_rollback", "auth_fail", "error"))
CHECK_IDS = frozenset(("version", "python", "websockets", "platform", "state", "browser", "relay", "dashboard",
                       "agent", "serve", "service", "seat", "bound", "fence", "upgrade-restart", "root-structure", "main-inject"))
STATES = frozenset(("ok", "warn", "fail"))
LIBS = ("nss3", "nspr4", "atk-1.0", "atk-bridge-2.0", "cups", "drm", "dbus-1",
        "X11", "Xcomposite", "Xdamage", "Xext", "Xfixes", "Xrandr", "gbm", "xcb",
        "xkbcommon", "asound", "pango-1.0", "cairo", "glib-2.0")


def log_summary(path):
    counts = collections.Counter()
    try:
        # Fixed file, no symlink traversal; cap input even on a large service log.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as f:
            import stat
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                return {"available": False}
            size = os.fstat(f.fileno()).st_size
            f.seek(max(0, size - 65536))
            data = f.read(65536)
        for line in data.splitlines():
            try:
                row = json.loads(line)
            except (ValueError, UnicodeError, RecursionError):
                continue
            event = row.get("ev") if isinstance(row, dict) else None
            counts[event if isinstance(event, str) and event in EVENTS else "other"] += 1
        return {"available": True, "tail_bytes": len(data), "events": dict(counts)}
    except OSError:
        return {"available": False}


def collect(st, *, local=False):
    from . import host_diagnostics
    if not local and host_diagnostics.agent_side():
        from . import elevate, names
        try:
            result = elevate.client_request(st, {"t": "host_diagnostics", "op": "diag"}, timeout=90,
                                            path=os.environ.get(elevate.ENV) or names.elevate_sock(st))
        except Exception:
            raise OSError("diagnostics unavailable") from None
        if not isinstance(result, dict) or result.get("exit") != 0:
            raise OSError("diagnostics unavailable")
        try:
            return json.loads(result["output"])
        except (KeyError, ValueError, TypeError):
            raise OSError("diagnostics unavailable") from None
    from . import __version__, doctor, names, service
    try:
        rows = doctor.run(st, offline=True)
        # Allowlisted check names only: even a compromised check cannot echo PII.
        checks = [{"check": r.get("id") if r.get("id") in CHECK_IDS else "other", "status": r.get("status") if r.get("status") in STATES else "unknown"}
                  for r in rows]
    except Exception:
        checks = [{"status": "unavailable"}]
    try:
        svc = service.status()
        service_state = svc.get("active") if svc.get("active") in ("active", "inactive", "failed", "not_loaded") else "unknown"
    except Exception:
        service_state = "unavailable"
    try:
        status = names.ctl_call(st, {"cmd": "status"}, 3) or {}
        status = {k: status.get(k) if type(status.get(k)) is bool else None
                  for k in ("ok", "relay_up", "identity_ok")}
    except Exception:
        status = {"available": False}
    return {"format": 1, "version": __version__, "platform": sys.platform,
            "doctor": checks, "status": status, "service": service_state,
            "service_log_summary": log_summary(st.log_path)}


def export(st, output):
    data = (json.dumps(collect(st), ensure_ascii=False, indent=2) + "\n").encode()
    # New private file only: never overwrite an existing file or follow a destination link.
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return 0


def download_probe(url):
    # Public fixed destinations only. HEAD avoids downloading browser/model payloads.
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "AgentJ-preflight"})
        with urllib.request.urlopen(req, timeout=6) as response:
            return "ok" if 200 <= response.status < 400 else "failed"
    except Exception:
        return "unreachable"


def preflight(*, offline=False):
    from . import browser, doctor
    if not doctor._is_wsl():
        return {"wsl": False, "checks": [], "note": "仅 WSL / WSL only"}
    missing = [lib for lib in LIBS if not ctypes.util.find_library(lib)]
    # Ubuntu 24.04 renamed these ABI packages; avoid suggesting unavailable old names.
    try:
        distro = Path("/etc/os-release").read_text()
    except OSError:
        distro = ""
    ubuntu = 'ID=ubuntu' in distro or 'ID="ubuntu"' in distro
    modern = bool(re.search(r'VERSION_ID="?(24|25|26)\.', distro))
    suffix = "t64" if modern else ""
    packages = ("libnss3 libnspr4 libatk1.0-0{t} libatk-bridge2.0-0{t} libcups2{t} libdrm2 "
                "libdbus-1-3 libx11-6 libxcomposite1 libxdamage1 libxext6 libxfixes3 libxrandr2 "
                "libgbm1 libxcb1 libxkbcommon0 libasound2{t} libpango-1.0-0 libcairo2 libglib2.0-0{t}").format(t=suffix)
    fix = "sudo apt-get update && sudo apt-get install -y " + packages if ubuntu else "请按发行版安装浏览器系统依赖 / install browser system dependencies for this distribution"
    man = browser.manifest()
    build = "linux-arm64" if platform.machine() in ("aarch64", "arm64") else "linux-x64"
    urls = {"browser_download": man["builds"][build]["url"], "ollama_download": f"https://ollama.com/download/ollama-linux-{'arm64' if build == 'linux-arm64' else 'amd64'}.tar.zst"}
    checks = [{"id": "browser_libraries", "status": "fail" if missing else "ok", "missing": missing}]
    checks += [{"id": name, "status": "skipped" if offline else download_probe(url)} for name, url in urls.items()]
    return {"wsl": True, "checks": checks, "repair": fix,
            "next": "agentj browser setup", "note": "HEAD 仅验证入口连通，不证明完整下载；不执行修复 / HEAD checks reachability only; repairs are not run"}


def command(a):
    if a.cmd == "diag":
        from .state import State
        try:
            export(State(), a.output)
        except OSError:
            print("导出失败：目标须为可写的新文件 / export failed: choose a writable new file")
            raise SystemExit(1)
        print("已导出脱敏诊断 JSON；请在本机审阅后分享 / diagnostic JSON exported; review locally before sharing")
        return 0
    result = preflight(offline=a.offline)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(int(any(c["status"] in ("fail", "failed", "unreachable") for c in result["checks"])))


def add_parser(sub):
    diag = sub.add_parser("diag", help="导出脱敏诊断包 / export private diagnostic summary")
    sp = diag.add_subparsers(dest="action", required=True)
    ex = sp.add_parser("export", help="本机 JSON，无原始日志 / local JSON, no raw logs")
    ex.add_argument("--output", default="agentj-diagnostic.json", help="新文件，禁止覆盖 / new file, never overwritten")
    diag.set_defaults(fn=command)
    from . import layout_diag
    layout_diag.add_parser(sub, diagnostics=sp)
    flight = sub.add_parser("preflight", help="WSL 浏览器系统库与下载预检 / WSL browser libraries and download preflight")
    flight.add_argument("--offline", action="store_true", help="跳过下载连通性 / skip download reachability")
    flight.set_defaults(fn=command)
