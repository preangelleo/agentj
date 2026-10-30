"""Deliver checked reply attachments to the owner private channel (ADR-A165).
Reuse media checks, no-follow reads and bounded uploads. Credential files use pickup cards;
masked copies never fall back to their unmasked source. Hashing/scanning runs off the event loop."""
from __future__ import annotations

import hashlib
import os
import stat

from . import media as media_mod

MB = 1000 * 1000
UPLOAD_MAX = 50 * MB          # Bot API: "Bots can currently send files of up to 50 MB in size"
PHOTO_MAX = 10 * MB           # sendPhoto: "The photo must be at most 10 MB in size"
GAP = 1.0                     # s between two uploads to one chat (Telegram: about one message per second per chat)

# mime → (Bot API method, multipart field). Everything else is a document.
METHODS = {"image/png": ("sendPhoto", "photo"), "image/jpeg": ("sendPhoto", "photo"),
           "audio/mpeg": ("sendAudio", "audio"), "audio/mp4": ("sendAudio", "audio"),
           "video/mp4": ("sendVideo", "video")}
DOCUMENT = ("sendDocument", "document")
MAX_ITEMS = 8          # Telegram's own per-reply cap (P194 raised the phone page to media.MAX_ITEMS = 24; a chat is not a page)

# P211: every refusal says why and what to do next — never just 「可能含密钥」
SKIP = {"zh": {"too_big": "{name} 太大（{mb} MB），没有发到 Telegram。", "outside": "{name} 不在工作目录里，没有发。",
               "secret": "{name} 的文件名或内容像密钥，所以没发；去掉密钥或改个名后让 Agent J 再发。",
               "private": "{name} 在 Agent J 的内部文件夹里，不对外发；要给别人，先让 Agent J 把它复制到工作目录的普通文件夹里。",
               "key_file": "{name} 是凭据文件（.env、私钥这类），不直接发；要拿到它，跟 Agent J 说「用密钥领取卡发给我」，在手机上用 Face ID 领取。",
               "key_content": "{name} 里有真实的密钥（如 sk-…），所以没随文件发；要拿到它，跟 Agent J 说「用密钥领取卡发给我」，或删掉密钥后再发。",
               "key_card": "{name} 含密钥，没有随消息发出：已在你的手机上弹出密钥领取卡，验证 Face ID 后领取。",
               "type": "{name} 这种文件发不了。", "gone": "{name} 找不到了，没有发。",
               "masked": "{name} 的路径被隐私规则遮住了，所以没发；文件本身可能还在。", "failed": "{name} 没发出去。"},
        "en": {"too_big": "{name} is too big ({mb} MB) — not sent to Telegram.", "outside": "{name} isn't in the working folder — not sent.",
               "secret": "{name} — its name or content looks like a key, so it wasn't sent. Remove the key or rename it, then ask Agent J again.",
               "private": "{name} is in Agent J's internal folder and doesn't go to others — ask Agent J to copy it to a normal folder first.",
               "key_file": "{name} is a credential file (.env, a private key …) and isn't sent as a file — ask Agent J to send it as a secret pickup card (Face ID on your phone).",
               "key_content": "{name} holds a real key (like sk-…), so it wasn't sent as a file — ask Agent J to send it as a secret pickup card, or remove the key and send again.",
               "key_card": "{name} holds a key and wasn't sent with the message: a secret pickup card is waiting on your phone (Face ID to open).",
               "type": "{name} is a kind of file that can't be sent.",
               "gone": "{name} wasn't found — not sent.",
               "masked": "{name} — its path was masked by a privacy rule, so it wasn't sent (the file may still be there).", "failed": "{name} couldn't be sent."}}


def method_of(rec: dict) -> tuple[str, str]:
    m = METHODS.get(rec.get("mime"), DOCUMENT)
    if m[0] == "sendPhoto" and rec.get("bytes", 0) > PHOTO_MAX:
        return DOCUMENT
    return m


def collect(media: "media_mod.Media", text: str, keys: list | None = None, mask: bool = False) -> tuple[list[dict], list[dict]]:
    """Blocking owner attachment scan → records/skips. Run in a thread.
    With keys+mask, offer masked text copies and transient value-card pairs; credential files stay whole cards."""
    mask = mask and keys is not None
    items: list[dict] = []
    skips: list[dict] = []
    total, seen = 0, set()
    if not media or not getattr(media, "workdir", None) or not text:
        return items, skips
    for ref in media_mod.refs(text):
        if len(items) >= MAX_ITEMS:
            break
        name = os.path.basename(ref.path.rstrip("/"))[:media_mod.NAME_MAX] or ref.path[:media_mod.NAME_MAX]
        if media_mod._PLACEHOLDER.search(ref.path):        # P194: a masked path is `masked`, never 「找不到了」
            if ref.explicit:
                skips.append({"name": name, "why": "masked"})
            continue
        try:
            rec, why = media.check(ref, owner=True, mask=True) if mask else media.check(ref, owner=True)
        except OSError:
            rec, why = None, None
        values = media.last_values() if mask else []
        if why:
            skip = {"name": name, "why": why, **({"bytes": rec["bytes"]} if rec and why == "too_big" else {}),
                    **media_mod._at(media, why)}
            skips.append(skip)
            if keys is not None and why in ("key_file", "key_content") and rec and "sha256" in rec and ref.explicit:
                keys.append((skip, rec))
            continue
        if rec is None or (rec["dev"], rec["ino"]) in seen:
            continue
        if values and ref.explicit:
            keys.append((None, {"name": rec["name"], "sha256": rec["sha256"], "values": values}))
        if rec["bytes"] > UPLOAD_MAX or total + rec["bytes"] > media_mod.PAGE_BYTES:
            skips.append({"name": rec["name"], "why": "too_big", "bytes": rec["bytes"]})
            continue
        seen.add((rec["dev"], rec["ino"]))
        total += rec["bytes"]
        items.append(rec)
    return items, skips[:MAX_ITEMS * 2]


def read_checked(rec: dict) -> bytes:
    """No-follow revalidated bytes or Gone; stored masked copies never fall back to their source."""
    if rec.get("blob"):
        data = media_mod.read_blob(rec)
        if data is not None:
            return data
        if rec.get("masked"):
            raise media_mod.Gone("store")
    fd = media_mod.open_rel(rec["root"], list(rec["parts"]))
    try:
        s = os.fstat(fd)
        if (not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or (s.st_dev, s.st_ino) != (rec["dev"], rec["ino"])
                or s.st_size != rec["bytes"] or s.st_mtime_ns != rec["mtime"]):
            raise media_mod.Gone("changed")
        out = bytearray()
        while len(out) < rec["bytes"]:
            b = os.pread(fd, media_mod.SCAN_SLICE, len(out))
            if not b:
                break
            out += b
        if len(out) != rec["bytes"] or hashlib.sha256(out).hexdigest() != rec["sha256"]:
            raise media_mod.Gone("sha")
        return bytes(out)
    finally:
        os.close(fd)


AT = {"zh": "（第 {line} 行）", "en": " (line {line})"}     # P424 #142: where the key-like text is — the line only, no characters


def skip_lines(skips: list[dict], lang: str = "zh") -> str:
    t = SKIP.get(lang, SKIP["zh"])
    lines = []
    for s in skips:
        mb = f"{s.get('bytes', 0) / (1024 * 1024):.1f}".rstrip("0").rstrip(".")
        at = AT.get(lang, AT["zh"]).format(line=s["line"]) if isinstance(s.get("line"), int) else ""
        lines.append(t.get(s["why"], t["gone"]).format(name=s["name"], mb=mb) + at)
    return "\n".join(lines)
