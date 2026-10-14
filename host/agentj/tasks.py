"""`agentj tasks` — the scheduler for `workflows/<id>/task.json` (PROMPT-26 item 3; ADR-A53; contract ADR-A61 / taskspec.py).

Source: the Agent's folder (`agentj agent … --dir D`) → `D/workflows/*/task.json`, each checked with taskspec.problems();
an invalid task is listed with its reason and never runs. Its `enabled` is read only as a suggestion: whether a task runs is
the human's decision, stored in the state directory (`tasks.json`, 0600), made at the terminal (`agentj tasks enable`, with
the remote pairing password) or on a paired phone (signed, controls.py). An enabling is bound to the task's contract hash
(task.json + its prompt file): when either changes, the task stops running until a human enables it again.

`serve` runs enabled tasks when their cron fields match the minute (in the task's `tz`), at most once per minute, no catch-up
for minutes missed while serve was not running. A run waits until the chat turn is over (it takes the same lock — never two
users of one Agent), then runs the configured harness once, headless, in the workspace root, inside the fence; Claude Code
gets the usual permission tool + danger hook (approvals go to the phone as usual), and for `mode: research` the hook denies
everything that is not read-only (danger.readonly); Codex (app-server) and OpenCode (serve) run their chat adapter once in a
throw-away conversation, approvals to the phone too — research: Codex's own sandbox read-only + every request that is not
read-only declined, OpenCode session rules that deny where they would ask. The answer is saved to `workflows/<id>/reports/run-<run_id>.md`; its last
`VERDICT: ok|attention|fail — …` line goes to the phones as one notice (task name + that sentence) and into activity.log.
No VERDICT line = fail. 30 minutes at most, then it is ended (fail). Stop everything ends a running task at once and keeps
the scheduler paused until resume.
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import hashlib
import json
import os
import re
import signal
import stat
import time
import tempfile
import uuid

from . import taskspec
from .envcompat import getenv

MAX_PROMPT = 100 * 1024
VERDICT_RE = re.compile(r"^[ \t>*`_#-]*VERDICT[*`_]*[ \t]*[:：][ \t*`_]*(ok|attention|fail)[*`_]*[ \t]*[—–-]+[ \t]*(.+?)[ \t*`_]*$",
                        re.M | re.I)


def _ttl(env: str, default: float) -> float:
    try:
        return min(default, float(getenv(env, default)))
    except ValueError:
        return default


RUN_TIMEOUT = _ttl("AGENTJ_TEST_TASK_TIMEOUT", 1800)
TICK = _ttl("AGENTJ_TEST_TASK_TICK", 20)


class TaskError(Exception):
    """reason: unknown | invalid | changed | no_agent | busy | stopped | unsupported."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason)
        self.reason, self.detail = reason, detail


# ------------------------------------------------------------------ discovery
def _regular(path: str) -> bytes | None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_size > MAX_PROMPT:
            return None
        return os.read(fd, MAX_PROMPT + 1)[:MAX_PROMPT + 1]
    finally:
        os.close(fd)


def _discover_legacy(workdir: str | None, direct: bool = False) -> list[dict]:
    """[{id, dir, task|None, problems, tsha|None}] for every folder under <workdir>/workflows (symlinks are not followed)."""
    if not workdir:
        return []
    root = workdir if direct else os.path.join(workdir, "workflows")
    try:
        if stat.S_ISLNK(os.lstat(root).st_mode):
            return []
        names = sorted(os.listdir(root))
    except OSError:
        return []
    out = []
    for name in names[:200]:
        d = os.path.join(root, name)
        try:
            if not stat.S_ISDIR(os.lstat(d).st_mode):
                continue
        except OSError:
            continue
        if direct and not os.path.lexists(os.path.join(d, "task.json")):
            continue
        ent = {"relative_dir": name if direct else "workflows/" + name, "id": name, "dir": d, "task": None, "problems": [], "tsha": None}
        if not taskspec.ID_RE.fullmatch(name):
            ent["problems"] = ["folder name is not a task id"]
            out.append(ent)
            continue
        raw = _regular(os.path.join(d, "task.json"))
        if raw is None:
            ent["problems"] = ["task.json is missing (or not a regular file)"]
            out.append(ent)
            continue
        try:
            task = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            ent["problems"] = ["task.json is not valid UTF-8 JSON"]
            out.append(ent)
            continue
        probs = taskspec.problems(task, name)
        prompt = None
        if not probs:
            prompt = _regular(os.path.join(d, task["prompt_file"]))
            if prompt is None:
                probs = [f"{task['prompt_file']} is missing, not a regular file or larger than 100 KiB"]
        ent["task"] = task if isinstance(task, dict) else None
        extra = b""
        if not probs:                          # 0.18 (ADR-A193): a brief.json joins the contract with its scope digest
            from .capability import contract_part
            extra, bprobs = contract_part(d)
            probs = bprobs
        ent["problems"] = probs
        if not probs:
            ent["tsha"] = contract_sha(raw, prompt, extra)
        out.append(ent)
    return out


def discover(workdir: str | None) -> list[dict]:
    """New root/<id> folders and unchanged legacy workflows/<id>; duplicates fail closed."""
    rows = _discover_legacy(workdir, direct=True) + _discover_legacy(workdir)
    counts = {}
    for row in rows:
        counts[row["id"]] = counts.get(row["id"], 0) + 1
    for row in rows:
        if counts[row["id"]] > 1:
            row["problems"] = ["duplicate workflow id across root and legacy folders"]
            row["tsha"] = None
    return rows


def contract_sha(task_json: bytes, prompt: bytes, brief: bytes = b"") -> str:
    """task.json + its prompt (+ a brief's scope digest, capability.contract_part; b"" without a brief keeps old hashes)."""
    return hashlib.sha256(task_json + b"\0" + prompt + brief).hexdigest()


def find(workdir: str | None, tid: str) -> dict:
    for e in discover(workdir):
        if e["id"] == tid:
            return e
    raise TaskError("unknown")


# ------------------------------------------------------------------ the human's decisions (state dir)
def load(st) -> dict:
    try:
        d = json.loads(st.tasks_path.read_text())
    except (FileNotFoundError, ValueError, UnicodeDecodeError):
        d = {}
    d = d if isinstance(d, dict) else {}
    for k in ("on", "last", "ran", "runs"):
        if not isinstance(d.get(k), dict):
            d[k] = {}
    return d


def save(st, d: dict) -> None:
    st.write_private(st.tasks_path, json.dumps({"v": 2, "on": d["on"], "last": d["last"], "ran": d["ran"], "runs": d["runs"]},
                                               ensure_ascii=False).encode())


def update(st, change) -> dict:
    """P142 (B19): the one read-modify-write of tasks.json — under its own flock (`tasks.lock`), so the scheduler, a phone card,
    the account page and the terminal never lose each other's change. `change(d)` mutates the loaded dict."""
    with st.file_lock("tasks"):
        d = load(st)
        change(d)
        # Keep unfinished records; bound terminal metadata to 500 entries / 30 days.
        terminal = ((k, v) for k, v in d["runs"].items()
                    if v.get("status") in ("finished", "interrupted")
                    and not (v.get("uncertain") and not v.get("reviewed_at")))
        ended = sorted(terminal, key=lambda kv: kv[1].get("ended_at", 0), reverse=True)
        keep = {k for k, _ in ended[:500]}
        for k, v in ended:
            if v.get("ended_at", 0) < time.time() - 30 * 86400 or k not in keep:
                d["runs"].pop(k, None)
        save(st, d)
        return d


def set_enabled(st, workdir: str | None, tid: str, on: bool, by: str, tsha: str | None = None) -> dict:
    """Enable (bound to the current contract hash) or disable. `tsha` (from a phone) must equal the current hash."""
    e = find(workdir, tid)
    if on:
        if e["problems"]:
            raise TaskError("invalid", e["problems"][0])
        if tsha is not None and tsha != e["tsha"]:
            raise TaskError("changed")
    def change(d):
        if on:
            d["on"][tid] = {"tsha": e["tsha"], "at": int(time.time()), "by": by[:80]}
            # Existing human enable is also an explicit acknowledgement after checking old effects.
            for rec in d["runs"].values():
                if rec.get("id") == tid and rec.get("uncertain") and not rec.get("reviewed_at"):
                    rec["reviewed_at"] = int(time.time())
        else:
            d["on"].pop(tid, None)
    update(st, change)
    return e


def uncertain_run(st, tid: str) -> bool:
    return any(r.get("id") == tid and r.get("uncertain") and not r.get("reviewed_at")
               for r in load(st)["runs"].values())


def rows(st, workdir: str | None, now: dt.datetime | None = None) -> list[dict]:
    d = load(st)
    out = []
    for e in discover(workdir):
        t = e["task"] or {}
        on = d["on"].get(e["id"])
        uncertain = any(v.get("id") == e["id"] and v.get("uncertain") and not v.get("reviewed_at")
                        for v in d["runs"].values())
        valid_enable = bool(on) and not e["problems"] and on.get("tsha") == e["tsha"]
        enabled = valid_enable and not uncertain
        r = {"id": e["id"], "title": t.get("title") if isinstance(t.get("title"), dict) else {"zh": e["id"], "en": e["id"]},
             "schedule": t.get("schedule"), "tz": t.get("tz"), "mode": t.get("mode"), "suggested": t.get("enabled") is True,
             "enabled": enabled, "stale": bool(on) and not valid_enable, "uncertain": uncertain, "problems": e["problems"][:3], "tsha": e["tsha"],
             "last": d["last"].get(e["id"])}
        if enabled:
            nx = next_run(t["schedule"], t["tz"], now)
            r["next"] = int(nx.timestamp()) if nx else None
        out.append(r)
    return out


# ------------------------------------------------------------------ cron (5 numeric fields, taskspec.cron_problem)
def _field(f: str, lo: int, hi: int) -> set[int]:
    vals: set[int] = set()
    for part in f.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/")
            step = max(1, int(s))
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(x) for x in part.split("-"))
        else:
            a = b = int(part)
            if step > 1:
                b = hi
        vals.update(range(a, b + 1, step))
    return vals


def parse_cron(expr: str):
    m, h, dom, mon, dow = expr.split()
    days = _field(dow, 0, 7)
    if 7 in days:
        days = (days - {7}) | {0}
    return (_field(m, 0, 59), _field(h, 0, 23), _field(dom, 1, 31), _field(mon, 1, 12), days, dom != "*", dow != "*")


def _tz(tz: str):
    if tz == "local":
        return None
    import zoneinfo
    return zoneinfo.ZoneInfo(tz)


def _now(tz: str, now: dt.datetime | None = None) -> dt.datetime:
    z = _tz(tz)
    base = now or dt.datetime.now(dt.timezone.utc)
    if base.tzinfo is None:
        base = base.astimezone()
    return base.astimezone(z) if z else base.astimezone()


def _day_ok(c, d: dt.datetime) -> bool:
    _, _, dom, mon, dow, dom_r, dow_r = c
    if d.month not in mon:
        return False
    in_dom, in_dow = d.day in dom, (d.isoweekday() % 7) in dow
    if dom_r and dow_r:
        return in_dom or in_dow             # classic cron: either restricted field may match
    return in_dom and in_dow


def matches(expr: str, tz: str, now: dt.datetime | None = None) -> bool:
    c = parse_cron(expr)
    t = _now(tz, now)
    return t.minute in c[0] and t.hour in c[1] and _day_ok(c, t)


def minute_key(tz: str, now: dt.datetime | None = None) -> str:
    return _now(tz, now).strftime("%Y-%m-%dT%H:%M")


def next_run(expr: str, tz: str, now: dt.datetime | None = None) -> dt.datetime | None:
    try:
        c = parse_cron(expr)
        start = _now(tz, now).replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
    except Exception:  # noqa: BLE001
        return None
    day0 = start.replace(hour=0, minute=0)
    for k in range(400):
        d = day0 + dt.timedelta(days=k)
        if not _day_ok(c, d):
            continue
        for h in sorted(c[1]):
            for m in sorted(c[0]):
                cand = d.replace(hour=h, minute=m)
                if cand >= start:
                    return cand
    return None


def parse_verdict(text: str) -> tuple[str, str] | None:
    found = VERDICT_RE.findall(text or "")
    if not found:
        return None
    v, why = found[-1]
    return v.lower(), why.strip()


def prompt_for(e: dict, research: bool) -> str:
    task = e["task"]
    body = (_regular(os.path.join(e["dir"], task["prompt_file"])) or b"").decode("utf-8", "replace")
    relative = e.get("relative_dir", "workflows/" + e["id"])
    head = (f"[agentj 定时任务 / scheduled task] {relative}/{task['prompt_file']} — {task['title']['zh']} / "
            f"{task['title']['en']}. 说明里的相对路径以 {relative}/ 为准 / paths in the instructions are relative to "
            f"{relative}/.")
    if research:
        head += (" 这是只读运行：写文件、改动东西、对外发送的工具调用都会被拒绝；把结论写在回答里。/ Read-only run: every call that "
                 "writes, changes or sends is refused; put everything in your answer.")
    tail = ("\n\n---\n回答的最后一行必须是 `VERDICT: ok|attention|fail — 一句话`。/ End your answer with exactly one line "
            "`VERDICT: ok|attention|fail — <one sentence>`.")
    return head + "\n\n" + body.rstrip() + brief_block(e) + tail + "\n"



def brief_block(e: dict) -> str:
    from .capability import brief_block as bb      # 0.18 (ADR-A194): the brief's contract text lives with the feature
    return bb(e)


# ------------------------------------------------------------------ the runner inside serve
class Scheduler:
    """Lives in serve. One run at a time; runs only while the stop switch is off."""

    def __init__(self, host):
        self.host = host
        self.current: asyncio.Task | None = None
        self.current_id: str | None = None
        self.proc = None
        self.runner = None                 # a Codex / OpenCode adapter running a task (run_once)
        self.waiting: list[tuple[str, str]] = []
        self.wake = asyncio.Event()
        self.instance_id = uuid.uuid4().hex  # ownership is never inferred from a reusable PID
        self.queued: dict[str, str] = {}
        self.recovered = False

    @property
    def st(self):
        return self.host.st

    def workdir(self) -> str | None:
        c = self.host.agent_cfg
        return c["dir"] if c else None

    def recover(self) -> None:
        """Called by serve before accepting dispatches. Never executes or kills an old process."""
        if self.recovered:
            return
        recovered = []
        def change(d):
            for rec in d["runs"].values():
                if rec.get("status") not in ("queued", "running") or rec.get("instance_id") == self.instance_id:
                    continue
                uncertain = rec["status"] == "running"
                lang = getattr(self.host, "lang", "zh")
                if uncertain:
                    line = ("Host restarted; old execution may still be active. Check outputs and external effects, then enable the workflow again."
                            if lang == "en" else "主机重启，无法确认旧执行是否结束；请核对已有产物和外部操作后由主人重新启用。")
                else:
                    line = ("Host restarted; queued run interrupted, not replayed."
                            if lang == "en" else "主机重启，排队运行已中断，未自动重跑。")
                rec.update(status="interrupted", ended_at=int(time.time()), uncertain=uncertain,
                           verdict="attention", line=line)
                path = os.path.join(self.workdir() or "", rec.get("report") or "")
                rec["report_available"] = bool(rec.get("report") and _regular(path) is not None)
                if rec["report_available"]:
                    rec["line"] += (" Existing report: " if lang == "en" else " 已有报告：") + rec["report"]
                d["last"][rec["id"]] = dict(rec, at=rec["ended_at"])
                recovered.append(dict(rec))
        update(self.st, change)
        caps = getattr(self.host, "capabilities", None)
        if caps is not None:
            recovered.extend(caps.recover_runs(load(self.st)["runs"]))
        self.recovered = True
        for res in recovered:
            self.host.task_finished(res)

    def request(self, tid: str, trigger: str = "manual", minute: str | None = None) -> str:
        if self.current_id == tid or any(t == tid for t, _ in self.waiting):
            return self.queued.get(tid, "")
        if uncertain_run(self.st, tid):
            raise TaskError("busy", "旧执行是否结束尚未确认；核对产物和外部操作后，由主人重新启用。 / Old execution is uncertain; check effects, then have the owner enable the workflow again.")
        if len(self.waiting) >= 200:
            raise TaskError("busy")
        e = find(self.workdir(), tid)
        rid = uuid.uuid4().hex
        name = "run-" + rid + ".md"
        rec = {"run_id": rid, "id": tid, "tsha": e["tsha"], "trigger": trigger,
               "instance_id": self.instance_id, "status": "queued", "queued_at": int(time.time()),
               "report": f"{e.get('relative_dir', 'workflows/' + tid)}/reports/{name}"}
        def change(d):
            if len(d["runs"]) >= 1000:
                raise TaskError("busy", "运行记录已满；请先核对未确认的运行 / Run journal full; review uncertain runs first")
            d["runs"][rid] = rec
            if minute is not None:
                d["ran"][tid] = minute
        update(self.st, change)  # commit before acknowledging acceptance or adding to the in-memory queue
        self.queued[tid] = rid
        self.waiting.append((tid, trigger))
        self.wake.set()
        return rid

    async def loop(self) -> None:
        self.recover()
        while True:
            try:
                if not self.host.stopped():
                    self._due()
                while self.waiting and not self.host.stopped():
                    tid, trigger = self.waiting.pop(0)
                    self.current_id = tid
                    self.current = asyncio.create_task(self.run(tid, trigger))
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await self.current
                    self.queued.pop(tid, None)
                    self.current, self.current_id = None, None
            except Exception:  # noqa: BLE001 — a scheduler bug must never reach serve
                pass
            self.wake.clear()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.wake.wait(), TICK)

    def _due(self) -> None:
        if not self.host.agent_cfg:
            return
        d = load(self.st)
        for r in rows(self.st, self.workdir()):
            if not r["enabled"] or uncertain_run(self.st, r["id"]):
                continue
            try:
                if not matches(r["schedule"], r["tz"]):
                    continue
                key = minute_key(r["tz"])
            except Exception:  # noqa: BLE001
                continue
            if d["ran"].get(r["id"]) == key:
                continue
            self.request(r["id"], "schedule", minute=key)

    async def stop_running(self) -> bool:
        """/stop: end the run in progress (if any); the queue and the schedule stay."""
        t = self.current
        if t is None or t.done():
            return False
        t.cancel()
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(asyncio.shield(t), 8)
        return True

    async def stop_current(self) -> bool:
        """Stop everything: end the running task now (its process tree), forget the queue."""
        waiting, self.waiting = self.waiting, []
        for tid, trigger in waiting:
            rid = self.queued.pop(tid, None)
            if rid:
                # A full disk must never prevent cancellation of the actual running process.
                with contextlib.suppress(Exception):
                    self._end_run(rid, {"verdict": "fail", "line": "已急停，排队运行被中断 / Queued run stopped", "stopped": True})
        t = self.current
        if t is None or t.done():
            return False
        t.cancel()
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(asyncio.shield(t), 8)
        return True

    async def run(self, tid: str, trigger: str) -> dict:
        host = self.host
        wd = self.workdir()
        rid = self.queued.get(tid)
        if not rid:
            rid = self.request(tid, trigger)
        if (tid, trigger) in self.waiting:
            self.waiting.remove((tid, trigger))
        rec = load(self.st)["runs"][rid]
        res = {"id": tid, "run_id": rid, "status": "finished", "trigger": trigger, "verdict": "fail", "line": "", "readonly": False, "secs": 0.0}
        try:
            e = find(wd, tid)
        except TaskError:
            res["line"] = "工作流已移除 / Workflow removed"
            self._end_run(rid, res)
            return res
        title = (e["task"] or {}).get("title", {}).get("zh", tid) if isinstance((e["task"] or {}).get("title"), dict) else tid
        res["title"] = title
        if e["problems"]:
            res["line"] = "task.json 无效：" + e["problems"][0]
            self._finish(e, res)
            return res
        if rec["tsha"] != e["tsha"]:
            res["line"] = "合同已变更，没有运行 / Contract changed; did not run"
            self._finish(e, res)
            return res
        if trigger == "schedule":
            on = load(self.st)["on"].get(tid)
            if not on or on.get("tsha") != e["tsha"]:
                res["line"] = "定时任务已禁用，没有运行 / Schedule disabled; did not run"
                self._finish(e, res)
                return res
        research = e["task"]["mode"] == "research"
        res["readonly"] = research
        kind = host.agent_cfg["kind"] if host.agent_cfg else None
        note = None
        try:                                         # P254: quota alarm → this run on another AI account (or the owner's card)
            from . import quota
            kind, note = await quota.route(host, kind, e["task"], title)
        except asyncio.CancelledError:
            res.update(line="已急停，没有运行", stopped=True)
            self._finish(e, res)
            return res
        except Exception:  # noqa: BLE001 — any failure keeps the main Agent's harness
            pass
        host.activity("task_run", id=tid, title=title, trigger=trigger, mode=e["task"]["mode"])
        host.st.log("task_run", id=tid, kind=trigger)
        t0 = time.monotonic()
        run_started = time.time()
        lock = host.turn_lock
        try:
            async with lock:                         # waits for the chat turn to end; nothing else uses the Agent meanwhile
                if host.stopped():
                    res["line"] = "已急停，没有运行"
                    raise asyncio.CancelledError
                # The contract / enable can change while this run waits for the chat turn lock.
                e = find(self.workdir(), tid)
                if e["problems"] or rec["tsha"] != e["tsha"]:
                    raise TaskError("changed", "合同已变更，没有运行 / Contract changed; did not run")
                on = load(self.st)["on"].get(tid)
                if trigger == "schedule" and (not on or on.get("tsha") != e["tsha"]):
                    raise TaskError("changed", "定时任务已禁用，没有运行 / Schedule disabled; did not run")
                update(self.st, lambda d: d["runs"][rid].update(status="running", started_at=int(time.time())))
                caps = getattr(host, "capabilities", None)
                if caps is not None and trigger == "dispatch":
                    caps.run_started(tid, rid)
                # The atomic state is visible before any native tool can execute.
                from . import diskq
                host.task_label = title
                from .capability import brief_scope_for
                host.brief_scope = None if research else brief_scope_for(host.st, e)   # 0.18: confirmed brief, no card for its own outputs
                busy = getattr(host, "busy", None)   # P150: the workflow's CEO is one of the phone's 「在办事项」 dots
                tt = (e["task"] or {}).get("title")
                ceo = (tt.get("en") if getattr(host, "lang", "zh") == "en" else None) or title if isinstance(tt, dict) else title
                if busy is not None:
                    with contextlib.suppress(Exception):
                        busy.start("wf:" + tid, ceo, "ceo", "", lk="dispatch" if trigger == "dispatch" else "schedule",
                                   harness=kind, cwd=e.get("dir"), role="CEO")
                try:
                    # Publish lock ownership before the first await, including idle-harness shutdown (P249).
                    if not await diskq.aflush():
                        raise TaskError("busy", "运行记录未完成落盘，没有运行 / Run journal did not settle; did not run")
                    if host.agent:
                        await host.agent.halt(clear_queue=False)   # resumes on the next message; queued controls stay
                    out, code = await asyncio.wait_for(self._exec(kind, e, research), RUN_TIMEOUT)
                finally:
                    if busy is not None:
                        with contextlib.suppress(Exception):
                            busy.stop("wf:" + tid)
                    host.task_label = None
                    host.brief_scope = None
                    await self._kill()
            v = parse_verdict(out)
            if v:
                res["verdict"], res["line"] = v[0], v[1][:300]
            else:
                res["line"] = "回答里没有 VERDICT 行（按失败处理）" if out.strip() else f"没有回答（{code}）"
            if note:
                res["line"], res["harness"] = (res["line"] + " " + note)[:300], kind
            res["report"] = await asyncio.to_thread(self._save, e, out, research, rid)
            self._check_report(e, res, run_started)
        except asyncio.TimeoutError:
            res["status"] = "interrupted"
            res["line"] = f"超过 {int(RUN_TIMEOUT // 60) or 1} 分钟，已中断"
            await self._kill()
        except asyncio.CancelledError:
            res["line"] = res["line"] or "已急停，运行被中断"
            res["stopped"] = True
            res["status"] = "interrupted"
            await self._kill()
        except TaskError as err:
            res["line"] = err.detail or err.reason
        except Exception:
            res.update(status="interrupted", line="运行异常中断 / Run interrupted by an error")
            await self._kill()
        res["secs"] = round(time.monotonic() - t0, 1)
        self._finish(e, res)
        return res

    @staticmethod
    def _check_report(e: dict, res: dict, since: float) -> None:
        from .capability import check_run_report      # 0.18: report.json vs brief, deliverables (capability.py)
        check_run_report(e, res, since)

    def _end_run(self, rid: str, res: dict) -> None:
        def change(d):
            rec = d["runs"][rid]
            rec.update({k: res[k] for k in ("verdict", "line", "readonly", "secs", "report_check") if k in res})
            rec.update(status="interrupted" if res.get("stopped") else res.get("status", "finished"), ended_at=int(time.time()))
            d["last"][rec["id"]] = dict(rec, at=rec["ended_at"])
        update(self.st, change)
        self.queued.pop(res.get("id", ""), None)

    def _finish(self, e: dict, res: dict) -> None:
        self._end_run(res["run_id"], res)  # failure is not disguised as done
        caps = getattr(self.host, "capabilities", None)
        if caps is not None and res.get("trigger") == "dispatch":
            caps.run_finished(e["id"], res)
        self.host.task_finished(res)

    def _save(self, e: dict, out: str, research: bool, run_id: str | None = None) -> str | None:
        rep = os.path.join(e["dir"], "reports")
        try:
            if os.path.lexists(rep) and stat.S_ISLNK(os.lstat(rep).st_mode):
                return None
            os.makedirs(rep, exist_ok=True)
            name = "run-" + run_id + ".md" if run_id else time.strftime("run-%Y%m%d-%H%M%S.md")
            head = "<!-- agentj tasks: " + ("只读运行 / read-only run (mode: research)" if research else "mode: normal") + " -->\n"
            fd = os.open(os.path.join(rep, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
            try:
                with os.fdopen(fd, "wb", closefd=False) as f:
                    f.write((head + out).encode("utf-8"))
                    f.flush()
                    os.fsync(f.fileno())
            finally:
                os.close(fd)
            return f"{e.get('relative_dir', 'workflows/' + e['id'])}/reports/{name}"
        except OSError:
            return None

    async def _kill(self) -> None:
        claim, self.perm_claim_file = getattr(self, "perm_claim_file", None), None
        if claim:
            with contextlib.suppress(OSError):
                os.unlink(claim)
        r = self.runner
        if r is not None:
            with contextlib.suppress(Exception):
                await r.stop()
        p, self.proc = self.proc, None
        if p is None or p.returncode is not None:
            return
        from .agent import _signal_tree
        _signal_tree(p, signal.SIGTERM)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(p.wait(), 5)
        _signal_tree(p, signal.SIGKILL)

    async def _exec(self, kind: str | None, e: dict, research: bool) -> tuple[str, int | str]:
        from . import agent as agents
        from . import fence
        host = self.host
        cfg = dict(host.agent_cfg or {})
        if kind and cfg.get("kind") and kind != cfg["kind"]:   # P254: switched for quota — that harness's own defaults
            from .quota import alt_cfg
            cfg = alt_cfg(cfg, kind)
        cfg["_workflow_ceo"] = True
        cfg.pop("working_root", None)
        cfg["dir"] = e["dir"]
        wd = cfg["dir"]
        prompt = prompt_for(e, research)
        if kind == "claude":
            a = agents.ClaudeAgent(host, cfg)
            argv = a.argv(None, research=research)
            env = fence.auxiliary_environment(os.environ, "claude", extra_keep=tuple(k for k in fence._MAIN_ENV if k.startswith("FAKE_")))
            claim_env = host.new_perm_env()
            token = claim_env.pop("AGENTJ_PERM_TOKEN")
            # The task harness receives only a path. The permission tool consumes
            # this 0600 one-time claim before the model gets its first prompt.
            fd, self.perm_claim_file = tempfile.mkstemp(prefix="task-claim-", dir=host.st.perm_dir)
            with os.fdopen(fd, "w") as f:
                f.write(token)
            claim_env["AGENTJ_PERM_CLAIM"] = self.perm_claim_file
            env.update(claim_env)
            src = agents.source_root()
            if src:
                env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
            env["MCP_TOOL_TIMEOUT"] = str(int((host.ask_ttl + 60) * 1000))
            why = agents.hooks_blocked(wd)
            if why:
                raise TaskError("unsupported", why)
        elif kind in ("codex", "opencode"):
            # the chat adapter, once, in a throw-away conversation: approvals go to the phone as in chat (ADR-A72);
            # research: Codex gets sandbox read-only and every request that is not read-only declined, OpenCode rules that
            # deny instead of ask
            if cfg.get("fence", True):
                why = fence.problem(host.st, wd)
                if why:
                    raise TaskError("unsupported", fence.REASONS.get(why, why))
            if kind == "codex":
                from .agent_codex import CodexAgent as Runner
            else:
                from .agent_opencode import OpenCodeAgent as Runner
            self.runner = Runner(host, cfg, persist=False, research=research)
            try:
                return await self.runner.run_once(prompt)
            finally:
                self.runner = None
        else:
            raise TaskError("unsupported", "这个 Agent 还不能跑定时任务")
        if cfg.get("fence", True):
            why = fence.problem(host.st, wd)
            if why:
                raise TaskError("unsupported", fence.REASONS.get(why, why))
            argv = fence.wrap(host.st, argv, wd, allow_docker=cfg.get("docker", False))
        env = fence.launch_environment(env, cfg.get("docker", False), "claude")
        self.proc = p = await asyncio.create_subprocess_exec(*argv, cwd=wd, env=env, stdin=asyncio.subprocess.PIPE,
                                                             stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                                                             limit=agents.LINE_LIMIT, start_new_session=True)
        texts: list[str] = []
        try:
            await asyncio.wait_for(host.perm_claimed.wait(), agents.CLAIM_WAIT)
        except asyncio.TimeoutError:
            raise TaskError("unsupported", "Claude Code 的批准通道没有接上") from None
        msg = json.dumps({"type": "user", "message": {"role": "user", "content": prompt}}, ensure_ascii=False) + "\n"
        p.stdin.write(msg.encode())
        await p.stdin.drain()
        last = ""
        while line := await p.stdout.readline():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if not isinstance(ev, dict):
                continue
            if ev.get("type") == "assistant":
                m = ev.get("message") if isinstance(ev.get("message"), dict) else {}
                for b in m.get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str):
                        texts.append(b["text"])
            elif ev.get("type") == "result":
                if isinstance(ev.get("result"), str):
                    last = ev["result"]
                break
        with contextlib.suppress(Exception):
            p.stdin.close()
        out = "\n\n".join(texts)
        if last and last.strip() not in out:
            out = (out + "\n\n" + last).strip()
        return out, p.returncode if p.returncode is not None else 0
