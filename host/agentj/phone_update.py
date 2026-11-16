"""Owner-signed phone upgrades; detached from the service being replaced."""
import contextlib
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
    if update.market_managed():
        return False
    from . import auto_update, service_recovery
    # 0.30.23: the job runs from a snapshot of this package — the install replaces the venv under it (see snapshot_worker)
    worker = auto_update.snapshot_worker(st, "phone-lib", "agentj.phone_update")
    service_recovery.launch_job(st, [sys.executable, str(worker)], "update")

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
    if rec.get("reason") == "market_managed":
        return update.market_text(lang)
    if rec.get("reason") in ("upgraded", "doctor_failed"):
        return update.completion_text(rec, rec.get("counts"), lang, rec.get("checks"), web, machine)
    if rec.get("reason") == "already_current":
        from . import humane
        return humane.say("update.current", lang, to=rec["to"])
    from . import upgrade_diag
    reason = rec.get("reason")
    if rec.get("tell_owner"):          # P277: the plain sentence (what failed, where the Agent J host is, whether support has it)
        return upgrade_diag.owner_sentence(rec, _rep_of(rec), lang)
    en = lang == "en"
    # Receipt fields may come from an older updater. Only fixed vocabulary crosses into owner-visible text.
    if reason == "unknown":
        why = "could not check the latest version (network unavailable or the release site unreachable)" if en else "暂时查不到新版（网络不通或发布站连不上）"
    elif (rec.get("step") == "download" and rec.get("errno") == 13
          and rec.get("cache_status") in ("miss", "invalid")):
        why = "the upgrade cache is not writable" if en else "升级缓存文件夹没有写权限"
    else:
        why = upgrade_diag.REASON_WORDS.get(reason, ("升级过程遇到问题", "the upgrade could not finish"))[en]
    import re
    err = rec.get("error")
    if isinstance(err, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,48}", err):   # a type name only, never its message
        why += f" ({err})" if en else f"（{err}）"
    if rec.get("installed") and reason == "service_restart_failed":
        why += (f"; {rec['installed']} is installed, the old version is still running" if en else
                f"；{rec['installed']} 已装好，仍在运行旧版本")
    return (f"Upgrade failed: {why}. Use Repair connection below, or Settings → Emergency repair. If the host is offline, wake that computer first." if en else
            f"升级未完成：{why}。使用下方「修复连接」，或前往设置中的「紧急修复」。主机离线时，请先唤醒 Agent J 主机。")

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


def _restart(rec, quiet):
    """Hand the restart to the freshly installed CLI (`service install --deferred`): its recovery worker waits for an idle
    serve, restarts the service and reads the live version back. → None, or the real failure reason."""
    from . import service
    argv = update.new_argv(update.install_kind())
    try:
        r = quiet(argv + ["service", "install", "--deferred", rec["to"]], stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        return {"reason": "service_restart_failed", "service_error": type(e).__name__}
    if r.returncode == service.NO_MANAGER_EXIT:
        return {"reason": "service_restart_failed", **update.no_manager(r.stderr or "")}
    if r.returncode:
        from .upgrade_diag import tail
        return {"reason": "service_restart_failed", "stderr_tail": tail(r.stderr or "")}
    return None


def run(st, apply_fn=None, restart_fn=None):
    if update.market_managed():
        return update.market_result()
    from . import doctor, upgrade_diag
    def quiet(argv, **kw):
        kw.pop("capture_output", None)
        return subprocess.run(argv, **{**kw, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
                                      "text": True, "timeout": 900})
    rec = None
    try:
        try:
            rec = (apply_fn or update.apply)(st, svc_on=False, run=quiet)
        except Exception as e:  # noqa: BLE001 — the apply itself broke: its step stays unknown, the type is the reason
            rec = {"result": "failed", "reason": "apply_crashed", "error": type(e).__name__}
        installed = rec.get("reason") in ("upgraded", "doctor_failed")
        if installed:
            # Best effort only: nothing after the install may cancel the restart hand-off below (0.30.23: an in-process
            # doctor crash on M1 + Olares skipped it and the old serve kept running on the new package files).
            try:
                rows = doctor.run(st)
                rec["counts"] = update.doctor_counts(rows)
                rec["checks"] = [{"id": r.get("id"), "status": r.get("status"), **({"session_disconnected": True} if r.get("session_disconnected") is True else {}), **({"summary": "no verified main Agent launch"} if update.benign_waiting(r) else {})} for r in rows]
            except Exception as e:  # noqa: BLE001
                st.log("phone_update_doctor", status="failed", reason=type(e).__name__)
        if rec.get("result") != "ok" and (rec.get("step") or rec.get("reason") == "not_owner"):   # #475: not_owner → fix sentence
            # P277: a failed phone upgrade reports itself (metadata + redacted installer tail) unless switched off
            with contextlib.suppress(Exception):
                upgrade_diag.after_failure(st, rec, lang=_lang(st))
        st.write_private(st.root / RESULT, json.dumps(rec).encode())
        # The durable result, not the generic marker, owns this phone request.
        (st.root / update.UPGRADED).unlink(missing_ok=True)
        if installed:
            failed = None
            if restart_fn:
                restart_fn()
            else:
                failed = _restart(rec, quiet)
            if failed:
                # The new package is on disk but the old service keeps running: say so (never 「升级成功」, never a bare type).
                rec = {**rec, **failed, "result": "failed", "installed": rec.get("to"), "exit": 1}
                st.log("phone_update_restart", status="failed", reason=failed["reason"])
                st.write_private(st.root / RESULT, json.dumps(rec).encode())
    except Exception as e:  # noqa: BLE001
        st.write_private(st.root / RESULT, json.dumps({**(rec or {}), "result": "failed", "reason": "phone_update_crashed",
                                                       "error": type(e).__name__}).encode())
    finally:
        release(st)
        from . import service_recovery
        service_recovery.cleanup_job(st)

if __name__ == "__main__":
    from .state import State
    run(State())
