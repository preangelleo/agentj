"""Owner-only delivery: every recipient must be an owner address or paired private channel.
Contact details pass; credential values become bounded Face ID pickup cards, never mail/Telegram payloads.
prepare reserves placeholders; deliver opens cards only after sending. Failed cards get a follow-up notice.
Strict outsider filters remain authoritative. See ARCHITECTURE P194/P211/P573."""
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
    """Prepare bounded pickup placeholders and (kind, value) pairs for the owner channel; excess stays withheld."""
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


FAILED_NOTE = {"zh": "刚才那封发给你自己的邮件里，有 {n} 个密钥的领取卡没能弹出，邮件里写的「已弹出领取卡」不准；密钥没有随邮件发出。"
                     "要拿到它，在已配对的手机上让 Agent 用密钥领取卡发给你。",
               "en": "In the mail you just got from yourself, {n} secret pickup card(s) did not open, so 「a card is waiting」 there "
                     "is wrong; the key did not go by mail. Ask your Agent on a paired phone to send it as a secret pickup card."}


def failed_note(rep: dict, lang: str) -> str | None:
    n = rep.get("failed")
    return FAILED_NOTE[_lang(lang)].format(n=n) if n else None


class Pending(list):
    """P573: the (kind, value) pairs `prepare` set aside for cards; `found` = every credential the text held."""
    found = 0


def prepare(host, text: str, via: str, lang: str) -> tuple[str, Pending]:
    """Reserve mail placeholders and transient values; open no cards until deliver after send authorization."""
    out, values = plan(text, via, lang, can_card(host))
    pending = Pending(values)
    pending.found = len(privacy.credentials(text or ""))
    return out, pending


async def deliver(host, values: list[tuple[str, str]], via: str, lang: str, label: str) -> dict:
    """P573: the confirmed mail's pickup cards → {cards, withheld}. Counts only in the log."""
    opened = await offer(host, values, label, lang) if values else 0
    found = max(getattr(values, "found", 0), len(values))
    rep = {"cards": opened, "withheld": found - opened}
    if len(values) > opened:      # the mail said 「card waiting」 for these and it did not open: say so (FAILED_NOTE)
        rep["failed"] = len(values) - opened
    if found:
        with contextlib.suppress(Exception):
            host.st.log("owner_divert", via=via, **rep)
    return rep


async def divert(host, text: str, via: str, lang: str, label: str) -> tuple[str, dict]:
    """Offer cards before rendering placeholders for channels without mail confirmation; return text and counts."""
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


# ---------------------------------------------------------------- P211: files toward the owner
CARD_REPEAT = 600.0               # s: the same file (by SHA-256) named again — by the page AND by Telegram — opens one card


def expect_files(host, keys: list) -> None:
    """Mark eligible credential-file skips key_card before mail confirmation, without opening cards."""
    if not keys or not can_card(host):
        return
    for skip, rec in [k for k in keys if k[0] is not None and "values" not in k[1]][:MAX_CARDS]:
        if rec.get("sha256"):
            skip["why"] = "key_card"


async def offer_files(host, keys: list, lang: str, limit: int = MAX_CARDS) -> int:
    """Offer bounded pickup cards for whole credential files or transient values from masked copies.
    Update successful skips to key_card; dedupe by hash. Never log names/values; always discard transient values."""
    if not keys:
        return 0
    import base64
    import hashlib
    import time
    from . import media as media_mod
    from . import tg_media
    lang = _lang(lang)
    box = getattr(getattr(host, "elevate", None), "out", None)
    if box is None or not can_card(host):
        for _skip, rec in keys:
            rec.pop("values", None)
        return 0
    seen = getattr(host, "_p211_cards", None)
    if not isinstance(seen, dict):
        seen = {}
        with contextlib.suppress(Exception):
            host._p211_cards = seen
    now = time.monotonic()
    for k in [k for k, t in seen.items() if now - t >= CARD_REPEAT]:
        seen.pop(k, None)
    done = found = 0
    for skip, rec in keys:
        values = rec.pop("values", None)
        if values is not None:                       # P573: a masked file — one card per value
            label = _label(str(rec.get("name") or ""), lang)
            for n, (kind, value) in enumerate(values, 1):
                found += 1
                tag = "v:" + hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()
                if tag in seen:
                    done += 1
                    continue
                if done >= limit:
                    continue
                if await _one(box, n, kind, value, label, lang):
                    seen[tag] = now
                    done += 1
            values = None
            continue
        found += 1
        if rec.get("sha256") in seen:
            skip["why"] = "key_card"
            done += 1
            continue
        if done >= limit:
            continue
        try:
            data = tg_media.read_checked(rec)
        except (media_mod.Gone, OSError):
            continue
        name = str(rec.get("name") or "file").replace("\n", " ")
        purpose = ("凭据文件不随消息发出，改在这里领取" if lang == "zh" else "credential files never go as attachments; pick it up here")
        try:
            res = await box.send({"name": name[:80], "purpose": purpose, "kind": "file", "filename": name[:128],
                                  "data": base64.b64encode(data).decode()})
        except Exception:   # noqa: BLE001 — a failed card leaves the note that says how to ask for one
            continue
        finally:
            data = b""
        if res.get("result") == "sent":
            seen[rec["sha256"]] = now
            skip["why"] = "key_card"
            done += 1
    with contextlib.suppress(Exception):
        host.st.log("owner_key_files", count=found, keys=done)
    return done
