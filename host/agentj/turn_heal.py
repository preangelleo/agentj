"""0.26.9 (#461, ADR-0269-watchdog): a stuck turn heals itself; /clear /new /reset never queue behind it.

Stuck (`judge`, pure) — never while a card waits for the owner or a tool child of that turn is alive (a long build is
not stuck: `turn_children` = live descendants of the turn's harness started after the turn began, its boot helpers
excluded), else one of
  no_event  the harness sent no event for STUCK_IDLE s (5 min = 2.5 x Codex's TAIL_QUIET; P219 told the owner at 45 s);
  loop      the shared-Codex wait logged ≥ LOOP_HITS 「desktop turn active / did not finish」 in LOOP_WINDOW and is quiet ≥ QUIET;
  queued    an owner message has waited ≥ QUEUE_LIMIT behind the turn and it is quiet ≥ QUIET.
Shared Codex waiting behind a desktop turn: the event clock is the rollout's mtime and the roots are the processes holding
the rollout (the desktop / daemon), never stopped — the open turn is treated as ended (`release_stuck`) and the waiting
message resumes the same thread.

Heal (`TurnHeal.heal`): ① interrupt (the adapter's own path: Codex `turn/interrupt`, Claude control interrupt, OpenCode
abort, shared sessions their halt), ② after ESCALATE s still busy: end_turn → that turn's tool children; watchdog / takeover →
the harness process Agent J started (`may_stop`: our own child, or a recorded app-server with the same start time, or a
descendant of serve — never by pattern, never someone else's). The next turn relaunches it with Agent J's own settings
and resumes the stored conversation; the queue is kept. ③ proof: the queued owner message reached the harness
(`delivered`) in the same conversation id → 「刚才卡住了，已自动恢复。」, else 「…没成功」. Event `turn_selfheal` (metadata).
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import os
import signal
import subprocess
import sys
import time

STUCK_IDLE = 300
QUIET = 120
QUEUE_LIMIT = 600
LOOP_HITS = 3
LOOP_WINDOW = 1800
BOOT_GRACE = 10          # s: children a freshly started harness spawns itself (MCP servers) are not tool children
ESCALATE = 10
PROOF_WAIT = 120
TICK = 15
COOLDOWN = 600           # s between two automatic heals
DEAD_IDLE = 600          # 0.26.10 #484: a desktop turn whose rollout is quiet this long (no tool child, no pending approval)
                         # is dead even when the owner's daemon still reports it active (Avon: CLI/daemon version mismatch)
ACTIONS = ("end_turn", "takeover")


# ------------------------------------------------------------------ processes
def proc_rows() -> dict:
    """{pid: (ppid, started epoch | None)} of this user's processes; {} when unreadable."""
    out = {}
    if sys.platform.startswith("linux") and os.path.isdir("/proc/self"):
        try:
            with open("/proc/stat") as f:
                btime = next(int(x.split()[1]) for x in f if x.startswith("btime"))
            tck = os.sysconf("SC_CLK_TCK")
        except (OSError, StopIteration, ValueError):
            btime, tck = None, 100
        me = os.getuid()
        for d in os.listdir("/proc"):
            if not d.isdigit():
                continue
            with contextlib.suppress(OSError, ValueError, IndexError):
                if os.stat(f"/proc/{d}").st_uid != me:
                    continue
                with open(f"/proc/{d}/stat", "rb") as fh:
                    s = fh.read().decode("utf-8", "replace")
                w = s[s.rindex(")") + 2:].split()
                out[int(d)] = (int(w[1]), btime + int(w[19]) / tck if btime else None)
        return out
    try:
        r = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,etime="], capture_output=True, text=True, timeout=5,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return out
    now = time.time()
    for ln in r.stdout.splitlines():
        w = ln.split()
        if len(w) == 3 and w[0].isdigit() and w[1].isdigit():
            out[int(w[0])] = (int(w[1]), now - _etime(w[2]) if _etime(w[2]) is not None else None)
    return out


def _etime(v: str):
    try:
        days, _, rest = v.rpartition("-")
        parts = [int(x) for x in rest.split(":")]
        while len(parts) < 3:
            parts.insert(0, 0)
        return int(days or 0) * 86400 + parts[0] * 3600 + parts[1] * 60 + parts[2]
    except ValueError:
        return None


def proc_row(pid: int):
    """(ppid, started epoch | None) of one process, read now; None = gone / unreadable (proc_rows' format)."""
    if sys.platform.startswith("linux") and os.path.isdir("/proc/self"):
        try:
            with open("/proc/stat") as f:
                btime = next(int(x.split()[1]) for x in f if x.startswith("btime"))
            with open(f"/proc/{int(pid)}/stat", "rb") as fh:
                s = fh.read().decode("utf-8", "replace")
            w = s[s.rindex(")") + 2:].split()
            return int(w[1]), btime + int(w[19]) / os.sysconf("SC_CLK_TCK")
        except (OSError, StopIteration, ValueError, IndexError):
            return None
    try:
        r = subprocess.run(["ps", "-o", "ppid=,etime=", "-p", str(int(pid))], capture_output=True, text=True, timeout=5,
                           stdin=subprocess.DEVNULL)
        w = r.stdout.split()
        e = _etime(w[1]) if len(w) == 2 and w[0].isdigit() else None
        return (int(w[0]), time.time() - e if e is not None else None) if len(w) == 2 and w[0].isdigit() else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def signal_same(pid: int, row, sig) -> bool:
    """w2-sec L-1: signal `pid` only while it is still the process of the snapshot `row` (same parent, same start time,
    re-read just before the signal). Linux: through a pidfd opened BEFORE the re-read, so the number cannot be reused in
    between. → True when signalled."""
    fd = None
    if hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
        try:
            fd = os.pidfd_open(pid)
        except OSError:
            return False
    try:
        now = proc_row(pid)
        if now is None or row is None or now[0] != row[0]:
            return False
        if row[1] is not None and (now[1] is None or abs(now[1] - row[1]) > 2.0):
            return False
        if fd is not None:
            signal.pidfd_send_signal(fd, sig)
        else:
            os.kill(pid, sig)
        return True
    except OSError:
        return False
    finally:
        if fd is not None:
            os.close(fd)


def turn_children(roots, since, rows=None) -> list:
    """Live descendants of `roots` that belong to the turn: started at/after `since` and not in a root's first BOOT_GRACE s.
    Unknown start time = counted (not knowing never makes a turn stuck)."""
    rows = proc_rows() if rows is None else rows
    kids: dict = {}
    for pid, (ppid, _) in rows.items():
        kids.setdefault(ppid, []).append(pid)
    out = []
    for root in roots:
        boot = (rows.get(root) or (0, None))[1]
        floor = max(since or 0, (boot or 0) + BOOT_GRACE) - 1
        todo = list(kids.get(root, ()))
        while todo:
            p = todo.pop()
            todo.extend(kids.get(p, ()))
            t = rows.get(p, (0, None))[1]
            if t is None or t >= floor:
                out.append(p)
    return sorted(set(out))


def ancestors(pid, rows=None) -> list:
    rows = proc_rows() if rows is None else rows
    out, seen = [], set()
    while pid in rows and pid not in seen and pid > 1:
        seen.add(pid)
        pid = rows[pid][0]
        out.append(pid)
    return out


def may_stop(st, pid, own=None, rows=None) -> bool:
    """Agent J started it: our live child handle (`own`), an app-server recorded with the same start time, or serve is in
    its parent chain. Anything else (the owner's terminal, a daemon, another client) is never signalled."""
    if not isinstance(pid, int) or pid <= 1 or pid == os.getpid():
        return False
    if own is not None and getattr(own, "pid", None) == pid and getattr(own, "returncode", 0) is None:
        return True
    from . import codex_procs
    with contextlib.suppress(Exception):
        if pid in codex_procs.ours_alive(st):
            return True
    return os.getpid() in ancestors(pid, rows)


def rollout_turn_start(path):
    """Epoch of the rollout's last `task_started` (its line timestamp), None = unknown."""
    at = None
    with contextlib.suppress(OSError):
        with open(path, "rb") as f:
            for line in f:
                if b"task_started" not in line:
                    continue
                with contextlib.suppress(ValueError, AttributeError, TypeError):
                    ts = json.loads(line).get("timestamp")
                    at = dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    return at


def _rollout_sid(path):
    """The thread id at the end of a rollout file name (`rollout-<time>-<uuid>.jsonl`), None = not that shape."""
    stem = os.path.basename(str(path)).rsplit(".", 1)[0]
    from .shared_codex import UUID
    return stem[-36:] if len(stem) >= 36 and UUID.fullmatch(stem[-36:]) else None


def daemon_live(sid, holders) -> bool:
    """0.26.9 merge ruling (ADR-0269-merge-rulings): the owner's Codex app-server daemon holds this rollout and itself
    reports the thread's turn `active`. Its live report outranks the file-age guess for /clear and the automatic heal;
    unreachable / not active / not a holder → False (the stuck rule applies)."""
    if not sid or not holders:
        return False
    from . import codex_daemon
    try:
        st = codex_daemon.thread_status(sid)
    except Exception:  # noqa: BLE001
        return False
    return bool(st) and st.get("status") == "active" and st.get("pid") in holders


def desktop_facts(path, skip=(), sid=None) -> dict | None:
    """The desktop turn behind a shared-Codex wait: {"idle", "children", "roots", "asking", "live"}; None = the platform
    cannot say. `live`: the owner's daemon says this turn runs (asked only when the turn would otherwise look stuck)."""
    from . import codex_procs
    try:
        idle = time.time() - os.stat(path).st_mtime
        holders = codex_procs.fd_holders(path)
    except Exception:  # noqa: BLE001
        return None
    if holders is None:
        return None
    roots = [p for p in holders if p != os.getpid() and p not in skip]
    from .shared_codex import pending_approval      # L-6: a turn waiting on its owner's approval is not stuck
    kids = turn_children(roots, rollout_turn_start(path)) if roots else []
    asking = pending_approval(path)
    live = bool(roots) and idle >= QUIET and not kids and not asking and daemon_live(sid or _rollout_sid(path), roots)
    return {"idle": idle, "roots": roots, "children": kids, "asking": asking, "live": live}


def dead_facts(f) -> bool:
    """#484 on desktop_facts' answer: never with a tool child of its holders or a pending approval / question / unanswered
    tool call (security L-6); dead when quiet ≥ STUCK_IDLE and the owner's daemon does not report it active, or quiet ≥
    DEAD_IDLE whatever the daemon says (a daemon that keeps saying "active" for ten silent minutes is not proof)."""
    if not f or f["children"] or f.get("asking", True):
        return False
    return f["idle"] >= DEAD_IDLE or (f["idle"] >= STUCK_IDLE and not f.get("live", True))


def desktop_stuck(path, skip=(), sid=None) -> bool:
    """A shared rollout's open turn may be treated as ended (dead_facts): for /stop, the reset buttons, the watchdog and the
    upgrade restart's idle wait alike."""
    return dead_facts(desktop_facts(path, skip, sid))


# ------------------------------------------------------------------ the rule
def judge(*, idle, waited, children, asking=False, loops=0):
    """→ why ("no_event" | "loop" | "queued") or None. Pure."""
    if children or asking or idle is None:
        return None
    if idle >= STUCK_IDLE:
        return "no_event"
    if idle >= QUIET and loops >= LOOP_HITS:
        return "loop"
    if idle >= QUIET and waited is not None and waited >= QUEUE_LIMIT:
        return "queued"
    return None


def _owned_harness(a) -> bool:
    """The harness process belongs to Agent J (not the owner's shared Claude window / external OpenCode server)."""
    mode = (getattr(a, "cfg", None) or {}).get("session_mode")
    if mode != "shared" or a.kind == "codex":
        return True
    native = getattr(a, "native", None)
    return native is not None and getattr(native, "pid", None) == (getattr(a, "session", None) or {}).get("pid")


class TurnHeal:
    """serve's watchdog: `tick()` every TICK s, `heal()` for the watchdog and the signed rescue_do end_turn / takeover,
    `preempt()` for /clear /new and reset connection, `view()` for status / doctor / the queued notice."""

    def __init__(self, host):
        self.h = host
        self.loops: list = []
        self.ends = 0
        self.delivered_n = 0
        self.pages: set = set()
        self.state: dict = {"busy": False}
        self.last_auto = -COOLDOWN
        self.healing = False
        self.healed_turn = None

    # hooks (cheap, on the loop)
    def saw_loop(self):
        now = time.monotonic()
        self.loops = [t for t in self.loops if now - t < LOOP_WINDOW] + [now]

    def turn_ended(self):
        self.ends += 1

    def delivered(self, agent=None):
        if agent is None or agent is getattr(self.h, "agent", None):
            self.delivered_n += 1

    def queued_page(self, tid):
        if isinstance(tid, int):
            self.pages.add(tid)

    def view(self) -> dict:
        return dict(self.state)

    # measuring
    def _turn_t0(self):
        t0 = getattr(self.h, "turn_t0", None)
        return time.time() - (time.monotonic() - t0) if t0 else None

    def measure(self) -> dict:
        """One snapshot (call off the loop: it reads the process table)."""
        a = getattr(self.h, "agent", None)
        if a is None or getattr(a, "status", None) != "working":
            return {"busy": False}
        now_m, since = time.monotonic(), self._turn_t0()
        waited = int(time.time() - since) if since else None
        asking = bool(getattr(a, "_asks_open", lambda: False)())
        loops = len([t for t in self.loops if now_m - t < LOOP_WINDOW])
        queued = not a.q.empty() or bool(getattr(a, "desktop_wait", False))
        path = getattr(a, "wait_path", None) or getattr(a, "rollout", None)
        if getattr(a, "desktop_wait", False) and path is not None:
            sid = getattr(a, "wait_sid", None) or getattr(a, "rollout_sid", None)
            f = desktop_facts(path, skip=getattr(a, "_own_pids", lambda: ())(), sid=sid)
            if f is None:
                return {"busy": True, "waited_s": waited, "stuck": False, "desktop": True}
            # merge ruling: the daemon's live "active" outranks silence — no automatic heal of another app's turn —
            # until DEAD_IDLE (#484: ten silent minutes with no tool child and no approval is a dead turn)
            idle, kids = f["idle"], f["children"]
            asking = asking or f.get("asking", True) or (f.get("live", True) and f["idle"] < DEAD_IDLE)
        else:
            # an adapter that never reported an event (shared Claude reads a transcript) is never judged by silence
            ev = getattr(a, "event_at", None) if getattr(a, "ever_touched", False) is True else None
            idle = now_m - ev if isinstance(ev, (int, float)) else None
            p = getattr(a, "proc", None)
            alive = p is not None and getattr(p, "returncode", 0) is None
            kids = turn_children([p.pid], since) if alive else []
        why = judge(idle=idle, waited=waited if queued else None, children=kids, asking=asking, loops=loops)
        return {"busy": True, "waited_s": waited, "idle_s": int(idle) if idle is not None else None,
                "children": len(kids), "stuck": bool(why), **({"why": why} if why else {}),
                **({"desktop": True} if getattr(a, "desktop_wait", False) else {})}

    async def tick(self):
        if self.healing:
            return
        self.state = await asyncio.to_thread(self.measure)
        self._refresh_pages()
        if not self.state.get("stuck") or self.h.stopped():
            return
        if time.monotonic() - self.last_auto < COOLDOWN or self.healed_turn == getattr(self.h, "turn_t0", None):
            return
        self.last_auto, self.healed_turn = time.monotonic(), getattr(self.h, "turn_t0", None)
        await self.heal("watchdog", self.state.get("why"))

    async def run(self):
        while True:
            await asyncio.sleep(TICK)
            try:
                await self.tick()
            except Exception as e:  # noqa: BLE001 — the watchdog never stops serve
                self.h.st.log("turn_selfheal", phase="tick", result="error", exception_type=type(e).__name__)

    def _refresh_pages(self):
        if not self.pages:
            return
        busy = self.state.get("busy")
        for tid in list(self.pages):
            page = self.h.hist.get(tid) if getattr(self.h, "hist", None) is not None else None
            if not page or not busy:
                self.pages.discard(tid)
                continue
            card = {**(page.get("card") or {}), "queued": True, "waited_s": int(self.state.get("waited_s") or 0)}
            card.pop("stuck", None)
            if self.state.get("stuck"):
                card["stuck"] = True
            if card != page.get("card"):
                self.h.hist_update(tid, card=card)

    # acting
    def _log(self, **kw):
        a = getattr(self.h, "agent", None)
        self.h.st.log("turn_selfheal", agent=getattr(a, "kind", None), **kw)

    async def _until(self, cond, secs) -> bool:
        end = time.monotonic() + secs
        while not cond():
            if time.monotonic() >= end:
                return False
            await asyncio.sleep(0.2)
        return True

    async def heal(self, by: str, why=None, action: str = "selfheal") -> dict:
        """by: "watchdog" | a device id. action: "selfheal" (watchdog) · "end_turn" · "takeover" (owner-signed)."""
        a = getattr(self.h, "agent", None)
        if a is None or self.healing:
            return {"ok": False, "why": "busy" if self.healing else "no_agent"}
        if action == "takeover" and self.h.stopped():
            return {"ok": False, "why": "estop"}
        self.healing = True
        try:
            return await self._heal(a, by, why, action)
        finally:
            self.healing = False

    async def _heal(self, a, by, why, action) -> dict:
        st, kind = self.h.st, a.kind
        sid0, ends0, d0 = st.agent_session(kind), self.ends, self.delivered_n
        queued = not a.q.empty() or bool(getattr(a, "desktop_wait", False))
        busy0 = getattr(a, "status", None) == "working"
        self._log(phase="start", by="watchdog" if by == "watchdog" else "owner", action=action, why=why, queued=queued)
        step, result = "interrupt", None

        def ended():
            return self.ends != ends0 or getattr(a, "status", None) != "working" or getattr(self.h, "agent", None) is not a
        if getattr(a, "desktop_wait", False) and hasattr(a, "release_stuck"):
            step = "release"                       # the turn is in the owner's desktop / daemon: never signalled
            a.release_stuck()
            await self._until(lambda: not getattr(a, "desktop_wait", False), ESCALATE)
        elif busy0:
            await self._interrupt(a)
            if not await self._until(ended, ESCALATE):
                step, result = await self._escalate(a, action)
                await self._until(ended, ESCALATE)
        if result == "foreign":
            self._log(phase="end", action=action, step=step, result="foreign")
            self._say("selfheal.failed")
            return {"ok": False, "why": "foreign", "step": step}
        ok = await self._proof(a, sid0, d0, queued, ends0)
        self._log(phase="end", action=action, step=step, result="recovered" if ok else "failed")
        if by == "watchdog" or action == "takeover":
            self._say("selfheal.recovered" if ok else "selfheal.failed")
        return {"ok": ok, "step": step, **({} if ok else {"why": "not_recovered"})}

    async def _interrupt(self, a):
        from .agent import Agent
        a.halt_quiet = True
        try:
            if type(a).halt is Agent.halt:              # the base halt ends the process: interrupt only, first
                with contextlib.suppress(Exception):
                    await a.interrupt_request(getattr(a, "proc", None))
                td = getattr(a, "turn_done", None)
                if td is not None:
                    td.set()
            else:                                       # shared adapters: their halt is the non-killing interrupt
                with contextlib.suppress(Exception):
                    await a.halt(clear_queue=False)
        finally:
            a.halt_quiet = False

    async def _escalate(self, a, action):
        p = getattr(a, "proc", None)
        alive = p is not None and getattr(p, "returncode", 0) is None
        if not alive:
            return "stop", None
        rows = await asyncio.to_thread(proc_rows)
        if action == "end_turn":                        # only that turn's tool children (ours by the parent chain)
            for pid in turn_children([p.pid], self._turn_t0(), rows):
                if may_stop(self.h.st, pid, rows=rows):
                    signal_same(pid, rows.get(pid), signal.SIGTERM)    # w2-sec L-1: not a reused pid
            return "children", None
        if not _owned_harness(a) or not may_stop(self.h.st, p.pid, own=p, rows=rows):
            return "stop", "foreign"
        from .agent import _signal_tree
        a.halting = True
        a.ended.add(p)
        _signal_tree(p, signal.SIGTERM)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(p.wait(), 5)
        _signal_tree(p, signal.SIGKILL)
        td = getattr(a, "turn_done", None)
        if td is not None:
            td.set()
        return "stop", None

    async def _proof(self, a, sid0, d0, queued, ends0) -> bool:
        """Recovered = the queued owner message reached the harness, in the same conversation (none queued: the turn ended)."""
        kind = a.kind

        def done():
            if queued:
                return self.delivered_n > d0
            return self.ends != ends0 or getattr(getattr(self.h, "agent", None), "status", None) != "working"
        if not await self._until(done, PROOF_WAIT):
            return False
        sid = self.h.st.agent_session(kind)
        return not sid0 or sid == sid0

    def _say(self, key):
        from . import humane
        with contextlib.suppress(Exception):
            self.h.agent_notice(humane.of(self.h, key))

    async def preempt(self, by: str, what: str) -> bool:
        """/clear /new · reset connection: interrupt and discard the running turn (never wait behind it). True = one ran."""
        a = getattr(self.h, "agent", None)
        if a is None or getattr(a, "status", None) not in ("working", "compacting"):
            return False
        ends0 = self.ends
        self._log(phase="preempt", by="owner", action=what)
        a.halt_quiet = True          # shared Codex: its halt ends a stuck desktop wait for the phone (dead_turn)
        try:
            with contextlib.suppress(Exception):
                await a.halt(clear_queue=False)
        finally:
            a.halt_quiet = False
        await self._until(lambda: self.ends != ends0 or getattr(a, "status", None) != "working", 3)
        return True
