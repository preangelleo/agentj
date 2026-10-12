"""P150 在办事项: who is working in the background under this Agent's watch (PROTOCOL §22, wire `{"t":"busy"}`).

The phone shows one coloured dot per item at the right of the reply card's header; a tap opens the centred dialog
「有 N 个 Agent 在后台干活，<Agent 名> 正在替你监控」 with who · what. Nothing here starts, stops or approves work —
the list is display only, and an empty list sends `tasks: []` (the dots disappear).

Sources (all folded by `Board` in serve):
- the owned Claude session's stream-json (agent.py): an `Agent`/`Task` tool_use = a sub-agent (its description is
  the label), its tool_result ends it, unless it went to the background; then the `task_notification` ends it;
- shared Claude sessions' hooks (shared.py): PreToolUse(Agent|Task) remembers the description, SubagentStart /
  SubagentStop open / close the item, SessionEnd and Stop.background_tasks reconcile it;
- a workflow run (tasks.py): its CEO (the workflow's title) is busy for the length of the run;
- outside dispatches: `agentj busy run --who 掌枢 --label 发布收尾 -- claude -p …` (an item file with the wrapper's
  pid), `agentj busy start|stop`, and active systemd user units named `agentj-p<digits>…` (Description
  "who · what" / "who: what"). Item files live in <state>/agentperm/busy/ (0600), are display text only and are pruned when
  their pid is gone or after ITEM_TTL.

Labels are squeezed without a model (`short`): Chinese ≤ 5 characters, otherwise ≤ 10 characters on word bounds.
Colours: index c (0..8) into Leo's Heavy/Light pairs (web app.css --busy-h<c>/--busy-l<c>); a named worker keeps the
same index every time (<state>/busy/colors.json), and items on screen never share one while ≤ 9 are running.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

COLORS = 9
MAX_ITEMS = 12                 # on the wire; the phone shows up to 6 dots and "+n"
ITEM_TTL = 12 * 3600           # an item file without a live pid (or a lost close) is gone after this long
TEMP_TTL = 3 * 3600            # a sub-agent whose close never arrived
PENDING_TTL = 120              # PreToolUse(Agent) description waiting for its SubagentStart
WHO_MAX = 24
GENERIC = {"", "general-purpose", "general", "explore", "plan", "claude", "fork", "task", "agent", "default"}
AGENT_TOOLS = ("Agent", "Task")
UNIT_RE = re.compile(r"^agentj-p\d+[\w.-]*\.service$")
_CJK = re.compile(r"[㐀-鿿豈-﫿]")
_CLAUSE = re.compile(r"[，,。.;；:：!！?？、()（）\[\]【】\n]|\s[—–-]\s")
_CTRL = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|[\x00-\x1f\x7f]")
_TAIL = {"the", "a", "an", "of", "for", "to", "and", "in", "on", "with", "at", "by", "from", "&"}


def clean(s, n: int) -> str:
    return _CTRL.sub("", s).strip()[:n] if isinstance(s, str) else ""


def short(text) -> str:
    """The label: Chinese ≤ 5 characters (the first clause, CJK / letters / digits kept), otherwise ≤ 10 characters
    of whole words (a single longer word is cut at 10). No model, no network."""
    s = clean(text, 400)
    if not s:
        return ""
    first = next((p.strip() for p in _CLAUSE.split(s) if p and p.strip()), s)
    if _CJK.search(first):
        kept = [ch for ch in first if _CJK.match(ch) or ch.isalnum()]
        return "".join(kept[:5])
    out = ""
    for w in first.split():
        w = w.strip("\"'`*_")
        if not w:
            continue
        nxt = (out + " " + w) if out else w
        if len(nxt) > 10:
            break
        out = nxt
    words = out.split()
    while len(words) > 1 and words[-1].lower() in _TAIL:
        words.pop()
    return " ".join(words) or first.split()[0][:10]


def who_of(agent_type) -> str | None:
    """A named sub-agent keeps its name; a generic one (general-purpose, Explore, …) is the phone's 「临时助理」 (None)."""
    s = clean(agent_type, WHO_MAX)
    return None if s.lower() in GENERIC else s


def pidns() -> str:
    """This process's PID namespace (the fenced Agent has its own: its pids mean nothing to serve)."""
    try:
        return os.readlink("/proc/self/ns/pid")[:64]
    except OSError:
        return ""


HEARTBEAT = 30                 # `agentj busy run` in another PID namespace touches its file this often …
STALE = 120                    # … and serve drops it after this long without one


def item(key: str, who, kind: str, label="", lk=None, since=None, pid=None, session=None, ns=None) -> dict:
    d = {"key": key, "who": clean(who, WHO_MAX) or None, "kind": "ceo" if kind == "ceo" else "temp",
         "label": short(label) if label else "", "since": int(since or time.time())}
    if lk in ("schedule", "dispatch"):
        d["lk"] = lk
    if isinstance(pid, int) and pid > 0:
        d["pid"] = pid
        if isinstance(ns, str) and ns:
            d["ns"] = ns[:64]
    if session:
        d["session"] = clean(session, 80)
    return d


# ------------------------------------------------------------------------------------------- item files (outside)
def busy_dir(root) -> Path:
    """serve's own memory (colors.json)."""
    return Path(root) / "busy"


def items_dir(root) -> Path:
    """Item files: under agentperm/, the one part of the state dir the fenced Agent sees (fence.py), so `agentj busy run`
    from inside the Agent's sandbox lands where serve reads."""
    return Path(root) / "agentperm" / "busy"


def _safe(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", key)[:80]


def write_item(root, it: dict) -> Path:
    d = items_dir(root)
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    p = d / (_safe(it["key"]) + ".json")
    tmp = d / f".{p.name}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(it, f, ensure_ascii=False)
    os.replace(tmp, p)
    return p


def remove_item(root, key: str) -> bool:
    try:
        (items_dir(root) / (_safe(key) + ".json")).unlink()
        return True
    except OSError:
        return False


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def read_items(root, now: float | None = None) -> list[dict]:
    """Item files, pruned: a dead pid, an expired one or garbage is removed."""
    now = time.time() if now is None else now
    d = items_dir(root)
    me = pidns()
    out = []
    try:
        names = sorted(n for n in os.listdir(d) if n.endswith(".json") and not n.startswith("."))
    except OSError:
        return out
    for n in names[:256]:
        p = d / n
        try:
            if p.is_symlink() or p.stat().st_size > 4096:
                raise ValueError("bad item")
            it = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(it, dict) or not isinstance(it.get("key"), str):
                raise ValueError("bad item")
            it = item(it["key"], it.get("who"), it.get("kind"), it.get("label", ""), it.get("lk"), it.get("since"),
                      it.get("pid"), it.get("session"), it.get("ns"))
            if "pid" in it and it.get("ns", me) != me:      # another PID namespace (inside the fence): its heartbeat
                gone = now - p.stat().st_mtime > STALE
            else:
                gone = "pid" in it and not _alive(it["pid"])
            if gone or now - it["since"] > ITEM_TTL:
                raise ValueError("gone")
            it.pop("ns", None)
        except (OSError, ValueError, TypeError):
            with contextlib.suppress(OSError):
                p.unlink()
            continue
        out.append(it)
    return out


def scan_units(run=subprocess.run) -> list[dict]:
    """Active systemd user units `agentj-p<digits>…` (the CEO dispatch convention). Description "who · what" or
    "who: what"; otherwise the unit name (P150) is who and the Description the label."""
    try:
        r = run(["systemctl", "--user", "list-units", "--type=service", "--state=active", "--plain", "--no-legend",
                 "--no-pager", "agentj-p*"], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return []
    out = []
    for line in (r.stdout or "").splitlines()[:32]:
        parts = line.split(None, 4)
        if len(parts) < 4 or not UNIT_RE.match(parts[0]):
            continue
        desc = parts[4].strip() if len(parts) > 4 else ""
        m = re.match(r"^(.{1,24}?)\s*(?:·|:|：)\s*(.+)$", desc)
        who, what = (m.group(1), m.group(2)) if m else (parts[0][len("agentj-"):].split(".")[0].split("-")[0].upper(), desc)
        out.append(item("unit:" + parts[0], who, "ceo", what))
    return out


# ------------------------------------------------------------------------------------------- the board (serve)
class Board:
    """serve's view: in-memory items (owned / shared sessions, workflow runs) + item files + systemd units."""

    def __init__(self, root, on_change=None):
        self.root = Path(root) if root else None
        self.mem: dict[str, dict] = {}
        self.outside: list[dict] = []
        self.units: list[dict] = []
        self.pending: list[dict] = []          # shared hooks: [{session, type, desc, at}]
        self.colors: dict[str, int] = {}
        self.colors_dirty = False
        self.on_change = on_change or (lambda: None)
        self.sent = None
        if self.root is not None:
            with contextlib.suppress(OSError, ValueError, TypeError):
                c = json.loads((busy_dir(self.root) / "colors.json").read_text(encoding="utf-8"))
                self.colors = {str(k)[:64]: int(v) % COLORS for k, v in c.items()} if isinstance(c, dict) else {}

    # -- in-process sources
    def start(self, key, who, kind="temp", label="", lk=None, session=None) -> None:
        old = self.mem.get(key)
        it = item(key, who, kind, label, lk, old["since"] if old else None, session=session)
        if old != it:
            self.mem[key] = it
            self.on_change()

    def stop(self, key) -> None:
        if self.mem.pop(key, None) is not None:
            self.on_change()

    def stop_where(self, pred) -> None:
        gone = [k for k, v in self.mem.items() if pred(k, v)]
        for k in gone:
            del self.mem[k]
        if gone:
            self.on_change()

    def rekey(self, old: str, new: str) -> None:
        it = self.mem.pop(old, None)
        if it is not None:
            it["key"] = new
            self.mem[new] = it

    # -- shared Claude sessions' hook events (shared.py)
    def hook_event(self, ev: dict, scope: str = "shared") -> None:
        if not isinstance(ev, dict):
            return
        name, sid = ev.get("hook_event_name"), clean(ev.get("session_id"), 80)
        now = time.time()
        self.pending = [p for p in self.pending if now - p["at"] < PENDING_TTL]
        if name == "PreToolUse" and ev.get("tool_name") in AGENT_TOOLS and not ev.get("agent_id"):
            ti = ev.get("tool_input") if isinstance(ev.get("tool_input"), dict) else {}
            self.pending = (self.pending + [{"session": sid, "type": clean(ti.get("subagent_type"), WHO_MAX),
                                             "desc": clean(ti.get("description"), 200), "at": now}])[-16:]
        elif name == "SubagentStart":
            aid, typ = clean(ev.get("agent_id"), 80), clean(ev.get("agent_type"), WHO_MAX)
            match = next((p for p in self.pending if p["session"] == sid
                          and (p["type"] == typ or (not p["type"] and typ.lower() in GENERIC))), None)
            if match is None and not typ:
                return                         # no Agent call of ours (e.g. the compaction summariser): not shown
            if match is not None:
                self.pending.remove(match)
            self.start(f"{scope}:{sid}:{aid or uuid.uuid4().hex}", who_of(typ), "temp",
                       (match or {}).get("desc") or typ, session=f"{scope}:{sid}")
        elif name == "SubagentStop":
            aid = clean(ev.get("agent_id"), 80)
            if aid:
                self.stop(f"{scope}:{sid}:{aid}")
        elif name == "SessionEnd":
            self.stop_where(lambda k, v: v.get("session") == f"{scope}:{sid}")
        elif name == "Stop" and not ev.get("agent_id") and isinstance(ev.get("background_tasks"), list):
            live = {f"{scope}:{sid}:{t.get('id')}" for t in ev["background_tasks"] if isinstance(t, dict)}
            # foreground sub-agents are done when the main turn stops; background ones only when the harness drops them
            self.stop_where(lambda k, v: v.get("session") == f"{scope}:{sid}" and k not in live)

    # -- the owned Claude session's stream-json (agent.py)
    def stream_event(self, ev: dict, scope: str = "own") -> None:
        if not isinstance(ev, dict):
            return
        t = ev.get("type")
        if t == "assistant" and not ev.get("parent_tool_use_id"):
            for b in (ev.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in AGENT_TOOLS:
                    inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                    self.start(f"{scope}:tu:{clean(b.get('id'), 80)}", who_of(inp.get("subagent_type")), "temp",
                               inp.get("description") or inp.get("subagent_type") or "", session=scope)
        elif t == "user" and not ev.get("parent_tool_use_id"):
            for b in (ev.get("message") or {}).get("content") or []:
                if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                    continue
                key = f"{scope}:tu:{clean(b.get('tool_use_id'), 80)}"
                if key not in self.mem:
                    continue
                text = json.dumps(b.get("content"), ensure_ascii=False)[:4000]
                if re.search(r"async_launched|[Aa]sync agent launched|running in the background", text):
                    m = re.search(r"agentId\W{0,4}([A-Za-z0-9_-]{4,80})", text)
                    if m:
                        self.rekey(key, f"{scope}:task:{m.group(1)}")
                else:
                    self.stop(key)
        elif t == "system" and ev.get("subtype") == "task_started":
            tid = clean(ev.get("task_id"), 80)
            if tid and f"{scope}:task:{tid}" not in self.mem and ev.get("task_type") in (None, "local_agent", "agent"):
                self.start(f"{scope}:task:{tid}", None, "temp", ev.get("description") or "", session=scope)
        elif t == "system" and ev.get("subtype") == "task_notification":
            if ev.get("status") in ("completed", "failed", "stopped", "killed", "error"):
                self.stop(f"{scope}:task:{clean(ev.get('task_id'), 80)}")

    def session_gone(self, scope: str = "own") -> None:
        """The owned Claude process ended (halt, /clear, crash): nothing of it runs any more."""
        self.stop_where(lambda k, v: v.get("session") == scope)

    # -- outside sources (poll, off the event loop)
    def refresh_outside(self, units: bool = True, run=subprocess.run) -> bool:
        """Re-read item files (and, when `units`, the systemd units; otherwise the last scan stays). True = changed."""
        files = read_items(self.root) if self.root is not None else []
        if units:
            self.units = scan_units(run)
        got = files + getattr(self, "units", [])
        changed = got != self.outside
        self.outside = got
        return changed

    # -- the wire list
    def items(self, now: float | None = None) -> list[dict]:
        now = time.time() if now is None else now
        for k in [k for k, v in self.mem.items() if v["kind"] == "temp" and now - v["since"] > TEMP_TTL]:
            del self.mem[k]
        all_ = sorted(list(self.mem.values()) + self.outside, key=lambda v: (v["since"], v["key"]))[:MAX_ITEMS]
        used: set[int] = set()
        reserved = {c for w, c in self.colors.items()}
        out = []
        for i, v in enumerate(all_):
            name = ("n:" + v["who"]) if v["who"] else None
            c = self.colors.get(name) if name else None
            if c is None or c in used:
                free = [x for x in range(COLORS) if x not in used]
                pick = [x for x in free if x not in reserved] or free
                c = pick[0] if pick else (out[-1]["c"] + 1) % COLORS if out else 0
                if name and name not in self.colors:
                    self.colors[name] = c
                    self.colors_dirty = True
            used.add(c)
            out.append({"id": v["key"][-40:], "who": v["who"], "kind": v["kind"], "label": v["label"],
                        **({"lk": v["lk"]} if v.get("lk") else {}), "since": v["since"], "c": c})
        return out

    def take_colors(self) -> dict | None:
        """The colour memory to save (once per change), or None."""
        if not self.colors_dirty:
            return None
        self.colors_dirty = False
        return dict(list(self.colors.items())[-64:])

    def msg(self, by=None) -> dict:
        return {"t": "busy", "tasks": self.items(), "by": by}


def save_colors(root, colors: dict) -> None:
    d = busy_dir(root)
    with contextlib.suppress(OSError):
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = d / f".colors.{os.getpid()}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(colors, f, ensure_ascii=False)
        os.replace(tmp, d / "colors.json")


# ------------------------------------------------------------------------------------------- CLI
def _root(a):
    from .state import state_dir
    return state_dir()


def cmd_busy(a) -> int:
    root = _root(a)
    if a.busy_cmd == "list":
        b = Board(root)
        b.refresh_outside(units=not a.no_units)
        tasks = b.items()
        if a.json:
            print(json.dumps(tasks, ensure_ascii=False))
        else:
            for t in tasks:
                print(f"{t['who'] or '临时助理'} · {t['label']}")
            if not tasks:
                print("没有在后台干活的 Agent / nothing running")
        return 0
    if a.busy_cmd == "start":
        it = item("cli:" + (a.key or uuid.uuid4().hex[:12]), a.who, a.kind, a.label, pid=a.pid)
        write_item(root, it)
        print(it["key"])
        return 0
    if a.busy_cmd == "stop":
        return 0 if remove_item(root, a.key if a.key.startswith(("cli:", "run:")) else "cli:" + a.key) else 1
    if a.busy_cmd == "run":
        cmd = a.command[1:] if a.command[:1] == ["--"] else a.command
        if not cmd:
            print("用法 / usage: agentj busy run --who 掌枢 --label 发布收尾 -- <command…>", file=sys.stderr)
            return 2
        key = "run:" + uuid.uuid4().hex[:12]
        path = None
        try:
            path = write_item(root, item(key, a.who, a.kind, a.label, pid=os.getpid(), ns=pidns()))
        except OSError as e:              # e.g. no state dir: the work still runs, only unseen on the phone
            print(f"agentj busy: 手机上不会显示这件事 / not shown on the phone ({e.__class__.__name__})", file=sys.stderr)
        stop = threading.Event()

        def beat():
            while path is not None and not stop.wait(HEARTBEAT):
                with contextlib.suppress(OSError):
                    os.utime(path)
        threading.Thread(target=beat, daemon=True).start()
        try:
            return subprocess.call(cmd)
        except OSError as e:
            print(e, file=sys.stderr)
            return 127
        finally:
            stop.set()
            remove_item(root, key)
    return 2


def add_parser(sub) -> None:
    p = sub.add_parser("busy", help="在办事项（手机回复卡右上角的彩色圆点）：list · start · stop · run / who is working in the "
                                    "background (the dots on the phone)")
    ps = p.add_subparsers(dest="busy_cmd", required=True)
    x = ps.add_parser("list", help="现在谁在干活 / who is working now")
    x.add_argument("--json", action="store_true")
    x.add_argument("--no-units", action="store_true", help="不看 systemd 的 agentj-p* 单元 / skip systemd units")
    for n, h in (("start", "登记一件在办事项 / add an item"), ("run", "登记、运行命令、结束时撤下 / add, run a command, remove")):
        x = ps.add_parser(n, help=h)
        x.add_argument("--who", default="", help="谁（CEO 名字；空 = 临时助理）/ who (empty = a helper)")
        x.add_argument("--label", default="", help="在干什么（自动压成 ≤5 个汉字 / ≤10 个字母）/ what (squeezed)")
        x.add_argument("--kind", choices=("ceo", "temp"), default="ceo")
        if n == "start":
            x.add_argument("--key", default="", help="稍后 stop 用的名字 / the name to stop it with")
            x.add_argument("--pid", type=int, default=None, help="这个进程结束就自动撤下 / removed when this pid ends")
        else:
            x.add_argument("command", nargs=argparse.REMAINDER)
    x = ps.add_parser("stop", help="撤下一件 / remove an item")
    x.add_argument("key")
    p.set_defaults(fn=cmd_busy)
