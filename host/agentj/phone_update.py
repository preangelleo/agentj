"""Owner-signed phone upgrades; detached from the service being replaced."""
import json
import os
import subprocess
import sys
import time
import uuid
from . import update

RESULT = "phone-update-result.json"
LOCK = "phone-update-running.json"

def launch(st):
    from . import service_recovery
    service_recovery.launch_job(st, [sys.executable, "-m", "agentj.phone_update"], "update")

def busy(st):
    try:
        return time.time() - json.loads((st.root / LOCK).read_text())["at"] < 1800
    except (OSError, ValueError, KeyError, TypeError):
        return False

def reserve(st):
    if busy(st):
        return False
    st.write_private(st.root / LOCK, json.dumps({"at": time.time()}).encode())
    return True

def release(st):
    (st.root / LOCK).unlink(missing_ok=True)

def take(st):
    p = st.root / RESULT
    try:
        rec = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    # Only the newly installed service may consume successful completion.
    from . import __version__
    if rec.get("reason") in ("upgraded", "doctor_failed") and rec.get("to") != __version__:
        return None
    p.unlink()
    return rec

def text(rec, lang="zh"):
    if rec.get("reason") in ("upgraded", "doctor_failed"):
        counts = rec.get("counts")
        doc = f"doctor {counts[0]}✓/{counts[1]}!/{counts[2]}✗" if counts else "doctor ?"
        suffix = ("; run `agentj doctor` to fix the failed checks" if lang == "en" else "；运行 `agentj doctor` 修复未通过项") if rec.get("reason") == "doctor_failed" else ""
        if rec.get("existing_issues") and rec.get("reason") != "doctor_failed":
            suffix = "; upgrade succeeded; other existing issues need `agentj doctor`" if lang == "en" else "；升级成功，另有已有问题，请用 `agentj doctor` 检查"
        return (f"Upgraded from {rec['from']} to {rec['to']}; {doc}" if lang == "en" else
                f"已从 {rec['from']} 升到 {rec['to']}，{doc}") + suffix
    if rec.get("reason") == "already_current":
        return f"Already up to date: {rec['to']}" if lang == "en" else f"已是最新 {rec['to']}"
    reason = str(rec.get("reason", "unknown"))
    return (f"Upgrade failed ({reason}). Run `agentj update check`, then `agentj update apply` on the computer." if lang == "en" else
            f"升级未完成（{reason}）。在电脑运行 `agentj update check`，再运行 `agentj update apply` 重试。")

def run(st, apply_fn=None, restart_fn=None):
    from . import doctor, service
    def quiet(argv, **kw):
        kw.pop("capture_output", None)
        return subprocess.run(argv, **{**kw, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
                                      "text": True, "timeout": 900})
    try:
        rec = (apply_fn or update.apply)(st, svc_on=False, run=quiet)
        if rec.get("reason") in ("upgraded", "doctor_failed"):
            rec["counts"] = update.doctor_counts(doctor.run(st))
        st.write_private(st.root / RESULT, json.dumps(rec).encode())
        # The durable result, not the generic marker, owns this phone request.
        (st.root / update.UPGRADED).unlink(missing_ok=True)
        if rec.get("reason") in ("upgraded", "doctor_failed"):
            if restart_fn:
                restart_fn()
            else:
                # Use the freshly installed CLI; manager owns the restart worker.
                argv = update.new_argv(update.install_kind())
                r = quiet(argv + ["service", "install", "--deferred", rec["to"]], stdin=subprocess.DEVNULL)
                if r.returncode:
                    raise service.ServiceError("recovery_launch_failed")
    except Exception as e:
        st.write_private(st.root / RESULT, json.dumps({"result": "failed", "reason": type(e).__name__}).encode())
    finally:
        release(st)
        from . import service_recovery
        service_recovery.cleanup_job(st)

if __name__ == "__main__":
    from .state import State
    run(State())
