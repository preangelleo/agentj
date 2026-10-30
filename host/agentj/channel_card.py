"""P543 (#308): an owner-channel message (WeChat, mail, Telegram) on the phone = what was sent, not the manifest path.

`notice(host, source, text, files, manifest)` is the one call wechat.py / external_channels.py make once the message is
in the inbox. The phone gets a `sys` history page with `card.channel` {source, text, cut, files, paths}: the words as the
owner sent them (credentials redacted by privacy.owner_text, the same filter the owner's own channels use), the attached
files as §13 media (serve.channel_notice scans them like a reply's files; the JSON manifest is never one of them) and the
inbox paths only behind a folded 「…」. Older phones (no `card.channel`) and §8 devices read the page's reply text:
「来自微信：<the first words>」 — never the path. A host without `channel_notice` (a unit test's stand-in) gets that
same text as a plain `agent_notice`.
"""
from __future__ import annotations

import os

from . import privacy

SOURCES = ("wechat", "mail", "telegram")
LABEL = {"wechat": ("来自微信", "From WeChat"), "mail": ("来自邮件", "From email"),
         "telegram": ("来自 Telegram", "From Telegram")}
REPLIED = {"wechat": ("微信分身已回复", "WeChat avatar replied"), "mail": ("邮件分身已回复", "Email avatar replied"),
           "telegram": ("Telegram 分身已回复", "Telegram avatar replied")}
TEXT_MAX = 4000          # the card's words (the full text stays in the inbox manifest the Agent reads)
LEAD_MAX = 200           # what an older phone's notice quotes
INERT = str.maketrans({"[": "［", "]": "］", "<": "‹", ">": "›", "`": "'"})
FILES_MAX = 10           # WeChat carries at most 10 items; mail / Telegram are capped the same on the card


def label(source: str, lang: str) -> str:
    zh, en = LABEL[source]
    return en if lang == "en" else zh


def _attachment_only(n: int, lang: str) -> str:
    return (f"{n} attachment" + ("s" if n != 1 else "")) if lang == "en" else f"附件 {n} 个"


def build(source: str, text: str, files, manifest: str, lang: str = "zh") -> tuple[str, dict, list[str]]:
    """(old-client text, card, attachment paths). `files` = the attachments as placed in the inbox; the manifest is
    dropped from them whatever position it came in."""
    if source not in SOURCES:
        raise ValueError("channel_source")
    body = privacy.owner_text(text if isinstance(text, str) else "").strip()
    files = [p for p in (files or ()) if isinstance(p, str) and p and p != manifest][:FILES_MAX]
    head = label(source, lang)
    if body:
        lead = body[:LEAD_MAX].rstrip() + ("…" if len(body) > LEAD_MAX else "")
        lead = lead.translate(INERT)            # older phones render this as Markdown: outside text makes no links
        fallback = head + ("：" if lang != "en" else ": ") + lead
    elif files:
        fallback = head + ("：" if lang != "en" else ": ") + _attachment_only(len(files), lang)
    else:
        fallback = head
    card = {"channel": {"source": source, "label": head, "text": body[:TEXT_MAX], "cut": len(body) > TEXT_MAX,
                        "files": [os.path.basename(p)[:128] for p in files],
                        "paths": [p for p in [manifest, *files] if p]}}
    return fallback, card, files


def media_refs(files) -> str:
    """The attachments as explicit Markdown links: what media.Media.scan reads to make §13 items of them."""
    return "\n".join("[" + os.path.basename(p).replace("[", "(").replace("]", ")") + "](<" + p + ">)" for p in files
                     if "<" not in p and ">" not in p and "\n" not in p)


def notice(host, source: str, text: str, files, manifest: str) -> None:
    lang = "en" if getattr(host, "lang", "zh") == "en" else "zh"
    fallback, card, files = build(source, text, files, manifest, lang)
    fn = getattr(host, "channel_notice", None)
    if callable(fn):
        fn(fallback, card, files)
    else:
        host.agent_notice(fallback)


def attach(host, page, files) -> None:
    """P557: files / images an avatar answer carried → §13 media of its Web App page (serve.channel_media)."""
    fn = getattr(host, "channel_media", None)
    if callable(fn) and files and isinstance(page, dict) and type(page.get("id")) is int:
        fn(page, list(files))


def reply(host, source: str, text: str, files=()):
    """P557: an owner-channel avatar answered in its channel → the same answer on the Web App's main conversation, as a
    `sys` page under the 「微信分身已回复」 line, with what it attached. → the page (or None)."""
    if source not in SOURCES:
        raise ValueError("channel_source")
    head = REPLIED[source][1 if getattr(host, "lang", "zh") == "en" else 0]
    page = host.hist_add({"k": "sys", "via": source, "text": head}, privacy.owner_text(text or ""), "done")
    attach(host, page, files)
    return page
