"""Order protocol `agentj.order.v1` (AGENTJ-CORE-SPEC 12.3 – 12.5, §14, pstack ① – ⑤): one order = one directory under
`<state>/orders/<id>/` (0700, files 0600). The directory is the only truth; the main Agent (dispatch tool), the CLI
(`agentj order`), the dispatcher and the CEO runner all read and write it — never a key, an environment or a full tool output.

    order.json      who sent it, project, harness/model, mode, budget, origin kind, host, brief      派单方
    prompt.md       the task text                                                                     派单方
    inbox/NNN.md    appended instructions, append-only; delivered at the CEO's next step boundary      派单方
    control         {"action": "stop"} — the CEO writes its partial report and stops                   派单方 / 主机
    events.jsonl    started · step · refused · appended · subagent_start/end · stopping · budget · done/failed/stopped
    report.md       the CEO's report: the head (完成 / ready / 需要上级 / blocked-by) + body + VERDICT line
    result.json     exit code, wall clock, tokens, parsed head, verdict (12.5), failure_kind, root_cause_hash
    decisions.jsonl the decision log (pstack ③): ts · phase · decision · why · evidence · result, append-only —
                    `agentj.decision.v1`, the same format `agentj decide` writes (agentj/decisions.py)
    gates.jsonl     open questions with options, a DEFAULT answer and a deadline (pstack ⑤; the card is C19)
    denied.jsonl    step digests the owner refused in this order (O8: no session of the order may retry them)

Standing orders (pstack ①): `<state>/orders/standing/<project hash>.md`, numbered one-line directives only the main Agent /
the owner writes; pasted verbatim into every new order AND every resume of that project, never left for the model to find.
Brief (pstack ②): goal · scope.write · verify · timebox · report; a missing field refuses the order (research orders need no
verify and write nothing).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path

from .. import decisions

SCHEMA = "agentj.order.v1"
ORIGINS = ("owner_turn", "task", "goal", "bot", "cli")
MODES = ("work", "research")
TERMINAL = ("done", "failed", "stopped", "budget")
WALL_DEFAULT, WALL_MAX = 120, 480          # minutes (D12)
CEO_MAX, SUB_MAX = 3, 4                    # running CEOs (all main sessions) · sub-agents per CEO (D12)
HEAD_MAX = 300                             # tokens the main Agent gets per order (12.8)
SECTION_MAX = 2000                         # tokens per report(section=…)
ID_RE = re.compile(r"^o\d{8}-\d{6}-[0-9a-f]{4}$")


class OrderError(ValueError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code, self.detail = code, detail


def root(st_root=None) -> Path:
    if st_root is None:
        from ..state import state_dir
        st_root = state_dir()
    return Path(st_root) / "orders"


def _mk(p: Path) -> Path:
    p.mkdir(mode=0o700, parents=True, exist_ok=True)
    return p


def _write(p: Path, data: str) -> None:
    tmp = p.with_name(f".{p.name}.{os.getpid()}.{secrets.token_hex(3)}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(tmp, p)


_append = decisions.append          # P571: one append / read for every decisions.jsonl (agentj/decisions.py)
_jsonl = decisions.read


# ------------------------------------------------------------------------------------------------ brief (pstack ②)
def brief_problems(brief, mode: str) -> list[str]:
    if not isinstance(brief, dict):
        return ["brief"]
    miss = []
    if not str(brief.get("goal") or "").strip():
        miss.append("goal")
    sc = brief.get("scope") if isinstance(brief.get("scope"), dict) else {}
    w = sc.get("write")
    if mode == "work" and (not isinstance(w, list) or not all(isinstance(x, str) and x.strip() for x in w) or not w):
        miss.append("scope.write")
    v = brief.get("verify")
    if mode == "work" and (not isinstance(v, list) or not v or not all(isinstance(x, str) and x.strip() for x in v)):
        miss.append("verify")
    t = brief.get("timebox")
    if not (type(t) is int and 1 <= t <= WALL_MAX):
        miss.append("timebox")
    if not str(brief.get("report") or "").strip():
        miss.append("report")
    return miss


def brief_text(brief: dict) -> str:
    sc = brief.get("scope") or {}
    lines = [f"Goal: {brief['goal']}", "Write scope: " + (", ".join(sc.get("write") or []) or "(none — read only)")]
    if sc.get("forbidden"):
        lines.append("Forbidden: " + ", ".join(sc["forbidden"]))
    if brief.get("verify"):
        lines.append("Verify (run before you claim success): " + " ; ".join(brief["verify"]))
    lines.append(f"Timebox: {brief['timebox']} min — at the limit you hand in a partial report and stop.")
    lines.append(f"Report: {brief['report']}")
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------ standing orders (①)
def standing_path(project: str, st_root=None) -> Path:
    h = hashlib.sha256(os.path.realpath(project).encode()).hexdigest()[:16]
    return root(st_root) / "standing" / f"{h}.md"


def standing(project: str, st_root=None) -> str:
    try:
        return standing_path(project, st_root).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def standing_add(project: str, line: str, st_root=None) -> int:
    line = " ".join(str(line).split())[:300]
    if not line:
        raise OrderError("empty")
    p = standing_path(project, st_root)
    _mk(p.parent)
    cur = [x for x in standing(project, st_root).splitlines() if x.strip()]
    n = len(cur) + 1
    _write(p, "\n".join(cur + [f"{n}. {line}"]) + "\n")
    return n


# ------------------------------------------------------------------------------------------------ the order
class Order:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.id = self.path.name

    @property
    def meta(self) -> dict:
        return json.loads((self.path / "order.json").read_text(encoding="utf-8"))

    def prompt(self) -> str:
        return (self.path / "prompt.md").read_text(encoding="utf-8")

    def event(self, ev: str, **kw) -> None:
        _append(self.path / "events.jsonl", {"ts": round(time.time(), 3), "ev": ev, **kw})

    def events(self) -> list[dict]:
        return _jsonl(self.path / "events.jsonl")

    # inbox ---------------------------------------------------------------
    def send(self, text: str) -> int:
        text = str(text).strip()
        if not text:
            raise OrderError("empty")
        d = _mk(self.path / "inbox")
        n = len(list(d.glob("[0-9][0-9][0-9].md"))) + 1
        _write(d / f"{n:03d}.md", text[:8000])
        return n

    def inbox_all(self) -> list[tuple[int, str]]:
        d = self.path / "inbox"
        return [(int(p.stem), p.read_text(encoding="utf-8")) for p in sorted(d.glob("[0-9][0-9][0-9].md"))]

    def delivered(self) -> set[int]:
        return {e.get("n") for e in self.events() if e.get("ev") == "appended"}

    # control -------------------------------------------------------------
    def control(self) -> str | None:
        try:
            v = json.loads((self.path / "control").read_text(encoding="utf-8"))
            return v.get("action") if isinstance(v, dict) else None
        except (FileNotFoundError, ValueError):
            return None

    def set_control(self, action: str | None) -> None:
        if action is None:
            try:
                (self.path / "control").unlink()
            except FileNotFoundError:
                pass
            return
        _write(self.path / "control", json.dumps({"action": action, "ts": time.time()}))

    # report / result -----------------------------------------------------
    def report(self) -> str | None:
        try:
            return (self.path / "report.md").read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    def result(self) -> dict | None:
        try:
            return json.loads((self.path / "result.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return None

    def write_result(self, r: dict) -> None:
        _write(self.path / "result.json", json.dumps(r, ensure_ascii=False, indent=1))

    # decisions / gates / refusals ---------------------------------------
    def decide(self, phase: str, decision: str, why: str, evidence: str = "", result: str = "") -> None:
        _append(self.path / "decisions.jsonl", decisions.record(phase, decision, why, evidence, result))

    def decisions(self) -> list[dict]:
        return _jsonl(self.path / "decisions.jsonl")

    def gate(self, question: str, options: list[str], default: str, deadline_min: int = 60) -> str:
        if default not in options:
            raise OrderError("default_not_an_option")
        gid = "g" + secrets.token_hex(3)
        _append(self.path / "gates.jsonl", {"id": gid, "question": question[:300], "options": options[:6], "default": default,
                                            "deadline": round(time.time() + 60 * deadline_min), "answer": None})
        return gid

    def denied(self) -> set[str]:
        return {e["d"] for e in _jsonl(self.path / "denied.jsonl") if isinstance(e.get("d"), str)}

    def add_denied(self, d: str) -> None:
        _append(self.path / "denied.jsonl", {"ts": round(time.time(), 3), "d": d})

    # status ----------------------------------------------------------------
    def state(self) -> str:
        r = self.result()
        if r:
            return r.get("state") or "done"
        evs = self.events()
        if any(e.get("ev") == "started" for e in evs):
            return "running"
        if any(e.get("ev") == "queued" for e in evs):
            return "queued"
        return "new"


def make_id() -> str:
    return time.strftime("o%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


def get(order_id: str, st_root=None) -> Order:
    if not ID_RE.match(order_id or ""):
        raise OrderError("bad_id")
    p = root(st_root) / order_id
    if not (p / "order.json").is_file():
        raise OrderError("no_such_order")
    return Order(p)


def all_orders(st_root=None) -> list[Order]:
    r = root(st_root)
    return [Order(p) for p in sorted(r.glob("o*")) if ID_RE.match(p.name) and (p / "order.json").is_file()]


def create(*, project: str, prompt_text: str, brief: dict, title: str, parent: dict | None = None, harness: str = "core",
           model: str | None = None, mode: str = "work", origin: str = "owner_turn", ceo_name: str | None = None,
           budget: dict | None = None, st_root=None, allowed_roots=None, fence: bool = True,
           danger_extra: list | None = None, grants: dict | None = None) -> Order:
    """Validate (12.7.4 project allow-list, the brief) and write a new order directory. Nothing is started here."""
    if grants is not None:
        if not isinstance(grants, dict) or set(grants) - {"hooks"}:
            raise OrderError("bad_grants")
        h = grants.get("hooks")
        if h is not None and (not isinstance(h, dict) or set(h) - {"max", "life", "ttl_max"}
                or type(h.get("max")) is not int or not 1 <= h["max"] <= 10
                or not isinstance(h.get("life"), list) or not h["life"]
                or any(x not in ("once", "ttl") for x in h["life"])
                or type(h.get("ttl_max")) is not int or not 1 <= h["ttl_max"] <= 86400):
            raise OrderError("bad_grants")
    proj = os.path.realpath(os.path.expanduser(project))
    if not os.path.isdir(proj):
        raise OrderError("no_such_project", proj)
    if allowed_roots is not None and not any(proj == os.path.realpath(r) or proj.startswith(os.path.realpath(r) + os.sep)
                                             for r in allowed_roots):
        raise OrderError("project_not_allowed", proj)
    if mode not in MODES or origin not in ORIGINS or harness not in ("core",):
        raise OrderError("bad_field")
    miss = brief_problems(brief, mode)
    if miss:
        raise OrderError("brief_incomplete", ",".join(miss))
    if not str(prompt_text).strip():
        raise OrderError("empty")
    b = dict(budget or {})
    wall = min(WALL_MAX, max(1, int(b.get("wall_min") or brief.get("timebox") or WALL_DEFAULT)))
    oid = make_id()
    d = _mk(root(st_root) / oid)
    meta = {"schema": SCHEMA, "id": oid, "parent": parent or {"kind": origin}, "project": proj,
            "project_name": os.path.basename(proj), "ceo": (ceo_name or os.path.basename(proj) + " CEO")[:40],
            "harness": harness, "model": model, "title": " ".join(str(title).split())[:80] or "order", "mode": mode,
            "budget": {"wall_min": wall, "tokens": int(b["tokens"]) if b.get("tokens") else None,
                       "subagents": min(SUB_MAX, max(1, int(b.get("subagents") or SUB_MAX)))},
            "origin": origin, "host": "local", "brief": brief, "created": round(time.time(), 3),
            "fence": fence is not False, "danger_extra": danger_extra if isinstance(danger_extra, list) else None,
            "grants": json.loads(json.dumps(grants or {})), "seq": time.time_ns()}
    _write(d / "order.json", json.dumps(meta, ensure_ascii=False, indent=1))
    _write(d / "prompt.md", str(prompt_text))
    return Order(d)


# ------------------------------------------------------------------------------------------------ preflight (§14.1)
def preflight(o: Order, st_root=None, live=None) -> tuple[str, list[str]]:
    """('go' | 'queue', blocking order ids): an older live order (running, or queued before this one) on the same project →
    queue behind it (blocked-by), never in parallel. Deterministic; only older orders block, so two never wait on each other.
    live(order) → is its CEO really there (the dispatcher's pid check; a dead 'running' order does not block)."""
    me = o.meta
    blockers = []
    for other in all_orders(st_root):
        if (other.meta.get("seq", 0), other.id) >= (me.get("seq", 0), o.id):
            continue
        s = other.state()
        if s not in ("running", "queued") or (s == "running" and live is not None and not live(other)):
            continue
        if other.meta["project"] == me["project"]:
            blockers.append(other.id)
    return ("queue" if blockers else "go"), blockers


# ------------------------------------------------------------------------------------------------ report head (12.3)
_HEAD = {
    "done": re.compile(r"^\s*(?:完成|Done)\s*[:：]\s*(\d+)\s*/\s*(\d+)", re.I | re.M),
    "ready": re.compile(r"^\s*ready\s*[:：]\s*(是|否|不适用|yes|no|n/?a)", re.I | re.M),
    "needs": re.compile(r"^\s*(?:需要上级|Needs)\s*[:：]\s*(.+)$", re.I | re.M),
    "blocked": re.compile(r"^\s*blocked-by\s*[:：]\s*(.+)$", re.I | re.M),
    "verdict": re.compile(r"^\s*VERDICT\s*[:：]\s*(ok|attention|fail)\b\s*[—\-–:：]?\s*(.*)$", re.I | re.M),
}
_OPEN = re.compile(r"^\s*-\s*\[\s\]\s*(O\d+)\b\s*(.*)$", re.M)


def parse_head(text: str | None) -> dict:
    """The machine-readable head; `complete` = every field present (完成 x/y, ready, 需要上级, VERDICT)."""
    t = text or ""
    h: dict = {"complete": False}
    m = _HEAD["done"].search(t)
    if m:
        h["done"], h["total"] = int(m.group(1)), int(m.group(2))
    m = _HEAD["ready"].search(t)
    if m:
        v = m.group(1).lower()
        h["ready"] = "yes" if v in ("是", "yes") else "no" if v in ("否", "no") else "n/a"
    m = _HEAD["needs"].search(t)
    if m:
        h["needs"] = m.group(1).strip()[:200]
    m = _HEAD["blocked"].search(t)
    if m:
        ids = [x for x in re.split(r"[,\s]+", m.group(1).strip()) if ID_RE.match(x)]
        h["blocked_by"] = ids
    ms = list(_HEAD["verdict"].finditer(t))
    if ms:
        h["verdict"], h["verdict_text"] = ms[-1].group(1).lower(), ms[-1].group(2).strip()[:200]
    h["open"] = [m.group(1) for m in _OPEN.finditer(t)][:20]
    h["complete"] = all(k in h for k in ("done", "ready", "needs", "verdict"))
    return h


def judge(exit_code, head: dict, aborted: str | None = None) -> str:
    """12.5 — the report decides, the exit code is only a witness:
    done · incomplete · no_report · done_unverified · failed · aborted."""
    if aborted in ("stopped", "budget"):
        return "aborted"
    ok_exit = exit_code == 0
    if head.get("complete"):
        finished = head["verdict"] == "ok" and head["done"] >= head["total"]
        if ok_exit:
            return "done" if finished else "incomplete"
        return "done_unverified" if finished else "failed"
    return "no_report" if ok_exit else "failed"


NEXT = {"done": "report the result to the owner", "incomplete": "act on 需要上级; do not report success",
        "no_report": "resume once asking only for the report", "done_unverified": "check the evidence in the report first",
        "failed": "look at the last event, resume; two failures in a row → tell the owner",
        "aborted": "read where it stopped; resume with more budget or re-scope"}


# ------------------------------------------------------------------------------------------------ retries (§14.3, pstack ④)
# P571: the table and the hash live in agentj/decisions.py, shared with the default path; same names here.
RETRY, failure_kind, root_cause_hash = decisions.RETRY, decisions.failure_kind, decisions.root_cause_hash
places_of, retry_advice = decisions.places_of, decisions.retry_advice


# ------------------------------------------------------------------------------------------------ tools (12.5, ≤ 500 tokens)
DISPATCH_SPEC = {"type": "function", "function": {
    "name": "dispatch",
    "description": "Orders to project CEOs. new: needs project, title, prompt and brief {goal, scope:{write:[globs]}, "
                   "verify:[commands], timebox:minutes, report}; returns an id at once. status · send (append text) · stop · "
                   "resume (optional text) · report (head; section=N reads part N).",
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["new", "status", "send", "stop", "resume", "report"]},
        "id": {"type": "string"}, "project": {"type": "string"}, "title": {"type": "string"},
        "prompt": {"type": "string"}, "text": {"type": "string"}, "brief": {"type": "object"},
        "grants": {"type": "object"}, "mode": {"type": "string", "enum": ["work", "research"]}, "section": {"type": "integer"}},
        "required": ["action"]}}}

DECIDE_SPEC = {"type": "function", "function": {
    "name": "decide",
    "description": "Log one decision: what you decided, why, and a pointer to evidence (file:line, commit, log).",
    "parameters": {"type": "object", "properties": {"decision": {"type": "string"}, "why": {"type": "string"},
                                                    "evidence": {"type": "string"}}, "required": ["decision", "why"]}}}


def head_card(o: Order) -> str:
    """What the main Agent gets back (12.8: ≤ HEAD_MAX tokens): state, verdict, the head lines, the next step."""
    from .prompt import tokens
    r = o.result() or {}
    h = r.get("head") or parse_head(o.report())
    st = o.state()
    lines = [f"order {o.id} · {o.meta['title']} · {st}" + (f" · verdict {r['verdict']}" if r.get("verdict") else "")]
    if h.get("complete"):
        lines.append(f"完成：{h['done']}/{h['total']} · ready：{h['ready']} · 需要上级：{h.get('needs', '')}")
        lines.append(f"VERDICT: {h['verdict']} — {h.get('verdict_text', '')}")
    elif st not in ("running", "queued", "new"):
        lines.append("(no complete report head)")
    if r.get("verdict"):
        lines.append("next: " + NEXT.get(r["verdict"], ""))
    text = "\n".join(lines)
    while tokens(text) > HEAD_MAX and len(text) > 80:
        text = text[: int(len(text) * 0.9)]
    return text


def section(o: Order, n: int) -> str:
    """Part n (1-based) of the report body, ≤ SECTION_MAX tokens each."""
    from .prompt import tokens
    text = o.report() or ""
    parts, cur = [], ""
    for para in text.split("\n\n"):
        if cur and tokens(cur + "\n\n" + para) > SECTION_MAX:
            parts.append(cur)
            cur = para
        else:
            cur = (cur + "\n\n" + para) if cur else para
    if cur:
        parts.append(cur)
    if not parts:
        return "(no report yet)"
    if not 1 <= n <= len(parts):
        return f"(sections 1–{len(parts)})"
    p = parts[n - 1]
    while tokens(p) > SECTION_MAX:
        p = p[: int(len(p) * 0.9)]
    return f"[section {n}/{len(parts)}]\n{p}"
