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
            "ConnectionError": "download_failed", "ConnectionResetError": "download_failed", "OSError": "download_failed",
            "artifact_too_large": "download_failed", "insecure_update_redirect": "download_failed"}


def category(reason) -> str:
    r = str(reason or "unknown")
    r = CATEGORY.get(r, r)
    return r if REASON_RE.fullmatch(r) else "unknown"


def plan_reason(e: BaseException) -> str:
    """verified_plan's exception → the result reason. Getting the bytes (network, HTTP, timeouts, a redirect refused, too
    large) is download_failed; the installation kind auto-update cannot handle is unsupported_install; every other refusal
    of the bytes (hash, minisign, wheel identity, a broken archive) is signature_or_hash_mismatch."""
    if isinstance(e, OSError) or (isinstance(e, ValueError) and str(e) in ("artifact_too_large", "insecure_update_redirect")):
        return "download_failed"
    if isinstance(e, ValueError) and str(e) == "unsupported_install":
        return "unsupported_install"
    return "signature_or_hash_mismatch"


def plan_detail(e: BaseException) -> str:
    """A fixed, non-identifying line for the download step: exception type (+ HTTP status), never its message (URLs,
    proxies and paths can be in there)."""
    code = getattr(e, "code", None)
    name = type(e).__name__
    if isinstance(e, ValueError) and REASON_RE.fullmatch(str(e) or "-"):
        return f"{name}: {e}"
    return f"{name}" + (f" HTTP {code}" if isinstance(code, int) else "")


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
    lines = [ln.rstrip()[:LINE_MAX] for ln in text.replace("\r", "\n").splitlines() if ln.strip()][-TAIL_LINES:]
    return redact("\n".join(lines), identity)[-TAIL_MAX:]


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
        if res.get("stderr_tail"):
            rec["stderr_tail"] = str(res["stderr_tail"])[-TAIL_MAX:]
        if res.get("detail"):
            rec["detail"] = str(res["detail"])[:120]
    return rec


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
    if res.get("detail"):
        lines.append("detail: " + str(res["detail"])[:120])
    if res.get("stderr_tail"):
        lines += ["installer stderr (last lines, redacted):", str(res["stderr_tail"])]
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
    "uv_failed": ("安装命令执行失败", "the install command failed"),
    "service_restart_failed": ("新版装好了但后台服务没能重启", "the new version installed but the background service did not restart"),
    "not_upgraded": ("装完后版本没有变成目标版本", "after installing, the version was not the target"),
    "doctor_failed": ("升级后自检没通过", "the post-upgrade self-check failed"),
    "self_check_failed": ("升级后新程序跑不起来", "the new program could not run after the upgrade"),
    "unsupported_install": ("这台Agent J 主机的安装方式不能自动升级", "this installation cannot be upgraded automatically"),
}


def owner_sentence(res: dict, rep: dict, lang: str = "zh") -> str:
    """One plain sentence for the owner: what failed, where the Agent J host is now, whether support already has it."""
    en = lang == "en"
    why = REASON_WORDS.get(res.get("reason"), (f"原因 {res.get('reason')}", f"reason {res.get('reason')}"))[1 if en else 0]
    to, frm = res.get("to") or "?", res.get("from") or __version__
    if res.get("rollback") == "ok" or res.get("reason") in ("download_failed", "signature_or_hash_mismatch", "unsupported_install"):
        where = f"still on {frm}, working as before" if en else f"Agent J 主机仍是 {frm}，照常可用"
    elif res.get("service") == "no_systemd":     # P278
        where = (f"{to} is installed, but the Agent J host has no systemd to restart the background service: turn it on as the "
                 "result's fix line says, or restart `agentj serve` where it runs" if en else
                 f"{to} 已经装好，但这台Agent J 主机没有可用的 systemd 来重启后台服务：按结果里的 fix 行开启，或在运行 agentj serve 的地方重新运行一次")
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
