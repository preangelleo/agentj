"""P424 #75 (owner 10-17: 「既没有 input，怎么会产生 output？……我怎么会发了一个空消息呢？」): a phone message the shared Claude Code
did not take in time. The reason goes on that message's own page — the owner's words right above it, never a `sys` page of
its own that looks like an empty message; the settings diagnosis (layer, crossSessionInbound value) is card.detail, behind the
phone's 「详情」. card.held = "pending" until the message shows up after all (a prompt hook, a transcript record, a queued
attachment — shared.SharedClaudeAgent._held_seen), then one line saying it arrived (card.held = "delivered"; the phone adds its 「已送达」 mark); its reply comes the
usual way, as the Agent's next page. Older phones still hear the notice (the legacy `msg` stream)."""
from __future__ import annotations

from .text import one, pick


def note(host, text: str, detail: str = "") -> int | None:
    """Write the notice onto the running phone page. Returns its id; None = no open page (the caller sends an ordinary
    notice instead)."""
    from .system_text import remote
    tid = getattr(host, "cur_turn", None)
    page = host.hist.get(tid) if type(tid) is int else None
    if not isinstance(page, dict) or page.get("end") != "open" or not text:
        return None
    lang = getattr(host, "lang", "zh")
    text = remote(one(lang, text))
    host.turn_shown = True
    if hasattr(host, "emit"):
        host.emit("agent_notice", text=text)
    if hasattr(host, "_remember"):              # an older phone still hears it as a notice (the legacy `msg` stream)
        e = host._remember("notice", text)
        host._post(host._send_legacy, lambda s, e=e: host._render(e, s))
    host.wait_note = None                       # a wait line never comes back over it (serve.turn_wait_note)
    old = page.get("card") or {}
    card = {**({"progress": old["progress"]} if isinstance(old.get("progress"), str) else {}), "held": "pending"}
    if detail and detail.strip():
        card["detail"] = remote(one(lang, detail.strip()))[:2000]
    host.hist_update(tid, text=text, card=card)
    return tid


def delivered(host, tid: int | None) -> None:
    """That message reached Claude Code after all: the notice and its 「详情」 give way to one line saying so (the phone marks it 「已送达」)."""
    page = host.hist.get(tid) if type(tid) is int else None
    card = (page.get("card") if isinstance(page, dict) else None) or {}
    if card.get("held") != "pending":
        return
    rest = {k: v for k, v in card.items() if k not in ("held", "detail")}
    host.hist_update(tid, text=pick(getattr(host, "lang", "zh"),
                                    "Agent J 主机上的 Claude Code 后来接收了这条消息，回复会接着出现。",
                                    "Claude Code on the Agent J host took this message after all; its reply follows."),
                     card={**rest, "held": "delivered"})
    host.st.log("shared_held_delivered", turn=tid)
