"""F21 over Telegram (P59, ADR-A165): files an Agent reply shows go to the owner's private Telegram chat as well.

Same detection and the same safety checks as the phone (media.py, PROTOCOL §13): `media.refs` finds the references,
`Media.check` resolves each one (realpath inside the Agent's folder, outside the state dir, the deny list, an O_NOFOLLOW
dir-fd chain, a regular file of this user, extension + leading bytes, the per-kind caps, `taskspec.secret_like` for text
kinds). On top: Telegram's own bot upload limits (UPLOAD_MAX; a photo above PHOTO_MAX goes as a document instead), ≤
media.MAX_ITEMS files and ≤ media.PAGE_BYTES per reply. Right before each upload the file is re-opened the same safe way and
(dev, inode, size, mtime) and the SHA-256 of what is sent must equal what was checked — otherwise it is `gone`.

Who gets files: ONLY the owner's private chat. A reply to a turn that came from a Telegram group never carries files (a group
is not owner-only: its members are not the owner, and a file in the owner's folder is the owner's data). zip contents are not
scanned (same as the phone). Logs: counts only, never names, paths or content.
"""
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

SKIP = {"zh": {"too_big": "{name} 太大（{mb} MB），没有发到 Telegram。", "outside": "{name} 不在工作目录里，没有发。",
               "secret": "{name} 可能含密钥，没有发。", "type": "{name} 这种文件发不了。", "gone": "{name} 找不到了，没有发。",
               "failed": "{name} 没发出去。"},
        "en": {"too_big": "{name} is too big ({mb} MB) — not sent to Telegram.", "outside": "{name} isn't in the working folder — not sent.",
               "secret": "{name} may contain a key — not sent.", "type": "{name} is a kind of file that can't be sent.",
               "gone": "{name} wasn't found — not sent.", "failed": "{name} couldn't be sent."}}


def method_of(rec: dict) -> tuple[str, str]:
    m = METHODS.get(rec.get("mime"), DOCUMENT)
    if m[0] == "sendPhoto" and rec.get("bytes", 0) > PHOTO_MAX:
        return DOCUMENT
    return m


def collect(media: "media_mod.Media", text: str) -> tuple[list[dict], list[dict]]:
    """A reply → (records to upload, skips {name, why, bytes?}). Blocking (hashing, the secret scan): run in a thread."""
    items: list[dict] = []
    skips: list[dict] = []
    total, seen = 0, set()
    if not media or not getattr(media, "workdir", None) or not text:
        return items, skips
    for ref in media_mod.refs(text):
        if len(items) >= media_mod.MAX_ITEMS:
            break
        try:
            rec, why = media.check(ref)
        except OSError:
            rec, why = None, None
        name = os.path.basename(ref.path.rstrip("/"))[:media_mod.NAME_MAX] or ref.path[:media_mod.NAME_MAX]
        if why:
            skips.append({"name": name, "why": why, **({"bytes": rec["bytes"]} if rec and why == "too_big" else {})})
            continue
        if rec is None or (rec["dev"], rec["ino"]) in seen:
            continue
        if rec["bytes"] > UPLOAD_MAX or total + rec["bytes"] > media_mod.PAGE_BYTES:
            skips.append({"name": rec["name"], "why": "too_big", "bytes": rec["bytes"]})
            continue
        seen.add((rec["dev"], rec["ino"]))
        total += rec["bytes"]
        items.append(rec)
    return items, skips[:media_mod.MAX_ITEMS * 2]


def read_checked(rec: dict) -> bytes:
    """The bytes to upload, re-opened through the no-follow chain; raises media.Gone when the file is not the one checked."""
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


def skip_lines(skips: list[dict], lang: str = "zh") -> str:
    t = SKIP.get(lang, SKIP["zh"])
    lines = []
    for s in skips:
        mb = f"{s.get('bytes', 0) / (1024 * 1024):.1f}".rstrip("0").rstrip(".")
        lines.append(t.get(s["why"], t["gone"]).format(name=s["name"], mb=mb))
    return "\n".join(lines)
