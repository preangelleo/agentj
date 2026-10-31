"""P92 / ADR-A196: the first-run "at the Agent J host" checklist — `agentj setup checklist [--resume | --check | --item ID]`.

Developer reference: documentation/notes/P0263-HOST-REFERENCE.md#first-run.
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
MARKS = ("selected", "unused", "later", "start_exit",
         "install_browser", "adopt_browser", "open_login")     # P131: owner actions (the host performs them after the mark)
ACTIONS = MARKS[4:]

# The 30 items (P92 INVENTORY.md / inventory.json). need: required | conditional | optional. where: auto | local | phone.
# Titles and the one-line "what you do" are the card's text; the Agent explains more in chat.
ITEMS = [
    ("seat", "core", "required", "local", [], "账号与席位绑定", "Account and seat",
     "用设置码把这台Agent J 主机加入你的 Agent J 账号。", "Join the Agent J host to your Agent J account with the setup code."),
    ("harness", "core", "required", "local", ["seat"], "主 AI 登录与权限", "Main AI login and access",
     "在Agent J 主机上登录你选的 AI 工具（Claude Code / Codex / OpenCode）。", "Sign in to your chosen AI tool on the Agent J host."),
    ("desktop", "core", "required", "local", ["harness"], "遥控器浏览器配对", "Pair the Agent J host's browser",
     "在这台Agent J 主机的浏览器里打开配对页并批准。", "Open the pairing page in the Agent J host's browser and approve it."),
    ("phone", "core", "required", "local", ["desktop"], "主力手机配对", "Pair your main phone",
     "用手机扫码配对，并发一条消息试试。", "Scan the pairing code with your phone and send a message."),
    ("passkey", "core", "required", "phone", ["phone"], "手机通行密钥", "Phone passkey",
     "在手机上设置 Face ID / 指纹通行密钥，用于批准与恢复。", "Set up a Face ID / fingerprint passkey on the phone for approvals and recovery."),
    ("admin", "core", "required", "phone", ["phone"], "手机批准密钥", "Phone approval key",
     "手机已配对后自动具备；用于密码卡、密钥卡和审批。", "Comes with pairing; used for password, key and approval cards."),
    ("phonepermissions", "core", "optional", "phone", ["phone"], "手机麦克风与相机", "Phone microphone and camera",
     "语音和拍照要用时在手机上允许，不必回Agent J 主机。", "Allow them on the phone when you want voice or photos; no computer needed."),
    ("sudo", "machine", "conditional", "local", ["admin"], "Mac 本机 Touch ID 诊断", "Mac local Touch ID check",
     "若 sudo 要求本机 Touch ID，远程会卡住；我只诊断并告诉你怎么在Agent J 主机上处理。",
     "If sudo asks for local Touch ID, remote work stalls; I only diagnose and tell you what to do at the Agent J host."),
    ("awake", "machine", "required", "auto", ["admin"], "接电源、不睡眠", "Plugged in, no sleep",
     "插上电源，我来关闭自动睡眠（屏幕可以关）；改系统设置会发手机密码卡。",
     "Plug in; I turn off idle sleep (the screen may turn off). System changes come as a phone password card."),
    ("service", "machine", "required", "auto", ["harness"], "后台服务", "Background service",
     "让 Agent J 随Agent J 主机登录自动运行。", "Keep Agent J running whenever you are logged in."),
    ("updates", "machine", "required", "phone", ["phone"], "夜间自动升级策略", "Night-time automatic updates",
     "默认开启，可以说「关掉自动升级」。", "On by default; say \"turn off automatic updates\" to stop them."),
    ("network", "machine", "required", "auto", ["service"], "网络连通", "Network connection",
     "Agent J 主机能连上中转；不需要你操作。", "the Agent J host reaches the relay; nothing to do."),
    ("browser", "browser", "optional", "auto", ["service"], "专用浏览器", "Agent browser",
     "给 Agent 用的浏览器，用来登录你常用的网站。", "A browser for the Agent, used for your sites."),
    ("gmail", "browser", "optional", "local", ["browser"], "Gmail 网页登录", "Gmail website sign-in",
     "推荐第一个：在Agent J 主机的正式登录页登录 Google。", "Recommended first: sign in to Google on the real sign-in page on the Agent J host."),
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
     "授权凭据保存在Agent J 主机的系统钥匙串里。", "Authorization is stored in the Agent J host's system keychain."),
    ("files", "privacy", "conditional", "auto", ["harness"], "工作文件夹", "Work folder",
     "Agent 能读写的工作文件夹。", "The folder the Agent works in."),
    ("macfiles", "privacy", "conditional", "local", ["files"], "Mac 文件夹与 Finder 权限", "Mac folders and Finder access",
     "现在在 Agent J 主机前运行 agentj files prepare --at-computer，允许下载、桌面、文稿及 Finder 自动化；手机不能代替系统授权。",
     "At the computer, run agentj files prepare --at-computer; allow Downloads, Desktop, Documents and Finder Automation."),
    ("fda", "privacy", "optional", "local", ["files"], "Mac 完全磁盘访问", "Mac Full Disk Access",
     "只有确实需要受保护文件时才开。", "Only when a task truly needs protected files."),
    ("accessibility", "privacy", "optional", "local", ["harness"], "Mac 辅助功能（桌面控制）", "Mac Accessibility (desktop control)",
     "只有要控制桌面程序时才开。", "Only for desktop control."),
    ("automation", "privacy", "optional", "local", ["harness"], "Mac 自动化（Apple Events）", "Mac Automation (Apple Events)",
     "只有要控制其他 Mac 应用时才开。", "Only for controlling other Mac apps."),
    ("screen", "privacy", "optional", "local", ["harness"], "屏幕录制", "Screen recording",
     "只有要截屏或以后远程查看时才开。", "Only for screen capture or a future remote view."),
    ("hardware", "privacy", "optional", "local", ["harness"], "Agent J 主机摄像头 / 麦克风", "Computer camera / microphone",
     "只有任务要用Agent J 主机硬件时才开。", "Only when a task uses the Agent J host's hardware."),
    ("wx", "optional", "optional", "phone", ["phone"], "微信 ClawBot", "WeChat ClawBot",
     "大陆网络／中文用户优先；仅服务扫码者本人，也可同时开 Telegram。",
     "For mainland networks / Chinese speakers, offer first. Owner only; Telegram can stay on too."),
    ("tg", "optional", "optional", "phone", ["phone"], "Telegram 提醒", "Telegram notifications",
     "可选；不用就说「我不用」。", "Optional; say \"I won't use this\" if not needed."),
    ("mail", "optional", "optional", "phone", ["admin"], "邮件通道（QQ / 163 / 企业邮箱等）", "E-mail channel (any IMAP/SMTP mailbox)",
     "可选；用邮件给 Agent 派活。在手机「设置 → 邮件」填邮箱、白名单和授权码；不用就说「我不用」。",
     "Optional; send the Agent tasks by e-mail. On the phone: Settings → Email (mailbox, allowlist, app password). Say \"I won't use this\" if not needed."),
    ("botkey", "optional", "optional", "phone", ["admin"], "客服 Bot 模型服务", "Customer-service bot model",
     "可选；要做网站客服时再用手机密钥卡配置。", "Optional; configure it with a phone key card when you run a website bot."),
    ("exit", "core", "required", "phone", ["desktop", "phone", "service", "awake", "network"], "离机前手机检查", "Phone check before leaving",
     "让Agent J 主机屏幕熄灭，手机关掉 Wi-Fi 用蜂窝网络发一句话，收到回复就完成。",
     "Let the Agent J host's screen turn off, switch the phone to mobile data and send a message; a reply completes it."),
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
            "exit": {"armed_at": None, "evidence": None}, "action": None, "browser_source": None, "ai_route": None}


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
    base.update({k: rec[k] for k in base if k in rec and k != "items"})   # items merge row by row below: a newer
    # release's item (P170 "mail") keeps its blank row instead of vanishing with the old dict (0.20.0 KeyError: 'mail')
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
    if not isinstance(base.get("action"), dict):
        base["action"] = None
    if not isinstance(base.get("browser_source"), dict):
        base["browser_source"] = None
    from . import ai_route
    base["ai_route"] = ai_route.journal_entry(base.get("ai_route"))
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
    """(state, reason) for one item, from the real state of the Agent J host. ctx carries one-run facts (serve status…)."""
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
        # P279: where Claude Code / Codex are not offered (the last `agentj agent route`), the row recommends Ark / Kimi (P335)
        from . import ai_route
        route = ai_route.cached(st)
        kimi = bool(route and route["route"] == "kimi")
        ctx["ai_route"] = {"route": route["route"], "link": route["link"]} if kimi else None
        if not c:
            return "waiting_local", "no_agent_kimi" if kimi else "no_agent"
        if not os.path.isdir(c.get("dir") or ""):
            return "attention", "folder_missing"
        serve = ctx.get("serve") or {}
        if serve and serve.get("agent") is None:
            return "attention", "agent_not_running_kimi" if kimi and c.get("kind") in ("claude", "codex") else "agent_not_running"
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
    if iid == "macfiles":
        if not mac:
            return "not_applicable", "not_macos"
        from . import local_files
        tcc = local_files.tcc_status(st) or {}
        ready = all((tcc.get("folders") or {}).get(k) == "readable" for k in ("Downloads", "Desktop", "Documents")) and tcc.get("automation") == "available"
        return ("verified", None) if ready and time.time() - tcc.get("at", 0) < TTL["default"] else ("waiting_local", "tcc_check_pending")
    if iid in ("fda", "accessibility", "automation", "screen", "hardware"):
        return ("unsupported", "tcc_check_pending") if mac else ("not_applicable", "not_macos")
    if iid == "wx":
        view = (ctx.get("serve") or {}).get("wechat")
        return ("verified", None) if isinstance(view, dict) and view.get("bound") else ("pending", "not_configured")
    if iid == "tg":
        from . import telegram
        return ("verified", None) if telegram.configured(st) else ("pending", "not_configured")   # P142 (B14): serve's env
    if iid == "mail":
        from . import mail
        return mail.first_run_probe(st, ctx)
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
    if "ai_route" in ctx:                             # P279: the harness row's Kimi link (codes + link only)
        if ctx["ai_route"]:
            rec["ai_route"] = ctx["ai_route"]
        else:
            rec.pop("ai_route", None)
    if isinstance(ctx.get("browser_source"), dict):
        rec["browser_source"] = {k: ctx["browser_source"][k] for k in ("kind", "port", "running") if k in ctx["browser_source"]}
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


# ------------------------------------------------------------------ P131: what failed, what it affects, what to do next
SITE_ITEMS = ("gmail", "youtube", "bili", "douyin", "wechat")
# reason → (zh, en) fact. {title} = this item, {browser} = which browser was checked, {port} = the adopted / found port.
FACT = {
    "browser_not_installed": ("没通过：Agent J 的专用浏览器还没安装。", "Not passed: Agent J's own browser is not installed yet."),
    "existing_browser_found": ("你Agent J 主机上已经有一个浏览器在运行（端口 {port}）。可以直接用它——你在里面的登录都能用，不用再装一个。",
                               "A browser is already running on the Agent J host (port {port}). You can use it — your sign-ins there "
                               "work and nothing else gets installed."),
    "adopted_not_running": ("没通过：你登记的浏览器（端口 {port}）现在没在运行。请在Agent J 主机上把它打开，再点「检查」；Agent J 不会替你启动或关闭它。",
                            "Not passed: the browser you registered (port {port}) is not running. Start it on the Agent J host, "
                            "then tap Check; Agent J never starts or stops it."),
    "browser_not_running": ("没通过：Agent J 的专用浏览器装好了，但现在没在运行。", "Not passed: Agent J's browser is installed but not running."),
    "browser_off": ("你关闭了 Agent J 的浏览器（browser.enabled=false）。要用网站时在聊天里说「打开浏览器」。",
                    "You turned Agent J's browser off (browser.enabled=false). Say \"turn the browser on\" in chat when you need it."),
    "needs_browser": ("还不能查：要先准备好浏览器（上面的「专用浏览器」一项）。", "Can't check yet: the browser (the item above) comes first."),
    "site_not_added": ("还没确认：Agent J 还没在{browser}里查过 {title} 是否已登录。",
                       "Not confirmed yet: Agent J has not checked {title} in {browser}."),
    "site_signin_required": ("没通过：{browser}里还没登录 {title}{other}。", "Not passed: {title} is not signed in in {browser}{other}."),
    "site_check_stale": ("还没确认：{title} 上次检查已超过 15 分钟。", "Not confirmed: the last {title} check is older than 15 minutes."),
    "site_check_needed": ("还没确认：上次检查 {title} 没得出结果（网络或页面原因，不代表没登录）。",
                          "Not confirmed: the last {title} check had no result (network or page — not the same as signed out)."),
    "not_bound": ("还没完成：这台Agent J 主机还没加入你的 Agent J 账号。", "Not done: the Agent J host has not joined your Agent J account."),
    "no_agent": ("还没完成：还没选主 AI。", "Not done: no main AI chosen yet."),
    "agent_not_running": ("没通过：主 AI 现在没在运行。", "Not passed: the main AI is not running."),
    "no_agent_kimi": ("还没完成：还没选主 AI。这台Agent J 主机所在地区用不了 Claude Code 和 Codex。中国大陆从零开始首推火山方舟 Coding Plan，"
                      "先买 Lite（和 Pro 能力一样，只差 5 倍用量；`agentj provider ark`）；香港 / 海外推荐 Kimi 会员 Plus 或更高档"
                      "（Go 档没有编程额度），用 OpenCode 接入：{kimi}。已经有其中一家的就继续用。详细步骤：`agentj agent route`。"
                      "订阅后让 Agent 发手机密钥卡收 key。",
                      "Not done: no main AI chosen yet. Claude Code and Codex are not available where the Agent J host is. In "
                      "mainland China, starting from zero, we recommend Volcano Ark Coding Plan — buy Lite first (same as Pro, "
                      "only 1/5 of the quota; `agentj provider ark`). In Hong Kong / overseas, a Kimi membership, Plus or higher "
                      "(Go has no coding quota), through OpenCode: {kimi}. Already paying for one of them? Keep it. Step by step: "
                      "`agentj agent route`. After subscribing, ask your Agent for the phone key card."),
    "agent_not_running_kimi": ("没通过：主 AI 现在没在运行，而这台Agent J 主机所在地区用不了 Claude Code 和 Codex。中国大陆推荐改用火山方舟 "
                               "Coding Plan（先买 Lite），香港 / 海外推荐 Kimi 会员 Plus 或更高档（OpenCode）：{kimi}；已经有其中一家的"
                               "就用它（`agentj agent route`）。",
                               "Not passed: the main AI is not running, and Claude Code and Codex are not available where "
                               "the Agent J host is. In mainland China, switch to Volcano Ark Coding Plan (Lite first); in Hong Kong / "
                               "overseas, a Kimi membership, Plus or higher (OpenCode): {kimi}. Already paying for one of them? "
                               "Use it (`agentj agent route`)."),
    "not_paired": ("还没完成：还没配对。", "Not done: not paired yet."),
    "no_passkey": ("还没完成：手机上还没设通行密钥（Face ID / 指纹）。", "Not done: no passkey (Face ID / fingerprint) on the phone yet."),
    "sleep_enabled": ("没通过：这台Agent J 主机还会自动睡眠，睡着时手机就找不到它。", "Not passed: the Agent J host still sleeps when idle; the phone can't reach it then."),
    "not_active": ("没通过：后台服务装了但没在运行。", "Not passed: the background service is installed but not running."),
    "serve_not_running": ("没通过：Agent J 现在没在运行。", "Not passed: Agent J is not running."),
    "relay_down": ("没通过：Agent J 主机现在连不上中转。", "Not passed: the Agent J host can't reach the relay right now."),
    "no_secret_service": ("没通过：这台 Linux 没有系统钥匙串服务。", "Not passed: this Linux has no system keychain service."),
    "local_touch_id": ("没通过：sudo 要求本机 Touch ID，远程操作会卡住。", "Not passed: sudo asks for local Touch ID, so remote work stalls."),
}
# reason → the next steps the card offers (besides "I won't use this" / "Later", which the card adds by itself)
NEXT = {"browser_not_installed": ["install_browser"], "existing_browser_found": ["adopt_browser", "install_browser"],
        "browser_not_running": ["install_browser"], "adopted_not_running": ["check"],
        "site_not_added": ["check", "open_login"], "site_signin_required": ["open_login", "check"],
        "site_check_stale": ["check"], "site_check_needed": ["check", "open_login"]}


def _dependants(iid: str) -> list[str]:
    return [i[0] for i in ITEMS if iid in i[4]]


def explain(rec: dict, iid: str) -> dict | None:
    """{fact, effect, next} for a row that is not done — plain facts, never "if this fails …"."""
    row = rec["items"][iid]
    if row["choice"] == "unused" or fresh(row) or row["state"] in ("not_applicable", "deferred"):
        return None
    src = rec.get("browser_source") or {}
    port = src.get("port") or ""
    _, group, _, _, _, zh, en, _, _ = BY_ID[iid]
    adopted = src.get("kind") == "adopted"
    fill = {"zh": {"title": zh, "port": port, "browser": f"你登记的浏览器（端口 {port}）" if adopted else "Agent J 的专用浏览器",
                   "other": "" if adopted else "（你平时用的浏览器不算）"},
            "en": {"title": en, "port": port, "browser": f"the browser you registered (port {port})" if adopted else "Agent J's own browser",
                   "other": "" if adopted else " (your everyday browser does not count)"}}
    reason = row["reason"] or ""
    if iid == "harness":                              # P279: the link and its disclosure, together
        from . import ai_route
        link = (ai_route.journal_entry(rec.get("ai_route")) or {}).get("link") or ai_route.invite(use_platform=False)
        for lang in ("zh", "en"):
            fill[lang]["kimi"] = ai_route.link_line(link, lang)
    if reason in FACT:
        fact = {lang: FACT[reason][k].format(**fill[lang]) for k, lang in enumerate(("zh", "en"))}
    elif row["state"] == "waiting_local":
        fact = {"zh": "等你在Agent J 主机上完成这一步。", "en": "Waiting for you to do this on the Agent J host."}
    elif row["state"] == "attention":
        fact = {"zh": f"没通过（原因代码：{reason or '未知'}）。", "en": f"Not passed (reason code: {reason or 'unknown'})."}
    elif row["state"] == "unsupported":
        fact = {"zh": "这一版还不支持，先跳过。", "en": "Not supported in this version yet; skip it for now."}
    elif row["state"] == "stale":
        fact = {"zh": "上次检查已过期，需要再查一次。", "en": "The last check expired; check again."}
    else:
        return None
    if group == "browser" and iid != "browser":
        effect = {"zh": f"只影响要用 {zh} 的事，其他照常。", "en": f"Only tasks that use {en} wait; everything else works."}
    elif iid == "browser":
        effect = {"zh": "只影响要用网站的事（Gmail、YouTube 等），聊天和其他事照常。",
                  "en": "Only website tasks (Gmail, YouTube…) wait; chat and everything else work."}
    else:
        deps = [BY_ID[d] for d in _dependants(iid)]
        effect = ({"zh": "会暂停：" + "、".join(d[5] for d in deps) + "。其他照常。",
                   "en": "On hold until then: " + ", ".join(d[6] for d in deps) + ". Everything else works."} if deps else
                  {"zh": "不影响其他项。", "en": "Nothing else depends on it."})
    nxt = list(NEXT.get(reason, []))
    if reason == "existing_browser_found" and not port:
        nxt = ["install_browser"]
    return {"fact": fact, "effect": effect, "next": nxt}


def progress(rec: dict) -> dict:
    """「首次准备 3/12」: verified selected items out of the selected, applicable ones."""
    sel = [r for r in rec["items"].values() if r["choice"] == "selected" and r["state"] != "not_applicable"]
    return {"done": sum(1 for r in sel if fresh(r)), "total": len(sel)}


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
                      "reason": row["reason"], "checked_at": row["checked_at"], "explain": explain(rec, iid)})
    gi = GROUPS.index(BY_ID[cur][1]) + 1 if cur else len(GROUPS)
    src = rec.get("browser_source")
    return {"revision": rec["revision"], "items": items, "current": cur, "counts": counts(rec), "group": gi,
            "groups": [{"id": g, "title": {"zh": GROUP_TITLE[g][0], "en": GROUP_TITLE[g][1]}} for g in GROUPS],
            "remote_ready": ready, "blocking": block, "exit_armed": bool(rec["exit"].get("armed_at")) and
            _now() - int(rec["exit"]["armed_at"]) <= EXIT_WINDOW, "updated_at": rec["updated_at"],
            "closed": rec.get("closed", False), "progress": progress(rec),
            "browser_source": {k: src[k] for k in ("kind", "port", "running") if k in src} if isinstance(src, dict) else None,
            "action": rec.get("action")}


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
    """Explicit start / continue: create the journal if needed, probe everything, return the card snapshot.
    P131 追加2: a checklist the owner closed stays closed — only the owner reopens it (menu, or `--open` when they ask)."""
    with _lock(st):
        rec = read(st) or _blank(st)
        rec["resumed_at"] = _now()
        run_checks(st, rec, serve=serve)
        rec["revision"] += 1
        _save(st, rec)
    return {"ok": True, "result": "ready" if remote_ready(rec)[0] else "in_progress", **snapshot(rec)}


def live_sites(st, items) -> None:
    """P131: before probing a website row the owner asked to check, run the real read-only sign-in check for its sites in
    whichever browser Agent J uses (own or adopted): tabs it opens and closes itself. Never logs in. Best effort."""
    from . import browser, browser_sites as bs
    want = [i for i in items if i in SITE_ITEMS]
    if not want or not browser.runtime(st):
        return
    for iid in want:
        with contextlib.suppress(Exception):     # noqa: BLE001 — a failed check is the row's attention, never a crash
            bs.check(st, bs.ensure_item_sites(st, iid), reason="first_run")


def check(st, only: list[str] | None = None, serve: dict | None = None, phone_report: dict | None = None,
          live: bool = False) -> dict:
    """Live read-only probes; refreshes the result metadata (revision unchanged: no owner choice changed). live (one row the
    owner / CLI named): website rows run a real read-only sign-in check first."""
    if only and any(i not in BY_ID for i in only):
        raise SetupError("unknown_item")
    if live and only:
        rec0 = read(st)
        if rec0 is not None:
            live_sites(st, [i for i in only if rec0["items"][i]["choice"] != "unused"])
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
        elif choice in ACTIONS:                    # the owner asked the host to do something; act() does it next
            if (choice in ("install_browser", "adopt_browser") and item != "browser") or \
                    (choice == "open_login" and item not in SITE_ITEMS):
                raise SetupError("invalid")
            row["choice"] = "selected"
            if row["state"] == "deferred":
                row["state"] = "pending"
            rec["action"] = {"item": item, "choice": choice, "state": "running", "at": _now()}
        row["owner_decision_ref"] = decision_ref[:64]
        if choice not in ("later",) + ACTIONS:
            run_checks(st, rec, [item], serve=serve)
        rec["revision"] += 1
        _save(st, rec)
    return {"ok": True, **snapshot(rec)}


def act(st, item: str, choice: str, decision_ref: str = "", serve: dict | None = None) -> dict:
    """Carry out an owner action recorded by mark() (outside the journal lock: an install can take minutes), record the
    outcome in the journal's `action`, re-probe the browser rows and return the new snapshot."""
    from . import browser, browser_sites as bs
    out: dict = {"item": item, "choice": choice, "at": _now()}
    try:
        if choice == "install_browser":
            res = browser.setup(st, own=True)
            out.update(state="done" if res.get("ok") else "failed", result=res.get("result") or res.get("reason"),
                       reason=res.get("reason"))
        elif choice == "adopt_browser":
            res = browser.confirm_adopt(st, decision_ref)
            out.update(state="done" if res.get("ok") else "failed", result=res.get("result"), reason=res.get("reason"),
                       port=res.get("port"))
            if res.get("ok"):
                rec0 = read(st) or {"items": {}}
                live_sites(st, [i for i in SITE_ITEMS if rec0["items"].get(i, {}).get("choice") == "selected"])
        elif choice == "open_login":
            res = bs.open_login(st, item)
            raised = res.get("raised") or {}
            out.update(state="done" if res.get("ok") else "failed", result=res.get("result"), reason=res.get("reason"),
                       raised=bool(raised.get("ok")) if res.get("ok") else None, adopted=res.get("adopted"))
        else:
            raise SetupError("shape")
    except SetupError:
        raise
    except Exception:  # noqa: BLE001 — an action failure is reported on the card, never a crash
        out.update(state="failed", reason="error")
    st.log("setup_action", item=item, choice=choice, state=out.get("state"), reason=out.get("reason"))
    with _lock(st):
        rec = read(st)
        if rec is None:
            raise SetupError("not_found")
        rec["action"] = {k: v for k, v in out.items() if v is not None}
        run_checks(st, rec, [i for i in _order() if BY_ID[i][1] == "browser"], serve=serve)
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


def close(st) -> dict:
    """The banner's ✕ / `--close`: the checklist stops coming back by itself (reconnects, resumes by the Agent's offer) until
    the owner reopens it from the menu or says "continue first-run setup". Nothing else changes; works offline."""
    with _lock(st):
        rec = read(st)
        if rec is None:
            return {"ok": True, "result": "needs_setup", "closed": True}
        if not rec.get("closed"):
            rec["closed"] = True
            rec["revision"] += 1
            _save(st, rec)
    return {"ok": True, "result": "closed", **snapshot(rec)}


def reopen(st) -> dict:
    """The menu's 「首次准备清单」: show it again (and let it come back on reconnect for the next 6 h)."""
    with _lock(st):
        rec = read(st)
        if rec is None:
            return {"ok": True, "result": "needs_setup", "next": "agentj setup checklist --resume --json"}
        if rec.get("closed"):
            rec["closed"] = False
            rec["resumed_at"] = _now()
            rec["revision"] += 1
            _save(st, rec)
    return {"ok": True, "result": "ready" if remote_ready(rec)[0] else "in_progress", **snapshot(rec)}


def doctor_row(st) -> tuple[str, str, str]:
    rec = read(st)
    if rec is None:
        return "ok", "首次准备：未开始（可说「Agent J 主机前一次做完」）/ first-run checklist: not started", ""
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
            return (f"从现在起，你可以离开Agent J 主机，用手机继续处理已选并验证的事项。截至 {at}：{sep.join(ok)} 已就绪。"
                    + (f"未启用：{sep.join(unused)}。" if unused else "")
                    + "断电、重启后解锁、新的系统权限或账号安全检查仍可能需要你回到Agent J 主机。")
        return (f"You can now leave the Agent J host and continue the selected, verified tasks from your phone. As of {at}: "
                f"{sep.join(ok)} ready." + (f" Not enabled: {sep.join(unused)}." if unused else "")
                + " Power loss, a restart that needs unlocking, a new system permission or an account security check may still need you at the Agent J host.")
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
        return check(st, [item] if item else None, serve, live=bool(item))
    if op == "close":
        return close(st)
    if op == "open":
        return reopen(st)
    raise SetupError("shape")


def _text(res: dict, lang: str = "zh") -> str:
    if res.get("result") == "closed":
        return "首次准备清单已收起：不会再自动弹出；主人可在手机菜单「首次准备清单」重开，或说「打开首次准备清单」（agentj setup checklist --open）。/ Closed; reopen from the menu."
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
    op = "resume" if a.resume else "close" if a.close else "open" if a.reopen else "check" if (a.check or a.item) else "status"
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
    p = sub.add_parser("setup", help="首次准备：Agent J 主机前一次做完 / first-run checklist while you are at the Agent J host")
    ss = p.add_subparsers(dest="setup_cmd", required=True)
    c = ss.add_parser("checklist", help="首次准备清单：只读，或 --resume 开始/继续 / the checklist: read-only, or --resume to start")
    g = c.add_mutually_exclusive_group()
    g.add_argument("--resume", action="store_true", help="开始或继续，并把卡片发到手机 / start or continue and show the card")
    g.add_argument("--check", action="store_true", help="实时只读复查 / live read-only re-check")
    g.add_argument("--item", choices=[i[0] for i in ITEMS], help="只复查这一项（网站项会真去查一次登录）/ re-check one item")
    g.add_argument("--open", dest="reopen", action="store_true",
                   help="主人说要重开时：重新显示已收起的清单 / reopen a closed checklist (only when the owner asks)")
    g.add_argument("--close", "--pause", dest="close", action="store_true",
                   help="收起清单：手机/Agent J 主机网页立即收起，不再自动弹出；菜单里可重开 / close it now; reopen from the menu")
    c.add_argument("--json", action="store_true")
    c.set_defaults(fn=lambda a: sys.exit(cmd(a)))
