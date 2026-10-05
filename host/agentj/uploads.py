"""Blobs — files, photos and voice from the phone, end to end (PROTOCOL §10.3, PROMPT-33; relay `bridge/inbox.py` +
`compose.Stash`, now over the Noise session instead of HTTP).

The phone opens a blob (`blob_open`: purpose, name, MIME, size, SHA-256, origin), sends ≤ 45 056-byte chunks at the offset
the host asked for (`blob_ack.next`), and ends it (`blob_end`). The host keeps the partial in `<state>/uploads/<device>/
<bid>.part` (state dir: invisible to the fenced Agent) with its declared fields in `<bid>.json`, so after any reconnect — or a
serve restart — the same `blob_open` resumes where the bytes stop. At the end it checks the byte count, the SHA-256 and the
leading bytes against the declared type, then:
  * `att`: copies it into the Agent's inbox (inbox.place, §10.4) and keeps it *staged* — announced to nobody — until the same
    device's `say` claims it (compose) or STAGED_TTL ends (then the inbox file is deleted: nobody was ever told about it);
    a voice recording also keeps its verified bytes as `<bid>.voice` here, which say-time transcription reads (P33-C02);
  * `asr`: leaves the WAV in the uploads dir for the transcriber (serve) and deletes it after.
Every table is keyed by (device id, bid): another device can neither guess nor touch an id. Limits (both ends): size 1 …
25 MiB (asr ≤ 120 s of 16 kHz mono PCM16), ≤ 20 staged + ≤ 2 open per device, ≤ 200 MiB staged + partial per device, the
inbox ≤ 2 GiB. No signature (§10.15): an upload changes nothing the Agent sees until a `say` of the same device names it.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field

from . import inbox, wire

MAX_SIZE = 25 * 1024 * 1024
ASR_MAX = 44 + 120 * 16_000 * 2          # 3 840 044: 120 s of 16 kHz mono PCM16 + the 44-byte header
CHUNK = 45_056                           # raw bytes per chunk → 60 075 base64url chars → one 60 160-byte padded frame
ACK_EVERY = 4
MAX_STAGED = 20
MAX_OPEN = 2
DEVICE_BYTES = 200 * 1024 * 1024
STAGED_TTL = 30 * 60
PARTIAL_TTL = 30 * 60
NAME_MAX = 128
ORIGINS = ("file", "photo", "camera", "paste", "drop", "recording")
PURPOSES = ("att", "asr")
_SHA = re.compile(r"[0-9a-f]{64}")
_DEV = re.compile(r"[A-Za-z0-9_-]{16}")
WAV_MIMES = ("audio/wav", "audio/x-wav")
HEIF_BRANDS = (b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1", b"avif")


def magic_ok(mime: str, head: bytes) -> bool:
    """The leading bytes of images, PDF and WAV must match their declared type (§10.3); other types are not checked."""
    if mime == "image/jpeg":
        return head[:3] == b"\xff\xd8\xff"
    if mime == "image/png":
        return head[:8] == b"\x89PNG\r\n\x1a\n"
    if mime == "image/gif":
        return head[:6] in (b"GIF87a", b"GIF89a")
    if mime == "image/webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    if mime in ("image/heic", "image/heif"):
        return head[4:8] == b"ftyp" and head[8:12] in HEIF_BRANDS
    if mime == "video/mp4":
        return head[4:8] == b"ftyp"
    if mime == "application/pdf":
        return head[:5] == b"%PDF-"
    if mime in WAV_MIMES:
        return head[:4] == b"RIFF" and head[8:12] == b"WAVE"
    return True


def wav_ok(path: str) -> bool:
    """§10.9 strict check for an `asr` take: RIFF/WAVE, one `fmt ` PCM chunk (format 1, mono, 16 000 Hz, 16 bit), one `data`
    chunk whose length matches the file."""
    try:
        with open(path, "rb") as f:
            b = f.read(ASR_MAX + 1)
    except OSError:
        return False
    if len(b) < 44 or b[:4] != b"RIFF" or b[8:12] != b"WAVE" or int.from_bytes(b[4:8], "little") != len(b) - 8:
        return False
    i, fmt, data = 12, None, None
    while i + 8 <= len(b):
        cid, n = b[i:i + 4], int.from_bytes(b[i + 4:i + 8], "little")
        body = b[i + 8:i + 8 + n]
        if len(body) != n:
            return False
        if cid == b"fmt ":
            if fmt is not None:
                return False
            fmt = body
        elif cid == b"data":
            if data is not None:
                return False
            data = body
        i += 8 + n + (n & 1)
    if fmt is None or data is None or len(fmt) < 16:
        return False
    tag, ch, rate = int.from_bytes(fmt[0:2], "little"), int.from_bytes(fmt[2:4], "little"), int.from_bytes(fmt[4:8], "little")
    bits = int.from_bytes(fmt[14:16], "little")
    return tag == 1 and ch == 1 and rate == 16_000 and bits == 16 and len(data) % 2 == 0


@dataclass
class Blob:
    device: str
    bid: str
    purpose: str
    name: str
    mime: str
    size: int
    sha256: str
    origin: str
    secs: float | None = None
    created: float = field(default_factory=time.time)
    state: str = "open"            # open → staged (att) | asr (asr) ; staged → claimed → (committed: forgotten)
    have: int = 0
    since_ack: int = 0
    path: str = ""                 # the inbox path once staged
    expires: float = 0.0
    voice: str = ""                # a recording's private copy in the state dir (say-time transcription reads only this)

    @property
    def kind(self) -> str:
        return inbox.kind_of(self.mime)

    def fields(self) -> dict:
        return {"purpose": self.purpose, "name": self.name, "mime": self.mime, "size": self.size, "sha256": self.sha256,
                "origin": self.origin, "secs": self.secs}


class Refused(Exception):
    def __init__(self, why: str, att: list | None = None):
        super().__init__(why)
        self.why, self.att = why, att or []


class Uploads:
    def __init__(self, st, workdir: str | None, asr_off=lambda: False, clock=time.time):
        self.st, self.workdir, self.asr_off, self.clock = st, workdir, asr_off, clock
        self.root = st.root / "uploads"
        self.blobs: dict[tuple, Blob] = {}
        self._load()

    # ------------------------------------------------------------ storage
    def _dir(self, device: str):
        d = self.root / device
        self.root.mkdir(mode=0o700, exist_ok=True)
        d.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.root, 0o700)
        os.chmod(d, 0o700)
        return d

    def part_path(self, b: Blob):
        return self.root / b.device / f"{b.bid}.part"

    def _meta_path(self, b: Blob, staged=False):
        return self.root / b.device / (f"{b.bid}.staged.json" if staged else f"{b.bid}.json")

    def voice_path(self, b: Blob):
        return self.root / b.device / f"{b.bid}.voice"

    @staticmethod
    def is_recording(b: Blob) -> bool:
        return b.purpose == "att" and b.origin == "recording" and b.mime.startswith("audio/")

    def _save(self, b: Blob) -> None:
        self._dir(b.device)
        if b.state in ("staged", "claimed"):
            rec = {**b.fields(), "path": b.path, "expires": b.expires, "created": b.created, "voice": b.voice}
            self.st.write_private(self._meta_path(b, True), json.dumps(rec, ensure_ascii=False).encode())
        else:
            rec = {**b.fields(), "created": b.created}
            self.st.write_private(self._meta_path(b), json.dumps(rec, ensure_ascii=False).encode())

    def _forget(self, b: Blob, files: bool = True) -> None:
        self.blobs.pop((b.device, b.bid), None)
        for p in (self.part_path(b), self._meta_path(b), self._meta_path(b, True), self.voice_path(b)):
            if files or not str(p).endswith(".part"):
                with contextlib.suppress(OSError):
                    p.unlink()

    def _load(self) -> None:
        """Partials and staged blobs survive a serve restart (their files are on disk; the tables are rebuilt here)."""
        if not self.root.is_dir():
            return
        for d in self.root.iterdir():
            if not d.is_dir() or not _DEV.fullmatch(d.name):
                continue
            for meta in d.glob("*.json"):
                staged = meta.name.endswith(".staged.json")
                bid = meta.name[:-len(".staged.json")] if staged else meta.name[:-len(".json")]
                try:
                    r = json.loads(meta.read_text())
                    b = Blob(d.name, bid, r["purpose"], r["name"], r["mime"], int(r["size"]), r["sha256"], r["origin"],
                             r.get("secs"), float(r.get("created") or 0))
                except (OSError, ValueError, KeyError, TypeError):
                    with contextlib.suppress(OSError):
                        meta.unlink()
                    continue
                if not wire.is_id22(bid):
                    continue
                if staged:
                    b.state, b.path, b.expires = "staged", str(r.get("path") or ""), float(r.get("expires") or 0)
                    if not b.path or not os.path.isfile(b.path):
                        with contextlib.suppress(OSError):
                            meta.unlink()
                        continue
                    if r.get("voice") and self.voice_path(b).is_file():
                        b.voice = str(self.voice_path(b))
                elif b.purpose == "att":
                    p = self.part_path(b)
                    b.have = p.stat().st_size if p.exists() else 0
                else:
                    with contextlib.suppress(OSError):        # an asr take is not resumed across a restart
                        meta.unlink()
                        self.part_path(b).unlink()
                    continue
                self.blobs[(b.device, bid)] = b
            for part in d.glob("*.part"):                     # an asr take or a broken write without its record
                if (d.name, part.name[:-len(".part")]) not in self.blobs:
                    with contextlib.suppress(OSError):
                        part.unlink()
        self.sweep()

    # ------------------------------------------------------------ accounting
    def _mine(self, device: str) -> list[Blob]:
        return [b for (d, _), b in self.blobs.items() if d == device]

    def staged_paths(self) -> set[str]:
        return {b.path for b in self.blobs.values() if b.state in ("staged", "claimed") and b.path}

    def sweep(self) -> list[Blob]:
        """Staged blobs nobody claimed in time lose their inbox file (nobody was ever told about it); partials untouched for
        PARTIAL_TTL are deleted. Returns what expired."""
        now = self.clock()
        gone = []
        for b in list(self.blobs.values()):
            if b.state == "staged" and b.expires <= now:
                inbox.unlink_placed(b.path)
                self._forget(b)
                gone.append(b)
            elif b.state == "open":
                p = self.part_path(b)
                try:
                    last = max(p.stat().st_mtime, b.created) if p.exists() else b.created
                except OSError:
                    last = b.created
                if now - last > PARTIAL_TTL:
                    self._forget(b)
                    gone.append(b)
        return gone

    # ------------------------------------------------------------ the device's messages → answers
    @staticmethod
    def _err(bid, why) -> dict:
        return {"t": "blob_err", "bid": bid, "why": why}

    @staticmethod
    def _done_err(bid, why) -> dict:
        return {"t": "blob_done", "bid": bid, "ok": False, "why": why}

    def open(self, device: str, m: dict) -> list[dict]:
        bid = m.get("bid")
        if not wire.is_id22(bid):
            return []
        purpose, name, mime, size, sha, origin, secs = (m.get(k) for k in ("purpose", "name", "mime", "size", "sha256",
                                                                          "origin", "secs"))
        if purpose not in PURPOSES or not isinstance(name, str) or wire.text_problem(name, NAME_MAX) \
                or not isinstance(mime, str) or type(size) is not int or not isinstance(sha, str) or not _SHA.fullmatch(sha) \
                or origin not in ORIGINS or (secs is not None and (isinstance(secs, bool) or not isinstance(secs, (int, float))
                                                                    or not 0 <= secs <= 3600)):
            return [self._err(bid, "shape")]
        self.sweep()
        old = self.blobs.get((device, bid))
        want = {"purpose": purpose, "name": name, "mime": mime, "size": size, "sha256": sha, "origin": origin,
                "secs": secs}
        if old is not None:
            if old.state == "open" and old.fields() == want:          # resume: the bytes it already holds
                old.since_ack = 0
                return [{"t": "blob_ack", "bid": bid, "next": old.have}]
            if old.state in ("staged", "claimed") and old.fields() == want:   # its blob_done got lost: say it again
                return [{"t": "blob_done", "bid": bid, "ok": True, "kind": old.kind, "bytes": old.size}]
            if old.state == "open":
                self._forget(old)                                     # any field differs → start again from 0
            else:
                return [self._err(bid, "shape")]
        if mime not in inbox.ALLOWED or (purpose == "asr" and mime not in WAV_MIMES):
            return [self._err(bid, "type")]
        if size < 1:
            return [self._err(bid, "empty")]
        if size > (ASR_MAX if purpose == "asr" else MAX_SIZE):
            return [self._err(bid, "too_big")]
        if purpose == "asr" and self.asr_off():
            return [self._err(bid, "asr_off")]
        mine = self._mine(device)
        # claimed blobs (a queued say holds them) count until delivered / withdrawn (P33-X06)
        if sum(1 for b in mine if b.state == "open") >= MAX_OPEN \
                or sum(1 for b in mine if b.state in ("staged", "claimed")) >= MAX_STAGED:
            return [self._err(bid, "too_many")]
        if sum(b.size for b in mine if b.state in ("open", "staged", "claimed", "asr")) + size > DEVICE_BYTES:
            return [self._err(bid, "too_many")]
        if purpose == "att":
            if not self.workdir:
                return [self._err(bid, "unsafe_inbox")]
            try:
                with inbox.dot_dir(self.workdir):
                    pass
            except inbox.Unsafe:
                return [self._err(bid, "unsafe_inbox")]
            except OSError:
                return [self._err(bid, "disk")]
            if inbox.usage(self.workdir) + size > inbox.QUOTA:
                return [self._err(bid, "quota")]
        try:
            free = shutil.disk_usage(self.st.root).free
        except OSError:
            free = 0
        if free < 2 * size + 64 * 1024 * 1024:
            return [self._err(bid, "disk")]
        b = Blob(device, bid, purpose, name, mime, size, sha, origin, secs, self.clock())
        try:
            self._dir(device)
            with contextlib.suppress(FileNotFoundError):
                self.part_path(b).unlink()
            self._save(b)
        except OSError:
            return [self._err(bid, "disk")]
        self.blobs[(device, bid)] = b
        return [{"t": "blob_ack", "bid": bid, "next": 0}]

    def chunk(self, device: str, m: dict) -> list[dict]:
        bid = m.get("bid")
        b = self.blobs.get((device, bid)) if wire.is_id22(bid) else None
        if b is None or b.state != "open":
            return []
        o, d = m.get("o"), m.get("d")
        if type(o) is not int or o != b.have:
            return [{"t": "blob_ack", "bid": bid, "next": b.have}]      # dropped: resync
        if not isinstance(d, str) or len(d) > 60_075:
            return [self._fail(b, "shape")]
        try:
            raw = base64.urlsafe_b64decode(d + "=" * (-len(d) % 4)) if re.fullmatch(r"[A-Za-z0-9_-]*", d) else None
        except ValueError:
            raw = None
        if raw is None or not raw or len(raw) > CHUNK:
            return [self._fail(b, "shape")]
        if b.have + len(raw) > b.size:
            return [self._fail(b, "size_mismatch")]
        if b.have == 0 and len(raw) >= 12 and not magic_ok(b.mime, raw[:16]):
            return [self._fail(b, "type")]
        try:
            fd = os.open(self.part_path(b), os.O_WRONLY | os.O_CREAT | getattr(os, "O_CLOEXEC", 0), 0o600)
            try:
                os.lseek(fd, b.have, os.SEEK_SET)
                os.write(fd, raw)
                os.ftruncate(fd, b.have + len(raw))
            finally:
                os.close(fd)
        except OSError:
            return [self._fail(b, "disk")]
        b.have += len(raw)
        b.since_ack += 1
        if b.since_ack >= ACK_EVERY:
            b.since_ack = 0
            return [{"t": "blob_ack", "bid": bid, "next": b.have}]
        return []

    def _fail(self, b: Blob, why: str) -> dict:
        self._forget(b)
        return self._done_err(b.bid, why)

    def end(self, device: str, bid) -> tuple[list[dict], Blob | None]:
        """→ (answers, the finished asr blob to transcribe or None)."""
        b = self.blobs.get((device, bid)) if wire.is_id22(bid) else None
        if b is None:
            return [], None
        if b.state in ("staged", "claimed"):
            return [{"t": "blob_done", "bid": bid, "ok": True, "kind": b.kind, "bytes": b.size}], None
        if b.state != "open":
            return [], None
        out = [{"t": "blob_ack", "bid": bid, "next": b.have}]
        if b.have != b.size:
            return out + [self._fail(b, "size_mismatch")], None
        p = self.part_path(b)
        h = hashlib.sha256()
        try:
            with open(p, "rb") as f:
                head = f.read(16)
                h.update(head)
                for c in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(c)
        except OSError:
            return out + [self._fail(b, "disk")], None
        if h.hexdigest() != b.sha256:
            return out + [self._fail(b, "sha_mismatch")], None
        if not magic_ok(b.mime, head):
            return out + [self._fail(b, "type")], None
        if b.purpose == "asr":
            b.state = "asr"
            with contextlib.suppress(OSError):
                self._meta_path(b).unlink()
            return out + [{"t": "blob_done", "bid": bid, "ok": True, "kind": "audio", "bytes": b.size}], b
        try:
            b.path = inbox.place(self.workdir, str(p), b.name, b.mime, b.bid)
        except inbox.Unsafe:
            return out + [self._fail(b, "unsafe_inbox")], None
        except OSError:
            return out + [self._fail(b, "disk")], None
        if self.is_recording(b):
            # P33-C02: the verified bytes stay private in the state dir (the fenced Agent cannot reach it); say-time
            # transcription reads this copy, never the inbox file the Agent could swap for a link or a playlist
            try:
                os.replace(p, self.voice_path(b))
                b.voice = str(self.voice_path(b))
            except OSError:
                b.voice = ""
        with contextlib.suppress(OSError):
            p.unlink()
        with contextlib.suppress(OSError):
            self._meta_path(b).unlink()
        b.state, b.expires = "staged", self.clock() + STAGED_TTL
        self._save(b)
        return out + [{"t": "blob_done", "bid": bid, "ok": True, "kind": b.kind, "bytes": b.size}], None

    def asr_done(self, b: Blob) -> None:
        """The transcriber is finished with an asr take: the audio is deleted (it never reaches the inbox)."""
        self._forget(b)

    def drop(self, device: str, bid) -> list[dict]:
        if not wire.is_id22(bid):
            return []
        b = self.blobs.get((device, bid))
        if b is not None:
            if b.state == "open":
                self._forget(b)
            elif b.state == "staged":                  # announced to nobody: the inbox file goes too
                inbox.unlink_placed(b.path)
                self._forget(b)
            # claimed (a say is using it) or asr (being transcribed): left alone; a claimed file is never deleted by a drop
        return [self._err(bid, "dropped")]

    # ------------------------------------------------------------ say (compose) side
    def claim(self, device: str, ids) -> list[Blob]:
        """A say's `att`: all or nothing. Raises Refused(shape | too_many_att | att_open | att_gone)."""
        if ids is None:
            return []
        if not isinstance(ids, list) or not all(wire.is_id22(i) for i in ids) or len(set(ids)) != len(ids):
            raise Refused("shape")
        if len(ids) > 10:
            raise Refused("too_many_att")
        self.sweep()
        opened = [i for i in ids if (b := self.blobs.get((device, i))) is not None and b.state == "open"]
        if opened:
            raise Refused("att_open", opened)
        gone = [i for i in ids if (b := self.blobs.get((device, i))) is None or b.state != "staged"
                or not os.path.isfile(b.path)]
        if gone:
            raise Refused("att_gone", gone)
        out = []
        for i in ids:
            b = self.blobs[(device, i)]
            b.state = "claimed"
            self._save(b)
            out.append(b)
        return out

    def release(self, blobs: list[Blob]) -> None:
        """A withdrawn say: its attachments are staged again under the same ids (the TTL continues)."""
        for b in blobs:
            if self.blobs.get((b.device, b.bid)) is b and b.state == "claimed":
                b.state = "staged"
                self._save(b)

    def commit(self, blobs: list[Blob]) -> None:
        """Delivered: the files belong to the Agent now; only the bookkeeping goes."""
        for b in blobs:
            if self.blobs.get((b.device, b.bid)) is b:
                self._forget(b, files=True)

    def device_gone(self, device: str) -> None:
        """A revoked device: its partials and staged (never announced) files go."""
        for b in self._mine(device):
            if b.state == "staged":
                inbox.unlink_placed(b.path)
            if b.state != "claimed":
                self._forget(b)
