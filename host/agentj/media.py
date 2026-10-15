"""Media out — files the Agent shows to the phone (PROTOCOL §13, F21, 0.15.2): the reverse of §10.3, inside the Noise session.

When a page ends, serve hands its reply text to `Media.scan` (in a thread). The scan finds local file references — Markdown
images / links `![a](p)` `[a](p)` (explicit), `` `p` `` and bare paths (implicit), `file://` URLs; never http(s) (nothing is
fetched) and nothing inside a fenced code block (what the phone shows as code is not an attachment) — and checks each one:

  * resolved with every symlink (`realpath`) against the Agent's folder (`agentj agent … --dir`, = the session cwd): the
    result must lie inside that folder, outside agentj's state dir, and no component may be `.agentj` `.git` `credentials`
    `.ssh` `.aws` `.gnupg`, nor the file `.env` `.env.*` `*.key` `*.pem` `*.p12` `*.pfx` `id_rsa*` `id_ed25519*` `.netrc`
    `.npmrc` `.pypirc` (checked on the path as written AND as resolved);
  * opened through an O_NOFOLLOW directory-fd chain from the folder along the resolved components (a link planted between
    the realpath and the open fails closed), the file itself O_NOFOLLOW | O_NONBLOCK, then fstat: a regular file of this
    user — every later read re-opens the same way and compares (dev, inode, size, mtime) with what was scanned;
  * typed by extension AND leading bytes (KINDS); capped per kind (CAPS), ≤ MAX_ITEMS per page, ≤ PAGE_BYTES per page;
  * text-type content (svg, html, txt/md/csv/json …) runs through the host's secret-fragment detector
    (taskspec.secret_like = privacy layer 1, secret kinds only) before anything is offered; `data:` payloads inside
    html / svg are dropped from that scan (they are pictures, and a base64 picture always looks "high entropy").
  * P211: that scan and the `.agentj` / `.git` folder rule apply to OTHERS (owner=False). Toward the owner a file goes as it
    is, except a credential file (by name / KEY_DIRS) or a text file holding a real key shape, which is offered as a secret
    pickup card instead (see WHYS). Audio / video / pictures are typed by their leading bytes and never read as text.

Implicit references (backticks / bare paths) offer only media (image, audio, video, pdf, html) and deliverables (zip, office
files, csv …) — an Agent that lists the source files it edited does not fill the phone with cards — and are silent when the
file is missing or outside the folder (the Agent names system paths all the time). Explicit links offer every allowed type
and every refusal is a `media_skip` note on the page. Never the content of a refused file.

Wire (§13): the finished page gains `media` [{mid, name, mime, kind, bytes, sha256, ref}] and `media_skip` [{name, why}].
The phone pulls lazily: `media_get {mid, o}` → ≤ WINDOW `media_chunk {mid, o, d, last}` (45 056 raw bytes each) or
`media_err {mid, why}`. Integrity: a get at o = 0 re-hashes the whole file and answers `gone` when the SHA-256 differs from the
page's; every get re-checks dev / inode / size / mtime; the phone verifies the SHA-256 of what it assembled before showing it.
The table mid → (page, folder, components, size, sha256, …) is `<state>/media.json` (0600, survives a restart), newest
KEEP_N, KEEP_SECS old at most; an expired mid → `gone` → the phone says 「已过期」.
Stdlib only.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import secrets
import stat
import threading
import time
import urllib.parse
from dataclasses import dataclass

from . import taskspec, uploads, wire

CHUNK = 45_056                    # raw bytes per media_chunk → 60 075 base64url chars → one 60 160-byte padded frame (§10.3)
WINDOW = 8                        # chunks answered per media_get (the phone asks for the next window when it has them)
FPS = 22                          # host → device pacing, the §10.3 boosted rate (≤ 22 frames / s)
MIB = 1024 * 1024
CAPS = {"image": 10 * MIB, "audio": 25 * MIB, "video": 50 * MIB, "pdf": 25 * MIB, "html": 2 * MIB, "file": 25 * MIB}
MAX_ITEMS = 24                    # P194: a gallery reply (14 pictures + 3 pages) is one page, not 8 cards and silence
PAGE_BYTES = 100 * MIB
KEEP_SECS = 24 * 3600
KEEP_N = 500
MAX_REFS = 64                     # candidate references examined per page
NAME_MAX = 128
REF_MAX = 512
WHYS = ("too_big", "outside", "secret", "type", "gone", "masked", "too_many", "private", "key_file", "key_content", "key_card")
# P211: toward the owner (Media.scan = the owner's own remote, the owner bot's private chat, mail to the owner's own address) a
# file is NOT run through the secret guess. The floor that stays is deterministic: a credential file by name / folder
# (KEY_DIRS, DENY_FILES …) → `key_file`, a text file holding a real key shape (OWNER_KINDS — never `high_entropy`) →
# `key_content`; an explicit link to either becomes a secret pickup card (`key_card`, owner_route.offer_files) instead of
# being dropped. agentj's own per-folder workspace (`.agentj/`, where an Agent may keep an outbox) and `.git` are not secrets:
# open to the owner, `private` to anyone else. Everyone else (owner=False: friends, contacts, groups) keeps the full scan.
# P194: a reference whose path a privacy placeholder replaced (the reply text was masked before the scan) is `masked`, never
# `gone` — the file may be right there; one past MAX_ITEMS is `too_many`, never silently dropped.
_PLACEHOLDER = re.compile(r"<(?:redacted|email|phone|private-ip|host|user)>")
SCAN_SLICE = MIB                  # the secret scan reads text in slices of this much …
SCAN_OVERLAP = 8 * 1024           # … overlapping by this much, so a token across a boundary is still seen

# extension → (kind, MIME). The MIME the phone gets is ours, never the file's claim; the leading bytes must agree (_sniff).
KINDS = {
    ".png": ("image", "image/png"), ".jpg": ("image", "image/jpeg"), ".jpeg": ("image", "image/jpeg"),
    ".gif": ("image", "image/gif"), ".webp": ("image", "image/webp"), ".svg": ("image", "image/svg+xml"),
    ".mp3": ("audio", "audio/mpeg"), ".m4a": ("audio", "audio/mp4"), ".aac": ("audio", "audio/aac"),
    ".ogg": ("audio", "audio/ogg"), ".oga": ("audio", "audio/ogg"), ".opus": ("audio", "audio/ogg"),
    ".wav": ("audio", "audio/wav"),
    ".mp4": ("video", "video/mp4"), ".m4v": ("video", "video/mp4"), ".mov": ("video", "video/quicktime"),
    ".webm": ("video", "video/webm"),
    ".pdf": ("pdf", "application/pdf"),
    ".html": ("html", "text/html"), ".htm": ("html", "text/html"),
    ".txt": ("file", "text/plain"), ".log": ("file", "text/plain"), ".md": ("file", "text/markdown"),
    ".markdown": ("file", "text/markdown"), ".csv": ("file", "text/csv"), ".tsv": ("file", "text/tab-separated-values"),
    ".json": ("file", "application/json"), ".jsonl": ("file", "application/json"), ".xml": ("file", "application/xml"),
    ".yaml": ("file", "application/yaml"), ".yml": ("file", "application/yaml"), ".toml": ("file", "application/toml"),
    ".zip": ("file", "application/zip"), ".gz": ("file", "application/gzip"), ".tgz": ("file", "application/gzip"),
    ".docx": ("file", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ".xlsx": ("file", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ".pptx": ("file", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    ".odt": ("file", "application/vnd.oasis.opendocument.text"),
    ".ods": ("file", "application/vnd.oasis.opendocument.spreadsheet"),
    ".epub": ("file", "application/epub+zip"),
    # P184: what the Agent produces and the owner may want on the phone / by e-mail — contacts, calendars, mail, legacy
    # Office, OpenDocument slides, RTF, archives. Text kinds go through the same secret scan (TEXTY).
    ".vcf": ("file", "text/vcard"), ".vcard": ("file", "text/vcard"), ".ics": ("file", "text/calendar"),
    ".eml": ("file", "message/rfc822"), ".mbox": ("file", "application/mbox"), ".rtf": ("file", "application/rtf"),
    ".doc": ("file", "application/msword"), ".xls": ("file", "application/vnd.ms-excel"),
    ".ppt": ("file", "application/vnd.ms-powerpoint"), ".odp": ("file", "application/vnd.oasis.opendocument.presentation"),
    ".tar": ("file", "application/x-tar"), ".7z": ("file", "application/x-7z-compressed"),
}
ZIPS = (".zip", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".epub")
OLE = (".doc", ".xls", ".ppt")
DELIVERABLE = {*ZIPS, *OLE, ".gz", ".tgz", ".tar", ".7z", ".csv", ".tsv", ".vcf", ".vcard", ".ics", ".eml", ".rtf"}   # implicit refs
TEXTY = {"image/svg+xml", "text/html"} | {m for k, m in KINDS.values() if k == "file" and not m.startswith(("application/zip",
         "application/gzip", "application/vnd", "application/epub", "application/msword", "application/x-tar", "application/x-7z"))}

KEY_DIRS = {"credentials", ".ssh", ".aws", ".gnupg"}
INNER_DIRS = {".agentj", ".git"}
DENY_DIRS = KEY_DIRS | INNER_DIRS
OWNER_KINDS = taskspec.SECRET_KINDS - {"high_entropy"}
CARD_MAX = 256 * 1024             # = secret_out.MAX_FILE: a credential file larger than this cannot ride a pickup card
DENY_FILES = {".env", ".netrc", ".npmrc", ".pypirc"}
DENY_SUFFIX = (".key", ".pem", ".p12", ".pfx")
DENY_PREFIX = (".env.", "id_rsa", "id_ed25519")

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_MDLINK = re.compile(r"(!?)\[((?:[^\[\]\n]|\[[^\[\]\n]*\])*)\]\(\s*(<[^>\n]+>|[^\s()]+(?:\([^\s()]*\)[^\s()]*)*)"
                     r"(?:\s+(?:\"[^\"\n]*\"|'[^'\n]*'))?\s*\)")
_TICK = re.compile(r"`([^`\n]{1,1024})`")
_BARE = re.compile(r"(?<![\w/~.:@%-])((?:file://|~|\.{1,2})?/[^\s`'\"<>()\[\]{}|*\u3000-\u303f\uff00-\uffef]+"
                   r"|[\w.-]+/[^\s`'\"<>()\[\]{}|*\u3000-\u303f\uff00-\uffef]+)")   # CJK punctuation ends a path
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_TRAIL = ".,;:!?。，；：！？、）》」』\"'"
_DATA_URI = re.compile(r"data:[\w.+-]+/[\w.+-]+(?:;[\w=.+-]+)*,[^\"'\s)<>]*", re.I)
_UNESC = re.compile(r"\\([!-/:-@\[-`{-~])")


class Gone(Exception):
    """The file vanished, changed or was swapped since the scan, or the mid is unknown / expired: answered `gone`."""


@dataclass
class Ref:
    text: str            # as written in the reply (the phone matches an inline `![](…)` against it)
    path: str            # the path it names (decoded)
    explicit: bool       # a Markdown image / link


def _strip_fences(text: str) -> str:
    out, fence = [], None
    for line in text.split("\n"):
        m = _FENCE.match(line)
        if fence is None and m:
            fence = m.group(1)[0] * len(m.group(1))
            out.append("")
            continue
        if fence is not None:
            if line.strip().startswith(fence) and set(line.strip()) <= {fence[0]}:
                fence = None
            out.append("")
            continue
        out.append(line)
    return "\n".join(out)


def _local(dest: str) -> str | None:
    """A Markdown destination / bare token → the local path it names, or None (http(s), data:, mailto:, any other scheme)."""
    d = dest.strip()
    if d.startswith("<") and d.endswith(">"):
        d = d[1:-1].strip()
    d = _UNESC.sub(r"\1", d)
    if not d or d.startswith("//"):
        return None
    if d.lower().startswith("file:"):
        u = urllib.parse.urlsplit(d)
        if u.netloc not in ("", "localhost") or not u.path:
            return None
        return urllib.parse.unquote(u.path)
    if _SCHEME.match(d):
        return None
    return d


def refs(text: str) -> list[Ref]:
    """Every local file reference in a reply, in reading order, each path once (explicit wins), at most MAX_REFS."""
    s = _strip_fences(text or "")
    hits: list[tuple[int, str, str, bool]] = []          # (position, as written, path, explicit)

    def link(m):
        p = _local(m.group(3))
        if p:
            hits.append((m.start(), m.group(3), p, True))
        return " " * len(m.group(0))
    s = _MDLINK.sub(link, s)

    def tick(m):
        c = m.group(1).strip()
        if c and ("/" in c or re.fullmatch(r"[^/\s]+\.[A-Za-z0-9]{1,8}", c)) and "://" not in c.replace("file://", ""):
            p = _local(c)
            if p:
                hits.append((m.start(), c, p, False))
        return " " * len(m.group(0))
    s = _TICK.sub(tick, s)
    for m in _BARE.finditer(s):
        tok = m.group(1).rstrip(_TRAIL)
        if tok and "://" not in tok.replace("file://", ""):
            p = _local(tok)
            if p:
                hits.append((m.start(), tok, p, False))
    found: list[Ref] = []
    seen: dict[str, int] = {}
    for _, raw, path, explicit in sorted(hits, key=lambda h: h[0]):
        if "\x00" in path:
            continue
        if path in seen:
            if explicit and not found[seen[path]].explicit:
                found[seen[path]] = Ref(raw[:REF_MAX], path, True)
            continue
        if len(found) >= MAX_REFS:
            break
        seen[path] = len(found)
        found.append(Ref(raw[:REF_MAX], path, explicit))
    return found


# ------------------------------------------------------------------ path safety
def _denied(parts, dirs=DENY_DIRS) -> bool:
    for i, p in enumerate(parts):
        low = p.lower()
        if low in dirs:
            return True
        if i == len(parts) - 1 and (low in DENY_FILES or low.endswith(DENY_SUFFIX) or low.startswith(DENY_PREFIX)):
            return True
    return False


def _inner(parts) -> bool:
    return any(p.lower() in INNER_DIRS for p in parts)


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def open_rel(root: str, parts: list[str]) -> int:
    """fd of `root/parts…` without following any link below root (each directory O_NOFOLLOW | O_DIRECTORY relative to
    the one before, the file O_NOFOLLOW | O_NONBLOCK — a FIFO never blocks). Raises Gone on a link / anything missing."""
    cloexec = getattr(os, "O_CLOEXEC", 0)
    try:
        d = os.open(root, os.O_RDONLY | os.O_DIRECTORY | cloexec)
    except OSError:
        raise Gone("root") from None
    try:
        for name in parts[:-1]:
            try:
                nd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | cloexec, dir_fd=d)
            except OSError:
                raise Gone(name) from None
            os.close(d)
            d = nd
        try:
            return os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | cloexec, dir_fd=d)
        except OSError as e:
            raise Gone(errno.errorcode.get(e.errno, "open")) from None
    finally:
        os.close(d)


def _sniff(mime: str, ext: str, head: bytes) -> bool:
    """Leading bytes agree with the type the extension names."""
    if mime in ("image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf"):
        return uploads.magic_ok(mime, head)
    if mime == "image/svg+xml":
        return b"\x00" not in head and re.search(rb"<svg[\s>]", head[:8192], re.I) is not None
    if mime == "audio/mpeg":
        return head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0)
    if mime == "audio/aac":
        return head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xF6 == 0xF0)
    if mime in ("audio/mp4", "video/mp4", "video/quicktime"):
        return head[4:8] == b"ftyp" or (mime == "video/quicktime" and head[4:8] in (b"moov", b"mdat", b"wide", b"free"))
    if mime == "audio/ogg":
        return head[:4] == b"OggS"
    if mime == "audio/wav":
        return head[:4] == b"RIFF" and head[8:12] == b"WAVE"
    if mime == "video/webm":
        return head[:4] == b"\x1a\x45\xdf\xa3"
    if ext in ZIPS:
        return head[:4] in (b"PK\x03\x04", b"PK\x05\x06")
    if ext in (".gz", ".tgz"):
        return head[:2] == b"\x1f\x8b"
    if ext in OLE:
        return head[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    if ext == ".7z":
        return head[:6] == b"7z\xbc\xaf\x27\x1c"
    if ext == ".tar":
        return head[257:262] == b"ustar"
    if ext == ".rtf":
        return head[:5] == b"{\\rtf"
    return b"\x00" not in head                                   # text: html, txt, md, csv, json …


def _hash_and_scan(fd: int, size: int, texty: bool, html: bool, owner: bool = False) -> tuple[str, bool]:
    """(sha256 hex, secret?) of the whole file read from fd (from offset 0)."""
    h = hashlib.sha256()
    tail = ""
    secret = False
    buf = bytearray()
    pos = 0
    while True:
        b = os.pread(fd, SCAN_SLICE, pos)
        if not b:
            break
        pos += len(b)
        h.update(b)
        if texty and not secret:
            buf += b
            if len(buf) >= SCAN_SLICE:
                piece = tail + bytes(buf).decode("utf-8", "replace")
                buf.clear()
                secret = _secret(piece, html, owner)
                tail = piece[-SCAN_OVERLAP:]
    if texty and not secret and (buf or tail):
        secret = _secret(tail + bytes(buf).decode("utf-8", "replace"), html, owner)
    if pos != size:
        raise Gone("size")
    return h.hexdigest(), secret


def _secret(text: str, html: bool, owner: bool = False) -> bool:
    if html:
        text = _DATA_URI.sub("data:", text)
    if owner:                     # P211: a real key shape only — the high-entropy guess never blocks the owner's own file
        from .privacy import redact_with_report
        return any(k in OWNER_KINDS for k in redact_with_report(text)[1])
    return taskspec.secret_like(text)


# ------------------------------------------------------------------ the table and the scan
class Media:
    def __init__(self, st, workdir: str | None, clock=time.time):
        self.st, self.workdir, self.clock = st, workdir, clock
        self.path = st.root / "media.json"
        self.lock = threading.Lock()
        self.table: dict[str, dict] = {}
        try:
            t = json.loads(self.path.read_text())
            if isinstance(t, dict):
                self.table = {k: v for k, v in t.items() if wire.is_id22(k) and isinstance(v, dict)}
        except (OSError, ValueError):
            pass
        self._prune()

    def _prune(self) -> None:
        now = self.clock()
        live = sorted(((v.get("ts", 0), k) for k, v in self.table.items() if now - v.get("ts", 0) < KEEP_SECS), reverse=True)
        self.table = {k: self.table[k] for _, k in sorted(live[:KEEP_N])}

    def _save(self) -> None:
        try:
            self.st.write_private(self.path, json.dumps(self.table, ensure_ascii=False).encode())
        except OSError:
            self.st.log("media_table_fail")

    def check(self, ref: Ref, owner: bool = False) -> tuple[dict | None, str | None]:
        """One reference → (record, None) | (None, why) | (None, None) = not a file reference (silently ignored).
        owner=True (P211): the destination is the owner's own channel — see WHYS. A credential file / a text file with a real
        key → (record, "key_file" | "key_content"): the record (hashed, re-readable) is what a pickup card would carry."""
        root = self.workdir
        if not root:
            return None, None
        try:
            rroot = os.path.realpath(root)
        except OSError:
            return None, None
        p = ref.path
        if p == "~" or p.startswith("~/"):
            p = os.path.expanduser(p)
        raw = os.path.normpath(p if os.path.isabs(p) else os.path.join(root, p))
        quiet = not ref.explicit
        if not os.path.lexists(raw):
            return None, (None if quiet else "gone")
        real = os.path.realpath(raw)
        state = os.path.realpath(str(self.st.root))
        if _inside(real, state):
            return None, "private"
        if not _inside(real, rroot):
            return None, (None if quiet else "outside")
        raw_parts = os.path.relpath(raw, os.path.normpath(root)).split(os.sep) if _inside(raw, os.path.normpath(root)) \
            else raw.split(os.sep)
        parts = os.path.relpath(real, rroot).split(os.sep)
        if parts == ["."]:
            return None, None
        keyish = _denied(parts, KEY_DIRS) or _denied(raw_parts, KEY_DIRS)
        if not owner and (keyish or _inner(parts) or _inner(raw_parts)):
            return None, ("secret" if keyish else "private")
        if keyish and quiet:
            return None, None             # owner: the Agent naming `.env` in passing is not sending it
        ext = os.path.splitext(parts[-1])[1].lower()
        kind, mime = KINDS.get(ext, (None, None))
        try:
            fd = open_rel(rroot, parts)
        except Gone:
            return None, (None if quiet else "gone")
        try:
            s = os.fstat(fd)
            if not stat.S_ISREG(s.st_mode):
                return None, None                                 # a folder, a device, a FIFO: not an attachment
            if s.st_uid != os.getuid():
                return None, (None if quiet else "outside")
            if keyish:                    # owner: never sent as a file — a pickup card when it fits, else a clear note
                if s.st_size > CARD_MAX or s.st_size == 0:
                    return {"name": parts[-1][:NAME_MAX], "bytes": s.st_size}, "key_file"
                sha, _ = _hash_and_scan(fd, s.st_size, False, False)
                return self._rec(parts, "application/octet-stream", "file", s, sha, ref, rroot), "key_file"
            if kind is None or (quiet and kind == "file" and ext not in DELIVERABLE):
                return None, (None if quiet else "type")
            if not _sniff(mime, ext, os.pread(fd, 8192, 0)):
                return None, "type"
            if s.st_size > CAPS[kind]:
                return {"name": parts[-1], "bytes": s.st_size}, "too_big"
            if s.st_size == 0:
                return None, None
            # binary media (typed by leading bytes above) is never read as text: only TEXTY kinds are scanned
            sha, secret = _hash_and_scan(fd, s.st_size, mime in TEXTY, mime in ("text/html", "image/svg+xml"), owner)
            if secret:
                if not owner:
                    return None, "secret"
                rec = self._rec(parts, mime, kind, s, sha, ref, rroot)
                return (rec if s.st_size <= CARD_MAX else {"name": rec["name"], "bytes": s.st_size}), "key_content"
        except Gone:
            return None, "gone"
        finally:
            os.close(fd)
        return self._rec(parts, mime, kind, s, sha, ref, rroot), None

    @staticmethod
    def _rec(parts, mime, kind, s, sha, ref, rroot) -> dict:
        return {"name": parts[-1][:NAME_MAX], "mime": mime, "kind": kind, "bytes": s.st_size, "sha256": sha,
                "ref": ref.text, "root": rroot, "parts": parts, "dev": s.st_dev, "ino": s.st_ino,
                "mtime": s.st_mtime_ns}

    def strict(self, rec: dict) -> str | None:
        """P211: a record checked toward the owner, about to go to someone else (a contact's mail) → None when it also
        passes the strict checks (no secret guess hit, no `.agentj` / `.git`), else the why."""
        p = os.path.join(rec["root"], *rec["parts"])
        try:
            again, why = self.check(Ref(p, p, True), owner=False)
        except OSError:
            return "gone"
        if why:
            return why
        if not again or again.get("sha256") != rec.get("sha256"):
            return "gone"
        return None

    def scan(self, text: str, page: int) -> tuple[list[dict], list[dict]]:
        """A finished page's reply → (media, media_skip) as they go on the wire. Blocking: run it in a thread."""
        items, skips, _ = self.scan_keys(text, page)
        return items, skips

    def scan_keys(self, text: str, page: int) -> tuple[list[dict], list[dict], list[tuple[dict, dict]]]:
        """scan + the credential files an explicit link named: [(the skip on the wire, the record)] — serve offers each as a
        pickup card and turns that skip's why into `key_card` (P211). The page is the owner's: owner=True."""
        items, skips, keys, total, seen = [], [], [], 0, set()
        for ref in refs(text):
            name = os.path.basename(ref.path.rstrip("/"))[:NAME_MAX] or ref.path[:NAME_MAX]
            if _PLACEHOLDER.search(ref.path):
                if ref.explicit:
                    skips.append({"name": name, "why": "masked"})
                continue
            if len(items) >= MAX_ITEMS:
                if ref.explicit:
                    skips.append({"name": name, "why": "too_many"})
                continue
            try:
                rec, why = self.check(ref, owner=True)
            except OSError:
                rec, why = None, None
            if why:
                skip = {"name": name, "why": why, **({"bytes": rec["bytes"]} if rec and why == "too_big" else {})}
                skips.append(skip)
                if why in ("key_file", "key_content") and rec and "sha256" in rec and ref.explicit:
                    keys.append((skip, rec))
                continue
            if rec is None or (rec["dev"], rec["ino"]) in seen:
                continue
            if total + rec["bytes"] > PAGE_BYTES:
                skips.append({"name": rec["name"], "why": "too_big", "bytes": rec["bytes"]})
                continue
            seen.add((rec["dev"], rec["ino"]))
            total += rec["bytes"]
            mid = wire.b64u(secrets.token_bytes(16))
            with self.lock:
                self.table[mid] = {**rec, "page": page, "ts": self.clock()}
            items.append({"mid": mid, **{k: rec[k] for k in ("name", "mime", "kind", "bytes", "sha256", "ref")}})
        if items:
            with self.lock:
                self._prune()
                self._save()
        skips = skips[:MAX_ITEMS * 2]
        return items, skips, [(sk, r) for sk, r in keys if any(sk is x for x in skips)]

    def window(self, mid: str, o: int) -> list[tuple[int, bytes, bool]]:
        """≤ WINDOW chunks from offset o, re-opened safely and re-checked. Raises Gone."""
        with self.lock:
            rec = self.table.get(mid)
            if rec is None or self.clock() - rec.get("ts", 0) >= KEEP_SECS:
                raise Gone("mid")
            rec = dict(rec)
        size = rec["bytes"]
        if o < 0 or o >= size:
            raise Gone("offset")
        fd = open_rel(rec["root"], list(rec["parts"]))
        try:
            s = os.fstat(fd)
            if (not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or (s.st_dev, s.st_ino) != (rec["dev"], rec["ino"])
                    or s.st_size != size or s.st_mtime_ns != rec["mtime"]):
                raise Gone("changed")
            if o == 0:
                h = hashlib.sha256()
                pos = 0
                while pos < size:
                    b = os.pread(fd, SCAN_SLICE, pos)
                    if not b:
                        break
                    h.update(b)
                    pos += len(b)
                if h.hexdigest() != rec["sha256"]:
                    raise Gone("sha")
            out = []
            for i in range(WINDOW):
                off = o + i * CHUNK
                if off >= size:
                    break
                b = os.pread(fd, min(CHUNK, size - off), off)
                if len(b) != min(CHUNK, size - off):
                    raise Gone("short")
                out.append((off, b, off + len(b) >= size))
            return out
        finally:
            os.close(fd)

    def get(self, mid: str) -> dict | None:
        """P183: a live record (a copy) for the file-card → Telegram forward; None when unknown or past KEEP_SECS."""
        with self.lock:
            rec = self.table.get(mid)
            if rec is None or self.clock() - rec.get("ts", 0) >= KEEP_SECS:
                return None
            return dict(rec)

    def known(self, mid: str) -> bool:
        with self.lock:
            return mid in self.table
