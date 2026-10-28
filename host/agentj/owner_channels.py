"""P562 (#337, chairman 10-28): the owner's own WeChat, private Telegram chat and main mailbox are answered by the main
conversation itself — no separate avatar. 「董事长只知道一个助理」.

Who counts as the owner stays each adapter's check (provenance.owner_channel): WeChat = the bound ClawBot account's
sender id, private message, not a group / bot (wechat.receive); Telegram = the enrolled owner id in its own private chat
(telegram.incoming); mail = an allowlisted owner address whose SPF / DKIM / DMARC passed (mail.judge → VERIFIED).
Everyone else keeps the old low-trust path unchanged.

Such a message is an ordinary owner message in the main queue (serve._accept: queued behind a busy turn, never a second
conversation), its page labelled with the channel, the answer sent back to the channel. Approvals: every step it asks for
goes through serve.ask as the channel's (`avatar=` — the main floor and cards, never a batch grant / goal pass / owner-only
path); once a card has waited CARD_NOTICE_AFTER s the channel hears 「已转到 Agent J 网页，请在那里确认。」 once per message.
Nothing typed in a channel ever answers a card.

Pictures: a main harness without an image tool (Claude Code, OpenCode) cannot draw; an image request is then drawn by the
owner's Codex (wechat.WeChat.draw, subscription, never a paid service), sent to the channel and shown on the page.
"""
from __future__ import annotations

import asyncio
import contextlib
import re

CARD_NOTICE_AFTER = 1.0
LABEL = {"wechat": ("来自微信", "From WeChat"), "telegram": ("来自 Telegram", "From Telegram"), "mail": ("来自邮件", "From email")}
NAME = {"wechat": ("微信", "WeChat"), "telegram": ("Telegram", "Telegram"), "mail": ("邮件", "Email")}

# an explicit request to make a picture — a verb of making + a picture word (「给我做张图片…」「画一只猫」 "draw me a cat")
_ZH = re.compile(r"(?<![动计规策])画(?![面质风廊展家报册])"
                 r"|(?:生成|做|制作|创作|设计|弄|出|来)(?:一|两|几)?(?:张|幅|个|副)?[^\n。！？!?]{0,40}?"
                 r"(?:图片|图像|插画|插图|照片|相片|海报|头像|壁纸|漫画|logo|LOGO|(?<![地流结表程意构])图(?![表标纸书层]))")
_EN = re.compile(r"(?i)\b(?:draw|paint|sketch|illustrate|(?:generate|create|make|design|render)\s+(?:me\s+)?(?:an?\s+|the\s+|some\s+|\d+\s+)?"
                 r"(?:\w+\s+){0,3}?(?:image|picture|photo|illustration|poster|logo|drawing|painting|wallpaper|avatar)s?)\b")
_NOT = re.compile(r"(?:流程图|架构图|示意图|思维导图|导图|图表|表格|发给我|发我|找|搜|看看|识别|这张图|图里|截图|send me|find|search|what is in|look at)", re.I)


def label(via: str, lang: str) -> str:
    zh, en = LABEL[via]
    return en if lang == "en" else zh


def name(via: str, lang: str) -> str:
    zh, en = NAME[via]
    return en if lang == "en" else zh


def lang_of(host) -> str:
    return "en" if getattr(host, "lang", "zh") == "en" else "zh"


def wants_image(text: str) -> bool:
    """A request to make a new picture (not to send, find or read one)."""
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        return False
    words = text.strip()
    return bool((_ZH.search(words) or _EN.search(words)) and not _NOT.search(words))


def main_draws(host) -> bool:
    """Can the main conversation deliver a picture to a channel itself? Only Codex has its own image tool."""
    return getattr(getattr(host, "agent", None), "kind", None) == "codex"


async def ask(host, via: str, call) -> dict:
    """serve.ask for a step of an owner-channel turn: the same request as the channel's (`avatar=` label); a card still open
    after CARD_NOTICE_AFTER s → the Send's `on_card` (the channel's one line where to confirm), once per message."""
    send = getattr(getattr(host, "agent", None), "cur_send", None)
    before = set(getattr(host, "asks", None) or ())
    task = asyncio.ensure_future(call(label(via, lang_of(host))))
    try:
        done, _ = await asyncio.wait({task}, timeout=CARD_NOTICE_AFTER)
        notify = getattr(send, "on_card", None)
        carded = bool(set(getattr(host, "asks", None) or ()) - before)      # review S3: only when a card really waits
        if not done and carded and callable(notify) and not getattr(send, "card_told", False):
            send.card_told = True
            with contextlib.suppress(Exception):
                r = notify()
                if asyncio.iscoroutine(r):
                    await r
        return await task
    except asyncio.CancelledError:
        task.cancel()
        raise


def confirm_text(host) -> str:
    return "已转到 Agent J 网页，请在那里确认。" if lang_of(host) != "en" else "Sent to Agent J Web App. Please confirm there."


def not_running_text(host) -> str:
    return ("Agent J 主对话现在没有在运行（或已全部停止），这条消息已留在 Agent J 网页。恢复后再发一次。" if lang_of(host) != "en" else
            "The Agent J main conversation is not running (or everything is stopped); this message is on Agent J Web App. "
            "Send it again once it is back.")


REFUSED_ALTERNATIVE = ("可以换个说法再发：比如不要写实风格、不用真人姓名，或者发一张本人照片作参考。",
                       "You can send it again another way: a non-photorealistic style, no real person's name, or your own photo as a reference.")


def draw_refused_text(why_words: str, lang: str) -> str:
    """The drawer answered without a picture (its own reason, e.g. a real person's likeness): say it and offer a way on."""
    zh, en = REFUSED_ALTERNATIVE
    reason = (why_words or "").strip()
    if lang == "en":
        return "The picture was not made" + (": " + reason if reason else ".") + " " + en
    return "这次没画出来" + ("：" + reason if reason else "。") + ("" if reason.endswith(("。", "！", "？")) or not reason else "。") + zh


async def draw(channel, host, via: str, text: str):
    """→ (reply text, [(mime, data)]) for an image request in an owner channel the main conversation cannot draw. `channel`
    is the WeChat adapter (the owner's WeChat) or the shared one for mail / Telegram (external_channels): its draw() runs the
    owner's Codex image tool, a fresh headless turn of its own."""
    from . import privacy
    from .channel_avatar import public_reply
    from .wechat import draw_failed_text
    lang = lang_of(host)
    res = await channel.draw(text)
    if res is None:
        why = getattr(channel, "draw_failed", None)
        if why == "channel_forbidden":
            return None, []
        detail = getattr(channel, "draw_said", None)
        if why == "no_image" and detail:
            return draw_refused_text(detail, lang), []
        return draw_failed_text(why, lang), []
    images = list(getattr(res, "images", None) or [])
    words = public_reply(str(res.text or ""), images, lang) or ("图画好了。" if lang != "en" else "Here is the picture.")
    return privacy.owner_text(words), images


def page_for(host, via: str, words: str, reply: str, paths=(), *, sender: str = "") -> dict | None:
    """The Web App's main conversation shows what the owner said in the channel and what was answered (an image request the
    host drew by itself never passed through the main queue)."""
    from . import privacy
    from .channel_card import attach
    k = {"wechat": "wechat", "telegram": "telegram", "mail": "email"}[via]
    src = {"k": k, "dev": via + ":owner", "name": (sender or name(via, lang_of(host)))[:64], "text": privacy.owner_text(words or "")}
    try:
        page = host.hist_add(src, privacy.owner_text(reply or ""), "done")
    except Exception:  # noqa: BLE001 — the channel already has the answer
        return None
    with contextlib.suppress(Exception):
        attach(host, page, list(paths))
    return page


async def mail_draw(host, words: str, subject: str):
    """mail.deliver's image request: → None (not one: the main conversation), False (drawing forbidden: nothing delivered) or
    a stand-in Send whose page is on the Web App and whose `reply` (picture attached) mail.reply sends."""
    from types import SimpleNamespace
    from .channel_avatar import Reply
    channel = getattr(host, "external_channels", None)
    if not callable(getattr(channel, "draw", None)) or main_draws(host) or not wants_image(words or subject):
        return None
    ask = (subject + "\n" + words).strip() if subject else words
    text, images = await draw(channel, host, "mail", ask)
    if text is None:
        return False
    page = page_for(host, "mail", ask, text, await channel.place_images("mail", images))
    if page is None or type(page.get("id")) is not int:
        return False
    return SimpleNamespace(turn=page["id"], reply=Reply(text, images))


async def tg_draw(tg, cfg, chat_id, raw) -> bool:
    """telegram.incoming's image request in the owner's private chat: drawn → picture / reason sent there, the exchange on the
    Web App page → True; not an image request (or no drawer) → False, the main conversation takes it."""
    from . import telegram as T
    host = tg.host
    channel = getattr(host, "external_channels", None)
    if not callable(getattr(channel, "draw", None)) or main_draws(host) or not wants_image(raw):
        return False
    text, images = await draw(channel, host, "telegram", raw)
    if text is None or not tg.enabled() or T.configuration(host.st) != cfg or host.stopped():
        return text is None
    try:
        await asyncio.to_thread(T.api, cfg, "sendMessage", {"chat_id": chat_id, "text": text})
        for mime, data in images:
            await asyncio.to_thread(T.upload, cfg, "sendPhoto", {"chat_id": chat_id}, "photo", "image." + mime.split("/")[1], mime, data)
    except ValueError:
        host.st.log("telegram_draw", result="send_failed")
    page_for(host, "telegram", raw, text, await channel.place_images("telegram", images))
    return True
