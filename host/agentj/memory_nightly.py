"""One idle memory-maintenance heartbeat per owner's local calendar night."""
import datetime as dt
import json
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import auto_update, memory, memory_tidy, phone_update, service_recovery
from .preference_values import get

RECORD = "memory-nightly.json"


COUNTS = ("merged", "archived", "conflicts", "index_fixed", "guarded")


def _counts(values):
    return (isinstance(values, dict) and set(values) <= set(COUNTS)
            and all(type(v) is int and 0 <= v <= 10_000_000 for v in values.values()))


def _load(st):
    try:
        raw = memory.read_file(st.root / RECORD, limit=4096, private=True)
    except memory.MemoryError_ as exc:
        if exc.reason == "not_found":
            return {}, None
        raise
    r = json.loads(raw)
    if not isinstance(r, dict) or set(r) - {"day", "opened", "status", "reason", "partial", *COUNTS}:
        raise ValueError("invalid nightly record")
    for k in ("day", "opened"):
        if k in r and (not isinstance(r[k], str) or dt.date.fromisoformat(r[k]).isoformat() != r[k]):
            raise ValueError("invalid nightly date")
    if (r.get("status") not in (None, "done", "skipped") or r.get("reason") not in (None, "no_idle_heartbeat")
            or not _counts({k: r[k] for k in COUNTS if k in r})
            or not _counts(r.get("partial", {}))):
        raise ValueError("invalid nightly state")
    return r, memory.sha(raw)


def read(st):
    try:
        return _load(st)[0]
    except (memory.MemoryError_, OSError, ValueError, RecursionError):
        return {}


def _save(st, record):
    try:
        _, expected = _load(st)
        memory._write_atomic(st.root / RECORD, json.dumps(record).encode(), expected, create_mode=0o600)
        return True
    except (memory.MemoryError_, OSError, ValueError, RecursionError):
        return False


def window(prefs, now):
    tz = get(prefs, "heartbeat.memory_tidy_timezone", "")
    try:
        local = dt.datetime.fromtimestamp(now, ZoneInfo(tz)) if tz else dt.datetime.fromtimestamp(now).astimezone()
        start = dt.time.fromisoformat(get(prefs, "heartbeat.memory_tidy_start", "03:00"))
        end = dt.time.fromisoformat(get(prefs, "heartbeat.memory_tidy_end", "05:00"))
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        return None
    if start == end:
        return None
    clock, day = local.time(), local.date()
    if start < end:
        inside = start <= clock < end
        if clock < start:
            day -= dt.timedelta(days=1)
    else:
        inside = clock >= start or clock < end
        if clock < start:
            day -= dt.timedelta(days=1)
    return day.isoformat(), inside


def observe(host, now, *, idle):
    st = host.st
    prefs = getattr(host, "preferences", {}) or {}
    if not get(prefs, "heartbeat.memory_tidy_enabled", True):
        return None
    slot = window(prefs, now)
    if not slot:
        return None
    day, inside = slot
    if not inside and not (st.root / RECORD).exists():
        return None
    with st.file_lock("memory-nightly"):
        try:
            record, _ = _load(st)
        except (memory.MemoryError_, OSError, ValueError, RecursionError):
            return None
        opened = record.get("opened")
        if opened and (opened != day or not inside):
            record.pop("opened")
            record.pop("partial", None)
            record.update(day=opened, status="skipped", reason="no_idle_heartbeat",
                          merged=0, archived=0, conflicts=0, index_fixed=0)
            if not _save(st, record):
                return None
            st.log("memory_tidy_skipped", day=opened, reason="no_idle_heartbeat")
        if not inside or record.get("day") == day:
            return None
        config = st.agent_config() or {}
        if config.get("kind") not in memory.HARNESSES or not config.get("dir"):
            return None
        if record.get("opened") != day:
            record["opened"] = day
            if not _save(st, record):
                return None
        if not idle or phone_update.busy(st):
            return None
        upgrade = auto_update.read(st, auto_update.JOB)
        if upgrade and upgrade.get("phase") != "done":
            return None
        recovery = service_recovery.read(st) or {}
        if recovery.get("status") in ("pending", "waiting", "running"):
            return None
        heartbeat = getattr(host, "heartbeat", None)
        tick_start = time.monotonic()
        def still_idle():
            observed = now + time.monotonic() - tick_start
            board = getattr(host, "busy", None)
            if board is not None and hasattr(board, "items") and board.items():
                return False
            current = getattr(host, "preferences", {}) or {}
            return (get(current, "heartbeat.enabled", True)
                    and get(current, "heartbeat.memory_tidy_enabled", True)
                    and window(current, observed) == slot
                    and (not heartbeat or heartbeat.wait_reason(observed, include_night=False) is None))
        result = memory_tidy.run(st, config["kind"], config["dir"], still_idle=still_idle)
        if result.get("deferred"):
            # Keep this night's counts if an owner turn interrupts maintenance between two source files.
            carried = record.setdefault("partial", {})
            for key in COUNTS:
                carried[key] = carried.get(key, 0) + result[key]
            _save(st, record)
            return None
        outcome = {key: result[key] + record.get("partial", {}).get(key, 0) for key in COUNTS}
        if not _save(st, {"day": day, "status": "done", **outcome}):
            return None
        st.log("memory_tidy_done", day=day, **outcome)
        return {"ok": True, "reason": "memory_tidy", "day": day, **outcome}


def morning_line(st, lang, now):
    """Only yesterday/today's completed record can enter a new morning brief."""
    rec = read(st)
    try:
        day = dt.date.fromisoformat(rec.get("day", ""))
    except (TypeError, ValueError):
        return ""
    # Compare to UTC within a timezone-wide margin for owners on the opposite side of the date line.
    if abs((dt.datetime.fromtimestamp(now, dt.timezone.utc).date() - day).days) > 1:
        return ""
    if rec.get("status") == "skipped":
        return (f"Memory · {day}: no idle heartbeat during the window." if lang == "en"
                else f"记忆整理 {day}：窗口内没有空闲心跳，已跳过。")
    if rec.get("status") != "done":
        return ""
    merged, archived, conflicts = (rec.get(k, 0) for k in ("merged", "archived", "conflicts"))
    return (f"Memory · {day}: {merged} merged, {archived} archived, {conflicts} conflicts." if lang == "en"
            else f"记忆整理 {day}：合并 {merged}、归档 {archived}、冲突 {conflicts}。")
