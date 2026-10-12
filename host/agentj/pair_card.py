"""P153: add a remote from a remote — the approval card every paired remote gets for a waiting new device.

Flow (PROTOCOL §3.3): a ready remote sends a signed `pair_new` → the host opens the one pairing (QR + link go back to that
remote only, inside its Noise session) → the new device completes IKpsk2 and waits → every ready remote that announced
`pair1` gets one `ask` card (tool `pair_device`) → the first signed answer decides (Face ID too when that remote has a
passkey) → the others get `ask_done` and fold it away; the new device gets `approved` and opens the chat.

The 6-digit safety code is no longer typed: it is shown on the new device AND on the card, so the owner compares two
screens. It still binds the card to this one handshake (a stolen QR that wins the race shows a different code).
"""
from __future__ import annotations

import time

from . import onboarding, wire

TOOL = "pair_device"
CAP = "pair1"
CARD_TTL = 300          # s: the waiting device's deadline once a card is out (5 min, like the QR)
PK_KIND = "pair"        # passkey.elevate_challenge kind for this card

TEXT = {
    "zh": {"head": "有新遥控器申请绑定：{name}",
           "kind": {"phone": "手机或平板", "computer": "电脑浏览器", "other": "其他设备"},
           "line": "类型：{kind} · 申请时间：{when}",
           "code": "对照码：{code}（新设备屏幕上应显示同一串数字；不一致就点取消）",
           "replaces": "批准后会替换同一浏览器之前的记录「{name}」。",
           "evict": "遥控器已满 {limit} 台，批准后会解绑最久没用的「{name}」。",
           "unnamed": "未命名设备"},
    "en": {"head": "A new remote asks to pair: {name}",
           "kind": {"phone": "phone or tablet", "computer": "computer browser", "other": "other device"},
           "line": "Type: {kind} · requested at {when}",
           "code": "Match code: {code} (the new device shows the same digits; if not, tap Cancel)",
           "replaces": "Approving replaces this browser's earlier record \"{name}\".",
           "evict": "All {limit} remotes are in use: approving unpairs the least recently used \"{name}\".",
           "unnamed": "unnamed device"},
}


def lang_of(lang: str) -> str:
    return "en" if str(lang or "").lower().startswith("en") else "zh"


def summary(lang: str, name: str, code: str, event: dict, when: float | None = None) -> str:
    """The card text, in ONE language (the host's), exactly what the approving remote signs (approvals.shown_digest)."""
    t = TEXT[lang_of(lang)]
    label = name or t["unnamed"]
    lines = [t["head"].format(name=label),
             t["line"].format(kind=t["kind"][onboarding.kind_of(name)], when=time.strftime("%H:%M", time.localtime(when))),
             t["code"].format(code=f"{code[:3]} {code[3:]}")]
    if event.get("replaces"):
        lines.append(t["replaces"].format(name=event["replaces"].get("name") or t["unnamed"]))
    elif event.get("evict"):
        lines.append(t["evict"].format(limit=event.get("limit", 5), name=event["evict"].get("name") or t["unnamed"]))
    return "\n".join(lines)


def handshake_id(h: bytes) -> str:
    """What approvals.log keeps for the handshake (never the safety code itself)."""
    from .noise import sha256
    return wire.b64u(sha256(b"agentjarvis-pair-card-v1" + h)[:16])
