"""P277: an upgrade that fails can be diagnosed without the owner's terminal.

A Windows user on WSL2 (0.20.1a1 → 0.20.3a1, 2026-10-14): the Agent said 「安装步骤失败」 and nothing else reached us —
no installer output, no support thread, no Dashboard trace. Now every upgrade attempt (manual `update apply`, the phone's
/update, the authorized e-mail path, the nightly / idle worker):

- names the failed step and a finer reason: download_failed · signature_or_hash_mismatch · uv_failed ·
  service_restart_failed (plus the existing not_upgraded / doctor_failed / …);
- keeps the installer's last ~20 stderr lines, redacted here first (privacy layer 1: keys, tokens, e-mails, phones, home
  paths, this host's and user's names; then any long opaque token shape) — the same text in UPGRADE_RESULT, in the
  support report and nowhere else (never in the heartbeat);
- is remembered as the host's last upgrade (`last-upgrade.json`, 0600): from / to / result / reason / at / step / tail — the
  heartbeat sends only from / to / result / reason / at to the Dashboard;
- after a failure, unless the owner switched it off (`updates.report_failures`, default on), opens one support thread
  (`agentj support ask` kind bug, `--attach-doctor` equivalent): metadata and the redacted tail only, once per
  (target, reason) a day. The block says in one plain sentence what happened and whether support has it.
"""
from __future__ import annotations

import errno
import io
import json
import os
import platform
import re
import subprocess
import sys
import time

from . import __version__

LAST = "last-upgrade.json"
TAIL_LINES = 20
LINE_MAX = 240
TAIL_MAX = 3000
REPORT_ENV = "AGENTJ_UPGRADE_REPORT"       # "off": never report (tests; CI)
REPORT_EVERY = 24 * 3600                   # the same (target, reason) is reported at most once a day
RESULTS = ("ok", "failed", "rolled_back")
REASON_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
STEPS = ("check", "download", "install", "verify", "service", "health")
HASHY = {"artifact_hash_mismatch", "artifact_signature_mismatch", "artifact_identity_mismatch"}
# opaque token shapes privacy layer 1 does not know by prefix: ≥ 32 url-safe chars mixing letters and digits (a 64-hex
# SHA-256 is kept: it names a public artifact and is what support needs to compare)
_TOKEN = re.compile(r"(?<![A-Za-z0-9_\-])(?=[A-Za-z0-9_\-]*[A-Za-z])(?=[A-Za-z0-9_\-]*\d)[A-Za-z0-9_\-]{32,}(?![A-Za-z0-9_\-])")
_HEX = re.compile(r"^[0-9a-f]{32,128}$")
# auto_update's reasons → the P277 categories (its own reason text stays as it was in the phone notice)
CATEGORY = {"install_failed": "uv_failed", "restart_failed": "service_restart_failed", "version_mismatch": "not_upgraded",
            "artifact_hash_mismatch": "signature_or_hash_mismatch", "artifact_signature_mismatch": "signature_or_hash_mismatch",
            "artifact_identity_mismatch": "signature_or_hash_mismatch", "artifact_refused": "signature_or_hash_mismatch",
            "upgrade_self_check_failed": "doctor_failed", "service_failed": "service_restart_failed",
            "URLError": "download_failed", "HTTPError": "download_failed", "TimeoutError": "download_failed",
            "ConnectionError": "download_failed", "ConnectionResetError": "download_failed", "OSError": "os_error",
            "CalledProcessError": "uv_failed", "SubprocessError": "uv_failed", "FileNotFoundError": "installer_missing",
            "PermissionError": "permission_denied", "TimeoutExpired": "installer_timeout",
            "artifact_too_large": "download_failed", "insecure_update_redirect": "download_failed"}


# #166/#192: fixed needles in the installer's (lower-cased) output → a finer reason than uv_failed; first match wins, so
# the Python-version refusal comes before the general "no solution" one.
UV_STDERR = (
    ("disk_full", ("no space left on device", "os error 28", "disk quota exceeded", "os error 122")),
    ("python_incompatible", ("does not satisfy python", "requires-python", "requires python", "python version is not supported")),
    ("python_missing", ("no interpreter found", "no python installation", "failed to find a python", "python downloads are not allowed")),
    ("dependency_conflict", ("no solution found when resolving", "unsatisfiable", "resolutionimpossible")),
    ("dependency_download_failed", ("failed to fetch", "error sending request", "failed to download", "dns error",
                                    "connection refused", "connection reset", "network is unreachable", "tcp connect error",
                                    "certificate", "request failed after", "operation timed out")),
)


def category(reason) -> str:
    if isinstance(reason, OSError) and reason.errno == errno.EROFS:
        return "read_only_filesystem"
    r = type(reason).__name__ if isinstance(reason, BaseException) else str(reason or "unknown")
    r = CATEGORY.get(r, r)
    return r if REASON_RE.fullmatch(r) else "unknown"


def installer_reason(exc=None, stderr="") -> str:
    """Fixed reasons from errno or installer output; never expose exception messages/argv."""
    if isinstance(exc, (FileNotFoundError, PermissionError, subprocess.TimeoutExpired)):
        return category(exc)
    text = stderr.lower() if isinstance(stderr, str) else ""
    if getattr(exc, "errno", None) == errno.EROFS or "read-only file system" in text or "os error 30" in text:
        return "read_only_filesystem"
    if "permission denied" in text or "os error 13" in text:
        return "permission_denied"
    # #166/#192: a uv / pipx exit with output is named by what the output says, not one catch-all
    for reason, needles in UV_STDERR:
        if any(n in text for n in needles):
            return reason
    reason = category(exc) if exc is not None else "uv_failed"
    return "uv_failed" if reason == "unknown" else reason


def installer_fields(res):
    """Only known command templates and scalar metadata may leave the host. No owner paths/argv."""
    out = {}
    if res.get("command") in (
        "uv tool install --force <wheel>", "uv tool install --force <wheel> --python <python>",
        "pipx install --force <wheel>", "python -m pip install --no-deps --force-reinstall <wheel>",
    ):
        out["command"] = res["command"]
    if type(res.get("exit")) is int:
        out["exit"] = res["exit"]
    if res.get("exception") in CATEGORY:
        out["exception"] = res["exception"]
    return out


def plan_reason(e: BaseException) -> str:
    """verified_plan's exception → the result reason. Getting the bytes (network, HTTP, timeouts, a redirect refused, too
    large) is download_failed; the installation kind auto-update cannot handle is unsupported_install; every other refusal
    of the bytes (hash, minisign, wheel identity, a broken archive) is signature_or_hash_mismatch."""
    if isinstance(e, OSError) or (isinstance(e, ValueError) and str(e) in ("artifact_too_large", "insecure_update_redirect")):
        return "download_failed"
    if isinstance(e, ValueError) and str(e) == "unsupported_install":
        return "unsupported_install"
    return "signature_or_hash_mismatch"


# P586: immutable metadata vocabulary, never arbitrary exception messages or receipt content.
SUBREASONS = frozenset(('hash_mismatch', 'signature_invalid', 'untrusted_key', 'comment_mismatch',
                       'identity_mismatch', 'bad_zip', 'cache_receipt_invalid', 'internal_error'))
ARTIFACT_STAGES = frozenset(('wheel', 'sha256', 'signature', 'identity', 'cache'))
PLAN_CODES = frozenset(('artifact_hash_mismatch', 'artifact_signature_mismatch', 'artifact_identity_mismatch',
                        'artifact_too_large', 'insecure_update_redirect', 'unsupported_install',
                        'upgrade_cache_unreadable', 'bad_version'))
PLAN_EXCEPTIONS = frozenset(('ValueError', 'MinisignError', 'BadZipFile', 'KeyError', 'TypeError', 'AttributeError',
                            'UnicodeDecodeError', 'URLError', 'HTTPError', 'TimeoutError', 'OSError',
                            'ConnectionError', 'ConnectionResetError', 'FileNotFoundError', 'PermissionError'))


def artifact_fields(res):
    """Only bounded enums and public artifact SHA256 digests may leave the verification boundary."""
    out = {}
    for key, allowed in (('subreason', SUBREASONS), ('artifact_stage', ARTIFACT_STAGES),
                         ('cache_status', ('miss', 'invalid'))):
        value = res.get(key)
        if isinstance(value, str) and value in allowed:
            out[key] = value
    for key in ('artifact_sha256', 'stated_sha256'):
        value = res.get(key)
        if isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value):
            out[key] = value
    return out


def plan_fields(e):
    evidence = getattr(e, 'artifact_diagnostic', {})
    out = artifact_fields(evidence if isinstance(evidence, dict) else {})
    if 'subreason' not in out:
        codes = {'artifact_hash_mismatch': 'hash_mismatch', 'artifact_signature_mismatch': 'comment_mismatch',
                 'artifact_identity_mismatch': 'identity_mismatch', 'upgrade_cache_unreadable': 'cache_receipt_invalid'}
        out['subreason'] = codes.get(str(e), 'internal_error') if isinstance(e, ValueError) else 'internal_error'
    return out


def plan_detail(e: BaseException) -> str:
    """Fixed exception classes / codes only; no exception text, paths, URLs or receipt content."""
    code = getattr(e, 'code', None)
    name = type(e).__name__
    name = name if name in PLAN_EXCEPTIONS else 'Exception'
    if isinstance(e, ValueError) and str(e) in PLAN_CODES:
        return f"{name}: {e}"
    return name + (f" HTTP {code}" if type(code) is int and 100 <= code <= 599 else '')


def redact(text: str, identity=None) -> str:
    from . import privacy
    try:
        host, user = identity if identity is not None else privacy.local_identity()
        out = privacy.redact(text, host, user)
    except Exception:  # noqa: BLE001 — never send unredacted text: on any failure send nothing
        return ""
    return _TOKEN.sub(lambda m: m.group(0) if _HEX.fullmatch(m.group(0)) else "<token>", out)


def tail(text, identity=None) -> str:
    """The last TAIL_LINES non-empty lines (each ≤ LINE_MAX), redacted; '' when there is nothing."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    if not isinstance(text, str) or not text.strip():
        return ""
    # Redact the complete input first: truncating can remove a private-key header or split a credential value.
    safe = redact(text.replace("\r", "\n"), identity)
    lines = [ln.rstrip()[:LINE_MAX] for ln in safe.splitlines() if ln.strip()][-TAIL_LINES:]
    return "\n".join(lines)[-TAIL_MAX:]


def run_step(run, cmd, say, catch: bool = True) -> tuple[int, str]:
    """One installer command: output captured (and still shown through `say`), → (rc, redacted stderr tail). A command
    that cannot start is rc 127 with its exception type (catch=False: the OSError is the caller's)."""
    try:
        r = run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    except OSError as e:
        if not catch:
            raise
        return 127, f"{type(e).__name__}: {os.path.basename(str(cmd[0]))} could not be started"
    out, err = getattr(r, "stdout", None), getattr(r, "stderr", None)
    for s in (out, err):
        if isinstance(s, str) and s.strip():
            for ln in s.rstrip().splitlines()[-200:]:
                say("  " + ln)
    rc = r.returncode
    return rc, (tail(err) or tail(out)) if rc else ""


# ------------------------------------------------------------------ last upgrade (state dir; the heartbeat's source)
def _clean_version(v):
    from .update import parse
    return v if isinstance(v, str) and len(v) <= 32 and parse(v) is not None else None


def entry(res: dict, now: float | None = None) -> dict | None:
    """The record for one finished attempt, or None when nothing was attempted (refusals before the download)."""
    if not isinstance(res, dict) or not res.get("step"):
        return None
    if res.get("result") == "ok":
        result = "ok"
    else:
        result = "rolled_back" if res.get("rollback") == "ok" else "failed"
    rec = {"v": 1, "from": _clean_version(res.get("from")), "to": _clean_version(res.get("to")), "result": result,
           "reason": category(res.get("reason")), "at": int(time.time() if now is None else now)}
    if result != "ok":
        rec["step"] = res["step"] if res.get("step") in STEPS else "install"
        rec.update(installer_fields(res))
        rec.update(artifact_fields(res))
        if res.get("stderr_tail"):
            rec["stderr_tail"] = str(res["stderr_tail"])[-TAIL_MAX:]
        if res.get("detail"):
            rec["detail"] = str(res["detail"])[:120]
        checks = failed_checks(res)
        if checks:
            rec["failed_checks"] = checks
        rec.update(rollback_fields(res))
    return rec


ROLLBACK_TAIL_MAX = 1200
ROLLBACK_CHECKS = ("version_unchanged",)


def rollback_fields(res) -> dict:
    """#166/#192: why the automatic rollback itself failed — a fixed category, the rollback installer's own redacted tail
    (bounded, kept apart from the first failure's) and, when the installed version was found unchanged, that check's id."""
    out = {}
    if res.get("rollback_failure"):
        out["rollback_failure"] = category(res["rollback_failure"])
    if res.get("rollback_tail"):
        out["rollback_tail"] = str(res["rollback_tail"])[-ROLLBACK_TAIL_MAX:]
    if res.get("rollback_check") in ROLLBACK_CHECKS:
        out["rollback_check"] = res["rollback_check"]
    return out


def failed_checks(res) -> list:
    """P393 (#121): the self-check ids that blocked the upgrade — fixed ids only (update.CHECK_ID_RE), never a summary."""
    try:
        from .update import check_ids
        return check_ids(res.get("failed_checks")) if isinstance(res, dict) else []
    except Exception:  # noqa: BLE001
        return []


def valid(rec) -> bool:
    return (isinstance(rec, dict) and rec.get("result") in RESULTS and isinstance(rec.get("reason"), str)
            and bool(REASON_RE.fullmatch(rec["reason"])) and type(rec.get("at")) is int and rec["at"] >= 0
            and _clean_version(rec.get("to")) is not None and (rec.get("from") is None or _clean_version(rec["from"]) is not None))


def read(st) -> dict | None:
    try:
        rec = json.loads((st.root / LAST).read_text())
    except (OSError, ValueError):
        return None
    return rec if valid(rec) else None


def write(st, rec: dict) -> bool:
    if not valid(rec):
        return False
    try:
        if not st.exists():
            return False
        old = read(st) or {}
        if old.get("to") == rec["to"] and old.get("reason") == rec["reason"] and old.get("reported"):
            rec = {**rec, "reported": old["reported"], "reported_at": old.get("reported_at")}
        st.write_private(st.root / LAST, json.dumps(rec, ensure_ascii=False).encode())
        return True
    except Exception:  # noqa: BLE001 — remembering never breaks an upgrade
        return False


def remember(st, res: dict, *, fenced: bool = False) -> dict | None:
    """Write the attempt's record (inside the fence: by the host, over elevate.sock `upgrade` op `record`)."""
    rec = entry(res)
    if rec is None:
        return None
    if fenced:
        try:
            from .update import _host
            _host(st, "record", 15, to=rec["to"], rec=rec)
        except Exception:  # noqa: BLE001
            pass
        return rec
    write(st, rec)
    return rec


def heartbeat_field(st) -> dict | None:
    """What the heartbeat carries: from / to / result / reason / at — never the step's text or the stderr tail."""
    rec = read(st)
    if not rec:
        return None
    return {"from": rec.get("from"), "to": rec["to"], "result": rec["result"], "reason": rec["reason"], "at": rec["at"]}


# ------------------------------------------------------------------ automatic support report
def enabled(st) -> bool:
    if (os.environ.get(REPORT_ENV) or "").lower() == "off":
        return False
    try:
        from . import preferences
        return preferences.get(preferences.effective(st), "updates.report_failures", True) is not False
    except Exception:  # noqa: BLE001 — unreadable preferences: the default (on)
        return True


def _platform() -> str:
    try:
        from .doctor import _is_wsl
        wsl = _is_wsl()
    except Exception:  # noqa: BLE001
        wsl = False
    return f"{sys.platform} {platform.machine()}" + (" WSL2" if wsl else "") + f" · python {sys.version.split()[0]}"


def body(res: dict, kind: str | None = None) -> str:
    rec = entry(res) or {}
    lines = ["[auto] agentj upgrade failed (P277 automatic report — metadata and the redacted installer output only)",
             f"from: {res.get('from') or '?'} · to: {res.get('to') or '?'} · result: {rec.get('result', res.get('result'))}",
             f"reason: {res.get('reason')} · step: {rec.get('step', res.get('step') or '?')}"
             + (f" · rollback: {res['rollback']}" if res.get("rollback") else "")
             + (f" · service: {res['service']}" if res.get("service") else ""),
             f"install: {kind or '?'} · {_platform()}"]
    for key, value in {**installer_fields(res), **artifact_fields(res)}.items():
        lines.append(f"{key}: {value}")
    checks = failed_checks(res)
    if checks:
        lines.append("failed checks: " + ", ".join(checks))
    elif category(res.get("reason")) == "doctor_failed":
        lines.append("failed checks: not recorded")
    if res.get("detail"):
        lines.append("detail: " + str(res["detail"])[:120])
    if res.get("stderr_tail"):
        lines += ["installer stderr (last lines, redacted):", str(res["stderr_tail"])]
    rb = rollback_fields(res)
    if rb.get("rollback_failure"):
        lines.append("rollback failure: " + rb["rollback_failure"]
                     + (" · installed version unchanged: still " + str(res.get("from") or "?")
                        if rb.get("rollback_check") == "version_unchanged" else ""))
    if rb.get("rollback_tail"):
        lines += ["rollback installer stderr (last lines, redacted):", rb["rollback_tail"]]
    return "\n".join(lines)


def report(st, res: dict, *, send=None, now: float | None = None, kind: str | None = None, lang: str = "zh") -> dict:
    """→ {"status": "sent", "thread"} | {"status": "off"} | {"status": "already", "thread"} | {"status": "not_sent", "why"}.
    `send(text) -> (exit, printed)` is the seam (default: support.run_send kind bug, doctor attached, no waiting)."""
    if not isinstance(res, dict) or res.get("result") == "ok" or not res.get("step") or not res.get("to"):
        return {"status": "skipped"}
    if not enabled(st):
        return {"status": "off"}
    now = time.time() if now is None else now
    last = None
    try:
        last = read(st) if st.exists() else None
    except Exception:  # noqa: BLE001
        last = None
    if (last and last.get("to") == res.get("to") and last.get("reason") == category(res.get("reason"))
            and last.get("reported") and now - float(last.get("reported_at") or 0) < REPORT_EVERY):
        return {"status": "already", "thread": last["reported"]}
    text = body(res, kind)
    try:
        if send is None:
            from . import support
            buf = io.StringIO()
            rc = support.run_send("bug", text, attach_doctor=True, wait=0, lang=lang, out=buf)
            printed = buf.getvalue()
        else:
            rc, printed = send(text)
    except Exception as e:  # noqa: BLE001 — a report that cannot go out never hides the upgrade result
        return {"status": "not_sent", "why": type(e).__name__}
    m = re.search(r"st_[A-Za-z0-9_-]{22}", printed or "")
    if rc not in (0, 5) or not m:
        return {"status": "not_sent", "why": "blocked" if rc == 2 else f"exit_{rc}"}
    if last is not None and last.get("to") == res.get("to"):
        try:
            st.write_private(st.root / LAST, json.dumps({**last, "reported": m.group(0), "reported_at": int(now)}).encode())
        except Exception:  # noqa: BLE001
            pass
    return {"status": "sent", "thread": m.group(0)}


REASON_WORDS = {
    "download_failed": ("下载安装包失败，网络或 agentj.app 暂时连不上", "the release download failed: no network or agentj.app unreachable"),
    "signature_or_hash_mismatch": ("安装包的签名或校验值对不上，已拒绝安装", "the package's signature or checksum did not match, so it was refused"),
    "installer_missing": ("找不到安装工具，请先恢复 uv / pipx", "the installer is missing; restore uv / pipx first"),
    "permission_denied": ("安装目录权限不足", "the installation directory is not writable"),
    "read_only_filesystem": ("安装位置是只读文件系统", "the installation is on a read-only filesystem"),
    "installer_timeout": ("安装命令超时", "the installer timed out"),
    "os_error": ("安装遇到系统错误", "an operating-system error prevented installation"),
    "uv_failed": ("安装命令执行失败", "the install command failed"),
    "disk_full": ("磁盘空间不足，安装没能写完", "the disk is full, so the installation could not finish"),
    "python_incompatible": ("这台 Agent J 主机上的 Python 版本不符合新版要求", "this computer's Python version does not meet the new version's requirement"),
    "python_missing": ("安装工具找不到可用的 Python", "the installer could not find a usable Python"),
    "dependency_conflict": ("新版的依赖包在这台 Agent J 主机上装不到一起", "the new version's dependencies could not be resolved on this computer"),
    "dependency_download_failed": ("下载依赖包失败，网络或软件源暂时连不上", "downloading the dependencies failed: no network or the package index is unreachable"),
    "upgrade_cache_unreadable": ("本地升级缓存读不出来", "the local upgrade cache could not be read"),
    "service_restart_failed": ("新版装好了但后台服务没能重启", "the new version installed but the background service did not restart"),
    "not_upgraded": ("装完后版本没有变成目标版本", "after installing, the version was not the target"),
    "doctor_failed": ("升级后自检没通过", "the post-upgrade self-check failed"),
    "self_check_failed": ("升级后新程序跑不起来", "the new program could not run after the upgrade"),
    "unsupported_install": ("这台Agent J 主机的安装方式不能自动升级", "this installation cannot be upgraded automatically"),
}


def install_unchanged(res: dict) -> bool:
    """Journal phase wins; recognise pre-P487 download records without trusting their spurious recover line."""
    if "phase" in res:
        return res["phase"] in (None, "prepared")
    return category(res.get("reason")) in ("download_failed", "signature_or_hash_mismatch", "unsupported_install")


def owner_sentence(res: dict, rep: dict, lang: str = "zh") -> str:
    """One plain sentence for the owner: what failed, where the Agent J host is now, whether support already has it."""
    en = lang == "en"
    why = REASON_WORDS.get(res.get("reason"), (f"原因 {res.get('reason')}", f"reason {res.get('reason')}"))[1 if en else 0]
    to, frm = res.get("to") or "?", res.get("from") or __version__
    if res.get("rollback") == "ok" or install_unchanged(res):
        where = f"still on {frm}, working as before" if en else f"Agent J 主机仍是 {frm}，照常可用"
    elif res.get("service") == "no_systemd":     # P278
        where = (f"{to} is installed, but the Agent J host has no systemd to restart the background service: turn it on as the "
                 "result's fix line says, or restart `agentj serve` where it runs" if en else
                 f"{to} 已经装好，但这台Agent J 主机没有可用的 systemd 来重启后台服务：按结果里的 fix 行开启，或在运行 agentj serve 的地方重新运行一次")
    elif res.get("recovery_blocked") == "revocation_downgrade_blocked":
        from .update import RECOVERY_BLOCKED_WHY
        where = RECOVERY_BLOCKED_WHY[en]
    elif res.get("recover"):
        where = ("the rollback failed too; I will run the recover command in the result to put the previous version back" if en else
                 "回滚也没成功，我会运行结果里的 recover 命令装回原版本")
    else:
        where = "run `agentj doctor` to check the Agent J host" if en else "请让我运行 agentj doctor 检查一下"
    s = rep.get("status")
    if s in ("sent", "already"):
        tail_ = (f"the details went to Agent J support automatically ({rep['thread']}); nothing for you to do" if en else
                 f"失败原因和脱敏日志已自动发给 Agent J 客服（{rep['thread']}），你不用做什么")
    elif s == "off":
        tail_ = ("automatic reporting is off, so support was not told" if en else "你关闭了自动上报，没有发给客服")
    else:
        tail_ = ("it could not be sent to support automatically; I can send it with `agentj support ask --attach-doctor`" if en else
                 "没能自动发给客服，我可以用 agentj support ask --attach-doctor 再发")
    if en:
        return f"The upgrade to {to} did not go through ({why}); {where}; {tail_}."
    return f"升级到 {to} 没成功（{why}）；{where}；{tail_}。"


def after_failure(st, res: dict, *, lang: str = "zh", send=None) -> dict:
    """The CLI / phone / worker step after a finished attempt: report a failure and put the support / tell_owner lines into
    the result (result_block prints them). Never raises."""
    try:
        if not isinstance(res, dict) or res.get("result") == "ok" or not res.get("step"):
            return res
        kind = None
        try:
            from .update import install_kind
            kind = install_kind()["kind"]
        except Exception:  # noqa: BLE001
            pass
        rep = report(st, res, send=send, kind=kind, lang=lang)
        res["support"] = {"sent": f"sent ({rep.get('thread')})", "already": f"already sent ({rep.get('thread')})",
                          "off": "off (updates.report_failures = false)"}.get(rep.get("status"), f"not_sent ({rep.get('why', '?')})")
        res["tell_owner"] = owner_sentence(res, rep, lang)
    except Exception:  # noqa: BLE001
        pass
    return res
