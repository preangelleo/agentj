"""P254: the quota steward — each harness's weekly quota pace and reset opportunities, in the heartbeat and the morning brief.

Developer reference: documentation/notes/P0263-HOST-REFERENCE.md#quota.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import time
import threading
from datetime import datetime
from pathlib import Path
from .quota_pace import pace as quota_pace

WEEK_S = 7 * 86400
DAY_S = 86400
DAY_SHARE = 100 / 7                 # ≈ 14.3 % of a weekly quota per day
BEHIND_DAYS = 1.0                   # rule ①: more than one day's share behind the even pace
LATE_DAYS = 3.0                     # rule ②: the second half of the week — at most 3 days left
EXPIRING_DAYS = 7.0                 # rule ③: a reset that expires within a week
EXHAUSTED_PCT = 95.0                # rule ④: "见底"
H5_STOP_PCT = 90.0                  # no new headless work on this harness
H5_WARN_PCT = 80.0                  # 5-hour window: warn at 80 %
WEEK_WARN_PCT = 90.0                # weekly: near the bottom with no recorded reset
H5_S = 5 * 3600
MIN_ELAPSED_DAYS = 1.0              # no pace judgement in the first day of a window
STATE_FILE = "quota.json"
HARNESSES = ("claude", "codex", "opencode")
NAMES = {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode"}
TAIL_BYTES = 1 << 20                # read at most the last 1 MiB of a Codex session log
MAX_RESETS = 16
KINDS = ("h5_stop", "h5_high", "week_high", "underuse", "late_surplus", "reset_expiring", "reset_now")


# ------------------------------------------------------------------ sources (never raise)
def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and v not in (float("inf"), float("-inf"))


def unknown(harness: str, why: str) -> dict:
    return {"harness": harness, "status": "unknown", "why": why}


def claude_source(state_root, now: float | None = None) -> dict:
    """Newest tapped Claude Code status line with a seven-day window."""
    now = time.time() if now is None else now
    d = Path(state_root) / "claude-statusline"
    try:
        files = [p for p in d.iterdir() if p.suffix == ".json" and not p.is_symlink() and p.is_file()]
    except OSError:
        return unknown("claude", "no_statusline_tap")
    h5 = None
    for p in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
        try:
            if p.stat().st_size > 16384:
                continue
            data = json.loads(p.read_text())
            w, at = data.get("week"), p.stat().st_mtime
            if h5 is None:
                h5 = _window(data.get("h5"), "pct", "reset", at)
            if isinstance(w, dict) and _num(w.get("pct")) and _num(w.get("reset")):
                out = {"harness": "claude", "status": "ok", "source": "statusline", "used_pct": float(w["pct"]),
                       "reset_at": float(w["reset"]), "source_at": at}
                from .claude_statusline import activity, stale
                if stale(at, activity(data.get("session_id")), now):   # P565B: Claude kept working, the tap went quiet
                    out.update(status="stale", why="stale_reading", stale=True)
                return {**out, "h5": h5} if h5 else out
        except (OSError, ValueError, AttributeError):
            continue
    out = unknown("claude", "no_statusline_tap" if not files else "no_week_window")
    return {**out, "h5": h5} if h5 else out


def _window(w, pct_key: str, reset_key: str, at: float) -> dict | None:
    """One 5-hour reading {used_pct, reset_at, source_at}, or None."""
    if isinstance(w, dict) and _num(w.get(pct_key)) and _num(w.get(reset_key)):
        return {"used_pct": float(w[pct_key]), "reset_at": float(w[reset_key]), "source_at": at}
    return None


def codex_home(env=None) -> Path:
    e = os.environ if env is None else env
    return Path(e.get("CODEX_HOME") or os.path.join(e.get("HOME") or os.path.expanduser("~"), ".codex"))


def _newest_logs(sessions: Path, limit: int = 8) -> list[Path]:
    """Newest rollout logs, walking YYYY/MM/DD from the newest down (no full tree scan)."""
    out: list[Path] = []

    def kids(p: Path) -> list[Path]:
        try:
            return sorted((c for c in p.iterdir() if not c.is_symlink()), key=lambda c: c.name, reverse=True)
        except OSError:
            return []
    for y in kids(sessions):
        for m in kids(y) if y.is_dir() else []:
            for day in kids(m) if m.is_dir() else []:
                logs = [f for f in kids(day) if f.suffix == ".jsonl" and f.is_file()] if day.is_dir() else []
                out += sorted(logs, key=lambda f: f.stat().st_mtime, reverse=True)
                if len(out) >= limit:
                    return out[:limit]
    return out


def _codex_limits(f: Path) -> dict | None:
    """The last rate_limits object in one session log (tail only)."""
    try:
        with f.open("rb") as fh:
            size = fh.seek(0, 2)
            fh.seek(max(0, size - TAIL_BYTES))
            lines = fh.read().splitlines()
    except OSError:
        return None
    for raw in reversed(lines):
        if b'"rate_limits"' not in raw:
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        stack = [obj]
        while stack:                                   # rate_limits may sit at any depth of the event
            o = stack.pop()
            if isinstance(o, dict):
                if isinstance(o.get("rate_limits"), dict):
                    limits = dict(o["rate_limits"])
                    # P497: later writes to this log are not fresh quota measurements.
                    stamp = obj.get("timestamp")
                    try:
                        limits["_observed_at"] = datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() \
                            if isinstance(stamp, str) and datetime.fromisoformat(stamp.replace("Z", "+00:00")).tzinfo else None
                    except (ValueError, TypeError, OverflowError):
                        limits["_observed_at"] = None
                    return limits
                stack += list(o.values())
            elif isinstance(o, list):
                stack += o
    return None


def codex_source(env=None, now: float | None = None) -> dict:
    sessions = codex_home(env) / "sessions"
    logs = _newest_logs(sessions)
    if not logs:
        return unknown("codex", "no_session_logs")
    for f in logs:
        rl = _codex_limits(f)
        if not rl:
            continue
        at = rl.get("_observed_at")
        h5 = next((x for x in (_window(rl.get(k), "used_percent", "resets_at", at) for k in ("primary", "secondary")
                               if isinstance(rl.get(k), dict) and rl[k].get("window_minutes") == 300) if x), None)
        for key in ("primary", "secondary"):
            w = rl.get(key)
            if isinstance(w, dict) and w.get("window_minutes") == 10080 and _num(w.get("used_percent")) and _num(w.get("resets_at")):
                out = {"harness": "codex", "status": "ok", "source": "session_log", "used_pct": float(w["used_percent"]),
                       "reset_at": float(w["resets_at"]), "source_at": at}
                c = rl.get("credits")
                if isinstance(c, dict) and isinstance(c.get("has_credits"), bool):
                    bal = c.get("balance")
                    with contextlib.suppress(TypeError, ValueError):
                        bal = float(bal) if bal is not None else None
                    out["credits"] = {"has_credits": c["has_credits"], "unlimited": c.get("unlimited") is True,
                                      "balance": bal if _num(bal) else None}
                return {**out, "h5": h5} if h5 else out
        out = unknown("codex", "no_week_window")
        return {**out, "h5": h5} if h5 else out
    return unknown("codex", "no_rate_limits")


def opencode_source(meter_state: dict | None = None) -> dict:
    """OpenCode keeps no quota itself; when serve already measured the provider's own public usage endpoint (P67 meter
    `quota_windows`, weekly window) the used share is known, but no provider reports a reset time: partial, no pace."""
    ws = (meter_state or {}).get("quota_windows")
    for w in ws if isinstance(ws, list) else []:
        if isinstance(w, dict) and w.get("window") == "weekly" and _num(w.get("pct")):
            return {"harness": "opencode", "status": "partial", "source": "provider_usage", "used_pct": float(w["pct"]),
                    "why": "no_reset_time"}
    return unknown("opencode", "not_exposed")


# ------------------------------------------------------------------ state (dedupe, recorded resets)
def state_path(state_root) -> Path:
    return Path(state_root) / STATE_FILE


def load_state(state_root) -> dict:
    p = state_path(state_root)
    d = {}
    with contextlib.suppress(OSError, ValueError):
        if not p.is_symlink() and p.stat().st_size <= 256 * 1024:
            d = json.loads(p.read_text())
    if not isinstance(d, dict):
        d = {}
    d["sent"] = d["sent"] if isinstance(d.get("sent"), dict) else {}
    d["credit_sent"] = d["credit_sent"] if isinstance(d.get("credit_sent"), dict) else {}
    d["resets"] = [r for r in d.get("resets", []) if valid_reset(r)] if isinstance(d.get("resets"), list) else []
    return d


def save_state(state_root, d: dict) -> None:
    from .state import _write_private
    sent = d.get("sent", {})
    if len(sent) > 64:
        d["sent"] = dict(sorted(sent.items(), key=lambda kv: str(kv[1]))[-64:])
    _write_private(state_path(state_root), json.dumps(d, ensure_ascii=False).encode())


def valid_reset(r) -> bool:
    return (isinstance(r, dict) and r.get("harness") in HARNESSES and _num(r.get("expires"))
            and (r.get("note") is None or isinstance(r.get("note"), str)))


def parse_expires(raw: str, now: float | None = None) -> float:
    """YYYY-MM-DD (end of that local day) or YYYY-MM-DD HH:MM / ...THH:MM (local)."""
    s = str(raw or "").strip()
    for fmt, end in (("%Y-%m-%d", True), ("%Y-%m-%d %H:%M", False), ("%Y-%m-%dT%H:%M", False)):
        try:
            t = datetime.strptime(s, fmt)
        except ValueError:
            continue
        ts = t.timestamp() + (DAY_S - 1 if end else 0)
        if ts <= (time.time() if now is None else now):
            raise ValueError("already expired")
        return ts
    raise ValueError("expected YYYY-MM-DD or YYYY-MM-DD HH:MM")


def add_reset(state_root, harness: str, expires: float, note: str | None = None, now: float | None = None) -> dict:
    if harness not in HARNESSES:
        raise ValueError("harness must be one of " + ", ".join(HARNESSES))
    note = re.sub(r"[\x00-\x1f]", " ", note)[:80] if isinstance(note, str) and note.strip() else None
    d = load_state(state_root)
    if len(d["resets"]) >= MAX_RESETS:
        raise ValueError("too many recorded resets")
    r = {"harness": harness, "expires": float(expires), "added_at": time.time() if now is None else now}
    if note:
        r["note"] = note
    d["resets"].append(r)
    save_state(state_root, d)
    return r


def use_reset(state_root, harness: str, now: float | None = None) -> dict | None:
    """The owner pressed it: the soonest-expiring live reset of that harness goes away."""
    now = time.time() if now is None else now
    d = load_state(state_root)
    live = sorted((r for r in d["resets"] if r["harness"] == harness and r["expires"] > now), key=lambda r: r["expires"])
    if not live:
        return None
    d["resets"].remove(live[0])
    save_state(state_root, d)
    return live[0]


def live_resets(d: dict, harness: str, now: float) -> list[dict]:
    return sorted((r for r in d.get("resets", []) if r.get("harness") == harness and r["expires"] > now),
                  key=lambda r: r["expires"])


# ------------------------------------------------------------------ the analysis (pure)
def analyse(src: dict, resets: list[dict], now: float) -> dict:
    """Pace for one harness. `resets` = its live recorded resets."""
    out = dict(src)
    out["name"] = NAMES.get(src["harness"], src["harness"])
    out["at"] = now
    out["resets"] = [{"expires": r["expires"], **({"note": r["note"]} if r.get("note") else {})} for r in resets]
    out["pace"] = quota_pace(src.get("used_pct"), src.get("reset_at"), now,
                             stale=src.get("status") != "ok" or bool(src.get("stale"))
                             or not _num(src.get("source_at")) or not 0 <= now-src["source_at"] <= 300
                             or (_num(src.get("reset_at")) and src["source_at"] < src["reset_at"]-WEEK_S))
    h5 = src.get("h5")
    # Keep validity separate from display clamping: --pick must not use a repaired reading.
    out["weekly_valid"] = (src.get("weekly_valid") is not False and src.get("status") in ("ok", "stale") and src.get("weekly_valid") is not False
            and not ("weekly_valid" not in src and src.get("reading_valid") is False) and _num(src.get("used_pct")) and 0 <= src["used_pct"] <= 100
                           and _num(src.get("reset_at")) and _num(src.get("source_at")))
    out["h5_present"] = isinstance(h5, dict)
    out["h5_valid"] = not isinstance(h5, dict) or all(_num(h5.get(k)) for k in ("used_pct", "reset_at", "source_at")) and 0 <= h5["used_pct"] <= 100
    out["reading_valid"] = not src.get("stale") and all(_num(w.get("used_pct")) and 0 <= w["used_pct"] <= 100
                                                        for w in (src, h5) if isinstance(w, dict))
    out.pop("h5", None)
    if isinstance(h5, dict) and (not src.get("stale") or src.get("weekly_cached")) and h5["reset_at"] > now and h5["reset_at"] - now <= H5_S + 600:   # a stale window is no reading
        out["h5"] = {"used_pct": round(min(100.0, max(0.0, h5["used_pct"])), 1), "reset_at": h5["reset_at"],
                     "source_at": h5.get("source_at")}
    if src.get("status") == "partial":
        out["used_pct"] = round(min(100.0, max(0.0, src["used_pct"])), 1)
    if src.get("status") != "ok":
        return out
    reset_at = src.get("reset_at")
    if not out["weekly_valid"] or not _num(reset_at) or reset_at > now + WEEK_S:
        out.update(status="unknown", why="invalid_window")
        return out
    if reset_at <= now:                                # the window rolled over since this reading
        out.update(status="unknown", why="window_rolled_over")
        return out
    used = src["used_pct"]
    elapsed = min(7.0, max(0.0, (now - (reset_at - WEEK_S)) / DAY_S))
    left = 7.0 - elapsed
    pace = elapsed * DAY_SHARE
    out.update(used_pct=round(used, 1), elapsed_days=round(elapsed, 2), days_left=round(left, 2),
               even_pace_pct=round(pace, 1), behind_days=round((pace - used) / DAY_SHARE, 2),
               remaining_pct=round(100 - used, 1))
    if elapsed >= 0.5:
        proj = used / elapsed * 7.0
        out["projected_end_used_pct"] = round(min(100.0, proj), 1)
        out["projected_end_unused_pct"] = round(max(0.0, 100.0 - proj), 1)
    return out


def assess(a: dict) -> list[str]:
    """Deterministic reminder kinds for one analysed harness, most urgent first."""
    pct = (a.get("h5") or {}).get("used_pct", 0)
    kinds = ["h5_stop"] if pct >= H5_STOP_PCT else ["h5_high"] if pct >= H5_WARN_PCT else []
    if a.get("status") != "ok":
        return kinds
    used, left, behind, elapsed = a["used_pct"], a["days_left"], a["behind_days"], a["elapsed_days"]
    soon = [r for r in a.get("resets", []) if r["expires"] - a["at"] <= EXPIRING_DAYS * DAY_S]
    if used >= EXHAUSTED_PCT and a.get("resets"):
        kinds.append("reset_now")                      # ④ 见底 + reset available
    elif soon:
        kinds.append("reset_expiring")                 # ③ a reset expires within 7 days
    if used >= WEEK_WARN_PCT and not a.get("resets"):
        kinds.append("week_high")                      # weekly near the bottom, nothing to reset with
    if used < EXHAUSTED_PCT:
        if left <= LATE_DAYS and a["remaining_pct"] > (left + 1) * DAY_SHARE:
            kinds.append("late_surplus")               # ② second half of the week, more than a day's share to spare
        elif elapsed >= MIN_ELAPSED_DAYS and behind > BEHIND_DAYS:
            kinds.append("underuse")                   # ① behind the even pace by more than a day
    return kinds


def _when(ts: float, lang: str) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


def headroom(rep_or_list, harness: str) -> list[dict]:
    """Other harnesses whose own readings show room: weekly < 90 % and 5-hour < 80 % (both when known)."""
    hs = rep_or_list["harnesses"] if isinstance(rep_or_list, dict) else rep_or_list
    out = []
    for b in hs:
        if b["harness"] == harness or b.get("status") not in ("ok", "partial") or b["used_pct"] >= WEEK_WARN_PCT:
            continue
        if (b.get("h5") or {}).get("used_pct", 0) >= H5_WARN_PCT:
            continue
        out.append({"harness": b["harness"], "name": b["name"], "used_pct": b["used_pct"]})
    return out


def _alt(alts: list[dict], en: bool) -> str:
    if not alts:
        return ""
    names = ", ".join(f"{x['name']} ({x['used_pct']:.0f}% of its week)" for x in alts) if en else \
        "、".join(f"{x['name']}（本周已用 {x['used_pct']:.0f}%）" for x in alts)
    return (f" Other accounts with room: {names} — new work could go there (agentj agent switch checks it first)." if en else
            f"另有额度宽裕的账号：{names}——新活可以派给它（先用 agentj agent switch 握手确认能用）。")


def reminder_text(a: dict, kind: str, lang: str = "zh", alts: list[dict] | None = None) -> str:
    n, en = a["name"], lang == "en"
    if kind in ("h5_high", "h5_stop"):
        h = a["h5"]
        if kind == "h5_stop":
            return (f"{n}: 5-hour quota {h['used_pct']:.0f}% used; no new headless work until {_when(h['reset_at'], lang)}. Use another available AI account." if en else
                    f"{n} 的 5 小时额度已用 {h['used_pct']:.0f}%，重置（{_when(h['reset_at'], lang)}）前停止新起本账号无头任务；可改派其他可用 AI 账号。") + _alt(alts or [], en)
        return (f"{n}: the 5-hour window is {h['used_pct']:.0f}% used (resets {_when(h['reset_at'], lang)}). From now on a big "
                "job may be stopped by the server at any moment — just so the owner knows." if en else
                f"{n} 的 5 小时额度已用 {h['used_pct']:.0f}%（{_when(h['reset_at'], lang)} 重置）。从现在起，大一点的活随时可能"
                "被服务器直接终止——让主人心里有数。") + _alt(alts or [], en)
    reset = _when(a["reset_at"], lang)
    if kind == "week_high":
        return (f"{n}: {a['used_pct']:.0f}% of the weekly quota used with {a['days_left']:.1f} days to the reset ({reset}) "
                "and no extra reset on record." if en else
                f"{n} 本周额度已用 {a['used_pct']:.0f}%，离 {reset} 重置还有 {a['days_left']:.1f} 天，没有登记的额外 reset。") \
            + _alt(alts or [], en)
    left = a.get("days_left", 0)
    if kind in ("reset_now", "reset_expiring"):
        r = a["resets"][0]
        exp, days = _when(r["expires"], lang), max(0.0, (r["expires"] - a.get("at", time.time())) / DAY_S)
        count = len(a["resets"])
        if kind == "reset_now":
            return (f"{n}: weekly quota {a['used_pct']:.0f}% used (exhausted) and {count} extra reset(s) on record (expires {exp}). "
                    "Tell the owner to use the reset now." if en else
                    f"{n} 本周额度已用 {a['used_pct']:.0f}%，见底了；还有 {count} 次额外 reset（{exp} 过期）——请主人现在就按 reset。")
        return (f"{n}: {count} extra reset(s) on record, expiring {exp} (in {days:.1f} days) — unused it is wasted. Suggest "
                "more work this week; once the quota runs out, use the reset straight away." if en else
                f"{n} 有 {count} 次额外 reset，{exp} 过期（还剩 {days:.1f} 天），不用就白白浪费。建议这周加大工作量、多派活；"
                "额度一见底就立刻用掉它。")
    unused = a.get("projected_end_unused_pct")
    tail_en = f"; at this pace about {unused:.0f}% goes unused by the reset ({reset})" if unused is not None else ""
    tail_zh = f"，照这个速度到 {reset} 重置时约 {unused:.0f}% 会浪费" if unused is not None else ""
    if kind == "late_surplus":
        return (f"{n}: second half of the week — {left:.1f} days left with {a['remaining_pct']:.0f}% of the weekly quota "
                f"remaining (an even pace uses about {left * DAY_SHARE:.0f}%){tail_en}. Proactively propose big jobs." if en else
                f"{n} 已到后半周：只剩 {left:.1f} 天，还有 {a['remaining_pct']:.0f}% 周额度（均分只用得掉约 {left * DAY_SHARE:.0f}%）"
                f"{tail_zh}。请主动建议派大活。")
    return (f"{n}: {a['used_pct']:.0f}% of the weekly quota used; the even 7-day pace (≈14.3%/day) says about "
            f"{a['even_pace_pct']:.0f}% by now — {a['behind_days']:.1f} days' share behind{tail_en}. There is room for more work."
            if en else
            f"{n} 本周额度已用 {a['used_pct']:.0f}%，按 7 天均分（每天约 14.3%）此时应约 {a['even_pace_pct']:.0f}%，"
            f"落后约 {a['behind_days']:.1f} 天的份额{tail_zh}。额度会浪费，可以多派点活。")


ASK = {"zh": "这是额度管家的提醒（确定性规则，不是主人原话）：请告诉主人；额度富余 / reset 类，结合主人的工作流和待办给 1–3 个具体的大活建议"
             "（谁来做、大概多久）；5 小时 / 周额度告警类只让主人知道，点了别的账号就建议新活交给它（不切当前会话）；"
             "不要回〔静默消息〕。主人说「别提醒额度」就运行 agentj quota off。",
       "en": "This is the quota steward (deterministic rules, not the owner's words): tell the owner; for spare quota / resets, "
             "from the owner's workflows and to-dos, propose 1–3 concrete big jobs (who does it, roughly how long); for 5-hour / "
             "weekly warnings just let the owner know and, if another account is named, suggest new work goes there (this "
             "conversation stays where it is); do not reply 〔静默消息〕. If the owner says to stop quota reminders, run agentj quota off."}
TAG = {"zh": "〔额度提醒〕", "en": "[Quota]"}


# ------------------------------------------------------------------ report + reminders
def reminders_on(prefs: dict | None) -> bool:
    from . import preferences
    return preferences.get(prefs or {}, "quota.reminders", True) is not False


_WEEKLY_CACHE_LOCK = threading.Lock()


def _weekly_sample(src, now):
    kind = src.get("harness")
    return (kind in ("claude", "codex") and src.get("source") in (("session_log", "app_server") if kind == "codex" else ("statusline",))
            and src.get("status") in ("ok", "stale") and src.get("weekly_valid") is not False
            and not ("weekly_valid" not in src and src.get("reading_valid") is False) and _num(src.get("used_pct")) and 0 <= src["used_pct"] <= 100
            and _num(src.get("source_at")) and 0 <= src["source_at"] <= now and _num(src.get("reset_at"))
            and now < src["reset_at"] <= now + WEEK_S)


def last_weekly(state_root, src, acc, now):
    """Keep only genuine unexpired native weekly samples, isolated by harness and managed account id."""
    kind = src["harness"]
    if kind not in ("claude", "codex"):
        return src
    key = kind + ":" + str((acc.get(kind) or {}).get("active") or "@unmanaged")
    path = Path(state_root) / "quota-weekly-cache.json"
    with _WEEKLY_CACHE_LOCK:
        cache = {}
        try:
            if not path.is_symlink() and path.stat().st_size <= 65536:
                cache = json.loads(path.read_text())
        except (OSError, ValueError):
            pass
        if not isinstance(cache, dict):
            cache = {}
        cache = {k: v for k, v in cache.items() if isinstance(k, str) and isinstance(v, dict) and k.partition(":")[0] == v.get("harness") and _weekly_sample(v, now)}
        for account in (acc.get(kind) or {}).get("items", []):
            historical = cache.get(kind + ":" + account["id"])
            previous = account.get("last_quota") or {}
            if historical and historical['source_at'] > (previous.get('at') or -1):
                account['last_quota'] = {'pct': historical['used_pct'], 'window': 'week',
                                         'at': historical['source_at'], 'reset_at': historical['reset_at']}
        if _weekly_sample(src, now):
            old = cache.get(key)
            if not old or src['source_at'] > old['source_at']:
                cache[key] = {k: src[k] for k in ('harness', 'source', 'status', 'used_pct', 'reset_at', 'source_at')}
                cache = dict(sorted(cache.items(), key=lambda kv: kv[1]['source_at'])[-64:])
                from .state import _write_private
                with contextlib.suppress(OSError):
                    _write_private(path, json.dumps(cache).encode())
            return src
        old = cache.get(key)
        return {**old, 'status': 'stale', 'stale': True, 'weekly_cached': True, 'why': 'last_weekly_reading',
                **({'h5': src['h5']} if isinstance(src.get('h5'), dict) else {})} if old else src


def report(state_root, now: float | None = None, env=None, sources=None, meter_state: dict | None = None) -> dict:
    now = time.time() if now is None else now
    d = load_state(state_root)
    srcs = sources if sources is not None else []
    if sources is None:
        for fn in (lambda: claude_source(state_root, now), lambda: codex_source(env, now), lambda: opencode_source(meter_state)):
            try:
                srcs.append(fn())
            except Exception:  # noqa: BLE001 — a broken source is unknown, never an error
                pass
    seen = {s["harness"] for s in srcs}
    srcs += [unknown(h, "source_failed") for h in HARNESSES if h not in seen]
    acc = _accounts(state_root)
    from . import codex_reset_credits as native
    native_accounts = native.read_accounts(state_root, acc, now, env) if sources is None else {}
    active_codex = (acc.get("codex") or {}).get("active")
    active_native = native_accounts.get(active_codex) or {}
    if active_native.get("reading"):
        srcs = [active_native["reading"] if s["harness"] == "codex" else s for s in srcs]
    out = []
    for s in srcs:
        s = last_weekly(state_root, since_switch(s, acc), acc, now)
        a = analyse(s, live_resets(d, s["harness"], now), now)
        a["reminders"] = assess(a)
        rows = account_rows(acc, s["harness"], a)
        if s["harness"] == "codex":
            for row in rows:
                nr = native_accounts.get(row["id"]) or {}
                row["reset_credits"] = nr.get("reset_credits", native.unknown())
                r = nr.get("reading")
                if r:
                    row["quota"] = {"pct": r["used_pct"], "window": "week", "at": r["source_at"],
                                    "reset_at": r["reset_at"], "source": "app_server", "status": "ok",
                                    **({"h5": r["h5"]} if r.get("h5") else {})}
            a["reset_credits"] = active_native.get("reset_credits", native.unknown())
        if rows:
            a["accounts"] = rows
        out.append(a)
    return {"ok": True, "at": now, "day_share_pct": round(DAY_SHARE, 2), "harnesses": out, "codex_accounts": next((a.get("accounts", []) for a in out if a["harness"] == "codex"), [])}


# ------------------------------------------------------------------ #427: readings per account (accounts.py)
def _accounts(state_root) -> dict:
    try:
        from . import accounts
        return accounts.load(state_root)["harness"]
    except Exception:  # noqa: BLE001 — no registry = one account, exactly as before
        return {}


def since_switch(src: dict, acc: dict) -> dict:
    """After an account switch only readings taken later belong to the new account (Codex rate_limits events,
    Claude's tapped status line); an older one is `since_switch` — never shown as the new account's."""
    hd = acc.get(src.get("harness")) or {}
    t = hd.get("switched")
    if not t or not hd.get("items"):
        return src
    out = dict(src)
    h5 = out.get("h5")
    if isinstance(h5, dict) and (not _num(h5.get("source_at")) or h5["source_at"] < t):
        out.pop("h5", None)
    at = out.get("source_at")
    if out.get("status") in ("ok", "stale") and (not _num(at) or at < t):      # no timestamp = cannot be the new account's
        out = {**unknown(out["harness"], "since_switch"), **({"h5": out["h5"]} if out.get("h5") else {})}
    return out


def account_rows(acc: dict, harness: str, a: dict) -> list[dict]:
    """[{id, name, active, quota:{pct, window, at}|None}]: the account in use = this reading; the others = their last one."""
    hd = acc.get(harness) or {}
    rows = []
    for it in hd.get("items") or []:
        if it["id"] == hd.get("active"):
            q = {"pct": round(a["used_pct"], 1), "window": "week", "at": int(a["source_at"]), "reset_at": a.get("reset_at")} \
                if a.get("status") in ("ok", "stale") and a.get("weekly_valid") is not False and _num(a.get("used_pct")) and _num(a.get("source_at")) else it.get("last_quota")
        else:
            q = it.get("last_quota")
        rows.append({"id": it["id"], "name": it["name"], "active": it["id"] == hd.get("active"), "quota": q})
    return rows


def _due(rep: dict, state_root, day: str | None = None, mark: bool = True) -> list[dict]:
    """Reminders not yet given today; marks them given when `mark`."""
    day = day or time.strftime("%Y-%m-%d", time.localtime(rep.get("at") or time.time()))
    d = load_state(state_root)
    out = []
    for a in rep["harnesses"]:
        for k in a.get("reminders", []):
            if k in ("h5_high", "h5_stop"):                         # once per 5-hour window, whatever the day
                key = f"{a['harness']}:{k}:{int(a['h5']['reset_at'])}"
                if key in d["sent"]:
                    continue
            else:
                key = f"{a['harness']}:{k}"
                if d["sent"].get(key) == day:
                    continue
            out.append({"harness": a["harness"], "kind": k, "key": key})
            d["sent"][key] = day
    from . import codex_reset_credits as native
    now = rep.get("at") or time.time()
    for account in rep.get("codex_accounts", []):
        for credit in native.expiring(account.get("reset_credits"), now):
            # Durable per account + credit identity, never daily or expiry based.
            import hashlib
            key = "codex:credit:" + hashlib.sha256((account["id"] + "\0" + credit["id"]).encode()).hexdigest()
            if key in d["credit_sent"] or len(d["credit_sent"]) >= 1024:
                continue
            out.append({"harness": "codex", "kind": "native_reset_expiring", "key": key,
                        "account": {"id": account["id"], "name": account["name"]}, "credit": credit})
            d["credit_sent"][key] = credit["expires_at"]
    if out and mark:
        save_state(state_root, d)
    return out


def due(rep: dict, state_root, day: str | None = None, mark: bool = True) -> list[dict]:
    from .codex_reset_credits import reminder_lock
    with reminder_lock(state_root):
        return _due(rep, state_root, day, mark)


def due_text(item, by, rep, lang):
    if item["kind"] == "native_reset_expiring":
        from .codex_reset_credits import reminder
        return reminder(item["account"], item["credit"], lang)
    return reminder_text(by[item["harness"]], item["kind"], lang, headroom(rep, item["harness"]))


def heartbeat_lines(state_root, prefs: dict | None, lang: str = "zh", now: float | None = None, env=None,
                    meter_state: dict | None = None, active_harness: str | None = None) -> str:
    """What a heartbeat carries: the day's not-yet-given reminders (marked given), or ""."""
    if not reminders_on(prefs):
        return ""
    rep = report(state_root, now, env, meter_state=meter_state)
    if active_harness is not None:
        rep = {**rep, "harnesses": [a for a in rep["harnesses"] if a["harness"] == active_harness]}
    items = due(rep, state_root)
    if not items:
        return ""
    by = {a["harness"]: a for a in rep["harnesses"]}
    lang = "en" if lang == "en" else "zh"
    body = "\n".join("- " + due_text(i, by, rep, lang) for i in items)
    return f"{TAG[lang]} {ASK[lang]}\n{body}"


# ------------------------------------------------------------------ new work to another AI account (RESUME 1)
# Scope: the workflow tasks Agent J itself starts (scheduled or dispatched, tasks.py) — never the main conversation (that
# stays /switch-harness). When the harness about to run one is in alarm (5-hour ≥ 80 % or weekly ≥ 90 %), another harness
# whose own readings show room is handshaken (harness.probe: one tool-free call) and, if it answers, used — at once when
# `quota.auto_switch` is on (default off), otherwise only after the owner says yes on a phone card. A task.json that names
# `harness` is never moved outside it. Any failure keeps the original harness: nothing is ever worse than today.
SWITCH_TOOL = "agentj quota switch"          # the phone card's tool name (approvals.log); never sorted by a goal tier


class QuotaBlocked(RuntimeError):
    """A measured active 5-hour window forbids a new headless run."""


def alarmed(a: dict) -> bool:
    return (a.get("h5") or {}).get("used_pct", 0) >= H5_WARN_PCT or (a.get("status") in ("ok", "partial") and a["used_pct"] >= WEEK_WARN_PCT)


def auto_switch_on(prefs: dict | None) -> bool:
    from . import preferences
    return preferences.get(prefs or {}, "quota.auto_switch", False) is True


def allowed_harnesses(task: dict | None) -> tuple:
    h = (task or {}).get("harness")
    if isinstance(h, str):
        return (h,)
    if isinstance(h, list):
        return tuple(x for x in h if x in HARNESSES)
    return HARNESSES


def handshake(name: str) -> bool:
    """One tool-free call on that harness (`agentj agent switch`'s probe): only a real reply counts."""
    from . import harness
    try:
        d = harness.probe(only=(name,))
    except Exception:  # noqa: BLE001
        return False
    return any(h.get("name") == name and h.get("callable") for h in d.get("harnesses", []))


def alt_cfg(cfg: dict, kind: str) -> dict:
    """The main Agent's settings for a one-off run on another harness: its own default model, its own server."""
    out = {k: v for k, v in cfg.items() if k not in ("model", "effort", "shared_session_id", "shared_opencode_port")}
    out.update(kind=kind, session_mode="independent")
    return out


def switch_text(cur: dict, alt: str, title: str, lang: str = "zh") -> str:
    why = []
    h = cur.get("h5") or {}
    if h.get("used_pct", 0) >= H5_WARN_PCT:
        why.append(f"5-hour {h['used_pct']:.0f}%" if lang == "en" else f"5 小时额度 {h['used_pct']:.0f}%")
    if cur.get("status") in ("ok", "partial") and cur["used_pct"] >= WEEK_WARN_PCT:
        why.append(f"weekly {cur['used_pct']:.0f}%" if lang == "en" else f"周额度 {cur['used_pct']:.0f}%")
    return (f"{cur['name']} is low ({', '.join(why)}). Run 「{title}」 on {NAMES[alt]} instead? (checked: it answers)"
            if lang == "en" else
            f"{cur['name']} 额度告警（{'、'.join(why)}）。「{title}」这次改用 {NAMES[alt]} 来做？（已握手，能用）")


async def pick_alt(rep: dict, kind: str, allowed: tuple, probe=handshake) -> str | None:
    """Prefer measured headroom; an unknown quota is not exhaustion. Always require a live, tool-free reply.

    Unknown quotas never appear as a made-up percentage in the reminder. Known high readings disqualify a candidate.
    """
    import asyncio
    preferred = [c["harness"] for c in headroom(rep, kind)]
    candidates = preferred + [a["harness"] for a in rep["harnesses"]
                              if a["harness"] != kind and a["harness"] not in preferred and not alarmed(a)]
    for name in candidates:
        if name in allowed and await asyncio.to_thread(probe, name):
            return name
    return None


async def route(host, kind: str | None, task: dict | None, title: str, rep: dict | None = None,
                probe=handshake) -> tuple[str | None, str | None]:
    """(harness to run this task on, one sentence for the run's notice or None)."""
    import asyncio
    if kind not in HARNESSES or (callable(getattr(host, "stopped", None)) and host.stopped()):
        return kind, None
    allowed = allowed_harnesses(task)
    rep = rep if rep is not None else await asyncio.to_thread(report, host.st.root, None, None, None,
                                                              getattr(host, "meter_state", None))
    by = {a["harness"]: a for a in rep["harnesses"]}
    cur = by.get(kind)
    if not cur or not alarmed(cur):
        return kind, None
    lang = "en" if getattr(host, "lang", "zh") == "en" else "zh"
    alt = await pick_alt(rep, kind, allowed, probe)
    def original():
        if (cur.get("h5") or {}).get("used_pct", 0) >= H5_STOP_PCT:
            host.st.log("quota_switch", result="blocked", frm=kind)
            raise QuotaBlocked("5 小时额度已用 ≥90%，停止新起本账号无头任务，重置后再试 / 5-hour quota ≥90%; wait for reset or use another AI account")
        return kind, None

    if alt is None:
        host.st.log("quota_switch", result="none", frm=kind)
        return original()
    auto = auto_switch_on(getattr(host, "preferences", None))
    if not auto:
        ans = await host.ask(SWITCH_TOOL, {"summary": switch_text(cur, alt, title, lang)}, batch=False)
        if ans.get("behavior") != "allow":
            host.st.log("quota_switch", result="declined", frm=kind, to=alt)
            return original()
    host.st.log("quota_switch", result="auto" if auto else "allowed", frm=kind, to=alt)
    return alt, (f"(run on {NAMES[alt]}: {cur['name']} quota warning)" if lang == "en" else
                 f"（{cur['name']} 额度告警，这次改用 {NAMES[alt]} 运行）")


# ------------------------------------------------------------------ serve side (heartbeat.sock ops)
READ_OPS = ("quota", "quota_resume")
WRITE_OPS = ("quota_on", "quota_off", "quota_reset_add", "quota_reset_use", "quota_remind", "quota_resume_cancel")


async def handle(host, req: dict) -> dict:
    """quota ops over heartbeat.sock (the fenced Agent cannot read the state directory)."""
    return await _handle(host, req, host.st.root, getattr(host, "preferences", None) or {},
                         getattr(host, "meter_state", None))


async def _handle(host, req: dict, root, prefs: dict, meter_state: dict | None = None) -> dict:
    op = req.get("op")
    if op in ("quota_resume", "quota_resume_cancel"):     # P314: the waiting automatic continues (serve only)
        qr = getattr(host, "quota_resume", None)
        if qr is None:
            return {"ok": False, "why": "serve_not_running"}
        if op == "quota_resume_cancel":
            ok = qr.cancel(str(req.get("id") or ""), by="agent")
            return {"ok": ok, **({} if ok else {"why": "no_such_resume"}), "items": qr.view()}
        return {"ok": True, "items": qr.view()}
    if op == "quota":
        import asyncio
        rep = await asyncio.to_thread(report, root, meter_state=meter_state)
        rep["reminders_on"] = reminders_on(prefs)
        if host is not None and getattr(getattr(host, "agent", None), "kind", None) == "codex":
            row = next((a for a in rep["harnesses"] if a["harness"] == "codex"), {})
            host.meter_update(reset_credits=row.get("reset_credits"))
        return rep
    if op == "quota_remind":
        import asyncio
        rep = await asyncio.to_thread(report, root, meter_state=meter_state)
        rep["reminders_on"] = reminders_on(prefs)
        items = due(rep, root, mark=not req.get("peek")) if rep["reminders_on"] else []
        by = {a["harness"]: a for a in rep["harnesses"]}
        lang = "en" if req.get("lang") == "en" else "zh"
        rep["due"] = [{**i, "text": due_text(i, by, rep, lang)} for i in items]
        return rep
    if op in ("quota_on", "quota_off"):
        if host is None:
            return _offline_pref(op == "quota_on")
        res = await host.set_pref("quota.reminders", op == "quota_on")
        return {"ok": bool(res.get("ok")), **({} if res.get("ok") else {"why": res.get("error", "rejected")})}
    if op == "quota_reset_add":
        try:
            r = add_reset(root, str(req.get("harness")), parse_expires(req.get("expires")), req.get("note"))
        except (ValueError, OSError) as e:
            return {"ok": False, "why": str(e) if isinstance(e, ValueError) else type(e).__name__}
        if host is not None:
            host.st.log("quota_reset_add", harness=r["harness"])
        return {"ok": True, "reset": r}
    if op == "quota_reset_use":
        try:
            r = use_reset(root, str(req.get("harness")))
        except OSError as e:
            return {"ok": False, "why": type(e).__name__}
        if host is not None and r:
            host.st.log("quota_reset_use", harness=r["harness"])
        return {"ok": r is not None, **({"reset": r} if r else {"why": "no_recorded_reset"})}
    return {"ok": False, "why": "unknown_op"}


def _offline_pref(on: bool) -> dict:
    import io
    from . import preferences
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = preferences.command(["set", "quota.reminders", "true" if on else "false"])
    try:
        res = json.loads(out.getvalue().strip().splitlines()[-1])
    except (ValueError, IndexError):
        res = {"ok": code == 0}
    return {"ok": bool(res.get("ok")), "serve": False, **({} if res.get("ok") else {"why": res.get("error", "rejected")})}


# ------------------------------------------------------------------ CLI
def call(req: dict) -> dict:
    from . import heartbeat
    res = heartbeat.ask(req)
    if res is not None and res.get("why") != "unknown_op":
        return res
    import asyncio
    from . import preferences
    from .state import State
    try:
        p = preferences.effective()
    except Exception:  # noqa: BLE001
        p = {}
    res = asyncio.run(_handle(None, req, State().root, p))
    res.setdefault("serve", False)
    return res


def _lang() -> str:
    with contextlib.suppress(Exception):
        from . import preferences
        return "en" if preferences.get(preferences.effective(), "appearance.language", "zh") == "en" else "zh"
    return "zh"


WHY = {"no_statusline_tap": ("查不到：Claude 状态栏接入没开（agentj config claude-statusline on）", "unknown: status-line tap is off (agentj config claude-statusline on)"),
       "no_week_window": ("查不到：数据里没有 7 天窗口", "unknown: no 7-day window in the data"),
       "no_session_logs": ("查不到：本机没有 Codex 会话记录", "unknown: no Codex session logs here"),
       "no_rate_limits": ("查不到：Codex 会话记录里还没有额度数据", "unknown: no rate limits in the Codex logs yet"),
       "not_exposed": ("查不到：OpenCode 不在本地记录服务商额度", "unknown: OpenCode keeps no provider quota locally"),
       "window_rolled_over": ("查不到：上次读数之后已过重置时间，等下一次读数", "unknown: the window reset since the last reading"),
       "source_failed": ("查不到：数据源出错", "unknown: the source failed"),
       "since_switch": ("查不到：切换账号后还没有新读数", "unknown: no reading since the account switch"),
       "stale_reading": ("额度读数已过期：Claude 一直在用，状态栏读数却停了（agentj doctor 检查）",
                         "usage reading expired: Claude kept working but its status-line reading stopped (check agentj doctor)"),
       "no_reset_time": ("服务商没给重置时间，算不了均分进度", "the provider gives no reset time, so no pace")}


def render(rep: dict, lang: str = "zh") -> str:
    en = lang == "en"
    lines = []
    for a in rep["harnesses"]:
        if a.get("status") == "partial":
            zh_t, en_t = WHY.get(a.get("why"), ("", ""))
            lines.append(f"{a['name']}: " + (f"weekly {a['used_pct']:.0f}% used · {en_t}" if en else f"本周已用 {a['used_pct']:.0f}% · {zh_t}"))
        elif a.get("status") != "ok":
            zh_t, en_t = WHY.get(a.get("why"), ("查不到", "unknown"))
            old = time.strftime(" · %m-%d %H:%M", time.localtime(a["source_at"])) if a.get("stale") else ""
            lines.append(f"{a['name']}: " + (en_t if en else zh_t) + old)
        else:
            src = time.strftime("%m-%d %H:%M", time.localtime(a["source_at"]))
            lines.append(
                (f"{a['name']}: {a['used_pct']:.0f}% used · resets {_when(a['reset_at'], lang)} · day {a['elapsed_days']:.1f}/7 · "
                 f"even pace {a['even_pace_pct']:.0f}% (14.3%/day)" if en else
                 f"{a['name']}: 已用 {a['used_pct']:.0f}% · {_when(a['reset_at'], lang)} 重置 · 本周第 {a['elapsed_days']:.1f}/7 天 · "
                 f"均分进度 {a['even_pace_pct']:.0f}%（每天 14.3%）")
                + ((f" · projected unused at reset ≈{a['projected_end_unused_pct']:.0f}%" if en else
                    f" · 预测重置时剩 ≈{a['projected_end_unused_pct']:.0f}%") if "projected_end_unused_pct" in a else "")
                + (f" · reading {src}" if en else f" · 读数 {src}"))
            c = a.get("credits")
            if c and c.get("balance") is not None:
                lines.append(("  credits balance " if en else "  credits 余额 ") + f"{c['balance']:g}")
        h = a.get("h5")
        if h:
            lines.append((f"  5-hour window {h['used_pct']:.0f}% used · resets {_when(h['reset_at'], lang)}" if en else
                          f"  5 小时窗口已用 {h['used_pct']:.0f}% · {_when(h['reset_at'], lang)} 重置"))
        for r in a.get("resets", []):
            lines.append((f"  extra reset recorded, expires {_when(r['expires'], lang)}" if en else
                          f"  额外 reset（登记）：{_when(r['expires'], lang)} 过期") + (f" · {r['note']}" if r.get("note") else ""))
        for r in a.get("accounts", []):          # #427: one line per account; no reading = 「—」, never an estimate
            box = r.get("reset_credits")
            if box is not None:
                from .codex_reset_credits import summary
                lines.append(f"  · {r['name']}: " + summary(box, lang))
                for credit in box.get("credits", []):
                    exp = credit.get("expires_at")
                    lines.append(("    reset credit expires " + _when(exp, lang) if exp is not None else "    reset credit: no expiry") if en else
                                 ("    重置券到期：" + _when(exp, lang) if exp is not None else "    重置券：无到期时间"))
            q = r.get("quota")
            when = time.strftime("%m-%d %H:%M", time.localtime(q["at"])) if q else ""
            if r["active"]:
                val = (f"{q['pct']:.0f}% used ({when})" if en else f"已用 {q['pct']:.0f}%（{when}）") if q else "—"
                lines.append(f"  · {r['name']}" + (" (in use)" if en else "（当前）") + f": {val}")
            else:
                val = (f"last read {q['pct']:.0f}% ({when})" if en else f"上次读到 {q['pct']:.0f}%（{when}）") if q else "—"
                lines.append(f"  · {r['name']}: {val}")
        for k in a.get("reminders", []):
            lines.append("  ⚑ " + reminder_text(a, k, lang, headroom(rep, a["harness"])))
    if rep.get("reminders_on") is False:
        lines.append("(quota reminders are off: agentj quota on)" if en else "（额度提醒已关闭：agentj quota on 打开）")
    return "\n".join(lines)


def add_parser(sub) -> None:
    p = sub.add_parser("quota", help="额度管家：各 harness 周额度进度、均分线、reset 机会 · --json · --remind · on · off · "
                                     "reset add|used / weekly quota pace and reset opportunities per harness")
    p.add_argument("--json", action="store_true")
    p.add_argument("--task", choices=("content", "agentj"), default="", help="派单类型：内容创作 / Agent J 开发")
    choice = p.add_mutually_exclusive_group()
    choice.add_argument("--pick", action="store_true", help="按新鲜的两账号读数选派活账号 / pick from fresh measured quota")
    choice.add_argument("--remind", action="store_true",
                   help="只输出今天还没提醒过的提醒，并记为已提醒（晨报 / 例行用）/ only today's not-yet-given reminders, marked given")
    qs = p.add_subparsers(dest="q_cmd")
    qs.add_parser("on", help="打开额度提醒（默认开）/ reminders on (default)")
    qs.add_parser("off", help="关闭额度提醒 / reminders off")
    r = qs.add_parser("reset", help="登记额外 reset（各 harness 都查不到时由主人告诉你）/ record extra resets the harness does not expose")
    rs = r.add_subparsers(dest="q_reset", required=True)
    x = rs.add_parser("add", help="登记一次：agentj quota reset add claude --expires 2026-10-20 / record one")
    x.add_argument("harness", choices=HARNESSES)
    x.add_argument("--expires", required=True, help="YYYY-MM-DD 或 'YYYY-MM-DD HH:MM'（本地时间）/ local time")
    x.add_argument("--note", help="备注，不写凭据 / a note, never a credential")
    x = rs.add_parser("used", help="主人按掉了一次：去掉最早过期的那次 / the owner used one")
    x.add_argument("harness", choices=HARNESSES)
    r = qs.add_parser("resume", help="额度用完后等着自动继续的会话/工作流；cancel ID 取消 / waiting automatic continues; cancel ID")
    r.add_argument("cancel", nargs="?", choices=("cancel",))
    r.add_argument("id", nargs="?")
    p.set_defaults(fn=cmd_quota)


def cmd_quota(a) -> None:
    lang = _lang()
    c = getattr(a, "q_cmd", None)
    if getattr(a, "pick", False) and c:
        sys.exit("--pick cannot be combined with quota subcommands")
    if c in ("on", "off"):
        res = call({"op": "quota_" + c})
        if not res.get("ok"):
            sys.exit("没成功 / failed: " + str(res.get("why")))
        print(("额度提醒已打开 / quota reminders on") if c == "on" else "额度提醒已关闭 / quota reminders off")
        return
    if c == "reset":
        if a.q_reset == "add":
            res = call({"op": "quota_reset_add", "harness": a.harness, "expires": a.expires, "note": a.note})
            if not res.get("ok"):
                sys.exit("没成功 / failed: " + str(res.get("why")))
            print(f"记下了：{NAMES[a.harness]} 额外 reset，{_when(res['reset']['expires'], lang)} 过期 / recorded")
            return
        res = call({"op": "quota_reset_use", "harness": a.harness})
        if not res.get("ok"):
            sys.exit("没有登记过的 reset / no recorded reset")
        print(f"记下了：{NAMES[a.harness]} 用掉一次 reset / one reset used")
        return
    if c == "resume":
        res = call({"op": "quota_resume_cancel", "id": a.id} if a.cancel else {"op": "quota_resume"})
        if a.cancel and not res.get("ok"):
            sys.exit("没有这条自动继续 / no such automatic continue: " + str(res.get("why")))
        from .quota_resume import hhmm, RESUME_MARGIN
        rows = [f"{i['id']}  {NAMES.get(i['harness'], i['harness'])}"
                + (f" · {i['title']}" if i.get("title") else (" · 聊天 / chat" if i["scope"] == "main" else ""))
                + f" · {hhmm(i['at'] + (0 if i.get('to') else RESUME_MARGIN), lang)}" + (f" → {NAMES.get(i['to'], i['to'])}" if i.get("to") else "")
                for i in res.get("items", [])]
        print(("已取消 / cancelled\n" if a.cancel else "") + ("\n".join(rows) or
              ("没有等着自动继续的 / nothing waiting" if not a.cancel else "")))
        return
    if getattr(a, "pick", False):
        res = pick(call({"op": "quota"}), task=getattr(a, "task", ""))
        print(json.dumps(res, ensure_ascii=False) if a.json else render_pick(res, lang))
        if not res["harness"]:
            raise SystemExit(2)
        return
    if a.remind:
        res = call({"op": "quota_remind", "lang": lang})
        if a.json:
            print(json.dumps(res, ensure_ascii=False))
            return
        if not res.get("reminders_on", True):
            print("（额度提醒已关闭）" if lang == "zh" else "(quota reminders are off)")
            return
        print("\n".join(f"{TAG[lang]} {d['text']}" for d in res.get("due", [])) or
              ("今天没有新的额度提醒" if lang == "zh" else "no new quota reminders today"))
        return
    res = call({"op": "quota"})
    if a.json:
        print(json.dumps(res, ensure_ascii=False))
        return
    print(render(res, lang))


# P497: a recommendation only. Never switches a session or makes a paid probe.
PICK_MAX_AGE = 300


def pick(rep: dict, now: float | None = None, task: str = "") -> dict:
    """Recommend from weekly headroom per remaining day; stale readings stay labelled stale."""
    now = time.time() if now is None else now
    rows = []
    for r in rep.get("harnesses", []) if isinstance(rep, dict) else []:
        kind = r.get("harness")
        if kind not in ("claude", "codex"):
            continue
        native_rows = kind == "codex" and any((a.get("quota") or {}).get("source") == "app_server" for a in r.get("accounts", []))
        active_account = next((a for a in r.get("accounts", []) if a.get("active")), None)
        readings = [] if native_rows else [(r, active_account)]
        for account in r.get("accounts", []):
            q = account.get("quota") or {}
            live_week = r.get("status") in ("ok", "stale") and r.get("weekly_valid") is not False and _num(r.get("used_pct")) and _num(r.get("reset_at"))
            if q.get("window") == "week" and (native_rows or not account.get("active") or not live_week):
                readings.append(({"used_pct": q.get("pct"), "reset_at": q.get("reset_at"),
                                  "source_at": q.get("at"), "source": q.get("source") or {"claude": "statusline", "codex": "session_log"}[kind],
                                  "status": q.get("status", "stale"), **({"h5": q["h5"]} if q.get("h5") else {})}, account))
        for reading, account in readings:
            row = {"harness": kind, "eligible": False, "why": "missing_reading"}
            if account:
                row.update(account_id=account["id"], account_name=account["name"], switch_account=not account.get("active"))
            if account:
                from .codex_reset_credits import expiring
                live = expiring(account.get("reset_credits"), now)
                row["reset_credits"] = account.get("reset_credits")
                row["reset_credit_expires_at"] = live[0]["expires_at"] if live else None
            rows.append(row)
            used, reset, at = (reading.get(k) for k in ("used_pct", "reset_at", "source_at"))
            if reading.get("source") not in (("session_log", "app_server") if kind == "codex" else ("statusline",)) or reading.get("weekly_valid") is False or ("weekly_valid" not in reading and reading.get("reading_valid") is False) or not _num(used) or not 0 <= used <= 100:
                row["why"] = "invalid_usage"
                continue
            if not _num(at) or at > now or not _num(reset) or not now < reset <= now + WEEK_S:
                row["why"] = "missing_reset" if reset is None else "invalid_window"
                continue
            fresh = now - at <= PICK_MAX_AGE and reading.get("status") == "ok" and not reading.get("stale")
            h = reading.get("h5")
            h_used = h.get("used_pct") if isinstance(h, dict) else None
            h_fresh = isinstance(h, dict) and _num(h.get("source_at")) and 0 <= now-h["source_at"] <= PICK_MAX_AGE
            h_valid = reading.get("h5_valid") is not False and h_fresh and _num(h_used) and 0 <= h_used <= 100
            left = (reset-now) / DAY_S
            row.update(used_pct=used, source_at=at, fresh=fresh, per_day=(100-used)/left,
                       speed=quota_pace(used, reset, now, stale=not fresh or at < reset-WEEK_S)["speed"],
                       pace=quota_pace(used, reset, now, stale=not fresh or at < reset-WEEK_S),
                       h5_used_pct=h_used if h_valid else 0 if fresh and h is None and not reading.get("h5_present") else None,
                       h5_unlimited=fresh and h is None and not reading.get("h5_present"),
                       eligible=used < 100 and (not h_valid or h_used < H5_WARN_PCT),
                       why="available" if fresh else "last_weekly_reading")
    affinity = "codex" if task in ("content", "内容创作") else "claude" if task in ("agentj", "Agent J") else None
    candidates = sorted((r for r in rows if r["eligible"]),
                        key=lambda r: (not (r.get("reset_credit_expires_at") and r.get("fresh")),
                                       r.get("reset_credit_expires_at") if r.get("fresh") and r.get("reset_credit_expires_at") else float("inf"),
                                       affinity is not None and r["harness"] != affinity, -r["per_day"], r["harness"]))
    chosen = candidates[0] if candidates else None
    why = "reset_credit_expiring" if chosen and chosen.get("fresh") and chosen.get("reset_credit_expires_at") else "task_affinity" if chosen and affinity == chosen["harness"] else "daily_headroom" if chosen else "no_weekly_reading" if not any("per_day" in r for r in rows) else "both_quota_high"
    reason = (f"{chosen['harness']} 每天可用 {chosen['per_day']:.1f}%" + ("（最近一次周读数，已降级）" if not chosen['fresh'] else "") + ("，按任务类型优先" if why == "task_affinity" else "，重置券即将到期，优先使用" if why == "reset_credit_expiring" else "，剩余日均额度最多")) if chosen else "没有可用的周额度读数，请先读取账号额度"
    reason_en = (f"{chosen['harness']}: {chosen['per_day']:.1f}% available per day" + (" (degraded: last weekly reading)" if not chosen['fresh'] else "") + (", preferred for this task" if why == "task_affinity" else ", expiring reset credit takes priority" if why == "reset_credit_expiring" else ", largest daily headroom")) if chosen else "No usable weekly reading; read account quota first"
    return {"harness": chosen["harness"] if chosen else None, "account_id": chosen.get("account_id") if chosen else None,
            "switch_account": bool(chosen and chosen.get("switch_account")), "why": why, "reason": reason,
            "reason_en": reason_en, "max_age_s": PICK_MAX_AGE, "accounts": rows,
            "more_work": bool(chosen and chosen["per_day"] > DAY_SHARE and chosen["used_pct"] < 80),
            "codex_accounts": rep.get("codex_accounts", [])}


def render_pick(result: dict, lang="zh") -> str:
    lines = [f"{NAMES[r['harness']]}{(' · '+r['account_name']) if r.get('account_name') else ''}: {r['why']}" +
             (f" · daily headroom {r['per_day']:.1f}%" if "per_day" in r else "") for r in result["accounts"]]
    lines.append(result.get("reason_en", result["reason"]) if lang == "en" else result["reason"])
    if result.get("switch_account"):
        lines.append(("Switch account: " if lang == "en" else "建议切到账号：") + result["account_id"])
    if result.get("more_work"):
        lines.append("More work can use quota before reset" if lang == "en" else "可多派一些任务，避免额度到期浪费")
    return "\n".join(lines)
