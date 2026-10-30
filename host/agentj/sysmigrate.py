"""P570b: enabled `agentj tasks` → the Agent J host's own scheduler, after serve's one confirm card. ARCHITECTURE ADR-P570b / P570c."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import plistlib
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import tasks
from .text import pick

MARK = "agentj tasks migrate"          # in every generated unit: only ours are overwritten / removed
TOOL = MARK                            # the confirm card's tool (policy_registry.HOST_TOOLS, approvals.log)
DECLINED = ("用户在手机上拒绝了这个操作。", "The owner declined this on the phone.")   # serve.ask: a paired device said no
RUN_WAIT = 1900
PENDING_MS = (RUN_WAIT + 300) * 1000      # the phone's wait for `task_migrate` (a test run)
MAX_OFFERS = 3                         # cards per version without an answer
REOFFER = 6 * 3600
KEEP = 120                             # report + log files kept per task
MAX_REPORT = 4 << 20                   # a run's report + log (L-d): over it the run is ended, the files cut
DOW = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
_NF = getattr(os, "O_NOFOLLOW", 0)
WHY = {
    "no_systemd": ("这台 Agent J 主机没开 systemd（WSL2 默认不开），任务照旧由 Agent J 运行；WSL2 上可以对 Agent 说「帮我开启 WSL 的 systemd」",
                   "This Agent J host has no systemd (WSL2 has it off by default), so tasks stay in Agent J; on WSL2, ask the Agent to \"turn on systemd in WSL\""),
    "platform": ("这个系统不支持", "Not supported on this system"),
    "no_tool": ("找不到系统定时工具", "No system scheduler tool found"),
    "harness": ("这个 AI 编程工具没有核实过的无人值守模式", "This AI coding tool has no verified unattended mode"),
    "readonly": ("只读任务要只读运行，这个 AI 编程工具没有核实过的只读模式", "A read-only task needs a verified read-only mode this tool lacks"),
    "schedule": ("时间表换算不成系统定时", "The schedule has no exact system-scheduler form"),
    "steps": ("多步任务的进度记在 Agent J 里", "Multi-step tasks keep their checkpoints in Agent J"),
    "secret": ("说明里像有密钥（只写位置和变量名）", "The instructions look like they hold a secret"),
    "exists": ("已有别人的同名定时单元，没有覆盖", "A scheduler entry with this name exists and is not ours"),
    "path": ("路径里有系统定时不支持的字符", "The path has characters the scheduler cannot take"),
    "running": ("任务正在运行", "The task is running"),
    "not_enabled": ("没有启用", "Not enabled"),
    "stale": ("任务内容改了，要先重新启用", "The task changed; enable it again first"),
    "changed": ("任务内容改了，要重新迁移或在 Agent J 里重新启用", "The task changed; move it again or enable it again in Agent J"),
    "migrated": ("已经迁过", "Already moved"),
    "unknown": ("没有这个任务", "No such task"),
    "stopped": ("已急停", "Everything is stopped"),
    "fence": ("Agent 隔离起不来", "The Agent fence does not start"),
    "no_agent": ("还没接 Agent", "No Agent yet"),
    "install": ("没能装进系统定时", "It could not be added to the system scheduler"),
    "start": ("系统定时没能把它跑起来", "The system scheduler could not run it"),
    "verdict": ("试跑没有通过", "The test run did not pass"),
    "not_migrated": ("没有迁到系统定时", "Not moved"),
    "brief": ("带任务书的任务由 Agent J 交付", "Tasks with a brief are delivered by Agent J"),
    "no_remote": ("没有在线的手机：打开手机上的 Agent J 再试", "No phone online: open Agent J on the phone and retry"),
    "pending": ("手机上已有一张迁移卡", "A move card is already on the phone"),
    "no": ("手机上拒绝了，没有迁", "Declined on the phone; nothing moved"),
    "none": ("手机上没有确认，没有迁", "Not confirmed on the phone; nothing moved"),
    "no_serve": ("Agent J 没在运行：先 agentj service start", "Agent J is not running: agentj service start first"),
    "migrated_on": ("已迁到系统定时，由 Agent J 主机按时运行；要改回 Agent J 运行，在手机「定时任务」页点「退回」再启用（网页终端也可 agentj tasks unmigrate，再 agentj tasks enable）",
                    "Moved to the system scheduler; to run it in Agent J again, tap Move back on the phone's Scheduled tasks page, then enable it (or in the web terminal: agentj tasks unmigrate, then agentj tasks enable)"),
    "reload": ("任务刚改过：刷新页面再试", "The task just changed: reload and try again"),
    "declined": ("手机上拒绝过：24 小时内不再发卡", "Declined on the phone: no new card for 24 hours"),
}


class MigrateError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Unconvertible(Exception):
    pass


def why(reason: str, lang: str = "zh") -> str:
    return pick(lang, *WHY.get(reason, (reason, reason)))


def _run(argv: list, timeout: float = 30) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout + p.stderr
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""


def _ok_id(tid) -> bool:
    from .taskspec import ID_RE
    return isinstance(tid, str) and bool(ID_RE.fullmatch(tid))


# ------------------------------------------------------------------ state: sysmigrate.json (0600) + <state>/sysmigrate/<id>/
def load(st) -> dict:
    try:
        d = json.loads((st.root / "sysmigrate.json").read_text())
    except (OSError, ValueError):
        d = {}
    d = d if isinstance(d, dict) else {}
    for k in ("tasks", "offers"):
        d[k] = d[k] if isinstance(d.get(k), dict) else {}
    return d


def _update(st, change) -> None:
    with st.file_lock("sysmigrate"):
        d = load(st)
        change(d)
        st.write_private(st.root / "sysmigrate.json", json.dumps(d, ensure_ascii=False).encode())


def migrated(st) -> dict:
    return {k: v["unit"] for k, v in load(st)["tasks"].items() if v.get("result") == "migrated"}


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _dir(base, *parts):
    """`base`/parts as a directory fd, each part made 0700 and opened O_NOFOLLOW (a planted symlink fails). Windows (no dir
    fds, no Agent fence): the checked path."""
    if os.open not in os.supports_dir_fd:
        p = Path(base, *parts)
        p.mkdir(mode=0o700, parents=True, exist_ok=True)
        if any(Path(base, *parts[:i + 1]).is_symlink() for i in range(len(parts))):
            raise OSError("link")
        return str(p)
    fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts:
            with contextlib.suppress(FileExistsError):
                os.mkdir(part, 0o700, dir_fd=fd)
            nfd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | _NF, dir_fd=fd)
            os.close(fd)
            fd = nfd
    except BaseException:
        os.close(fd)
        raise
    return fd


def _at(d, name):
    return (name, {"dir_fd": d}) if isinstance(d, int) else (os.path.join(d, name), {})


def _close(d) -> None:
    if isinstance(d, int):
        os.close(d)


def _put(d, name: str, data: bytes, mode: int = 0o600) -> None:
    """A fresh file in `d` (O_CREAT | O_EXCL | O_NOFOLLOW): whatever stood there, link or file, is unlinked, never written through."""
    p, kw = _at(d, name)
    with contextlib.suppress(FileNotFoundError):
        os.unlink(p, **kw)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NF | getattr(os, "O_BINARY", 0), mode, **kw)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def _get(d, name: str) -> bytes | None:
    p, kw = _at(d, name)
    try:
        fd = os.open(p, os.O_RDONLY | _NF | getattr(os, "O_BINARY", 0), **kw)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as f:
        return f.read(tasks.MAX_PROMPT * 2) if stat.S_ISREG(os.fstat(fd).st_mode) else None


# ------------------------------------------------------------------ cron → each scheduler (tasks.parse_cron semantics)
def _list(vals, lo, hi, f=lambda x: "%02d" % x) -> str:
    if set(vals) == set(range(lo, hi + 1)):
        return "*"
    v, out, i = sorted(vals), [], 0
    while i < len(v):
        j = i
        while j + 1 < len(v) and v[j + 1] == v[j] + 1:
            j += 1
        out += [f"{f(v[i])}..{f(v[j])}"] if j - i >= 2 else [f(x) for x in v[i:j + 1]]
        i = j + 1
    return ",".join(out)


def on_calendar(expr: str, tz: str) -> list[str]:
    """OnCalendar= lines (a union); day-of-month AND weekday both restricted = either, as cron → two lines."""
    mi, h, dom, mon, dow, dr, wr = tasks.parse_cron(expr)
    t = f"{_list(h, 0, 23)}:{_list(mi, 0, 59)}:00" + ("" if tz == "local" else " " + tz)
    w, d, m = _list(dow, 0, 6, DOW.__getitem__), _list(dom, 1, 31), _list(mon, 1, 12)

    def line(w, d):
        return ("" if w == "*" else w + " ") + f"*-{m}-{d} {t}"
    return [line("*", d), line(w, "*")] if dr and wr else [line(w, d)]


def _local_only(tz: str, local: str | None) -> None:
    if local is None:
        p = os.path.realpath("/etc/localtime")
        local = p.split("zoneinfo/", 1)[-1]
    if tz not in ("local", local):
        raise Unconvertible("tz")


def launchd_intervals(expr: str, tz: str, local: str | None = None) -> list[dict]:
    _local_only(tz, local)
    mi, h, dom, mon, dow, dr, wr = tasks.parse_cron(expr)
    full = {"Minute": 60, "Hour": 24, "Month": 12, "Day": 31, "Weekday": 7}

    def prod(*fields):
        out = [{}]
        for k, vals in (("Minute", mi), ("Hour", h), ("Month", mon), *fields):
            if len(vals) < full[k]:
                out = [dict(o, **{k: v}) for o in out for v in sorted(vals)]
        return out
    out = prod(("Day", dom)) + prod(("Weekday", dow)) if dr and wr else prod(("Day", dom), ("Weekday", dow))
    if len(out) > 200:
        raise Unconvertible("size")
    return out


def schtasks_schedule(expr: str, tz: str, local: str | None = None) -> list[str]:
    _local_only(tz, local)
    mi, h, dom, mon, dow, _, _ = tasks.parse_cron(expr)
    alld, allw, m0 = len(dom) == 31, len(dow) == 7, min(mi)
    if len(h) == 24 and alld and allw and len(mon) == 12:
        if len(mi) == 1:
            return ["/SC", "HOURLY", "/ST", "00:%02d" % m0]
        n = sorted(mi)[1] - m0
        if 60 % n == 0 and m0 < n and set(mi) == set(range(m0, 60, n)):
            return ["/SC", "MINUTE", "/MO", str(n), "/ST", "00:%02d" % m0]
    if len(mi) == 1 and len(h) == 1 and len(mon) == 12 and (alld or allw):
        at = ["/ST", "%02d:%02d" % (min(h), m0)]
        if alld and allw:
            return ["/SC", "DAILY", *at]
        if alld:
            return ["/SC", "WEEKLY", "/D", ",".join(DOW[d].upper() for d in sorted(dow)), *at]
        return ["/SC", "MONTHLY", "/D", ",".join(map(str, sorted(dom))), *at]
    raise Unconvertible("shape")


# ------------------------------------------------------------------ headless command per harness (agentj-automation skill)
def headless(kind: str, research: bool, model: str | None = None) -> tuple[str, list, str]:
    """→ (tool, args, how): prompt "in" stdin / "arg" {P}; stdout = the report. research: read-only, no web, no config-file
    settings / MCP (L-a; codex `-c mcp_servers={}` merges = no-op on 0.162.1)."""
    mdl = model if isinstance(model, str) and all(c.isalnum() or c in "-._/" for c in model) else None
    if kind == "codex":     # read-only: Codex's sandbox has no network; its own web search off too
        return "codex", ["exec", "--sandbox", "read-only" if research else "danger-full-access", "-c", "approval_policy=never",
                         *(["-c", 'web_search="disabled"', "--ignore-user-config", "--ignore-rules"] if research else []), "--skip-git-repo-check",
                         *(["-m", mdl] if mdl else []), "-C", "{W}", "-"], "in"
    if kind == "claude":
        mode = ["--permission-mode", "dontAsk", "--strict-mcp-config", "--setting-sources", "", "--allowedTools", "Read,Grep,Glob", "--disallowedTools",
                "Bash,Edit,Write,NotebookEdit,WebFetch,WebSearch"] if research else ["--dangerously-skip-permissions"]
        return "claude", ["-p", *mode, *(["--model", mdl] if mdl else [])], "in"
    if kind == "qwen":
        return "qwen", ["{P}", "--approval-mode", *(["plan", "--exclude-tools", "web_fetch", "web_search"] if research else ["yolo"])], "arg"
    if kind == "opencode" and not research:
        return "opencode", ["run", "--auto", "--dir", "{W}", "{P}"], "arg"
    raise MigrateError("readonly" if kind == "opencode" else "harness")


# ------------------------------------------------------------------ the three schedulers
def unavailable(plat: str | None = None, run=_run) -> str | None:
    plat = plat or sys.platform
    if plat.startswith("linux"):
        return None if shutil.which("systemctl") and not run(["systemctl", "--user", "show-environment"])[0] else "no_systemd"
    tool = {"darwin": "launchctl", "win32": "schtasks"}.get(plat)
    return ("no_tool" if not shutil.which(tool) else None) if tool else "platform"


def names(plat: str, tid: str) -> dict:
    """What a moved task has in the scheduler — from its id and the platform only, never from a state file (M2)."""
    if plat.startswith("linux"):
        u, sc = "agentj-task-" + tid, ["systemctl", "--user"]
        return {"unit": u + ".timer", "dir": os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                                                         "systemd", "user"),
                "files": [u + ".service", u + ".timer"], "start": sc + ["start", u + ".service"], "stop": sc + ["stop", u + ".service"],
                "remove": [sc + ["disable", "--now", u + ".timer"], sc + ["stop", u + ".service"]], "reload": [sc + ["daemon-reload"]]}
    if plat == "darwin":
        label, gui = "agentj.task." + tid, f"gui/{os.getuid()}"
        return {"unit": label, "dir": os.path.expanduser("~/Library/LaunchAgents"), "files": [label + ".plist"], "gui": gui,
                "start": ["launchctl", "kickstart", f"{gui}/{label}"], "stop": ["launchctl", "kill", "TERM", f"{gui}/{label}"],
                "remove": [["launchctl", "bootout", f"{gui}/{label}"]], "reload": []}
    n = "AgentJ\\agentj-task-" + tid
    end = ["schtasks", "/End", "/TN", n]
    return {"unit": n, "dir": None, "files": [], "start": ["schtasks", "/Run", "/TN", n], "stop": end,
            "remove": [end, ["schtasks", "/Delete", "/F", "/TN", n]], "reload": []}


def command(st, tid: str) -> list:
    """This Agent J's own interpreter, isolated (-I), `-m agentj.sysmigrate run` (run_task)."""
    py = os.path.abspath(sys.executable)
    w = os.path.join(os.path.dirname(py), "pythonw.exe")
    return [w if sys.platform == "win32" and os.path.isfile(w) else py, "-I", "-m", "agentj.sysmigrate", "run", tid, os.path.abspath(st.root)]


def backend(plat: str, tid: str, t: dict, cmd: list, tz_local: str | None = None) -> dict:
    """names() + {text {file: content}, install [argv]}. No catch-up after a missed time (L3: like serve, Persistent=false)."""
    b, sched, tz = names(plat, tid), t["schedule"], t["tz"]
    if any(c in x for x in cmd for c in "\n\r\0"):
        raise MigrateError("path")
    if plat.startswith("linux"):
        from .service import _sd_quote
        cal = on_calendar(sched, tz)
        if shutil.which("systemd-analyze") and any(_run(["systemd-analyze", "calendar", c])[0] for c in cal):
            raise Unconvertible("calendar")
        head, (svc, tmr) = f"# {MARK}\n[Unit]\nDescription=Agent J task {tid}\n\n", b["files"]
        b["text"] = {svc: head + "[Service]\nType=oneshot\nExecStart=" + " ".join(map(_sd_quote, cmd)) +
                     f"\nTimeoutStartSec={tasks.RUN_TIMEOUT + 120:.0f}\n",
                     tmr: head + "[Timer]\n" + "".join(f"OnCalendar={c}\n" for c in cal) + "Persistent=false\n\n[Install]\nWantedBy=timers.target\n"}
        b["install"] = [["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", "--now", tmr]]
    elif plat == "darwin":
        f = b["files"][0]
        b["text"] = {f: plistlib.dumps({"Label": b["unit"], "ProgramArguments": cmd, "Comment": MARK,
                                        "StartCalendarInterval": launchd_intervals(sched, tz, tz_local)}).decode()}
        b["install"] = [["launchctl", "bootstrap", b["gui"], os.path.join(b["dir"], f)]]
    else:
        tr = subprocess.list2cmdline(cmd)
        if len(tr) > 261:                       # schtasks /TR limit
            raise MigrateError("path")
        b["text"], b["install"] = {}, [["schtasks", "/Create", "/F", "/TN", b["unit"], *schtasks_schedule(sched, tz, tz_local), "/TR", tr]]
    return b


# ------------------------------------------------------------------ migrate / roll back / unmigrate
def _ours(path: str) -> bool:
    """Missing, or a regular file (never a link) carrying MARK."""
    try:
        fd = os.open(path, os.O_RDONLY | _NF)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    with os.fdopen(fd, "rb") as f:
        return stat.S_ISREG(os.fstat(fd).st_mode) and MARK.encode() in f.read(4096)


def _title(t, tid: str, lang: str) -> str:
    t = t if isinstance(t, dict) else {}
    return str(t.get("en" if lang == "en" else "zh") or t.get("zh") or tid)[:60]


def _frozen(e: dict, tsha: str) -> tuple[dict, bytes]:
    """H2-R: task.json + instructions read once; these bytes must hash to the enabling's tsha and become the frozen text."""
    if os.path.lexists(os.path.join(e["dir"], "brief.json")):
        raise MigrateError("brief")
    raw = tasks._regular(os.path.join(e["dir"], "task.json")) or b""
    try:
        t = json.loads(raw)
        body = tasks._regular(os.path.join(e["dir"], t["prompt_file"]))
    except (ValueError, TypeError, KeyError):
        body = None
    if body is None or tasks.contract_sha(raw, body) != tsha:
        raise MigrateError("stale")
    return {**e, "task": t}, body


def _plan(st, e: dict, cfg: dict, plat: str, tz_local=None, tsha: str | None = None) -> dict:
    if e["task"].get("steps"):
        raise MigrateError("steps")
    tsha = tsha or e["tsha"]
    e, raw = _frozen(e, tsha)
    t = e["task"]
    research = t["mode"] == "research"
    tool, args, how = headless(cfg["kind"], research, cfg.get("model"))
    from .binaries import resolve
    found = resolve(tool)
    if not found["path"]:
        raise MigrateError("harness")
    body = tasks.prompt_for(e, research, body=raw)
    text = (f"<!-- {MARK} -->\n" + body[:body.rfind("\n\n---\n")] + "\n\n---\n回答的最后一行必须是 `VERDICT: PASS <一句话>` 或 "
            "`VERDICT: FAIL <原因>`。/ The last line of your answer must be `VERDICT: PASS <one line>` or `VERDICT: FAIL <reason>`.\n")
    from .taskspec import secret_like
    if secret_like(text, owner=True):
        raise MigrateError("secret")
    fenced = bool(cfg.get("fence", True))
    if fenced:
        from . import fence
        if fence.problem(st, e["dir"]):
            raise MigrateError("fence")
    try:
        b = backend(plat, e["id"], t, command(st, e["id"]), tz_local)
    except (Unconvertible, ValueError):
        raise MigrateError("schedule") from None
    if b["dir"] and not all(_ours(os.path.join(b["dir"], f)) for f in b["files"]):
        raise MigrateError("exists")
    b.update(research=research, prompt=text.encode(), tsha=tsha, psha=_sha(text.encode()), argv=[found["path"], *args], how=how,
             xdg=os.environ.get("XDG_CONFIG_HOME"),
             bindirs=os.pathsep.join(dict.fromkeys(os.path.dirname(x) for x in (found.get("requested"), found["path"]) if x)),
             title=t.get("title"), kind=cfg["kind"], fence=fenced, docker=bool(cfg.get("docker")), wd=cfg["dir"])
    return b


def _rollback(st, tid: str, plat: str, run, prev: dict | None = None) -> list[str]:
    """Scheduler entry off, our unit files and the frozen instructions removed (runs/ kept), `prev` written back."""
    b = names(plat, tid)
    bad = [" ".join(a[:3]) for a in b["remove"] if run(a)[0]]
    for f in b["files"]:
        p = os.path.join(b["dir"], f)
        if os.path.lexists(p):
            try:
                if not _ours(p):
                    raise OSError
                os.unlink(p)
            except OSError:
                bad.append(f)
    try:
        d = _dir(st.root, "sysmigrate", tid)
        try:
            p, kw = _at(d, "PROMPT.md")
            with contextlib.suppress(FileNotFoundError):
                os.unlink(p, **kw)
        finally:
            _close(d)
    except OSError:
        bad.append("PROMPT.md")
    bad += [" ".join(a[:3]) for a in b["reload"] if run(a)[0]]
    if prev:
        tasks.update(st, lambda d: d["on"].__setitem__(tid, prev))
    return bad


def _verdict(st, tid: str, t0: float) -> str | None:
    last = load(st)["tasks"].get(tid, {}).get("last") or {}
    return last.get("verdict") if last.get("at", 0) >= t0 - 1 else None


def migrate(st, ids: list | None = None, dry: bool = False, plat: str | None = None, run=_run, lang: str = "zh",
            tz_local: str | None = None) -> list[dict]:
    """→ one row per task {id, title, result: migrated | rolled_back | rollback_failed | skipped | plan, reason, unit, verdict}."""
    from . import activity, controls
    plat, cfg = plat or sys.platform, st.agent_config()
    if not cfg:
        raise MigrateError("no_agent")
    if controls.estop_state(st)["on"] and not dry:
        raise MigrateError("stopped")
    if r := unavailable(plat, run):
        raise MigrateError(r)
    wd = cfg["dir"]
    if ids is None:
        done = migrated(st)
        ids = [x["id"] for x in tasks.rows(st, wd) if x["enabled"] and x["id"] not in done]
    out = []
    for tid in ids:
        res = {"id": tid}
        out.append(res)
        rows = {x["id"]: x for x in tasks.rows(st, wd)}      # per task: an earlier test run may have changed this one (TOCTOU)
        res["title"] = (rows.get(tid) or {}).get("title")
        try:
            if tid in migrated(st) or tid not in rows or not rows[tid]["enabled"]:
                raise MigrateError("migrated" if tid in migrated(st) else "unknown" if tid not in rows else
                                   "stale" if rows[tid]["stale"] else "not_enabled")
            if any(v.get("id") == tid and v.get("status") in ("queued", "running") for v in tasks.load(st)["runs"].values()):
                raise MigrateError("running")
            b = _plan(st, tasks.find(wd, tid), cfg, plat, tz_local, rows[tid]["tsha"])
        except MigrateError as err:
            res.update(result="skipped", reason=err.reason)
            continue
        res.update(unit=b["unit"], research=b["research"], result="plan")
        if dry:
            res.update(files=[str(st.root / "sysmigrate" / tid / "PROMPT.md")] + [os.path.join(b["dir"], f) for f in b["text"]],
                       install=b["install"], start=b["start"])
            continue
        prev = tasks.load(st)["on"].get(tid)
        rec = {k: b[k] for k in ("unit", "tsha", "psha", "argv", "how", "bindirs", "title", "kind", "fence", "docker", "wd", "research",
                                 "xdg")}
        _update(st, lambda m: m["tasks"].__setitem__(tid, {**rec, "at": int(time.time()), "result": "migrating", "prev": prev}))
        reason = verdict = None
        t0 = time.time()
        try:
            d = _dir(st.root, "sysmigrate", tid)
            try:
                _put(d, "PROMPT.md", b["prompt"])
            finally:
                _close(d)
            if b["text"]:
                os.makedirs(b["dir"], exist_ok=True)
                d = _dir(b["dir"])
                try:
                    for f, text in b["text"].items():
                        _put(d, f, text.encode(), 0o644)
                finally:
                    _close(d)
            if any(run(a)[0] for a in b["install"]):
                raise MigrateError("install")
            tasks.set_enabled(st, wd, tid, False, "migrate")
            end = time.monotonic() + RUN_WAIT
            failed = run(b["start"], RUN_WAIT)[0]
            while (verdict := _verdict(st, tid, t0)) is None and not failed and time.monotonic() < end:
                time.sleep(2)              # launchd / Task Scheduler return at once; systemd's oneshot start waits
            if verdict is None or not verdict.startswith("VERDICT: PASS"):
                raise MigrateError("start" if verdict is None else "verdict")
        except (MigrateError, OSError, tasks.TaskError) as err:
            reason = getattr(err, "reason", "install")
        if reason:
            bad = _rollback(st, tid, plat, run, prev)
            res.update(result="rollback_failed" if bad else "rolled_back", reason=reason, left=bad)
        else:
            res["result"] = "migrated"
        res["verdict"] = verdict
        _update(st, lambda m: m["tasks"][tid].update(result=res["result"], reason=reason, verdict=verdict))
        st.log("task_migrate", id=tid, result=res["result"], reason=reason)
        activity.record(st, "task_on" if reason else "task_off", id=tid, text=verdict,
                        by=pick(lang, "迁移失败已退回", "move rolled back") if reason else pick(lang, "改用系统定时", "system scheduler"))
    return out


def unmigrate(st, tid: str, run=_run, plat: str | None = None) -> dict:
    """Scheduler entry off, files removed; the task stays OFF, whoever asks (H5-R: `agentj tasks enable` brings it back)."""
    rec = load(st)["tasks"].get(tid) if _ok_id(tid) else None
    if not rec or rec.get("result") not in ("migrated", "migrating", "rollback_failed"):
        raise MigrateError("not_migrated")
    bad = _rollback(st, tid, plat or sys.platform, run)
    _update(st, lambda m: m["tasks"][tid].update(result="rollback_failed" if bad else "unmigrated"))
    st.log("task_migrate", id=tid, result="unmigrated", reason="left" if bad else None)
    return {"id": tid, "title": rec.get("title"), "unit": rec.get("unit"), "result": "unmigrated", "left": bad}


def halt(st, run=_run) -> None:
    """serve's emergency stop: a moved task's current run ends (M1-R). Windows: `/End` kills run_task, not its harness (no
    Job Object) — run_task's 2 s estop poll ends it there (M1-R2)."""
    for tid in migrated(st) if sys.platform != "win32" else ():
        if run(names(sys.platform, tid)["stop"])[0]:
            st.log("task_migrate_stop", id=tid, result="error")


def remove_all(st, run=_run) -> int:
    """`agentj service uninstall`: every moved task off, <state>/sysmigrate and the card's decision gone."""
    n = 0
    for tid, rec in load(st)["tasks"].items():
        if rec.get("result") in ("migrated", "migrating", "rollback_failed"):
            with contextlib.suppress(MigrateError, OSError, tasks.TaskError):
                unmigrate(st, tid, run)
                n += 1
    shutil.rmtree(st.root / "sysmigrate", ignore_errors=True)
    with contextlib.suppress(OSError):
        _update(st, lambda m: (m.pop("decision", None), m.__setitem__("offers", {})))
    return n


# ------------------------------------------------------------------ one scheduled run (the scheduler → run_task)
def run_task(st, tid: str) -> int:
    """`python -m agentj.sysmigrate run <id> <state>`: outside the fence; the harness inside it. 0 = VERDICT: PASS."""
    from . import controls, fence
    rec = load(st)["tasks"].get(tid) if _ok_id(tid) else None
    if not rec or rec.get("result") not in ("migrating", "migrated"):
        return 2
    if rec.get("xdg"):      # QA L1: hide serve's preferences, not this environment's
        os.environ["XDG_CONFIG_HOME"] = rec["xdg"]
    d = _dir(st.root, "sysmigrate", tid)
    e = prompt = csha = None
    try:
        if controls.estop_state(st)["on"]:
            bad = "stopped"
        else:
            prompt = _get(d, "PROMPT.md")
            with contextlib.suppress(tasks.TaskError):
                e = tasks.find(rec["wd"], tid)
            bad = ("changed" if not e or e["tsha"] != rec["tsha"] or prompt is None or _sha(prompt) != rec["psha"] else
                   "fence" if rec["fence"] and fence.problem(st, e["dir"]) else None)
            csha = f"{e and e['tsha']}:{prompt and _sha(prompt)}"
        r = _dir(st.root, "sysmigrate", tid, "runs")
    finally:
        _close(d)
    stamp = time.strftime("%Y-%m-%d-%H%M%S") + f"-{os.getpid()}"
    try:
        _put(r, stamp + ".md", b"")
        _put(r, stamp + ".log", b"")
        p, kw = _at(r, stamp + ".md")
        rep = os.open(p, os.O_RDWR | os.O_APPEND | _NF, **kw)
        p, kw = _at(r, stamp + ".log")
        log = os.open(p, os.O_WRONLY | os.O_APPEND | _NF, **kw)
        rc = None
        if not bad:
            argv = [rec["argv"][0]] + [{"{W}": e["dir"], "{P}": prompt.decode("utf-8", "replace")}.get(x, x) for x in rec["argv"][1:]]
            if rec["fence"]:      # the task's own fence: credentials and Agent J's state invisible; no phone approvals
                argv = fence.wrap(st, argv, e["dir"], allow_docker=rec["docker"])
            env = fence.launch_environment({**os.environ, "PATH": rec["bindirs"] + os.pathsep + os.environ.get("PATH", "")},
                                           rec["docker"], rec["kind"])
            try:
                rc, bad = _supervise(st, argv, e["dir"], env, prompt if rec["how"] == "in" else None, rep, log)
            except OSError:
                rc = 127
            for f in (rep, log):
                if os.fstat(f).st_size > MAX_REPORT:
                    os.ftruncate(f, MAX_REPORT)
        os.lseek(rep, 0, os.SEEK_SET)
        raw = os.read(rep, MAX_REPORT)
        lines = [x.strip() for x in raw.decode("utf-8", "replace").splitlines() if x.strip()]
        verdict = lines[-1][:300] if not bad and lines and lines[-1].startswith(("VERDICT: PASS", "VERDICT: FAIL")) else \
            "VERDICT: FAIL " + (why(bad) if bad else f"exit {rc}")
        if not lines or lines[-1][:300] != verdict:
            os.write(rep, (("\n" if raw[-1:] not in b"\n" else "") + verdict + "\n").encode())
        os.close(rep)
        os.close(log)
        old = sorted(os.listdir(r))[:-KEEP]
        for f in old:
            p, kw = _at(r, f)
            with contextlib.suppress(OSError):
                os.unlink(p, **kw)
    finally:
        _close(r)
    _update(st, lambda m: m["tasks"].get(tid, {}).update(last={"at": time.time(), "verdict": verdict, "why": bad, "file": stamp,
                                                               "csha": csha}))
    if rec["result"] == "migrated" and bad != "stopped":
        from . import names as ctl
        with contextlib.suppress(Exception):    # serve tells the phone (it re-reads this file; nothing is taken from here)
            ctl.ctl_call(st, {"cmd": "task_migrated_ran", "id": tid}, 5)
    return 0 if verdict.startswith("VERDICT: PASS") else 1


def _supervise(st, argv, wd, env, stdin, rep, log) -> tuple:
    """The harness in its own process group; estop (M1-R), size (L-d) or timeout kill it. → (rc, "stopped" | None)."""
    from .controls import estop_state
    hit, old = [], {}
    for s in {signal.SIGTERM, getattr(signal, "SIGBREAK", signal.SIGTERM)}:    # M1-R2: launchctl kill TERM / systemctl stop
        with contextlib.suppress(ValueError):                                   # (main thread only)
            old[s] = signal.signal(s, lambda *_: hit.append(1))
    try:
        with tempfile.TemporaryFile() as f:
            f.write(stdin or b"")
            f.seek(0)
            p = subprocess.Popen(argv, cwd=wd, env=env, stdin=f if stdin is not None else subprocess.DEVNULL, stdout=rep, stderr=log,
                                 start_new_session=True)
        end = time.monotonic() + tasks.RUN_TIMEOUT
        while True:
            with contextlib.suppress(subprocess.TimeoutExpired):
                return p.wait(2), "stopped" if hit else None
            stop = hit or estop_state(st)["on"]
            rc = "size" if os.fstat(rep).st_size + os.fstat(log).st_size > MAX_REPORT else "timeout" if time.monotonic() > end else None
            if stop or rc:
                with contextlib.suppress(OSError, subprocess.SubprocessError):    # M1-R3: Windows .cmd shims → whole tree
                    os.killpg(p.pid, signal.SIGKILL) if hasattr(os, "killpg") else subprocess.run(
                        ["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, timeout=10, check=False)
                with contextlib.suppress(OSError):
                    p.kill()
                p.wait()
                return rc, "stopped" if stop else None
    finally:
        for s, h in old.items():
            signal.signal(s, h)


def ran_notice(host, tid) -> None:
    """serve's `task_migrated_ran`: one notice per scheduled run of a moved task, from <state> only."""
    rec = load(host.st)["tasks"].get(tid) if _ok_id(tid) else None
    last, lang = (rec or {}).get("last") or {}, getattr(host, "lang", "zh")
    told = host.__dict__.setdefault("_sysmigrate_told", {})       # each run once, a content change once (QA L2)
    key = last.get("csha") if last.get("why") == "changed" else last.get("at")
    if not last or rec.get("result") != "migrated" or last.get("why") == "stopped" or told.get(tid) == key:
        return
    told[tid] = key
    from .privacy import owner_text
    title, v = _title(rec.get("title"), tid, lang), str(last.get("verdict", ""))
    ok = v.startswith("VERDICT: PASS")
    host.task_notice(pick(lang, f"定时任务「{title}」没有运行：{why('changed')}。", f'Scheduled task "{title}" did not run: '
                          f"{_reason({'reason': 'changed'}, 'en')}.") if last.get("why") == "changed" else
                     pick(lang, f"定时任务「{title}」（系统定时）" + ("通过：" if ok else "没有通过："),
                          f'Scheduled task "{title}" (system scheduler) ' + ("passed: " if ok else "did not pass: ")) +
                     owner_text(v[13:].strip(" :—-"))[:300])


# ------------------------------------------------------------------ the result notice, the confirm card
def _reason(r: dict, lang: str) -> str:
    if r.get("reason") == "verdict":
        v = str(r.get("verdict") or "")
        s = v[13:].strip(" :—-") if v.startswith("VERDICT: FAIL") else ""
        if not s or s.startswith("exit "):
            return pick(lang, "试跑没有通过：AI 工具出错退出", "the test run did not pass: the AI tool stopped with an error")
        from .privacy import owner_text
        return pick(lang, "试跑没有通过：", "the test run did not pass: ") + owner_text(s)[:80]
    w = why(str(r.get("reason")), lang)
    return w[:1].lower() + w[1:] if lang == "en" else w


def summary(rows: list, lang: str) -> str:
    sep = pick(lang, "、", ", ")
    ok = [_title(r.get("title"), r["id"], lang) for r in rows if r["result"] == "migrated"]
    back = [_title(r.get("title"), r["id"], lang) + pick(lang, f"（{_reason(r, lang)}）", f" ({_reason(r, lang)})")
            for r in rows if r["result"] != "migrated"]
    s = pick(lang, f"已改用系统定时 {len(ok)} 个：", f"Moved {len(ok)} to the system scheduler: ") + sep.join(ok) if ok else \
        pick(lang, "没有任务改用系统定时", "Nothing moved to the system scheduler")
    if back:
        s += pick(lang, f"。{len(back)} 个照旧由 Agent J 运行：", f". {len(back)} stay{'s' * (len(back) == 1)} in Agent J: ") + sep.join(back)
    if any(r.get("left") for r in rows):
        s += pick(lang, "。退回时有东西没清理掉，见 agentj tasks list", ". A rollback left something behind; see agentj tasks list")
    if ok and sys.platform.startswith("linux") and "Linger=no" in _run(["loginctl", "show-user", os.environ.get("USER", ""),
                                                                         "--property=Linger"])[1]:
        s += pick(lang, "。没登录 Agent J 主机时不会跑（和原来一样）", ". They run only while you're logged in, as before")
    return s + pick(lang, "。", ".")


def _short(s: str, n: int = 24) -> str:
    """Cut at a word, else at n characters, + 「…」."""
    cut = s[:n + 1]
    return s if len(s) <= n else (cut[:cut.rfind(" ")] if " " in cut else s[:n]).rstrip() + "…"


def card_text(titles: list, lang: str, fenced: bool = True, again: bool = True) -> str:
    n, sep = len(titles), pick(lang, "、", ", ")
    names_ = sep.join(map(_short, titles[:3])) + (pick(lang, f"等 {n} 个", f" and {n - 3} more") if n > 3 else "")
    return pick(lang, f"Agent J 主机 · 定时任务改用系统定时\n把 {n} 个定时任务改用 Agent J 主机自己的系统定时？{names_}\n迁过去后按时自己跑，不经手机审批："
                      "非只读任务以完全权限运行，平时要 Face ID 的步骤（花钱、删数据等）也不再问。" + "Agent J 的状态和密钥对它们仍不可见；" * fenced +
                      f"急停能停下它们。每个任务马上试跑一次，失败自动退回。\n批准＝现在迁（{n} 个）；拒绝＝不迁" + "，以后别问" * again + "。",
                f"Agent J host · Move to the system scheduler\nMove {n} scheduled task{'s' * (n != 1)} to the Agent J host's own scheduler? {names_}\n"
                "Once moved they run on time without phone approvals: tasks that aren't read-only run with full access, and steps that "
                "normally need Face ID (spending, deleting data…) aren't asked. " +
                ("Agent J's state and keys stay hidden from them; the" if fenced else "The") + " emergency stop stops them. Each runs "
                f"once now as a test; a failure goes back as it was.\nApprove = move now ({n}); Deny = don't move" + ", don't ask again" * again + ".")


def candidates(st, plat: str | None = None, run=_run) -> list[dict]:
    try:
        return [r for r in migrate(st, dry=True, plat=plat, run=run) if r["result"] == "plan"]
    except MigrateError:
        return []


async def offer(host, plat: str | None = None, run=_run) -> str:
    """Started with the scheduler: the owner is asked once (≤ MAX_OFFERS unanswered cards per version). Other host tests
    (AGENTJ_TEST) never see it."""
    if os.environ.get("AGENTJ_TEST") and not os.environ.get("AGENTJ_TEST_MIGRATE_CARD"):
        return "test"
    try:
        return await _offer(host, plat, run)
    except Exception as e:  # noqa: BLE001 — never reaches serve
        host.st.log("task_migrate_card", result="error", reason=type(e).__name__)
        return "error"


async def _card(host, rows: list, lang: str, sent=None) -> str:
    """The one confirm card (P570c: a plain allow / deny, no Face ID) → yes (a paired device's signed allow, logged) | no (a
    device declined) | none | pending. `sent`: ctl's "card is out" line (QA L3); its card never says "don't ask again" (L-g)."""
    from .policy_registry import HOST_CARD
    if host.__dict__.get("_sysmigrate_card"):
        return "pending"
    host._sysmigrate_card = True
    try:
        if sent:
            host._sysmigrate_ctl = time.monotonic() + 86400
            await sent()
        ans = await host.ask(TOOL, {"summary": card_text([_title(r.get("title"), r["id"], lang) for r in rows], lang,
                                                         bool((host.agent_cfg or {}).get("fence", True)), not sent)},
                             batch=False, grant_info=True, _company_proposal=HOST_CARD)
    finally:
        host._sysmigrate_card = False
    res = ("yes" if ans.get("behavior") == "allow" and isinstance(ans.get("grant_info"), dict) else
           "no" if ans.get("behavior") == "deny" and ans.get("message") in DECLINED else "none")
    host.st.log("task_migrate_card", result=res, count=len(rows))
    if res != "none":
        await asyncio.to_thread(_update, host.st, lambda m: m.__setitem__("decision", {"answer": res, "at": int(time.time())}))
    return res


async def _offer(host, plat, run) -> str:
    from . import __version__ as v
    st, lang = host.st, getattr(host, "lang", "zh")
    while True:
        m = await asyncio.to_thread(load, st)
        if m.get("decision") or m["offers"].get(v, 0) >= MAX_OFFERS:
            return "decided" if m.get("decision") else "limit"
        rows = await asyncio.to_thread(candidates, st, plat, run) if host.agent_cfg else []
        if not rows:
            if not await asyncio.to_thread(_wsl_due, st, plat, run, v):
                return "none"
            host.task_notice(pick(lang, *WSL), guide="wsl")
            return "wsl"
        while host.stopped() or not host._online_approvers():
            await asyncio.sleep(5)
        await asyncio.to_thread(_update, st, lambda m: m["offers"].__setitem__(v, m["offers"].get(v, 0) + 1))
        ans = await _card(host, rows, lang)
        if ans == "yes":
            try:
                text = summary(await asyncio.to_thread(migrate, st, [r["id"] for r in rows], False, plat, run, lang), lang)
            except MigrateError as err:
                text = why(err.reason, lang)
            host.task_notice(text)
        if ans in ("yes", "no"):
            return ans
        await asyncio.sleep(REOFFER)


def wsl() -> bool:
    try:
        return bool(os.environ.get("WSL_DISTRO_NAME")) or "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def _wsl_due(st, plat, run, v) -> bool:
    """P570c (co-34e3d1): WSL2 without systemd and movable tasks → one plain notice per version, never silence."""
    if load(st).get("wsl") == v or unavailable(plat, run) != "no_systemd" or not wsl() or \
            not candidates(st, plat, lambda *a, **k: (0, "")):
        return False
    _update(st, lambda m: m.__setitem__("wsl", v))
    return True


WSL = ("这台 Agent J 主机是 WSL2，没开 systemd，定时任务只能由 Agent J 运行，你不在时可能卡在审批上。要改用系统定时，直接回复"
       "「帮我开启 WSL 的 systemd」，或点下面的按钮：Agent 会在 /etc/wsl.conf 里打开它（手机上会弹管理员密码卡），再请你在 Windows 里运行 "
       "wsl --shutdown 后重新打开；开好后会自动弹出迁移卡。",
       "This Agent J host is WSL2 without systemd, so scheduled tasks can only run in Agent J and may wait on an "
       "approval while you're away. To use the system scheduler, reply \"turn on systemd in WSL\": the Agent turns it "
       "on in /etc/wsl.conf (an admin password card comes to the phone), then you run wsl --shutdown in Windows and "
       "open it again; the move card then comes by itself. Tap the button below to ask for help.")


def mark(st, rows: list) -> list:
    """The phone's task list: `sys` = migrated | changed (moved, but the task changed since: its runs refuse)."""
    recs = load(st)["tasks"]
    for r in rows:
        if (rec := recs.get(r["id"]) or {}).get("result") == "migrated":
            r["sys"] = "migrated" if rec.get("tsha") == r.get("tsha") else "changed"
    return rows


def again(st, tid: str, tsha, by: str, lang: str = "zh") -> tuple[list, str]:
    """The phone's signed `task_migrate` (the owner's own tap; no card): (re)move one task. A failure leaves it as it was
    in Agent J (off unless it was on there)."""
    from . import controls
    cfg = st.agent_config()
    r = "no_agent" if not cfg else "stopped" if controls.estop_state(st)["on"] else unavailable()
    if r:
        raise MigrateError(r)
    row = {x["id"]: x for x in tasks.rows(st, cfg["dir"])}.get(tid)
    if not row:
        raise MigrateError("unknown")
    if row["tsha"] != tsha:
        raise MigrateError("reload")
    if tid in migrated(st):
        unmigrate(st, tid)
    rows = [{"id": tid, "title": row["title"], "result": "skipped", "reason": "reload"}]
    try:
        if not row["enabled"]:
            tasks.set_enabled(st, cfg["dir"], tid, True, by, tsha)
        rows = migrate(st, [tid], lang=lang)
    except tasks.TaskError:
        pass
    finally:
        if rows[0]["result"] != "migrated" and not row["enabled"]:
            with contextlib.suppress(tasks.TaskError, OSError):
                tasks.set_enabled(st, cfg["dir"], tid, False, by)
    return rows, summary(rows, lang)


async def phone(host, s, r: str, t: dict) -> None:
    """serve's phone `task_migrate`: the result (summary) as ctl_res + one notice; the test run may take minutes."""
    lang = getattr(host, "lang", "zh")
    try:
        if host.__dict__.get("_sysmigrate_card"):
            raise MigrateError("pending")
        host._sysmigrate_card = True
        try:
            await host.send_app(s, {"t": "ctl_pending", "r": r, "action": "task_migrate", "timeout_ms": PENDING_MS})
            rows, text = await asyncio.to_thread(again, host.st, t["id"], t["tsha"], "phone:" + s.device, lang)
        finally:
            host._sysmigrate_card = False
    except (MigrateError, OSError) as e:
        return await host._ctl_res(s, r, "task_migrate", False, why=why(getattr(e, "reason", "install"), lang))
    host.task_notice(text)
    with contextlib.suppress(AttributeError):
        host.scheduler.wake.set()
    await host._ctl_res(s, r, "task_migrate", rows[0]["result"] == "migrated", text=text)


# ------------------------------------------------------------------ serve's control socket, `agentj tasks migrate`
async def ctl(host, req: dict, sent=None) -> dict:
    """serve's `tasks_migrate`: whoever asks, a move needs the same card (H5-R); after a decline ≤ 1 card / 24 h (L-g)."""
    lang = req.get("lang") if req.get("lang") in ("zh", "en") else getattr(host, "lang", "zh")
    ids = req.get("ids")
    if ids is not None and not (isinstance(ids, list) and all(_ok_id(x) for x in ids)):
        return {"ok": False, "error": "unknown"}
    try:
        rows = await asyncio.to_thread(migrate, host.st, ids, True, None, _run, lang)
        if plan := [r for r in rows if r["result"] == "plan"]:
            no = time.monotonic() < host.__dict__.get("_sysmigrate_ctl", 0) and (
                (await asyncio.to_thread(load, host.st)).get("decision") or {}).get("answer") == "no"
            ans = ("stopped" if host.stopped() else "no_remote" if not host._online_approvers() else "declined" if no else
                   await _card(host, plan, lang, sent or (lambda: asyncio.sleep(0))))
            if ans != "yes":
                raise MigrateError(ans)
            rows = [r for r in rows if r["result"] != "plan"] + await asyncio.to_thread(
                migrate, host.st, [r["id"] for r in plan], False, None, _run, lang)
    except MigrateError as err:
        return {"ok": False, "error": err.reason}
    with contextlib.suppress(AttributeError):
        host.scheduler.wake.set()
    return {"ok": True, "rows": rows}


def hint(tid: str, lang: str, new: bool = False) -> str:
    return pick(lang, "更稳的做法：" + f"启用（agentj tasks enable {tid}）后用 " * new + f"`agentj tasks migrate {tid}` 改用系统定时（主人不在时不会卡在审批上）",
                "More reliable: " + f"once enabled (agentj tasks enable {tid}), " * new + f"`agentj tasks migrate {tid}` moves it to "
                "the system scheduler (it won't wait on an approval while you're away)")


def list_note(st, rows: list, lang: str) -> tuple[str | None, dict]:
    """`agentj tasks list`: why enabled tasks stay in Agent J — the host (no scheduler) and per task (last move)."""
    recs, out = load(st)["tasks"], {}
    for r in rows:
        if r["enabled"] and (rec := recs.get(r["id"])) and rec.get("result") in ("rolled_back", "rollback_failed"):
            out[r["id"]] = pick(lang, "留在 Agent J：", "stays in Agent J: ") + _reason(rec, lang)
    u = unavailable() if any(r["enabled"] for r in rows) else None
    return (pick(lang, "定时任务由 Agent J 运行：", "Scheduled tasks run in Agent J: ") + why(u, lang) if u else None), out


def cli(st, a, lang: str) -> None:
    from . import names as ctl_
    if not a.id and not (a.mode == "migrate" and a.all):
        sys.exit(pick(lang, "要给任务 id 或 --all", "Give a task id or --all"))
    try:
        if a.mode == "unmigrate":            # always off (H5-R); serve only re-reads tasks.json
            rows = [unmigrate(st, a.id)]
            with contextlib.suppress(Exception):
                ctl_.ctl_call(st, {"cmd": "tasks_changed"}, 10)
        else:
            ids = None if a.all else [a.id]
            rows = migrate(st, ids, True, lang=lang)
            if not a.dry_run and any(r["result"] == "plan" for r in rows):
                try:
                    res = ctl_.ctl_call(st, {"cmd": "tasks_migrate", "lang": lang, "ids": ids}, 24 * 3600, max_bytes=1 << 20,
                                        progress=lambda: print(pick(lang, "已发到手机：在卡片上点确认后才迁（每个任务马上试跑一次，失败自动退回）……",
                             "Sent to the phone: it moves once you confirm on the card (each runs once as a test; a "
                             "failure goes back)…"), file=sys.stderr, flush=True))
                except ctl_.ServeBusy:
                    res = None
                if not res or not res.get("ok"):
                    raise MigrateError(str((res or {}).get("error") or "no_serve"))
                rows = res["rows"]
    except MigrateError as err:
        sys.exit("✗ " + why(err.reason, lang))
    if a.json:
        return print(json.dumps(rows, ensure_ascii=False, indent=1))
    for r in rows:
        res, u = r["result"], r.get("unit", "")
        line = (f"· → {u} (" + pick(lang, "只读" if r["research"] else "完全权限", "read-only" if r["research"] else "full access") + ")"
                if res == "plan" else f"✓ → {u}" if res == "migrated" else
                f"✓ {u} " + pick(lang, f"已撤下，任务停用了（要 Agent J 照旧运行：agentj tasks enable {r['id']}）",
                                 f"removed; the task is off (to run it in Agent J: agentj tasks enable {r['id']})")
                if res == "unmigrated" else ("↩ " if res == "rolled_back" else "✗ ") + _reason(r, lang))
        print(f"  {r['id']:<20} {line}" + "".join(f" · {x}" for x in r.get("left") or ()))
    if a.mode == "migrate" and not a.dry_run:
        print(summary(rows, lang))


if __name__ == "__main__":      # the scheduler's entry: python -I -m agentj.sysmigrate run <id> <state>
    if len(sys.argv) == 4 and sys.argv[1] == "run":
        from .state import State
        try:
            sys.exit(run_task(State(Path(sys.argv[3])), sys.argv[2]))
        except OSError:       # e.g. a planted symlink in <state>/sysmigrate: refused, no traceback in the journal
            sys.exit(3)
    sys.exit(2)
