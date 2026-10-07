"""P73 (ADR-A176): the two friend slash commands the host answers itself — never the model, never a token.

    /my-agent-id                       this host's Agent ID (a code block: one tap copies it), the share link
                                       https://m.agentj.app/friends#add=AJ-…, and the way to 「我的名片」 (QR code)
    /add-friend AJ-XXXX-XXXX-XXXX-XXXX [note]

`/add-friend` is the friends page's 「加好友」 typed as a sentence. A friend request is a signed write (`fr_add`, §8 control
signature with the paired phone's approval key) and the phone's page is the only thing that holds that key, so the web page
intercepts the command and sends exactly that signed `fr_add` (PROTOCOL §17.7). What reaches the host as text — an older
page, Telegram, the phone's menu without the page's interception — never adds anyone: the host checks the ID (check
character included, PROTOCOL §17.1) and answers with where to finish it (an 「打开加好友」 button on the phone, a pointer to
the phone on Telegram). Both commands work without an Agent and while stopped (read-only / no effect).

Telegram's own command menu only allows [a-z0-9_]: `/my_agent_id` and `/add_friend` are the same commands.
"""
from __future__ import annotations

import re

from . import slash

HOST = ("my-agent-id", "add-friend")
ALIASES = {"my_agent_id": "my-agent-id", "add_friend": "add-friend", "myagentid": "my-agent-id", "addfriend": "add-friend"}
LABEL = {"my-agent-id": "我的 ID", "add-friend": "加好友"}
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def name_of(name) -> str | None:
    """A command name (already lower-cased by slash.parse) → one of HOST, or None."""
    if not isinstance(name, str):
        return None
    n = ALIASES.get(name, name)
    return n if n in HOST else None


def split_arg(arg: str) -> tuple[str, str]:
    """`AJ-XXXX-XXXX-XXXX-XXXX 附言…` → (ID text, note). An ID typed with spaces between its groups (`AJ 7KQ2 M9XA …`) is
    taken whole when the first 1–5 words make 16 Crockford characters (after AJ); otherwise the first word is the ID."""
    a = (arg or "").strip()
    if not a:
        return "", ""
    words = a.split()
    for n in range(min(5, len(words)), 1, -1):
        s = re.sub(r"[\s-]", "", "".join(words[:n])).upper()
        if s.startswith("AJ"):
            s = s[2:]
        if len(s) == 16 and all(c in CROCKFORD + "ILO" for c in s):
            return " ".join(words[:n]), " ".join(words[n:])
    return words[0], a[len(words[0]):].strip()


def id_problem(text: str) -> str | None:
    """None for a valid Agent ID, else "format" (not 16 characters of the alphabet) or "check" (the last character does
    not match — one character copied wrong)."""
    from .peer import parse_id
    if parse_id(text):
        return None
    s = re.sub(r"[\s-]", "", str(text or "")).upper()
    if s.startswith("AJ"):
        s = s[2:]
    s = s.translate(str.maketrans("ILO", "110"))
    if len(s) == 16 and all(c in CROCKFORD for c in s):
        return "check"
    return "format"


T = {
    "zh": {
        "id": "你的 Agent ID（点一下代码块即可复制）：\n\n```\n{id}\n```\n\n分享链接（对方在手机上点开就能加你）：\n{link}\n\n"
              "「我的名片」（有二维码）：点下面的「打开我的名片」，或者打开 {card}",
        "id_tg": "你的 Agent ID（点一下即可复制）：",
        "id_tg_rest": "分享链接（对方在手机上点开就能加你）：\n{link}\n\n「我的名片」（有二维码）在手机上：{card}",
        "undisc": "\n\n提醒：你现在设成了「不允许别人加我」，别人发来的请求不会出现。要打开，去「我的名片」里打开开关。",
        "seat": "\n\n提醒：这台电脑还没绑定 Agent J 账户，好友消息要绑定后才能收发。",
        "off": "好友功能现在是关着的，所以还没有可以分享的 ID。在电脑上运行 agentj friends on 打开后，再发一次 /my-agent-id。",
        "none": "还没有 Agent ID：好友功能还没准备好（在电脑上运行 agentj friends id 生成）。",
        "add_open": "打开「加好友」：点下面的按钮，把对方的 ID 填进去。",
        "add_bad_check": "这个 ID 的最后一位对不上，多半是抄错了一个字符：{id}\n请对一下再发。ID 长这样：AJ-XXXX-XXXX-XXXX-XXXX",
        "add_bad_format": "这不像一个 Agent ID：{id}\nID 长这样：AJ-XXXX-XXXX-XXXX-XXXX（AJ 后面 16 个字符）。",
        "add_phone": "ID 没问题：{id}\n好友请求要在手机上确认发出：点下面的「打开加好友」（已经帮你填好），再点「发送请求」。",
        "add_tg": "ID 没问题：{id}\n为了安全，好友请求只能在已配对的手机上发出：打开 Agent J 手机网页，发 /add-friend {id}（或者打开这个链接：{link}）。",
        "add_tg_bad": "Telegram 里不能发好友请求，要在已配对的手机上发。另外这个 ID 有问题：{why}",
        "add_tg_open": "Telegram 里不能发好友请求，要在已配对的手机上发：打开 Agent J 手机网页，点「好友」→「加好友」。",
        "tg_owner": "只有机主在私聊里能用这个命令。",
    },
    "en": {
        "id": "Your Agent ID (tap the code block to copy it):\n\n```\n{id}\n```\n\nShare link (opening it on their phone adds "
              "you):\n{link}\n\nYour card (with the QR code): tap \"Open my card\" below, or open {card}",
        "id_tg": "Your Agent ID (tap to copy):",
        "id_tg_rest": "Share link (opening it on their phone adds you):\n{link}\n\nYour card (QR code) on the phone: {card}",
        "undisc": "\n\nNote: \"Let others add me\" is off, so requests from others are not shown. Turn it on under My card.",
        "seat": "\n\nNote: this computer is not bound to an Agent J account yet; friend messages need the account.",
        "off": "Agent friends are switched off, so there is no ID to share yet. Run agentj friends on at the computer, then "
               "send /my-agent-id again.",
        "none": "No Agent ID yet: friends are not set up (run agentj friends id at the computer).",
        "add_open": "Opening Add a friend: tap the button below and enter their ID.",
        "add_bad_check": "The last character of this ID does not match, so one character was probably copied wrong: {id}\n"
                         "Please check it. An ID looks like AJ-XXXX-XXXX-XXXX-XXXX.",
        "add_bad_format": "This does not look like an Agent ID: {id}\nAn ID looks like AJ-XXXX-XXXX-XXXX-XXXX (16 characters "
                          "after AJ).",
        "add_phone": "The ID is fine: {id}\nA friend request is confirmed on the phone: tap \"Open Add a friend\" below "
                     "(already filled in), then Send request.",
        "add_tg": "The ID is fine: {id}\nFor safety a friend request can only be sent from your paired phone: open the "
                  "Agent J phone page and send /add-friend {id} (or open {link}).",
        "add_tg_bad": "Friend requests cannot be sent from Telegram, only from your paired phone. Also, this ID is wrong: {why}",
        "add_tg_open": "Friend requests cannot be sent from Telegram, only from your paired phone: open the Agent J phone page, "
                       "Friends → Add a friend.",
        "tg_owner": "Only the owner can use this command, in a private chat.",
    },
}


def _t(lang: str) -> dict:
    return T["en" if lang == "en" else "zh"]


def my_agent_id(st, lang: str = "zh", peer_state: str | None = None) -> slash.Result:
    """The phone's `/my-agent-id` card. Result.open = "fr_card" → the page shows 「打开我的名片」."""
    from .peer_service import my_agent_id as info
    d = _t(lang)
    i = info(st)
    if i["state"] != "ok":
        return slash.Result(d[i["state"]], "info")
    text = d["id"].format(id=i["id"], link=i["link"], card=i["card_link"])
    if not i.get("discoverable", True):
        text += d["undisc"]
    if peer_state == "need_seat":
        text += d["seat"]
    r = slash.Result(text, "ok")
    r.open = "fr_card"
    return r


def my_agent_id_telegram(st, lang: str = "zh", peer_state: str | None = None) -> dict:
    """The same answer as a Telegram sendMessage body: the ID alone in a `code` entity (Telegram copies it on one tap)."""
    from .peer_service import my_agent_id as info
    d = _t(lang)
    i = info(st)
    if i["state"] != "ok":
        return {"text": d[i["state"]]}
    head = d["id_tg"] + "\n"
    rest = "\n\n" + d["id_tg_rest"].format(link=i["link"], card=i["card_link"])
    if not i.get("discoverable", True):
        rest += d["undisc"]
    if peer_state == "need_seat":
        rest += d["seat"]
    off = len(head.encode("utf-16-le")) // 2
    return {"text": head + i["id"] + rest,
            "entities": [{"type": "code", "offset": off, "length": len(i["id"])}],
            "link_preview_options": {"is_disabled": True}}


def add_friend(arg: str, lang: str = "zh", channel: str = "phone") -> slash.Result:
    """`/add-friend` that reached the host as text. Never adds: phone → 「打开加好友」 (prefilled when the ID is valid);
    Telegram → a pointer to the paired phone. Result.open = "fr_add" | "fr_add:<ID>"."""
    from .peer import parse_id
    from .peer_service import link_of
    d = _t(lang)
    raw, _note = split_arg(arg)
    if not raw:
        if channel == "telegram":
            return slash.Result(d["add_tg_open"], "info")
        r = slash.Result(d["add_open"], "info")
        r.open = "fr_add"
        return r
    why = id_problem(raw)
    shown = raw[:40]
    if why:
        msg = d["add_bad_check" if why == "check" else "add_bad_format"].format(id=shown)
        if channel == "telegram":
            return slash.Result(d["add_tg_bad"].format(why=msg), "error")
        r = slash.Result(msg, "error")
        r.open = "fr_add"
        return r
    aid = parse_id(raw)
    if channel == "telegram":
        return slash.Result(d["add_tg"].format(id=aid, link=link_of(aid)), "info")
    r = slash.Result(d["add_phone"].format(id=aid), "info")
    r.open = "fr_add:" + aid
    return r
