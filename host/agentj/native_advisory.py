"""0378 (0.30.78): the shared Claude session no longer stalls behind a native advisory it cannot see.

Claude Code 2.1.323 shows a one-time "We recommend compacting earlier" dialog at the end of a large turn (registry
`waiting` / "dialog open"). It swallowed the phone's `/compact`, the host waited 10 minutes in silence, and the phone
messages behind it were written to a native queue that never dequeued them.

- `advisory`: the exact dialog (shared_menu.detect → `autocompact_offer`), on the native Agent J itself started on its own
  pty, with the registry agreeing → one Esc ("not now": nothing changes). Never another dialog, never a permission box,
  never the owner's own terminal (there it is the owner's menu card).
- `watch` / `unaccepted`: a native that stays `waiting` with nothing on the phone for it, or took a phone message into its
  queue and never dequeued it, is said so once per episode: the queue card's `stuck` flag, and for a dialog one notice
  with the existing 「重启 Agent」 button.
- held pages never stay queued for good: redelivered / said undelivered only when the restart verifiably ended the old
  native; in every other case they leave the queue as `unconfirmed` (their words may still run).
"""
from __future__ import annotations

import asyncio
import os
import time

from .text import pick

CONTROL_ACCEPT_WAIT = 30.0     # s for the native to take a typed /compact · /clear (PreCompact · SessionStart(clear))
CONTROL_SETTLE_S = 2.0         # s the registry must stay idle after a turn end before a control is typed
ADVISORY_CONFIRM_S = 3.0       # s the registry may lag the screen; after it the dialog is offered as an ordinary menu card
STUCK_WAITING_S = 45.0         # s of registry `waiting` with no ask / menu / card of ours before it is called stuck
CLI_RESTART_WAIT = 120.0       # s `agentj agent restart` waits for a running turn before it restarts anyway
STUCK_HELD_S = 30.0            # s a held message may still be dequeued (after CLAUDE_INPUT_WAIT) before the native is stuck
HELD_KEEP = ("held", "detail", "queued", "waited_s", "stuck")


def restart_path(lang) -> str:
    return pick(lang, "设置 → 紧急修复 →「重启 Agent」", "Settings → Emergency repair → Restart Agent")


def owned(a) -> bool:
    """The native process is Agent J's own child on its own pty (ordinary_start), not the owner's terminal."""
    s, n = getattr(a, "session", None), getattr(a, "native", None)
    return bool(s and n is not None and n.pid == s.get("pid") and getattr(n, "returncode", None) is None
                and getattr(a, "master", None) is not None)


def registry(a):
    """The served session's own registry row (exact pid), else None. Reads a small file: call off the loop."""
    from . import shared
    s = a.session
    if not s:
        return None
    rows = shared.claude_sessions(a.cfg["dir"], s.get("sessionId") or "")
    return rows[0] if rows and rows[0].get("pid") == s.get("pid") else None


WAITING_FOR = ("dialog open", "input needed", "sandbox request", "goal proposal")


def waiting_for(row):
    """The registry's `waitingFor` for a log line: a known phrase, else "other" (never free text)."""
    v = (row or {}).get("waitingFor")
    return None if not v else v if v in WAITING_FOR else "other"


def _log(a, event, **kw):
    a.host.st.log(event, **kw)


def session_identity(row):
    return tuple((row or {}).get(k) for k in ("sessionId", "pid", "procStart", "startedAt"))


def pty_identity(a):
    return (session_identity(a.session), a.native, a.master) if owned(a) else None


async def advisory(a, info) -> str:
    """Desk._poll saw the autocompact offer. → "closed" (Esc sent, dialog gone) · "wait" (not yet corroborated) ·
    "card" (not ours to press: offer it like any other menu)."""
    from .shared_menu import detect
    now = time.monotonic()
    target = pty_identity(a)
    ident = session_identity(a.session)
    ep = a.__dict__.get("_advisory_ep")
    if not ep or ep["id"] != ident:
        ep = a._advisory_ep = {"id": ident, "since": now, "tried": False}
    if ep["tried"] or target is None:
        return "card"
    if a.state.status == "waiting" or a.host.stopped() is True:
        return "wait"                                   # a permission card / Stop everything: nothing is typed
    row = await asyncio.to_thread(registry, a)
    if not row or row.get("status") != "waiting" or row.get("waitingFor") != "dialog open":
        return "wait" if now - ep["since"] < ADVISORY_CONFIRM_S else "card"
    desk = a.menu_desk
    if (a.halting or desk.blocked() or a.host.agent is not a or pty_identity(a) != target
            or session_identity(row) != ident or a.state.status == "waiting"
            or not desk.surface.valid or desk.surface.pending or detect(desk.surface.text()) != info):
        return "wait"
    ep["tried"] = True
    try:
        os.write(a.master, b"\x1b")
    except OSError:
        return "card"
    closed = False
    for _ in range(12):
        await asyncio.sleep(.25)
        if pty_identity(a) != target or a.halting or desk.blocked():
            return "wait"
        text = desk.surface.text()
        if desk.surface.valid and not desk.surface.pending and text and detect(text) != info:
            closed = True
            break
    _log(a, "shared_native_notice", kind="autocompact_offer", action="not_now", result="closed" if closed else "still_open")
    if not closed:
        return "card"
    a._advisory_closed = a._settle_at = time.monotonic()
    return "closed"


def turn_ended(a) -> None:
    a._settle_at = time.monotonic()


def settled(a) -> bool:
    at = getattr(a, "_settle_at", None)
    return at is None or time.monotonic() - at >= CONTROL_SETTLE_S


def unaccepted_text(a, name) -> str:
    lang = getattr(a.host, "lang", "zh")
    head = {"clear": "没清成", "compact": "没压缩成"}.get(name, "没执行")
    return pick(lang, f"{head}：主会话没有接收 /{name}（{int(CONTROL_ACCEPT_WAIT)} 秒内没有开始）。可以再发一次；还是不行，"
                      f"到 {restart_path('zh')}，对话会接着原来的。排在后面的消息会照常发送。",
                f"No change: the main session did not take /{name} (it did not start within {int(CONTROL_ACCEPT_WAIT)} s). "
                f"Send it again; if that fails too, use {restart_path('en')} — the conversation continues. "
                f"Messages waiting behind it go on as usual.")


def blocked_text(a, name) -> str:
    lang = getattr(a.host, "lang", "zh")
    head = {"clear": "没清成", "compact": "没压缩成"}.get(name, "没执行")
    return pick(lang, f"{head}：主会话停在一个提示框上，/{name} 没有发送。到 {restart_path('zh')}，对话会接着原来的；"
                      f"然后再发一次 /{name}。排在后面的消息会照常发送。",
                f"No change: the main session is stopped at a dialog; /{name} was not sent. Use {restart_path('en')} — the "
                f"conversation continues — then send /{name} again. Messages waiting behind it go on as usual.")


def single_chunk(session) -> bool:
    """Claude Code ≥ 2.1.323 executes a control written as one chunk (`/compact\\r`): no gap between the text and its Enter
    in which the screen could change (security r2). Older or unknown versions keep the two writes they were verified with."""
    import re
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", str((session or {}).get("version", "")))
    return bool(m) and tuple(map(int, m.groups())) >= (2, 1, 323)


def unknown_wait(a, reg) -> bool:
    """The registry says `waiting` and neither the hooks, the menu desk nor a phone card knows what for."""
    return (reg == "waiting" and a.state.status != "waiting" and not getattr(a.menu_desk, "current", None)
            and not a._asks_open() and not getattr(a, "open_cards", None))


def drop_controls(host, old, items=(), why="restart") -> int:
    """End waiting and in-flight native controls before retiring their adapter."""
    from .agent import Cmd
    q = getattr(getattr(old, "q", None), "_queue", None) or ()
    n, seen = 0, set()
    for cmd in (getattr(old, "lock_cmd", None), getattr(old, "cur_cmd", None), *list(q), *items):
        if not isinstance(cmd, Cmd) or id(cmd) in seen:
            continue
        seen.add(id(cmd))
        if cmd.cancelled or cmd.name not in ("compact", "clear"):
            continue
        cmd.cancelled = True
        page = host.hist.get(cmd.turn) if cmd.turn is not None else None
        if page and page.get("end") == "open":
            _finish_control(host, cmd.turn, cmd.name, cmd.native_started, why)
            n += 1
    return n


def _finish_control(host, turn, name, started, why):
    lang = getattr(host, "lang", "zh")
    text = (pick(lang, f"主会话已重启，无法确认 /{name} 是否完成。", f"The main session restarted before /{name} could be confirmed.")
            if started else pick(lang, f"主会话已重启，这条 /{name} 没有执行；需要的话请再发一次。",
                                 f"The main session was restarted; this /{name} did not run. Send it again if you need it."))
    host.hist_update(turn, end="stopped", card={}, text=text)
    host.st.log("shared_control_unconfirmed" if started else "shared_control_dropped", cmd=name, why=why)


def close_previous_controls(host):
    for turn, page in list(host.hist.turns.items()):
        card = page.get("card") or {}
        name = card.get("cmd")
        if (page.get("end") == "open" and (page.get("src") or {}).get("k") == "cmd"
                and name in ("compact", "clear") and (card.get("control_pending") or card.get("cancel_wait"))):
            # After a crash the disk may still have the pre-write marker.
            _finish_control(host, turn, name, True, "serve_start")


# ------------------------------------------------------------------ stuck native
def _pages(a):
    host = a.host
    hist = getattr(host, "hist", None)
    if hist is None or not callable(getattr(host, "hist_update", None)):
        return
    tids = [tid for tid, _ in getattr(a, "held", ())]
    cur = getattr(host, "cur_turn", None)
    if type(cur) is int and cur not in tids:
        tids.append(cur)
    for tid in tids:
        page = hist.get(tid)
        card = (page.get("card") if isinstance(page, dict) else None) or {}
        if (card.get("held") == "pending" or card.get("queued") is True and page.get("end") == "open") and card.get("stuck") is not True:
            host.hist_update(tid, card={**card, "stuck": True})


def stuck(a, why, row=None, tell=True) -> bool:
    """Flag the waiting queue cards; once per episode (why + process) log it and — for a dialog — say so in one notice
    with the restart button. A message the native never dequeued keeps to its own queue card (P424 #75: no page of its own)."""
    _pages(a)
    s = a.session or {}
    ep = (why, s.get("sessionId"), s.get("pid"))
    if a.__dict__.get("_stuck_ep") == ep:
        return False
    a._stuck_ep = ep
    row = row or {}
    _log(a, "shared_native_stuck", why=why, registry=str(row.get("status"))[:12], waiting_for=waiting_for(row))
    if tell and why == "dialog":
        lang = getattr(a.host, "lang", "zh")
        text = pick(lang, f"主会话停在一个提示框上，手机发的消息和命令进不去。可以到 {restart_path('zh')} 重启主会话，对话会接着原来的。",
                    f"The main session is stopped at a dialog; phone messages and commands cannot get in. Use {restart_path('en')} "
                    f"to restart the main session; the conversation continues.")
        notice = getattr(a.host, "agent_recovery_notice", None)
        (notice if callable(notice) else a.host.agent_notice)(text)
    return True


def recovered(a) -> None:
    a.__dict__.pop("_stuck_ep", None)


def watch(a, row) -> None:
    """observe(): the registry says `waiting` while hooks, menu desk and the phone's cards know of nothing to wait for."""
    waiting = bool(row) and row.get("status") == "waiting"
    known = (a.state.status == "waiting" or bool(getattr(a.menu_desk, "current", None)) or a._asks_open()
             or bool(getattr(a, "open_cards", None)))
    if not waiting or known:
        a._waiting_since = None
        ep = a.__dict__.get("_stuck_ep")
        if ep and ep[0] == "dialog" and not waiting:
            recovered(a)
        return
    now = time.monotonic()
    if getattr(a, "_waiting_since", None) is None:
        a._waiting_since = now
    elif now - a._waiting_since >= STUCK_WAITING_S:
        # the owner's own terminal with nothing from the phone waiting: logged, not announced
        want = owned(a) or a.phone_turn or a.cur_cmd is not None or bool(a.held) or not a.q.empty()
        stuck(a, "dialog", row, tell=bool(want))


def unaccepted(a, tid, policy) -> None:
    """turn(): the socket write went out, the native never dequeued it (`shared_input_unaccepted`). A message held by the
    owner's own inbound setting (`policy`) is not a stuck native."""
    send = getattr(a, "cur_send", None)
    if tid is not None and send is not None:
        a.__dict__.setdefault("held_sends", {})[tid] = (send, (a.session or {}).get("sessionId"))
        for k in [k for k in a.held_sends if k not in {t for t, _ in a.held}]:
            del a.held_sends[k]
    if not policy and tid is not None:
        asyncio.get_running_loop().call_later(STUCK_HELD_S, _still_held, a, tid)


def _still_held(a, tid) -> None:
    """STUCK_HELD_S later the message is still in nobody's hands: the queue card's own `stuck` flag (the phone's queue
    warning and its restart entry) — P424 #75 keeps it off a page of its own."""
    if getattr(a.host, "agent", a) is not a or not any(t == tid for t, _ in a.held):
        return
    try:
        row = registry(a)
    except (OSError, ValueError, TypeError, KeyError):
        row = None
    stuck(a, "no_dequeue", row)


# ------------------------------------------------------------------ held pages (P424) after a restart / stop / serve start
# A held page's words were already written to the native's socket. "Not delivered" / a second delivery is said only when
# that process has verifiably ended (the restart killed it); otherwise the page leaves the queue as `unconfirmed` without guessing from transcript text.


def _undelivered_text(host) -> str:
    return pick(getattr(host, "lang", "zh"), "这条消息没有送达主会话，没有执行。需要的话请再发一次。",
                "This message did not reach the main session and did not run. Send it again if you need it.")


def _unconfirmed_text(host) -> str:
    return pick(getattr(host, "lang", "zh"),
                f"已从排队中移除。这条消息之前已经交给主会话，可能仍会被执行；要确保丢弃，请到 {restart_path('zh')}。",
                f"Removed from the queue. This message had already been handed to the main session and may still run; "
                f"to make sure it is discarded, use {restart_path('en')}.")


def _held_card(host, tid):
    page = host.hist.get(tid) if type(tid) is int else None
    card = (page.get("card") if isinstance(page, dict) else None) or {}
    if card.get("held") != "pending":
        return None, None
    heal = getattr(host, "turn_heal", None)
    if heal is not None:
        heal.pages.discard(tid)
    return page, {k: v for k, v in card.items() if k not in HELD_KEEP}


def mark_undelivered(host, tid) -> bool:
    """Only for a message whose native process has ended (process_ended): nothing can run it any more."""
    page, rest = _held_card(host, tid)
    if page is None:
        return False
    host.hist_update(tid, text=_undelivered_text(host), end="failed", card={**rest, "undelivered": True})
    host.st.log("shared_held_undelivered", turn=tid)
    return True


def unconfirm(host, tid) -> bool:
    """End a queued page whose native delivery cannot be determined."""
    page, rest = _held_card(host, tid)
    if page is None:
        return False
    host.hist_update(tid, text=_unconfirmed_text(host), end="stopped", card={**rest, "unconfirmed": True})
    host.st.log("shared_held_unconfirmed", turn=tid)
    return True


def process_ended(a) -> bool:
    """The served native process is verifiably gone: our own child reaped, or the pid no longer the registered process."""
    s, n = getattr(a, "session", None), getattr(a, "native", None)
    if not isinstance(s, dict) or type(s.get("pid")) is not int:
        return False
    if n is not None and n.pid == s["pid"]:
        return getattr(n, "returncode", None) is not None
    try:
        os.kill(s["pid"], 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    from .shared import _same_process
    return bool(s.get("procStart")) and not _same_process(s)


def take_held(a) -> list:
    """[(page id, Send | None, session id, body)] of the adapter's held messages, oldest first; the adapter forgets them."""
    held = getattr(a, "held", None)
    if not isinstance(held, list):
        return []
    sends = a.__dict__.pop("held_sends", None) or {}
    out = [(tid, *(sends.get(tid) or (None, None)), body) for tid, body in held]
    if out:
        a.held = []
    return out


def clear_held(host, a=None) -> int:
    """/stop, serve start, a rebind to another process: every page still `held: pending` leaves the queue as unconfirmed."""
    if a is not None:
        take_held(a)
    hist = getattr(host, "hist", None)
    if hist is None:
        return 0
    return sum(unconfirm(host, tid) for tid in [t for t, p in list(hist.turns.items())
                                                                 if ((p.get("card") or {}).get("held")) == "pending"])


def redeliver(host, a, items, sid, ended=False) -> int:
    """After 「重启 Agent」: `items` (take_held). `ended` = the old native process is verifiably gone — only then a message
    goes first in `a`'s queue again (same conversation `sid`, words still in memory) or is said not delivered. Otherwise
    it is unconfirmed. → how many were queued again."""
    again = []
    for tid, send, was, _ in items:
        page = host.hist.get(tid) if type(tid) is int else None
        card = (page.get("card") if isinstance(page, dict) else None) or {}
        if card.get("held") != "pending":
            continue
        if not ended:
            unconfirm(host, tid)
            continue
        if (a is None or send is None or not sid or was != sid or not getattr(send, "text", "")
                or getattr(send, "state", None) == "cancelled" or host.stopped() is True):
            mark_undelivered(host, tid)
            continue
        send.state = "pending"
        host.hist_update(tid, end="open", card={**{k: v for k, v in card.items() if k not in HELD_KEEP}, "queued": True})
        host.st.log("shared_held_redelivered", turn=tid)
        again.append(send)
    if again:
        rest = []
        while not a.q.empty():
            rest.append(a.q.get_nowait())
        for x in again + rest:
            a.q.put_nowait(x)
    return len(again)


# ------------------------------------------------------------------ `agentj agent restart` on Agent J's own shared native
def cli_restart(host, a, human=False) -> dict:
    """Only the human at a terminal of the computer (ctl_auth: not an Agent's process tree, has a tty) restarts the shared
    native Agent J started; every other caller keeps the refusal a shared session always had."""
    caller = "human" if human else "agent"
    if getattr(a, "kind", None) != "claude" or (getattr(a, "cfg", None) or {}).get("session_mode") != "shared" or not owned(a):
        host.st.log("agent_restart", by="cli", caller=caller, result="refused", why="foreign")
        return {"ok": False, "when": "own", "error": "shared"}
    if not human:
        host.st.log("agent_restart", by="cli", caller=caller, result="refused", why="not_human")
        return {"ok": False, "when": "own", "error": "not_human"}
    if host.stopped():
        host.st.log("agent_restart", by="cli", caller=caller, result="refused", why="estop")
        return {"ok": False, "when": "own", "error": "estop"}
    if host.__dict__.get("_cli_restart"):
        return {"ok": True, "when": "now", "shared": True}
    busy = a.state.status in ("working", "compacting") or a.native_turn

    async def go():
        try:
            end = time.monotonic() + CLI_RESTART_WAIT
            while busy and getattr(host, "agent", None) is a and (a.state.status in ("working", "compacting") or a.native_turn):
                if time.monotonic() >= end:               # a hook state stuck at working must not hold the owner's restart
                    host.st.log("agent_restart", by="cli", caller=caller, result="wait_timeout", seconds=int(CLI_RESTART_WAIT))
                    break
                await asyncio.sleep(.5)                   # the running turn is not interrupted (as for an owned harness)
            if getattr(host, "agent", None) is not a or not owned(a) or host.stopped():
                return
            from . import phone_restart
            res = await phone_restart.run(host)
            host.st.log("agent_restart", by="cli", caller=caller, result="ok" if res.get("ok") else str(res.get("why"))[:24])
        finally:
            host.__dict__.pop("_cli_restart", None)
    host._cli_restart = True
    host._bg(go(), "agent_restart")
    return {"ok": True, "when": "next_turn" if busy else "now", "shared": True}
