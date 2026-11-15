"""0332: native acceptance timeout shares the existing owner queue card.

The host retains a pending delivery marker for every admitted source. Presentation
uses existing provenance.owner_intent; background input never emits a queue notice.
"""
from __future__ import annotations


def note(host, text: str, detail: str = "") -> int | None:
    tid = getattr(host, "cur_turn", None)
    page = host.hist.get(tid) if type(tid) is int else None
    if not isinstance(page, dict) or page.get("end") != "open":
        return None
    # The marker is delivery bookkeeping, never an authorization fact.
    host.turn_shown = True
    host.wait_note = None
    old = page.get("card") or {}
    card = {k: v for k, v in old.items() if k not in ("held", "detail", "queued", "waited_s", "stuck")}
    card["held"] = "pending"
    card["queued"] = True
    host.hist_update(tid, text="", card=card)
    return tid


def delivered(host, tid: int | None) -> None:
    page = host.hist.get(tid) if type(tid) is int else None
    card = (page.get("card") if isinstance(page, dict) else None) or {}
    if card.get("held") != "pending":
        return
    rest = {k: v for k, v in card.items() if k not in ("held", "detail", "queued", "waited_s", "stuck")}
    heal = getattr(host, "turn_heal", None)
    if heal is not None:
        heal.pages.discard(tid)
    host.hist_update(tid, card=rest)
    host.st.log("shared_held_delivered", turn=tid)
