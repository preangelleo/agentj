"""Private file cards from local Agent replies over Noise (PROTOCOL §13).
No HTTP fetches. Written and resolved paths obey owner/outsider policy; credential files use secret pickup, never plain
cards. Open through an O_NOFOLLOW dir-fd chain; require regular user-owned files and recheck inode/size/mtime on every
read. Type, per-file/page limits and secret checks gate offers. A zero-offset read rehashes bytes; the phone verifies
SHA-256. Persist bounded private copies for history; changed sources cannot replace scanned bytes.
Masked copies never fall back to source; refused contents never leave.

Protocol/history detail: agentjarvis/ARCHITECTURE.md, P0261 source notes / media.py.
"""
from __future__ import annotations

import contextlib
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

from . import privacy, taskspec, uploads, wire

CHUNK = 45_056                    # raw bytes per media_chunk → 60 075 base64url chars → one 60 160-byte padded frame (§10.3)
WINDOW = 8                        # chunks answered per media_get (the phone asks for the next window when it has them)
FPS = 22                          # host → device pacing, the §10.3 boosted rate (≤ 22 frames / s)
MIB = 1024 * 1024
CAPS = {"image": 10 * MIB, "audio": 25 * MIB, "video": 50 * MIB, "pdf": 25 * MIB, "html": 2 * MIB, "file": 25 * MIB}
MAX_ITEMS = 24                    # P194: a gallery reply (14 pictures + 3 pages) is one page, not 8 cards and silence
PAGE_BYTES = 100 * MIB
KEEP_SECS = 24 * 3600              # a record served from its source file (no stored copy)
KEEP_N = 2000                      # P573: ≥ the history's 500 pages × a few cards each
STORE_DIR = "media_store"          # P573: content-addressed private copies, <sha256 hex> each
STORE_SECS = 30 * 24 * 3600        # a record with a stored copy
STORE_BYTES = 512 * MIB            # total bytes of stored copies
STORE_GRACE = 3600                 # s: an unreferenced copy (a mail attachment, a racing scan) stays this long
COPY_MAX = 25 * MIB                # larger items (videos) stay source-backed (24 h, as before)
MASK_MAX = 4 * MIB                 # a text file larger than this is not masked: the old `key_content` note / card
MAX_REFS = 64                     # candidate references examined per page
NAME_MAX = 128
REF_MAX = 512
WHYS = ("too_big", "outside", "secret", "type", "gone", "masked", "too_many", "private", "key_file", "key_content", "key_card")
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
    ".vcf": ("file", "text/vcard"), ".vcard": ("file", "text/vcard"), ".ics": ("file", "text/calendar"),
    ".eml": ("file", "message/rfc822"), ".mbox": ("file", "application/mbox"), ".rtf": ("file", "application/rtf"),
    ".doc": ("file", "application/msword"), ".xls": ("file", "application/vnd.ms-excel"),
    ".ppt": ("file", "application/vnd.ms-powerpoint"), ".odp": ("file", "application/vnd.oasis.opendocument.presentation"),
    ".tar": ("file", "application/x-tar"), ".7z": ("file", "application/x-7z-compressed"),
    **{e: ("file", "text/plain") for e in (".sh", ".bash", ".zsh", ".fish", ".ps1", ".psm1", ".bat", ".cmd", ".py", ".js",
                                          ".mjs", ".ts", ".rb", ".pl", ".lua", ".sql", ".diff", ".patch")},
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


_HEX64 = re.compile(r"[0-9a-f]{64}")


def _blob_fd(rec: dict) -> int | None:
    """P573: fd of the record's stored copy (O_NOFOLLOW, a regular file of this user, the recorded size), or None."""
    sha, store = rec.get("blob"), rec.get("store")
    if not (isinstance(sha, str) and _HEX64.fullmatch(sha) and isinstance(store, str) and os.path.isabs(store)):
        return None
    try:
        fd = os.open(os.path.join(store, sha), os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        return None
    try:
        s = os.fstat(fd)
        if stat.S_ISREG(s.st_mode) and s.st_uid == os.getuid() and s.st_size == rec.get("bytes"):
            return fd
    except OSError:
        pass
    os.close(fd)
    return None


def read_blob(rec: dict) -> bytes | None:
    """P573: the whole stored copy, SHA-256 checked, or None (no copy / gone / not the one recorded)."""
    fd = _blob_fd(rec)
    if fd is None:
        return None
    try:
        out = bytearray()
        while len(out) < rec["bytes"]:
            b = os.pread(fd, SCAN_SLICE, len(out))
            if not b:
                break
            out += b
    finally:
        os.close(fd)
    if len(out) != rec["bytes"] or hashlib.sha256(out).hexdigest() != rec.get("sha256"):
        return None
    return bytes(out)


def _data_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _DATA_URI.finditer(text)]


def mask_text(text: str, html: bool = False) -> tuple[str, list[tuple[str, str]]]:
    """Mask owner-file credential spans (entropy=False), preserving HTML/SVG data images; return text and value pairs."""
    found = privacy.credentials(text, entropy=False)
    if html:
        data = _data_spans(text)
        found = [f for f in found if not any(a < f[3] and f[2] < b for a, b in data)]
    out, at, values = [], 0, []
    for kind, value, s, e in found:
        out.append(text[at:s])
        out.append(privacy.R)
        values.append((kind, value))
        at = e
    out.append(text[at:])
    return "".join(out), values


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


def _dot_part(parts: list[str]) -> bool:
    """Reject hidden home subfolders except project worktrees; these must never widen the current-folder scan."""
    for i, x in enumerate(parts):
        if not x.startswith("."):
            continue
        if i > 0 and (x == ".worktrees" or (x == ".claude" and parts[i + 1:i + 2] == ["worktrees"])):
            continue
        return True
    return False


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def open_rel(root: str, parts: list[str]) -> int:
    """Open root-relative directories/file with O_NOFOLLOW and O_NONBLOCK; Gone for links or missing components."""
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


def _hash_and_scan(fd: int, size: int, texty: bool, html: bool, owner: bool = False, where: dict | None = None) -> tuple[str, bool]:
    """Hash and scan the whole fd from zero; populate where with a masked location hint for multiline hits."""
    h = hashlib.sha256()
    tail = ""
    secret = False
    buf = bytearray()
    pos = 0
    lines = 0                     # newlines before the current piece's start
    nl, last = 0, b""             # newlines in the whole file, its last byte
    while True:
        b = os.pread(fd, SCAN_SLICE, pos)
        if not b:
            break
        pos += len(b)
        h.update(b)
        nl += b.count(b"\n")
        last = b[-1:]
        if texty and not secret:
            buf += b
            if len(buf) >= SCAN_SLICE:
                piece = tail + bytes(buf).decode("utf-8", "replace")
                buf.clear()
                secret = _secret(piece, html, owner)
                if secret:
                    _where(where, piece, html, owner, lines)
                lines += piece.count("\n") - piece[-SCAN_OVERLAP:].count("\n")
                tail = piece[-SCAN_OVERLAP:]
    if texty and not secret and (buf or tail):
        piece = tail + bytes(buf).decode("utf-8", "replace")
        secret = _secret(piece, html, owner)
        if secret:
            _where(where, piece, html, owner, lines)
    if where and nl - (last == b"\n") < 1:
        where.clear()
    if pos != size:
        raise Gone("size")
    return h.hexdigest(), secret


def _where(out: dict | None, piece: str, html: bool, owner: bool, lines: int) -> None:
    if out is None:
        return
    if html:
        piece = _DATA_URI.sub("data:", piece)
    w = taskspec.secret_where(piece, owner=owner)
    if w:
        out.update({"line": lines + w["line"], "hint": w["hint"]})


def _secret(text: str, html: bool, owner: bool = False) -> bool:
    if html:
        text = _DATA_URI.sub("data:", text)
    if owner:                     # P211: a real key shape only — the high-entropy guess never blocks the owner's own file
        return taskspec.secret_like(text, owner=True)
    return taskspec.secret_like(text)


def _at(media, why: str) -> dict:
    """P424 #142: a `secret` / `key_content` note carries where — {"line": n, "hint": "aB…"} (§13 media_skip, optional)."""
    w = media.last_where() if why in ("secret", "key_content") else None
    return {"line": w["line"], "hint": w["hint"]} if w and isinstance(w.get("line"), int) else {}


# ------------------------------------------------------------------ the table and the scan
class Media:
    def __init__(self, st, workdir: str | None, clock=time.time, cwd=None):
        self.st, self.workdir, self.clock = st, workdir, clock
        self.cwd = cwd                # P424 #64: () → the session's current folder (the Agent's shell may `cd`), or None
        self._tl = threading.local()  # P424 #142: this thread's last secret hit position (`last_where`)
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

    @staticmethod
    def _live(rec: dict, now: float) -> bool:
        """P573: a record with a stored copy lives STORE_SECS; one served from its source file KEEP_SECS."""
        return now - rec.get("ts", 0) < (STORE_SECS if rec.get("blob") else KEEP_SECS)

    def _prune(self) -> None:
        now = self.clock()
        live = sorted(((v.get("ts", 0), k) for k, v in self.table.items() if self._live(v, now)), reverse=True)
        self.table = {k: self.table[k] for _, k in sorted(live[:KEEP_N])}

    def _save(self) -> None:
        try:
            self.st.write_private(self.path, json.dumps(self.table, ensure_ascii=False).encode())
        except OSError:
            self.st.log("media_table_fail")

    def current(self) -> str | None:
        """Validated current folder, or None; widens owner scan only.
    No links, credential/hidden home folders, home/ancestors or state overlap; configured root stays the floor."""
        f = self.cwd
        if not callable(f):
            return None
        try:
            c = f()
        except Exception:  # noqa: BLE001 — a provider that fails widens nothing
            return None
        if not isinstance(c, str) or not c or "\x00" in c or not os.path.isabs(c):
            return None
        try:
            n = os.path.normpath(c)
            r = os.path.realpath(n)
            if r != n or not os.path.isdir(r) or os.stat(r).st_uid != os.getuid():
                return None
            home = os.path.realpath(os.path.expanduser("~"))
            state = os.path.realpath(str(self.st.root))
        except OSError:
            return None
        if r == os.sep or _inside(home, r) or _inside(r, state) or _inside(state, r):
            return None
        if any(x.lower() in KEY_DIRS for x in r.split(os.sep)):
            return None
        if _inside(r, home) and _dot_part(os.path.relpath(r, home).split(os.sep)):
            return None
        return r

    def check(self, ref: Ref, owner: bool = False, root: str | None = None, mask: bool = False) -> tuple[dict | None, str | None]:
        """Reference → (record, why), or (None, None) when implicit/nonfile.
    Owner roots include validated current; others use configured root. Credential files keep pickup cards.
    mask=True emits only a private masked copy; transient values leave via last_values, never the record."""
        self._tl.where = None
        self._tl.values = None
        explicit_root = root is not None
        root = root or self.workdir
        if not root:
            return None, None
        try:
            rroot = os.path.realpath(root)
        except OSError:
            return None, None
        here = self.current() if owner and not explicit_root else None
        roots = [(root, rroot)] + ([(here, here)] if here and here != rroot else [])
        p = ref.path
        if p == "~" or p.startswith("~/"):
            p = os.path.expanduser(p)
        if os.path.isabs(p):
            raw = os.path.normpath(p)
        else:
            raw = os.path.normpath(os.path.join(root, p))
            if here and os.path.lexists(os.path.join(here, p)):
                raw = os.path.normpath(os.path.join(here, p))
        quiet = not ref.explicit
        if not os.path.lexists(raw):
            return None, (None if quiet else "gone")
        real = os.path.realpath(raw)
        state = os.path.realpath(str(self.st.root))
        if _inside(real, state):
            return None, "private"
        hit = next(((g, r) for g, r in roots if _inside(real, r)), None)
        if hit is None:
            return None, (None if quiet else "outside")
        root, rroot = hit
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
            at: dict = {}
            sha, secret = _hash_and_scan(fd, s.st_size, mime in TEXTY, mime in ("text/html", "image/svg+xml"), owner, at)
            if secret:
                self._tl.where = at or None           # P424 #142: the note says which line (scan_keys / tg_media read it)
                if not owner:
                    return None, "secret"
                rec = self._rec(parts, mime, kind, s, sha, ref, rroot)
                if mask and mime in TEXTY and s.st_size <= MASK_MAX:
                    masked = self._mask(fd, s.st_size, sha, rec, mime in ("text/html", "image/svg+xml"))
                    if masked is not None:
                        return masked, None
                return (rec if s.st_size <= CARD_MAX else {"name": rec["name"], "bytes": s.st_size}), "key_content"
        except Gone:
            return None, "gone"
        finally:
            os.close(fd)
        return self._rec(parts, mime, kind, s, sha, ref, rroot), None

    def _mask(self, fd: int, size: int, sha: str, rec: dict, html: bool) -> dict | None:
        """Store a masked UTF-8 copy of the scanned bytes by its hash; None preserves the key_content refusal."""
        raw = bytearray()
        while len(raw) < size:
            b = os.pread(fd, SCAN_SLICE, len(raw))
            if not b:
                break
            raw += b
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != sha:
            raise Gone("changed")
        try:
            text = bytes(raw).decode("utf-8")
        except UnicodeDecodeError:
            return None
        finally:
            raw = bytearray()
        out, values = mask_text(text, html)
        if not values:
            return None
        data = out.encode("utf-8")
        msha = self._put(data)
        if msha is None:
            return None
        self._tl.values = values
        return {**rec, "bytes": len(data), "sha256": msha, "blob": msha, "store": str(self.store), "masked": len(values)}

    def last_values(self) -> list[tuple[str, str]]:
        """P573: the (kind, value) list the last `check(mask=True)` on this thread took out of a file — once, then cleared."""
        v = getattr(self._tl, "values", None) or []
        self._tl.values = None
        return list(v)

    # ------------------------------------------------------------ P573: the private store
    @property
    def store(self):
        return self.st.root / STORE_DIR

    def _store_dir(self) -> str:
        d = str(self.store)
        os.makedirs(d, mode=0o700, exist_ok=True)
        os.chmod(d, 0o700)
        return d

    def _put(self, data: bytes) -> str | None:
        """Bytes → `<store>/<sha256>` (0600, atomic). Blocking: scan threads only. → the hex digest, or None."""
        sha = hashlib.sha256(data).hexdigest()
        try:
            d = self._store_dir()
            path = os.path.join(d, sha)
            if self._have(path, len(data)):
                return sha
            tmp = os.path.join(d, ".tmp-" + secrets.token_hex(8))
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                view = memoryview(data)
                while view:
                    view = view[os.write(fd, view):]
            finally:
                os.close(fd)
            os.replace(tmp, path)
            return sha
        except OSError:
            self.st.log("media_store_fail")
            return None

    @staticmethod
    def _have(path: str, size: int) -> bool:
        try:
            s = os.lstat(path)
        except OSError:
            return False
        if stat.S_ISREG(s.st_mode) and s.st_size == size and s.st_uid == os.getuid():
            os.utime(path, follow_symlinks=False)      # a fresh mtime: the grace period starts again
            return True
        return False

    def _persist(self, rec: dict) -> None:
        """A scanned item (≤ COPY_MAX) → its content copied into the store, streamed and re-hashed through the same
        no-follow open as the scan; `blob` / `store` set only when the copy is exactly what was scanned."""
        if rec.get("blob") or rec.get("bytes", 0) > COPY_MAX:
            return
        sha = rec["sha256"]
        try:
            d = self._store_dir()
            path = os.path.join(d, sha)
            if not self._have(path, rec["bytes"]):
                src = open_rel(rec["root"], list(rec["parts"]))
                tmp = os.path.join(d, ".tmp-" + secrets.token_hex(8))
                try:
                    s = os.fstat(src)
                    if (not stat.S_ISREG(s.st_mode) or (s.st_dev, s.st_ino) != (rec["dev"], rec["ino"])
                            or s.st_size != rec["bytes"] or s.st_mtime_ns != rec["mtime"]):
                        return
                    out = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                    h, pos = hashlib.sha256(), 0
                    try:
                        while pos < rec["bytes"]:
                            b = os.pread(src, SCAN_SLICE, pos)
                            if not b:
                                break
                            h.update(b)
                            view = memoryview(b)
                            while view:
                                view = view[os.write(out, view):]
                            pos += len(b)
                    finally:
                        os.close(out)
                    if pos != rec["bytes"] or h.hexdigest() != sha:
                        return
                    os.replace(tmp, path)
                finally:
                    os.close(src)
                    if os.path.lexists(tmp):
                        os.unlink(tmp)
        except (OSError, Gone):
            return
        rec["blob"], rec["store"] = sha, str(self.store)

    def _gc(self) -> None:
        """Blocking (scan threads): copies no live record names go after STORE_GRACE; over STORE_BYTES the oldest
        records lose their copy (they fall back to the source file and KEEP_SECS)."""
        try:
            d = str(self.store)
            names = os.listdir(d)
        except OSError:
            return
        now_wall = time.time()
        with self.lock:
            refs: dict[str, float] = {}
            for v in self.table.values():
                b = v.get("blob")
                if isinstance(b, str):
                    refs[b] = max(refs.get(b, 0), v.get("ts", 0))
        sizes: dict[str, int] = {}
        for n in names:
            p = os.path.join(d, n)
            try:
                s = os.lstat(p)
            except OSError:
                continue
            if n not in refs and now_wall - s.st_mtime >= STORE_GRACE:
                with contextlib.suppress(OSError):
                    os.unlink(p)
                continue
            if n in refs:
                sizes[n] = s.st_size
        total, drop = sum(sizes.values()), []
        for n in sorted(sizes, key=lambda x: refs[x]):
            if total <= STORE_BYTES:
                break
            total -= sizes[n]
            drop.append(n)
        if drop:
            gone = set(drop)
            with self.lock:
                for v in self.table.values():
                    if v.get("blob") in gone:
                        v.pop("blob", None)
                        v.pop("store", None)
                self._prune()
                self._save()
            for n in drop:
                with contextlib.suppress(OSError):
                    os.unlink(os.path.join(d, n))

    def last_where(self) -> dict | None:
        """P424 #142: {"line", "hint"} of the secret this thread's last `check` refused (a masked two-character hint), or None."""
        return getattr(self._tl, "where", None)

    @staticmethod
    def _rec(parts, mime, kind, s, sha, ref, rroot) -> dict:
        return {"name": parts[-1][:NAME_MAX], "mime": mime, "kind": kind, "bytes": s.st_size, "sha256": sha,
                "ref": ref.text, "root": rroot, "parts": parts, "dev": s.st_dev, "ino": s.st_ino,
                "mtime": s.st_mtime_ns}

    def strict(self, rec: dict) -> str | None:
        """Recheck an owner record for outsider delivery: strict secrets and private folders still refuse."""
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
        """Owner scan plus explicit credential references as (skip, record) pairs for pickup cards."""
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
                rec, why = self.check(ref, owner=True, mask=True)
            except OSError:
                rec, why = None, None
            values = self.last_values()
            if why:
                skip = {"name": name, "why": why, **({"bytes": rec["bytes"]} if rec and why == "too_big" else {}),
                        **_at(self, why)}
                skips.append(skip)
                if why in ("key_file", "key_content") and rec and "sha256" in rec and ref.explicit:
                    keys.append((skip, rec))
                continue
            if rec is None or (rec["dev"], rec["ino"]) in seen:
                continue
            if values and ref.explicit:   # P573: the masked copy goes as a card; each value → its own pickup card
                keys.append((None, {"name": rec["name"], "sha256": rec["sha256"], "values": values}))
            if total + rec["bytes"] > PAGE_BYTES:
                skips.append({"name": rec["name"], "why": "too_big", "bytes": rec["bytes"]})
                continue
            seen.add((rec["dev"], rec["ino"]))
            total += rec["bytes"]
            self._persist(rec)            # P573: the card stays openable after 24 h / a changed source
            mid = wire.b64u(secrets.token_bytes(16))
            with self.lock:
                self.table[mid] = {**rec, "page": page, "ts": self.clock()}
            items.append({"mid": mid, **{k: rec[k] for k in ("name", "mime", "kind", "bytes", "sha256", "ref")}})
        if items:
            with self.lock:
                self._prune()
                self._save()
            self._gc()
        skips = skips[:MAX_ITEMS * 2]
        return items, skips, [(sk, r) for sk, r in keys if sk is None or any(sk is x for x in skips)]

    def register(self, rec: dict, page: int) -> dict:
        """P365: a file serve itself copied for the owner (notify.stage — private copy, already typed and checked) → the §13
        item of `page`; served by window() like any card."""
        mid = wire.b64u(secrets.token_bytes(16))
        with self.lock:
            self.table[mid] = {**rec, "page": page, "ts": self.clock()}
            self._prune()
            self._save()
        return {"mid": mid, **{k: rec[k] for k in ("name", "mime", "kind", "bytes", "sha256", "ref")}}

    def window(self, mid: str, o: int) -> list[tuple[int, bytes, bool]]:
        """≤ WINDOW chunks from offset o, re-opened safely and re-checked. Raises Gone."""
        with self.lock:
            rec = self.table.get(mid)
            if rec is None or not self._live(rec, self.clock()):
                raise Gone("mid")
            rec = dict(rec)
        size = rec["bytes"]
        if o < 0 or o >= size:
            raise Gone("offset")
        fd = _blob_fd(rec)                # P573: the stored copy first — it outlives the source file
        if fd is None:
            if rec.get("masked") or (rec.get("blob") and self.clock() - rec.get("ts", 0) >= KEEP_SECS):
                raise Gone("store")       # a masked copy never falls back to the unmasked source
            fd = open_rel(rec["root"], list(rec["parts"]))
            src = True
        else:
            src = False
        try:
            s = os.fstat(fd)
            if src and (not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or (s.st_dev, s.st_ino) != (rec["dev"], rec["ino"])
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
            if rec is None or not self._live(rec, self.clock()):
                return None
            return dict(rec)

    def known(self, mid: str) -> bool:
        with self.lock:
            return mid in self.table
