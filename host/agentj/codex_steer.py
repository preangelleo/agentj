"""0.26.9 (#471, ADR-0269-w2-steer): the owner's message during a running Codex turn goes INTO that turn (`turn/steer`).

Codex app-server's `turn/steer {threadId, input, expectedTurnId}` → `{turnId}` adds user input to the active turn (measured in
the shipped binaries 0.153.4 … 0.162.1: method, `TurnSteerParams` and the errors 「no active turn to steer」 / 「cannot steer a
review turn」 / 「cannot steer a compact turn」 / 「expected active turn id … but found …」). Not every server has it, so it is
probed by use: method-not-found (or an experimental gate) = unsupported, cached per app-server process; any other refusal
(the turn just ended, a review / compaction turn, another turn id) costs only this message's steer. Whatever definitely did
not go in is queued exactly as before (the wave-1 queued notice with waited minutes) — a message is never dropped. A steer
written but unanswered (timeout, link broken mid-way) is `uncertain` (P33-X08, security-r2 N-4): never re-sent, the owner
is told it may or may not have landed.

Only the owner's own phone/terminal message, into a turn the owner's own phone/terminal message started (same trust, never
a stranger's words into an owner turn nor the reverse); never a scheduled run, a research thread, a review / compaction /
rate-retry turn, a voice note still being transcribed, a native slash command, a message behind an earlier queued one, or a
shared session still waiting behind the desktop's turn. Claude Code / OpenCode are untouched (they never reach here).
"""
from __future__ import annotations

import asyncio
import contextlib
import time

STEER_WAIT = 15.0           # s for the app-server's answer; a timeout = uncertain (never re-sent, N-4)
_UNSUPPORTED = ("method not found", "unknown method", "unknown variant", "requires experimentalapi")
NOT_RUNNING = "codex app-server is not running"      # agent_codex._send: raised before anything is written
UNCERTAIN = "uncertain"
_HOST_SENDS = ("heartbeat_key", "bot_handoff_id", "official_notice_id", "quota_resume_id", "auto_notify", "hook_notice",
               "background_done_ids", "completed")


def unsupported(err) -> bool:
    """The server does not know `turn/steer` (JSON-RPC -32601, serde's unknown variant, or an experimental-only gate)."""
    e = getattr(err, "err", None) or {}
    msg = str(e.get("message") or "").lower()
    return e.get("code") == -32601 or any(k in msg for k in _UNSUPPORTED)


def _owner(src) -> bool:
    from . import provenance
    try:
        return provenance.is_owner(src)
    except Exception:  # noqa: BLE001 — unknown = not the owner
        return False


def eligible(a, send) -> str | None:
    """None = may steer now; else why not (a fixed word for host.log). Pure reads, no I/O."""
    from . import slash
    if getattr(send, "sid", None) is None or any(getattr(send, k, None) for k in _HOST_SENDS):
        return "not_say"
    if not a.persist or a.research or a.cfg.get("_workflow_ceo") or a.collect is not None:
        return "not_main"
    if not _owner(getattr(send, "source", None)):
        return "not_owner"
    cur = getattr(a, "cur_send", None)
    if cur is None or not _owner(getattr(cur, "source", None)):
        return "turn_not_owner"
    text = getattr(send, "text", "")
    if getattr(send, "prep", None) is not None or not isinstance(text, str) or not text.strip() or slash.parse(text):
        return "not_plain"
    if getattr(send, "state", None) != "pending":
        return "state"
    p, td = a.proc, a.turn_done
    if (a.status != "working" or td is None or td.is_set() or not isinstance(a.turn_id, str) or not a.tid or p is None
            or getattr(p, "returncode", 0) is not None or a.turn_proc is not p or a.halting or a.quiet
            or getattr(a, "turn_review", False) or getattr(a, "rate_retried", False) or getattr(a, "desktop_wait", False)):
        return "no_turn"
    if not a.q.empty() or getattr(a, "lock_cmd", None) is not None:
        return "behind_queue"
    if getattr(a, "steer_off", None) == getattr(p, "pid", None):
        return "unsupported"
    return None


async def steer(a, send) -> bool:
    """True = the message went into the running turn (delivered); UNCERTAIN = written but unanswered (never re-sent);
    False = the caller queues it (a definite refusal / unknown method, or nothing was written). Never raises."""
    if eligible(a, send) is not None:
        return False
    p, tid, turn_id = a.proc, a.tid, a.turn_id
    result = "ok"
    try:
        async with send.lock:
            guard = getattr(send, "delivery_allowed", None)
            if send.state != "pending" or (callable(guard) and not guard()):
                return False
            send.state = "delivering"
            try:
                await a.call("turn/steer", {"threadId": tid, "expectedTurnId": turn_id,
                                            "input": [{"type": "text", "text": send.text, "text_elements": []}]}, STEER_WAIT)
            except Exception as e:  # noqa: BLE001
                from .agent_codex import RPCError
                if isinstance(e, RPCError) or (isinstance(e, ConnectionError) and str(e) == NOT_RUNNING):
                    send.state = "pending"            # a definite no (refusal / unknown method) or nothing was written
                    if isinstance(e, RPCError) and unsupported(e):
                        a.steer_off, result = getattr(p, "pid", None), "unsupported"
                    else:
                        result = "refused" if isinstance(e, RPCError) else "gone"
                    return False
                # security-r2 N-4 / P33-X08: written, no answer (timeout, the link broke mid-way) — maybe in the turn:
                # never re-sent; the owner is told it may or may not have landed
                send.state = "uncertain"
                result = "timeout" if isinstance(e, asyncio.TimeoutError) else "uncertain"
                return UNCERTAIN
            send.state, send.steered = "delivered", turn_id
            a.said, a.last_note = False, time.monotonic()     # P282's quiet tail waits for the reply to this input too
            return True
    finally:
        with contextlib.suppress(Exception):
            a.host.st.log("codex_steer", agent=a.kind, result=result, version=getattr(a, "version", "") or None)


async def steer_or_queue(a, send, enqueue) -> None:
    """One at a time, in arrival order: a later message never overtakes one that fell back into the queue."""
    lock = a.__dict__.setdefault("steer_lock", asyncio.Lock())
    try:
        async with lock:
            ok = False
            try:
                ok = await steer(a, send)
            finally:
                if not ok:
                    enqueue(send)
            if ok is UNCERTAIN:
                for fn, kw in (("say_uncertain", {}), ("msg_steered", {"uncertain": True})):
                    with contextlib.suppress(Exception):
                        f = getattr(a.host, fn, None)
                        f and f(send, **kw)
            elif ok:
                for fn in ("say_delivered", "msg_steered"):
                    with contextlib.suppress(Exception):
                        f = getattr(a.host, fn, None)
                        f and f(send)
                a._heal_delivered()
    finally:
        a.steer_n = max(0, getattr(a, "steer_n", 1) - 1)


async def after_steers(a, item, enqueue) -> None:
    """Anything submitted while a steer is in flight waits for it, so the queue keeps arrival order."""
    async with a.__dict__.setdefault("steer_lock", asyncio.Lock()):
        enqueue(item)
