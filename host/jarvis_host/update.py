"""`jarvis update check|apply|auto` (L3, Z4: the customer decides when new code arrives).

Where the latest version comes from: the public repo the host is installed from — `host/jarvis_host/__init__.py` on
`main`, fetched from raw.githubusercontent.com and parsed as text (never executed). Not a file on our own site: that would
give us a daily request from every host (IP + version) — GitHub sees it instead of us, and it is the same source
`uv tool upgrade` / `pipx` will install from, so "newer" here means "what the upgrade command would get". A network failure
is "unknown", never an error.

Who installs: only a human at a terminal. `check` (any caller, the Agent included) prints the command that fits how this
host was installed (uv tool · pipx · source checkout · pip); `apply` runs it only after a y typed on an interactive terminal
— no `--yes`, a pipe or a file on stdin is refused. Inside the fence the Agent cannot run it anyway: jarvis's code and venv
are read-only there. The terminal check is a statement of the rule, not a security boundary (a program can fake a
terminal); the boundary for the phone's Agent is the fence. After an upgrade an installed service is re-installed (restart
with the new code). `serve` checks once a day and tells the phones — it never installs.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from . import DIST, __version__

REPO = "https://github.com/preangelleo/agentjarvis"
SPEC = f"git+{REPO}#subdirectory=host"
LATEST_URL = "https://raw.githubusercontent.com/preangelleo/agentjarvis/main/host/jarvis_host/__init__.py"
URL_ENV = "AGENTJARVIS_UPDATE_URL"     # tests / mirrors; "off" disables every check
TIMEOUT = 6
MAX_BYTES = 64 * 1024
DAY = 24 * 3600
_VER_RE = re.compile(r'^__version__\s*=\s*["\']([0-9A-Za-z.+-]{1,32})["\']\s*$', re.M)
_PARSE_RE = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?(?:(a|b|rc)(\d+))?(?:\.dev(\d+))?$")


def parse(v: str) -> tuple | None:
    """PEP 440 subset used by this project: 0.8.0, 0.8.0a1, 0.8.0b2, 0.8.0rc1, 0.8.0.dev3. None = not understood."""
    m = _PARSE_RE.match((v or "").strip())
    if not m:
        return None
    major, minor, patch, pre, pre_n, dev = m.groups()
    rank = {"a": 0, "b": 1, "rc": 2, None: 3}[pre]
    return (int(major), int(minor), int(patch or 0), -1 if dev is not None and pre is None else rank,
            int(pre_n or 0), int(dev) if dev is not None else 1 << 30)


def compare(a: str, b: str) -> int | None:
    """-1 / 0 / 1, or None when either version is not understood."""
    pa, pb = parse(a), parse(b)
    if pa is None or pb is None:
        return None
    return (pa > pb) - (pa < pb)


def latest_url() -> str | None:
    u = os.environ.get(URL_ENV) or LATEST_URL
    if u == "off":
        return None
    p = urllib.parse.urlparse(u)
    if p.scheme == "https" or (p.scheme == "http" and p.hostname in ("127.0.0.1", "localhost")):
        return u
    return None


def fetch_latest(timeout: float = TIMEOUT) -> tuple[str | None, str]:
    """(version, "ok") or (None, why): "off" · "network" · "http_<code>" · "unparsable"."""
    url = latest_url()
    if not url:
        return None, "off"
    req = urllib.request.Request(url, headers={"user-agent": f"{DIST}/{__version__} (update check)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        e.close()
        return None, f"http_{e.code}"
    except Exception:  # noqa: BLE001 — offline, DNS, TLS, proxy, timeout: all mean "could not find out"
        return None, "network"
    m = _VER_RE.search(body[:MAX_BYTES].decode("utf-8", "replace"))
    if not m or parse(m.group(1)) is None:
        return None, "unparsable"
    return m.group(1), "ok"


# ------------------------------------------------------------------ how this copy was installed
def install_kind(prefix: str | None = None) -> dict:
    """{"kind": "uv" | "pipx" | "checkout" | "pip", "where": path, ...}. Decided by files the installers leave in the venv
    (uv-receipt.toml / pipx_metadata.json), else a source checkout (host/ with the launcher), else a plain pip venv."""
    prefix = prefix or sys.prefix
    if os.path.isfile(os.path.join(prefix, "uv-receipt.toml")):
        return {"kind": "uv", "where": prefix}
    if os.path.isfile(os.path.join(prefix, "pipx_metadata.json")):
        spec = SPEC
        try:
            with open(os.path.join(prefix, "pipx_metadata.json")) as f:
                m = json.load(f).get("main_package") or {}
            if isinstance(m.get("package_or_url"), str) and m["package_or_url"]:
                spec = m["package_or_url"]
        except (OSError, ValueError, AttributeError):
            pass
        return {"kind": "pipx", "where": prefix, "spec": spec}
    from .agent import source_root
    src = source_root()
    if src and os.path.isfile(os.path.join(src, "jarvis")) and os.path.isfile(os.path.join(src, "pyproject.toml")):
        return {"kind": "checkout", "where": src}
    return {"kind": "pip", "where": prefix}


def commands(info: dict) -> list[list[str]]:
    """The upgrade, as argv lists run one after the other (stop at the first failure)."""
    k = info["kind"]
    if k == "uv":
        return [[shutil.which("uv") or "uv", "tool", "upgrade", DIST]]
    if k == "pipx":
        return [[shutil.which("pipx") or "pipx", "install", "--force", info.get("spec") or SPEC]]
    if k == "checkout":
        src = info["where"]
        return [["git", "-C", src, "pull", "--ff-only"], [shutil.which("uv") or "uv", "sync", "--project", src]]
    return [[os.path.join(info["where"], "bin", "python"), "-m", "pip", "install", "--upgrade", SPEC]]


def command_text(info: dict) -> str:
    return " && ".join(shlex.join(c) for c in commands(info))


def check(timeout: float = TIMEOUT) -> dict:
    """{"current", "latest", "status": "newer" | "current" | "ahead" | "unknown", "why", "install", "command"}."""
    latest, why = fetch_latest(timeout)
    info = install_kind()
    c = compare(latest, __version__) if latest else None
    status = "unknown" if c is None else {1: "newer", 0: "current", -1: "ahead"}[c]
    out = {"current": __version__, "latest": latest, "status": status, "why": why if status == "unknown" else "ok",
           "install": info["kind"], "command": command_text(info)}
    return out


# ------------------------------------------------------------------ daily check in `serve` (state: update.json, 0600)
def _rec_path(st):
    return st.root / "update.json"


def read_rec(st) -> dict:
    try:
        d = json.loads(_rec_path(st).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def auto_enabled(st) -> bool:
    try:
        return st.config().get("update_check", True) is not False and latest_url() is not None
    except (OSError, ValueError):
        return False


def daily(st, now: float | None = None, fetch=fetch_latest) -> str | None:
    """At most one check per 24 h (persisted, so restarts do not re-check). Returns the phone notice text when a newer
    version shows up that the phones were not told about yet, else None."""
    now = time.time() if now is None else now
    rec = read_rec(st)
    if not auto_enabled(st) or now - float(rec.get("checked") or 0) < DAY:
        return None
    latest, why = fetch()
    rec.update({"checked": int(now), "latest": latest, "why": why})
    note = None
    if latest and compare(latest, __version__) == 1 and rec.get("notified") != latest:
        rec["notified"] = latest
        note = notice_text(latest)
    st.write_private(_rec_path(st), json.dumps(rec).encode())
    return note


def notice_text(latest: str) -> str:
    return (f"Agent Jarvis 主机程序有新版本 {latest}（这台电脑是 {__version__}）。不会自动安装：要升级时，在这台电脑的终端运行 "
            f"`jarvis update apply`（会先给你看命令、问你确认）。/ A new host version {latest} is available (this computer: "
            f"{__version__}). Nothing is installed automatically: run `jarvis update apply` in a terminal on this computer.")


# ------------------------------------------------------------------ apply (human at a terminal only)
class Refused(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def preflight(stdin=None, stdout=None, prefix: str | None = None) -> None:
    """Raise Refused unless a human can confirm here and this copy can be written."""
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    if not (stdin.isatty() and stdout.isatty()):
        raise Refused("no_terminal")
    if not os.access(prefix or sys.prefix, os.W_OK):
        raise Refused("read_only")


REFUSED = {
    "no_terminal": "✗ 升级只能由人在终端里确认后执行（没有终端：可能是 Agent、脚本或管道在调用）。把 `jarvis update check` 给出的命令告诉人，"
                   "由人在自己的终端运行 `jarvis update apply`。/ An upgrade needs a human at an interactive terminal: tell your human "
                   "the command from `jarvis update check`; they run `jarvis update apply` themselves.",
    "read_only": "✗ 程序目录是只读的（在 Agent 隔离里运行？），这里装不了。/ jarvis's own files are read-only here (inside the "
                 "Agent fence?): run it in your own terminal.",
}
