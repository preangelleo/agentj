"""P426 #181-5: node checkpoints for multi-step automations, and continuing a failed run from its failed node.

Leo (10-20): 「工作流失败局部修复：自动化中途失败从失败节点续跑，不整条重来。」

A *node* is one entry of `steps` in task.json (taskspec.steps_problem): its own instructions file, run as its own headless
harness run (tasks.Scheduler._exec, same fence / approvals / timeout as a whole task), ending with its own VERDICT line.
`fail` (or no VERDICT, a timeout, a stop, an error) = the node failed and the run stops there; later nodes never start.

Checkpoint: <state>/wfruns/<run_id>.json (0600, atomic replace via State.write_private, in the hidden state dir — the Agent
cannot forge "done"). Rewritten whenever a node starts, finishes or fails: per node state (pending · running · done · failed ·
reused), attempts, VERDICT sentence, its report file and a fingerprint (sha256 + size) of that report and of the node's
literal `outputs`; on failure the failed node and the reason in plain words.

Resume (`agentj automation resume <run id | task id>`, control.sock from the owner's terminal, capability.sock from an Agent):
- a new run reuses every node before the restart point (their sentences and report paths are handed to later nodes) and runs
  the rest. The restart point is the failed node, or an earlier node whose instructions changed, whose report / outputs were
  deleted or changed, or when the node list itself changed — said in plain words.
- S5 (security review): what decides "needs the owner" can not be rewritten by the Agent after a failure. Every run snapshots
  its contract into the checkpoint (`contract`: the task's mode, each node's idempotent / retry; hidden state) and its `tsha`;
  a resume takes the STRICTER of that snapshot and the current task.json (non-idempotent in either → still non-idempotent;
  research only when both say research), and a contract hash that differs from the failed run's counts as needing the owner
  too, said in plain words (「自动化内容在失败后被改过」). A checkpoint without a snapshot counts as all non-idempotent.
- a node that will run AGAIN and is not `idempotent: true` (mode research counts as idempotent) needs the owner: at the terminal
  `--yes`; from an Agent (or a terminal that is not provably the owner's) one approval card on the phone (host.ask, a host card:
  never batch / goal / full-level automatic). Nodes after the failure never ran, so they need nothing.
- one resume per run: a claim written into the checkpoint under the `wfruns` flock; a second resume of the same run is refused
  while the first waits for its card or after it queued. A claim from a previous serve instance that never queued is stale.
- a missing or damaged checkpoint never guesses: resume is refused with the full-run command instead.
- `retry` (0–3): an idempotent node that failed is tried again by itself up to that many times within the same run.
No owner lock is created for an Agent; no step is ever started outside the Scheduler (one run at a time, chat lock).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import stat
import time

from . import taskspec

DIR = "wfruns"
KEEP = 200
AGE = 30 * 86400
STATES = ("pending", "running", "done", "failed", "reused")
RESUMABLE = ("failed", "interrupted")
HASH_MAX = 256 * 1024 * 1024
TOOL = "agentj automation resume"          # the phone card's tool name (approvals.log; goal.SOFT_EXEMPT)
_RID = re.compile(r"[0-9a-f]{6,32}")


class RunError(Exception):
    """why: unknown | corrupt | not_failed | claimed | not_steps | changed."""

    def __init__(self, why: str, detail: str = ""):
        super().__init__(why)
        self.why, self.detail = why, detail


def _en(lang) -> bool:
    return str(lang or "zh").startswith("en")


def _t(lang, zh: str, en: str) -> str:
    return en if _en(lang) else zh


def steps_of(task) -> list:
    return list((task or {}).get("steps") or []) if isinstance(task, dict) else []


def _title(step: dict, lang) -> str:
    t = step.get("title")
    return (t.get("en" if _en(lang) else "zh") if isinstance(t, dict) else None) or step["id"]


# ------------------------------------------------------------------ the checkpoint file
def folder(st):
    return st.root / DIR


def path(st, rid: str):
    if not (isinstance(rid, str) and re.fullmatch(r"[0-9a-f]{32}", rid)):
        raise RunError("unknown")
    return folder(st) / f"{rid}.json"


def save(st, rec: dict) -> None:
    d = folder(st)
    d.mkdir(mode=0o700, exist_ok=True)
    st.write_private(path(st, rec["run_id"]), json.dumps(rec, ensure_ascii=False).encode())


def _valid(rec, rid: str) -> bool:
    if not (isinstance(rec, dict) and rec.get("v") == 1 and rec.get("run_id") == rid and isinstance(rec.get("id"), str)
            and taskspec.ID_RE.fullmatch(rec["id"]) and isinstance(rec.get("steps"), list) and rec["steps"]):
        return False
    for n in rec["steps"]:
        if not (isinstance(n, dict) and isinstance(n.get("id"), str) and n.get("state") in STATES
                and isinstance(n.get("artifacts", []), list)
                and all(isinstance(a, dict) and isinstance(a.get("path"), str) and isinstance(a.get("sha256"), str)
                        for a in n.get("artifacts", []))):
            return False
    return True


def load(st, rid: str) -> dict:
    p = path(st, rid)
    try:
        fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        raise RunError("unknown") from None
    except OSError:
        raise RunError("corrupt") from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise RunError("corrupt")
        raw = os.read(fd, 1024 * 1024 + 1)
    finally:
        os.close(fd)
    try:
        rec = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise RunError("corrupt") from None
    if not _valid(rec, rid):
        raise RunError("corrupt")
    return rec


def _prune(st) -> None:
    try:
        files = sorted(folder(st).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return
    now = time.time()
    for i, p in enumerate(files):
        try:
            if i >= KEEP or p.stat().st_mtime < now - AGE:
                p.unlink()
        except OSError:
            pass


def find_run(st, ref) -> str:
    """A full run id, a unique prefix (≥ 6 hex) or a task id (→ its newest run that can be continued)."""
    ref = str(ref or "").strip().lower()
    try:
        names = [p.stem for p in folder(st).glob("*.json")]
    except OSError:
        names = []
    if _RID.fullmatch(ref):
        hits = [n for n in names if n.startswith(ref)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise RunError("unknown", "ambiguous")
    if taskspec.ID_RE.fullmatch(ref):
        best = None
        for n in names:
            try:
                rec = load(st, n)
            except RunError:
                continue
            if rec["id"] == ref and (best is None or rec.get("at", 0) > best.get("at", 0)):
                best = rec
        if best is not None:
            return best["run_id"]
    raise RunError("unknown")


# ------------------------------------------------------------------ fingerprints (what a later node may depend on)
def _inside(wf_dir: str, rel: str) -> str | None:
    if any(c in rel for c in "<>{}*?") or ".." in rel.split("/"):
        return None                       # a pattern (reports/<date>.md) names no single file to compare
    root = os.path.realpath(wf_dir)
    p = os.path.realpath(os.path.join(root, rel))
    return p if p.startswith(root + os.sep) else None


def _digest(p: str) -> tuple[str, int] | None:
    try:
        fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode):
            return None
        if s.st_size > HASH_MAX:
            return f"size:{s.st_size}:{s.st_mtime_ns}", s.st_size
        h = hashlib.sha256()
        while chunk := os.read(fd, 1 << 20):
            h.update(chunk)
        return h.hexdigest(), s.st_size
    finally:
        os.close(fd)


def fingerprint(wf_dir: str, rels) -> list[dict]:
    out = []
    for rel in dict.fromkeys(r for r in rels if isinstance(r, str)):
        p = _inside(wf_dir, rel)
        d = _digest(p) if p else None
        if d:
            out.append({"path": rel, "sha256": d[0], "size": d[1]})
    return out


def changed(wf_dir: str, arts: list) -> tuple[str, str] | None:
    """(path, "missing" | "changed") for the first artifact that is no longer what the node left; None = all intact."""
    for a in arts:
        p = _inside(wf_dir, a["path"])
        d = _digest(p) if p else None
        if d is None:
            return a["path"], "missing"
        if d[0] != a["sha256"]:
            return a["path"], "changed"
    return None


def prompt_sha(e: dict, step: dict) -> str:
    from .tasks import _regular
    return hashlib.sha256(_regular(os.path.join(e["dir"], step["prompt_file"])) or b"").hexdigest()


# ------------------------------------------------------------------ the contract snapshot (S5)
def contract_of(task) -> dict:
    """What a resume's "needs the owner" depends on, as task.json says it now."""
    task = task if isinstance(task, dict) else {}
    return {"mode": task.get("mode"),
            "steps": {s["id"]: {"idempotent": s.get("idempotent") is True, "retry": int(s.get("retry") or 0)}
                      for s in steps_of(task) if isinstance(s, dict) and isinstance(s.get("id"), str)}}


def stricter(snap, cur: dict) -> dict:
    """The stricter of a run's snapshot and the current contract. No (or a damaged) snapshot = nothing may repeat by itself."""
    snap = snap if isinstance(snap, dict) and isinstance(snap.get("steps"), dict) else {"mode": None, "steps": {}}
    modes = (snap.get("mode"), cur.get("mode"))
    out = {"mode": "research" if modes == ("research", "research") else next(m for m in modes if m != "research"), "steps": {}}
    for sid, c in cur["steps"].items():
        o = snap["steps"].get(sid)
        o = o if isinstance(o, dict) else {}
        r = o.get("retry")
        out["steps"][sid] = {"idempotent": o.get("idempotent") is True and c["idempotent"],
                             "retry": min(c["retry"], r if isinstance(r, int) and not isinstance(r, bool) else 0)}
    return out


def _safe(contract: dict, sid: str) -> bool:
    return contract.get("mode") == "research" or (contract["steps"].get(sid) or {}).get("idempotent") is True


# ------------------------------------------------------------------ the plan of a resume
def plan(st, rid: str, e: dict, lang="zh", instance: str | None = None) -> dict:
    """Where a resume of run `rid` starts, what it reuses and which re-run nodes need the owner. Read-only."""
    rec = load(st, rid)
    steps = steps_of(e.get("task"))
    if rec["id"] != e["id"]:
        raise RunError("unknown")
    if not steps:
        raise RunError("not_steps")
    if rec.get("status") not in RESUMABLE:
        raise RunError("not_failed", rec.get("status") or "")
    claim = rec.get("resumed_by")
    if isinstance(claim, dict) and (claim.get("run") or claim.get("instance") == instance):
        raise RunError("claimed", str(claim.get("run") or ""))
    old = rec["steps"]
    fail_i = next((i for i, n in enumerate(old) if n["state"] in ("failed", "running")),
                  next((i for i, n in enumerate(old) if n["state"] == "pending"), len(old) - 1))
    fail_i = min(fail_i, len(steps) - 1)
    start, why = fail_i, []
    for i in range(fail_i):
        n, s = old[i], steps[i]
        if s["id"] != n["id"]:
            start = i
            why.append(_t(lang, f"节点列表改过了，从第 {i + 1} 步重新开始", f"the list of steps changed; starting again at step {i + 1}"))
            break
        if n["state"] not in ("done", "reused"):
            start = i
            break
        if n.get("prompt_sha") != prompt_sha(e, s):
            start = i
            why.append(_t(lang, f"第 {i + 1} 步「{_title(s, lang)}」的说明改过了，从这一步重跑",
                          f"the instructions of step {i + 1} \"{_title(s, lang)}\" changed; running again from it"))
            break
        c = changed(e["dir"], n.get("artifacts") or [])
        if c:
            start = i
            what = _t(lang, "被删了" if c[1] == "missing" else "变了", "was deleted" if c[1] == "missing" else "changed")
            why.append(_t(lang, f"第 {i + 1} 步「{_title(s, lang)}」的产物 {c[0]} {what}，从这一步重跑",
                          f"the output {c[0]} of step {i + 1} \"{_title(s, lang)}\" {what}; running again from it"))
            break
    eff = stricter(rec.get("contract"), contract_of(e.get("task")))     # S5: the Agent can not relax it after the failure
    confirm = [steps[i]["id"] for i in range(start, fail_i + 1)
               if not _safe(eff, steps[i]["id"])
               and i < len(old) and old[i]["id"] == steps[i]["id"] and old[i]["state"] != "pending"]
    edited = rec.get("tsha") != e.get("tsha")
    if edited:
        why.append(_t(lang, "自动化内容（task.json 或步骤说明）在失败后被改过，所以要你确认才继续",
                         "the automation (task.json or a step's instructions) was changed after the failure, so it needs your OK"))
    return {"run_id": rid, "id": e["id"], "start": start, "failed_at": fail_i,
            "failed": (rec.get("failed") or {}).get("why") or "",
            "reuse": [dict(n) for n in old[:start]], "rerun": [s["id"] for s in steps[start:]], "why": why,
            "confirm": confirm, "edited": edited, "owner": bool(confirm) or edited, "contract": eff,
            "titles": {s["id"]: _title(s, lang) for s in steps}}


def _claim(st, rid: str, token: str, instance: str) -> None:
    with st.file_lock(DIR):
        rec = load(st, rid)
        c = rec.get("resumed_by")
        if isinstance(c, dict) and (c.get("run") or c.get("instance") == instance):
            raise RunError("claimed", str(c.get("run") or ""))
        rec["resumed_by"] = {"token": token, "instance": instance, "at": int(time.time())}
        save(st, rec)


def _set_claim(st, rid: str, token: str, new_rid: str | None) -> None:
    """new_rid: bind the claim to the run it queued; None: give the claim back (declined / failed to queue)."""
    with st.file_lock(DIR):
        try:
            rec = load(st, rid)
        except RunError:
            return
        c = rec.get("resumed_by")
        if not (isinstance(c, dict) and c.get("token") == token):
            return
        if new_rid:
            c["run"] = new_rid
        else:
            rec.pop("resumed_by", None)
        save(st, rec)


def card_text(p: dict, title: str, lang="zh") -> str:
    names = "、".join(f"「{p['titles'].get(i, i)}」" for i in p["confirm"]) if not _en(lang) else \
        ", ".join(f"\"{p['titles'].get(i, i)}\"" for i in p["confirm"])
    reuse = len(p["reuse"])
    if _en(lang):
        lines = [f"Continue the automation \"{title}\" from step {p['start'] + 1}?"]
        if names:
            lines.append(f"Steps that will run again and are not marked safe to repeat: {names}. They may have already done "
                         "part of their work (sent, posted, paid…) last time; running them again can do it twice.")
        lines.append(f"{reuse} finished step(s) are reused, not run again." if reuse else "No finished step can be reused.")
    else:
        lines = [f"从第 {p['start'] + 1} 步继续自动化「{title}」？"]
        if names:
            lines.append(f"会再跑一遍、但没标成「可重复」的步骤：{names}。上次它们可能已经做了一部分（发出、发布、付款……），再跑可能做两次。")
        lines.append(f"已完成的 {reuse} 步直接复用，不再执行。" if reuse else "没有可以复用的已完成步骤。")
    lines += p["why"][:2]
    return "\n".join(lines)[:600]


def refusal(err: RunError, lang="zh", tid: str | None = None) -> dict:
    run = f"`agentj tasks run {tid}`" if tid else "`agentj tasks run <id>`"
    words = {
        "unknown": _t(lang, "找不到这次运行（只有分步自动化的运行能续跑）。", "No such run (only runs of a multi-step automation can be continued)."),
        "corrupt": _t(lang, f"这次运行的进度记录坏了，判断不了哪些步骤已经完成；为安全起见不续跑。要重来就整条重新运行：{run}。",
                      f"This run's progress record is damaged, so it is unknown which steps finished; to be safe it is not "
                      f"continued. To start over, run the whole automation: {run}."),
        "not_failed": _t(lang, "这次运行没有失败（已完成或还在跑），没有要续跑的。", "This run did not fail (it finished or is still running); nothing to continue."),
        "claimed": _t(lang, "这次运行已经在续跑了（或正在等你确认）；要再续，就续最新的那一次。",
                      "This run is already being continued (or waits for your confirmation); continue the newest run instead."),
        "not_steps": _t(lang, f"这个自动化现在不是分步的，没法从中间继续；整条重新运行：{run}。",
                        f"This automation has no steps any more, so it cannot continue from the middle; run it whole: {run}."),
        "changed": _t(lang, "等确认期间自动化或它的进度变了，没有续跑；请重新发起。", "The automation or its progress changed while waiting; nothing was continued, ask again."),
    }
    return {"result": "refused", "why": err.why, "detail": words.get(err.why, err.detail or err.why)}


# ------------------------------------------------------------------ serve: `agentj automation resume|status`
async def resume(host, ref, yes: bool = False, trusted: bool = False) -> dict:
    """trusted = the owner's terminal (no Agent ancestry, a terminal on stdin): `--yes` replaces the card. Otherwise a node that
    runs again and is not idempotent needs the owner's allow on the phone."""
    from . import tasks
    lang = getattr(host, "lang", "zh")
    if host.stopped():
        return {"result": "refused", "why": "stopped", "detail": _t(lang, "已全部停下：先恢复再续跑。", "Stop everything is on: resume Agent J first.")}
    if not host.agent_cfg:
        return {"result": "refused", "why": "no_agent", "detail": _t(lang, "还没接 Agent。", "No Agent is set up.")}
    st, sch = host.st, host.scheduler
    wd = host.agent_cfg.get("dir")
    tid = rid = None
    token = secrets.token_hex(8)
    claimed = False
    try:
        rid = find_run(st, ref)
        rec = load(st, rid)
        tid = rec["id"]
        try:
            e = tasks.find(wd, tid)
        except tasks.TaskError:
            raise RunError("unknown") from None
        if e["problems"]:
            return {"result": "refused", "why": "invalid", "detail": "task.json: " + e["problems"][0]}
        if sch.current_id == tid or any(t == tid for t, _ in sch.waiting):
            return {"result": "refused", "why": "busy", "detail": _t(lang, "这个自动化正在跑或在排队，等它结束再续。",
                                                                      "This automation is running or queued; continue after it ends.")}
        if tasks.uncertain_run(st, tid):
            return {"result": "refused", "why": "uncertain", "detail": _t(
                lang, "主机重启时有一次运行没确认结束：先核对产物和外部操作，再由主人重新启用这个自动化。",
                "A run was cut by a restart and its end is unconfirmed: check its effects, then have the owner enable the automation again.")}
        p = plan(st, rid, e, lang, sch.instance_id)
        _claim(st, rid, token, sch.instance_id)
        claimed = True
        title = (e["task"].get("title") or {}).get("en" if _en(lang) else "zh") or tid
        if p["owner"] and not (trusted and yes):
            if trusted:
                _set_claim(st, rid, token, None)
                claimed = False
                return {"result": "needs_yes", "run_id": rid, "confirm": p["confirm"], "edited": p["edited"], "start": p["start"] + 1,
                        "detail": card_text(p, title, lang) + "\n" + _t(lang, "确认就加 --yes 再运行一次。", "To confirm, run it again with --yes.")}
            ans = await host.ask(TOOL, {"summary": card_text(p, title, lang)}, batch=False)
            if ans.get("behavior") != "allow":
                return {"result": "declined", "run_id": rid, "detail": _t(lang, "主人没有同意，没有续跑。", "The owner did not allow it; nothing was continued.")}
            e = tasks.find(wd, tid)            # the card took time: the contract and the record must be what was shown
            p2 = plan(st, rid, e, lang, None) if not e["problems"] else None
            if p2 is None or (p2["start"], p2["confirm"], p2["rerun"], p2["edited"], p2["contract"]) != \
                    (p["start"], p["confirm"], p["rerun"], p["edited"], p["contract"]):
                raise RunError("changed")
        sch.plans[tid] = p
        try:
            new = sch.request(tid, "resume")
        except tasks.TaskError as err:
            sch.plans.pop(tid, None)
            return {"result": "refused", "why": err.reason, "detail": err.detail or err.reason}
        _set_claim(st, rid, token, new)
        claimed = False
        from . import diskq
        await diskq.aflush()
        st.log("task_resume", id=tid, count=len(p["reuse"]))
        return {"result": "queued", "run_id": new, "from": rid, "id": tid, "start": p["start"] + 1,
                "reuse": [n["id"] for n in p["reuse"]], "rerun": p["rerun"], "why": p["why"],
                "detail": _t(lang, f"已排队：从第 {p['start'] + 1} 步继续，复用已完成的 {len(p['reuse'])} 步。",
                             f"Queued: continuing from step {p['start'] + 1}, reusing {len(p['reuse'])} finished step(s).")}
    except RunError as err:
        if tid is None and err.why == "corrupt":   # the run journal still knows which automation it was
            tid = (tasks.load(st)["runs"].get(rid) or {}).get("id")
        return refusal(err, lang, tid)
    finally:
        if claimed:
            _set_claim(st, rid, token, None)


def status(st, ref, lang="zh") -> dict:
    try:
        rid = find_run(st, ref)
        rec = load(st, rid)
    except RunError as err:
        return refusal(err, lang)
    return {"result": "status", "run_id": rid, "id": rec["id"], "status": rec.get("status"), "from": rec.get("from"),
            "failed": rec.get("failed"), "resumed_by": (rec.get("resumed_by") or {}).get("run"),
            "steps": [{k: n.get(k) for k in ("id", "state", "attempts", "verdict", "line", "report")} for n in rec["steps"]]}


# ------------------------------------------------------------------ the runner (inside tasks.Scheduler.run)
def new_record(rid: str, e: dict, p: dict | None) -> dict:
    reuse = (p or {}).get("reuse", [])
    nodes = []
    for i, s in enumerate(steps_of(e["task"])):
        if i < len(reuse) and reuse[i]["id"] == s["id"]:
            nodes.append(dict(reuse[i], state="reused", reused_from=(p or {}).get("run_id")))
        else:
            nodes.append({"id": s["id"], "state": "pending", "attempts": 0, "verdict": None, "line": "", "report": None,
                          "artifacts": []})
    cur = contract_of(e["task"])            # S5: a continuation keeps the stricter of the run it continues and today's file
    contract = stricter(p["contract"], cur) if p and isinstance(p.get("contract"), dict) else cur
    return {"v": 1, "run_id": rid, "id": e["id"], "tsha": e.get("tsha"), "contract": contract, "from": (p or {}).get("run_id"),
            "at": int(time.time()), "status": "running", "failed": None, "steps": nodes}


def step_text(e: dict, i: int, rec: dict, lang="zh") -> str:
    steps = steps_of(e["task"])
    s = steps[i]
    from .tasks import _regular
    body = (_regular(os.path.join(e["dir"], s["prompt_file"])) or b"").decode("utf-8", "replace")
    rel = e.get("relative_dir", "workflows/" + e["id"])
    head = (f"\n\n---\n这是分步自动化的第 {i + 1}/{len(steps)} 步「{_title(s, 'zh')}」（{s['prompt_file']}）：只做这一步，别的步骤由 Agent J "
            f"按顺序另行运行。/ This is step {i + 1} of {len(steps)} \"{_title(s, 'en')}\" ({s['prompt_file']}) of a multi-step "
            "automation: do only this step; Agent J runs the others in order.\n")
    done = [f"- {k + 1}. {n['id']}: {n.get('line') or ''} ({rel}/{n['report']})" for k, n in enumerate(rec["steps"][:i])
            if n["state"] in ("done", "reused") and n.get("report")]
    if done:
        head += "前面步骤的结果（报告可读）/ Earlier steps (their reports are readable):\n" + "\n".join(done) + "\n"
    return head + "\n" + body.rstrip()


def _save_step(sch, e: dict, out: str, research: bool, rid: str, sid: str, attempt: int) -> str | None:
    rel = sch._save(e, out, research, f"{rid}-{sid}-{attempt}")
    return "reports/" + os.path.basename(rel) if rel else None


def _why(lang, kind: str, detail: str = "") -> str:
    return {"timeout": _t(lang, "超过时间没做完，已中断", "did not finish in time and was stopped"),
            "stopped": _t(lang, "被急停中断", "stopped by Stop"),
            "limit": _t(lang, "用量额度用完了", "the usage limit ran out"),
            "error": _t(lang, "运行出错中断", "interrupted by an error"),
            "noverdict": _t(lang, "回答里没有 VERDICT 行", "its answer had no VERDICT line"),
            "noanswer": _t(lang, "没有回答", "no answer")}.get(kind, detail)


async def run_steps(sch, kind, e: dict, research: bool, rid: str, trigger: str) -> tuple[str, int | str]:
    """Every node in order, each checkpointed. Returns (summary text ending in the run's VERDICT line, 0)."""
    from . import tasks
    st, lang = sch.st, getattr(sch.host, "lang", "zh")
    p = sch.plans.pop(e["id"], None) if trigger == "resume" else None
    if trigger == "quota_resume":          # P314 rerun after a usage limit: continue, but never re-run a side effect unasked
        p = _auto_plan(sch, e, lang, rid)
    steps = steps_of(e["task"])
    rec = new_record(rid, e, p)
    sch.step_state = rec
    save(st, rec)
    _prune(st)
    for i, s in enumerate(steps):
        n = rec["steps"][i]
        if n["state"] == "reused":
            continue
        while True:
            n.update(state="running", attempts=n.get("attempts", 0) + 1, started_at=int(time.time()),
                     prompt_sha=prompt_sha(e, s))
            save(st, rec)
            why, out, code = None, "", 0
            sch.limit = None
            try:
                out, code = await asyncio.wait_for(
                    sch._exec(kind, e, research, tasks.prompt_for(e, research, step_text(e, i, rec, lang))), tasks.RUN_TIMEOUT)
            except asyncio.TimeoutError:
                why = _why(lang, "timeout")
            except asyncio.CancelledError:
                _fail(st, rec, i, s, _why(lang, "stopped"), lang, "interrupted")
                raise
            except tasks.TaskError as err:
                why = err.detail or err.reason
            except Exception:  # noqa: BLE001 — the node failed; the record says so, the run ends here
                why = _why(lang, "error")
            finally:
                await sch._kill()
            v = tasks.parse_verdict(out)
            if why is None and v and v[0] != "fail":
                rep = _save_step(sch, e, out, research, rid, s["id"], n["attempts"])
                n.update(state="done", verdict=v[0], line=v[1][:300], report=rep, ended_at=int(time.time()),
                         artifacts=fingerprint(e["dir"], ([rep] if rep else []) + list(s.get("outputs") or [])))
                save(st, rec)
                break
            if out:
                n["report"] = _save_step(sch, e, out, research, rid, s["id"], n["attempts"])
            if why is None:
                why = (v[1][:200] if v else _why(lang, "limit") if sch.limit else _why(lang, "noverdict" if out.strip() else "noanswer"))
            safe = research or _safe(rec["contract"], s["id"])          # S5: the run's snapshot, not a file edited meanwhile
            if safe and n["attempts"] <= min(s.get("retry", 0), (rec["contract"]["steps"].get(s["id"]) or {}).get("retry", 0)) \
                    and not sch.limit:
                st.log("task_step_retry", id=e["id"], count=n["attempts"])
                continue
            _fail(st, rec, i, s, why, lang, "failed")
            return _summary(e, rec, lang, limit=bool(sch.limit)), 0
    rec["status"] = "done"
    save(st, rec)
    return _summary(e, rec, lang), 0


def _fail(st, rec: dict, i: int, s: dict, why: str, lang, status: str) -> None:
    from . import privacy
    why = privacy.owner_text(str(why))[:200]
    rec["steps"][i].update(state="failed", why=why, ended_at=int(time.time()))
    rec["failed"] = {"step": s["id"], "index": i, "title": _title(s, lang), "why": why}
    rec["status"] = status
    save(st, rec)


def _auto_plan(sch, e: dict, lang, new_rid: str) -> dict | None:
    """The newest failed run of this task, when continuing it re-runs nothing that is not idempotent; else the run refuses
    (the failure notice already offered 「从失败处继续」) — nothing with side effects is repeated without the owner."""
    from . import tasks
    try:
        rid = find_run(sch.st, e["id"])
        p = plan(sch.st, rid, e, lang, sch.instance_id)
    except RunError:
        return None
    if p["owner"]:
        sch.step_state = load(sch.st, rid)
        raise tasks.TaskError("confirm", _t(lang, "额度恢复了，但要重跑的步骤不能自动重复，等你确认后再继续",
                                            "the usage limit reset, but the step to run again is not safe to repeat by itself; "
                                            "waiting for your confirmation"))
    token = secrets.token_hex(8)
    try:                                   # the same one-resume-per-run claim as `agentj automation resume`
        _claim(sch.st, rid, token, sch.instance_id)
    except RunError:
        raise tasks.TaskError("busy", _t(lang, "这次失败的运行已经在续跑了", "that failed run is already being continued")) from None
    _set_claim(sch.st, rid, token, new_rid)
    return p


def _summary(e: dict, rec: dict, lang, limit: bool = False) -> str:
    steps = steps_of(e["task"])
    lines = [f"# {_t(lang, '分步运行', 'Step run')} {rec['run_id'][:8]}" +
             (f" ({_t(lang, '续跑自', 'continues')} {rec['from'][:8]})" if rec.get("from") else "")]
    worst = "ok"
    for i, (s, n) in enumerate(zip(steps, rec["steps"])):
        tag = {"done": n.get("verdict") or "ok", "reused": _t(lang, "复用", "reused"), "failed": "fail",
               "pending": _t(lang, "没有运行", "not run"), "running": "?"}[n["state"]]
        if n["state"] in ("done", "reused") and n.get("verdict") == "attention":
            worst = "attention"
        lines.append(f"{i + 1}. {_title(s, lang)} — {tag} — {n.get('why') or n.get('line') or ''}".rstrip(" —"))
    f = rec.get("failed")
    if f:
        if limit:
            return "\n".join(lines) + "\n"     # no VERDICT: the P314 usage-limit rerun picks this run up (tasks._limit_hit)
        lines.append(f"VERDICT: fail — {fail_line(f, lang)}")
    else:
        lines.append(f"VERDICT: {worst} — " + _t(lang, f"{len(steps)} 步全部完成", f"all {len(steps)} steps finished"))
    return "\n".join(lines) + "\n"


def fail_line(f: dict, lang) -> str:
    return _t(lang, f"第 {f['index'] + 1} 步「{f['title']}」没做成：{f['why']}", f"step {f['index'] + 1} \"{f['title']}\" failed: {f['why']}")


def res_fields(sch, tid: str, res: dict) -> None:
    """After a multi-step run: the failure in plain words and the 「从失败处继续」 entry on the result (notice / readback)."""
    rec, sch.step_state = sch.step_state, None
    if not rec:
        return
    if rec.get("status") == "running":     # cut outside a node (an error between nodes): the running node is the failure
        rec["status"] = "interrupted"
        save(sch.st, rec)
    if rec.get("status") == "done":
        return
    lang = getattr(sch.host, "lang", "zh")
    f = rec.get("failed")
    if f and not res.get("stopped") and rec["run_id"] == res.get("run_id"):   # not the quota refusal: its own line stays
        res["line"] = fail_line(f, lang)[:300]
    res["resume"] = {"run_id": rec["run_id"], "id": tid, "step": (f or {}).get("step"), "title": (f or {}).get("title")}


def mark_cut(st, rid: str, tid: str, lang="zh") -> dict | None:
    """serve restarted with this run queued / running (tasks.Scheduler.recover): its checkpoint says where it stopped. The
    node that was running counts as failed (it may have done part of its work: a non-idempotent one needs the owner)."""
    try:
        rec = load(st, rid)
    except RunError:
        return None
    if rec.get("status") not in ("running",):
        return None
    i = next((k for k, n in enumerate(rec["steps"]) if n["state"] == "running"), None)
    why = _t(lang, "主机重启时这一步还没做完", "Agent J restarted before this step finished")
    if i is not None:
        rec["steps"][i].update(state="failed", why=why)
        rec["failed"] = {"step": rec["steps"][i]["id"], "index": i, "title": rec["steps"][i]["id"], "why": why}
    rec["status"] = "interrupted"
    save(st, rec)
    return {"run_id": rid, "id": tid, "step": (rec.get("failed") or {}).get("step"), "title": (rec.get("failed") or {}).get("title")}


def hint(res: dict, lang="zh") -> str:
    """The failure notice's continue entry: one sentence to say to the Agent, or the command at the computer."""
    r = res.get("resume")
    if not isinstance(r, dict) or not r.get("run_id"):
        return ""
    return _t(lang, f"\n从失败处继续：跟 Agent 说「从失败处继续 {r['id']}」，或在电脑上运行 `agentj automation resume {r['run_id'][:12]}`。",
              f"\nContinue from where it failed: tell your Agent \"continue {r['id']} from where it failed\", or run "
              f"`agentj automation resume {r['run_id'][:12]}` on the computer.")


# ------------------------------------------------------------------ CLI
def cmd_automation(a) -> None:
    import sys
    from . import capability, names
    from .state import State
    req = {"run": a.run}
    if a.automation_cmd == "resume":
        req["yes"] = bool(a.yes)
    res = None
    try:
        st = State()
        if st.exists():
            if a.automation_cmd == "status":
                res = status(st, a.run)
            else:
                res = names.ctl_call(st, {"cmd": "automation_resume", **req}, 900)
    except names.ServeBusy:
        sys.exit("agentj serve 没有响应 / agentj serve is not answering")
    except (OSError, ValueError):
        res = None
    if res is None:                         # inside the fence (state hidden): the Agent's capability.sock
        res = capability.client({"t": "automation_" + a.automation_cmd, **req}, timeout=900) or \
            {"result": "refused", "why": "no_serve", "detail": "Agent J 没在运行 / Agent J is not running"}
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    elif res.get("result") == "status":
        print(f"{res['id']} · {res['run_id'][:12]} · {res['status']}" + (f" · ← {res['from'][:12]}" if res.get("from") else ""))
        for k, n in enumerate(res["steps"], 1):
            print(f"  {k}. {n['id']:<20} {n['state']:<8} {n.get('line') or ''}")
        if res.get("failed"):
            print(f"  ✗ {res['failed'].get('step')}: {res['failed'].get('why')}")
    else:
        print(res.get("detail") or json.dumps(res, ensure_ascii=False))
    if res.get("result") not in ("queued", "status"):
        sys.exit(2 if res.get("result") != "needs_yes" else 3)


def add_parser(sub) -> None:
    p = sub.add_parser("automation", help="分步自动化：status · resume（从失败的步骤继续）/ multi-step automations: continue a failed run")
    ps = p.add_subparsers(dest="automation_cmd", required=True)
    x = ps.add_parser("resume", help="从失败的步骤继续，已完成的步骤不重跑 / continue from the failed step")
    x.add_argument("run", help="运行 id（或前缀）或自动化 id / run id (or prefix) or automation id")
    x.add_argument("--yes", action="store_true", help="确认重跑不可重复的步骤（仅主人终端）/ confirm re-running steps not safe to repeat")
    x.add_argument("--json", action="store_true")
    x = ps.add_parser("status", help="这次运行每一步的状态 / each step of a run")
    x.add_argument("run")
    x.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_automation)
