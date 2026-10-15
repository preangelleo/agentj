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

def text(rec, lang="zh", web="older", machine=None):
    if rec.get("reason") in ("upgraded", "doctor_failed"):
        return update.completion_text(rec, rec.get("counts"), lang, rec.get("checks"), web, machine)
    if rec.get("reason") == "already_current":
        from . import humane
        return humane.say("update.current", lang, to=rec["to"])
    reason = str(rec.get("reason", "unknown"))
    if rec.get("tell_owner"):          # P277: the plain sentence (what failed, where the computer is, whether support has it)
        from . import upgrade_diag
        return upgrade_diag.owner_sentence(rec, _rep_of(rec), lang)
    return (f"Upgrade failed ({reason}). Run `agentj update check`, then `agentj update apply` on the computer." if lang == "en" else
            f"升级未完成（{reason}）。在电脑运行 `agentj update check`，再运行 `agentj update apply` 重试。")

def _rep_of(rec):
    import re
    s = str(rec.get("support") or "")
    m = re.search(r"st_[A-Za-z0-9_-]{22}", s)
    if m:
        return {"status": "sent", "thread": m.group(0)}
    return {"status": "off"} if s.startswith("off") else {"status": "not_sent"}


def _lang(st):
    try:
        from . import preferences
        return "en" if preferences.get(preferences.effective(st), "appearance.language", "zh") == "en" else "zh"
    except Exception:  # noqa: BLE001
        return "zh"


def run(st, apply_fn=None, restart_fn=None):
    from . import doctor, service
    def quiet(argv, **kw):
        kw.pop("capture_output", None)
        return subprocess.run(argv, **{**kw, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
                                      "text": True, "timeout": 900})
    try:
        rec = (apply_fn or update.apply)(st, svc_on=False, run=quiet)
        if rec.get("reason") in ("upgraded", "doctor_failed"):
            rows = doctor.run(st)
            rec["counts"] = update.doctor_counts(rows)
            rec["checks"] = [{"id": r.get("id"), "status": r.get("status")} for r in rows]
        if rec.get("result") != "ok" and rec.get("step"):
            # P277: a failed phone upgrade reports itself (metadata + redacted installer tail) unless switched off
            from . import upgrade_diag
            upgrade_diag.after_failure(st, rec, lang=_lang(st))
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
