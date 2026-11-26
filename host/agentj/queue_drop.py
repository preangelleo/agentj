"""0378 (PROTOCOL §10.2b): the owner removes waiting messages from the queue — one by one or all of them.

`{"t":"queue_drop","turns":[page id,…]}` / `{"t":"queue_drop","all":true}` from a paired p33 phone. A page is droppable
while nothing of it runs natively: its Send has not been handed to the harness (cancelled under the Send's own lock, as
`say_cancel` does) or its command has not been typed (`cmd_cancel`'s own rule). A P424 `held: "pending"` page — already
written to the native — leaves the queue as `card.unconfirmed` (it may still run). The running turn, delivered pages and
every host-made send (heartbeat, resume, background-done, hook) are never touched. A dropped page: end "stopped",
card.dropped true, for every remote and for good.
"""
from __future__ import annotations


CAP = "queuedrop1"              # `ready.caps`: this host answers queue_drop (the phone shows its remove controls)
MAX_TURNS = 64
KEEP_OUT = ("held", "detail", "queued", "waited_s", "stuck")


OWNER_KINDS = ("phone", "terminal", "wechat", "telegram", "email")      # web phone-activity.js ownerInput, exactly
OWNER_PAGES = ("phone", "host", "wechat", "telegram", "email")
HOST_SENDS = ("heartbeat_key", "quota_resume_id", "compact_resume", "auto_notify", "background_done_ids", "hook_notice",
              "bot_handoff_id", "official_notice_id")


def _queued(page) -> bool:
    card = page.get("card") or {}
    return card.get("held") == "pending" or (page.get("end") == "open" and card.get("queued") is True)


def _owner(page) -> bool:
    """The pages the phone's queue lists (web ownerInput), never a host-made one: heartbeat, quota / compact resume,
    background-done, auto-notify and hook sends are `provenance.system()` (role owner) on a `k:"agent"` page with a `via`."""
    src = page.get("src") or {}
    if src.get("via") or src.get("k") not in OWNER_PAGES:
        return False
    if isinstance(src.get("source_kind"), str) or isinstance(src.get("source_role"), str):
        return src.get("source_role") == "owner" and src.get("source_kind") in OWNER_KINDS
    return src.get("k") in ("phone", "host")


async def _drop(host, s, tid, explicit) -> bool:
    page = host.hist.get(tid) if type(tid) is int else None
    a = host.agent
    if not host._paired_live(s) or not isinstance(page, dict) or tid == host.cur_turn:
        return False
    if (page.get("src") or {}).get("k") == "cmd":
        # a waiting /clear · /compact: exactly `cmd_cancel`'s rule (its own device, shared Claude / workflow wait, not typed)
        if not explicit or not host.cancel_waiting_cmd(s, tid):
            return False
        host.hist_update(tid, card={"dropped": True})
        return True
    if not _queued(page) or not _owner(page):
        return False
    card = page.get("card") or {}
    if card.get("held") == "pending":
        # its words are already in the native's hands: off the queue as unconfirmed
        from . import native_advisory
        if a is not None and isinstance(getattr(a, "held", None), list):
            a.held = [x for x in a.held if x[0] != tid]
            getattr(a, "held_sends", {}).pop(tid, None)
        return native_advisory.unconfirm(host, tid)
    send = next((x for x in [*host.sends.items.values(), *(list(a.q._queue) if a is not None else [])]
                 if getattr(x, "sid", None) is not None and getattr(x, "turn", None) == tid
                 and getattr(x, "state", None) in ("pending", "delivering")), None)
    if send is not None:
        if any(getattr(send, k, None) for k in HOST_SENDS):
            return False
        async with send.lock:                       # the lock Agent.deliver holds through the write: exact, like say_cancel
            if not host._paired_live(s) or send.state != "pending" or tid == host.cur_turn:
                return False
            # cancelled + receipt + attachments released; its device hears `why:"dropped"` (not a withdraw: no refill)
            host.queue_dropped(send, why="dropped")
    else:
        from . import upgrade_messages
        if upgrade_messages.pending(host.st):       # a message sealed for the upgrade hand-over is not ours to drop
            return False
    heal = getattr(host, "turn_heal", None)
    if heal is not None:
        heal.pages.discard(tid)
    page = host.hist.get(tid) or page
    host.hist_update(tid, end="stopped", card={**{k: v for k, v in (page.get("card") or {}).items() if k not in KEEP_OUT},
                                                "dropped": True})
    return True


async def on_phone(host, s, obj) -> None:
    turns, every = obj.get("turns"), obj.get("all") is True
    if not host._paired_live(s):
        return
    if every:
        want = [tid for tid, p in list(host.hist.turns.items()) if _queued(p) and _owner(p)]
    elif isinstance(turns, list) and 0 < len(turns) <= MAX_TURNS and all(type(x) is int for x in turns):
        want = list(dict.fromkeys(turns))
    else:
        return
    dropped, skipped = [], []
    for tid in want:
        (dropped if await _drop(host, s, tid, not every) else skipped).append(tid)
    host.st.log("queue_drop", count=len(dropped), by=s.device, **({"all": True} if every else {}))
    if dropped:
        host.status_changed()
    r = obj.get("r")
    from . import wire
    await host.send_app(s, {"t": "queue_dropped", "turns": dropped, "skipped": [] if every else skipped,
                            **({"r": r} if wire.is_rid(r) else {})})
