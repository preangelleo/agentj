"""The local activity log (PROMPT-26 item 3; ADR-A52): what the phone's 「记录」 page shows, newest first.

approvals.log stays as it is (hashes only, signed). This is the human-readable companion: turn start / end, every permission
request (tool, danger categories, the summary the card showed), who decided it and how (phone allow / deny, timeout, batch),
stop / resume, memory deletions, scheduled task runs with their VERDICT line. It may hold summary text — it lives only on the
customer's own computer, in the state directory the fenced Agent cannot see, and reaches a phone only inside the end-to-end
Noise session, page by page. Nothing of it is sent to our servers.

Layout: `<state>/activity/YYYY-MM-DD.jsonl` (dir 0700, files 0600), one JSON object per line. Kept 30 days, at most 20 MiB in
total (oldest days go first), one day at most 8 MiB (then a single `truncated` line and nothing more that day). Off switch:
`agentj config activity off` (config.json `activity: false`) — nothing more is written; `agentj activity --clear` deletes it.
"""
from __future__ import annotations

import json
import os
import re
import time

KEEP_DAYS = 30
MAX_TOTAL = 20 * 1024 * 1024
MAX_DAY = 8 * 1024 * 1024
PAGE = 50
TEXT_MAX = 300
_DAY = re.compile(r"(\d{4}-\d{2}-\d{2})\.jsonl")
KINDS = ("turn_start", "turn_end", "ask", "decision", "auto", "grant", "grant_end", "estop", "resume", "message_refused",
         "mem_rm", "mem_undo", "task_on", "task_off", "task_run", "task_done", "control_refused", "activity_on", "truncated",
         "slash", "shared", "auto_update")
_last_purge = [0.0]


def enabled(st) -> bool:
    try:
        return st.config().get("activity", True) is not False
    except (OSError, ValueError):
        return False


def set_enabled(st, on: bool) -> None:
    with st.config_lock():
        cfg = st.config()
        cfg["activity"] = bool(on)
        st.write_private(st.config_path, json.dumps(cfg, indent=1, ensure_ascii=False).encode())


def _dir(st):
    return st.root / "activity"


def _short(v, n: int = TEXT_MAX):
    if isinstance(v, str):
        v = "".join(ch if ch == "\n" or ch >= " " else " " for ch in v)
        return v if len(v) <= n else v[: n - 1] + "…"
    return v


def record(st, kind: str, **fields) -> dict | None:
    """Append one entry (or nothing when switched off). Never raises: a full disk must not stop serve."""
    if kind not in KINDS or not enabled(st):
        return None
    now = time.time()
    rec = {"ts": int(now * 1000), "k": kind, **{k: _short(v) for k, v in fields.items() if v is not None}}
    try:
        d = _dir(st)
        d.mkdir(mode=0o700, exist_ok=True)
        os.chmod(d, 0o700)
        path = d / (time.strftime("%Y-%m-%d", time.localtime(now)) + ".jsonl")
        line = (json.dumps(rec, ensure_ascii=False) + "\n").encode()
        size = path.stat().st_size if path.exists() else 0
        if size + len(line) > MAX_DAY:
            if size < MAX_DAY:             # one marker (padded to the cap), then silence for the rest of the day
                m = json.dumps({"ts": rec["ts"], "k": "truncated"}).encode()
                marker = m + b" " * max(0, MAX_DAY - size - len(m) - 1) + b"\n"
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
                try:
                    os.write(fd, marker)
                finally:
                    os.close(fd)
            return None
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
        if now - _last_purge[0] > 3600:
            purge(st)
    except OSError:
        return None
    return rec


def days(st) -> list[str]:
    """Day files, newest first."""
    try:
        names = os.listdir(_dir(st))
    except OSError:
        return []
    return sorted((n for n in names if _DAY.fullmatch(n)), reverse=True)


def purge(st, now: float | None = None) -> int:
    """Delete days older than KEEP_DAYS, then the oldest days until the total is ≤ MAX_TOTAL. Returns files removed."""
    _last_purge[0] = time.time() if now is None else now
    cutoff = time.strftime("%Y-%m-%d", time.localtime((time.time() if now is None else now) - KEEP_DAYS * 86400))
    removed = 0
    ds = days(st)
    for n in ds:
        if n[:10] < cutoff:
            try:
                os.unlink(_dir(st) / n)
                removed += 1
            except OSError:
                pass
    ds = days(st)
    sizes = {}
    for n in ds:
        try:
            sizes[n] = (_dir(st) / n).stat().st_size
        except OSError:
            sizes[n] = 0
    total = sum(sizes.values())
    for n in reversed(ds[1:]):              # never the newest day
        if total <= MAX_TOTAL:
            break
        try:
            os.unlink(_dir(st) / n)
            total -= sizes[n]
            removed += 1
        except OSError:
            pass
    return removed


def clear(st) -> int:
    n = 0
    for name in days(st):
        try:
            os.unlink(_dir(st) / name)
            n += 1
        except OSError:
            pass
    return n


def _read_day(st, name: str) -> list[dict]:
    try:
        fd = os.open(_dir(st) / name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return []
    try:
        with os.fdopen(fd, "rb") as f:
            raw = f.read(MAX_DAY + 4096)
    except OSError:
        return []
    out = []
    for ln in raw.decode("utf-8", "replace").splitlines():
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


def page(st, before: str | None = None, n: int = PAGE, since_ms: int | None = None) -> tuple[list[dict], str | None]:
    """≤ n entries strictly older than the cursor `before` ("YYYY-MM-DD:<line index>"), newest first, each with its own
    cursor `c`; returns (items, next cursor or None)."""
    n = max(1, min(int(n), PAGE))
    bday, bidx = None, None
    if isinstance(before, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}:\d{1,7}", before):
        bday, bidx = before.split(":")[0], int(before.split(":")[1])
    out: list[dict] = []
    for name in days(st):
        day = name[:10]
        if bday and day > bday:
            continue
        rows = _read_day(st, name)
        idxs = range(len(rows) - 1, -1, -1)
        for i in idxs:
            if bday == day and i >= bidx:
                continue
            r = rows[i]
            if since_ms is not None and isinstance(r.get("ts"), int) and r["ts"] < since_ms:
                return out, None
            out.append({**r, "c": f"{day}:{i}"})
            if len(out) >= n:
                return out, out[-1]["c"]
    return out, None


def all_since(st, since_ms: int | None = None, limit: int = 100_000) -> list[dict]:
    """Oldest first (for `agentj activity`)."""
    items, cur = [], None
    while len(items) < limit:
        got, cur = page(st, cur, PAGE, since_ms)
        items += got
        if not cur:
            break
    return list(reversed(items))


def size(st) -> int:
    t = 0
    for n in days(st):
        try:
            t += (_dir(st) / n).stat().st_size
        except OSError:
            pass
    return t
