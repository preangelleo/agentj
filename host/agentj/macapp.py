"""P327: the signed + notarized "Agent J.app" that carries Agent J's macOS LaunchAgents.

macOS lists every LaunchAgent under System Settings → Login Items → App Background Activity by its ProgramArguments[0]:
an unsigned uv-installed `agentj` shows as "agentj · Item from unidentified developer". The plist instead starts
`~/Applications/Agent J.app/Contents/MacOS/Agent J` (a tiny Developer ID–signed launcher that execs the real agentj, same
pid) and names the app in AssociatedBundleIdentifiers → the row reads "Agent J", with its icon and the developer's name.

The app is not in the wheel: it is built, signed and notarized on a Mac at release time (agentjarvis/release/macos/sign.py)
and published under /dl next to the wheel; macapp.json pins its version, size and SHA-256. Any failure here — no pin yet,
offline, wrong bytes, a broken signature, not macOS — returns None and the service is written exactly as before
(unsigned argv). Never raises; never blocks an install or an upgrade."""
from __future__ import annotations
import contextlib
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

BUNDLE_ID = "app.agentj.agent"
APP_NAME = "Agent J.app"
EXEC = ("Contents", "MacOS", "Agent J")
MIRRORS = ("https://agentj.app/dl/{f}",)     # https only, no environment override; the SHA-256 pin decides anyway
OFF_ENV = "AGENTJ_MACAPP"         # "off" → never use the app (support escape hatch: the plain unsigned service)
TIMEOUT = 20
LSREGISTER = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"


def pin() -> dict:
    try:
        return json.loads((Path(__file__).with_name("macapp.json")).read_text())
    except (OSError, ValueError):
        return {}


def app_path() -> Path:
    return Path(os.path.expanduser("~")) / "Applications" / APP_NAME


def exe_path(app: Path | None = None) -> Path:
    return (app or app_path()).joinpath(*EXEC)


def _version(app: Path) -> str | None:
    try:
        return str(plistlib.loads((app / "Contents" / "Info.plist").read_bytes()).get("CFBundleVersion"))
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None


def requirement(p: dict) -> str:
    """Apple-anchored Developer ID chain, our bundle id, the pinned Team ID (leaf certificate OU) — checked by codesign
    itself, not by reading its text output."""
    return (f'anchor apple generic and identifier "{BUNDLE_ID}" and certificate leaf[subject.OU] = "{p.get("team")}"')


def verified(app: Path, p: dict) -> bool:
    """The bundle is the pinned version, intact and signed for us (codesign --verify --strict against requirement())."""
    if not exe_path(app).is_file() or _version(app) != str(p.get("version")) or not str(p.get("team") or "").isalnum():
        return False
    try:
        return subprocess.run(["codesign", "--verify", "--deep", "--strict", f"-R={requirement(p)}", str(app)],
                              capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _download(p: dict) -> bytes | None:
    for u in MIRRORS:
        with contextlib.suppress(Exception):
            with urllib.request.urlopen(u.format(f=p["file"]), timeout=TIMEOUT) as r:
                data = r.read(int(p["size"]) + 1)
            if len(data) == int(p["size"]) and hashlib.sha256(data).hexdigest() == p["sha256"]:
                return data
    return None


def ensure(log=None) -> Path | None:
    """→ the launcher to put first in ProgramArguments, or None (= write the plain unsigned service). macOS only."""
    p = pin()
    if sys.platform != "darwin" or os.environ.get(OFF_ENV) == "off" or not (p.get("version") and p.get("sha256")):
        return None
    app = app_path()
    if verified(app, p):
        return exe_path(app)
    data = _download(p)
    if data is None:
        _note(log, "macapp_unavailable", "download")
        return None
    try:
        app.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=app.parent, prefix=".agentj-app-") as tmp:
            z = Path(tmp) / p["file"]
            z.write_bytes(data)
            subprocess.run(["ditto", "-x", "-k", str(z), tmp], check=True, capture_output=True, timeout=60)
            new = Path(tmp) / APP_NAME
            if not verified(new, p):
                _note(log, "macapp_unavailable", "signature")
                return None
            if app.exists() or app.is_symlink():
                shutil.rmtree(app) if app.is_dir() and not app.is_symlink() else app.unlink()
            os.replace(new, app)
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run([LSREGISTER, "-f", str(app)], capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as e:
        _note(log, "macapp_unavailable", type(e).__name__)
        return None
    return exe_path(app) if verified(app, p) else None


def wrap(argv: list[str], log=None) -> tuple[list[str], dict]:
    """(ProgramArguments prefix, extra plist keys): through the signed app when it is available, else unchanged."""
    exe = ensure(log)
    if exe is None:
        return argv, {}
    try:      # the launcher accepts only agentj in a venv, owner-only writable: prove it starts this one, else unsigned
        ok = subprocess.run([str(exe), *argv, "--version"], capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    if not ok:
        _note(log, "macapp_unavailable", "launch")
        return argv, {}
    return [str(exe), *argv], {"AssociatedBundleIdentifiers": [BUNDLE_ID]}


def remove_if_unused() -> bool:
    """Uninstall: delete the app once no Agent J LaunchAgent (any instance, the browser) still starts through it."""
    app = app_path()
    if not app.exists():
        return False
    la = Path(os.path.expanduser("~")) / "Library" / "LaunchAgents"
    marker = str(exe_path(app)).encode()
    with contextlib.suppress(OSError):
        for f in la.glob("net.agentj.*.plist"):
            with contextlib.suppress(OSError):
                if marker in f.read_bytes():
                    return False
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run([LSREGISTER, "-u", str(app)], capture_output=True, timeout=15)
    shutil.rmtree(app, ignore_errors=True)
    return not app.exists()


def _note(log, event: str, why: str) -> None:
    with contextlib.suppress(Exception):
        if log:
            log(event, why=why)
