"""`agentj tasks` — the scheduler for `workflows/<id>/task.json` (PROMPT-26 item 3; ADR-A53; contract ADR-A61 / taskspec.py).

Source: the Agent's folder (`agentj agent … --dir D`) → `D/workflows/*/task.json`, each checked with taskspec.problems();
an invalid task is listed with its reason and never runs. Its `enabled` is read only as a suggestion: whether a task runs is
the human's decision, stored in the state directory (`tasks.json`, 0600), made at the terminal (`agentj tasks enable`, with
the approval passphrase) or on a paired phone (signed, controls.py). An enabling is bound to the task's contract hash
(task.json + its prompt file): when either changes, the task stops running until a human enables it again.

`serve` runs enabled tasks when their cron fields match the minute (in the task's `tz`), at most once per minute, no catch-up
for minutes missed while serve was not running. A run waits until the chat turn is over (it takes the same lock — never two
users of one Agent), then runs the configured harness once, headless, in the workspace root, inside the fence; Claude Code
gets the usual permission tool + danger hook (approvals go to the phone as usual), and for `mode: research` the hook denies
everything that is not read-only (danger.readonly); Codex (app-server) and OpenCode (serve) run their chat adapter once in a
throw-away conversation, approvals to the phone too — research: Codex's own sandbox read-only + every request that is not
read-only declined, OpenCode session rules that deny where they would ask. The answer is saved to `workflows/<id>/reports/run-<time>.md`; its last
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


def discover(workdir: str | None) -> list[dict]:
    """[{id, dir, task|None, problems, tsha|None}] for every folder under <workdir>/workflows (symlinks are not followed)."""
    if not workdir:
        return []
    root = os.path.join(workdir, "workflows")
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
        ent = {"id": name, "dir": d, "task": None, "problems": [], "tsha": None}
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
        ent["problems"] = probs
        if not probs:
            ent["tsha"] = contract_sha(raw, prompt)
        out.append(ent)
    return out


def contract_sha(task_json: bytes, prompt: bytes) -> str:
    return hashlib.sha256(task_json + b"\0" + prompt).hexdigest()


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
    for k in ("on", "last", "ran"):
        if not isinstance(d.get(k), dict):
            d[k] = {}
    return d


def save(st, d: dict) -> None:
    st.write_private(st.tasks_path, json.dumps({"v": 1, "on": d["on"], "last": d["last"], "ran": d["ran"]},
                                               ensure_ascii=False).encode())


def set_enabled(st, workdir: str | None, tid: str, on: bool, by: str, tsha: str | None = None) -> dict:
    """Enable (bound to the current contract hash) or disable. `tsha` (from a phone) must equal the current hash."""
    e = find(workdir, tid)
    if on:
        if e["problems"]:
            raise TaskError("invalid", e["problems"][0])
        if tsha is not None and tsha != e["tsha"]:
            raise TaskError("changed")
    with st.config_lock():
        d = load(st)
        if on:
            d["on"][tid] = {"tsha": e["tsha"], "at": int(time.time()), "by": by[:80]}
        else:
            d["on"].pop(tid, None)
        save(st, d)
    return e


def rows(st, workdir: str | None, now: dt.datetime | None = None) -> list[dict]:
    d = load(st)
    out = []
    for e in discover(workdir):
        t = e["task"] or {}
        on = d["on"].get(e["id"])
        enabled = bool(on) and not e["problems"] and on.get("tsha") == e["tsha"]
        r = {"id": e["id"], "title": t.get("title") if isinstance(t.get("title"), dict) else {"zh": e["id"], "en": e["id"]},
             "schedule": t.get("schedule"), "tz": t.get("tz"), "mode": t.get("mode"), "suggested": t.get("enabled") is True,
             "enabled": enabled, "stale": bool(on) and not enabled, "problems": e["problems"][:3], "tsha": e["tsha"],
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
    head = (f"[agentj 定时任务 / scheduled task] workflows/{e['id']}/{task['prompt_file']} — {task['title']['zh']} / "
            f"{task['title']['en']}. 说明里的相对路径以 workflows/{e['id']}/ 为准 / paths in the instructions are relative to "
            f"workflows/{e['id']}/.")
    if research:
        head += (" 这是只读运行：写文件、改动东西、对外发送的工具调用都会被拒绝；把结论写在回答里。/ Read-only run: every call that "
                 "writes, changes or sends is refused; put everything in your answer.")
    tail = ("\n\n---\n回答的最后一行必须是 `VERDICT: ok|attention|fail — 一句话`。/ End your answer with exactly one line "
            "`VERDICT: ok|attention|fail — <one sentence>`.")
    return head + "\n\n" + body.rstrip() + tail + "\n"


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

    @property
    def st(self):
        return self.host.st

    def workdir(self) -> str | None:
        c = self.host.agent_cfg
        return c["dir"] if c else None

    def request(self, tid: str, trigger: str = "manual") -> None:
        if all(t != tid for t, _ in self.waiting) and self.current_id != tid:
            self.waiting.append((tid, trigger))
        self.wake.set()

    async def loop(self) -> None:
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
        changed = False
        for r in rows(self.st, self.workdir()):
            if not r["enabled"]:
                continue
            try:
                if not matches(r["schedule"], r["tz"]):
                    continue
                key = minute_key(r["tz"])
            except Exception:  # noqa: BLE001
                continue
            if d["ran"].get(r["id"]) == key:
                continue
            d["ran"][r["id"]] = key
            changed = True
            self.request(r["id"], "schedule")
        if changed:
            with self.st.config_lock():
                cur = load(self.st)
                cur["ran"].update(d["ran"])
                save(self.st, cur)

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
        self.waiting.clear()
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
        res = {"id": tid, "trigger": trigger, "verdict": "fail", "line": "", "readonly": False, "secs": 0.0}
        try:
            e = find(wd, tid)
        except TaskError:
            return res
        title = (e["task"] or {}).get("title", {}).get("zh", tid) if isinstance((e["task"] or {}).get("title"), dict) else tid
        res["title"] = title
        if e["problems"]:
            res["line"] = "task.json 无效：" + e["problems"][0]
            self._finish(e, res)
            return res
        if trigger == "schedule":
            on = load(self.st)["on"].get(tid)
            if not on or on.get("tsha") != e["tsha"]:
                return res
        research = e["task"]["mode"] == "research"
        res["readonly"] = research
        kind = host.agent_cfg["kind"] if host.agent_cfg else None
        host.activity("task_run", id=tid, title=title, trigger=trigger, mode=e["task"]["mode"])
        host.st.log("task_run", id=tid, kind=trigger)
        t0 = time.monotonic()
        lock = host.turn_lock
        try:
            async with lock:                         # waits for the chat turn to end; nothing else uses the Agent meanwhile
                if host.stopped():
                    res["line"] = "已急停，没有运行"
                    raise asyncio.CancelledError
                if host.agent:
                    await host.agent.halt(clear_queue=False)   # the idle chat process steps aside (resumes on the next message)
                host.task_label = title
                try:
                    out, code = await asyncio.wait_for(self._exec(kind, e, research), RUN_TIMEOUT)
                finally:
                    host.task_label = None
                    await self._kill()
            v = parse_verdict(out)
            if v:
                res["verdict"], res["line"] = v[0], v[1][:300]
            else:
                res["line"] = "回答里没有 VERDICT 行（按失败处理）" if out.strip() else f"没有回答（{code}）"
            res["report"] = self._save(e, out, research)
        except asyncio.TimeoutError:
            res["line"] = f"超过 {int(RUN_TIMEOUT // 60) or 1} 分钟，已中断"
            await self._kill()
        except asyncio.CancelledError:
            res["line"] = res["line"] or "已急停，运行被中断"
            res["stopped"] = True
            await self._kill()
        except TaskError as err:
            res["line"] = err.detail or err.reason
        res["secs"] = round(time.monotonic() - t0, 1)
        self._finish(e, res)
        return res

    def _finish(self, e: dict, res: dict) -> None:
        with contextlib.suppress(Exception):
            with self.st.config_lock():
                d = load(self.st)
                d["last"][e["id"]] = {"at": int(time.time()), "verdict": res["verdict"], "line": res["line"][:300],
                                      "readonly": res.get("readonly", False), "secs": res.get("secs", 0),
                                      "report": res.get("report"), "trigger": res["trigger"]}
                save(self.st, d)
        self.host.task_finished(res)

    def _save(self, e: dict, out: str, research: bool) -> str | None:
        rep = os.path.join(e["dir"], "reports")
        try:
            if os.path.lexists(rep) and stat.S_ISLNK(os.lstat(rep).st_mode):
                return None
            os.makedirs(rep, exist_ok=True)
            name = time.strftime("run-%Y%m%d-%H%M%S.md")
            head = "<!-- agentj tasks: " + ("只读运行 / read-only run (mode: research)" if research else "mode: normal") + " -->\n"
            fd = os.open(os.path.join(rep, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
            try:
                os.write(fd, (head + out).encode("utf-8"))
            finally:
                os.close(fd)
            return f"workflows/{e['id']}/reports/{name}"
        except OSError:
            return None

    async def _kill(self) -> None:
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
        wd = cfg.get("dir")
        prompt = prompt_for(e, research)
        if kind == "claude":
            a = agents.ClaudeAgent(host, cfg)
            argv = a.argv(None, research=research)
            env = dict(os.environ)
            env.update(host.new_perm_env())
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
