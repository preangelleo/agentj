"""#585 「紧急修复」做彻底: every button proves what it promises before it says so.

Chairman 11-07 18:14: 「这几个按钮……点了以后，好像没有几次真正的是帮用户解决了问题呢？……要不点了没用，放在那里干啥呢？」

Pieces (reset.Desk runs them on the owner's own signed `rescue_do <action>`; nothing here verifies a signature, touches an
approval decision rule, a pairing or a scan — those stay exactly as in 0.28.3):

    probe(h)            the Agent answers one host line (ProbeSend: the harness-switch hello's queue and provenance,
                        a system turn outside any phone page). Success = the harness produced reply text after the probe
                        was queued (serve's `agent_said_at`, the harness-switch job's own proof). Never on an e-stop.
    stop_hard(h, dev)   「停止」: open approval / question waits end (as 「全部停下」 does), the stuck turn is interrupted
                        (/stop's ladder, reset.unstick), then the harness process tree Agent J started for it is ended and
                        CHECKED gone — its sub-agents and tool children included. A program the owner started (shared window,
                        desktop Codex) is never signalled: said plainly. The conversation stays; no stop switch is latched.
    Marker              <state>/rescue_pending.json (0600): a service restart started by 「重启服务」 / 「一键修复」, so the
                        page's `rescue_do repair_cont` / `probe` after the reconnect can say whether serve really restarted.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import time

from .compose import Send
from .text import pick

PROBE_S = 90.0           # s: a cold harness start (resume a long conversation) plus one short answer
PROBE_POLL = 0.25
GONE_WAIT = 6.0          # s: the ended process tree must be gone within this
MARKER = "rescue_pending.json"
MARKER_TTL = 900         # s: a restart older than this is not "the one we started"
BUSY = ("working", "compacting")


class ProbeSend(Send):
    """The rescue probe: the Agent's ordinary queue, outside any phone page (turn 0, like the harness-switch hello), so the
    reply lands as the Agent's own message — the owner sees the Agent really answered. Provenance: the owner's own host."""

    def __init__(self, text: str):
        from .provenance import system
        super().__init__("rescue-probe", "probe", 0, text=text, by="Agent J", source=system())


def probe_text(lang: str) -> str:
    return pick(lang,
                "〔Agent J 系统提示〕这是「紧急修复」发出的连通测试，不是主人的消息。请只回复一句：「我在，可以继续了。」"
                "不要调用任何工具，不要做别的事。",
                "[Agent J system note] This is the Emergency repair connectivity check, not a message from the owner. Reply "
                "with exactly one sentence: \"I'm here and ready to continue.\" Do not call any tool and do nothing else.")


async def probe(h, timeout: float = PROBE_S, sleep=asyncio.sleep, clock=time.monotonic) -> dict:
    """→ {"ok": True, "secs"} | {"ok": False, "why": no_agent | estop | no_reply, "status"?}. The probe never waits behind
    an e-stop and is withdrawn (state cancelled) when it got no answer, so a later restart does not carry it along."""
    a = getattr(h, "agent", None)
    if a is None:
        return {"ok": False, "why": "no_agent"}
    if h.stopped():
        return {"ok": False, "why": "estop"}
    t0, c0 = time.time(), clock()
    p = ProbeSend(probe_text(getattr(h, "lang", "zh")))
    try:
        (getattr(h, "agent", None) or a).submit(p)
    except Exception:  # noqa: BLE001
        return {"ok": False, "why": "no_agent"}
    h.st.log("rescue_probe", agent=getattr(a, "kind", None), result="sent")
    end = c0 + timeout
    while clock() < end:
        if (getattr(h, "agent_said_at", 0) or 0) > t0:
            secs = round(clock() - c0, 1)
            h.st.log("rescue_probe", agent=getattr(h.agent, "kind", None), result="answered", secs=secs)
            return {"ok": True, "secs": secs}
        if h.stopped():
            p.state = "cancelled"
            return {"ok": False, "why": "estop"}
        await sleep(PROBE_POLL)
    if p.state == "pending":
        p.state = "cancelled"
    status = h.eff_status() if hasattr(h, "eff_status") else None
    h.st.log("rescue_probe", agent=getattr(getattr(h, "agent", None), "kind", None), result="no_reply", status=status)
    return {"ok": False, "why": "no_reply", "status": status}


# ------------------------------------------------------------------ 「停止」
def _alive(pid: int) -> bool:
    if os.name == "nt":                 # os.kill(pid, 0) would terminate it there (security #585-7): read-only check
        from .turn_heal import proc_row
        return proc_row(pid) is not None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    with contextlib.suppress(OSError):
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    return True


def _tree(root: int, rows: dict) -> list:
    kids: dict = {}
    for pid, (ppid, _) in rows.items():
        kids.setdefault(ppid, []).append(pid)
    out, todo = [], [root]
    while todo:
        p = todo.pop()
        for k in kids.get(p, ()):
            if k not in out:
                out.append(k)
                todo.append(k)
    return out


def end_waits(h) -> int:
    """Open approval cards and questions end the way 「全部停下」 ends them (deny / stopped): a turn waiting on a card the
    phone never got is not stuck forever. → how many ended."""
    n = 0
    for a in list(getattr(h, "asks", {}).values()):
        fut = getattr(a, "fut", None)
        if fut is not None and not fut.done():
            fut.set_result(("deny", None, None, None))
            n += 1
    for q in list(getattr(h, "questions", {}).values()):
        fut = getattr(q, "fut", None)
        if fut is not None and not fut.done():
            fut.set_result(("stopped", None, None, None, None))
            n += 1
    return n


async def stop_hard(h, device, sleep=asyncio.sleep, clock=time.monotonic) -> dict:
    """→ {"ok", "how", "waits", "ended", "left", "why"?}. ok only when the turn is over AND every process of the tree Agent
    J started for the Agent is gone (checked, not assumed)."""
    from . import reset, turn_heal
    from .agent import Agent
    a = getattr(h, "agent", None)
    if a is None:
        return {"ok": False, "why": "no_agent", "waits": 0, "ended": 0, "left": 0}
    waits = end_waits(h)
    busy0 = getattr(a, "status", None) in BUSY or reset._desktop_waiting(a)
    snap = []                                            # the tree as it was when the owner tapped: what must be gone
    p0 = getattr(a, "proc", None)
    if p0 is not None and getattr(p0, "returncode", 0) is None and turn_heal._owned_harness(a) and type(a).halt is Agent.halt:
        rows0 = await asyncio.to_thread(turn_heal.proc_rows)
        if turn_heal.may_stop(h.st, p0.pid, own=p0, rows=rows0):
            snap = [p0.pid] + _tree(p0.pid, rows0)
    un = {"ran": False}
    if busy0:
        un = await reset.unstick(h, device, sleep=sleep, clock=clock)
        if un.get("why") == "desktop_live":
            h.st.log("rescue_stop", agent=a.kind, result="desktop_live", waits=waits)
            return {"ok": False, "why": "desktop_live", "waits": waits, "ended": 0, "left": 0}
        if un.get("how") == "released":     # a dead turn in the owner's own Codex: never signalled, the message goes on
            h.st.log("rescue_stop", agent=a.kind, result="released", waits=waits)
            return {"ok": True, "how": "released", "waits": waits, "ended": 0, "left": 0}
    a = getattr(h, "agent", None) or a
    p = getattr(a, "proc", None)
    owned = turn_heal._owned_harness(a) and type(a).halt is Agent.halt
    ended = left = 0
    if p is not None and getattr(p, "returncode", 0) is None:
        rows = await asyncio.to_thread(turn_heal.proc_rows)
        if not owned or not turn_heal.may_stop(h.st, p.pid, own=p, rows=rows):
            h.st.log("rescue_stop", agent=a.kind, result="foreign", waits=waits)
            still = getattr(a, "status", None) in BUSY
            return {"ok": not still, "how": "interrupted" if not still else None, "why": "foreign" if still else None,
                    "waits": waits, "ended": 0, "left": 0}
        pids = [p.pid] + _tree(p.pid, rows)
        snap = list(dict.fromkeys(snap + pids))
        await a.halt(clear_queue=False)                      # the soft interrupt, then TERM → KILL of the tree
        end = clock() + GONE_WAIT
        while any(_alive(x) for x in pids) and clock() < end:
            await sleep(0.1)
        for x in pids:                                       # a grandchild that escaped the group (setsid): only ours
            row = rows.get(x)
            if x != p.pid and _alive(x) and row and turn_heal.signal_same(x, row, signal.SIGKILL):
                pass
        end = clock() + 2.0
        while any(_alive(x) for x in pids) and clock() < end:
            await sleep(0.1)
        td = getattr(a, "turn_done", None)
        if td is not None:
            td.set()
    if snap:
        end = clock() + GONE_WAIT
        while any(_alive(x) for x in snap) and clock() < end:
            await sleep(0.1)
        left = sum(1 for x in snap if _alive(x))
        ended = len(snap) - left
    end = clock() + 5.0
    while getattr(getattr(h, "agent", None), "status", None) in BUSY and clock() < end:
        await sleep(0.1)
    still = getattr(getattr(h, "agent", None), "status", None) in BUSY
    ok = not still and not left
    h.st.log("rescue_stop", agent=a.kind, result="ok" if ok else "left", waits=waits, ended=ended, left=left,
             busy=bool(busy0))
    out = {"ok": ok, "how": "ended" if ended else ("interrupted" if busy0 else "idle"), "waits": waits, "ended": ended,
           "left": left}
    if not ok:
        out["why"] = "still_running" if left else "not_stopped"
    return out


def stop_text(res: dict, lang: str) -> str:
    w, n = res.get("waits") or 0, res.get("ended") or 0
    why = res.get("why")
    if why == "no_agent":
        return pick(lang, "Agent J 主机上还没选好 AI 助手，没有可以停的。", "No AI assistant is set up on the Agent J host; nothing to stop.")
    if why == "desktop_live":
        return pick(lang, "卡住的这一轮在你自己在 Agent J 主机上打开的 Codex 里，Agent J 不会关掉它。请在那台电脑上按 Esc 或关掉那个窗口；"
                          "不想再遇到，可以在「设置 → 安全 → 会话模式」选「使用独立会话」。",
                    "The stuck turn runs in the Codex you opened yourself on the Agent J host; Agent J never closes it. Press Esc or "
                    "close that window there. To avoid this, choose the independent session in Settings → Safety → Session mode.")
    if why == "foreign":
        return pick(lang, "卡住的这一轮在你自己启动的程序里，Agent J 不会关掉它。请在 Agent J 主机上退出那个程序，或点「重启服务」。",
                    "The stuck turn runs in a program you started yourself; Agent J never closes it. Quit it on the Agent J host, "
                    "or tap Restart service.")
    if why in ("still_running", "not_stopped"):
        return pick(lang, f"还有 {res.get('left') or 1} 个进程没停下来。请点「重启服务」，它会结束 Agent J 的全部进程。",
                    f"{res.get('left') or 1} process(es) did not stop. Tap Restart service: it ends every Agent J process.")
    parts = []
    if res.get("how") == "released":
        from .reset import UNSTICK
        parts.append(pick(lang, *UNSTICK["released"]))
    elif n:
        parts.append(pick(lang, f"已结束卡住的 AI 进程和它带起来的 {max(0, n - 1)} 个子进程（子 Agent、命令），已确认都不在了。",
                          f"The stuck AI process and the {max(0, n - 1)} process(es) it started (sub-agents, commands) are ended and "
                          "confirmed gone."))
    elif res.get("how") == "interrupted":
        parts.append(pick(lang, "卡住的这一轮已停下。", "The stuck turn is stopped."))
    else:
        parts.append(pick(lang, "现在没有正在运行的一轮，也没有残留的 AI 进程。", "No turn was running and no AI process was left over."))
    if w:
        parts.append(pick(lang, f"{w} 张一直没答完的确认/提问已撤下。", f"{w} approval / question wait(s) that never finished were withdrawn."))
    parts.append(pick(lang, "对话保留着，下一条消息接着原来的对话。", "The conversation is kept; your next message continues it."))
    return (" " if lang == "en" else "").join(parts)


# ------------------------------------------------------------------ the restart marker
def write_marker(st, flow: str, r: str, device, steps=None) -> None:
    rec = {"flow": flow, "r": r, "device": device, "at": int(time.time()), "pid": os.getpid(), "steps": list(steps or [])}
    st.write_private(st.root / MARKER, json.dumps(rec).encode())


def take_marker(st) -> dict | None:
    """The restart this serve came up after (once; removed when read). None = none, stale or unreadable."""
    path = st.root / MARKER
    try:
        rec = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    with contextlib.suppress(OSError):
        path.unlink()
    if not isinstance(rec, dict) or not isinstance(rec.get("at"), int) or time.time() - rec["at"] > MARKER_TTL:
        return None
    rec["restarted"] = rec.get("pid") != os.getpid()
    return rec


# ------------------------------------------------------------------ plain sentences
def probe_line(res: dict, lang: str) -> str:
    if res.get("ok"):
        secs = res.get("secs") or 0
        return pick(lang, f"Agent 已经回话了（用了 {secs:.0f} 秒），可以继续聊天。" if secs >= 1 else "Agent 已经回话了，可以继续聊天。",
                    f"The Agent answered ({secs:.0f} s); you can chat again." if secs >= 1 else "The Agent answered; you can chat again.")
    why = res.get("why")
    if why == "estop":
        return pick(lang, "现在是「全部停下」状态，Agent 不会回话。点下面的「恢复」，Agent J 会再确认一次它能回话。",
                    "Agent J is stopped (Stop everything), so the Agent cannot answer. Tap Resume below; Agent J checks again "
                    "that it answers.")
    if why == "no_agent":
        return pick(lang, "Agent J 主机上还没选好 AI 助手。", "No AI assistant is set up on the Agent J host.")
    return pick(lang, f"发了一句测试，{PROBE_S:.0f} 秒内 Agent 没有回话。", f"A test line got no answer from the Agent within {PROBE_S:.0f} s.")


PROBE_RUN = ("正在确认 Agent 能回话（发一句测试）…", "Checking that the Agent answers (one test line)…")
