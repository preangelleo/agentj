"""`agentj update apply --wheel FILE --version V`: the owner's own terminal installs a local wheel through the /update path.

Release testing (R-0032) has to prove that the very entry the phone's /update uses — a detached service-manager job
running `phone_update.run` → `update.apply` → install → `service install --deferred` — really upgrades and restarts a
host, with a candidate that is not published yet. This module adds nothing to that path: it only swaps the published-
wheel download (`update.verified_plan`) for a copy of the owner's local file and the latest-version check for the asked
version. Owner terminal only: refused inside the Agent fence and from an Agent's environment.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import time
import zipfile
from pathlib import Path

from . import update

WHEEL_ENV = "AGENTJ_LOCAL_WHEEL"
TARGET_ENV = "AGENTJ_LOCAL_TARGET"
FOLDER = "local-update"
WAIT = 1500     # s: the job installs, runs doctor and hands the restart off well within this


class Refused(Exception):
    pass


def _wheel_version(path: Path) -> str | None:
    try:
        with zipfile.ZipFile(path) as z:
            text = z.read("agentj/__init__.py").decode("utf-8", "replace")
    except (OSError, KeyError, zipfile.BadZipFile):
        return None
    m = update._VER_RE.search(text)
    return m.group(1) if m else None


def stage(st, wheel: str, target: str) -> Path:
    """Copy the owner's wheel into the private state folder (0400) after checking it is what it says it is."""
    from . import ctl_auth, fence
    if fence.inside() or set(os.environ) & set(ctl_auth.AGENT_ENV):
        raise Refused("owner_terminal_only")
    if update.parse(target) is None:
        raise Refused("bad_version")
    src = Path(wheel)
    try:
        meta = src.lstat()
    except OSError:
        raise Refused("wheel_missing") from None
    if not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.geteuid():
        raise Refused("wheel_not_owned")
    if src.name != f"agentj-{target}-py3-none-any.whl" or _wheel_version(src) != target:
        raise Refused("wheel_version_mismatch")
    folder = st.root / FOLDER
    folder.mkdir(mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)
    for old in folder.glob("*.whl"):
        old.unlink(missing_ok=True)
    dst = folder / src.name
    shutil.copyfile(src, dst)
    os.chmod(dst, 0o400)
    if _wheel_version(dst) != target:
        dst.unlink(missing_ok=True)
        raise Refused("wheel_version_mismatch")
    return dst


def apply_fn(wheel: Path, target: str):
    """`update.apply` exactly as /update runs it, with the local wheel as the plan and `target` as the version asked."""
    from . import auto_update

    def check():
        return {"current": update.__version__, "latest": target, "status": "newer", "why": None}

    def plan(st, info, to):
        return auto_update.install_commands(info, wheel)
    return lambda st, **kw: update.apply(st, target, check_fn=check, plan_fn=plan, **kw)


def launch(st, wheel: Path, target: str) -> None:
    """The same detached job as phone_update.launch, with the staged wheel named in the job's environment."""
    from . import phone_update, service_recovery
    if not phone_update.reserve(st):
        raise Refused("upgrade_in_progress")
    (st.root / phone_update.RESULT).unlink(missing_ok=True)
    try:
        service_recovery.launch_job(st, [sys.executable, "-m", "agentj.local_update"], "update",
                                    extra_env={WHEEL_ENV: str(wheel), TARGET_ENV: target})
    except Exception:
        phone_update.release(st)
        raise


def wait_result(st, limit: float = WAIT, sleep=time.sleep) -> dict | None:
    """The job's durable result (left in place: the restarted serve still reports it on the phone)."""
    from . import phone_update
    end = time.monotonic() + limit
    while time.monotonic() < end:
        try:
            return json.loads((st.root / phone_update.RESULT).read_text())
        except (OSError, ValueError):
            sleep(2)
    return None


def main() -> None:
    from . import phone_update
    from .state import State
    wheel, target = os.environ.get(WHEEL_ENV, ""), os.environ.get(TARGET_ENV, "")
    st = State()
    if not wheel or update.parse(target) is None or Path(wheel).parent != st.root / FOLDER:
        st.write_private(st.root / phone_update.RESULT, json.dumps({"result": "failed", "reason": "bad_local_job"}).encode())
        phone_update.release(st)
        return
    phone_update.run(st, apply_fn=apply_fn(Path(wheel), target))


if __name__ == "__main__":
    main()
