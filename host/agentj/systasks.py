"""The computer's own scheduled tasks, read-only, for the phone's 「定时任务」 page (P207 §2, PROTOCOL §8 `systask_list`).

Agent J's own tasks live in tasks.py; this module only *lists* what the computer already schedules by itself, so the page
can show 「这台电脑上的其它定时任务」 next to them. Nothing here can create, change, start or delete anything.

Sources: Linux = systemd --user timers (`systemctl --user list-timers --all --output=json`, text fallback; Description and
state from `systemctl --user show` — never ExecStart) + `crontab -l`; macOS = ~/Library/LaunchAgents/*.plist with
StartCalendarInterval / StartInterval (+ `launchctl list` for loaded / last exit) + `crontab -l`; Windows =
`schtasks /query /fo csv /v`, the current user's tasks, \\Microsoft\\ excluded.

Privacy: a row carries only {src, id, name, when, next|next_s, last|last_s, state}. Command lines, arguments, environment
lines, working directories and "Task To Run" are never read into a row; a cron line becomes its schedule + the basename of its
first command word (no arguments). Bounded: ≤ MAX rows, short strings, each tool ≤ TIMEOUT s, no shell, a missing tool = no
rows. Cached TTL s (the page re-reads on every open). Runs in the host process only — nothing is exposed to the Agent.
"""
from __future__ import annotations

import csv
import io
import json
import os
import plistlib
import re
import shlex
import subprocess
import sys
import threading
import time

CAP = "systasks"
MAX = 200
TIMEOUT = 5
TTL = 60
STATES = ("active", "waiting", "failed", "disabled")
_NAME = 120
_lock = threading.Lock()
_cache: dict = {}


def _run(argv: list[str]) -> str | None:
    """stdout of one read-only tool, or None (missing, timed out, failed). Never a shell."""
    try:
        kw = {}
        if sys.platform == "win32":
            kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        p = subprocess.run(argv, capture_output=True, timeout=TIMEOUT, stdin=subprocess.DEVNULL, check=False, **kw)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if p.returncode != 0:
        return None
    raw = p.stdout[:4 * 1024 * 1024]
    for enc in (("oem", "utf-8") if sys.platform == "win32" else ("utf-8",)):
        try:
            return raw.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", "replace")


def _clip(s, n: int = _NAME) -> str:
    s = re.sub(r"[\x00-\x1f\x7f]+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[:n - 1] + "…"


def _row(src: str, ident: str, name: str, when: list, state: str, nxt=None, last=None, nxt_s=None, last_s=None) -> dict:
    r = {"src": src, "id": _clip(ident, 80), "name": _clip(name or ident), "when": when[:3],
         "state": state if state in STATES else "waiting"}
    for k, v in (("next", nxt), ("last", last)):
        if isinstance(v, int) and v > 0:
            r[k] = v
    for k, v in (("next_s", nxt_s), ("last_s", last_s)):
        if isinstance(v, str) and v.strip():
            r[k] = _clip(v, 40)
    return r


# ---------------------------------------------------------------- systemd --user timers
_SPAN = {"us": 1e-6, "ms": 1e-3, "s": 1, "sec": 1, "m": 60, "min": 60, "h": 3600, "hr": 3600, "d": 86400, "w": 604800,
         "month": 2629800, "y": 31557600}


def _span_secs(s: str) -> int | None:
    total, ok = 0.0, False
    for n, u in re.findall(r"(\d+(?:\.\d+)?)\s*([a-z]+)", s or ""):
        if u not in _SPAN:
            return None
        total += float(n) * _SPAN[u]
        ok = True
    return int(total) if ok and total >= 1 else None


def _show(run, units: list[str]) -> dict:
    """{unit: {prop: value}} from `systemctl --user show` — only names, descriptions, states and timer specs."""
    out: dict = {}
    for i in range(0, len(units), 50):
        txt = run(["systemctl", "--user", "show", "--no-pager", "-p",
                   "Id,Description,ActiveState,UnitFileState,Result,TimersCalendar,TimersMonotonic", "--", *units[i:i + 50]])
        if not txt:
            continue
        for block in txt.split("\n\n"):
            props: dict = {}
            for line in block.splitlines():
                k, sep, v = line.partition("=")
                if sep:
                    props.setdefault(k, []).append(v)
            if props.get("Id"):
                out[props["Id"][0]] = props
    return out


def _timer_when(props: dict) -> list:
    when = []
    for spec in props.get("TimersCalendar", []):
        m = re.search(r"OnCalendar=(.*?)\s*;", spec)
        if m:
            when.append({"k": "cal", "v": _clip(m.group(1), 80)})
    for spec in props.get("TimersMonotonic", []):
        m = re.search(r"(On\w+?)(?:USec|Sec)?=([^;]*?)\s*;", spec)
        if not m:
            continue
        secs = _span_secs(m.group(2))
        if secs is None:
            continue
        when.append({"k": "boot" if m.group(1) in ("OnBoot", "OnStartup") else "every", "v": secs})
    return when


def _list_timers_text(txt: str) -> list[dict]:
    """Fallback for systemd without --output=json: the unit + activated unit columns only (no times)."""
    out = []
    for line in txt.splitlines():
        m = re.search(r"(\S+\.timer)\s+(\S+)\s*$", line)
        if m:
            out.append({"unit": m.group(1), "activates": m.group(2)})
    return out


def scan_systemd(run=_run) -> list[dict]:
    txt = run(["systemctl", "--user", "list-timers", "--all", "--output=json", "--no-pager"])
    timers = None
    if txt:
        try:
            timers = json.loads(txt)
        except ValueError:
            timers = None
    if not isinstance(timers, list):
        txt = run(["systemctl", "--user", "list-timers", "--all", "--no-pager", "--no-legend"])
        timers = _list_timers_text(txt) if txt else []
    timers = [t for t in timers if isinstance(t, dict) and isinstance(t.get("unit"), str)][:MAX]
    units = []
    for t in timers:
        units.append(t["unit"])
        if isinstance(t.get("activates"), str) and t["activates"]:
            units.append(t["activates"])
    info = _show(run, units) if units else {}
    rows = []
    for t in timers:
        tp = info.get(t["unit"], {})
        sp = info.get(t.get("activates") or "", {})
        first = lambda p, k: (p.get(k) or [""])[0]
        name = first(sp, "Description") or first(tp, "Description") or t["unit"].removesuffix(".timer")
        if first(sp, "ActiveState") == "failed" or first(sp, "Result") not in ("", "success"):
            state = "failed"
        elif first(tp, "ActiveState") not in ("active", ""):
            state = "disabled"
        elif first(sp, "ActiveState") in ("active", "activating", "reloading"):
            state = "active"
        else:
            state = "waiting"
        us = lambda v: int(v // 1_000_000) if isinstance(v, int) and not isinstance(v, bool) and v > 0 else None
        rows.append(_row("systemd", t["unit"], name, _timer_when(tp), state, us(t.get("next")), us(t.get("last"))))
    return rows


# ---------------------------------------------------------------- crontab
_CRON_AT = ("@reboot", "@yearly", "@annually", "@monthly", "@weekly", "@daily", "@midnight", "@hourly")
_WRAPPERS = {"env", "nice", "nohup", "ionice", "time", "exec", "sudo", "command", "flock", "timeout", "chronic"}
_CRON_NAME = r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|sun|mon|tue|wed|thu|fri|sat"
_CRON_FIELD = re.compile(rf"(?i:[\d*?]|{_CRON_NAME})(?i:[\d*/,\-?LW#]|{_CRON_NAME})*")
_INTERP = {"sh", "bash", "zsh", "dash", "python", "python3", "node", "perl", "ruby", "php", "uv", "deno", "bun"}


def _cmd_name(cmd: str) -> str:
    """The basename of the first real command word of a cron command — never an argument, never an assignment."""
    cmd = re.split(r"(?<!\\)%", cmd, maxsplit=1)[0]                 # cron: an unescaped % starts stdin
    for seg in re.split(r"&&|\|\||;|\|", cmd):
        try:
            words = shlex.split(seg)
        except ValueError:                                 # unbalanced quotes: plain words
            words = seg.split()
        while words and ("=" in words[0] or words[0].strip("'\"") in _WRAPPERS or words[0].startswith("-") or words[0].isdigit()):
            words.pop(0)
        if not words or words[0] == "cd":
            continue
        base = lambda w: w.strip("'\"").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        first = base(words[0])
        if first in _INTERP:                               # `bash ~/bin/job.sh …` / `python3 -u x.py` → the script (no argument)
            for w in words[1:]:
                if w == "-" or (w.startswith("-") and not w.startswith("--") and set(w[1:]) & set("cme")):
                    break                                  # -c / -ec / -m / -e …: what follows is inline code, never shown
                if not w.startswith("-"):
                    if not re.search(r"[\s=;$`'\"]", w):    # a script path, not quoted inline code
                        first = base(w)
                    break
        base = first
        base = re.sub(r"[^\w.+@-]", "", base)[:60]
        if base:
            return base
    return "?"


def parse_crontab(txt: str) -> list[dict]:
    rows = []
    for line in (txt or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*=", s):    # an environment line: may hold a secret, never read further
            continue
        f = s.split(None, 1)
        if f[0].lower() in _CRON_AT:
            if len(f) < 2:
                continue
            sched, cmd = f[0].lower(), f[1]
        else:
            f = s.split(None, 5)
            if len(f) < 6 or not all(_CRON_FIELD.fullmatch(x) for x in f[:5]):
                continue
            sched, cmd = " ".join(f[:5]), f[5]
        name = _cmd_name(cmd)
        when = [{"k": "boot", "v": 0}] if sched == "@reboot" else [{"k": "cron", "v": sched}]
        rows.append(_row("cron", name, name, when, "waiting"))
        if len(rows) >= MAX:
            break
    return rows


def scan_cron(run=_run) -> list[dict]:
    return parse_crontab(run(["crontab", "-l"]) or "")


# ---------------------------------------------------------------- macOS LaunchAgents
def _cal_to_cron(d) -> str | None:
    if not isinstance(d, dict):
        return None
    out = []
    for k in ("Minute", "Hour", "Day", "Month", "Weekday"):
        v = d.get(k)
        if v is None:
            out.append("*")
        elif isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 60:
            out.append(str(v))
        else:
            return None
    return " ".join(out)


def _launchctl(txt: str) -> dict:
    """{label: (pid or None, last exit status)} from `launchctl list` (PID\\tStatus\\tLabel)."""
    out = {}
    for line in (txt or "").splitlines()[1:]:
        f = line.split("\t")
        if len(f) == 3:
            pid = f[0].strip()
            try:
                status = int(f[1].strip())
            except ValueError:
                status = 0
            out[f[2].strip()] = (pid if pid.isdigit() else None, status)
    return out


def scan_launchd(run=_run, home: str | None = None) -> list[dict]:
    d = os.path.join(home or os.path.expanduser("~"), "Library", "LaunchAgents")
    try:
        names = sorted(n for n in os.listdir(d) if n.endswith(".plist"))[:MAX * 2]
    except OSError:
        return []
    loaded = _launchctl(run(["launchctl", "list"]) or "")
    rows = []
    for n in names:
        p = os.path.join(d, n)
        try:
            if os.path.getsize(p) > 256 * 1024:
                continue
            with open(p, "rb") as fh:
                pl = plistlib.load(fh)
        except Exception:                                   # noqa: BLE001 — a broken plist is just not listed
            continue
        if not isinstance(pl, dict):
            continue
        cal, every = pl.get("StartCalendarInterval"), pl.get("StartInterval")
        when = []
        for c in (cal if isinstance(cal, list) else [cal] if cal is not None else [])[:3]:
            expr = _cal_to_cron(c)
            if expr:
                when.append({"k": "cron", "v": expr})
        if isinstance(every, int) and not isinstance(every, bool) and every > 0:
            when.append({"k": "every", "v": every})
        if not when:
            continue                                        # not a scheduled job (a KeepAlive daemon, a login item)
        label = pl.get("Label") if isinstance(pl.get("Label"), str) else n.removesuffix(".plist")
        if label in loaded:
            pid, status = loaded[label]
            state = "active" if pid else "failed" if status != 0 else "waiting"
        else:
            state = "disabled"
        if pl.get("Disabled") is True and label not in loaded:
            state = "disabled"
        rows.append(_row("launchd", label, label, when, state))
        if len(rows) >= MAX:
            break
    return rows


# ---------------------------------------------------------------- Windows Task Scheduler
_WIN_COLS = {"TaskName": 1, "Next Run Time": 2, "Status": 3, "Last Run Time": 5, "Last Result": 6, "Author": 7,
             "Comment": 10, "Scheduled Task State": 11, "Run As User": 14, "Schedule Type": 18, "Start Time": 19}


def parse_schtasks(txt: str, user: str | None = None) -> list[dict]:
    """`schtasks /query /fo csv /v` → rows. English headers by name, any other language by the fixed column order.
    "Task To Run" / "Start In" are never read."""
    rd = csv.reader(io.StringIO(txt or ""))
    header, idx = None, dict(_WIN_COLS)
    user = (user or "").lower()
    seen, rows = set(), []
    for rec in rd:
        if not rec or len(rec) < 12:
            continue
        if header is None or rec == header:
            if header is None:
                header = rec
                pos = {h.strip(): i for i, h in enumerate(rec)}
                if "TaskName" in pos:
                    idx = {k: pos.get(k, v) for k, v in _WIN_COLS.items()}
            continue
        col = lambda k: rec[idx[k]].strip() if idx[k] < len(rec) else ""
        full = col("TaskName")
        if not full or full.lower().startswith("\\microsoft\\") or full in seen:
            continue
        runas, author = col("Run As User").lower(), col("Author").lower()
        if user and runas and not (runas == user or runas.endswith("\\" + user) or author.endswith(user)):
            continue
        seen.add(full)
        leaf = full.rstrip("\\").rsplit("\\", 1)[-1]
        comment = col("Comment")
        name = comment if comment and comment.upper() != "N/A" and len(comment) <= 80 else leaf
        status, tstate, result = col("Status").lower(), col("Scheduled Task State").lower(), col("Last Result")
        if status == "disabled" or tstate == "disabled":
            state = "disabled"
        elif status == "running":
            state = "active"
        elif result not in ("", "0", "267011", "267009", "267008", "267010"):
            state = "failed"
        else:
            state = "waiting"
        when_txt = " ".join(x for x in (col("Schedule Type"), col("Start Time")) if x and x.upper() != "N/A")
        na = lambda s: None if not s or s.upper() == "N/A" or s.startswith("1999") or s.startswith("11/30/1999") else s
        rows.append(_row("schtasks", leaf, name, [{"k": "raw", "v": _clip(when_txt, 60)}] if when_txt else [], state,
                         nxt_s=na(col("Next Run Time")), last_s=na(col("Last Run Time"))))
        if len(rows) >= MAX:
            break
    return rows


def scan_schtasks(run=_run) -> list[dict]:
    return parse_schtasks(run(["schtasks", "/query", "/fo", "csv", "/v"]) or "", os.environ.get("USERNAME"))


# ---------------------------------------------------------------- entry point
def scan(platform: str | None = None, run=_run, home: str | None = None) -> list[dict]:
    """Every source of this OS, ≤ MAX rows; a source that fails contributes nothing."""
    platform = platform or sys.platform
    if platform.startswith("linux"):
        parts = (lambda: scan_systemd(run), lambda: scan_cron(run))
    elif platform == "darwin":
        parts = (lambda: scan_launchd(run, home), lambda: scan_cron(run))
    elif platform == "win32":
        parts = (lambda: scan_schtasks(run),)
    else:
        parts = (lambda: scan_cron(run),)
    rows: list[dict] = []
    for part in parts:
        try:
            rows.extend(part())
        except Exception:                                   # noqa: BLE001 — a surprise in one source never hides the others
            continue
    return rows[:MAX]


def rows(now: float | None = None) -> list[dict]:
    """scan(), cached TTL seconds (one scan at a time)."""
    now = time.monotonic() if now is None else now
    with _lock:
        hit = _cache.get("rows")
        if hit and now - hit[0] < TTL:
            return [dict(r) for r in hit[1]]
        out = scan()
        _cache["rows"] = (now, out)
        return [dict(r) for r in out]
