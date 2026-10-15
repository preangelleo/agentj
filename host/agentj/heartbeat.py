"""P156: the platform heartbeat — Agent J wakes its own main Agent after a quiet spell, on every harness.

Owner (2026-10-12): 「小白用户他也没这个概念，所以我们需要把这个能力预制进去……定时让它醒来……检查一下后台 automation 的
进程，看看董事长的待办事项……Claude Code 有这个能力不代表其他 harness 也有……每个 Agent J 都可以修改自己的 heartbeat
频率……每个 heartbeat 后边还可以跟一个固定的 prompt……如果 prompt 的 hash 更新了就重读」.

How it works (all on this computer, never through the relay — a cloud push cannot wake a computer that does not answer):
- serve's `Heartbeat.loop` looks every TICK s. A beat is due when `heartbeat.interval_minutes` have passed since the last
  turn of any kind ended (phone, Telegram, desktop, scheduled, an earlier beat) and since serve started. It waits while the
  Agent works, a message is queued, an approval / question card is open, a compaction runs, a scheduled run or friend
  session is busy, the emergency stop is on, or inside the nightly upgrade window (03:00–05:00 local, when auto-install is
  on). A phone conversation therefore pushes it back by itself.
- A beat is one ordinary queued message (`HeartbeatSend`, host provenance `system`) through the same queue every adapter
  already takes — own Claude Code / Codex / OpenCode and the three shared sessions. Shared Claude sees the 〔Agent J 系统
  提示〕 envelope (not the owner's words, grants nothing).
- Text: 〔Agent J 心跳〕 + the first 8 hex of sha256(heartbeat.md) + 「没变就不用重读」. The full prompt is attached the first
  time in this serve process (restart), when the hash changed, and after a compaction / clear of that conversation.
- The prompt lives at a fixed, human path: `~/.config/agentj/heartbeat.md` (next to config.json5). Default template on
  first start; the owner or the main Agent maintains it (`agentj heartbeat prompt …`).
- The reply page is marked `via: heartbeat` (folded label on the phone); a reply of exactly 〔不回群〕 stays silent — no
  page, no push, no unread. Each conversation (own / shared, per harness and session id) keeps its own clock.
- P207 `heartbeat.daily_at` ("HH:MM" local, "" = off): besides the idle beat, one beat a day once the clock passes HH:MM
  (it still waits for the same conditions). Rule: due when today's HH:MM has passed, no daily beat is recorded for today
  in heartbeat.json (`daily`: "YYYY-MM-DD", so a restart never fires twice), and serve was already running at HH:MM or
  started at most DAILY_GRACE (60 min) after it — a time missed by more than that is skipped until tomorrow. A late beat
  (Agent busy, night window) still goes the same day; never on the next day. Any timer beat at or after HH:MM counts as
  the day's beat (no two beats back to back). A daily beat resets the idle clock like every turn.
- The fenced own Agent cannot see `~/.config/agentj` or the state directory: `agentj heartbeat …` talks to serve over
  `<state>/agentperm/heartbeat.sock` (like recall.sock); without serve the CLI edits the files directly.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
import re
import secrets
import socket
import stat
import sys
import time
from pathlib import Path

from .compose import Send

MIN_MINUTES, MAX_MINUTES, DEFAULT_MINUTES = 5, 24 * 60, 60
TICK = 20.0                     # s between checks
PROMPT_MAX = 16 * 1024          # bytes of heartbeat.md delivered / accepted
STATE_FILE = "heartbeat.json"
SOCK_NAME = "heartbeat.sock"
ENV = "AGENTJ_HEARTBEAT_SOCK"
REQ_MAX = 64 * 1024
DAILY_GRACE = 60 * 60           # s: a daily HH:MM passed before serve started more than this ago is skipped today (P207)
DAILY_RE = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
NIGHT = (3, 5)                  # local hours of the nightly upgrade window (auto_update)
TAG = {"zh": "〔Agent J 心跳〕", "en": "[Agent J heartbeat]"}
SILENT = "〔不回群〕"

TEMPLATE = {
    "zh": """# 心跳提示（Heartbeat Prompt）

<!-- Agent J 每隔一段时间（默认 60 分钟，`agentj heartbeat set 30m` 修改）在你空闲时把这份提示送给主 Agent。
     主人或主 Agent 都可以改它：`agentj heartbeat prompt edit`。改了以后下一次心跳会附上全文。
     不要在这里写密码、API key 或任何凭据。 -->

醒来以后按顺序做，做完就回到待命：

1. 看一眼后台：正在跑的子 Agent、headless 任务和定时任务（`agentj busy list`、`agentj capability list`），有没有卡住、失败或该交报告还没交的。
2. 读回最近完成的运行报告和 VERDICT，不凭退出码判断成功。
3. 看看董事长的待办和你答应过、还没做完的事（工作根目录的 documentation/NEXT_SESSION.md、交接文件、MEMORY.md）。
4. 中途挂掉的活：能接着干的接着干，需要派给 CEO 的照常派。

只报需要董事长知道或拍板的事，一两句话。一切正常、没有要报的，最终回复只写：〔不回群〕
""",
    "en": """# Heartbeat Prompt

<!-- Agent J hands this note to the main Agent when it has been idle for a while (default 60 minutes; change it with
     `agentj heartbeat set 30m`). The owner or the main Agent may edit it: `agentj heartbeat prompt edit`. After a change
     the next heartbeat carries the full text. Never put passwords, API keys or any credential here. -->

When you wake up, go through this in order, then go back to standby:

1. Check the background: running sub-agents, headless jobs and scheduled tasks (`agentj busy list`, `agentj capability list`) —
   anything stuck, failed, or overdue with its report?
2. Read the newest finished run reports and verdicts; an exit code is not success.
3. Look at the owner's to-dos and anything you promised and have not finished (documentation/NEXT_SESSION.md in the working
   root, handoff files, MEMORY.md).
4. Work that died half-way: pick it up if you can, or dispatch it to its CEO as usual.

Report only what the owner needs to know or decide, in a sentence or two. When everything is fine and there is nothing to
report, make your final reply exactly: 〔不回群〕
""",
}


# ------------------------------------------------------------------ pure helpers (CLI and serve)
def pick(lang, zh, en):
    return en if lang == "en" else zh


def prompt_path() -> Path:
    from .preferences import path as config_path
    return config_path().parent / "heartbeat.md"


def shown(p: Path) -> str:
    s, h = str(p), os.path.expanduser("~").rstrip("/")
    return "~" + s[len(h):] if h and s.startswith(h + "/") else s


def parse_interval(raw) -> int:
    """'30m' · '2h' · '1h30m' · '90' (minutes) · '1d' → minutes within MIN_MINUTES..MAX_MINUTES; ValueError otherwise."""
    s = str(raw or "").strip().lower().replace(" ", "")
    s = s.replace("分钟", "m").replace("分", "m").replace("小时", "h").replace("天", "d")
    if re.fullmatch(r"\d{1,5}", s):
        n = int(s)
    else:
        m = re.fullmatch(r"(?:(\d{1,3})d)?(?:(\d{1,3})h)?(?:(\d{1,5})m(?:in)?)?", s)
        if not s or not m or not any(m.groups()):
            raise ValueError("interval: like 30m, 2h, 1h30m or 1d")
        n = int(m.group(1) or 0) * 1440 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0)
    if not MIN_MINUTES <= n <= MAX_MINUTES:
        raise ValueError(f"interval: {MIN_MINUTES}m … 24h")
    return n


def parse_daily(raw) -> str:
    """P207: '07:30' → '07:30'; '7:30' → '07:30'; 'off' / '' / '关' → ''; ValueError otherwise."""
    s = str(raw if raw is not None else "").strip().lower()
    if s in ("", "off", "none", "关", "关闭"):
        return ""
    m = re.fullmatch(r"(\d{1,2})[:：](\d{2})", s)
    v = f"{int(m.group(1)):02d}:{m.group(2)}" if m else s
    if not DAILY_RE.fullmatch(v):
        raise ValueError("daily: HH:MM (00:00–23:59) or off")
    return v


def fmt_interval(n: int, lang: str = "zh") -> str:
    h, m = divmod(int(n), 60)
    if lang == "en":
        return (f"{h}h" if h else "") + (f"{m}m" if m or not h else "")
    return (f"{h} 小时" if h else "") + (f"{m} 分钟" if m or not h else "")


def daily_target(now: float, hhmm: str) -> float | None:
    """P207: the epoch of today's (local) HH:MM; None when hhmm is not a valid "HH:MM"."""
    if not isinstance(hhmm, str) or not DAILY_RE.fullmatch(hhmm):
        return None
    lt = time.localtime(now)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, int(hhmm[:2]), int(hhmm[3:]), 0, 0, 0, -1))


def day_of(t: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(t))


def daily_due(now: float, hhmm: str, last_day: str | None, started: float, grace: float = DAILY_GRACE) -> bool:
    """P207 pure rule: today's HH:MM has passed, today has no daily beat yet, and serve started before HH:MM + grace."""
    t = daily_target(now, hhmm)
    if t is None or now < t or last_day == day_of(now):
        return False
    return started <= t + grace


def sha8(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:8]


def _read_regular(p: Path, limit: int = PROMPT_MAX) -> str | None:
    """A regular file (never through a link) owned by this user, at most `limit` bytes; None otherwise."""
    try:
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
            return None
        raw = f.read(limit + 1)
    return raw[:limit].decode("utf-8", "replace")


def read_prompt(p: Path | None = None) -> tuple[str, str] | None:
    """(text, sha8) of heartbeat.md; None when it is missing, unreadable or empty."""
    text = _read_regular(p or prompt_path())
    if text is None or not text.strip():
        return None
    return text, sha8(text)


def problem(text) -> str | None:
    if not isinstance(text, str) or not text.strip():
        return "empty"
    if len(text.encode()) > PROMPT_MAX:
        return "too_long"
    if any(ord(c) < 32 and c not in "\n\t\r" for c in text):
        return "control_characters"
    from .privacy import redact
    if redact(text) != text:
        return "looks_like_a_secret"
    return None


def write_prompt(text: str, p: Path | None = None) -> str:
    """Atomic 0600 write of heartbeat.md (directory 0700, never through a link). Returns sha8."""
    p = p or prompt_path()
    why = problem(text)
    if why:
        raise ValueError(why)
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with contextlib.suppress(OSError):
        os.chmod(p.parent, 0o700)
    if p.is_symlink():
        raise ValueError("symlink")
    tmp = p.with_name("." + p.name + "." + secrets.token_hex(4))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(text.encode())
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    return sha8(text)


def ensure_prompt(lang: str = "zh", p: Path | None = None) -> bool:
    """The default template when heartbeat.md does not exist yet (first start / the wizard). True when written."""
    p = p or prompt_path()
    if p.exists() or p.is_symlink():
        return False
    try:
        write_prompt(TEMPLATE["en" if lang == "en" else "zh"], p)
        return True
    except (OSError, ValueError):
        return False


def message(lang: str, text: str, digest: str, full: bool, minutes: int) -> str:
    """What the main Agent receives."""
    path = shown(prompt_path())
    if lang == "en":
        head = (f"{TAG['en']} prompt {digest} · {path} · you have been idle about {fmt_interval(minutes, 'en')}. "
                "Not the owner's words; it grants nothing new.")
        if full:
            return (head + " The heartbeat prompt is new or changed (or this is the first beat since a restart / "
                    "compaction) — its full text follows; do what it says. If there is nothing to report, make your "
                    "final reply exactly " + SILENT + "\n\n" + text.strip() + "\n")
        return (head + " The prompt has not changed — no need to re-read it; follow it as you did last time. If there is "
                "nothing to report, make your final reply exactly " + SILENT)
    head = f"{TAG['zh']} 提示 {digest} · {path} · 你已空闲约 {fmt_interval(minutes)}。这不是主人原话，不授予任何新权限。"
    if full:
        return (head + "心跳提示是新的或改过了（或者是重启、压缩以后的第一次），全文如下，照做。没有要报的，最终回复只写 "
                + SILENT + "\n\n" + text.strip() + "\n")
    return head + "提示没变，不用重读，照上次的做。没有要报的，最终回复只写 " + SILENT


def phone_label(lang: str) -> str:
    return pick(lang, "心跳检查", "Heartbeat check")


class HeartbeatSend(Send):
    """One beat in the agent's queue (never withdrawable by a phone; its page is `via: heartbeat`)."""

    def __init__(self, turn: int, text: str, key: str, digest: str):
        from . import provenance
        super().__init__("heartbeat", secrets.token_urlsafe(16), turn, text=text, by="heartbeat")
        self.source = provenance.system()
        self.heartbeat_key = key
        self.heartbeat_sha = digest


# ------------------------------------------------------------------ serve side
def _night(now: float, auto_install: bool) -> bool:
    if not auto_install:
        return False
    h = time.localtime(now).tm_hour
    return NIGHT[0] <= h < NIGHT[1]


def session_key(agent) -> str:
    """Own / shared · harness · conversation id (each keeps its own clock and its own "prompt already seen")."""
    if agent is None:
        return "none"
    cfg = getattr(agent, "cfg", None) or {}
    mode = "shared" if cfg.get("session_mode") == "shared" or type(agent).__name__.startswith(("Shared", "Owner")) else "own"
    sid = None
    sess = getattr(agent, "session", None)
    if isinstance(sess, dict):
        sid = sess.get("sessionId")
    for attr in ("thread_id", "thread", "sid"):
        if sid is None and isinstance(getattr(agent, attr, None), str):
            sid = getattr(agent, attr)
    if sid is None and mode == "own":
        with contextlib.suppress(Exception):
            sid = agent.host.st.agent_session(agent.kind)
    sid = re.sub(r"[^A-Za-z0-9_.-]", "_", str(sid or "-"))[:64]
    return f"{mode}-{getattr(agent, 'kind', '?')}-{sid}"


class Heartbeat:
    """Owned by serve's Host (self.heartbeat)."""

    def __init__(self, host):
        self.host = host
        self.started = time.time()
        self.last_activity = self.started
        self.seen: dict[str, str] = {}     # key → sha8 delivered in full in this serve process
        self.inflight: HeartbeatSend | None = None
        self.last_wait: str | None = None
        self.server = None

    # ---- settings
    def prefs(self) -> tuple[bool, int]:
        from . import preferences
        p = getattr(self.host, "preferences", None) or {}
        on = preferences.get(p, "heartbeat.enabled", True) is not False
        n = preferences.get(p, "heartbeat.interval_minutes", DEFAULT_MINUTES)
        n = n if type(n) is int and MIN_MINUTES <= n <= MAX_MINUTES else DEFAULT_MINUTES
        return on, n

    def daily_at(self) -> str:
        """P207: "HH:MM" or "" (off; also for anything malformed)."""
        from . import preferences
        v = preferences.get(getattr(self.host, "preferences", None) or {}, "heartbeat.daily_at", "")
        return v if isinstance(v, str) and DAILY_RE.fullmatch(v) else ""

    def daily_is_due(self, now: float) -> bool:
        last = self._load().get("daily")
        return daily_due(now, self.daily_at(), last if isinstance(last, str) else None, self.started)

    def lang(self) -> str:
        return "en" if getattr(self.host, "lang", "zh") == "en" else "zh"

    # ---- state file
    def _path(self) -> Path:
        return self.host.st.root / STATE_FILE

    def _load(self) -> dict:
        try:
            d = json.loads(_read_regular(self._path(), 256 * 1024) or "{}")
            return d if isinstance(d, dict) and isinstance(d.get("beats", {}), dict) else {}
        except ValueError:
            return {}

    def _save(self, d: dict) -> None:
        from .state import _write_private
        beats = d.get("beats", {})
        if len(beats) > 32:                            # old conversations fall out, newest kept
            d["beats"] = dict(sorted(beats.items(), key=lambda kv: (kv[1] or {}).get("at", 0))[-32:])
        with contextlib.suppress(OSError):
            _write_private(self._path(), json.dumps(d, ensure_ascii=False).encode())

    # ---- activity (every turn of any kind, phone input, desktop input)
    def touch(self) -> None:
        self.last_activity = time.time()

    def forget(self, why: str = "") -> None:
        """The conversation lost its context (compaction / clear / new): the next beat carries the full prompt again."""
        key = session_key(getattr(self.host, "agent", None))
        if self.seen.pop(key, None) is not None:
            self.host.st.log("heartbeat_forget", why=why[:20])

    # ---- the decision
    def wait_reason(self, now: float | None = None) -> str | None:
        """None = a beat may go now (apart from the interval)."""
        h = self.host
        a = getattr(h, "agent", None)
        if a is None:
            return "no_agent"
        if type(a).__name__ == "UnavailableSharedAgent":
            return "no_agent"
        if h.stopped():
            return "stopped"
        with contextlib.suppress(Exception):
            if a.is_down():
                return "agent_down"
        if self.inflight is not None and self.inflight.state in ("pending", "delivering"):
            return "pending"
        if getattr(a, "halting", False):
            return "busy"
        if getattr(a, "status", "idle") not in ("idle",) or (getattr(a, "q", None) is not None and not a.q.empty()):
            return "busy"
        if getattr(a, "cur_send", None) is not None or getattr(a, "cur_cmd", None) is not None:
            return "busy"
        if getattr(a, "control_name", None) == "compact":
            return "compacting"
        nb = getattr(a, "native_busy", None)
        with contextlib.suppress(Exception):
            if callable(nb) and nb():
                return "busy"
        if any(not x.fut.done() for x in getattr(h, "asks", {}).values()) or h._open_question():
            return "approval"
        if any(getattr(s, "state", "") in ("pending", "delivering") for s in getattr(getattr(h, "sends", None), "items", {}).values()):
            return "phone"
        sched = getattr(h, "scheduler", None)
        if sched is not None and getattr(sched, "current_id", None):
            return "busy"
        if getattr(getattr(h, "elevate", None), "cards", None):
            return "approval"
        qr = getattr(h, "quota_resume", None)
        if qr is not None and qr.pending("main"):     # P314: out of quota until the reset; the continue comes then
            return "quota_wait"
        from . import preferences
        auto = preferences.get(getattr(h, "preferences", None) or {}, "updates.auto_install", True) is not False
        if _night(now or time.time(), auto):
            return "night_update"
        with contextlib.suppress(Exception):
            if not h.st.devices():
                return "no_phone"
        return None

    def next_due(self, key: str | None = None) -> float:
        _, n = self.prefs()
        key = key or session_key(getattr(self.host, "agent", None))
        last = (self._load().get("beats", {}).get(key) or {}).get("at", 0)
        last = last if isinstance(last, (int, float)) else 0
        return max(self.started, self.last_activity, last) + n * 60

    async def loop(self) -> None:
        while True:
            await asyncio.sleep(TICK)
            with contextlib.suppress(Exception):
                await self.tick()

    async def tick(self, now: float | None = None) -> dict | None:
        on, _ = self.prefs()
        if not on:
            return None
        now = time.time() if now is None else now
        daily = self.daily_is_due(now)                 # P207: the once-a-day beat at heartbeat.daily_at
        if now < self.next_due() and not daily:
            return None
        why = self.wait_reason(now)
        if why:
            if why != self.last_wait:
                self.last_wait = why
                self.host.st.log("heartbeat_wait", reason=why)
            return {"ok": False, "why": why}
        return self.fire(now, by="daily" if daily and now < self.next_due() else "timer")

    def fire(self, now: float | None = None, by: str = "timer") -> dict:
        """Queue one beat now (the caller checked wait_reason, or this is `agentj heartbeat now`)."""
        h = self.host
        now = time.time() if now is None else now
        a = getattr(h, "agent", None)
        if a is None:
            return {"ok": False, "why": "no_agent"}
        lang = self.lang()
        ensure_prompt(lang)
        got = read_prompt()
        text, digest = got if got else (TEMPLATE[lang], sha8(TEMPLATE[lang]))
        key = session_key(a)
        _, n = self.prefs()
        full = self.seen.get(key) != digest
        idle_min = max(1, int((now - max(self.started, self.last_activity)) // 60))
        body = message(lang, text, digest, full, idle_min)
        with contextlib.suppress(Exception):          # P165 (P158 ask): a goal still running → one line at the end
            from . import goal as goal_mod
            g = goal_mod.heartbeat_line(h.st, lang)
            if g:
                body += "\n" + g
        with contextlib.suppress(Exception):          # P254: the quota steward's not-yet-given reminders of the day
            from . import quota as quota_mod
            q = quota_mod.heartbeat_lines(h.st.root, getattr(h, "preferences", None), lang,
                                          meter_state=getattr(h, "meter_state", None))
            if q:
                body += "\n" + q
        with contextlib.suppress(Exception):          # P288: the Claude login inside its last 7 days → one line
            from . import auth_rescue
            g = auth_rescue.heartbeat_line(h.st, lang)
            if g:
                body += "\n" + g
        page = h.hist_add({"k": "agent", "via": "heartbeat", "text": ""}, "", "open")
        send = HeartbeatSend(page["id"], body, key, digest)
        self.inflight = send
        self.seen[key] = digest
        d = self._load()
        d.setdefault("beats", {})[key] = {"at": now, "sha": digest}
        t = daily_target(now, self.daily_at())
        if by in ("timer", "daily") and t is not None and now >= t:
            d["daily"] = day_of(now)                   # P207: today's daily beat is done (survives a restart)
        d["last"] = {"at": now, "key": key, "sha": digest, "full": full, "by": by}
        self._save(d)
        self.last_wait = None
        self.last_activity = max(self.last_activity, now)   # P207: a beat resets the idle clock like any turn
        h.st.log("heartbeat_sent", key=key[:80], sha=digest, full=full, by=by)
        a.submit(send)
        return {"ok": True, "key": key, "sha": digest, "full": full, "page": page["id"]}

    # ---- views
    def status(self) -> dict:
        on, n = self.prefs()
        key = session_key(getattr(self.host, "agent", None))
        d = self._load()
        got = read_prompt()
        return {"ok": True, "enabled": on, "interval_minutes": n, "prompt": shown(prompt_path()),
                "sha": got[1] if got else None, "key": key, "last": d.get("last"),
                "next_due": round(self.next_due(key)) if on else None, "waiting": self.wait_reason() if on else None,
                "seen_full": self.seen.get(key), "daily_at": self.daily_at(),
                "daily_last": d.get("daily") if isinstance(d.get("daily"), str) else None}

    # ---- heartbeat.sock (the fenced Agent's way in)
    @property
    def sock(self) -> Path:
        return self.host.st.perm_dir / SOCK_NAME

    async def start(self) -> None:
        st = self.host.st
        st.perm_dir.mkdir(mode=0o700, exist_ok=True)
        with contextlib.suppress(FileNotFoundError):
            self.sock.unlink()
        old = os.umask(0o077)
        try:
            self.server = await asyncio.start_unix_server(self.on_client, path=str(self.sock), limit=REQ_MAX)
        finally:
            os.umask(old)
        os.chmod(self.sock, 0o600)
        os.environ[ENV] = str(self.sock)

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            with contextlib.suppress(FileNotFoundError):
                self.sock.unlink()

    async def on_client(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(r.readline(), 10)
            req = json.loads(line or b"{}")
            res = await self.handle(req if isinstance(req, dict) else {})
        except (asyncio.TimeoutError, ValueError, asyncio.LimitOverrunError):
            res = {"ok": False, "why": "bad_request"}
        except Exception as e:  # noqa: BLE001 — one bad request never costs serve
            res = {"ok": False, "why": type(e).__name__}
        with contextlib.suppress(Exception):
            w.write((json.dumps(res, ensure_ascii=False) + "\n").encode())
            await w.drain()
            w.close()

    async def handle(self, req: dict) -> dict:
        from . import provenance
        op = req.get("op")
        if op == "status":
            return self.status()
        if op == "prompt_show":
            got = read_prompt()
            return {"ok": True, "path": shown(prompt_path()), "text": got[0] if got else "", "sha": got[1] if got else None}
        from . import quota as quota_mod
        if op in quota_mod.READ_OPS:                   # P254: agentj quota (the fenced Agent cannot read the state directory)
            return await quota_mod.handle(self.host, req)
        # changes: never while a message that is not the owner's is being handled (a group member cannot rewrite them)
        if provenance.untrusted_turn(getattr(self.host, "agent", None)):
            self.host.st.log("heartbeat_refused", op=str(op)[:16], reason="untrusted_turn")
            return {"ok": False, "why": "untrusted_turn"}
        if op in quota_mod.WRITE_OPS:                  # P254: reminders on/off, recorded resets, today's reminders marked given
            return await quota_mod.handle(self.host, req)
        if op == "now":
            why = self.wait_reason()
            if why in ("no_agent", "stopped", "agent_down", "pending"):
                return {"ok": False, "why": why}
            if why and not req.get("queue"):
                return {"ok": False, "why": why, "hint": "busy: it queues after the current turn with --queue"}
            return self.fire(by="command")
        if op in ("on", "off"):
            res = await self.host.set_pref("heartbeat.enabled", op == "on")
            return {"ok": bool(res.get("ok")), **({} if res.get("ok") else {"why": res.get("error", "rejected")})}
        if op == "set":
            try:
                n = parse_interval(req.get("interval"))
            except ValueError as e:
                return {"ok": False, "why": str(e)}
            res = await self.host.set_pref("heartbeat.interval_minutes", n)
            return {"ok": bool(res.get("ok")), "interval_minutes": n, **({} if res.get("ok") else {"why": res.get("error", "rejected")})}
        if op == "daily":                              # P207: "HH:MM" or "" / "off"
            try:
                v = parse_daily(req.get("at"))
            except ValueError as e:
                return {"ok": False, "why": str(e)}
            res = await self.host.set_pref("heartbeat.daily_at", v)
            return {"ok": bool(res.get("ok")), "daily_at": v, **({} if res.get("ok") else {"why": res.get("error", "rejected")})}
        if op == "prompt_set":
            text = req.get("text")
            if req.get("reset"):
                text = TEMPLATE[self.lang()]
            try:
                digest = await asyncio.to_thread(write_prompt, text)
            except (ValueError, OSError) as e:
                return {"ok": False, "why": str(e) if isinstance(e, ValueError) else type(e).__name__}
            self.host.st.log("heartbeat_prompt", sha=digest)
            return {"ok": True, "sha": digest, "path": shown(prompt_path())}
        return {"ok": False, "why": "unknown_op"}


# ------------------------------------------------------------------ CLI (the owner, or the main Agent fenced or not)
def sock_paths() -> list[str]:
    out = []
    if os.environ.get(ENV):
        out.append(os.environ[ENV])
    with contextlib.suppress(Exception):
        from .state import State
        out.append(str(State().perm_dir / SOCK_NAME))
    return list(dict.fromkeys(out))


def ask(req: dict, timeout: float = 15) -> dict | None:
    """serve's answer, or None when no serve listens."""
    for path in sock_paths():
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            try:
                s.connect(path)
                s.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode())
                buf = b""
                while b"\n" not in buf and len(buf) < 4 * REQ_MAX:
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                return json.loads(buf.split(b"\n", 1)[0])
            except (OSError, ValueError):
                continue
    return None


def _offline(req: dict) -> dict:
    """No serve: read / change the files directly (works unfenced; inside the fence these files are not visible)."""
    from . import preferences
    op = req["op"]
    if op in ("status", "prompt_show"):
        try:
            p = preferences.effective()
        except Exception:  # noqa: BLE001
            p = {}
        got = read_prompt()
        if op == "prompt_show":
            return {"ok": True, "path": shown(prompt_path()), "text": got[0] if got else "", "sha": got[1] if got else None,
                    "serve": False}
        return {"ok": True, "enabled": preferences.get(p, "heartbeat.enabled", True) is not False,
                "interval_minutes": preferences.get(p, "heartbeat.interval_minutes", DEFAULT_MINUTES),
                "daily_at": preferences.get(p, "heartbeat.daily_at", "") or "",
                "prompt": shown(prompt_path()), "sha": got[1] if got else None, "serve": False}
    if op == "now":
        return {"ok": False, "why": "serve_not_running"}
    if op in ("on", "off", "set", "daily"):
        if op == "daily":
            try:
                v = parse_daily(req.get("at"))
            except ValueError as e:
                return {"ok": False, "why": str(e)}
            args = ["set", "heartbeat.daily_at", v]
        elif op == "set":
            try:
                n = parse_interval(req.get("interval"))
            except ValueError as e:
                return {"ok": False, "why": str(e)}
            args = ["set", "heartbeat.interval_minutes", str(n)]
        else:
            args = ["set", "heartbeat.enabled", "true" if op == "on" else "false"]
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = preferences.command(args)
        try:
            res = json.loads(out.getvalue().strip().splitlines()[-1])
        except (ValueError, IndexError):
            res = {"ok": code == 0}
        return {"ok": bool(res.get("ok")), "serve": False, **({} if res.get("ok") else {"why": res.get("error", "rejected")}),
                **({"interval_minutes": n} if op == "set" else {}), **({"daily_at": v} if op == "daily" else {})}
    if op == "prompt_set":
        text = TEMPLATE["zh"] if req.get("reset") else req.get("text")
        try:
            return {"ok": True, "sha": write_prompt(text), "path": shown(prompt_path()), "serve": False}
        except (ValueError, OSError) as e:
            return {"ok": False, "why": str(e) if isinstance(e, ValueError) else type(e).__name__}
    return {"ok": False, "why": "unknown_op"}


def call(req: dict) -> dict:
    res = ask(req)
    return res if res is not None else _offline(req)


def add_parser(sub) -> None:
    p = sub.add_parser("heartbeat", help="心跳：Agent 空闲一段时间后自动醒来检查后台和待办 · status · on · off · set <30m> · daily <07:30|off> · "
                                         "prompt show|edit|set|reset · now / the main Agent's heartbeat")
    ps = p.add_subparsers(dest="hb_cmd")
    ps.add_parser("status", help="开关、间隔、提示文件、上次心跳 / switch, interval, prompt, last beat").add_argument("--json", action="store_true")
    ps.add_parser("on", help="打开（默认开）/ turn on (default)")
    ps.add_parser("off", help="关闭 / turn off")
    x = ps.add_parser("set", help="间隔，5m–24h，例如 30m、2h / interval, 5m–24h, e.g. 30m, 2h")
    x.add_argument("interval")
    x = ps.add_parser("daily", help="每天固定时间唤醒，本地时间 HH:MM；off 关闭 / a daily wake-up at HH:MM local, off to stop")
    x.add_argument("at")
    x = ps.add_parser("now", help="马上心跳一次（测试用）/ one beat now")
    x.add_argument("--queue", action="store_true", help="Agent 正忙时排在当前回合后面 / queue behind a busy turn")
    pr = ps.add_parser("prompt", help="心跳提示 ~/.config/agentj/heartbeat.md：show · edit · set · reset / the heartbeat prompt")
    prs = pr.add_subparsers(dest="hb_prompt", required=True)
    prs.add_parser("show", help="显示全文 / print it")
    prs.add_parser("edit", help="在终端里用编辑器改；不在终端时从标准输入读新全文 / $EDITOR at a terminal, else stdin")
    x = prs.add_parser("set", help="用文件或标准输入整篇替换 / replace from a file or stdin")
    x.add_argument("--file", help="新提示文件 / the new prompt (default: stdin)")
    prs.add_parser("reset", help="恢复默认模板 / back to the default template")
    p.set_defaults(fn=cmd_heartbeat)


def _say(res: dict, ok_zh: str, ok_en: str) -> None:
    if res.get("ok"):
        print(ok_zh + " / " + ok_en)
        return
    sys.exit("没成功 / failed: " + str(res.get("why") or "?") + ((" — " + res["hint"]) if res.get("hint") else ""))


def _edit_text(current: str) -> str | None:
    import subprocess
    import tempfile
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "nano"
    with tempfile.TemporaryDirectory(prefix="agentj-hb-") as d:
        f = Path(d) / "heartbeat.md"
        f.write_text(current)
        os.chmod(f, 0o600)
        import shlex
        if subprocess.call(shlex.split(editor) + [str(f)]) != 0:
            return None
        return f.read_text()


def cmd_heartbeat(a) -> None:
    c = a.hb_cmd or "status"
    if c == "status":
        res = call({"op": "status"})
        if getattr(a, "json", False):
            print(json.dumps(res, ensure_ascii=False))
            return
        if not res.get("ok"):
            sys.exit("读不到心跳状态 / heartbeat status unavailable: " + str(res.get("why")))
        n = res.get("interval_minutes", DEFAULT_MINUTES)
        print(("开" if res.get("enabled") else "关") + f" · 每 {fmt_interval(n)}（空闲满这么久才醒）· 提示 {res.get('prompt')}"
              + (f"（{res['sha']}）" if res.get("sha") else "（还没有，第一次心跳时写入默认模板）"))
        last = res.get("last") or {}
        if isinstance(last.get("at"), (int, float)):
            print("上次心跳 / last beat: " + time.strftime("%Y-%m-%d %H:%M", time.localtime(last["at"]))
                  + (" · 附全文" if last.get("full") else ""))
        if res.get("daily_at"):
            print(f"每天 {res['daily_at']} 定时唤醒一次 / daily at {res['daily_at']}"
                  + (" · 今天已醒过 / done today" if res.get("daily_last") == day_of(time.time()) else ""))
        if res.get("next_due"):
            print("最早下次 / next at the earliest: " + time.strftime("%Y-%m-%d %H:%M", time.localtime(res["next_due"]))
                  + (f" · 现在要等：{res['waiting']}" if res.get("waiting") else ""))
        if res.get("serve") is False:
            print("（Agent J 没在运行：心跳只在 serve 运行时发生）/ (serve is not running)")
        print("每次心跳会消耗一次模型调用 / every heartbeat costs one model call")
        return
    if c in ("on", "off"):
        _say(call({"op": c}), "心跳已" + ("打开" if c == "on" else "关闭"), "heartbeat " + c)
        return
    if c == "set":
        res = call({"op": "set", "interval": a.interval})
        n = res.get("interval_minutes")
        _say(res, f"记下了：空闲满 {fmt_interval(n or 0)} 心跳一次", f"heartbeat every {fmt_interval(n or 0, 'en')} of idle time")
        return
    if c == "daily":
        res = call({"op": "daily", "at": a.at})
        v = res.get("daily_at")
        _say(res, f"记下了：每天 {v} 定时唤醒" if v else "已关闭每天定时唤醒",
             f"daily wake-up at {v}" if v else "daily wake-up off")
        return
    if c == "now":
        res = call({"op": "now", "queue": a.queue})
        _say(res, "已发出一次心跳（" + ("附全文" if res.get("full") else "提示没变") + "）", "heartbeat sent")
        return
    if c == "prompt":
        pc = a.hb_prompt
        if pc == "show":
            res = call({"op": "prompt_show"})
            if not res.get("ok"):
                sys.exit("读不到 / unavailable: " + str(res.get("why")))
            print(f"# {res.get('path')} · {res.get('sha') or '—'}")
            print(res.get("text") or "（还没有：第一次心跳时写入默认模板，或运行 agentj heartbeat prompt reset）")
            return
        if pc == "reset":
            res = call({"op": "prompt_set", "reset": True})
            _say(res, f"已恢复默认模板（{res.get('sha')}）", "default template restored")
            return
        if pc == "edit" and sys.stdin.isatty() and sys.stdout.isatty():
            cur = call({"op": "prompt_show"})
            text = _edit_text(cur.get("text") or TEMPLATE["zh"])
            if text is None:
                sys.exit("编辑器没有正常退出，没改 / editor failed, nothing changed")
        elif pc == "set" and a.file:
            try:
                text = Path(a.file).expanduser().read_text()
            except (OSError, UnicodeDecodeError) as e:
                sys.exit("读不到文件 / cannot read the file: " + type(e).__name__)
        else:
            text = sys.stdin.read(PROMPT_MAX + 1)
        res = call({"op": "prompt_set", "text": text})
        _say(res, f"记下了：心跳提示已更新（{res.get('sha')}），下一次心跳附全文", "heartbeat prompt updated; the next beat carries it")
        return
