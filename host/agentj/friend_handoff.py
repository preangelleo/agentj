"""ADR-0306-friend-handoff-mail: the 「交给主 Agent 来办」 turn may mail a third party behind the owner's UV card.

serve._friend_main mints ONE host-held record per handoff, keyed by the F3 fence nonce and bound to that exact Send object.
`agentj mail send` (contacts_serve.agent_send) asks `current` while that Send is the running turn: a record → the owner-only
pass or a UV card that names the friend; a friend_task Send carrying a nonce that is not live for it (stale, replayed onto
another Send, forged, friend gone / blocked) → refused, no card. Turn text and request fields are never read here.
"""
from __future__ import annotations
import copy
import json
import re
import time

TTL = 3600                 # seconds a handoff stays usable (it may queue behind a busy turn first)
KEEP = 64
_FENCE = re.compile(r"〔.*· ([0-9a-f]{12})〕")


def mint(host, send, agent_text: str) -> str | None:
    """`agent_text` is PeerService.main_text (host-built: head, reply line, fence, ONE peer_guard.wrap JSON line, fence)."""
    try:
        lines = agent_text.split("\n")
        nonce = _FENCE.fullmatch(lines[2]).group(1)
        if _FENCE.fullmatch(lines[4]).group(1) != nonce:
            return None
        who = json.loads(lines[3])["from_friend"]
        fid, name = str(who["id"]), str(who.get("name") or "")
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return None                     # no record: the turn mails exactly as any friend_task turn (no card)
    book = host.__dict__.setdefault("_friend_handoffs", {})
    book[nonce] = {"send": send, "fid": fid, "name": "" if name == fid else name, "until": time.monotonic() + TTL,
                   "origin": copy.deepcopy(getattr(send, "approval_origin", None))}   # the owner's tap (serve._friend_main)
    while len(book) > KEEP:
        book.pop(next(iter(book)))
    send.friend_handoff = nonce
    return nonce


def current(host, agent, peers=None):
    """None = not a handoff turn (unchanged paths); False = a handoff marker that is not live → refuse; else the record.
    Blocking (friends.json read): call through asyncio.to_thread."""
    from . import provenance
    send = getattr(agent, "cur_send", None)
    nonce = getattr(send, "friend_handoff", None)
    if nonce is None:
        return None
    if (provenance._paired_source() is not provenance._UNSET or provenance._REQUEST.get() is not provenance._UNSET
            or provenance.task_scope() is not None or provenance._shared_child(agent) is not provenance._UNSET
            or provenance.normalize(getattr(send, "source", None)) != {"kind": "friend_task", "role": "task"}):
        return False                    # another request scope around the turn never borrows the handoff
    row = host.__dict__.get("_friend_handoffs", {}).get(nonce) if isinstance(nonce, str) else None
    if row is None or row["send"] is not send or time.monotonic() >= row["until"]:
        return False
    store = getattr(peers, "store", None)
    try:
        if store is None or store.get(row["fid"]) is None or store.is_blocked(row["fid"]):
            return False
    except Exception:  # noqa: BLE001 — an unreadable friend list is not a live friend
        return False
    return row


def line(row: dict, zh: bool) -> str:
    """The card's first summary line: a friend handoff, by display name (never the AJ-… id)."""
    if zh:
        return f"来源：好友「{row['name']}」的消息，主人点了「交给主 Agent 来办」" if row["name"] else \
            "来源：一位好友的消息，主人点了「交给主 Agent 来办」"
    return (f'From: a message from friend "{row["name"]}" that the owner handed to the main Agent' if row["name"]
            else "From: a friend's message that the owner handed to the main Agent")


def front(row: dict) -> dict:
    """The card's reason line (approval-layout0281.stepReason reads envelope reason / reason_en)."""
    return {"reason": line(row, True), "reason_en": line(row, False)}


def origin(row: dict, zh: bool) -> dict | None:
    """The card's origin block: the owner's tap that minted this record (root verified) carrying the friend's words as
    quoted external material (suspicious stays True — ADR-541 quoted-material wording, security r2 0306). Built
    from the record only — the turn the host wrote when the paired phone's signed tap arrived; None → unchanged snapshot."""
    base = row.get("origin")
    if not isinstance(base, dict) or type(base.get("message_id")) is not int:
        return None
    name = row["name"]
    text = (f"交给主 Agent 来办：好友「{name}」的消息" if name else "交给主 Agent 来办：一位好友的消息") if zh else \
        (f'Handed to the main Agent: a message from friend "{name}"' if name else "Handed to the main Agent: a friend's message")
    out = copy.deepcopy(base)
    out.update(channel="phone", text=text, sender="", verified=True, suspicious=True, trigger_message_id=base["message_id"],
               root={"message_id": base["message_id"], "session": base.get("session"), "channel": "phone",
                     "text": "交给主 Agent 来办" if zh else "Handed to the main Agent", "verified": True})
    return out


REFUSED = {"ok": False, "why": "friend_handoff_unverified",
           "detail": "这次「交给主 Agent 来办」已失效（回合已结束、超时、好友已移除或不是主人点的转交）；没有发卡片，邮件没有发出。"
                     "请主人在 Agent J 网页里直接说 / This 'Hand it to the main Agent' handoff is no longer live (the turn "
                     "ended, it expired, the friend was removed, or the owner did not hand it over); no card was sent and "
                     "nothing was mailed. Ask the owner to say it directly in the Agent J Web App."}
