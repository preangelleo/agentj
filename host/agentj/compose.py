"""Compose — the one message the Agent receives for a phone's `say` (PROTOCOL §10.2 / §10.6 / §10.9, PROMPT-33; relay
`bridge/compose.py` + `quote.py`), and the send table that makes 「× 取消」 exact.

What the Agent gets (host-built, in the host's language, `config.json` `lang`, default zh): the quote block (if any), the
human's text, then — with attachments — where the files are (absolute paths inside its own folder; the bytes never travel
through the chat), then for each voice recording what was said in it. The quote block is built from the host's OWN history
(the phone sends only the turn id), so a phone cannot make the Agent believe a page said something it did not — except through
`excerpt`, which is labelled as the human's own selection.

Withdraw (relay `compose.Sends`): every say is a `Send` whose lock is held from "is it cancelled?" through the write to the
harness (agent.Agent.deliver); `say_cancel` takes the same lock, so `cancelled` is never answered for a message that went out
and `already_delivered` never for one that did not.
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field

QUOTE_CHARS = 300
EXCERPT_MAX = 2000
QUOTES_MAX = 5                  # P286: one say quotes ≤ 5 places (reply_to + excerpt, then quote_more ≤ 4)
CAP_MQ = "mq5"                  # P286: `ready` caps — this host takes `quote_more`
CAP_SYNC = "ss1"                # P398: `ready` caps — this host answers `say_sync` (where the phone's queued sends are)
SEND_TTL = 600
MAX_SENDS = 16                  # not yet handed to the Agent, per device (the 17th is refused: too_many)
SEEN_MAX = 1024                 # sids remembered per device (dup), independent of SEND_TTL
ASR_INLINE = 30_000       # P166: a longer transcript goes into the inbox as a .txt; the message keeps its beginning
SAY_ASR_BUDGET = 3960.0  # maximum per recording, including cold start on slow CPUs

def asr_budget(seconds=None):
    """180 s cold start + six times recording duration; unknown duration gets the full budget.

    Queue waiting is outside this processing budget. A recording is bounded to 630 s by the WAV decoder.
    This is a conservative allowance, not an Intel Mac performance measurement.
    """
    import math
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not math.isfinite(seconds) or seconds <= 0:
        return SAY_ASR_BUDGET
    return min(SAY_ASR_BUDGET, 180.0 + 6.0 * seconds)


T = {
    "zh": {
        "att_head": "附件 {n} 个（已在你的工作目录里，内容没有经过聊天通道——需要时自己读这些路径）：",
        "att_line": "- {path}（{mime}，{bytes} 字节）",
        "asr_head": "语音转写（{secs}{name}）：",
        "asr_secs": "{s} 秒，",
        "asr_fail": "转写失败（{why}）——音频仍在上面的路径。",
        "asr_partial": "（转写不完整：{why}，后面的部分没有转出来——完整音频仍在上面的路径。）",
        "asr_more": "（转写较长，上面只是开头；全文在 {path}）",
        "quote_head": "【回复 #{id} · {who} · {hhmm}】",
        "excerpt": "（摘录）",
        "mq_head": "【引用 {i}/{n} · 你的回复 #{id} · {when}】",
        "mq_ctx": "（选自这条回复，它的开头：{line}）",
        "voice_only_fail": "（这条消息只有语音、没有文字，而且语音没转写出来。请简短告诉对方没听清，请他再说一遍或者打字——不要自己猜他说了什么。）",
        "who": {"phone": "你", "host": "Agent J 主机", "agent": "Agent", "sys": "系统", "task": "定时任务", "cmd": "命令"},
        "why": {"timeout": "处理超时", "no_speech": "没有听到说话", "not_installed": "Agent J 主机上还没装语音转写",
                "off": "语音转写已关闭", "broken": "转写器出错", "busy": "转写器忙", "bad_audio": "音频格式不对",
                "format": "格式", "cancelled": "已取消", "cut": "超过 2 小时的部分没有转写"},
        # P342 (#28): a Telegram message the human replied to — same shape as 「回复 #N」, but Telegram has no page id
        "tg_quote_head": "【回复 Telegram 消息 · {who}{when}】",
        "tg_quote_ext": "【回复另一个聊天里的消息 · {who}{when}】",
        "tg_who": {"bot": "你（Agent，经这个 Telegram Bot 发出）", "owner": "主人", "member": "群成员 {id}",
                   "bot_other": "{name}（机器人）", "unknown": "来源不明"},
        "tg_media": {"photo": "[图片]", "voice": "[语音]", "audio": "[音频]", "video": "[视频]", "video_note": "[视频消息]",
                     "animation": "[动图]", "sticker": "[贴纸]", "document": "[文件 {name}]", "location": "[位置]",
                     "venue": "[地点]", "contact": "[联系人]", "poll": "[投票]"},
        "tg_empty": "（那条消息没有文字）",
    },
    "en": {
        "att_head": "{n} attachment(s) (already in your working folder; their content did not travel through the chat — read "
                    "these paths yourself when you need them):",
        "att_line": "- {path} ({mime}, {bytes} bytes)",
        "asr_head": "Voice transcript ({secs}{name}):",
        "asr_secs": "{s} s, ",
        "asr_fail": "Transcription failed ({why}) — the audio is still at the path above.",
        "asr_partial": "(Transcript incomplete: {why}; the rest was not transcribed — the full audio is still at the path above.)",
        "asr_more": "(Long transcript: only the beginning is above; the full text is at {path})",
        "quote_head": "[Reply to #{id} · {who} · {hhmm}]",
        "excerpt": "(excerpt)",
        "mq_head": "[Quote {i}/{n} · your reply #{id} · {when}]",
        "mq_ctx": "(selected from that reply, which begins: {line})",
        "voice_only_fail": "(This message is a voice note only, with no text, and it could not be transcribed. Briefly tell them you "
                           "did not catch it and ask them to say it again or type it — do not guess what was said.)",
        "who": {"phone": "you", "host": "computer", "agent": "Agent", "sys": "system", "task": "scheduled task",
                "cmd": "command"},
        "why": {"timeout": "processing timed out", "no_speech": "no speech heard", "not_installed": "local transcription is not installed",
                "off": "transcription is off", "broken": "the transcriber failed", "busy": "the transcriber is busy",
                "bad_audio": "wrong audio format", "format": "format", "cancelled": "cancelled",
                "cut": "audio past 2 hours was not transcribed"},
        "tg_quote_head": "[Reply to Telegram message · {who}{when}]",
        "tg_quote_ext": "[Reply to a message from another chat · {who}{when}]",
        "tg_who": {"bot": "you (the Agent, sent via this Telegram bot)", "owner": "the owner", "member": "group member {id}",
                   "bot_other": "{name} (a bot)", "unknown": "unknown sender"},
        "tg_media": {"photo": "[photo]", "voice": "[voice]", "audio": "[audio]", "video": "[video]", "video_note": "[video message]",
                     "animation": "[GIF]", "sticker": "[sticker]", "document": "[file {name}]", "location": "[location]",
                     "venue": "[place]", "contact": "[contact]", "poll": "[poll]"},
        "tg_empty": "(that message has no text)",
    },
}


def lang_of(st) -> str:
    try:
        v = st.config().get("lang")
    except (OSError, ValueError):
        v = None
    return v if v in T else "zh"


def who_of(src: dict, lang: str) -> str:
    """The quoted page's speaker as the Agent should read it: a phone's label, else the kind."""
    k = src.get("k")
    if k == "phone" and src.get("name"):
        return str(src["name"])[:64]
    if k in ("task", "cmd") and src.get("name"):
        return str(src["name"])[:64]
    return T[lang]["who"].get(k, str(k))


def quote_info(turn: dict, excerpt: str | None, lang: str) -> dict:
    """src.quote as stored with the phone's turn: {id, who, ts, text ≤ 300, ex}."""
    text = excerpt if excerpt else (turn.get("reply") or {}).get("text") or (turn.get("src") or {}).get("text") or ""
    return {"id": turn["id"], "who": who_of(turn.get("src") or {}, lang), "ts": turn.get("ts", 0),
            "text": text[:QUOTE_CHARS], "ex": bool(excerpt)}


def quote_block(turn: dict, excerpt: str | None, lang: str) -> str:
    q = quote_info(turn, excerpt, lang)
    hhmm = time.strftime("%H:%M", time.localtime((q["ts"] or 0) / 1000))
    body = excerpt if excerpt else q["text"]
    lines = [T[lang]["quote_head"].format(id=q["id"], who=q["who"], hhmm=hhmm)]
    lines += ["> " + ln for ln in body.split("\n")]
    if excerpt:
        lines.append("> " + T[lang]["excerpt"])
    return "\n".join(lines)


def _first_line(text: str, n: int = 60) -> str:
    for ln in (text or "").split("\n"):
        ln = ln.strip().lstrip("#>*-+ ").strip()
        if ln:
            return ln if len(ln) <= n else ln[:n] + "…"
    return ""


def quotes_block(items: list, lang: str) -> str:
    """P286: [(turn, excerpt|None)], in the order the human picked them. One = `quote_block` exactly; two to five = one
    numbered block each — 「引用 i/n」, the page's id (the Agent's own reply: the phone quotes reply cards) and time (with the date when not today), the words as `> `
    lines; an excerpt also gets that reply's opening line so the Agent knows which reply it came from."""
    if len(items) == 1:
        return quote_block(items[0][0], items[0][1], lang)
    t, now, out = T[lang], time.localtime(), []
    for i, (turn, ex) in enumerate(items, 1):
        q = quote_info(turn, ex, lang)
        lt = time.localtime((q["ts"] or 0) / 1000)
        when = time.strftime("%H:%M" if lt[:3] == now[:3] else "%m-%d %H:%M", lt)
        lines = [t["mq_head"].format(i=i, n=len(items), id=q["id"], when=when)]
        lines += ["> " + ln for ln in (ex if ex else q["text"]).split("\n")]
        if ex:
            lines.append("> " + t["excerpt"])
            head = _first_line((turn.get("reply") or {}).get("text") or "")
            if head and not ex.strip().startswith(head.rstrip("…")[:12]):
                lines.append("> " + t["mq_ctx"].format(line=head))
        out.append("\n".join(lines))
    return "\n\n".join(out)


def tg_quote_block(q: dict, lang: str, limit: int = QUOTE_CHARS, wrap=None) -> str:
    """P342 (#28): the Telegram message the human replied to, in `quote_block`'s shape — a head line, the words as `> `
    lines, 「（摘录）」 when Telegram sent only the part they selected. q = {who, ts (ms, 0 = unknown), text, ex, ext,
    named (who is a Telegram name, not our label)}. The words are at most `limit` characters (an ellipsis when cut).
    `wrap` (a group / forward: provenance.body) neutralizes the untrusted words and name; the head and `> ` stay ours, so a
    sender cannot draw a quote block of their own inside the envelope."""
    t = T[lang]
    wrap = wrap or (lambda x: x)
    when = time.strftime(" · %H:%M", time.localtime(q["ts"] / 1000)) if q.get("ts") else ""
    body = q.get("text") or t["tg_empty"]
    if len(body) > limit:
        body = body[:max(0, limit)] + "…"
    body = wrap(body)
    who = wrap(q["who"]) if q.get("named") else q["who"]
    lines = [t["tg_quote_ext" if q.get("ext") else "tg_quote_head"].format(who=who, when=when)]
    lines += ["> " + ln for ln in body.split("\n")]
    if q.get("ex"):
        lines.append("> " + t["excerpt"])
    return "\n".join(lines)

def render(text: str, files: list[dict], lang: str = "zh", quote: str | None = None, native=None) -> str:
    """files: [{"path", "mime", "bytes", "origin", "asr"?: {"ok", "text"?, "why"?, "secs"?}}]

    F27 (0.16): `text` may be empty (or spaces) when files came with the message — the prompt is then the attachment block and
    the transcripts alone, never an empty text part; voice notes alone that all failed to transcribe end with a line telling
    the Agent to ask again instead of guessing. `native(text)` → the Agent's words for a typed harness command it does on the
    owner's behalf (P412 #175), or None."""
    t = T[lang]
    parts = []
    if quote:
        parts.append(quote)
    if not files and not quote:      # P155: the phone menu's /switch-harness goes to the main Agent, rewritten (never "/…")
        from .switch_harness import agent_text
        from .feedback_father import agent_text as feedback_text
        # P412 #175: a typed command of the harness that the Agent does for the owner (`native` = Host.native_text)
        text = feedback_text(text, lang) or agent_text(text, lang) or (native(text) if native else None) or text
    if text and text.strip():
        parts.append(text)
    if files:
        lines = [t["att_head"].format(n=len(files))]
        lines += [t["att_line"].format(path=f["path"], mime=f["mime"], bytes=f"{f['bytes']:,}") for f in files]
        parts.append("\n".join(lines))
    for f in files:
        a = f.get("asr")
        if not a:
            continue
        secs = t["asr_secs"].format(s=round(a["secs"])) if a.get("secs") else ""
        head = t["asr_head"].format(secs=secs, name=os.path.basename(f["path"]))
        if a.get("ok") and (a.get("text") or "").strip():
            parts.append(head + "\n" + a["text"])
            if a.get("file"):
                parts.append(t["asr_more"].format(path=a["file"]))
            if a.get("partial"):
                parts.append(t["asr_partial"].format(why=t["why"].get(a["partial"], a["partial"])))
        else:
            parts.append(head + "\n" + t["asr_fail"].format(why=t["why"].get(a.get("why"), a.get("why") or "?")))
    if files and not (text and text.strip()) and all(f.get("asr") and not (f["asr"].get("ok") and (f["asr"].get("text") or "").strip())
                                                     for f in files):
        parts.append(t["voice_only_fail"])
    return "\n\n".join(parts)


@dataclass
class Send:
    """One `say` that can be withdrawn. state: pending → delivering → delivered | failed (the harness said no: never
    delivered) | uncertain (the write raised half-way: maybe delivered) ; pending → cancelled."""
    device: str
    sid: str
    turn: int
    text: str = ""                 # what the Agent gets (built once the transcripts are in)
    blobs: list = field(default_factory=list)
    created: float = field(default_factory=time.monotonic)
    state: str = "pending"
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    ready: asyncio.Future | None = None       # resolved when the text is complete (say-time transcription)
    prep: asyncio.Task | None = None
    by: str | None = None
    source: dict | None = None     # B1 (P117): host-generated provenance (agentj.provenance); None = the paired phone

    async def wait_ready(self) -> bool:
        if self.ready is not None:
            try:
                await self.ready
            except asyncio.CancelledError:
                return False
        return self.state != "cancelled"


UNSENT = ("pending", "delivering")


class Sends:
    """(device, sid) → Send. A device has at most MAX_SENDS sends not yet handed over (pending / delivering): the next one
    is refused (`say_res why:"too_many"`), never one of them evicted (P33-C03 / X06) — an evicted send would still be
    delivered but could no longer be withdrawn, and its sid would be free for a duplicate. Finished sends stay SEND_TTL
    (withdraw answers); every sid a device used is remembered (SEEN_MAX newest per device) so a retry with the same sid is
    `dup`, never a second delivery."""

    def __init__(self):
        self.items: dict[tuple, Send] = {}
        self.seen: dict[str, dict[str, None]] = {}      # device → sids in use order (insertion-ordered, capped)

    def _sweep(self) -> None:
        now = time.monotonic()
        for k, s in list(self.items.items()):
            if now - s.created > SEND_TTL and s.state not in UNSENT:
                del self.items[k]

    def used(self, device: str, sid: str) -> bool:
        self._sweep()
        return (device, sid) in self.items or sid in self.seen.get(device, {})

    def full(self, device: str) -> bool:
        """MAX_SENDS of this device's sends are still waiting to be handed to the Agent."""
        return sum(1 for x in self.items.values() if x.device == device and x.state in UNSENT) >= MAX_SENDS

    def add(self, s: Send) -> bool:
        """False (nothing added) when the device is full and s would wait too; finished sends make room first."""
        self._sweep()
        if s.state in UNSENT and self.full(s.device):
            return False
        mine = sorted((x for x in self.items.values() if x.device == s.device and x.state not in UNSENT),
                      key=lambda x: x.created)
        n = sum(1 for x in self.items.values() if x.device == s.device)
        for old in mine[:max(0, n - MAX_SENDS * 2 + 1)]:      # only finished ones ever leave early
            del self.items[(old.device, old.sid)]
        self.items[(s.device, s.sid)] = s
        seen = self.seen.setdefault(s.device, {})
        seen[s.sid] = None
        while len(seen) > SEEN_MAX:
            seen.pop(next(iter(seen)))
        return True

    def get(self, device: str, sid: str) -> Send | None:
        return self.items.get((device, sid))

    async def cancel(self, device: str, sid: str) -> tuple[str, Send | None]:
        """'cancelled' | 'already_delivered' | 'not_found' — another device's id reads 'not_found'. `cancelled` only when the
        message provably never reached the harness (pending, or the harness answered "no"); a write that failed half-way
        (`uncertain`) is answered `already_delivered` — the phone's "too late" (P33-X08)."""
        s = self.items.get((device, sid))
        if s is None:
            return "not_found", None
        async with s.lock:                      # waits out a write in progress
            if s.state in ("delivered", "uncertain"):
                return "already_delivered", s
            if s.state == "cancelled":
                return "cancelled", None        # said once already: nothing more to undo
            s.state = "cancelled"
        if s.prep is not None and not s.prep.done():
            s.prep.cancel()
        if s.ready is not None and not s.ready.done():
            s.ready.cancel()
        return "cancelled", s
