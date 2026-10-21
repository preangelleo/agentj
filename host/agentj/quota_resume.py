"""P314: a turn stopped by the AI account's usage limit continues by itself once the limit resets.

Owner (2026-10-15, from the cloudroom study, item 2): 「轮次因额度失败时打标记；到 resetsAt（或「额度告警时交给其他 AI
账号」开关切到别的账号后）自动发一句「继续」续跑，只续一次、可在手机上取消；手机显示「额度用完，预计 HH:MM 自动继续」；
共享会话和后台任务都覆盖；避免重复续跑与审批卡复用。」

What counts: only a used-up subscription window (Claude Code 「usage limit reached」 / 「hit your limit」, Codex `usage`).
A momentary 429 (`rate`), an empty balance (`quota`) or a login problem never resume by time — waiting would not fix them.
P433 (#196): `judge` is the one verdict for every entry. The limit words must open the line (Claude Code's own wording, see
_CLAUDE_LIMIT); a per-model cap (Opus / Sonnet / Fable / fast mode) is named as such with the model to switch to. A confirmed
limit always wins over the readings (#196 correction: the owner's Claude page said 100 % used while the phone said 39 %): readings
that contradict it are marked 「可能过期」 on the phone (mark_stale) and the notice says so; for Claude the notice also points at a
free reset (Claude settings → Usage → 「Reset for free」, or one the owner recorded) — never pressed by Agent J.

When: the harness's own reset time — Claude Code's `|<epoch>` / rejected rate_limit_event `resetsAt`, a 「resets 3pm」 /
「try again at …」 sentence, else the meters' exhausted window (≥ 95 %). No time known → nothing is scheduled (the notice says
so). Scopes: "main" (the chat, own or shared session) and "task:<id>" (a workflow / dispatched run). One record per scope;
a new hit replaces it. It fires once, RESUME_MARGIN after the reset: the record is removed (written) before the turn is
queued, so a restart never sends it twice. It is dropped, never fired, when the owner already spoke (a phone `say`, a new run
of that task), the Agent is busy, or the stop switch is on. A resumed turn that hits the limit again schedules again only for
a strictly later reset (never a loop on a stale reading).

Another account: a task whose harness may move (task.json does not pin it) and `quota.auto_switch` on → rerun at once on a
harness whose readings show room and which answers a handshake (quota.pick_alt). The chat stays on its harness (switching it
is /switch-harness); when serve comes back on another harness, a waiting chat record fires at once.

The continue is a fresh turn with a fresh page (via "quota"), system provenance, no attachments: it never answers or reuses an
approval card (batch grants already end with the failed turn, ADR-A48). Cancel = `qr_cancel {r, id}` from a paired phone
(it only stops an automatic action, like grant_off) or `agentj quota resume cancel`.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
import secrets
import time
from datetime import datetime, timedelta
from pathlib import Path

FILE = "quota_resume.json"
RESUME_MARGIN = 90           # seconds after the reported reset (server clocks, rounding)
MAX_WAIT = 8 * 86400         # a reset further away than this is not believed
MIN_LEAD = 30                # a reset this close (or past) is a stale reading: unknown
EXHAUSTED = 95.0
SWITCH_DELAY = 20            # the rerun on another account starts after this many seconds (time to cancel)
NAMES = {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode", "kimi": "Kimi Code", "qwen": "Qwen Code", "dsh": "DeepSeek Harness"}

# P433 (#196): the words Claude Code 2.1.296 itself writes when a subscription window refuses a turn — 「You've hit your
# {session|weekly|Opus|Sonnet|Fable|usage|fast} limit · resets 8pm (Asia/Tokyo)」 (its limit names per rateLimitType), older
# 「Claude AI usage limit reached|<epoch>」, 「5-hour limit reached ∙ resets 3pm」, 「Opus weekly limit reached ∙ …」,
# 「Fable limit reached · continuing on …」. They open a line; the model talking about limits mid-sentence is not the limit.
# Never a limit: 「Context limit reached · /compact …」, 「Server is temporarily limiting requests (not your usage limit)」,
# a 429 / 529 / overloaded / high-load line, a monthly spend or budget cap (time does not fix those the same way).
_CLAUDE_LIMIT = re.compile(r"^[^\w\n]{0,4}(?:api error:?\s*)?(?:claude(?: ai)?\s+|you(?:'|’)?ve\s+|you have\s+)?(?:"
                           r"hit your (?:(?P<a>session|weekly|usage|opus|sonnet|fable|fast) )?limit\b|usage limit reached|"
                           r"(?:(?P<b>opus|sonnet|fable) (?:(?:5-hour|weekly|session) )?|(?P<c>5-hour|weekly|session) )"
                           r"limit reached|your limit will reset)", re.I | re.M)
_NOT_LIMIT = re.compile(r"not your usage limit|context limit|spend limit|budget|overloaded|high load|rate_limit_error|"
                        r"\b(?:429|529)\b", re.I)
CLEAR = 80.0                 # a window below this contradicts a confirmed limit: the reading is stale (mark_stale)
# rateLimitType of a rejected rate_limit_event → our limit names; the per-model ones are not the 5H / WEEKLY meters
_RATE_LIMIT = {"five_hour": "session", "seven_day": "weekly", "seven_day_opus": "opus", "seven_day_sonnet": "sonnet",
               "seven_day_overage_included": "fable", "overage": "usage"}
MODEL_LIMITS = {"opus": "Opus", "sonnet": "Sonnet", "fable": "Fable", "fast": "fast"}
SWITCH_TO = {"opus": "sonnet", "sonnet": "opus", "fable": "opus"}        # Claude Code's own advice (/model …)
_MONTHS = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}
_IN = re.compile(r"\bin\s+((?:\d+\s*(?:days?|d|hours?|hrs?|h|minutes?|mins?|m)\b[\s,and]*)+)", re.I)
_AT = re.compile(r"(?:resets?|reset at|try again at|again at|available at)\s*(?:at\s+)?(?:on\s+)?"
                 r"(?:(?P<mon>[A-Za-z]{3})[a-z]*\.?\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?,?\s*(?:(?P<year>\d{4}),?\s*)?)?"
                 r"(?:at\s+)?(?P<h>\d{1,2})(?::(?P<mi>\d{2}))?\s*(?P<ap>[ap]\.?m\.?)?(?:\s*\((?P<tz>[A-Za-z_]+/[A-Za-z_/+-]+|UTC)\))?",
                 re.I)


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v


def _unix(v) -> float | None:
    if not _num(v) or v <= 0:
        return None
    return float(v) / 1000 if v > 10_000_000_000 else float(v)


# ------------------------------------------------------------------ classification and reset time (pure)
def claude_limit(text) -> bool:
    """A Claude Code turn ended on a used-up subscription window (not a momentary 429, not a login problem)."""
    return limit_of(text) is not None


def limit_of(text, rate: dict | None = None) -> str | None:
    """P433: which Claude Code limit refused the turn — session · weekly · usage · opus · sonnet · fable · fast, "" when the
    words do not name one — or None when the line is not a usage limit at all. A rejected rate event of this turn names it
    exactly (Pro / Enterprise word a Sonnet cap as 「weekly limit」)."""
    if not isinstance(text, str) or not text or _NOT_LIMIT.search(text[:200]):
        return None
    m = _CLAUDE_LIMIT.search(text)
    if not m:
        return None
    name = (m.group("a") or m.group("b") or m.group("c") or "").lower().replace("5-hour", "session")
    if isinstance(rate, dict) and rate.get("status") == "rejected" and rate.get("rateLimitType") in _RATE_LIMIT:
        name = _RATE_LIMIT[rate["rateLimitType"]]
    return name


def readings(harness: str, meters: dict | None = None, state_root=None, now: float | None = None) -> dict:
    """P433: the 5H / WEEKLY percentages the phone shows for this harness, only windows still current (reset in the future):
    the live meters first, else the readings on disk. {"h5": pct, "week": pct} — a missing key is unknown."""
    now = time.time() if now is None else now
    out = {}
    for k in ("h5", "week"):
        w = (meters or {}).get(k)
        if isinstance(w, dict):
            pct, at = w.get("pct", w.get("used_pct")), _unix(w.get("reset", w.get("reset_at")))
            if _num(pct) and at and at > now:
                out[k] = float(pct)
    if out or state_root is None:
        return out
    with contextlib.suppress(Exception):
        from . import quota
        a = next((x for x in quota.report(state_root, now)["harnesses"] if x["harness"] == harness), None) or {}
        h5 = a.get("h5")
        if isinstance(h5, dict) and _num(h5.get("used_pct")) and (_unix(h5.get("reset_at")) or 0) > now:
            out["h5"] = float(h5["used_pct"])
        if a.get("status") == "ok" and _num(a.get("used_pct")) and (_unix(a.get("reset_at")) or 0) > now:
            out["week"] = float(a["used_pct"])
    return out


def judge(harness: str, text=None, rate: dict | None = None, meters: dict | None = None, state_root=None,
          now: float | None = None, limit: str | None = None) -> dict | None:
    """P433: the one verdict every entry uses (chat, shared session, workflow run). None = not a usage limit.
    {"harness", "limit", "at"} = a used-up limit and the reset the harness reported; with "model" when only one model is
    capped (Opus / Sonnet / Fable / fast mode), with "stale": readings when the phone's 5H / WEEKLY windows clearly say
    otherwise — the harness's confirmed limit wins, those readings are out of date (#196: 39 % shown, 100 % used)."""
    now = time.time() if now is None else now
    if harness == "claude":
        limit = limit_of(text, rate)
        if limit is None:
            return None
    limit = limit or ""
    out = {"harness": harness, "limit": limit}
    if limit in MODEL_LIMITS:
        out["model"] = limit
    else:
        r = readings(harness, meters, state_root, now)
        keys = {"session": ("h5",), "weekly": ("week",)}.get(limit, ("h5", "week"))
        seen = [r[k] for k in keys if k in r]
        if seen and all(p < CLEAR for p in seen):
            out["stale"] = {k: r[k] for k in keys if k in r}
    out["at"] = reset_time(harness, text, rate, None if "model" in out else meters,
                           None if "model" in out else state_root, now)
    return out


def parse_when(text, now: float | None = None) -> float | None:
    """The reset moment a harness wrote in words: `…|1760000000`, `in 2 hours 5 minutes`, `resets 3pm (Asia/Tokyo)`,
    `try again at Oct 20th, 2026 3:05 PM`. Local time unless a zone is named; a bare time already past is tomorrow."""
    if not isinstance(text, str) or not text:
        return None
    now = time.time() if now is None else now
    m = re.search(r"\|\s*(\d{10,13})\b", text)
    if m:
        return _unix(int(m.group(1)))
    m = _IN.search(text)
    if m:
        secs = 0
        for n, u in re.findall(r"(\d+)\s*([a-z]+)", m.group(1), re.I):
            u = u.lower()
            secs += int(n) * (86400 if u.startswith("d") else 3600 if u.startswith("h") else 60)
        return now + secs if secs else None
    m = _AT.search(text)
    if not m:
        return None
    h, mi = int(m.group("h")), int(m.group("mi") or 0)
    ap = (m.group("ap") or "").lower().replace(".", "")
    if ap:
        if not 1 <= h <= 12:
            return None
        h = h % 12 + (12 if ap == "pm" else 0)
    elif not m.group("mi"):          # a bare number without am/pm or minutes is not a time
        return None
    if h > 23 or mi > 59:
        return None
    tz = None
    if m.group("tz"):
        with contextlib.suppress(Exception):
            from zoneinfo import ZoneInfo
            tz = ZoneInfo("UTC" if m.group("tz").upper() == "UTC" else m.group("tz"))
    base = datetime.fromtimestamp(now, tz) if tz else datetime.fromtimestamp(now)
    try:
        if m.group("mon"):
            mon = _MONTHS.get(m.group("mon")[:3].lower())
            if not mon:
                return None
            year = int(m.group("year") or base.year)
            at = base.replace(year=year, month=mon, day=int(m.group("day")), hour=h, minute=mi, second=0, microsecond=0)
            if not m.group("year") and at.timestamp() < now - 86400:
                at = at.replace(year=year + 1)
        else:
            at = base.replace(hour=h, minute=mi, second=0, microsecond=0)
            if at.timestamp() <= now:
                at += timedelta(days=1)
    except ValueError:
        return None
    return at.timestamp()


def from_meters(meters: dict | None, now: float | None = None) -> float | None:
    """The latest reset among exhausted windows (≥ 95 %) of {h5|week: {pct, reset}} / quota report readings."""
    now = time.time() if now is None else now
    best = None
    for w in (meters or {}).values():
        if not isinstance(w, dict):
            continue
        pct = w.get("pct", w.get("used_pct"))
        at = _unix(w.get("reset", w.get("reset_at")))
        if _num(pct) and pct >= EXHAUSTED and at and at > now:
            best = at if best is None else max(best, at)
    return best


def sane(at, now: float | None = None) -> float | None:
    now = time.time() if now is None else now
    return float(at) if _num(at) and now + MIN_LEAD < at <= now + MAX_WAIT else None


def reset_time(harness: str, text=None, rate: dict | None = None, meters: dict | None = None, state_root=None,
               now: float | None = None) -> float | None:
    """First reliable answer wins: the harness's own words / rejected rate event, its live meters, its readings on disk."""
    now = time.time() if now is None else now
    if isinstance(rate, dict) and rate.get("status") == "rejected":
        at = sane(_unix(rate.get("resetsAt")), now)
        if at:
            return at
    at = sane(parse_when(text, now), now) or sane(from_meters(meters, now), now)
    if at or state_root is None:
        return at
    with contextlib.suppress(Exception):
        from . import quota
        a = next((x for x in quota.report(state_root, now)["harnesses"] if x["harness"] == harness), None)
        if a:
            ws = {"h5": a.get("h5")} if a.get("h5") else {}
            if a.get("status") == "ok":
                ws["week"] = {"used_pct": a["used_pct"], "reset_at": a["reset_at"]}
            return sane(from_meters(ws, now), now)
    return None


def hhmm(at: float, lang: str = "zh", now: float | None = None) -> str:
    now = time.time() if now is None else now
    d, today = datetime.fromtimestamp(at), datetime.fromtimestamp(now)
    t = d.strftime("%H:%M")
    if d.date() == today.date():
        return t
    if d.date() == (today + timedelta(days=1)).date():
        return ("tomorrow " if lang == "en" else "明天 ") + t
    return d.strftime("%m-%d ") + t


def continue_text(rec: dict, lang: str = "zh") -> str:
    name = NAMES.get(rec.get("harness"), rec.get("harness") or "AI")
    if lang == "en":
        return (f"Continue. 〔quota reset〕 The last turn stopped because the {name} usage limit was used up; the limit has "
                "reset now, so carry on with what was left unfinished. Sent automatically by Agent J, not new words from "
                "the owner; it grants nothing new.")
    return (f"继续。〔额度恢复〕上一轮因为 {name} 额度用完停下了，现在已到重置时间，请接着把没做完的做完。"
            "这句由 Agent J 自动发送，不是主人新的指令，不授予任何新权限。")


def task_note(rec: dict, lang: str = "zh") -> str:
    """Appended to the workflow's prompt for its automatic rerun."""
    to = NAMES.get(rec.get("to"), rec.get("to")) if rec.get("to") else None
    if lang == "en":
        return ("\n\n(The previous run of this workflow stopped because the AI usage limit was used up; this is the automatic "
                + (f"rerun on {to}. " if to else "rerun after the reset. ")
                + "Check what the last run already produced first, finish the rest, and do not repeat external actions "
                  "that already happened.)")
    return ("\n\n（这个工作流上一次运行因为 AI 额度用完中断，这是" + (f"改用 {to} 的自动重跑。" if to else "额度恢复后的自动重跑。")
            + "先看上一次已经产出的东西，接着做完剩下的，已经做过的外部操作不要重复。）")


def limit_label(harness: str, limit: str | None, lang: str = "zh") -> str:
    """「Claude Code 额度」 / 「Claude Code 的 Opus 专属周限额」 — which allowance is used up, said plainly."""
    name = NAMES.get(harness, harness)
    if limit == "fast":
        return f"{name} fast-mode limit" if lang == "en" else f"{name} 的快速模式（fast mode）限额"
    if limit in MODEL_LIMITS:
        m = MODEL_LIMITS[limit]
        return f"{name}'s {m}-only weekly limit" if lang == "en" else f"{name} 的 {m} 专属周限额"
    return f"{name} usage limit" if lang == "en" else f"{name} 额度"


def switch_advice(harness: str, limit: str | None, lang: str = "zh") -> str:
    """P433: a per-model cap leaves the others free — say which model to switch to (Claude Code's own advice)."""
    if harness != "claude" or limit not in MODEL_LIMITS:
        return ""
    if limit == "fast":
        return (" Other models and normal mode still work: send /fast to turn fast mode off, then 「继续」." if lang == "en"
                else "其他模式不受影响：发 /fast 关掉快速模式，再发一句「继续」就能马上接着做。")
    to = MODEL_LIMITS[SWITCH_TO[limit]]
    return (f" Only {MODEL_LIMITS[limit]} is capped; your 5H / WEEKLY allowance still has room. To go on now, tap the model name "
            f"at the top and pick {to} (or send /model {SWITCH_TO[limit]}), then 「继续」." if lang == "en" else
            f"只有 {MODEL_LIMITS[limit]} 被限，5H / WEEKLY 总额度还有余量。想马上接着做：点顶部的模型名换成 {to}"
            f"（或发 /model {SWITCH_TO[limit]}），再发一句「继续」。")


def stale_note(seen: dict | None, lang: str = "zh", harness: str = "claude") -> str:
    """P433 (#196 correction): the phone's readings said there was room — they are out of date, the limit is real."""
    if not seen:
        return ""
    name = NAMES.get(harness, harness)
    parts = [(("5H" if k == "h5" else "WEEKLY") + f" {round(v)}%") for k, v in sorted(seen.items())]
    if lang == "en":
        return (f" The phone's reading ({', '.join(parts)}) is out of date — {name} itself confirmed the limit; it is "
                "marked 「may be out of date」 until a fresh one arrives.")
    return f"手机上的读数（{'、'.join(parts)}）已过期，以 {name} 自己确认的限额为准；读数已标「可能过期」，等新读数到了自动更新。"


def reset_hint(harness: str, limit: str | None, state_root=None, lang: str = "zh", now: float | None = None) -> str:
    """P433: Claude Code sometimes offers a free reset (its settings → Usage → 「Reset for free」, with an expiry). No harness
    says so locally, so: name one the owner recorded (`agentj quota reset add`), else point at the page. Never pressed for them."""
    if harness != "claude" or limit == "fast":
        return ""
    live = []
    with contextlib.suppress(Exception):
        from . import quota
        live = quota.live_resets(quota.load_state(state_root), harness, time.time() if now is None else now) \
            if state_root is not None else []
    if live:
        exp = datetime.fromtimestamp(live[0]["expires"]).strftime("%m-%d")
        return (f" You have a recorded free reset (expires {exp}): Claude settings → Usage → 「Reset for free」 restores it at "
                "once — Agent J never presses it for you." if lang == "en" else
                f"你记录过一次可用的免费重置（{exp} 到期）：在 Claude 设置 → Usage 里点「Reset for free」就能马上恢复，Agent J 不会替你点。")
    return (" Worth a look: Claude settings → Usage sometimes offers 「Reset for free」 — pressing it restores the allowance at "
            "once (Agent J never presses it for you)." if lang == "en" else
            "可以去 Claude 设置 → Usage 看看有没有「Reset for free」（免费重置）：有的话点一下就能马上恢复，Agent J 不会替你点。")


# ------------------------------------------------------------------ serve side
class Resumer:
    """Owned by serve's Host (self.quota_resume). Records survive a restart; each fires at most once."""

    def __init__(self, host):
        self.host = host
        self.items: dict[str, dict] = {}
        self.timers: dict[str, asyncio.Task] = {}
        self.fired: dict[str, float] = {}       # scope → the reset of the continue already sent (no loop on a stale one)
        self.loaded = False

    @property
    def path(self) -> Path:
        return Path(self.host.st.root) / FILE

    def lang(self) -> str:
        return "en" if getattr(self.host, "lang", "zh") == "en" else "zh"

    def load(self) -> None:
        if self.loaded:
            return
        self.loaded = True
        with contextlib.suppress(OSError, ValueError):
            p = self.path
            if not p.is_symlink() and p.stat().st_size <= 64 * 1024:
                d = json.loads(p.read_text())
                for scope, rec in (d.get("items") or {}).items() if isinstance(d, dict) else ():
                    if self._valid(scope, rec):
                        self.items[scope] = rec

    @staticmethod
    def _valid(scope, rec) -> bool:
        return (isinstance(scope, str) and (scope == "main" or scope.startswith("task:")) and isinstance(rec, dict)
                and isinstance(rec.get("id"), str) and _num(rec.get("at")) and isinstance(rec.get("harness"), str))

    def _save(self) -> None:
        from .state import _write_private
        try:
            _write_private(self.path, json.dumps({"items": self.items}, ensure_ascii=False).encode())
        except OSError as e:
            self.host.st.log("quota_resume_save_failed", reason=type(e).__name__)

    def view(self) -> list[dict]:
        out = []
        for scope, r in sorted(self.items.items(), key=lambda kv: kv[1]["at"]):
            out.append({"id": r["id"], "scope": "main" if scope == "main" else "task", "harness": r["harness"],
                        "at": int(r["at"]), **({"title": r["title"]} if r.get("title") else {}),
                        **({"to": r["to"]} if r.get("to") else {}),
                        **({"limit": r["limit"]} if r.get("limit") in MODEL_LIMITS else {})})
        return out

    def msg(self) -> dict:
        return {"t": "quota_resume", "items": self.view()}

    def push(self) -> None:
        fn = getattr(self.host, "_post", None)
        if fn is None or not hasattr(self.host, "_send_p33"):
            return
        m = self.msg()
        fn(self.host._send_p33, lambda s: m)

    def pending(self, scope: str = "main") -> dict | None:
        return self.items.get(scope)

    # ---- marking
    def hit(self, scope: str, harness: str, at: float | None, title: str | None = None, frm_resume: str | None = None,
            to: str | None = None, task: dict | None = None, limit: str | None = None,
            stale: dict | None = None) -> dict | None:
        """A turn / run of `scope` stopped on the usage limit. at = the reset (None = unknown: only a notice).
        P433: one reset per harness + limit — the chat and every workflow waiting on the same limit show the same time
        (the newest one the harness reported wins; a hit without a time takes the one already known). `stale` = the phone's
        readings that said otherwise (named as out of date in the notice); Claude's notice ends with the free-reset hint."""
        self.load()
        lang = self.lang()
        name = NAMES.get(harness, harness)
        old = self.items.get(scope)
        limit = limit or ""
        if not to:
            same = [(k, r) for k, r in self.items.items() if k != scope and not r.get("to") and r.get("harness") == harness
                    and r.get("limit", "") == limit]
            if at is None and same:
                at = same[0][1]["at"]
            for k, r in same if at is not None else ():
                if r["at"] != at:
                    r["at"] = float(at)
                    self._arm(k)
        if frm_resume and old is None and not to:
            prev = self.fired.get(scope)
            if prev is not None and (at is None or at <= prev + 60):
                self.host.st.log("quota_resume", scope=scope.split(":")[0], result="again_no_later_reset")
                self._notice(scope, title, (f"{name} is still out of quota after the automatic continue; not retrying. "
                                            "Send 「继续」 yourself once it recovers." if lang == "en" else
                                            f"自动继续以后 {name} 额度还是用完的状态，不再自动重试；恢复后你自己发一句「继续」就行。"))
                return None
        if at is None and not to:
            self.host.st.log("quota_resume", scope=scope.split(":")[0], result="no_reset_time", agent=harness)
            self._notice(scope, title, (f"{limit_label(harness, limit, lang)} is used up, and it did not say when it resets — "
                                        "nothing is scheduled. Once it recovers, send 「继续」." if lang == "en" else
                                        f"{limit_label(harness, limit, lang)}用完了，它没给重置时间，所以没有安排自动继续。"
                                        "恢复后发一句「继续」就行。") + switch_advice(harness, limit, lang)
                         + self._extra(harness, limit, stale, lang))
            return None
        rec = {"id": old["id"] if old and old.get("at") == at and old.get("to") == to else secrets.token_hex(8),
               "harness": harness, "at": float(at), "created": time.time(), **({"limit": limit} if limit else {})}
        if title:
            rec["title"] = str(title)[:64]
        if to:
            rec.update(to=to, frm=harness)
        if task and isinstance(task.get("trigger"), str):
            rec["trigger"] = task["trigger"]
        self.items[scope] = rec
        self._save()
        self.host.st.log("quota_resume", scope=scope.split(":")[0], result="scheduled", agent=harness,
                         to=to, wait=int(max(0, at - time.time())))
        when = hhmm(at + (0 if to else RESUME_MARGIN), lang)
        what = (f"「{title}」" if title and scope != "main" else "")
        lab = limit_label(harness, limit, lang)
        if to:
            text = (f"{lab} is used up. Workflow {what} reruns on {NAMES.get(to, to)} at {when} (the 「hand to "
                    "another AI account」 switch is on). Tap ✕ on the bar above to cancel." if lang == "en" else
                    f"{lab}用完了。工作流{what}将在 {when} 改用 {NAMES.get(to, to)} 接着跑（「额度告警时交给其他 AI 账号」已打开）。"
                    "不需要的话点上方横条的 ✕ 取消。")
        elif scope == "main":
            text = (f"{lab} is used up. Expected to continue automatically at {when} (after the reset) with one "
                    "「continue」. Tap ✕ on the bar above to cancel." if lang == "en" else
                    f"{lab}用完了，预计 {when}（额度重置后）自动发一句「继续」接着做。不需要的话点上方横条的 ✕ 取消。")
        else:
            text = (f"{lab} is used up. Workflow {what} reruns automatically at {when}. Tap ✕ on the bar above "
                    "to cancel." if lang == "en" else
                    f"{lab}用完了，工作流{what}预计 {when} 自动重跑。不需要的话点上方横条的 ✕ 取消。")
        if not to:
            text += switch_advice(harness, limit, lang)
        self._notice(scope, title, text + self._extra(harness, limit, stale, lang))
        self._arm(scope)
        self.push()
        return rec

    def _extra(self, harness: str, limit: str | None, stale: dict | None, lang: str) -> str:
        return stale_note(stale, lang, harness) + reset_hint(harness, limit, getattr(self.host.st, "root", None), lang)

    def _notice(self, scope: str, title, text: str) -> None:
        with contextlib.suppress(Exception):
            self.host.agent_notice(text)

    # ---- dropping
    def cancel(self, rid: str, by: str = "phone") -> bool:
        self.load()
        for scope, rec in list(self.items.items()):
            if rec["id"] == rid:
                self.drop(scope, "cancelled", by=by)
                with contextlib.suppress(Exception):
                    self.host.agent_notice("Automatic continue cancelled." if self.lang() == "en" else "已取消自动继续。")
                return True
        return False

    def owner_spoke(self, scope: str = "main") -> None:
        """The owner moved on by hand (a phone `say`, a new run of the task): the waiting continue is not needed."""
        if scope in self.items:
            self.drop(scope, "owner_spoke")

    def drop(self, scope: str, why: str, by: str | None = None) -> dict | None:
        rec = self.items.pop(scope, None)
        t = self.timers.pop(scope, None)
        if t is not None and t is not _current():
            t.cancel()
        if rec is not None:
            self._save()
            self.host.st.log("quota_resume", scope=scope.split(":")[0], result=why, **({"device": by} if by else {}))
            self.push()
        return rec

    # ---- timers
    def start(self) -> None:
        """Serve start: re-arm what survived; a chat record made on another harness fires at once (the owner switched)."""
        self.load()
        for scope in list(self.items):
            self._arm(scope)

    def _arm(self, scope: str) -> None:
        old = self.timers.pop(scope, None)
        if old is not None and old is not _current():
            old.cancel()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self.timers[scope] = loop.create_task(self._wait(scope, self.items[scope]["id"]))

    def due_at(self, rec: dict, scope: str) -> float:
        if rec.get("to"):
            return rec["at"]
        a = getattr(self.host, "agent", None)
        if scope == "main" and a is not None and getattr(a, "kind", None) not in (None, rec["harness"]):
            return 0.0          # serve came back on another harness: that account has its own quota
        return rec["at"] + RESUME_MARGIN

    async def _wait(self, scope: str, rid: str) -> None:
        while True:
            rec = self.items.get(scope)
            if rec is None or rec["id"] != rid:
                return
            left = self.due_at(rec, scope) - time.time()
            if left <= 0:
                break
            await asyncio.sleep(min(left, 300))      # re-read: a suspended laptop's clock jumps
        self.timers.pop(scope, None)
        try:
            self.fire(scope)
        except Exception as e:  # noqa: BLE001 — never costs serve
            self.host.st.log("quota_resume", scope=scope.split(":")[0], result="error", reason=type(e).__name__)

    def busy(self) -> bool:
        a = getattr(self.host, "agent", None)
        if a is None:
            return True
        if getattr(a, "status", "idle") in ("working", "compacting", "waiting") or not a.q.empty():
            return True
        if any(not x.fut.done() for x in list(getattr(self.host, "asks", {}).values())):
            return True
        lock = getattr(self.host, "turn_lock", None)
        return bool(lock is not None and lock.locked())

    def fire(self, scope: str) -> str:
        rec = self.items.get(scope)
        if rec is None:
            return "gone"
        stopped = getattr(self.host, "stopped", None)
        if callable(stopped) and stopped():
            self.drop(scope, "stopped")
            return "stopped"
        if scope == "main":
            if self.busy():
                self.drop(scope, "busy")
                return "busy"
            self.drop(scope, "fired")
            self.fired[scope] = rec["at"]
            send_resume(self.host, rec, self.lang())
            return "fired"
        sch = getattr(self.host, "scheduler", None)
        if sch is None:
            self.drop(scope, "no_scheduler")
            return "no_scheduler"
        tid = scope.split(":", 1)[1]
        self.drop(scope, "fired")
        self.fired[scope] = rec["at"]
        sch.resume[tid] = rec
        try:
            sch.request(tid, "quota_resume")
        except Exception as e:  # noqa: BLE001 — TaskError (busy / unknown / uncertain): the owner sees why
            sch.resume.pop(tid, None)
            self.host.st.log("quota_resume", scope="task", result="refused", reason=getattr(e, "reason", type(e).__name__))
            return "refused"
        return "fired"


def _current():
    with contextlib.suppress(RuntimeError):
        return asyncio.current_task()
    return None


def send_resume(host, rec: dict, lang: str = "zh") -> None:
    """One fresh turn: its own page (via quota), system provenance, nothing attached, never a phone's say."""
    from . import provenance
    from .compose import Send
    page = host.hist_add({"k": "agent", "via": "quota", "text": ""}, "", "open")
    s = Send("quota", secrets.token_urlsafe(16), page["id"], text=continue_text(rec, lang), by="quota_resume")
    s.source = provenance.system()
    s.quota_resume_id = rec["id"]
    host.st.log("quota_resume_sent", agent=rec["harness"])
    host.agent.submit(s)


# ------------------------------------------------------------------ adapter helpers
def host_meters(host, harness: str) -> dict | None:
    """The phone's live meters belong to the chat's harness only (a workflow on another harness reads its own on disk)."""
    a = getattr(host, "agent", None)
    kind = getattr(a, "kind", None) if a is not None else None
    m = getattr(host, "meter_state", None)
    return m if isinstance(m, dict) and kind in (None, harness) else None


def main_hit(agent, harness: str, text=None, rate: dict | None = None) -> None:
    """Called by an adapter whose chat turn ended on a used-up limit (never a workflow run: tasks.py reads `limit_hit`)."""
    host = agent.host
    own = not agent.cfg.get("_workflow_ceo")
    v = judge(harness, text, rate, host_meters(host, harness), getattr(host.st, "root", None))
    if v is None:
        return
    mark_stale(host, harness, v)
    agent.limit_hit = {"harness": harness, "at": v["at"], "limit": v["limit"], **({"stale": v["stale"]} if v.get("stale") else {})}
    if not own:
        return
    r = getattr(host, "quota_resume", None)
    if r is None:
        return
    send = getattr(agent, "cur_send", None)
    r.hit("main", harness, v["at"], frm_resume=getattr(send, "quota_resume_id", None), limit=v["limit"], stale=v.get("stale"))


def mark_stale(host, harness: str, v: dict | None) -> None:
    """P433 (#196 correction): a confirmed limit the phone's readings contradict — those readings are out of date. Mark them
    (`stale`) and push the meters at once; the next exact reading (a fresh rate event) replaces the window and the mark."""
    seen = (v or {}).get("stale")
    m = host_meters(host, harness)
    fn = getattr(host, "meter_update", None)
    if not seen or m is None or fn is None:
        return
    upd = {k: {**m[k], "stale": True} for k in seen if isinstance(m.get(k), dict) and not m[k].get("stale")}
    if upd:
        with contextlib.suppress(Exception):
            host.st.log("quota_resume", result="readings_stale", agent=harness, windows=",".join(sorted(upd)))
            fn(**upd)
