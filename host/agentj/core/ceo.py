"""The CEO runner (AGENTJ-CORE-SPEC 12.1 / 12.3 / 12.5): `python -P -m agentj.core.ceo <order id>`, started by the
dispatcher outside the fence. It owns the loop and the model key; files and commands go through a toolbox inside the fence
rooted at the project folder.

Opening (12.4): the project's entry document, the standing orders (verbatim, pstack ①), the brief, the task — and on a resume
the previous report head and every appended instruction again (pstack ① consolidated scope), with the rule to answer each
open item first (§14.4). Approvals: the askbridge to serve's phone card, origin chain built by serve; without it the gate has
no approver (C14). On the phone the order is an 在办事项 (busy item `order:<id>`); its end is a completion notice for the main
session (background_done), exactly like `agentj busy run`.

Always a report (12.9): stop / budget / step limit → one tool-less call for a partial report; a final message without a
complete head → one more tool-less call asking only for the head; result.json = verdict by the report, exit code as witness.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

from . import llm, orders, prompt
from .orders import Order

HEAD_FIX = ("〔报告头不完整〕Your report must START with these four lines and END with the VERDICT line. Rewrite the whole "
            "report now, nothing else:\n完成：<done>/<total>\nready：是|否|不适用\n需要上级：<…>\nblocked-by: <ids or ->\n…\n"
            "VERDICT: ok|attention|fail — <one sentence>")


def opening(o: Order, resume: dict | None) -> str:
    m = o.meta
    parts = []
    st = orders.standing(m["project"])
    if st:
        parts.append("Standing orders (always in force):\n" + st)
    parts.append(f"Order {o.id} — {m['title']} (mode: {m['mode']})\n" + orders.brief_text(m["brief"]))
    parts.append("Task:\n" + o.prompt())
    if resume is not None:
        inbox = o.inbox_all()
        if inbox:
            parts.append("All instructions appended so far:\n" + "\n".join(f"#{n}: {t}" for n, t in inbox))
        h = resume.get("head") or {}
        parts.append(f"This is a RESUME. The last run ended as '{resume.get('verdict')}' "
                     f"(state {resume.get('state')}, failure {resume.get('failure_kind')}). "
                     + (f"Open items of the last report: {', '.join(h.get('open') or [])}. " if h.get("open") else "")
                     + "First answer every open item: fixed / not fixed / no longer applies, with evidence; then continue.")
    return "\n\n".join(parts)


async def run(order_id: str, st_root=None, environ=None, call=None, fenced: bool | None = None) -> int:
    from .. import busy
    from ..state import State
    from .askbridge import Client
    from .loop import Loop, SubAgents
    from .pipeline import Gate
    from .presets import resolve
    from .session import Session
    from .toolbox import Toolbox
    env = dict(os.environ if environ is None else environ)
    env["AGENTJ_ORDER_ID"] = order_id
    o = orders.get(order_id, st_root)
    m = o.meta
    st = State(Path(st_root)) if st_root else State()
    (o.path / "pid").write_text(str(os.getpid()))
    hist = orders._jsonl(o.path / "history.jsonl")
    resume = hist[-1] if hist else None
    o.event("started", pid=os.getpid(), resume=resume is not None)
    t0 = time.monotonic()
    deadline = t0 + 60 * m["budget"]["wall_min"]
    item_key = f"order:{o.id}"
    try:
        busy.write_item(st.root, busy.item(item_key, m["ceo"], "ceo", m["title"], pid=os.getpid(), ns=busy.pidns(),
                                           report=str(o.path / "report.md"), lk="dispatch", harness="core",
                                           model=m.get("model") or "", cwd=m["project"]))
    except OSError:
        pass
    code, ab, kind, text = 0, None, None, ""
    usage = {"input": 0, "output": 0}
    client = Client(o, st_root)
    boxes = []
    try:
        preset = resolve(m.get("model"), "ceo", env)
        if not preset.key(env):
            raise llm.LLMError("auth", None, "no key")
        linked = await client.connect()
        o.event("approval_link", ok=linked)
        denied = o.denied()
        stopped = lambda: o.control() == "stop"
        extra = m.get("danger_extra")       # P359 review M2: the owner's extra danger rules reach the CEO too
        if extra is None:
            try:
                extra = (st.agent_config() or {}).get("danger_extra")
            except Exception:  # noqa: BLE001
                extra = None
        gate = Gate(m["project"], client if linked else None, research=m["mode"] == "research", extra=extra,
                    scope_write=(m["brief"].get("scope") or {}).get("write"), denied=denied, stopped=stopped,
                    on_denied=o.add_denied)
        # P359 review M3: the order says (the parent session's own isolation choice); no environment opt-out
        fence_on = fenced if fenced is not None else m.get("fence", True) is not False

        def make_toolbox():
            tb = Toolbox(m["project"], st=st, fenced=fence_on, environ=env)
            boxes.append(tb)
            return tb

        tb = await asyncio.to_thread(make_toolbox)
        seen = o.delivered()

        def inbox():
            out = []
            for n, body in o.inbox_all():
                if n not in seen:
                    seen.add(n)
                    out.append((n, body))
            return out
        if resume is not None:              # a resume re-pastes every appended instruction in the opening: mark delivered
            for n, _ in o.inbox_all():
                seen.add(n)

        def on_event(ev, **kw):
            o.event(ev, **{k: v for k, v in kw.items() if v is not None})
        origin = None                       # the CEO's own asks: serve adds the chain; sub-agents add their suffix
        subs = SubAgents(preset=preset, root=m["project"], gate=gate, make_toolbox=make_toolbox,
                         sessions_dir=o.path / "sessions", environ=env, call=call, max_parallel=m["budget"]["subagents"],
                         budget_tokens=m["budget"].get("tokens"), deadline=deadline, control=o.control,
                         on_event=on_event, origin="")

        async def decide(args):
            o.decide("work", str(args.get("decision", "")), str(args.get("why", "")), str(args.get("evidence", "")))
            return "logged"
        sess = Session(o.path / "sessions" / "ceo.jsonl")
        lp = Loop(preset=preset, system=prompt.system("ceo", m["project"], preset.tier), specs=prompt.specs("ceo", preset.tier),
                  gate=gate, toolbox=tb, session=sess, environ=env, call=call, budget_tokens=m["budget"].get("tokens"),
                  deadline=deadline, inbox=inbox, control=o.control, on_event=on_event,
                  extra={"task": subs, "decide": decide}, who=origin)
        first = opening(o, resume) if resume is not None or not sess.load() else \
            "〔续跑〕The previous run of this order was interrupted. Continue from where you are."
        r = await lp.run(first)
        text = r["text"] or ""
        if r["reason"] in ("stopped", "budget"):
            ab = r["reason"]
        if not orders.parse_head(text)["complete"]:
            r2 = await lp._final(HEAD_FIX, r["reason"])
            if orders.parse_head(r2["text"])["complete"] or not text:
                text = r2["text"] or text
        usage = {"input": lp.usage["input"] + subs.usage["input"], "output": lp.usage["output"] + subs.usage["output"]}
    except llm.LLMError as e:
        code, kind = 2, e.kind
        o.event("model_error", kind=e.kind, status=e.status)
    except Exception as e:  # noqa: BLE001 — the order still gets a result
        code, kind = 1, "unknown"
        o.event("runner_error", error=type(e).__name__)
    finally:
        await client.close()
        for b in boxes:
            await asyncio.to_thread(b.close)
    if text:
        orders._write(o.path / "report.md", text)
    head = orders.parse_head(o.report() if text else "")
    verdict = orders.judge(code, head, ab)
    state = ab or ("done" if code == 0 else "failed")
    fk = orders.failure_kind(kind, ab, code) if verdict != "done" else None
    res = {"state": "stopped" if ab == "stopped" else state, "exit_code": code, "wall_s": round(time.monotonic() - t0, 1),
           "tokens": usage, "head": head, "verdict": verdict, "failure_kind": fk, "ended": time.time(),
           "model": m.get("model")}
    if verdict in ("failed", "no_report", "incomplete"):
        res["root_cause_hash"] = orders.root_cause_hash(state, fk or verdict, orders.places_of(text))
    res["retry"] = orders.retry_advice(hist + [res])
    o.write_result(res)
    o.event({"done": "done", "failed": "failed"}.get(state, state), verdict=verdict)
    o.set_control(None)
    try:
        it = busy.item(item_key, m["ceo"], "ceo", m["title"], report=str(o.path / "report.md"))
        busy.write_finished(st.root, it, code)
        busy.remove_item(st.root, item_key)
    except OSError:
        pass
    try:
        from .dispatcher import drain
        drain(st_root, env)
    except Exception:  # noqa: BLE001
        pass
    return code


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: python -m agentj.core.ceo <order id>", file=sys.stderr)
        return 2
    return asyncio.run(run(argv[0]))


if __name__ == "__main__":
    sys.exit(main())
