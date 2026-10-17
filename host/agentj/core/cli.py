"""`agentj order …` and `agentj core …` (AGENTJ-CORE-SPEC 12.4: the same order protocol for a main Agent that is not Agent J
Harness — Claude Code / Codex call these with Bash and get exactly what the `dispatch` tool gives).

    agentj order new --dir P --title T (--prompt TEXT | --prompt-file F) --goal G --write GLOB… --verify CMD… --timebox MIN
                     --report TEXT [--mode research] [--model provider/model] [--wait]
    agentj order list · status ID · send ID TEXT · stop ID · resume ID [TEXT] · report ID [--section N] · decisions ID
    agentj order standing --dir P [--add LINE] · reconcile
    agentj core models · core ask TEXT [--dir D] [--model M] [--wait]   (a headless main-Agent turn: no phone, so C14)

P359: only outside the fence (the state directory is invisible inside it; the serve-socket path for fenced main Agents is
C17). Hidden unless AGENTJ_CORE=1.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from . import LABEL, dispatcher, enabled, orders


def _guard() -> None:
    if not enabled():
        sys.exit(f"{LABEL} is off: set AGENTJ_CORE=1 in the service environment (P359 preview). / 未开启。")
    from ..fence import inside
    if inside():
        sys.exit("agentj order runs outside the fence in this version (the Agent's own `dispatch` tool works inside). "
                 "/ 这一版只能在隔离外运行。")


def _brief(a) -> dict:
    return {"goal": a.goal or "", "scope": {"write": a.write or []}, "verify": a.verify or [], "timebox": a.timebox,
            "report": a.report or ""}


def _wait(o: orders.Order, timeout: float) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end and not o.result():
        time.sleep(1)


def cmd_order(a) -> int:
    _guard()
    act = a.order_cmd
    try:
        if act == "new":
            text = Path(a.prompt_file).read_text(encoding="utf-8") if a.prompt_file else (a.prompt or "")
            o = orders.create(project=a.dir, prompt_text=text, brief=_brief(a), title=a.title, model=a.model,
                              mode=a.mode, origin="cli", parent={"kind": "cli"}, allowed_roots=[os.path.expanduser("~")])
            how = dispatcher.launch(o)
            print(json.dumps({"id": o.id, "launch": how, "dir": str(o.path)}, ensure_ascii=False))
            if a.wait:
                _wait(o, 60 * (o.meta["budget"]["wall_min"] + 2))
                print(orders.head_card(o))
            return 0
        if act == "list":
            for o in orders.all_orders():
                print(f"{o.id}  {o.state():8}  {o.meta['ceo']}  {o.meta['title']}")
            return 0
        if act == "standing":
            if a.add:
                print(orders.standing_add(a.dir, a.add))
            print(orders.standing(a.dir) or "(none)")
            return 0
        if act == "reconcile":
            print(json.dumps({"lost": dispatcher.reconcile(), "started": dispatcher.drain()}))
            return 0
        o = orders.get(a.id)
        if act == "decisions":
            for d in o.decisions():
                print(json.dumps(d, ensure_ascii=False))
            return 0
        args = {"action": act, "id": a.id}
        if act in ("send", "resume") and a.text:
            args["text"] = " ".join(a.text)
        if act == "report" and a.section:
            args["section"] = a.section
        print(dispatcher.tool(args, parent={"kind": "cli"}, allowed_roots=[]))
        return 0
    except orders.OrderError as e:
        print(f"✗ {e.code}" + (f" ({e.detail})" if e.detail else ""), file=sys.stderr)
        return 3


async def ask_once(text: str, root: str, model: str | None, wait: bool, environ=None, call=None, st_root=None,
                   out=print) -> dict:
    """A headless main-Agent turn (no serve, no phone: the gate has no approver → C14). With wait: every order this turn
    started is followed to its end, then ONE more turn reads the heads — what background_done does for serve."""
    from . import llm, prompt
    from .loop import Loop, SubAgents
    from .pipeline import Gate
    from .presets import resolve
    from .session import Session
    from .toolbox import Toolbox
    env = os.environ if environ is None else environ
    call = call or llm.chat
    preset = resolve(model, "main", env)
    root = os.path.realpath(root)
    started: list[str] = []
    from ..state import State
    st = State(Path(st_root)) if st_root else State()
    gate = Gate(root, None)
    tb = Toolbox(root, st=st, fenced=True, environ=env)      # files only (C14: no approver here, so no bash at all)

    def dispatch_sync(args):
        r = dispatcher.tool(args, parent={"kind": "cli"}, allowed_roots=[root], model=model, origin="owner_turn",
                            st_root=st_root, environ=env)
        if args.get("action") == "new" and r.startswith("order "):
            started.append(r.split()[1])
        return r

    async def dispatch(args):
        return await asyncio.to_thread(dispatch_sync, args)
    sroot = Path(st_root) if st_root else Path(os.path.expanduser(env.get("AGENTJ_STATE_DIR") or "~/.local/state/agentj"))
    sess = Session(sroot / "core" / "sessions" / f"cli-{int(time.time())}.jsonl")
    subs = SubAgents(preset=preset, root=root, gate=gate, sessions_dir=sess.path.parent, environ=env, call=call,
                     make_toolbox=lambda: Toolbox(root, st=st, fenced=True, environ=env))
    lp = Loop(preset=preset, system=prompt.system("main", root, preset.tier), specs=prompt.specs("main", preset.tier),
              gate=gate, toolbox=tb, session=sess, environ=env, call=call, extra={"task": subs, "dispatch": dispatch})
    try:
        r = await lp.run(text)
        out(r["text"])
        if wait and started:
            for oid in started:
                o = orders.get(oid, st_root)
                while not o.result():
                    await asyncio.sleep(1)
            note = "〔Agent J 系统提示〕" + "; ".join(f"order {i} ended" for i in started) + \
                   ". Read each report head (dispatch report) and tell the owner the outcome in two lines."
            r = await lp.run(note)
            out(r["text"])
        return {"text": r["text"], "orders": started, "usage": lp.usage}
    finally:
        tb.close()


def cmd_core(a) -> int:
    _guard()
    if a.core_cmd == "models":
        from .presets import listing
        for p in listing():
            print(f"{p['name']:16} key {p['key_env']:26} {'✓' if p['has_key'] else '—'}  "
                  f"{'verified' if p['verified'] else 'unverified'}{' · Coding Plan' if p['plan'] else ''}  "
                  f"{', '.join(p['models'])}")
        return 0
    r = asyncio.run(ask_once(" ".join(a.text), a.dir or os.getcwd(), a.model, a.wait))
    return 0 if r["text"] else 1


def add_parser(sub) -> None:
    o = sub.add_parser("order", help=f"{LABEL}：派单给项目 CEO / orders to project CEOs (AGENTJ_CORE=1)")
    os_ = o.add_subparsers(dest="order_cmd", required=True)
    n = os_.add_parser("new")
    n.add_argument("--dir", required=True)
    n.add_argument("--title", required=True)
    n.add_argument("--prompt")
    n.add_argument("--prompt-file")
    n.add_argument("--goal")
    n.add_argument("--write", action="append", metavar="GLOB")
    n.add_argument("--verify", action="append", metavar="CMD")
    n.add_argument("--timebox", type=int, default=orders.WALL_DEFAULT)
    n.add_argument("--report", default="the standard head + what changed + evidence")
    n.add_argument("--mode", choices=orders.MODES, default="work")
    n.add_argument("--model")
    n.add_argument("--wait", action="store_true")
    os_.add_parser("list")
    os_.add_parser("reconcile")
    s = os_.add_parser("standing")
    s.add_argument("--dir", required=True)
    s.add_argument("--add")
    for name in ("status", "send", "stop", "resume", "report", "decisions"):
        x = os_.add_parser(name)
        x.add_argument("id")
        if name in ("send", "resume"):
            x.add_argument("text", nargs="*")
        if name == "report":
            x.add_argument("--section", type=int)
    o.set_defaults(fn=lambda a: sys.exit(cmd_order(a)))
    c = sub.add_parser("core", help=f"{LABEL}：模型预置 · 无界面跑一轮 / presets · a headless turn (AGENTJ_CORE=1)")
    cs = c.add_subparsers(dest="core_cmd", required=True)
    cs.add_parser("models")
    k = cs.add_parser("ask")
    k.add_argument("text", nargs="+")
    k.add_argument("--dir")
    k.add_argument("--model")
    k.add_argument("--wait", action="store_true")
    c.set_defaults(fn=lambda a: sys.exit(cmd_core(a)))
