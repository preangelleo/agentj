"""P194 (Leo, 10-13): owner-directed outbound — one definition, judged in code by the destination, never by the model.

Owner-directed = the destination is the owner's OWN channel:
- a paired remote of this seat (phone / web, inside the Noise session — P167 already);
- the owner bot's private chat with the owner (Telegram `owner_id`, not a group, not a forwarded member);
- an e-mail whose recipients are ALL the owner's own addresses: slot 0 of the address book (contacts.OWNER_ID) and the e-mail
  channel's allowlist rows marked 「我本人」 (P170). One other recipient makes the whole mail outward-facing.

What owner-directed text gets: `privacy.owner_text` — e-mail, phone numbers, links, access codes, one-time codes, a report's
authorization code, paths and private IPs as written; no Jev layer, no masking of contact details.

The floor that stays (Jarvis, 10-13): a real credential (API key, private key, password, token, seed words — what
`privacy.credentials` finds) never rides in mail or Telegram, even to the owner — both are third-party stores with a wide
leak surface. It is taken out of the text and offered as an F32 secret pickup card on the owner's paired devices
(`secret_out.Outbox`, Face ID to open), with a one-line placeholder where it stood. No device able to receive the card →
the placeholder says it was withheld. The value itself is never logged; logs carry counts only.

Everything else — friends, groups, customer-service bots, mail with any non-owner recipient, public web reports — keeps the
strict filters it had (privacy.redact, group_filter, Jev where it already runs).
"""
from __future__ import annotations

import contextlib

from . import privacy

MAX_CARDS = 3                     # secret cards one message may open; the rest are withheld (secret_out.MAX_OPEN is 8)
VIA = {"zh": {"mail": "邮件", "telegram": "Telegram"}, "en": {"mail": "e-mail", "telegram": "Telegram"}}
KIND = {"zh": {"mnemonic": "助记词", "private_key": "私钥", "credential_assignment": "密码/密钥", "high_entropy": "疑似密钥"},
        "en": {"mnemonic": "seed words", "private_key": "private key", "credential_assignment": "password / key",
               "high_entropy": "likely a key"}}
PLACE = {
    "zh": {"card": "〔密钥 {n}（{kind}）没有随{via}发出：已在你的手机上弹出密钥领取卡，验证 Face ID 后领取〕",
           "held": "〔密钥 {n}（{kind}）没有随{via}发出，已遮住。要拿到它，请在已配对的手机上让 Agent 用密钥领取卡发给你〕"},
    "en": {"card": "[Key {n} ({kind}) was not sent by {via}: a secret pickup card is waiting on your phone (Face ID to open)]",
           "held": "[Key {n} ({kind}) was not sent by {via} and is hidden. Ask your Agent on a paired phone to send it as a "
                   "secret pickup card]"},
}


def _lang(lang) -> str:
    return "en" if lang == "en" else "zh"


def owner_emails(st) -> list[str]:
    from . import contacts
    return contacts.owner_emails(st)


def all_owner(st, addrs) -> bool:
    """True only when there is at least one recipient and every one is an owner address."""
    addrs = [str(a).strip().lower() for a in addrs or [] if str(a).strip()]
    if not addrs:
        return False
    mine = set(owner_emails(st))
    return all(a in mine for a in addrs)


def kind_word(kind: str, lang: str) -> str:
    lang = _lang(lang)
    return KIND[lang].get(kind) or kind.replace("_", " ")


def can_card(host) -> bool:
    """Is there a paired, approved device that can be offered a secret card right now?"""
    with contextlib.suppress(Exception):
        if host.stopped():
            return False
        return bool(host._approvers()) and getattr(getattr(host, "elevate", None), "out", None) is not None
    return False


def _assemble(text: str, found, opened: set, via: str, lang: str) -> str:
    out, at = [], 0
    for n, (kind, _value, s, e) in enumerate(found, 1):
        out.append(text[at:s])
        out.append(PLACE[lang]["card" if n in opened else "held"].format(n=n, kind=kind_word(kind, lang),
                                                                         via=VIA[lang].get(via, via)))
        at = e
    out.append(text[at:])
    return privacy.owner_text("".join(out))


def plan(text: str, via: str, lang: str, cards: bool) -> tuple[str, list[tuple[str, str]]]:
    """Synchronous form (Telegram's send queue): → (the text for the owner's channel, [(kind, value)] to offer as cards).
    The first MAX_CARDS credentials get the 「card」 placeholder when `cards`; the rest are withheld. Never raises."""
    lang = _lang(lang)
    text = text or ""
    found = privacy.credentials(text)
    opened = set(range(1, min(len(found), MAX_CARDS) + 1)) if cards else set()
    return _assemble(text, found, opened, via, lang), [(k, v) for n, (k, v, _s, _e) in enumerate(found, 1) if n in opened]


async def _one(box, n: int, kind: str, value: str, label: str, lang: str) -> bool:
    name = (f"{label} · 密钥 {n}" if lang == "zh" else f"{label} · key {n}")[:80]
    purpose = (f"{kind_word(kind, lang)}；邮件 / Telegram 不发密钥，改在这里领取" if lang == "zh"
               else f"{kind_word(kind, lang)}; keys never go by e-mail / Telegram, pick it up here")[:300]
    try:
        res = await box.send({"name": name, "purpose": purpose, "kind": "text", "value": value})
    except Exception:   # noqa: BLE001 — a failed card never stops the message; its placeholder then says withheld
        return False
    return res.get("result") == "sent"


def _label(label: str, lang: str) -> str:
    return " ".join(str(label or "").split())[:40] or ("消息" if lang == "zh" else "message")


async def offer(host, values: list[tuple[str, str]], label: str, lang: str) -> int:
    """Open one secret pickup card per value on the owner's paired devices. → how many opened."""
    lang = _lang(lang)
    box = getattr(getattr(host, "elevate", None), "out", None)
    if box is None:
        return 0
    return sum([await _one(box, n, k, v, _label(label, lang), lang) for n, (k, v) in enumerate(values, 1)])


async def divert(host, text: str, via: str, lang: str, label: str) -> tuple[str, dict]:
    """Mail: offer the cards first, then write each placeholder by what really happened. → (text, {cards, withheld})."""
    lang = _lang(lang)
    text = text or ""
    found = privacy.credentials(text)
    opened: set = set()
    box = getattr(getattr(host, "elevate", None), "out", None)
    if found and box is not None and can_card(host):
        for n, (kind, value, _s, _e) in enumerate(found[:MAX_CARDS], 1):
            if await _one(box, n, kind, value, _label(label, lang), lang):
                opened.add(n)
    rep = {"cards": len(opened), "withheld": len(found) - len(opened)}
    if found:
        with contextlib.suppress(Exception):
            host.st.log("owner_divert", via=via, **rep)
    return _assemble(text, found, opened, via, lang), rep
