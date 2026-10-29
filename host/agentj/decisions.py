"""Decision log `agentj.decision.v1` (P571, 0.26.0, after pstack's show-me-your-work) — ONE format for the
default path and the Agent J Harness kernel (core/orders.py `decisions.jsonl`), so the kernel can turn on later without
migrating anything.

One JSON object per line, append-only (O_APPEND, 0600, never through a link):
    ts · phase · decision · why · evidence · result          (P359's fields, unchanged)
    + who                                                     (filled by the host: harness and run, never by the model)

Where `agentj decide` writes (plain names only — no hash or session id, the phone masks id-like strings):
    --dir <workflow>   → <workflow>/reports/decisions.jsonl            a workflow CEO's run
    (no --dir)         → <working root>/.agentj/decisions/<YYYY-MM-DD>.jsonl   the main conversation / a goal
    a kernel order     → <state>/orders/<id>/decisions.jsonl          (core/orders.py, through record() below)
`evidence` is a pointer (file:line, commit, log path), not a copy. Text that looks like a credential is refused, not stored;
an email address or phone number is stored masked (`***@domain`, `***` + last four digits). `reports/` gets a `.gitignore`
line for `decisions.jsonl`, so a workflow's log never enters its git (the rest of `reports/` is the workflow's own business).

Also shared with the kernel: the retry table by failure kind and the root-cause hash (pstack ④; same cause twice → stop and
escalate). core/orders.py re-exports them under their old names.
"""
from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
import re
import stat
import sys
import time
from pathlib import Path

SCHEMA = "agentj.decision.v1"
FIELDS = ("ts", "phase", "decision", "why", "evidence", "result")
LIMITS = {"phase": 40, "decision": 300, "why": 500, "evidence": 300, "result": 200}   # P359's per-field caps
LINE_MAX = 1024                       # bytes per line written by `agentj decide` (one decision per stage, not per command)
READ_MAX = 4 * 1024 * 1024            # a log larger than this is read from its tail only
SINCE_MAX_DAYS = 366                  # `list --since` reaches back at most this far (one file per day)
DIR = "decisions"                     # <working root>/.agentj/decisions/
WORKFLOW_FILE = ("reports", "decisions.jsonl")
# privacy kinds that are locations, not credentials: an evidence pointer like ~/work/reports/run.log:12 or a LAN address;
# an email address or phone number is not refused either — record() masks it (mask_contacts) before anything is written
NOT_SECRETS = {"home_path", "private_ip", "host", "user", "email", "phone"}
IGNORE_LINE = WORKFLOW_FILE[1]                # the line `agentj decide --dir` keeps in <workflow>/reports/.gitignore


class DecisionError(ValueError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code, self.detail = code, detail


# ------------------------------------------------------------------------------------------------ the record
def mask_contacts(text: str) -> str:
    """An email keeps its domain (`***@example.com`), a phone number its last four digits (`***5678`)."""
    from .privacy import _EMAIL, _PHONES
    text = _EMAIL.sub(lambda m: "***@" + m.group(1), text)
    for pat in _PHONES:
        text = pat.sub(lambda m: "***" + re.sub(r"\D", "", m.group(0))[-4:], text)
    return text


def record(phase: str, decision: str, why: str, evidence: str = "", result: str = "", who: dict | None = None,
           ts: float | None = None) -> dict:
    """One line of the log, contacts masked, each field cut to its P359 cap (the kernel's behaviour: it never refuses its
    own model)."""
    r = {"ts": round(time.time() if ts is None else ts, 3)}
    for k, v in (("phase", phase), ("decision", decision), ("why", why), ("evidence", evidence), ("result", result)):
        r[k] = mask_contacts(str(v or ""))[:LIMITS[k]]
    if who:
        r["who"] = who
    return r


def problems(phase: str, decision: str, why: str, evidence: str = "", result: str = "") -> str | None:
    """Why `agentj decide` refuses a line (the CLI never cuts silently: the model shortens and retries)."""
    from .privacy import redact_with_report
    vals = {"phase": phase, "decision": decision, "why": why, "evidence": evidence, "result": result}
    if not (decision or "").strip():
        return "decision_empty"
    if not (why or "").strip():
        return "why_empty"
    for k, v in vals.items():
        v = v or ""
        if len(v) > LIMITS[k]:
            return f"{k}_too_long"
        if any(ord(c) < 32 and c not in "\t" for c in v):
            return "control_characters"
        if set(redact_with_report(v, owner=True)[1]) - NOT_SECRETS:   # the owner's own file: any secret shape refuses
            return "looks_like_a_secret"
    return None


def who_am_i(environ=None) -> dict:
    """The harness and run this command runs under, from the environment the harness / host set (not a model argument).
    A provenance label only: the Agent runs this command itself and can set its own environment — never authorize or
    deduplicate on it."""
    env = os.environ if environ is None else environ
    if env.get("CLAUDECODE") == "1":
        harness = "claude"
    elif any(k.startswith("CODEX_") for k in env if k not in ("CODEX_HOME",)):
        harness = "codex"
    elif env.get("OPENCODE") or env.get("OPENCODE_CONFIG_CONTENT"):
        harness = "opencode"
    else:
        harness = "terminal"
    out = {"harness": harness}
    rep = env.get("AGENTJ_REPORT")                 # a `busy run` / dispatch unit: the report it writes names the run
    if rep:
        from .privacy import redact
        out["run"] = redact(os.path.basename(os.path.dirname(rep.rstrip("/"))) or os.path.basename(rep), owner=True)[:80]
    return out


# ------------------------------------------------------------------------------------------------ files (never through a link)
def append(path: Path, obj: dict) -> None:
    """The kernel's append (orders._append): O_APPEND | O_NOFOLLOW, 0600."""
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def parse(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        try:
            v = json.loads(line)
        except ValueError:
            continue
        if isinstance(v, dict):
            out.append(v)
    return out


def read(path: Path) -> list[dict]:
    """orders._jsonl: every well-formed object line; a missing file is an empty log."""
    try:
        return parse(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []


def _line(obj: dict) -> bytes:
    raw = (json.dumps(obj, ensure_ascii=False) + "\n").encode()
    if len(raw) > LINE_MAX:
        raise DecisionError("line_too_long", f"> {LINE_MAX} bytes: shorten, put detail in the evidence file")
    return raw


def _append_at(dir_fd: int, name: str, raw: bytes) -> None:
    try:
        fd = os.open(name, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), 0o600, dir_fd=dir_fd)
    except OSError as e:
        if e.errno in (errno.ELOOP, errno.EISDIR):
            raise DecisionError("unsafe_log", name) from None
        raise
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
            raise DecisionError("unsafe_log", name)
        os.write(fd, raw)                          # one write of ≤ 1 KB with O_APPEND: lines from parallel writers never mix
    finally:
        os.close(fd)


def _ignore_log(dir_fd: int) -> None:
    """`<workflow>/reports/.gitignore` names decisions.jsonl: created when missing, the line appended when absent."""
    try:
        fd = os.open(".gitignore", os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), 0o644,
                     dir_fd=dir_fd)
    except OSError as e:
        if e.errno in (errno.ELOOP, errno.EISDIR):
            raise DecisionError("unsafe_log", ".gitignore") from None
        raise
    with os.fdopen(fd, "r+b") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
            raise DecisionError("unsafe_log", ".gitignore")
        have = f.read(READ_MAX).decode("utf-8", "replace")
        rules = {x.strip() for x in have.splitlines()}
        if rules & {IGNORE_LINE, "/" + IGNORE_LINE, "*", "*.jsonl"}:
            return
        f.write((("" if not have or have.endswith("\n") else "\n") + IGNORE_LINE + "\n").encode())


def _read_at(dir_fd: int, name: str) -> list[dict]:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), dir_fd=dir_fd)
    except FileNotFoundError:
        return []
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode):
            return []
        if st.st_size > READ_MAX:
            f.seek(st.st_size - READ_MAX)
            f.readline()                           # drop the cut line
        return parse(f.read().decode("utf-8", "replace"))


@contextlib.contextmanager
def _workflow_reports(workflow: str, create: bool):
    """fd of `<workflow>/reports/` — the workflow folder is the owner's (it may be reached through a link); `reports` and the
    log inside it are opened without following links."""
    from .inbox import Unsafe, _open_dir
    root = os.open(os.path.realpath(os.path.expanduser(workflow)), os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0))
    try:
        try:
            d = _open_dir(WORKFLOW_FILE[0], root, create)
        except Unsafe as e:
            raise DecisionError("unsafe_log", str(e)) from None
    finally:
        os.close(root)
    try:
        yield d
    finally:
        os.close(d)


@contextlib.contextmanager
def _root_decisions(root: str, create: bool):
    """fd of `<working root>/.agentj/decisions/` through the inbox's O_NOFOLLOW chain (.agentj is 0700 with .gitignore *)."""
    from .inbox import Unsafe, _open_dir, dot_dir
    try:
        with dot_dir(root, create=create) as dot:
            d = _open_dir(DIR, dot, create)
            try:
                yield d
            finally:
                os.close(d)
    except Unsafe as e:
        raise DecisionError("unsafe_log", str(e)) from None


def day_name(t: float | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(time.time() if t is None else t)) + ".jsonl"


def working_root() -> str:
    """The configured working root when this process can read the host's state (outside the fence); inside the fence the
    state directory is empty and the main Agent's cwd IS the working root."""
    with contextlib.suppress(Exception):
        from .state import State
        ag = State().agent_config()
        if ag and ag.get("dir"):
            from .main_identity import working_root as wr
            return str(wr(ag))
    return os.getcwd()


def where(workflow: str | None, root: str | None = None, t: float | None = None) -> str:
    if workflow:
        return os.path.join(os.path.realpath(os.path.expanduser(workflow)), *WORKFLOW_FILE)
    return os.path.join(root or working_root(), ".agentj", DIR, day_name(t))


def add(decision: str, why: str, evidence: str = "", phase: str = "", result: str = "", workflow: str | None = None,
        root: str | None = None, environ=None, now: float | None = None) -> dict:
    why_not = problems(phase, decision, why, evidence, result)
    if why_not:
        raise DecisionError(why_not)
    obj = record(phase, decision, why, evidence, result, who=who_am_i(environ), ts=now)
    raw = _line(obj)
    if workflow:
        with _workflow_reports(workflow, True) as d:
            _ignore_log(d)
            _append_at(d, WORKFLOW_FILE[1], raw)
    else:
        with _root_decisions(root or working_root(), True) as d:
            _append_at(d, day_name(now), raw)
    return obj


def parse_since(raw: str | None, now: float | None = None) -> float | None:
    """`12h`, `30m`, `2d` back from now, or an ISO date / date-time (local time when it has no zone)."""
    if not raw:
        return None
    now = time.time() if now is None else now
    m = re.fullmatch(r"\s*(\d+)\s*([mhd])\s*", raw)
    if m:
        return now - int(m.group(1)) * {"m": 60, "h": 3600, "d": 86400}[m.group(2)]
    from datetime import datetime
    try:
        return datetime.fromisoformat(raw.strip()).timestamp()
    except ValueError:
        raise DecisionError("bad_since", "12h | 30m | 2d | 2026-10-29 | 2026-10-29T08:00") from None


def entries(workflow: str | None = None, root: str | None = None, since: float | None = None, now: float | None = None) -> list[dict]:
    """Decisions oldest first. The working root keeps one file per day: read the days `since` covers (default: today)."""
    out: list[dict] = []
    try:
        if workflow:
            with _workflow_reports(workflow, False) as d:
                out = _read_at(d, WORKFLOW_FILE[1])
        else:
            now = time.time() if now is None else now
            start = max(since if since is not None else now, now - SINCE_MAX_DAYS * 86400)
            days, t = [], start
            while True:
                days.append(day_name(t))
                if day_name(t) == day_name(now) or t > now:
                    break
                t += 86400
            with _root_decisions(root or working_root(), False) as d:
                for name in dict.fromkeys(days):
                    out += _read_at(d, name)
    except FileNotFoundError:
        return []
    if since is not None:
        out = [e for e in out if isinstance(e.get("ts"), (int, float)) and e["ts"] >= since]
    return out


# ------------------------------------------------------------------------------------------------ retries (pstack ④, shared with the kernel)
RETRY = {"budget": "narrow_scope", "context": "narrow_scope", "oom": "narrow_scope", "network": "retry_same",
         "rate_limit": "retry_same", "server": "retry_same", "tool_error": "switch_model", "auth": "ask_owner",
         "unknown": "retry_once"}


def failure_kind(llm_kind: str | None = None, aborted: str | None = None, exit_code=None) -> str:
    if aborted == "budget":
        return "budget"
    if exit_code in (137, -9):
        return "oom"
    if llm_kind in ("network", "rate_limit", "server", "auth", "context"):
        return llm_kind
    if llm_kind in ("bad_request", "bad_reply", "model"):
        return "tool_error"
    return "unknown"


def root_cause_hash(stage: str, err_class: str, places: list[str]) -> str:
    """sha256(工序|错误类别|前 3 个 file:line)[:12] — the same hash twice in a row after a resume = stop and escalate."""
    return hashlib.sha256("|".join([stage, err_class] + list(places)[:3]).encode()).hexdigest()[:12]


_PLACE = re.compile(r"([\w./-]+\.\w+):(\d+)")


def places_of(text: str) -> list[str]:
    return [f"{m.group(1)}:{m.group(2)}" for m in _PLACE.finditer(text or "")][:3]


def retry_advice(history: list[dict]) -> str:
    """history: result records of one job's runs, oldest first → the action for the next run."""
    if not history:
        return "go"
    last = history[-1]
    if last.get("verdict") in ("done",):
        return "none"
    fails = [h for h in history if h.get("verdict") in ("failed", "no_report")]
    if len(history) >= 2 and history[-1].get("root_cause_hash") and \
            history[-1].get("root_cause_hash") == history[-2].get("root_cause_hash"):
        return "escalate"                         # the same root cause twice: stop, tell the superior
    if len(fails) >= 2 and fails[-1] is last:
        return "escalate"
    return RETRY.get(last.get("failure_kind") or "unknown", "retry_once")


# ------------------------------------------------------------------------------------------------ CLI
WHY = {"decision_empty": ("决定是空的", "the decision is empty"),
       "why_empty": ("要写理由：--why \"…\"", "give the reason: --why \"…\""),
       "control_characters": ("不能含控制字符（换行请拆成两条）", "no control characters (split it into two decisions)"),
       "looks_like_a_secret": ("像密钥或密码，没有写入；只写指向证据的位置", "looks like a credential: not written; point at the evidence instead"),
       "line_too_long": (f"一行超过 {LINE_MAX} 字节：缩短，细节放进证据文件", f"over {LINE_MAX} bytes: shorten it, put detail in the evidence file"),
       "unsafe_log": ("流水文件或它的目录是链接、或不属于你：为安全起见没有写", "the log or its folder is a link or not yours: nothing written"),
       "bad_since": ("--since 写 12h、30m、2d 或日期", "--since takes 12h, 30m, 2d or a date")}


def add_parser(sub) -> None:
    p = sub.add_parser("decide", help="决策流水：边做边记关键决定（agentj decide \"<决定>\" --why \"<理由>\"）· list 列出 / "
                                      "the decision log: record key decisions as you go · list")
    p.add_argument("text", nargs="*", help="决定（一句话）；或 list 列出流水 / the decision in one sentence, or list")
    p.add_argument("--why", help="理由 / why")
    p.add_argument("--evidence", default="", help="证据指针：文件:行、提交、日志路径 / a pointer: file:line, commit, log path")
    p.add_argument("--phase", default="", help="阶段，例如 调研、实现、验收 / the stage, e.g. research, build, check")
    p.add_argument("--result", default="", help="结果（可选） / the outcome (optional)")
    p.add_argument("--dir", help="工作流目录：写到 <目录>/reports/decisions.jsonl；不写 = 工作根目录 / a workflow folder: writes <dir>/reports/decisions.jsonl; default the working root")
    p.add_argument("--since", help="list：12h、30m、2d 或日期（默认今天） / list: 12h, 30m, 2d or a date (default today)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_decide)


def _fail(code: str, as_json: bool, detail: str = "") -> int:
    zh, en = WHY.get(code, (code, code))
    if as_json:
        print(json.dumps({"ok": False, "why": code, **({"detail": detail} if detail else {})}, ensure_ascii=False))
    else:
        print(f"没写 / not written: {zh} / {en}" + (f"（{detail}）" if detail else ""), file=sys.stderr)
    return 1


def _show(e: dict) -> str:
    t = time.strftime("%m-%d %H:%M", time.localtime(e["ts"])) if isinstance(e.get("ts"), (int, float)) else "?"
    line = t + (f" [{e['phase']}]" if e.get("phase") else "") + " " + str(e.get("decision", ""))
    line += "\n    理由 / why: " + str(e.get("why", ""))
    if e.get("evidence"):
        line += "\n    证据 / evidence: " + str(e["evidence"])
    if e.get("result"):
        line += "\n    结果 / result: " + str(e["result"])
    return line


def cmd_decide(a) -> None:
    rc = run(a)
    if rc:
        sys.exit(rc)      # cli.main ignores return values: a refusal must not exit 0


def run(a) -> int:
    words = list(a.text or [])
    if words == ["list"] and a.why is None:
        try:
            since = parse_since(a.since)
            rows = entries(a.dir, since=since)
        except DecisionError as e:
            return _fail(e.code, a.json, e.detail)
        except OSError as e:
            return _fail("unsafe_log" if isinstance(e, PermissionError) else type(e).__name__, a.json)
        path = where(a.dir)
        if a.json:
            print(json.dumps({"ok": True, "schema": SCHEMA, "path": path, "count": len(rows), "decisions": rows}, ensure_ascii=False))
            return 0
        if not rows:
            print(f"还没有记录 / no decisions yet（{path}）")
            return 0
        print("\n".join(_show(e) for e in rows))
        print(f"共 {len(rows)} 条 / {len(rows)} decision(s) · {path}")
        return 0
    if a.since:
        return _fail("bad_since", a.json, "--since 只用于 list / only with list")
    try:
        obj = add(" ".join(words), a.why or "", a.evidence, a.phase, a.result, workflow=a.dir)
    except DecisionError as e:
        return _fail(e.code, a.json, e.detail)
    except OSError as e:
        return _fail(type(e).__name__, a.json)
    path = where(a.dir, t=obj["ts"])
    if a.json:
        print(json.dumps({"ok": True, "path": path, "decision": obj}, ensure_ascii=False))
    else:
        print(f"记下了 / recorded · {path}")
    return 0
