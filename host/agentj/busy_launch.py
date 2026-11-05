"""#450: launch evidence for outside background jobs (`agentj busy run` / `busy start`), <state>/busy/launch.json.

A job inherits its launching turn's trust only when kernel facts tie it to this host's own harness during a trusted turn
and no untrusted turn's process could have started it. Anything unknown stays foreign. Rules: ARCHITECTURE ADR-#450.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

MARGIN = 1.0      # s: trusted windows shrink by this, untrusted episodes grow by it (clock / tick precision)
CORE = 10.0       # s: the harness's own launch-time children (bwrap's inner process, a node shim's binary)
KEEP = 128
FIELDS = ("who", "label", "report")   # what a completion says comes from first sight, never a later item rewrite
HZ = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


def procs(root=Path("/proc")):
    """{pid: (ppid, sid, start_id, wall_start)} — Linux only; elsewhere {} (every outside job stays foreign)."""
    out = {}
    if not sys.platform.startswith("linux"):
        return out
    try:
        up = float((root / "uptime").read_text().split()[0])
        now = time.time()
        names = [d for d in os.listdir(root) if d.isdigit()]
    except (OSError, ValueError, IndexError):
        return out
    for d in names:
        try:
            s = (root / d / "stat").read_text().rsplit(")", 1)[1].split()
            if s[0] in ("Z", "X"):
                continue
            out[int(d)] = (int(s[1]), int(s[3]), s[19], now - (up - int(s[19]) / HZ))
        except (OSError, ValueError, IndexError):
            continue
    return out


def _cgroup(pid, root=Path("/proc")):
    try:
        return next((x[3:].strip() for x in (root / str(pid) / "cgroup").read_text().splitlines() if x.startswith("0::")), None)
    except OSError:
        return None


def sessions(contexts, table, turns=()):
    """(session ids, harness pids) of this host's own live harnesses: host-managed scopes only (a shared desktop session
    is never one); the harness process plus its launch-time children are the harness, not work a turn started."""
    out, core = set(), set()
    for scope, c in (contexts or {}).items():
        pid = c.get("pid")
        row = table.get(pid) if isinstance(pid, int) else None
        if str(scope).startswith("shared") or row is None or not c.get("proc_start") or row[2] != c["proc_start"]:
            continue
        first = min((t for t in turns if t >= row[3] - MARGIN), default=float("inf"))   # nothing a turn started is core
        kids = {p for p, r in table.items() if r[0] == pid and 0 <= r[3] - row[3] <= CORE and r[3] < first - MARGIN}
        core |= {pid} | kids
        out |= {row[1]} | {table[p][1] for p in kids}
    return out, core


class Launch:
    def __init__(self, root):
        self.path = Path(root) / "busy" / "launch.json"
        self.lock = threading.Lock()
        self.trusted, self.taint, self.jobs, self.open, self.dirty = [], [], {}, None, False
        try:
            d = json.loads(self.path.read_text())
            self.trusted = [[float(s), float(e)] for s, e in d.get("trusted", [])][-KEEP:]
            self.taint = [[float(s), None if e is None else float(e)] for s, e in d.get("taint", [])][-KEEP:]
            self.jobs = {str(k): ({f: str(v.get(f) or "")[:400] for f in FIELDS} if isinstance(v, dict) else False)
                         for k, v in d.get("jobs", {}).items()}
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    # ---------------------------------------------------------------- turn boundaries (event loop: memory only)
    def turn(self, trusted, now=None):
        now = time.time() if now is None else now
        with self.lock:
            self._close(now)
            self.open = [now, bool(trusted)]
            if not trusted:
                self.taint.append([now, None])     # closed by a scan that finds none of its processes alive
            self.dirty = True

    def end(self, now=None):
        with self.lock:
            self._close(time.time() if now is None else now)
            self.dirty = True

    def _close(self, now):
        if self.open and self.open[1]:
            self.trusted.append([self.open[0], now])
            self.trusted = self.trusted[-KEEP:]
        self.open = None

    # ---------------------------------------------------------------- scans (worker thread)
    def scan(self, contexts, table=None, cgroup=None, me=None):
        table = procs() if table is None else table
        sids, core = sessions(contexts, table, self.starts())
        cg = cgroup or _cgroup
        me = os.getpid() if me is None else me
        kids = {}
        for p, r in table.items():
            kids.setdefault(r[0], []).append(p)
        helpers, todo = {me}, [me]           # serve's own helpers (not started by a harness) never hold an episode open
        while todo:
            for k in kids.get(todo.pop(), ()):
                if k not in core and k not in helpers:
                    helpers.add(k)
                    todo.append(k)
        homes = {cg(p) for p in core} - {None}   # L1: a setsid / double-forked child keeps its harness cgroup

        def ours(p, r):     # in a harness cgroup and either below a harness process or orphaned (its parent elsewhere)
            if p in helpers or cg(p) not in homes:
                return False
            q, n = r[0], 0
            while q in table and q not in core and n < 64:
                q, n = table[q][0], n + 1
            return q in core or cg(r[0]) != cg(p)
        now = time.time()
        with self.lock:
            live_turn = self.open is not None and not self.open[1]
            for i, t in enumerate(self.taint):
                if t[1] is not None or (live_turn and i == len(self.taint) - 1):
                    continue
                if not any(r[3] >= t[0] - MARGIN and p not in core and (r[1] in sids or ours(p, r)) for p, r in table.items()):
                    t[1] = now
                    self.dirty = True
        return table, sids

    def judge(self, it, contexts, table=None, sids=None):
        """First sight of an outside item: remember whether it was launched by this harness in a trusted turn."""
        key = it.get("key")
        with self.lock:
            if not isinstance(key, str) or key in self.jobs:
                return
        if table is None:
            table, sids = self.scan(contexts)
        core = sessions(contexts, table, self.starts())[1]
        pid = it.get("pid")
        if it.get("ns_other") and isinstance(pid, int):
            from .busy_probe import Processes
            pid = Processes().namespace_pid(pid, it.get("source_ns"))
        row = table.get(pid) if isinstance(pid, int) else None
        ok = bool(row and it.get("proc_start") == row[2] and row[1] in (sids or ()) and pid not in core)
        now = time.time()
        with self.lock:
            # first sight while an untrusted turn or its processes may still write item files: never known (forgery)
            ok = ok and not (self.open and not self.open[1]) and all(e is not None and now > e + MARGIN for _, e in self.taint)
            if ok:
                t = row[3]
                spans = self.trusted + ([[self.open[0], float("inf")]] if self.open and self.open[1] else [])   # still open
                ok = (any(s + MARGIN <= t <= e - MARGIN for s, e in spans)
                      and not any(s - MARGIN <= t <= (now if e is None else e) + MARGIN for s, e in self.taint))
            self.jobs[key] = {f: str(it.get(f) or "")[:400] for f in FIELDS} if ok else False
            while len(self.jobs) > 4 * KEEP:
                self.jobs.pop(next(iter(self.jobs)))
            self.dirty = True

    def starts(self):
        with self.lock:
            return [w[0] for w in self.trusted + self.taint] + ([self.open[0]] if self.open else [])

    def ok(self, key) -> bool:
        with self.lock:
            return isinstance(self.jobs.get(key), dict)

    def fields(self, key) -> dict:
        """A known job's first-sight who / label / report (overrides a rewritten item or receipt); {} otherwise."""
        with self.lock:
            v = self.jobs.get(key)
            return {f: v[f] or None for f in FIELDS} if isinstance(v, dict) else {}

    def save(self):
        with self.lock:
            if not self.dirty:
                return
            old = time.time() - 48 * 3600
            self.trusted = [w for w in self.trusted if w[1] >= old]
            self.taint = [w for w in self.taint if w[1] is None or w[1] >= old]
            body = json.dumps({"trusted": self.trusted, "taint": self.taint, "jobs": self.jobs}).encode()
            self.dirty = False
        try:
            from .state import _write_private
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            _write_private(self.path, body)
        except OSError:
            pass
