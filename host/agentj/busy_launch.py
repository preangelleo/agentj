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
LAUNCH_SKEW = 10.0   # s: `agentj busy run` writes its item right after it starts (hotfix #455 S1)
CORE = 10.0       # s: the harness's own launch-time children (bwrap's inner process, a node shim's binary)
KEEP = 128
FIELDS = ("who", "label", "report")   # what a completion says comes from first sight, never a later item rewrite
CHAIN_TTL = 24 * 3600   # 0269-w3-policy E2: the owner's words that opened a task authorise its follow-ups this long
CHAIN_WORDS = 300
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


def restart_snapshot(it, pid, started):
    """Freeze matching metadata at verified first sight; cgroup/start come from the host kernel."""
    from .busy import restart_meta
    meta = restart_meta(it)
    meta.pop("stop_reason", None)
    meta.pop("stop_signal", None)
    meta.pop("unit", None)
    cg = _cgroup(pid)
    if isinstance(cg, str):
        from . import service
        excluded = {"agentj.service", service.LEGACY_UNIT + ".service", service.name() + ".service"}
        unit = next((p for p in reversed(cg.split("/")) if p.endswith(".service")), None)
        if unit and unit not in excluded:
            meta["unit"] = unit
    meta["started"] = started
    return restart_meta(meta)


class Launch:
    def __init__(self, root):
        self.path = Path(root) / "busy" / "launch.json"
        self.lock = threading.Lock()
        try:
            self.boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        except OSError:
            self.boot = None
        self.trusted, self.taint, self.jobs, self.open, self.dirty = [], [], {}, None, False
        self.owner, self.chains = {}, {}       # E2: trusted-window start → chain id; chain id → [at, the owner's words]
        try:
            d = json.loads(self.path.read_text())
            self.trusted = [[float(s), float(e)] for s, e in d.get("trusted", [])][-KEEP:]
            self.taint = [[float(s), None if e is None else float(e)] for s, e in d.get("taint", [])][-KEEP:]
            self.jobs = {str(k): ({f: str(v.get(f) or "")[:400] for f in FIELDS} if isinstance(v, dict) else False)
                         for k, v in d.get("jobs", {}).items()}
            from .busy import restart_meta
            for k, v in (d.get("jobs") or {}).items():
                if isinstance(v, dict) and isinstance(self.jobs.get(str(k)), dict):
                    self.jobs[str(k)].update({f: x for f, x in restart_meta(v).items() if f not in ("stop_reason", "stop_signal")})
            for k, v in (d.get("jobs") or {}).items():
                if isinstance(v, dict) and isinstance(v.get("chain"), str) and isinstance(self.jobs.get(str(k)), dict):
                    self.jobs[str(k)]["chain"] = v["chain"][:32]
            for k, v in (d.get("jobs") or {}).items():
                if (self.boot and d.get("boot") == self.boot and isinstance(v, dict) and type(v.get("pid")) is int and v["pid"] > 1
                        and isinstance(v.get("proc_start"), str) and v["proc_start"].isdigit()):
                    self.jobs[str(k)].update(pid=v["pid"], proc_start=v["proc_start"])
            self.owner = {str(k): str(v)[:32] for k, v in (d.get("owner") or {}).items()}
            self.chains = {str(k): [float(v[0]), str(v[1])[:CHAIN_WORDS]] for k, v in (d.get("chains") or {}).items()}
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    # ---------------------------------------------------------------- turn boundaries (event loop: memory only)
    def turn(self, trusted, now=None, words=None):
        """`words`: the owner's own message when this is an owner turn (E2) — it opens an owner chain."""
        now = time.time() if now is None else now
        cid = None
        if trusted and isinstance(words, str) and words.strip():
            import secrets
            cid = secrets.token_hex(8)
        with self.lock:
            self._close(now)
            self.open = [now, bool(trusted), cid]
            if cid:
                self.chains[cid] = [now, words.strip()[:CHAIN_WORDS]]
            if not trusted:
                self.taint.append([now, None])     # closed by a scan that finds none of its processes alive
            self.dirty = True

    def end(self, now=None):
        with self.lock:
            self._close(time.time() if now is None else now)
            self.dirty = True

    def _close(self, now):
        if self.open and self.open[1]:
            if len(self.open) > 2 and self.open[2]:
                self.owner["%.6f" % self.open[0]] = self.open[2]
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

    def known_process(self, pid, table=None):
        """Only a live kernel ancestor pinned by the host proves a descendant; PID reuse fails."""
        table = procs() if table is None else table
        with self.lock:
            pins = {(v.get("pid"), v.get("proc_start")) for v in self.jobs.values() if isinstance(v, dict)}
        seen = set()
        while type(pid) is int and pid > 1 and pid not in seen and len(seen) < 64:
            seen.add(pid)
            row = table.get(pid)
            if row is None:
                return False
            if (pid, row[2]) in pins:
                return True
            pid = row[0]
        return False

    def accept_verified(self, it, table=None):
        """Host-only caller proof. Freeze wrapper identity and completion fields before it runs."""
        table = procs() if table is None else table
        pid, key = it.get("pid"), it.get("key")
        row = table.get(pid) if type(pid) is int else None
        if row is None or row[2] != it.get("proc_start") or not isinstance(key, str):
            return False
        since = it.get("_ctime")
        if not isinstance(since, (int, float)) or abs(row[3] - since) > LAUNCH_SKEW:
            return False
        with self.lock:
            if not isinstance(self.jobs.get(key), dict):
                self.jobs[key] = {f: str(it.get(f) or "")[:400] for f in FIELDS} | {"pid": pid, "proc_start": row[2]} | restart_snapshot(it, pid, row[3])
                self.dirty = True
            self.jobs[key].update(pid=pid, proc_start=row[2])
            self.dirty = True
            while len(self.jobs) > 4 * KEEP:
                self.jobs.pop(next(iter(self.jobs)))
            return isinstance(self.jobs.get(key), dict)

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
        alive = bool(row and it.get("proc_start") == row[2])
        ok = alive and row[1] in (sids or ()) and pid not in core
        now = time.time()
        with self.lock:
            # first sight while an untrusted turn or its processes may still write item files: never known (forgery)
            clean = alive and not (self.open and not self.open[1]) and all(e is not None and now > e + MARGIN for _, e in self.taint)
            chain = None
            ok = ok and clean
            if clean:
                t = row[3]
                spans = self.trusted + ([[self.open[0], float("inf")]] if self.open and self.open[1] else [])   # still open
                hit = [s for s, e in spans if s + MARGIN <= t <= e - MARGIN]
                clean = not any(s - MARGIN <= t <= (now if e is None else e) + MARGIN for s, e in self.taint)
                ok = ok and bool(hit) and clean
                # hotfix #455 (security r1 S1): the same-computer path needs the job's own fresh process — never a harness
                # core process or an old long-lived one an item merely names (it started when the item says, ± LAUNCH_SKEW),
                # and no ancestor of it was started during an untrusted episode
                since = it.get("_ctime")     # R2-S1: the item file's kernel ctime, not the item's own `since`
                clean = (clean and pid not in core and pid != os.getpid() and isinstance(since, (int, float)) and abs(t - since) <= LAUNCH_SKEW
                         and not self._tainted_ancestor(pid, table, core, now))
                # hotfix #455: a job the owner's own processes started on this computer (a shared desktop session, a
                # dispatched CEO unit, the terminal) outside every untrusted episode is the same-computer tier — its
                # completion is a host system notice. Only a trusted-window launch carries the owner chain (E2).
                if ok:
                    s0 = hit[-1]
                    chain = (self.open[2] if self.open and len(self.open) > 2 and self.open[0] == s0 and self.open[1]
                             else self.owner.get("%.6f" % s0))
            self.jobs[key] = ({f: str(it.get(f) or "")[:400] for f in FIELDS} | ({"chain": chain} if chain else {})) if ok or clean else False
            if isinstance(self.jobs[key], dict):
                self.jobs[key].update(restart_snapshot(it, pid, row[3]))
            while len(self.jobs) > 4 * KEEP:
                self.jobs.pop(next(iter(self.jobs)))
            self.dirty = True

    def _tainted_ancestor(self, pid, table, core, now) -> bool:   # caller holds the lock
        q, n = table.get(pid, (None,))[0], 0
        while q in table and q not in core and n < 64:
            st = table[q][3]
            if any(s - MARGIN <= st <= (now if e is None else e) + MARGIN for s, e in self.taint):
                return True
            q, n = table[q][0], n + 1
        return False

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
            from .busy import restart_meta
            return ({f: v[f] or None for f in FIELDS} | {f: x for f, x in restart_meta(v).items() if f not in ("stop_reason", "stop_signal")}
                    | ({"chain": v["chain"]} if v.get("chain") else {})) if isinstance(v, dict) else {}

    def current_chain(self):
        """E2: the owner chain of the turn running now (None outside an owner turn)."""
        with self.lock:
            return self.open[2] if self.open and len(self.open) > 2 and self.open[1] else None

    def chain(self, cid, now=None):
        """E2: (opened at, the owner's words) of a live chain, else None."""
        now = time.time() if now is None else now
        with self.lock:
            v = self.chains.get(cid) if isinstance(cid, str) else None
            return (v[0], v[1]) if v and 0 <= now - v[0] <= CHAIN_TTL else None

    def save(self):
        with self.lock:
            if not self.dirty:
                return
            old = time.time() - 48 * 3600
            self.trusted = [w for w in self.trusted if w[1] >= old]
            self.taint = [w for w in self.taint if w[1] is None or w[1] >= old]
            live = time.time() - CHAIN_TTL
            self.chains = {k: v for k, v in self.chains.items() if v[0] >= live}
            self.owner = {k: v for k, v in self.owner.items() if v in self.chains}
            body = json.dumps({"boot": self.boot, "trusted": self.trusted, "taint": self.taint, "jobs": self.jobs, "owner": self.owner,
                               "chains": self.chains}, ensure_ascii=False).encode()
            self.dirty = False
        try:
            from .state import _write_private
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            _write_private(self.path, body)
        except OSError:
            pass
