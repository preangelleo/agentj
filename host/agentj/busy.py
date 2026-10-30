"""Display-only background-work board (PROTOCOL §22); it never starts, stops or approves work.
Board folds native Claude/Codex/OpenCode events, workflows and outside dispatches, reconciling completion and liveness.
Item-file text is display data, not proof of a trusted launch. Detail reads are bounded and redacted. Labels are capped;
load is measured, not inferred. Stable colour assignments and explicit empty task lists drive the phone projection.

Protocol/history detail: agentjarvis/ARCHITECTURE.md, P0261 source notes / busy.py.
"""
from __future__ import annotations

import argparse
import contextlib
from collections import deque
import json
import os
import re
import subprocess
import stat
import sys
import threading
import time
import uuid
from pathlib import Path

COLORS = 9
MAX_ITEMS = 32                 # on the wire; the phone shows up to 6 robots (12 on a wide screen of a strong computer) and "+n"
ITEM_TTL = 12 * 3600           # an item file without a live pid (or a lost close) is gone after this long
TEMP_TTL = 3 * 3600            # a sub-agent whose close never arrived
BG_DONE = ("completed", "failed", "killed", "stopped", "error", "cancelled")   # P291: a background entry that is over
PENDING_TTL = 120              # PreToolUse(Agent) description waiting for its SubagentStart
WHO_MAX = 24
LABEL_MAX = 120               # P154: the full human-readable title, not 5 characters
LAST_MAX = 160                # P160: a sub-agent's latest tool description (detail only, never on the list)
REPORT_PATH_MAX = 400
REPORT_HEAD_MAX = 240         # P160: the first non-empty line of a REPORT
REPORT_READ = 16 * 1024
LOAD_EVERY = 5                # P154: seconds between load samples …
LOAD_STEP = 0.05              # … and the change that is worth a message
GENERIC = {"", "general-purpose", "general", "explore", "plan", "claude", "fork", "task", "agent", "default",
           "worker", "explorer", "subagent", "build"}      # P160: Codex's built-in roles / OpenCode's default agents too
CODEX_FINAL = ("completed", "errored", "shutdown", "interrupted", "notFound")
OC_SUFFIX = re.compile(r"\s*\(@([\w.-]{1,40}) subagent\)\s*$")     # OpenCode titles a child "<description> (@<agent> subagent)"
AGENT_TOOLS = ("Agent", "Task")
UNIT_RE = re.compile(r"^agentj-p\d+[\w.-]*\.service$")
_CTRL = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|[\x00-\x1f\x7f]")


def clean(s, n: int) -> str:
    return _CTRL.sub("", s).strip()[:n] if isinstance(s, str) else ""


def short(text) -> str:
    """The label (P154): the whole description on one line — control characters out, runs of whitespace (newlines too)
    become one space — capped at LABEL_MAX characters (a longer one ends in "…"). The phone gives every row a fixed
    width and ellipsises it there; nothing is squeezed here any more. No model, no network."""
    if isinstance(text, str):
        text = re.sub(r"[\t\n\r\v\f\u2028\u2029]+", " ", text)     # line breaks are spaces, not glue (clean drops them)
    s = " ".join(clean(text, 4 * LABEL_MAX).split())
    return s if len(s) <= LABEL_MAX else s[:LABEL_MAX - 1].rstrip() + "…"


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


def item(key: str, who, kind: str, label="", lk=None, since=None, pid=None, session=None, ns=None, report=None, **meta) -> dict:
    d = {"key": key, "who": clean(who, WHO_MAX) or None, "kind": "ceo" if kind == "ceo" else "temp",
         "label": short(label) if label else "", "since": int(since or time.time())}
    if isinstance(report, str) and report.strip():
        d["report"] = clean(report, REPORT_PATH_MAX)
    if lk in ("schedule", "dispatch"):
        d["lk"] = lk
    if isinstance(pid, int) and pid > 0:
        from .busy_probe import start_id
        d["pid"] = pid
        if start_id(pid):
            d["proc_start"] = start_id(pid)
        if isinstance(ns, str) and ns:
            d["ns"] = ns[:64]
    if session:
        d["session"] = clean(session, 80)
    d.update(task_meta(meta))
    return d


META_LIMITS = {"harness": 32, "model": 100, "effort": 24, "cwd": 400, "workflow": 80, "role": 80, "parent": 80}


def task_meta(meta):
    """Only declared display metadata; never infer a child's model from its parent."""
    from .privacy import redact
    return {k: redact(clean(meta.get(k), n)) for k, n in META_LIMITS.items()
            if isinstance(meta.get(k), str) and clean(meta[k], n)}


def sys_load(getloadavg=getattr(os, "getloadavg", None), cpus=os.cpu_count) -> float | None:
    """P154: how busy the Agent J host is, 0..1 — the 1-minute load average per core (1 = every core has work). None where
    the system has no load average (Windows) or it cannot be read."""
    if getloadavg is None:
        return None
    try:
        one = float(getloadavg()[0])
    except (OSError, ValueError, TypeError, IndexError):
        return None
    if one != one or one < 0:
        return None
    return round(min(1.0, one / max(1, cpus() or 1)), 2)


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
    process_table = None
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
            raw = it
            it = item(it["key"], it.get("who"), it.get("kind"), it.get("label", ""), it.get("lk"), it.get("since"),
                      it.get("pid"), it.get("session"), it.get("ns"), it.get("report"), **task_meta(it))
            it["self_reported"] = True
            if isinstance(raw.get("proc_start"), str):
                it["proc_start"] = raw["proc_start"][:80]
            if "pid" in it and it.get("ns", me) != me:      # another PID namespace (inside the fence): its heartbeat
                from .busy_probe import Processes
                if process_table is None:
                    process_table = Processes()
                translated = process_table.namespace_pid(it["pid"], it.get("ns"))
                gone = (bool(it.get("proc_start") and it["proc_start"] != process_table.identity(translated))
                        if translated is not None else now - p.stat().st_mtime > STALE)
                it["source_ns"] = it.get("ns")
                it["ns_other"] = True                       # P423 #160: its pid means nothing here (survives_restart)
            else:
                gone = "pid" in it and not _alive(it["pid"])
            if gone or ("pid" not in it and now - it["since"] > ITEM_TTL):
                raise ValueError("gone")
            it.pop("ns", None)
        except (OSError, ValueError, TypeError):
            with contextlib.suppress(OSError):
                p.unlink()
            continue
        out.append(it)
    return out


# P200 追加 1: systemd-run without --description names the unit after its command line ("[systemd-run] /usr/bin/bash
# …/run-px.sh 165 …"; systemd < 256: the bare command line). That is not a title: a known launcher (`run-<name>.sh|py`)
# is asked for its PROMPT file's first "# " heading; else the unit name. The command line itself is detail only (`cmd`).
SYSTEMD_RUN = re.compile(r"^\[systemd-run\]\s+")
LAUNCHER_RE = re.compile(r"^run-[\w.-]{1,40}\.(?:sh|bash|py)$")
PROMPT_ARG = re.compile(r"^[\w.-]{1,40}$")
CMD_MAX = 400


def cmdline_desc(desc: str) -> str | None:
    """The command line when a Description is systemd-run's default (prefixed, or an absolute path first); else None."""
    m = SYSTEMD_RUN.match(desc or "")
    if m:
        return desc[m.end():].strip() or None
    return desc.strip() if (desc or "").startswith("/") else None


def prompt_title(path) -> str | None:
    """The first markdown H1 ("# …", outside ``` fences) of a regular file (lstat, ≤ REPORT_READ bytes); else None."""
    try:
        import stat as _stat
        if not _stat.S_ISREG(os.lstat(path).st_mode):
            return None
        with open(path, "rb") as f:
            data = f.read(REPORT_READ)
    except OSError:
        return None
    fence = False
    for line in data.decode("utf-8", "replace").splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
        elif not fence and line.startswith("# ") and line[2:].strip():
            return line[2:].strip()
    return None


def launcher_title(cmd: str, num: str | None = None) -> str | None:
    """The PROMPT title of a known launcher on a command line: `…/run-px.sh 165 …` → PROMPT-165.md next to the script,
    `…/run-shotfable.sh [name]` → PROMPT-<name>.md, else PROMPT-<launcher name>.md / PROMPT-<unit number>.md."""
    argv = cmd.split()
    for i, tok in enumerate(argv[:4]):
        p = Path(tok)
        if p.is_absolute() and LAUNCHER_RE.match(p.name):
            names = [a for a in argv[i + 1:i + 4] if PROMPT_ARG.match(a)]
            names += [p.stem[len("run-"):]] + ([num] if num else [])
            for n in dict.fromkeys(names):
                t = prompt_title(p.parent / f"PROMPT-{n}.md")
                if t:
                    return t
            return None
    return None


def _drop_who(who: str, title: str) -> str:
    """"P165 — 0.20.0 集成" under who P165 → "0.20.0 集成" (the card already shows who); unchanged when nothing is left."""
    rest = re.sub(rf"^{re.escape(who)}(?![\w])\s*(?:[—–\-:：·|]\s*)?", "", title, flags=re.I).strip()
    return rest or title


def unit_names(unit: str, desc: str) -> tuple[str | None, str, str | None]:
    """(who, label, cmd) for a dispatch unit: ① Description "who · what" / "who: what" or any other written title (who =
    the unit's P-number); ② systemd-run's default (a command line): a launcher's PROMPT title, the leading "P<N> —" cut
    when it repeats who; ③ the unit name. `cmd` = the command line (detail only), else None."""
    who = unit[len("agentj-"):].split(".")[0].split("-")[0].upper()
    cmd = cmdline_desc(desc)
    if cmd is None:
        m = re.match(r"^(.{1,24}?)\s*(?:·|:|：)\s*(.+)$", desc)
        return (m.group(1), m.group(2), None) if m else (who, _drop_who(who, desc) if desc else unit.removesuffix(".service"), None)
    num = re.match(r"^agentj-p(\d+)", unit)
    title = launcher_title(cmd, num.group(1) if num else None)
    if title:
        from . import privacy
        return who, _drop_who(who, privacy.redact(title, paths=True)), cmd[:CMD_MAX]
    return who, unit.removesuffix(".service"), cmd[:CMD_MAX]


# P302 (owner 09:47: P290 / P302 / P303 sat under 「临时帮手 · 不属于任何工作流」): a dispatch unit runs a CEO in some folder
# of the working root — systemd-run's own cwd is $HOME, the launcher cd's later, so the unit's place is the deepest working
# directory among the processes of its control group (serve's task_assignment maps it to the folder → the company). Its
# title is the heading of the nearest PROMPT-<N>.md (in a `.jarvis` folder) above that directory.
CG_ROOT = Path("/sys/fs/cgroup")
CG_PIDS = 64


def cg_pids(cg: str, root: Path = CG_ROOT) -> list[int]:
    """The pids of a control group and its children (≤ CG_PIDS); [] off Linux / unreadable."""
    pids: list[int] = []
    base = root / cg.lstrip("/")
    for d, _dirs, _files in os.walk(base):
        with contextlib.suppress(OSError, ValueError):
            pids += [int(x) for x in (Path(d) / "cgroup.procs").read_text().split()]
        if len(pids) >= CG_PIDS:
            break
    return pids[:CG_PIDS]


def deepest_cwd(pids, readlink=os.readlink) -> str | None:
    best = None
    for pid in pids:
        try:
            c = readlink(f"/proc/{pid}/cwd")
        except OSError:
            continue
        if best is None or len(Path(c).parts) > len(Path(best).parts):
            best = c
    return best


def unit_places(units, run=subprocess.run, pids_of=cg_pids, readlink=os.readlink) -> dict:
    """unit → {"cg": its control group, "cwd"?: where it works} — one `systemctl show` for all units."""
    if not units:
        return {}
    try:
        r = run(["systemctl", "--user", "show", "-p", "Id,ControlGroup", "--", *units], capture_output=True, text=True,
                timeout=3)
    except (OSError, subprocess.SubprocessError):
        return {}
    out, cur = {}, {}
    for line in (r.stdout or "").splitlines() + [""]:
        if not line.strip():
            if cur.get("Id") in units and cur.get("ControlGroup", "").startswith("/"):
                out[cur["Id"]] = {"cg": cur["ControlGroup"]}
            cur = {}
            continue
        k, _, v = line.partition("=")
        cur[k] = v.strip()
    for place in out.values():
        cwd = deepest_cwd(pids_of(place["cg"]), readlink)
        if cwd:
            place["cwd"] = cwd
    return out


def prompt_near(cwd: str, num: str, up: int = 6) -> str | None:
    """The first heading of PROMPT-<num>.md in the `.jarvis` folder of cwd or one of its parents (≤ up levels)."""
    d = Path(cwd)
    for _ in range(up):
        t = prompt_title(d / ".jarvis" / f"PROMPT-{num}.md")
        if t:
            return t
        if d.parent == d:
            break
        d = d.parent
    return None


def in_cgroup(pid, cg: str | None) -> bool:
    """The pid runs inside the control group cg (or one of its children)."""
    if not isinstance(pid, int) or not cg:
        return False
    try:
        lines = Path(f"/proc/{pid}/cgroup").read_text().splitlines()
    except OSError:
        return False
    return any((ln.split(":", 2)[-1] + "/").startswith(cg.rstrip("/") + "/") for ln in lines)


# P423 #160: a serve restart (upgrade) ends serve's own process tree and, under systemd (agentj.service, default
# KillMode=control-group), everything in serve's control group. A worker started elsewhere — a `systemd-run --user` unit,
# a launchd job, another terminal — keeps running and is not "interrupted". Anything we cannot prove counts (as before).
def _proc_ppid(pid: int, proc: Path) -> int | None:
    try:
        stat_ = (proc / str(pid) / "stat").read_text()
        return int(stat_[stat_.rindex(")") + 2:].split()[1])
    except (OSError, ValueError, IndexError):
        return None


def _own_cg(proc: Path) -> str | None:
    try:
        lines = (proc / "self" / "cgroup").read_text().splitlines()
    except OSError:
        return None
    return next((ln.split(":", 2)[-1] for ln in lines if ln.startswith("0::")), None)


def ps_parents(run=subprocess.run) -> dict[int, int] | None:
    """macOS / BSD: pid → ppid of every process from one `ps` call; None when ps is unavailable."""
    try:
        r = run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    out = {}
    for ln in (r.stdout or "").splitlines():
        p = ln.split()
        if len(p) == 2 and p[0].isdigit() and p[1].isdigit():
            out[int(p[0])] = int(p[1])
    return out or None


def survives_restart(it: dict, serve_pid: int | None = None, proc: Path = Path("/proc"), parents=None) -> bool:
    """True only when this outside item is known to run outside serve: a systemd unit / launchd job (`unit:`), or a pid
    (in serve's PID namespace) whose parent chain does not reach serve and — when serve is a systemd service — that is not
    in serve's control group. No pid, a fenced pid, an unreadable /proc or ps → False (it counts as interrupted)."""
    if str(it.get("key", "")).startswith("unit:"):
        return True
    pid = it.get("pid")
    if not isinstance(pid, int) or pid <= 1 or it.get("ns_other"):
        return False
    me = os.getpid() if serve_pid is None else serve_pid
    if pid == me:
        return False
    linux = (proc / "self" / "stat").exists()
    if not linux and parents is None:
        parents = ps_parents()
    get = (lambda p: _proc_ppid(p, proc)) if linux else (lambda p: (parents or {}).get(p))
    cur, seen = pid, 0
    while cur is not None and cur > 1 and seen < 256:
        if cur == me:
            return False                                 # serve's own descendant
        cur, seen = get(cur), seen + 1
    if cur is None or seen >= 256:
        return False                                     # chain unreadable: conservative
    if not linux:                                        # launchd stops a job's whole process group
        try:
            if os.getpgid(pid) == os.getpgid(me):
                return False
        except (OSError, AttributeError):
            return False
    if linux:
        cg = _own_cg(proc)
        if cg is None:
            return False
        if cg.rstrip("/").endswith(".service"):          # serve is a systemd service: its cgroup goes with it
            try:
                lines = (proc / str(pid) / "cgroup").read_text().splitlines()
            except OSError:
                return False
            if any((ln.split(":", 2)[-1].rstrip("/") + "/").startswith(cg.rstrip("/") + "/") for ln in lines):
                return False
    return True


def same_job(f: dict, u: dict) -> bool:
    """P302: an `agentj busy run` item (the 10-15 run-px2.sh convention) and the dispatch unit it runs in are one job —
    its pid sits in the unit's control group, or its REPORT is the unit's REPORT-<N>.md."""
    if in_cgroup(f.get("pid"), u.get("cg")):
        return True
    m = re.match(r"^unit:agentj-p(\d+)", u.get("key", ""))
    return bool(m) and Path(str(f.get("report") or "")).name == f"REPORT-{m.group(1)}.md"


LAUNCHD_RE = re.compile(r"^agentj-p(\d+)([\w.-]*)$")


def scan_launchd(run=subprocess.run) -> list[dict]:
    """P291: macOS has no systemd — running launchd jobs labelled `agentj-p<digits>…` are the same convention
    (`launchctl submit -l agentj-p291-busy-fix -- …` or a LaunchAgent plist with that Label). who = P<N>; what = the rest of
    the label, dashes as spaces. A job without a pid (loaded, not running) is not work."""
    try:
        r = run(["launchctl", "list"], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return []
    out = []
    for line in (r.stdout or "").splitlines()[:2048]:
        parts = line.split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        m = LAUNCHD_RE.match(parts[2].strip())
        if not m:
            continue
        what = re.sub(r"[-_.]+", " ", m.group(2)).strip() or parts[2].strip()
        out.append(item("unit:" + parts[2].strip(), "P" + m.group(1), "ceo", what, pid=int(parts[0])))
        if len(out) >= 32:
            break
    return out


def scan_units(run=subprocess.run, platform=None, strict=False, places=unit_places) -> list[dict]:
    """Active systemd user units `agentj-p<digits>…` (the CEO dispatch convention). Names: see unit_names (P150 / P200).
    macOS (P291): no systemctl there → the running launchd jobs of the same name (scan_launchd). strict (P262): a failed
    scan raises instead of looking like "nothing runs". P302: where each works (cwd → its workflow), the PROMPT heading as
    its title, role CEO."""
    try:
        r = run(["systemctl", "--user", "list-units", "--type=service", "--state=active,activating", "--plain", "--no-legend",
                 "--no-pager", "--full", "agentj-p*"], capture_output=True, text=True, timeout=3)
    except OSError:
        if strict and (platform or sys.platform) != "darwin":
            raise
        return scan_launchd(run) if (platform or sys.platform) == "darwin" else []
    except subprocess.SubprocessError:
        if strict:
            raise
        return []
    if strict and getattr(r, "returncode", 0):
        raise OSError("systemd scan failed")
    out = []
    for line in (r.stdout or "").splitlines()[:32]:
        parts = line.split(None, 4)
        if len(parts) < 4 or not UNIT_RE.match(parts[0]):
            continue
        desc = parts[4].strip() if len(parts) > 4 else ""
        who, what, cmd = unit_names(parts[0], desc)
        it = item("unit:" + parts[0], who, "ceo", what, role="CEO")
        if cmd:
            it["cmd"] = clean(cmd, CMD_MAX)
        out.append(it)
    if out:
        where = places([x["key"][5:] for x in out], run)
        for it in out:
            place = where.get(it["key"][5:]) or {}
            if place.get("cg"):
                it["cg"] = place["cg"]
            num = re.match(r"^unit:agentj-p(\d+)", it["key"])
            if place.get("cwd"):
                it.update(task_meta({"cwd": place["cwd"]}))
                title = prompt_near(place["cwd"], num.group(1)) if num else None
                if title:
                    from . import privacy
                    it["label"] = short(_drop_who(it["who"] or "", privacy.redact(title, paths=True)))
    return out


# ------------------------------------------------------------------------------------------- detail (P160)
_DESC_KEYS = ("description", "title", "command", "cmd", "file_path", "filePath", "path", "pattern", "query", "url", "prompt")


def tool_desc(name, inp=None) -> str:
    """One line for a sub-agent's latest tool call: the tool's own description when it has one, else its main argument
    (the command, the file, the pattern …), prefixed with the tool name. Display only; redacted again on the way out."""
    n = clean(name, 40)
    inp = inp if isinstance(inp, dict) else {}
    arg = ""
    for k in _DESC_KEYS:
        v = inp.get(k)
        if isinstance(v, list):
            v = " ".join(x for x in v if isinstance(x, str))
        if isinstance(v, str) and v.strip():
            arg = v
            break
    if isinstance(arg, str):
        arg = re.sub(r"[\t\n\r\v\f\u2028\u2029]+", " ", arg)
    arg = " ".join(clean(arg, 4 * LAST_MAX).split())
    s = f"{n} · {arg}" if n and arg else (n or arg)
    return s if len(s) <= LAST_MAX else s[:LAST_MAX - 1].rstrip() + "…"


def codex_item_desc(it: dict) -> str:
    """A Codex child thread's item → one line (command / file / tool / search), like tool_desc."""
    typ = it.get("type") if isinstance(it, dict) else None
    if typ == "commandExecution":
        cmd = it.get("command")
        if isinstance(cmd, str):
            m = re.match(r"^(?:\S*/)?(?:ba|z)?sh\s+-l?c\s+(['\"])(.*)\1\s*$", cmd, re.S)
            cmd = m.group(2) if m else cmd
        return tool_desc("Bash", {"command": cmd})
    if typ == "fileChange":
        ch = it.get("changes") if isinstance(it.get("changes"), list) else []
        paths = [c.get("path") for c in ch if isinstance(c, dict) and isinstance(c.get("path"), str)]
        return tool_desc("Edit", {"path": " ".join(paths[:3])})
    if typ == "mcpToolCall":
        return tool_desc(f"{clean(it.get('server'), 30)}.{clean(it.get('tool'), 30)}", it.get("arguments"))
    if typ == "webSearch":
        return tool_desc("WebSearch", {"query": it.get("query")})
    if typ in ("dynamicToolCall",):
        return tool_desc(it.get("tool"), it.get("arguments"))
    return ""


def path_label(path) -> str:
    """Codex's agentPath "/root/file_count" → "file count" (its last segment, made readable)."""
    seg = clean(path, 200).rstrip("/").rsplit("/", 1)[-1]
    return " ".join(seg.replace("_", " ").replace("-", " ").split())


def _find(obj, name: str, depth: int = 0):
    """The first dict value under key `name` anywhere in a small JSON tree (Codex nests the thread source)."""
    if depth > 4:
        return None
    if isinstance(obj, dict):
        if isinstance(obj.get(name), dict):
            return obj[name]
        for v in list(obj.values())[:16]:
            r = _find(v, name, depth + 1)
            if r is not None:
                return r
    return None


def _state(it: dict, tid: str):
    st = it.get("agentsStates") if isinstance(it.get("agentsStates"), dict) else {}
    s = st.get(tid)
    return s.get("status") if isinstance(s, dict) else None


def report_head(path) -> dict | None:
    """{path, head, at} for a REPORT file: its first non-empty line (markdown heading marks off). Regular files only (no
    symlink, no device); None when the path is not a readable file — the phone then shows the path as 「还没有报告」."""
    if not isinstance(path, str) or not path.strip():
        return None
    p = Path(os.path.expanduser(path.strip()))
    out = {"path": str(p)[:REPORT_PATH_MAX], "head": None, "at": None}
    try:
        st = os.lstat(p)
        import stat as _stat
        if not _stat.S_ISREG(st.st_mode):
            return out
        with open(p, "rb") as f:
            data = f.read(REPORT_READ)
        out["at"] = int(st.st_mtime)
    except OSError:
        return out
    for line in data.decode("utf-8", "replace").splitlines():
        line = clean(re.sub(r"^\s*(?:#+|[-*>]|\*\*)\s*", "", line), 4 * REPORT_HEAD_MAX).strip()
        if line:
            out["head"] = line if len(line) <= REPORT_HEAD_MAX else line[:REPORT_HEAD_MAX - 1].rstrip() + "…"
            break
    return out


def unit_report(unit: str, run=subprocess.run) -> str | None:
    """Where a dispatch unit `agentj-p<N>…` writes its REPORT: Environment AGENTJ_REPORT=<path> (the documented way), else
    REPORT-<N>.md next to the script it runs or in its WorkingDirectory (./ , .jarvis/ , reports/). The first that
    exists; else the first candidate (shown as 「还没有报告」); None when nothing points anywhere."""
    if not UNIT_RE.match(unit or ""):
        return None
    try:
        r = run(["systemctl", "--user", "show", unit, "-p", "Environment", "-p", "WorkingDirectory", "-p", "ExecStart",
                 "--no-pager"], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    props: dict[str, str] = {}
    for line in (r.stdout or "").splitlines()[:16]:
        k, _, v = line.partition("=")
        props[k] = v
    m = re.search(r"(?:^|\s)AGENTJ_REPORT=(\S+)", props.get("Environment", ""))
    if m:
        return m.group(1)
    num = re.match(r"^agentj-p(\d+)", unit).group(1)
    name = f"REPORT-{num}.md"
    dirs: list[Path] = []
    argv = re.search(r"argv\[\]=([^;]*)", props.get("ExecStart", ""))
    for tok in (argv.group(1).split() if argv else [])[1:]:
        if "/" in tok and not tok.startswith("-"):
            tp = Path(tok)
            if tp.suffix in (".sh", ".py", ".bash", ".zsh", ".js", ".mjs"):
                dirs.append(tp.parent)
    wd = props.get("WorkingDirectory", "").lstrip("!-")
    if wd and wd.startswith("/"):
        dirs += [Path(wd), Path(wd) / ".jarvis", Path(wd) / "reports"]
    cands = [d / name for d in dirs]
    for c in cands:
        with contextlib.suppress(OSError):
            if c.is_file():
                return str(c)
    return str(cands[0]) if cands else None



# P262: completion receipts are local metadata, never argv or report contents.
def write_finished(root, it, code):
    d = items_dir(root) / "finished"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    from .state import _write_private
    _write_private(d / (_safe(it["key"]) + ".json"),
                   json.dumps({**it, "ended": time.time(), "exit_code": code}, ensure_ascii=False).encode())


def finished_items(root):
    d = items_dir(root) / "finished"
    out = []
    for p in sorted(d.glob("*.json"))[:256]:
        try:
            info = p.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
                continue
            v = json.loads(p.read_text())
            if not isinstance(v, dict) or not isinstance(v.get("key"), str):
                continue
            it = item(v["key"], v.get("who"), v.get("kind"), v.get("label"), since=v.get("since"), report=v.get("report"))
            it["ended"] = v.get("ended") if isinstance(v.get("ended"), (int, float)) else time.time()
            it["exit_code"] = v.get("exit_code") if type(v.get("exit_code")) is int else None
            out.append(it)
        except (OSError, ValueError, TypeError):
            continue
    return out


def unit_since(unit, run=subprocess.run):
    # systemd monotonic start gives real elapsed time and stable dedup across serve restarts.
    try:
        r = run(["systemctl", "--user", "show", unit, "-p", "ExecMainStartTimestampMonotonic", "--value"],
                capture_output=True, text=True, timeout=3)
        value = int((r.stdout or "").strip())
        if r.returncode == 0 and value > 0:
            return int(time.time() - time.monotonic() + value / 1000000)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def unit_end(unit, run=subprocess.run):
    try:
        r = run(["systemctl", "--user", "show", unit, "-p", "ActiveState", "-p", "LoadState",
                 "-p", "ExecMainStatus", "-p", "ExecMainCode", "--no-pager"],
                capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    props = dict(line.split("=", 1) for line in (r.stdout or "").splitlines() if "=" in line)
    if props.get("LoadState") == "not-found":
        return {"exit_code": None}               # collected transient unit; never invent success
    if r.returncode or props.get("ActiveState") not in ("inactive", "failed"):
        return None
    code = props.get("ExecMainStatus", "")
    mode = props.get("ExecMainCode", "")
    return {"exit_code": int(code) if mode in ("1", "exited") and code.isdigit() else None}


# ------------------------------------------------------------------------------------------- the board (serve)
class Board:
    """serve's view: in-memory items (owned / shared sessions, workflow runs) + item files + systemd units."""

    def __init__(self, root, on_change=None, verify_sources=False):
        self.verify_sources = verify_sources
        self.trusted = None                    # P570: is the launching turn the owner's / the host's own?
        self.launched = {}
        self.evidence = {}
        self.claude_transcripts = {}
        self.root = Path(root) if root else None
        self.context: dict[str, dict] = {}
        self.mem: dict[str, dict] = {}
        self.outside: list[dict] = []
        self.units: list[dict] = []
        self.finished = deque()              # P262: terminal events, drained by serve on its event loop
        self.pending: list[dict] = []          # shared hooks: [{session, type, desc, at}]
        self.colors: dict[str, int] = {}
        self.colors_dirty = False
        self.on_change = on_change or (lambda: None)
        self.sent = None
        self.load: float | None = None         # P154: the last load value published (changes only by > LOAD_STEP)
        self.load_at = 0.0
        self.cap: int | None = None            # P154 追加 11: running + how many more the Agent J host can take (P156 capacity)
        self.last: dict[str, dict] = {}        # P160: key → {text, at}, a sub-agent's latest tool call (detail only)
        self.alias: dict[str, str] = {}        # P160: a child id (Claude tool_use, Codex thread, OpenCode session) → item key
        self.child: dict[str, dict] = {}       # P160: Codex child thread / OpenCode v2 child session → {who, label}
        if self.root is not None:
            with contextlib.suppress(OSError, ValueError, TypeError):
                c = json.loads((busy_dir(self.root) / "colors.json").read_text(encoding="utf-8"))
                self.colors = {str(k)[:64]: int(v) % COLORS for k, v in c.items()} if isinstance(c, dict) else {}

    # -- in-process sources
    def set_context(self, scope, harness=None, cwd=None, pid=None):
        # Default working directory / tool only; a child's model is always its own event.
        from .busy_probe import start_id
        self.context[scope] = {**task_meta({"harness": harness, "cwd": cwd}), **({"pid": pid, "proc_start": start_id(pid)} if isinstance(pid, int) else {})}

    def start(self, key, who, kind="temp", label="", lk=None, session=None, late=False, **meta) -> None:
        old = self.mem.get(key)
        it = item(key, who, kind, label, lk, old["since"] if old else None, session=session)
        context = next((v for k,v in self.context.items() if str(session or key)==k or str(session or key).startswith(k+":")), {})
        it.update(context)
        it["generation"] = old.get("generation") if old else uuid.uuid4().hex
        if old:
            it.update(task_meta(old))
            if old.get("bg"):
                it["bg"] = True        # P426: once background, its end is a 「后台活已结束」 one
        if (old or {}).get("foreign") or (not old and session and (late and self.launched.get(key, True) or self.trusted and not self.trusted())):
            it["foreign"] = True       # P570; late: first seen in Stop (restart), launcher unknown
        if session and not old and not (late and key in self.launched):   # P570b: launch trust, kept after the item ends
            self.launched[key] = bool(it.get("foreign") or not self.trusted)
            while len(self.launched) > 64:
                self.launched.pop(next(iter(self.launched)))
        source = str(session or key).lower()
        harness = next((x for x in ("codex", "opencode", "claude") if x in source), None)
        if harness is None and source in ("own", "shared"):
            harness = "claude"
        if harness:
            it["harness"] = harness
        it.update(task_meta(meta))
        if isinstance(meta.get("pid"), int):
            it["pid"] = meta["pid"]
        if old:
            for field in ("pid", "proc_start", "native_id"):
                if old.get(field):
                    it.setdefault(field, old[field])
        # Native registration is machine evidence; external CLI text is handled separately.
        if session and not str(key).startswith("wf:"):
            it["native"] = True
            it["native_child"] = True
            it["activity_at"] = time.time()
            # Registration itself is native activity; no first tool call is required.
            for field in ("native_status", "checked_at", "native_started_at"):
                if old and field in old:
                    it[field] = old[field]
            if not old:
                it.update(native_status="running", checked_at=it["activity_at"])
            if ":th:" in str(key) or ":s:" in str(key):
                it["native_id"] = str(key).split(":")[-1]
        if old != it:
            self.mem[key] = it
            self.on_change()

    def annotate(self, key, **meta):
        key = self.alias.get(key, key)
        if key in self.mem:
            it = {**self.mem[key], **task_meta(meta)}
            if it != self.mem[key]:
                self.mem[key] = it
                self.on_change()

    def stop(self, key, exit_code=None, fg=False) -> None:
        it = self.mem.pop(key, None)
        if it is not None:
            self.finish(it, exit_code=exit_code, fg=fg)
            self.on_change()

    def finish(self, it, exit_code=None, ended=None, fg=False):
        """P426: `fg` = a foreground sub-agent (it ended inside the main turn that waited for it) — the main Agent already
        has its result, so P262 does not wake it again; only background work ends with 「后台活已结束」."""
        fg = (fg or it.get("fg") is True) and not it.get("bg")
        self.finished.append({**it, "ended": ended or time.time(), "exit_code": exit_code, "fg": fg})

    def mark_bg(self, key) -> None:
        key = self.alias.get(key, key)
        if key in self.mem:
            self.mem[key] = {**self.mem[key], "bg": True}

    def stop_where(self, pred, fg=False) -> None:
        gone = [k for k, v in self.mem.items() if pred(k, v)]
        for k in gone:
            self.finish(self.mem.pop(k), fg=fg)
        if gone:
            self.on_change()

    def rekey(self, old: str, new: str) -> None:
        it = self.mem.pop(old, None)
        if it is not None:
            it["key"] = new
            if it.get("native") and (":th:" in new or ":s:" in new):
                it["native_id"] = new.split(":")[-1]
            self.mem[new] = it
            self._alias(old, new)
            if old in self.last:
                self.last[new] = self.last.pop(old)

    def _alias(self, child: str, key: str) -> None:
        self.alias[child] = key
        while len(self.alias) > 256:
            self.alias.pop(next(iter(self.alias)))

    def note(self, key, text) -> None:
        """P160: the latest thing this item's sub-agent did (a tool description), for the row's detail only — the list
        does not change, so nothing is sent."""
        key = self.alias.get(key, key)
        if key in self.mem and isinstance(text, str) and text.strip():
            self.last[key] = {"text": text[:LAST_MAX], "at": int(time.time())}
            self.mem[key]["activity_at"] = time.time()
            self.native_status(key, "running")

    # -- shared Claude sessions' hook events (shared.py)
    def hook_event(self, ev: dict, scope: str = "shared") -> None:
        if not isinstance(ev, dict):
            return
        name, sid = ev.get("hook_event_name"), clean(ev.get("session_id"), 80)
        if isinstance(ev.get("transcript_path"), str):
            self.claude_transcripts[f"{scope}:{sid}"] = ev["transcript_path"]
            while len(self.claude_transcripts) > 32:
                self.claude_transcripts.pop(next(iter(self.claude_transcripts)))
        now = time.time()
        self.pending = [p for p in self.pending if now - p["at"] < PENDING_TTL]
        if ev.get("agent_id"):
            self.annotate(f"{scope}:{sid}:{clean(ev.get('agent_id'), 80)}", model=ev.get("model"), cwd=ev.get("cwd"))
        if name == "PreToolUse" and ev.get("agent_id"):            # P160: a sub-agent's own tool call → its detail
            self.note(f"{scope}:{sid}:{clean(ev.get('agent_id'), 80)}", tool_desc(ev.get("tool_name"), ev.get("tool_input")))
        if name == "PreToolUse" and ev.get("tool_name") in AGENT_TOOLS and not ev.get("agent_id"):
            ti = ev.get("tool_input") if isinstance(ev.get("tool_input"), dict) else {}
            self.pending = (self.pending + [{"session": sid, "type": clean(ti.get("subagent_type"), WHO_MAX),
                                             "desc": clean(ti.get("description"), 200), "tool_id": clean(ev.get("tool_use_id"), 80), "model": ti.get("model"), "at": now,
                                             "bg": ti.get("run_in_background") is True}])[-16:]
        elif name == "SubagentStart":
            aid, typ = clean(ev.get("agent_id"), 80), clean(ev.get("agent_type"), WHO_MAX)
            match = next((p for p in self.pending if p["session"] == sid
                          and (p["type"] == typ or (not p["type"] and typ.lower() in GENERIC))), None)
            if match is None and not typ:
                return                         # no Agent call of ours (e.g. the compaction summariser): not shown
            if match is not None:
                self.pending.remove(match)
            key = f"{scope}:{sid}:{aid or uuid.uuid4().hex}"
            self.start(key, who_of(typ), "temp",
                       (match or {}).get("desc") or typ, session=f"{scope}:{sid}",
                       harness="claude", model=ev.get("model"), cwd=ev.get("cwd"), role=typ)
            self.native_status(key, "running")
            if (match or {}).get("tool_id"):
                self._alias(f"{scope}:{sid}:tu:{match['tool_id']}", f"{scope}:{sid}:{aid}")
            if (match or {}).get("bg"):
                self.mark_bg(f"{scope}:{sid}:{aid}")
        elif name == "SubagentStop":
            aid = clean(ev.get("agent_id"), 80)
            if aid:            # P426: foreground unless it was launched / listed as background work
                self.stop(f"{scope}:{sid}:{aid}", fg=True)
        elif name == "PostToolUse" and not ev.get("agent_id"):
            # P291: the main thread's background work as soon as it is launched — a run_in_background Bash
            # (backgroundTaskId) is an item of its own; an async Agent whose SubagentStart never arrived is caught up here
            resp = ev.get("tool_response") if isinstance(ev.get("tool_response"), dict) else {}
            ti = ev.get("tool_input") if isinstance(ev.get("tool_input"), dict) else {}
            if resp.get("backgroundTaskId"):
                self._background(scope, sid, {"id": resp["backgroundTaskId"], "type": "shell",
                                              "description": ti.get("description"), "command": ti.get("command")})
            elif resp.get("agentId") and (resp.get("isAsync") or resp.get("status") == "async_launched"):
                self._background(scope, sid, {"id": resp["agentId"], "type": "subagent", "agent_type": ti.get("subagent_type"),
                                              "description": ti.get("description") or resp.get("description")})
            elif ev.get("tool_name") in ("TaskStop", "KillShell", "KillBash"):
                for k in ("task_id", "shell_id", "bash_id"):
                    if ti.get(k):
                        self.stop(f"{scope}:{sid}:{clean(str(ti[k]), 80)}")
        elif name == "SessionEnd":
            self.stop_where(lambda k, v: v.get("session") == f"{scope}:{sid}")
        elif name == "Stop" and not ev.get("agent_id") and isinstance(ev.get("background_tasks"), list):
            # Claude Code's own list of live background work (sub-agents, run_in_background shells, …). P291: it also ADDS
            # what is missing — a lost / refused SubagentStart, a background Bash — so the phone never under-reports.
            live = set()
            for t in ev["background_tasks"]:
                if isinstance(t, dict) and t.get("id"):
                    self.mark_bg(f"{scope}:{sid}:{clean(str(t.get('id')), 80)}")
                if isinstance(t, dict) and t.get("id") and t.get("status") not in BG_DONE:
                    live.add(self._background(scope, sid, t, True))
            # foreground sub-agents are done when the main turn stops; background ones only when the harness drops them
            self.stop_where(lambda k, v: v.get("session") == f"{scope}:{sid}" and k not in live, fg=True)

    def _background(self, scope: str, sid: str, t: dict, late=False) -> str:
        """P291: one entry of Claude Code's background list (Stop.background_tasks / a launch result) on the board; an item
        already there (SubagentStart's, with its better label) is kept as it is. → its key."""
        key = f"{scope}:{sid}:{clean(str(t.get('id')), 80)}"
        if key in self.mem:
            self.mark_bg(key)
            self.native_status(key, "running")
            return key
        typ = clean(t.get("type"), 24) or "task"
        if typ == "subagent":
            role = clean(t.get("agent_type"), WHO_MAX)
            self.start(key, who_of(role), "temp", t.get("description") or role, session=f"{scope}:{sid}",
                       harness="claude", role=role or None, late=late)
        else:                                  # "shell" (run_in_background Bash) and anything newer: labelled by its description
            label = t.get("description")
            if not (isinstance(label, str) and label.strip()) and isinstance(t.get("command"), str):
                from .privacy import redact    # a bare command line may carry a token: scrubbed before it is a label
                label = redact(clean(t["command"], 4 * LABEL_MAX), paths=True)
            self.start(key, None, "temp", label or typ, session=f"{scope}:{sid}", harness="claude", role=typ, late=late)
        self.mark_bg(key)
        self.native_status(key, "running")
        return key

    # -- the owned Claude session's stream-json (agent.py)
    def stream_event(self, ev: dict, scope: str = "own") -> None:
        if not isinstance(ev, dict):
            return
        t = ev.get("type")
        if t == "assistant" and ev.get("parent_tool_use_id"):
            self.annotate(f"{scope}:tu:{clean(ev.get('parent_tool_use_id'), 80)}",
                          model=(ev.get("message") or {}).get("model"))     # P160: inside a sub-agent → its latest tool call
            for b in (ev.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    self.note(f"{scope}:tu:{clean(ev.get('parent_tool_use_id'), 80)}", tool_desc(b.get("name"), b.get("input")))
        if t == "assistant" and not ev.get("parent_tool_use_id"):
            for b in (ev.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in AGENT_TOOLS:
                    inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                    self.start(f"{scope}:tu:{clean(b.get('id'), 80)}", who_of(inp.get("subagent_type")), "temp",
                               inp.get("description") or inp.get("subagent_type") or "", session=scope, role=inp.get("subagent_type"))
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
                    self.stop(key, fg=True)   # P426: its result came back inside this turn
        elif t == "system" and ev.get("subtype") == "task_started":
            tid = clean(ev.get("task_id"), 80)
            if tid and f"{scope}:task:{tid}" not in self.mem and ev.get("task_type") in (None, "local_agent", "agent", "local_bash"):
                # P291: a run_in_background Bash (local_bash) is background work too, labelled by its description
                self.start(f"{scope}:task:{tid}", None, "temp", ev.get("description") or "", session=scope,
                           **({"role": "shell"} if ev.get("task_type") == "local_bash" else {}))
        elif t == "system" and ev.get("subtype") == "task_updated":
            if ((ev.get("patch") or {}).get("status") if isinstance(ev.get("patch"), dict) else None) in BG_DONE:
                self.stop(f"{scope}:task:{clean(ev.get('task_id'), 80)}")
        elif t == "system" and ev.get("subtype") == "task_notification":
            if ev.get("status") in ("completed", "failed", "stopped", "killed", "error"):
                self.stop(f"{scope}:task:{clean(ev.get('task_id'), 80)}")

    def session_gone(self, scope: str = "own") -> None:
        """The owned Claude process ended (halt, /clear, crash): nothing of it runs any more. Codex / OpenCode (P160): the
        app-server / serve child ended — its sub-agents are gone with it."""
        self.stop_where(lambda k, v: v.get("session") == scope)
        self.claude_transcripts.pop(scope, None)
        for k in [k for k in self.child if k.startswith(scope + ":")]:
            del self.child[k]

    # -- Codex app-server notifications (agent_codex.py, owned and shared; P160)
    def codex_event(self, method: str, p: dict, scope: str = "codex") -> None:
        if not isinstance(p, dict) or not isinstance(method, str):
            return
        th = clean(p.get("threadId"), 80)
        if method in ("item/started", "item/completed"):
            it = p.get("item") if isinstance(p.get("item"), dict) else {}
            typ = it.get("type")
            if typ == "collabAgentToolCall":
                self._codex_collab(method, it, scope)
            elif typ == "subAgentActivity":
                tid = clean(it.get("agentThreadId"), 80)
                k = f"{scope}:th:{tid}"
                if it.get("kind") in ("completed", "interrupted"):
                    self.stop(k)
                elif it.get("kind") == "started" and tid:
                    # Codex 0.159 (measured, tests/fixtures): the spawn arrives only as this item; agentPath "/root/<task_name>"
                    # is the label (underscores → spaces); the child's turn/started / turn/completed then open / close it.
                    c = self.child.setdefault(k, {})
                    c.setdefault("label", path_label(it.get("agentPath")))
                    while len(self.child) > 128:
                        self.child.pop(next(iter(self.child)))
                    if k not in self.mem:
                        self.start(k, c.get("who"), "temp", c.get("label") or "", session=scope, **task_meta(c))
            elif th and method == "item/started" and f"{scope}:th:{th}" in self.mem:
                self.note(f"{scope}:th:{th}", codex_item_desc(it))
        elif method == "thread/started":
            t = p.get("thread") if isinstance(p.get("thread"), dict) else {}
            spawn = _find(t.get("source"), "thread_spawn")
            tid = clean(t.get("id"), 80)
            if tid and isinstance(spawn, dict):
                k = f"{scope}:th:{tid}"
                who = who_of(spawn.get("agent_role")) or who_of(spawn.get("agent_nickname"))
                c = self.child.setdefault(k, {})
                c["who"] = who
                c.update(task_meta({"model": t.get("model"), "cwd": t.get("cwd"), "effort": t.get("reasoningEffort"), "role": spawn.get("agent_role")}))
                self.annotate(k, **task_meta(c))
                if k in self.mem and who and self.mem[k]["who"] != who:
                    self.mem[k] = {**self.mem[k], "who": who}
                    self.on_change()
        elif method == "turn/started" and th and f"{scope}:th:{th}" in self.child:
            k = f"{scope}:th:{th}"
            c = self.child[k]
            self.start(k, c.get("who"), "temp", c.get("label") or "", session=scope, **task_meta(c))
        elif method == "turn/completed" and th:
            self.stop(f"{scope}:th:{th}")

    def _codex_collab(self, method: str, it: dict, scope: str) -> None:
        tool, status = it.get("tool"), it.get("status")
        recv = [clean(x, 80) for x in it.get("receiverThreadIds") or [] if isinstance(x, str) and x][:16]
        if tool == "spawnAgent":
            pk = f"{scope}:spawn:{clean(it.get('id'), 80)}"
            label = it.get("prompt") or ""
            if status == "failed":
                self.stop(pk)
            elif recv:
                for r in recv:
                    k = f"{scope}:th:{r}"
                    c = self.child.setdefault(k, {})
                    c.setdefault("label", short(label) if label else "")
                    while len(self.child) > 128:
                        self.child.pop(next(iter(self.child)))
                    if k in self.mem:              # its turn/started came first: give it the spawn's label
                        if not self.mem[k]["label"] and c["label"]:
                            self.mem[k] = {**self.mem[k], "label": c["label"]}
                            self.on_change()
                        continue
                    if _state(it, r) in CODEX_FINAL:
                        continue
                    if pk in self.mem:
                        self.rekey(pk, k)
                        self.mem[k] = {**self.mem[k], "who": c.get("who")}
                        self.on_change()
                    else:
                        self.start(k, c.get("who"), "temp", c.get("label") or label, session=scope, **task_meta(c))
                self.stop(pk)
            elif method == "item/started":
                self.start(pk, None, "temp", label, session=scope)
            else:
                self.stop(pk)
        states = it.get("agentsStates") if isinstance(it.get("agentsStates"), dict) else {}
        for tid, st in list(states.items())[:32]:
            if isinstance(st, dict) and st.get("status") in CODEX_FINAL:
                self.stop(f"{scope}:th:{clean(tid, 80)}")
        if tool == "closeAgent" and method == "item/completed" and status == "completed":
            for r in recv:
                self.stop(f"{scope}:th:{r}")

    # -- OpenCode events (agent_opencode.py v1 / agent_opencode2.py v2, owned and shared; P160)
    def opencode_event(self, ev: dict, sid, scope: str = "opencode", turn_on: bool = False) -> None:
        """v1 (`/event`, `{type, properties}`): the parent session's `task` tool part is the sub-agent — pending/running
        opens it (`input.description`, `input.subagent_type`), completed/error closes it; the child session's tool parts
        (child id from `state.metadata.sessionId`, or a `session.created` with our `parentID`) are its detail."""
        if not isinstance(ev, dict) or not sid:
            return
        t = ev.get("type")
        p = ev.get("properties") if isinstance(ev.get("properties"), dict) else {}
        if t == "message.updated":
            info = p.get("info") if isinstance(p.get("info"), dict) else {}
            if info.get("sessionID") != sid:
                model = info.get("modelID")
                provider = info.get("providerID")
                self.annotate(f"{scope}:s:{clean(info.get('sessionID'), 80)}",
                              model=f"{provider}/{model}" if provider and model else model)
        if t == "message.part.updated":
            part = p.get("part") if isinstance(p.get("part"), dict) else {}
            if part.get("type") != "tool":
                return
            st = part.get("state") if isinstance(part.get("state"), dict) else {}
            inp = st.get("input") if isinstance(st.get("input"), dict) else {}
            if part.get("sessionID") == sid and part.get("tool") == "task":
                k = f"{scope}:task:{clean(part.get('callID') or part.get('id'), 80)}"
                if st.get("status") in ("pending", "running"):
                    self.start(k, who_of(inp.get("subagent_type")), "temp",
                               inp.get("description") or inp.get("subagent_type") or "", session=scope)
                    self.native_status(k, "running")
                    meta = st.get("metadata") if isinstance(st.get("metadata"), dict) else {}
                    if isinstance(meta.get("sessionId"), str):
                        self._alias(f"{scope}:s:{clean(meta['sessionId'], 80)}", k)
                elif st.get("status") in ("completed", "error"):
                    self.stop(k, fg=turn_on)   # P426: ended inside our turn = foreground
            elif isinstance(part.get("sessionID"), str):
                self.note(f"{scope}:s:{clean(part['sessionID'], 80)}", tool_desc(part.get("tool"), inp) or clean(st.get("title"), 200))
        elif t == "session.created":
            info = p.get("info") if isinstance(p.get("info"), dict) else {}
            cid = clean(info.get("id"), 80)
            if info.get("parentID") == sid and cid and f"{scope}:s:{cid}" not in self.alias:
                mine = set(self.alias.values())
                free = [k for k, v in self.mem.items() if v.get("session") == scope and k.startswith(f"{scope}:task:")
                        and k not in mine]
                if free:
                    self._alias(f"{scope}:s:{cid}", free[-1])

    def opencode2_event(self, ev: dict, sid, scope: str = "opencode", turn_on: bool = False) -> None:
        """v2 (`/api/event`, `{type, data}`): a child session (`session.created` whose `parentID` is ours) is the sub-agent;
        its title "<description> (@<agent> subagent)" is the label and who; its `session.execution.succeeded / failed /
        interrupted` closes it, `session.execution.started` re-opens it; its tool events are the detail."""
        if not isinstance(ev, dict) or not sid:
            return
        t = ev.get("type")
        p = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if not isinstance(t, str):
            return
        if t in ("session.created", "session.updated"):
            info = p.get("info") if isinstance(p.get("info"), dict) else p
            cid = clean(info.get("id") or p.get("sessionID"), 80)
            if info.get("parentID") != sid or not cid:
                return
            k = f"{scope}:s:{cid}"
            title = info.get("title") if isinstance(info.get("title"), str) else ""
            m = OC_SUFFIX.search(title)
            c = self.child.setdefault(k, {})
            c.update(who=who_of(info.get("agent") or (m.group(1) if m else None)), label=OC_SUFFIX.sub("", title))
            c.update(task_meta({"model": info.get("model"), "cwd": info.get("directory"), "role": info.get("agent")}))
            if t == "session.created" or k in self.mem:
                self.start(k, c["who"], "temp", c["label"], session=scope, **task_meta(c))
            return
        cid = clean(p.get("sessionID"), 80)
        k = f"{scope}:s:{cid}"
        if not cid or k not in self.child:
            return
        if t in ("session.execution.succeeded", "session.execution.failed", "session.execution.interrupted"):
            self.stop(k, fg=turn_on)
        elif t == "session.execution.started":
            self.start(k, self.child[k].get("who"), "temp", self.child[k].get("label") or "", session=scope)
            # Replace even an identical row: an in-flight poll belongs to the previous execution.
            self.mem[k] = {**self.mem[k], "native_started_at": time.time()}
            self.native_status(k, "running")
        elif t.startswith("session.tool"):
            self.note(k, tool_desc(p.get("tool") or p.get("name"), p.get("input")))

    # -- outside sources (poll, off the event loop)
    def refresh_outside(self, units: bool = True, run=subprocess.run) -> bool:
        """Re-read item files (and, when `units`, the systemd units; otherwise the last scan stays). True = changed."""
        files = read_items(self.root) if self.root is not None else []
        if self.root is not None:
            for receipt in finished_items(self.root):
                self.finish({**receipt, "foreign": True}, receipt.get("exit_code"), receipt.get("ended"))
        if units:
            try:
                scanned = scan_units(run, strict=True)
            except (OSError, subprocess.SubprocessError):
                scanned = None                  # an unavailable bus is not a completion
            if scanned is not None:
                old = {it["key"]: it for it in self.units}
                current = {it["key"] for it in scanned}
                retained = []
                for it in self.units:
                    if it["key"] not in current:
                        result = unit_end(it["key"][5:], run)
                        if result is None:
                            retained.append(it)  # still running, or lookup failed: try next scan
                        else:
                            self.finish({**it, "foreign": True}, result.get("exit_code"), result.get("ended"))
                for it in scanned:
                    if it["key"] in old:
                        it.update(since=old[it["key"]]["since"], report=old[it["key"]].get("report"))
                    else:
                        it["report"] = unit_report(it["key"][5:], run)
                        it["since"] = unit_since(it["key"][5:], run) or it["since"]
                self.units = scanned + retained
        # P302: a unit whose job already registered itself (`agentj busy run` inside it) is shown once — as that item
        got = files + [u for u in getattr(self, "units", []) if not any(same_job(f, u) for f in files)]
        live = {it["key"] for it in got}
        for it in self.outside:
            if not it["key"].startswith("unit:") and it["key"] not in live:
                self.finish({**it, "foreign": True})   # unwrapped pid/stop: exit code is unknowable

        changed = got != self.outside
        self.outside = got
        return changed

    def native_status(self, key, status, now=None):
        """A child-state observation, separate from evidence that its host process exists."""
        v = self.mem.get(key)
        if v is None:
            return
        if status == "done":
            self.stop(key)
        else:
            v["native_status"] = status
            if status == "running":
                v["checked_at"] = time.time() if now is None else now
                v["activity_at"] = v["checked_at"]

    def reconcile_claude(self, scope, transcript=None):
        """Read bounded native task results; missing/truncated state remains unknown (never infer idle).
        Only terminal records for existing IDs are consumed; no prompt, body or path reaches the wire.
        """
        paths = {scope: transcript} if transcript else dict(self.claude_transcripts)
        for owner, path in paths.items():
            if not path or not any(v.get("session") == owner for v in list(self.mem.values())):
                continue
            try:
                with open(path, 'rb') as f:
                    f.seek(0, 2)
                    size = f.tell()
                    f.seek(max(0, size - 262144))
                    if size > 262144:
                        f.readline()
                    lines = f.read(262144).splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    ev = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if not isinstance(ev, dict):
                    continue
                # Use the same parser for a missed foreground result / terminal task notification.
                if ev.get("type") == "user":
                    message = ev.get("message")
                    content = message.get("content") if isinstance(message, dict) else None
                    origin = ev.get("origin") if isinstance(ev.get("origin"), dict) else {}
                    native = origin.get("kind") == "task-notification" or ev.get("turnOrigin") == "task_notification"
                    text = content if native and isinstance(content, str) else ""
                    # Native origin is required: an ordinary user quoting XML is not a task result.
                    for notification in re.findall(r"<task-notification>(.*?)</task-notification>", text, re.S):
                        tid = re.search(r"<task-id>([A-Za-z0-9_-]{1,80})</task-id>", notification)
                        status = re.search(r"<status>(completed|failed|stopped|killed|error)</status>", notification)
                        if tid and status:
                            self.stop(f"{owner}:task:{tid.group(1)}")
                            self.stop(f"{owner}:{tid.group(1)}")
                    for part in content if isinstance(content, list) else []:
                        if isinstance(part, dict) and part.get("type") == "tool_result":
                            key = f"{owner}:tu:{clean(part.get('tool_use_id'), 80)}"
                            target = self.alias.get(key, key)
                            if target in self.mem:
                                text = json.dumps(part.get("content"), ensure_ascii=False)[:4000]
                                if target != key:
                                    if not re.search(r"async_launched|[Aa]sync agent launched|running in the background", text):
                                        self.stop(target, fg=True)
                                else:
                                    self.stream_event({"type":"user", "message":{"content":[part]}}, owner)
                elif ev.get("type") == "system" and ev.get("subtype") in ("task_notification", "task_updated"):
                    self.stream_event(ev, owner)

    def refresh_sources(self, processes=None):
        from .busy_probe import Processes, fields
        processes = processes or Processes()
        evidence = {}
        for v in list(self.mem.values()) + self.outside:
            if v.get("cg") and not v.get("pid"):
                pids = cg_pids(v["cg"])
                if pids:
                    v["pid"] = pids[0]
            f = fields(v, processes)
            if f:
                if not v.get("proc_start"):
                    v["proc_start"] = f["source"]["start"]
                evidence[v["key"]] = f
        self.evidence = evidence

    # -- the wire list
    def items(self, now: float | None = None) -> list[dict]:
        now = time.time() if now is None else now
        def uncertain(v):
            return v.get("native_child") and v.get("harness") in ("claude", "opencode")
        for v in self.mem.values():
            if uncertain(v):
                v["status"] = "running" if (v.get("native_status") == "running" and now - v.get("checked_at", 0) <= TEMP_TTL) else "unknown"
        for k in [k for k, v in self.mem.items() if v["kind"] == "temp" and now - max(v["since"], v.get("activity_at", 0), v.get("checked_at", 0)) > TEMP_TTL and (k not in self.evidence or uncertain(v))]:
            del self.mem[k]
        for k in [k for k in self.last if k not in self.mem]:
            del self.last[k]
        all_ = sorted([v for v in list(self.mem.values()) + self.outside if not self.verify_sources or v["key"] in self.evidence], key=lambda v: (v["since"], v["key"]))[:MAX_ITEMS]
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
                        **({"lk": v["lk"]} if v.get("lk") else {}), "since": v["since"], "c": c, **task_meta(v), **(self.evidence.get(v["key"], {})), **({"status": v["status"]} if "status" in v else {})})
        return out

    def restart_split(self, survives=survives_restart) -> tuple[int, int]:
        """P423 #160: (items a serve restart would interrupt, items that keep running) — the upgrade card counts only the
        first. In-process items (owned / shared sessions, workflow runs) always count; an outside one only unless
        `survives_restart` proves it runs on its own. One `ps` call at most (macOS)."""
        listed = {v["id"] for v in self.items()}
        outside = [v for v in self.outside if v["key"][-40:] in listed]
        kw = {}
        if any(isinstance(v.get("pid"), int) for v in outside) and not Path("/proc/self/stat").exists():
            kw["parents"] = ps_parents()
        keep = sum(1 for v in outside if survives(v, **kw))
        return len(listed) - keep, keep

    def find(self, wid) -> tuple[dict, dict | None] | None:
        """P160: the item behind a wire id (a copy) and its latest step (a copy) — cheap, on the event loop."""
        if not isinstance(wid, str) or not wid:
            return None
        v = next((x for x in list(self.mem.values()) + list(self.outside) if x["key"][-40:] == wid), None)
        if v is None or (self.verify_sources and v["key"] not in self.evidence):
            return None
        v = {**v, **self.evidence.get(v["key"], {})}
        last = self.last.get(v["key"])
        return dict(v), (dict(last) if last else None)

    def detail(self, wid, run=subprocess.run) -> dict | None:
        """P160 (§22 `busy_get`): one row's detail, read-only. None = not running. See detail_of."""
        f = self.find(wid)
        return detail_of(f[0], f[1], run) if f else None

    def take_colors(self) -> dict | None:
        """The colour memory to save (once per change), or None."""
        if not self.colors_dirty:
            return None
        self.colors_dirty = False
        return dict(list(self.colors.items())[-64:])

    def sample_load(self, now: float | None = None, read=None, cap_read=None) -> bool:
        """P154: read the load at most every LOAD_EVERY s; True when the published value moved (> LOAD_STEP, or appeared /
        went away)."""
        now = time.monotonic() if now is None else now
        if self.load_at and now - self.load_at < LOAD_EVERY:
            return False
        self.load_at = now
        v = (read or sys_load)()
        if v is None or self.load is None:
            changed = (v is None) != (self.load is None)
        else:
            changed = abs(v - self.load) > LOAD_STEP
        if changed:
            self.load = v
        c = (cap_read or self.sample_cap)()
        if c != self.cap:
            self.cap = c
            changed = True
        return changed

    def sample_cap(self) -> int | None:
        """P154 追加 11: how many background Agents the Agent J host can run at once = what runs now + P156's `capacity()`
        can_start. None until that function exists (it ships with P156) or when it fails; the phone then shows 6 robots."""
        fn = globals().get("capacity")
        if fn is None:
            return None
        try:
            running = counts(self.items())["task_count"]
            return max(1, min(64, running + int(fn(running)["can_start"])))
        except Exception:  # noqa: BLE001 — display only
            return None

    def msg(self, by=None) -> dict:
        tasks = self.items()
        m = {"t": "busy", "tasks": tasks, "by": by, **counts(tasks)}
        if self.load is not None:
            m["load"] = self.load
        if self.cap is not None:
            m["cap"] = self.cap
        return m


def detail_of(v: dict, last: dict | None, run=subprocess.run) -> dict:
    """One item's detail: who / label / since / src, the sub-agent's latest tool call (`last`) and, for a dispatch unit or
    an item file that names one, its REPORT (path + first line). Display text is redacted (privacy.redact). Blocking
    (systemctl, a file read): serve calls it off the event loop with the copies Board.find made there."""
    from . import privacy

    def red(s):
        return privacy.redact(s, paths=True) if isinstance(s, str) else s
    wid = v["key"][-40:]
    d = {"id": wid, "who": v["who"], "kind": v["kind"], "label": v["label"], "since": v["since"],
         **({"lk": v["lk"]} if v.get("lk") else {})}
    d.update({k: v[k] for k in ("detected", "reported", "conflict", "source", "live_pids") if k in v})
    src = v["key"].split(":", 1)[0]
    d["src"] = ("unit" if src == "unit" else "file" if src in ("cli", "run") else "workflow" if src == "wf"
                else "codex" if src.endswith("codex") else "opencode" if src.endswith("opencode") else "claude")
    if last:
        d["last"] = {"text": red(last["text"]), "at": last["at"]}
    if v.get("cmd"):
        d["cmd"] = red(v["cmd"])[:CMD_MAX]                     # P200: the raw command line, detail only
    path = v.get("report") or (unit_report(v["key"][5:], run) if src == "unit" else None)
    rep = report_head(path) if path else None
    if rep:
        d["report"] = {"path": red(rep["path"]), "head": red(rep["head"]) if rep["head"] else None, "at": rep["at"]}
    return d


def save_colors(root, colors: dict) -> None:
    d = busy_dir(root)
    with contextlib.suppress(OSError):
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = d / f".colors.{os.getpid()}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(colors, f, ensure_ascii=False)
        os.replace(tmp, d / "colors.json")


# ------------------------------------------------------------------------------------------- P156 追加 1: capacity
PER_JOB_MIB = 700            # a headless Agent (claude -p / codex exec / opencode run) and its tools, typical resident size
RESERVE_MIB = 1024           # kept free for the owner's own programs
CPU_SHARE = 0.8              # never plan past 80 % of the cores
CAP_MAX = 16


def _mem_available_mib() -> int | None:
    """Available memory (MiB) on Linux / WSL (MemAvailable), macOS (vm_stat free + inactive + speculative), Windows
    (GlobalMemoryStatusEx); None when it cannot be read."""
    with contextlib.suppress(OSError, ValueError):
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    if sys.platform == "darwin":
        with contextlib.suppress(OSError, ValueError, subprocess.SubprocessError):
            out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
            m = re.search(r"page size of (\d+) bytes", out)
            page = int(m.group(1)) if m else 4096
            pages = sum(int(x) for x in re.findall(r"Pages (?:free|inactive|speculative):\s+(\d+)", out))
            return pages * page // (1024 * 1024) if pages else None
    if sys.platform == "win32":
        with contextlib.suppress(Exception):
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            ms = MS()
            ms.dwLength = ctypes.sizeof(MS)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
                return int(ms.ullAvailPhys // (1024 * 1024))
    return None


def capacity(running: int = 0, cores: int | None = None, load: float | None = -1.0, avail_mib: int | None = -1) -> dict:
    """How many more background Agents the Agent J host can take now — by load, not a fixed number (the owner, 2026-10-12: 「按负载的
    动态规则……主机配置高就多起一些」). CPU room = 80 % of the cores minus the 1-minute load (without a load average: half the
    cores minus what runs); memory room = (available − 1 GiB) / 700 MiB. The smaller wins; a harness's own limit still applies."""
    cores = cores if cores is not None else (os.cpu_count() or 1)
    if load == -1.0:
        load = None
        with contextlib.suppress(OSError, AttributeError):
            load = round(os.getloadavg()[0], 2)
    if avail_mib == -1:
        avail_mib = _mem_available_mib()
    cpu_room = cores * CPU_SHARE - load if load is not None else cores / 2 - running
    mem_room = (avail_mib - RESERVE_MIB) / PER_JOB_MIB if avail_mib is not None else cpu_room
    more = max(0, min(CAP_MAX, int(min(cpu_room, mem_room))))
    tight = "cpu" if cpu_room <= mem_room else "memory"
    return {"cores": cores, "load1": load, "mem_available_mib": avail_mib, "running": running, "can_start": more,
            "limited_by": tight if more < CAP_MAX else None,
            "advice_zh": (f"机器空，可再起 {more} 条" if more else "机器紧，新活先排队，等手上的完成再起"),
            "advice_en": (f"room for {more} more" if more else "busy: queue new work until something finishes")}


# ------------------------------------------------------------------------------------------- CLI
def _root(a):
    from .state import state_dir
    return state_dir()


def counts(tasks: list[dict]) -> dict:
    """Count the published snapshot, independent of transport or presentation flags.

    Unknown children remain visible but are not active tasks. Their verified host
    processes are still alive; deduplicate PIDs across every published row.
    """
    unknown = sum(t.get("status") == "unknown" for t in tasks)
    return {"task_count": len(tasks) - unknown, "unknown_count": unknown,
            "live_process_count": len({p for t in tasks for p in t.get("live_pids", [])})}


def cmd_busy(a) -> int:
    root = _root(a)
    if a.busy_cmd == "list":
        from .cli import _ctl_quiet
        from .state import State
        native = _ctl_quiet(State(), {"cmd": "busy_list"})
        if native and native.get("ok"):
            snapshot = native["busy"]
        else:
            b = Board(root, verify_sources=True)
            b.refresh_outside(units=True)
            b.refresh_sources()
            snapshot = b.msg()
        snapshot["complete"] = bool(native and native.get("ok"))
        totals = counts(snapshot["tasks"])
        tasks = snapshot["tasks"]
        if a.no_units:
            tasks = [t for t in tasks if t.get("source", {}).get("kind") != "unit" and not t["id"].startswith("unit:")]
        snapshot = {**snapshot, "tasks": tasks, **totals}
        if a.json:
            print(json.dumps(snapshot, ensure_ascii=False))
        else:
            print(" ".join(f"{k}={snapshot[k]}" for k in totals))
            for t in tasks:
                print(f"{t['who'] or '临时助理'} · {t['label']}")
            if not tasks:
                print("没有在后台干活的 Agent / nothing running")
        return 0
    if a.busy_cmd == "capacity":
        b = Board(root, verify_sources=True)
        with contextlib.suppress(Exception):
            b.refresh_outside(units=True)
        try:
            b.refresh_sources()
            running = counts(b.items())["task_count"]
        except Exception:  # noqa: BLE001
            running = 0
        c = capacity(running)
        if a.json:
            print(json.dumps(c, ensure_ascii=False))
        else:
            mem = f"{c['mem_available_mib'] / 1024:.1f} GiB" if c["mem_available_mib"] is not None else "—"
            print(f"核数 {c['cores']} · 负载 {c['load1'] if c['load1'] is not None else '—'} · 可用内存 {mem} · "
                  f"后台在跑 {c['running']} 条 → {c['advice_zh']}")
            print(f"cores {c['cores']} · load {c['load1'] if c['load1'] is not None else '—'} · available {mem} · "
                  f"running {c['running']} → {c['advice_en']}（harness 自己的并发上限照守 / a harness's own limit still applies）")
        return 0
    if a.busy_cmd == "show":
        b = Board(root, verify_sources=True)
        b.refresh_outside(units=not a.no_units)
        b.refresh_sources()
        d = b.detail(a.id)
        if a.json:
            print(json.dumps(d, ensure_ascii=False))
        elif d is None:
            print("没有这一条（已结束？）/ not running", file=sys.stderr)
        else:
            print(f"{d['who'] or '临时助理'} · {d['label']}")
            if d.get("last"):
                print("最近 / latest: " + d["last"]["text"])
            if d.get("cmd"):
                print("命令 / command: " + d["cmd"])
            if d.get("report"):
                print("报告 / report: " + d["report"]["path"] + ("\n  " + d["report"]["head"] if d["report"]["head"] else
                                                                 "（还没有 / not yet）"))
        return 0 if d is not None else 1
    if a.busy_cmd == "start":
        it = item("cli:" + (a.key or uuid.uuid4().hex[:12]), a.who, a.kind, a.label, pid=a.pid, ns=pidns(), report=a.report, **task_meta(vars(a)))
        if not isinstance(a.pid, int) or a.pid <= 0:
            print("需要存活来源 --pid / a live --pid is required", file=sys.stderr)
            return 2
        from .busy_probe import Processes
        it["proc_start"] = Processes().identity(a.pid)
        if not it["proc_start"]:
            return 2
        it["self_reported"] = True
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
            path = write_item(root, item(key, a.who, a.kind, a.label, pid=os.getpid(), ns=pidns(), report=a.report, cwd=os.getcwd(), **task_meta({k:v for k,v in vars(a).items() if k != "cwd"})))
        except OSError as e:              # e.g. no state dir: the work still runs, only unseen on the phone
            print(f"agentj busy: 手机上不会显示这件事 / not shown on the phone ({e.__class__.__name__})", file=sys.stderr)
        stop = threading.Event()

        def beat():
            while path is not None and not stop.wait(HEARTBEAT):
                with contextlib.suppress(OSError):
                    os.utime(path)
        threading.Thread(target=beat, daemon=True).start()
        code = 127
        try:
            code = subprocess.call(cmd)
            return code
        except OSError as e:
            print(e, file=sys.stderr)
            return 127
        finally:
            stop.set()
            if path is not None:
                with contextlib.suppress(OSError, ValueError):
                    it = json.loads(path.read_text())
                    write_finished(root, it, code)
            remove_item(root, key)
    return 2


def add_parser(sub) -> None:
    p = sub.add_parser("busy", help="在办事项（手机回复卡右上角的像素小机器人）：list · show · start · stop · run / who is working "
                                    "in the background (the robots on the phone)")
    ps = p.add_subparsers(dest="busy_cmd", required=True)
    x = ps.add_parser("list", help="现在谁在干活 / who is working now")
    x.add_argument("--json", action="store_true")
    x.add_argument("--no-units", action="store_true", help="不看 systemd 的 agentj-p* 单元 / skip systemd units")
    for n, h in (("start", "登记一件在办事项 / add an item"), ("run", "登记、运行命令、结束时撤下 / add, run a command, remove")):
        x = ps.add_parser(n, help=h)
        x.add_argument("--who", default="", help="谁（CEO 名字；空 = 临时助理）/ who (empty = a helper)")
        x.add_argument("--label", default="", help="在干什么（一句话标题，最多 120 字）/ what (a one-line title, ≤ 120 characters)")
        x.add_argument("--kind", choices=("ceo", "temp"), default="ceo")
        x.add_argument("--report", default=None, help="报告文件路径：手机点开这一条时显示它的第一行 / the REPORT file whose first "
                                                     "line the phone shows when this row is tapped")
        for field in ("harness", "model", "workflow", "role", "parent"):
            x.add_argument("--" + field, default=None, help="self-reported display metadata (not detection): " + field)
        if n == "start":
            x.add_argument("--key", default="", help="稍后 stop 用的名字 / the name to stop it with")
            x.add_argument("--pid", type=int, default=None, help="这个进程结束就自动撤下 / removed when this pid ends")
        else:
            x.add_argument("command", nargs=argparse.REMAINDER)
    x = ps.add_parser("capacity", help="还能再起几条后台活：核数、负载、可用内存、在跑的条数 → 建议 / how many more background "
                                         "jobs the Agent J host can take now")
    x.add_argument("--json", action="store_true")
    x = ps.add_parser("show", help="一条的详情：最近一步、报告首行（只读）/ one item's detail: latest step, report's first line")
    x.add_argument("id", help="`busy list --json` 里的 id / the id from `busy list --json`")
    x.add_argument("--json", action="store_true")
    x.add_argument("--no-units", action="store_true")
    x = ps.add_parser("stop", help="撤下一件 / remove an item")
    x.add_argument("key")
    p.set_defaults(fn=cmd_busy)
