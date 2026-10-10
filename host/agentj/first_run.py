"""P92 / ADR-A196: the first-run "at your computer" checklist — `agentj setup checklist [--resume | --check | --item ID]`.

One main Agent walks the owner through everything that later needs them at the computer, once, while they are still there
(Leo approved the four P92 defaults, 2026-10-09): offered automatically after the FIRST installation's welcome, on demand for
existing hosts; "I won't use this" is remembered per item; the leave-the-computer confirmation covers only the selected, verified
items and lists the rest; 0.18.0 is the first P86 scenario, the full Google API connection follows in 0.18.1 (P91) and the remote
viewer in 0.19 (P90) — those rows say so instead of pretending.

Journal: `<state>/setup/checklist.json` (0700 dir, 0600 file) — a companion file next to the P86 registry, never a second
registry: item id / choice (selected | unused | undecided) / state (pending | waiting_local | verified | attention | deferred |
unsupported | not_applicable) / reason word / check and expiry times / the owner-decision reference. No e-mail, token, password,
cookie, QR, callback URL or system file content. Writes take one lock and a revision compare-and-swap; a phone answering an older
revision gets `changed` and re-reads (never last-writer-wins).

Who decides what: the host's adapters decide `verified` (a probe that reads the real state back). The owner decides choices,
only through the signed phone/desktop card (`setup_mark`, controls.py); the CLI and the Agent cannot mark anything ready, unused
or skipped. "I'm done" from the owner only triggers a check. A failed item pauses its dependants only, never the main Agent.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

DIR = "setup"
FILE = "checklist.json"
TTL = {"browser": 15 * 60, "default": 24 * 3600}      # P86 freshness: browser 15 min, credentials / machine 24 h
EXIT_WINDOW = 15 * 60                                  # the phone round trip must come within this after "start the check"
EXIT_FRESH = 2 * 3600                                  # … and a leave-the-computer confirmation is about the last 2 h
ACTIVE_SECS = 6 * 3600                                 # a checklist resumed in the last 6 h re-shows on reconnect
GROUPS = ("core", "machine", "browser", "google", "privacy", "optional")
GROUP_TITLE = {"core": ("基本连接", "Your connection"), "machine": ("电源与服务", "Power and services"),
               "browser": ("常用网站", "Your websites"), "google": ("Google 工具", "Google tools"),
               "privacy": ("按需系统权限", "Permissions you need"), "optional": ("可选通道", "Optional connections")}
CHOICES = ("selected", "unused", "undecided")
STATES = ("pending", "waiting_local", "verified", "attention", "deferred", "unsupported", "not_applicable")
# What the card's buttons may ask for (setup_mark, signed). "later" = deferred (kept as unfinished), "unused" = "I won't use this".
MARKS = ("selected", "unused", "later", "start_exit")

# The 30 items (P92 INVENTORY.md / inventory.json). need: required | conditional | optional. where: auto | local | phone.
# Titles and the one-line "what you do" are the card's text; the Agent explains more in chat.
ITEMS = [
    ("seat", "core", "required", "local", [], "账号与席位绑定", "Account and seat",
     "用设置码把这台电脑加入你的 Agent J 账号。", "Join this computer to your Agent J account with the setup code."),
    ("harness", "core", "required", "local", ["seat"], "主 AI 登录与权限", "Main AI login and access",
     "在电脑上登录你选的 AI 工具（Claude Code / Codex / OpenCode）。", "Sign in to your chosen AI tool on this computer."),
    ("desktop", "core", "required", "local", ["harness"], "电脑浏览器配对", "Pair this computer's browser",
     "在这台电脑的浏览器里打开配对页并批准。", "Open the pairing page in this computer's browser and approve it."),
    ("phone", "core", "required", "local", ["desktop"], "主力手机配对", "Pair your main phone",
     "用手机扫码配对，并发一条消息试试。", "Scan the pairing code with your phone and send a message."),
    ("passkey", "core", "required", "phone", ["phone"], "手机通行密钥", "Phone passkey",
     "在手机上设置 Face ID / 指纹通行密钥，用于批准与恢复。", "Set up a Face ID / fingerprint passkey on the phone for approvals and recovery."),
    ("admin", "core", "required", "phone", ["phone"], "手机批准钥匙", "Phone approval key",
     "手机已配对后自动具备；用于密码卡、密钥卡和审批。", "Comes with pairing; used for password, key and approval cards."),
    ("phonepermissions", "core", "optional", "phone", ["phone"], "手机麦克风与相机", "Phone microphone and camera",
     "语音和拍照要用时在手机上允许，不必回电脑。", "Allow them on the phone when you want voice or photos; no computer needed."),
    ("sudo", "machine", "conditional", "local", ["admin"], "Mac 本机 Touch ID 诊断", "Mac local Touch ID check",
     "若 sudo 要求本机 Touch ID，远程会卡住；我只诊断并告诉你怎么在电脑上处理。",
     "If sudo asks for local Touch ID, remote work stalls; I only diagnose and tell you what to do at the computer."),
    ("awake", "machine", "required", "auto", ["admin"], "接电源、不睡眠", "Plugged in, no sleep",
     "插上电源，我来关闭自动睡眠（屏幕可以关）；改系统设置会发手机密码卡。",
     "Plug in; I turn off idle sleep (the screen may turn off). System changes come as a phone password card."),
    ("service", "machine", "required", "auto", ["harness"], "后台服务", "Background service",
     "让 Agent J 随电脑登录自动运行。", "Keep Agent J running whenever you are logged in."),
    ("updates", "machine", "required", "phone", ["phone"], "夜间自动升级策略", "Night-time automatic updates",
     "默认开启，可以说「关掉自动升级」。", "On by default; say \"turn off automatic updates\" to stop them."),
    ("network", "machine", "required", "auto", ["service"], "网络连通", "Network connection",
     "电脑能连上中转；不需要你操作。", "The computer reaches the relay; nothing to do."),
    ("browser", "browser", "optional", "auto", ["service"], "专用浏览器", "Agent browser",
     "给 Agent 用的浏览器，用来登录你常用的网站。", "A browser for the Agent, used for your sites."),
    ("gmail", "browser", "optional", "local", ["browser"], "Gmail 网页登录", "Gmail website sign-in",
     "推荐第一个：在电脑的正式登录页登录 Google。", "Recommended first: sign in to Google on the real sign-in page on this computer."),
    ("youtube", "browser", "optional", "local", ["gmail"], "YouTube Studio 网页", "YouTube Studio website",
     "要发布视频时再登录。", "Sign in when you publish videos."),
    ("bili", "browser", "optional", "local", ["browser"], "B站", "Bilibili", "需要时登录。", "Sign in if you use it."),
    ("douyin", "browser", "optional", "local", ["browser"], "抖音", "Douyin", "需要时登录。", "Sign in if you use it."),
    ("wechat", "browser", "optional", "local", ["browser"], "公众号 / 视频号", "WeChat Official Accounts / Channels",
     "需要时登录（按站点分别验证）。", "Sign in if you use them (checked per site)."),
    ("sites", "browser", "optional", "local", ["browser"], "其他网站", "Other websites", "需要时登录。", "Sign in if you use them."),
    ("google", "google", "optional", "local", ["keyring"], "Google API（邮件/日历/YouTube）", "Google API (mail, calendar, YouTube)",
     "选好用途后，在你自己的 Google 项目里授权；完整一键接入在 0.18.1。",
     "Choose the uses, then authorize your own Google project; the full guided connection arrives in 0.18.1."),
    ("keyring", "google", "conditional", "auto", ["service"], "本机钥匙串", "Local keychain",
     "授权凭据保存在电脑的系统钥匙串里。", "Authorization is stored in the computer's system keychain."),
    ("files", "privacy", "conditional", "auto", ["harness"], "工作文件夹", "Work folder",
     "Agent 能读写的工作文件夹。", "The folder the Agent works in."),
    ("fda", "privacy", "optional", "local", ["files"], "Mac 完全磁盘访问", "Mac Full Disk Access",
     "只有确实需要受保护文件时才开。", "Only when a task truly needs protected files."),
    ("accessibility", "privacy", "optional", "local", ["harness"], "Mac 辅助功能（桌面控制）", "Mac Accessibility (desktop control)",
     "只有要控制桌面程序时才开。", "Only for desktop control."),
    ("automation", "privacy", "optional", "local", ["harness"], "Mac 自动化（Apple Events）", "Mac Automation (Apple Events)",
     "只有要控制其他 Mac 应用时才开。", "Only for controlling other Mac apps."),
    ("screen", "privacy", "optional", "local", ["harness"], "屏幕录制", "Screen recording",
     "只有要截屏或以后远程查看时才开。", "Only for screen capture or a future remote view."),
    ("hardware", "privacy", "optional", "local", ["harness"], "电脑摄像头 / 麦克风", "Computer camera / microphone",
     "只有任务要用电脑硬件时才开。", "Only when a task uses the computer's hardware."),
    ("tg", "optional", "optional", "phone", ["phone"], "Telegram 提醒", "Telegram notifications",
     "可选；不用就说「我不用」。", "Optional; say \"I won't use this\" if not needed."),
    ("botkey", "optional", "optional", "phone", ["admin"], "客服 Bot 模型服务", "Customer-service bot model",
     "可选；要做网站客服时再用手机密钥卡配置。", "Optional; configure it with a phone key card when you run a website bot."),
    ("exit", "core", "required", "phone", ["desktop", "phone", "service", "awake", "network"], "离机前手机检查", "Phone check before leaving",
     "让电脑屏幕熄灭，手机关掉 Wi-Fi 用蜂窝网络发一句话，收到回复就完成。",
     "Let the computer's screen turn off, switch the phone to mobile data and send a message; a reply completes it."),
]
BY_ID = {i[0]: i for i in ITEMS}
REQUIRED = tuple(i[0] for i in ITEMS if i[2] == "required")
_ADAPTERS: dict = {}       # item id → probe(st, ctx) → (state, reason); other modules (browser login, P90) register here


def register_adapter(item: str, fn) -> None:
    """Another module owns an item's real check (e.g. the 0.18 browser login health check for browser / gmail / …)."""
    if item not in BY_ID:
        raise ValueError("unknown item")
    _ADAPTERS[item] = fn


class SetupError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# ------------------------------------------------------------------ storage
def path(st) -> pathlib.Path:
    return st.root / DIR / FILE


def _dir(st) -> pathlib.Path:
    d = st.root / DIR
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    if d.is_symlink() or not d.is_dir():
        raise SetupError("storage")
    os.chmod(d, 0o700)
    return d


@contextlib.contextmanager
def _lock(st):
    fd = os.open(_dir(st) / "checklist.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _now() -> int:
    return int(time.time())


def _blank(st) -> dict:
    from . import __version__
    now = _now()
    host = hashlib.sha256(st.signing_pub()).hexdigest()[:16] if st.exists() else ""
    return {"schema_version": 1, "host_ref": host, "revision": 1, "source_versions": {"agentj": __version__},
            "created_at": now, "updated_at": now, "resumed_at": now, "closed": False,
            "items": {i[0]: {"choice": "selected" if i[2] == "required" else "undecided", "state": "pending", "reason": None,
                             "checked_at": None, "expires_at": None, "attempt_id": None, "owner_decision_ref": None}
                      for i in ITEMS},
            "exit": {"armed_at": None, "evidence": None}}


def read(st) -> dict | None:
    """The journal, or None when the owner never started it (needs_setup). A broken file reads as a fresh start, flagged."""
    try:
        rec = json.loads(path(st).read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError, UnicodeDecodeError):
        rec = None
    if not isinstance(rec, dict) or rec.get("schema_version") != 1 or not isinstance(rec.get("items"), dict):
        fresh = _blank(st)
        fresh["recovered"] = True
        return fresh
    base = _blank(st)
    base.update({k: rec[k] for k in base if k in rec})
    for iid, row in base["items"].items():
        got = rec["items"].get(iid)
        if isinstance(got, dict):
            row.update({k: got[k] for k in row if k in got})
        if row["choice"] not in CHOICES or (BY_ID[iid][2] == "required" and row["choice"] != "selected"):
            row["choice"] = "selected" if BY_ID[iid][2] == "required" else "undecided"
        if row["state"] not in STATES:
            row["state"] = "pending"
    if not isinstance(base.get("exit"), dict):
        base["exit"] = {"armed_at": None, "evidence": None}
    return base


def _save(st, rec: dict) -> None:
    rec["updated_at"] = _now()
    st.write_private(_dir(st) / FILE, json.dumps(rec, ensure_ascii=False, sort_keys=True).encode())


# ------------------------------------------------------------------ probes (read-only; never log in, never show a QR)
def _system() -> str:
    from . import keep_awake
    return keep_awake.system()


def _devices(st) -> dict:
    from . import onboarding
    out = {"computer": [], "phone": []}
    for did, d in (st.devices() or {}).items():
        k = onboarding.kind_of(d.get("name", "") if isinstance(d, dict) else "")
        if k in out:
            out[k].append(did)
    return out


def _keyring_ok() -> tuple[str, str]:
    sysname = _system()
    if sysname == "macos":
        return "verified", "keychain"
    if sysname in ("linux", "wsl"):
        if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            return "attention", "no_session_bus"
        tool = shutil.which("busctl")
        if not tool:
            return "attention", "no_probe_tool"
        try:
            p = subprocess.run([tool, "--user", "--no-pager", "list", "--acquired"], capture_output=True, text=True, timeout=5,
                               stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            return "attention", "probe_failed"
        if "org.freedesktop.secrets" in p.stdout:
            return "verified", "secret_service"
        return "attention", "no_secret_service"
    return "unsupported", "platform"


def probe(st, iid: str, ctx: dict) -> tuple[str, str | None]:
    """(state, reason) for one item, from the real state of this computer. ctx carries one-run facts (serve status…)."""
    if iid in _ADAPTERS:
        try:
            return _ADAPTERS[iid](st, ctx)
        except Exception:   # noqa: BLE001 — an adapter failure is attention, never a crash
            return "attention", "adapter_error"
    sysname = ctx["system"]
    mac = sysname == "macos"
    if iid == "seat":
        from . import cloud
        return ("verified", None) if cloud.read_cloud(st) else ("waiting_local", "not_bound")
    if iid == "harness":
        c = st.agent_config()
        if not c:
            return "waiting_local", "no_agent"
        if not os.path.isdir(c.get("dir") or ""):
            return "attention", "folder_missing"
        serve = ctx.get("serve") or {}
        if serve and serve.get("agent") is None:
            return "attention", "agent_not_running"
        return "verified", None
    if iid in ("desktop", "phone"):
        return ("verified", None) if ctx["devices"]["computer" if iid == "desktop" else "phone"] else ("waiting_local", "not_paired")
    if iid == "passkey":
        phones = ctx["devices"]["phone"]
        if not phones:
            return "pending", "needs_phone"
        return ("verified", None) if any(st.passkey_of_device(d) for d in phones) else ("waiting_local", "no_passkey")
    if iid == "admin":
        phones = ctx["devices"]["phone"]
        if not phones:
            return "pending", "needs_phone"
        return ("verified", None) if any(st.sign_key(d) for d in phones) else ("attention", "no_approval_key")
    if iid == "phonepermissions":
        rep = ctx.get("phone_report") or {}
        if rep.get("mic") == "granted":
            return "verified", None
        return ("waiting_local", "phone_permission") if rep else ("pending", "ask_on_phone")
    if iid == "sudo":
        if not mac:
            return "not_applicable", "not_macos"
        from . import elevate
        try:
            risk = elevate.local_auth_risk()
        except Exception:   # noqa: BLE001
            return "attention", "pam_unreadable"
        return ("attention", "local_touch_id") if risk else ("verified", None)
    if iid == "awake":
        from . import keep_awake
        s = keep_awake.status()
        if s.get("awake") is True:
            return "verified", None
        if s.get("awake") is False:
            return "waiting_local", "sleep_enabled"
        return ("waiting_local", "windows_host") if s.get("system") == "wsl" else ("unsupported", "platform")
    if iid == "service":
        from . import service
        svc = ctx.get("service") or service.status()
        if not svc.get("installed"):
            return "waiting_local", "not_installed"
        return ("verified", None) if svc.get("active") == "active" else ("attention", "not_active")
    if iid == "updates":
        from . import auto_update
        auto_update.enabled(st)     # the policy reads back (on or off are both a decision)
        return "verified", None
    if iid == "network":
        serve = ctx.get("serve")
        if serve is None:
            return "attention", "serve_not_running"
        return ("verified", None) if serve.get("relay_up") else ("attention", "relay_down")
    if iid in ("browser", "gmail", "youtube", "bili", "douyin", "wechat", "sites"):
        from . import browser_sites
        return browser_sites.first_run_probe(st, iid, ctx)
    if iid == "google":
        from . import google
        t = google.status(st)["tool"]
        return "unsupported", "api_in_0.18.1" if t["state"] in ("installed", "absent") else "google_off"
    if iid == "keyring":
        return _keyring_ok()
    if iid == "files":
        c = st.agent_config()
        d = (c or {}).get("dir")
        if not d:
            return "pending", "no_agent"
        return ("verified", None) if os.path.isdir(d) and os.access(d, os.W_OK) else ("attention", "not_writable")
    if iid in ("fda", "accessibility", "automation", "screen", "hardware"):
        return ("unsupported", "tcc_check_pending") if mac else ("not_applicable", "not_macos")
    if iid == "tg":
        from . import telegram
        return ("verified", None) if telegram.configuration(st) else ("pending", "not_configured")
    if iid == "botkey":
        from . import provider_profiles
        try:
            return ("verified", None) if provider_profiles.listing() else ("pending", "not_configured")
        except (OSError, ValueError):
            return "attention", "unreadable"
    if iid == "exit":
        return _exit_state(st, ctx)
    return "unsupported", "unknown"


def _exit_state(st, ctx) -> tuple[str, str | None]:
    rec = ctx["journal"]
    ev = rec["exit"].get("evidence")
    if ev and _now() - int(ev.get("at", 0)) <= EXIT_FRESH:
        return "verified", None
    armed = rec["exit"].get("armed_at")
    if armed and _now() - int(armed) <= EXIT_WINDOW:
        return "waiting_local", "send_from_phone"
    return "pending", "start_check"


def _ctx(st, rec: dict, serve: dict | None, phone_report: dict | None) -> dict:
    return {"system": _system(), "devices": _devices(st), "serve": serve, "journal": rec, "phone_report": phone_report}


def _serve_status(st) -> dict | None:
    from . import names
    try:
        return names.ctl_call(st, {"cmd": "status"}, 5)
    except Exception:   # noqa: BLE001 — busy / not running
        return None


def _order() -> list[str]:
    return [i[0] for g in GROUPS for i in ITEMS if i[1] == g and i[0] != "exit"] + ["exit"]


def run_checks(st, rec: dict, only: list[str] | None = None, serve: dict | None = None, phone_report: dict | None = None) -> dict:
    """Probe every applicable item (or `only`) whose choice is selected / undecided; unused items are not probed."""
    ctx = _ctx(st, rec, serve, phone_report)
    now = _now()
    for iid in _order():
        if only and iid not in only:
            continue
        row = rec["items"][iid]
        if row["choice"] == "unused":
            continue
        state, reason = probe(st, iid, ctx)
        if row["state"] == "deferred" and state not in ("verified", "not_applicable"):
            state, reason = "deferred", row["reason"]       # "later" stays later until it passes or the owner picks it again
        exp = now + TTL["browser" if BY_ID[iid][1] == "browser" else "default"] if state == "verified" else None
        ev = rec["exit"].get("evidence") if iid == "exit" else None
        if exp and ev:                                # the round trip itself is only good for EXIT_FRESH
            exp = min(exp, int(ev.get("at", 0)) + EXIT_FRESH)
        row.update(state=state, reason=reason, checked_at=now, expires_at=exp)
    return rec


# ------------------------------------------------------------------ summary, predicate, card
def fresh(row: dict, now: int | None = None) -> bool:
    return row["state"] == "verified" and bool(row.get("expires_at")) and (now or _now()) < int(row["expires_at"])


def remote_ready(rec: dict, now: int | None = None) -> tuple[bool, list[str]]:
    """remote_ready_for_selected_goals: every selected, applicable item verified and fresh, nothing selected waiting /
    failing / deferred / unsupported, and a recent phone round trip (the exit item). Returns (ready, blocking item ids)."""
    now = now or _now()
    block = []
    for iid in _order():
        row = rec["items"][iid]
        if row["choice"] != "selected" or row["state"] == "not_applicable":
            continue
        if not fresh(row, now):
            block.append(iid)
    return (not block), block


def current(rec: dict) -> str | None:
    """The one item the card shows next: dependency order; deferred items only when nothing else is left."""
    later = None
    for iid in _order():
        row = rec["items"][iid]
        if row["choice"] == "unused" or row["state"] in ("not_applicable",) or fresh(row):
            continue
        if row["state"] == "deferred":
            later = later or iid
            continue
        if row["choice"] == "undecided" and row["state"] in ("unsupported",) and BY_ID[iid][2] != "required":
            continue          # not offered while it cannot be done in this version; listed in the overview
        return iid
    return later


def counts(rec: dict) -> dict:
    c = {"verified": 0, "unused": 0, "todo": 0, "auto": 0, "unsupported": 0, "undecided": 0, "not_applicable": 0}
    for iid, row in rec["items"].items():
        if row["choice"] == "unused":
            c["unused"] += 1
        elif row["state"] == "not_applicable":
            c["not_applicable"] += 1
        elif fresh(row):
            c["verified"] += 1
        elif row["choice"] == "undecided":
            c["undecided"] += 1
        elif row["state"] == "unsupported":
            c["unsupported"] += 1
        else:
            c["todo"] += 1
            if BY_ID[iid][3] == "auto":
                c["auto"] += 1
    return c


def snapshot(rec: dict) -> dict:
    """What the card and `--json` show: no secrets, no paths, no device ids — titles, choices, states and reason words."""
    ready, block = remote_ready(rec)
    cur = current(rec)
    items = []
    for iid, group, need, where, deps, zh, en, hzh, hen in ITEMS:
        row = rec["items"][iid]
        items.append({"id": iid, "group": group, "need": need, "where": where, "depends_on": deps,
                      "title": {"zh": zh, "en": en}, "hint": {"zh": hzh, "en": hen}, "choice": row["choice"],
                      "state": "verified" if fresh(row) else ("stale" if row["state"] == "verified" else row["state"]),
                      "reason": row["reason"], "checked_at": row["checked_at"]})
    gi = GROUPS.index(BY_ID[cur][1]) + 1 if cur else len(GROUPS)
    return {"revision": rec["revision"], "items": items, "current": cur, "counts": counts(rec), "group": gi,
            "groups": [{"id": g, "title": {"zh": GROUP_TITLE[g][0], "en": GROUP_TITLE[g][1]}} for g in GROUPS],
            "remote_ready": ready, "blocking": block, "exit_armed": bool(rec["exit"].get("armed_at")) and
            _now() - int(rec["exit"]["armed_at"]) <= EXIT_WINDOW, "updated_at": rec["updated_at"],
            "closed": rec.get("closed", False)}


def active(rec: dict | None) -> bool:
    if not rec or rec.get("closed"):
        return False
    return _now() - int(rec.get("resumed_at") or 0) <= ACTIVE_SECS and not remote_ready(rec)[0]


# ------------------------------------------------------------------ operations (host side)
def status_(st) -> dict:
    """`agentj setup checklist --json`: read-only. A missing journal is reported, never created."""
    rec = read(st)
    if rec is None:
        return {"ok": True, "result": "needs_setup", "next": "agentj setup checklist --resume --json"}
    return {"ok": True, "result": "ready" if remote_ready(rec)[0] else "in_progress", **snapshot(rec)}


def resume(st, serve: dict | None = None) -> dict:
    """Explicit start / continue: create the journal if needed, probe everything, return the card snapshot."""
    with _lock(st):
        rec = read(st) or _blank(st)
        rec["resumed_at"] = _now()
        rec["closed"] = False
        run_checks(st, rec, serve=serve)
        rec["revision"] += 1
        _save(st, rec)
    return {"ok": True, "result": "ready" if remote_ready(rec)[0] else "in_progress", **snapshot(rec)}


def check(st, only: list[str] | None = None, serve: dict | None = None, phone_report: dict | None = None) -> dict:
    """Live read-only probes; refreshes the result metadata (revision unchanged: no owner choice changed)."""
    if only and any(i not in BY_ID for i in only):
        raise SetupError("unknown_item")
    with _lock(st):
        rec = read(st)
        if rec is None:
            return {"ok": True, "result": "needs_setup", "next": "agentj setup checklist --resume --json"}
        run_checks(st, rec, only, serve=serve, phone_report=phone_report)
        _save(st, rec)
    return {"ok": True, "result": "ready" if remote_ready(rec)[0] else "in_progress", **snapshot(rec)}


def mark(st, item: str, choice: str, revision: int, decision_ref: str, serve: dict | None = None) -> dict:
    """The owner's signed choice from the card (verified by the caller). CAS on revision; required items cannot be unused."""
    if item not in BY_ID or choice not in MARKS:
        raise SetupError("shape")
    with _lock(st):
        rec = read(st)
        if rec is None:
            raise SetupError("not_found")
        if rec["revision"] != revision:
            raise SetupError("changed")
        row = rec["items"][item]
        need = BY_ID[item][2]
        if choice == "unused":
            if need == "required":
                raise SetupError("invalid")
            row.update(choice="unused", state="pending", reason="owner_unused", expires_at=None)
        elif choice == "selected":
            row["choice"] = "selected"
            if row["state"] == "deferred":
                row["state"] = "pending"
        elif choice == "later":                    # unfinished, kept as a to-do; never the same as "I won't use this"
            row.update(state="deferred", reason="owner_later", expires_at=None)
        elif choice == "start_exit":
            if item != "exit":
                raise SetupError("invalid")
            rec["exit"] = {"armed_at": _now(), "evidence": None}
        row["owner_decision_ref"] = decision_ref[:64]
        if choice != "later":
            run_checks(st, rec, [item], serve=serve)
        rec["revision"] += 1
        _save(st, rec)
    return {"ok": True, **snapshot(rec)}


def phone_round_trip(st, device_kind: str, net: str | None = None, serve: dict | None = None) -> dict | None:
    """serve: a message from a phone arrived. When the exit check is armed, it is the evidence (phone → relay → host); the
    reply the Agent sends back completes the round trip. Returns the new snapshot when the exit item changed."""
    if device_kind != "phone":
        return None
    with _lock(st):
        rec = read(st)
        if rec is None or not rec["exit"].get("armed_at") or _now() - int(rec["exit"]["armed_at"]) > EXIT_WINDOW:
            return None
        rec["exit"] = {"armed_at": None, "evidence": {"at": _now(), "via": "relay", "device": "phone",
                                                      "net": net if net in ("cellular", "wifi", "unknown") else "unknown"}}
        run_checks(st, rec, serve=serve)
        rec["revision"] += 1
        _save(st, rec)
    return snapshot(rec)


def close(st) -> None:
    with contextlib.suppress(SetupError, OSError):
        with _lock(st):
            rec = read(st)
            if rec:
                rec["closed"] = True
                rec["revision"] += 1
                _save(st, rec)


def doctor_row(st) -> tuple[str, str, str]:
    rec = read(st)
    if rec is None:
        return "ok", "首次准备：未开始（可说「电脑前一次做完」）/ first-run checklist: not started", ""
    ready, block = remote_ready(rec)
    if ready:
        return "ok", "首次准备：已选事项均已验证，可离机 / first-run: selected items verified, ready to leave", ""
    c = counts(rec)
    return ("warn", f"首次准备：已验证 {c['verified']}、我不用 {c['unused']}、待办 {c['todo']} / first-run: "
            f"{c['verified']} verified, {c['unused']} unused, {c['todo']} to do", "agentj setup checklist --resume")


def leave_text(rec: dict, lang: str = "zh") -> str:
    """The leave-the-computer confirmation — only when remote_ready; otherwise what is usable now and what is left."""
    ready, block = remote_ready(rec)
    snap = snapshot(rec)
    title = {i["id"]: i["title"][lang] for i in snap["items"]}
    sep = "、" if lang == "zh" else ", "
    unused = [title[i] for i, r in rec["items"].items() if r["choice"] == "unused"]
    at = time.strftime("%H:%M", time.localtime())
    if ready:
        ok = [title[i] for i, r in rec["items"].items() if r["choice"] == "selected" and fresh(r)]
        if lang == "zh":
            return (f"从现在起，你可以离开电脑，用手机继续处理已选并验证的事项。截至 {at}：{sep.join(ok)} 已就绪。"
                    + (f"未启用：{sep.join(unused)}。" if unused else "")
                    + "断电、重启后解锁、新的系统权限或账号安全检查仍可能需要你回到电脑。")
        return (f"You can now leave your computer and continue the selected, verified tasks from your phone. As of {at}: "
                f"{sep.join(ok)} ready." + (f" Not enabled: {sep.join(unused)}." if unused else "")
                + " Power loss, a restart that needs unlocking, a new system permission or an account security check may still need you at the computer.")
    left = [title[i] for i in block]
    if lang == "zh":
        return f"手机对话已可用，离机检查还有 {len(left)} 项：{sep.join(left)}。"
    return f"Phone chat works; {len(left)} item(s) left before leaving: {sep.join(left)}."


# ------------------------------------------------------------------ CLI (`agentj setup checklist`)
def execute(op: str, st, item: str | None = None) -> dict:
    serve = _serve_status(st) if op != "status" else None
    if op == "status":
        return status_(st)
    if op == "resume":
        return resume(st, serve)
    if op == "check":
        return check(st, [item] if item else None, serve)
    raise SetupError("shape")


def _text(res: dict, lang: str = "zh") -> str:
    if res.get("result") == "needs_setup":
        return "还没开始首次准备。主人同意后运行：agentj setup checklist --resume / Not started; with the owner's go-ahead run: agentj setup checklist --resume"
    c = res["counts"]
    head = (f"首次准备 rev {res['revision']}：已验证 {c['verified']}、我不用 {c['unused']}、待办 {c['todo']}"
            f"（其中自动处理 {c['auto']}）、待定 {c['undecided']}、当前版本不支持 {c['unsupported']} / "
            f"{c['verified']} verified, {c['unused']} unused, {c['todo']} to do, {c['undecided']} undecided")
    rows = [head]
    for i in res["items"]:
        mark_ = {"verified": "✓", "not_applicable": "–", "unsupported": "·"}.get(i["state"], "!" if i["choice"] == "selected" else "?")
        if i["choice"] == "unused":
            mark_ = "×"
        rows.append(f" {mark_} {i['id']:<16} {i['choice']:<10} {i['state']:<14} {i['reason'] or ''}")
    rows.append("就绪 / ready: " + ("是 yes" if res["remote_ready"] else "否 no · " + ", ".join(res["blocking"])))
    return "\n".join(rows)


def cmd(a) -> int:
    from . import elevate
    from .state import State
    st = State()
    op = "resume" if a.resume else "check" if (a.check or a.item) else "status"
    res = elevate.client_request(st, {"t": "setup", "op": op, **({"item": a.item} if a.item else {})}, timeout=120)
    if res.get("result") == "unavailable":         # serve not running: the CLI reads / writes the journal itself
        try:
            res = execute(op, st, a.item)
        except SetupError as e:
            res = {"ok": False, "why": e.reason}
        except PermissionError:
            res = {"ok": False, "why": "fenced", "detail": "Agent J is not running (agentj service status)"}
    if a.json or not res.get("ok"):
        print(json.dumps(res, ensure_ascii=False))
    else:
        print(_text(res))
    return 0 if res.get("ok") else 1


def add_parser(sub) -> None:
    p = sub.add_parser("setup", help="首次准备：电脑前一次做完 / first-run checklist while you are at the computer")
    ss = p.add_subparsers(dest="setup_cmd", required=True)
    c = ss.add_parser("checklist", help="首次准备清单：只读，或 --resume 开始/继续 / the checklist: read-only, or --resume to start")
    g = c.add_mutually_exclusive_group()
    g.add_argument("--resume", action="store_true", help="开始或继续，并把卡片发到手机 / start or continue and show the card")
    g.add_argument("--check", action="store_true", help="实时只读复查 / live read-only re-check")
    g.add_argument("--item", choices=[i[0] for i in ITEMS], help="只复查这一项 / re-check one item")
    c.add_argument("--json", action="store_true")
    c.set_defaults(fn=lambda a: sys.exit(cmd(a)))
