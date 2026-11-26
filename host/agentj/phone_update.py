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
    return launch_target(st, None)


def launch_target(st, target, job=None):
    if update.market_managed():
        return False
    from . import auto_update, service_recovery
    # 0.30.23: the job runs from a snapshot of this package — the install replaces the venv under it (see snapshot_worker)
    worker = auto_update.snapshot_worker(st, "phone-lib", "agentj.phone_update", **({"target": target, "job": job} if target else {}))
    service_recovery.launch_job(st, [sys.executable, str(worker)], "update")

LEASE = 1800


def _lease_state(st):
    """Numbered instances share the installed package and its upgrade lease."""
    from . import instance
    from .state import State
    number = instance.current()
    if number > 1 and st.root.resolve() == instance.state_root(number).resolve():
        return State(instance.state_root(1))
    return st


def _unexpired(state):
    try:
        return time.time() - json.loads((state.root / LOCK).read_text())["at"] < LEASE
    except (OSError, ValueError, KeyError, TypeError):
        return False


def busy(st):
    lease = _lease_state(st)
    return _unexpired(lease) or (st.root != lease.root and _unexpired(st))


def lease_end(st):
    """When the reserved lease runs out on the monotonic clock."""
    try:
        at = json.loads((_lease_state(st).root / LOCK).read_text())["at"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(at, (int, float)):
        return None
    return time.monotonic() + (at + LEASE - time.time())


def reserve(st, *, holder=None):
    lease = _lease_state(st)
    lease.root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with lease.file_lock("phone-update-lease"):
        if busy(st):
            return False
        row = {"at": time.time(), **({"holder": holder} if holder else {})}
        lease.write_private(lease.root / LOCK, json.dumps(row).encode())
        return True


def holds(st, holder):
    try:
        row = json.loads((_lease_state(st).root / LOCK).read_text())
        return row.get("holder") == holder and time.time() - row["at"] < LEASE
    except (OSError, ValueError, AttributeError, KeyError, TypeError):
        return False


def release(st, *, holder=None):
    lease = _lease_state(st)
    if not lease.root.is_dir():
        return
    with lease.file_lock("phone-update-lease"):
        path = lease.root / LOCK
        try:
            row = json.loads(path.read_text())
        except (OSError, ValueError):
            return
        if isinstance(row, dict) and row.get("holder") == holder:
            path.unlink(missing_ok=True)

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
    if 'uv_repair_doctor' in rec:
        from . import auto_update
        rec['uv_auto_retry'] = auto_update.uv_retry_scheduled(st, rec)
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
    if reason == "read_only":
        guide = upgrade_diag.UPGRADE_REPAIR_GUIDE
        return (f"The host program directory is read-only. Follow the [host upgrade repair guide]({guide}) to keep your pairings."
                if lang == "en" else f"主机程序目录只读。请按[升级修复步骤]({guide})处理，现有配对可以保留。")
    if reason in ("launch_failed", "receipt_timeout"):
        guide = upgrade_diag.UPGRADE_REPAIR_GUIDE
        if lang == "en":
            status = "The host could not start the upgrade." if reason == "launch_failed" else "The host has not confirmed the upgrade result."
            return f"{status} Once the host reconnects, send /update. If it still fails, follow the [host upgrade repair guide]({guide})."
        status = "主机未能启动升级。" if reason == "launch_failed" else "主机尚未确认升级结果。"
        return f"{status} 主机重新连上后，在这台遥控器发 /update。若仍失败，请[查看升级修复步骤]({guide})。"
    if rec.get("tell_owner") or upgrade_diag.resolution_failed(rec):          # P277: the plain sentence (what failed, where the Agent J host is, whether support has it)
        return upgrade_diag.owner_sentence(rec, _rep_of(rec), lang)
    en = lang == "en"
    # Receipt fields may come from an older updater. Only fixed vocabulary crosses into owner-visible text.
    if reason == "estop":
        return ("Upgrade paused. Turn off Emergency stop on your paired remote, then open Settings → Update." if en else
                "升级已暂停。请在配对遥控器上解除急停，再从设置打开升级。")
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
        return {"status": "already" if s.startswith("already sent") else "sent",
                "thread": m.group(0), "metadata_only": s.endswith(" metadata only")}
    return {"status": "off" if s.startswith("off") else "not_sent" if s else "unknown"}


def _lang(st):
    try:
        from . import preferences
        return "en" if preferences.get(preferences.effective(st), "appearance.language", "zh") == "en" else "zh"
    except Exception:  # noqa: BLE001
        return "zh"


def _restart(rec, quiet, job=None):
    """Hand the restart to the freshly installed CLI (`service install --deferred`): its recovery worker waits for an idle
    serve, restarts the service and reads the live version back. → None, or the real failure reason."""
    from . import service
    argv = update.new_argv(update.install_kind())
    try:
        args = ["service", "install", "--deferred", rec["to"]]
        if job:
            args += ["--peer-upgrade-job", job]
        r = quiet(argv + args, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        return {"reason": "service_restart_failed", "service_error": type(e).__name__}
    if r.returncode == service.NO_MANAGER_EXIT:
        return {"reason": "service_restart_failed", **update.no_manager(r.stderr or "")}
    if r.returncode:
        from .upgrade_diag import tail
        return {"reason": "service_restart_failed", "stderr_tail": tail(r.stderr or "")}
    return None


def outcome_path(st, job):
    import re
    if not isinstance(job, str) or not re.fullmatch(r"[0-9a-f]{32}", job):
        raise ValueError("invalid upgrade job")
    return st.root / "upgrade-cache" / "peer-results" / (job + ".json")


def record_outcome(st, job, target, outcome):
    path = outcome_path(st, job)
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        st.write_private(path, json.dumps({"job": job, "target": target, "outcome": outcome}).encode())
    except OSError:
        return  # the receiver can only report unconfirmed if the durable outcome is unavailable
    for old in path.parent.glob("*.json"):
        with contextlib.suppress(OSError):
            if old.stat().st_mtime < time.time() - 86400:
                old.unlink()


def read_outcome(st, job, target):
    try:
        item = json.loads(outcome_path(st, job).read_bytes())
        if item.get("job") == job and item.get("target") == target and item.get("outcome") in ("restarted", "failed"):
            return item["outcome"]
    except (OSError, ValueError, AttributeError):
        pass
    return None


def run(st, apply_fn=None, restart_fn=None, *, target=None, job=None):
    if update.market_managed():
        return update.market_result()
    from . import controls, doctor, upgrade_diag

    def not_paused():
        return not controls.estop_state(st).get("on")
    def quiet(argv, **kw):
        kw.pop("capture_output", None)
        return subprocess.run(argv, **{**kw, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
                                      "text": True, "timeout": min(kw.get("timeout", 900), 900)})   # 0334: a retry's share
    rec = None
    job_outcome = "failed"
    try:
        try:
            if not not_paused():
                rec = {"result": "refused", "reason": "estop"}
            else:
                kw = {"lease_end": lease_end(st), "start_allowed": not_paused} if apply_fn is None else {}
                if target is not None:
                    kw["target"] = target
                rec = (apply_fn or update.apply)(st, svc_on=False, run=quiet, **kw)
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
        if rec.get("result") != "ok" and rec.get("reason") != "estop" and (rec.get("step") or rec.get("reason") == "not_owner"):   # #475: not_owner → fix sentence
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
                verified = rec.get("result") == "ok" and rec.get("reason") == "upgraded" and rec.get("to") == target
                failed = _restart(rec, quiet, job) if job and verified else _restart(rec, quiet)
            if failed:
                # The new package is on disk but the old service keeps running: say so (never 「升级成功」, never a bare type).
                rec = {**rec, **failed, "result": "failed", "installed": rec.get("to"), "exit": 1}
                st.log("phone_update_restart", status="failed", reason=failed["reason"])
                st.write_private(st.root / RESULT, json.dumps(rec).encode())
            elif rec.get("result") == "ok" and rec.get("reason") == "upgraded" and rec.get("to") == target:
                job_outcome = None  # the deferred recovery job writes the final live-process readback
    except Exception as e:  # noqa: BLE001
        st.write_private(st.root / RESULT, json.dumps({**(rec or {}), "result": "failed", "reason": "phone_update_crashed",
                                                       "error": type(e).__name__}).encode())
    finally:
        if job and job_outcome:
            record_outcome(st, job, target, job_outcome)
        release(st)
        from . import service_recovery
        service_recovery.cleanup_job(st)

if __name__ == "__main__":
    from .state import State
    run(State())
