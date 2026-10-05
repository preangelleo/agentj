"""Persistent chat history (PROTOCOL §10.5, PROMPT-33; relay `bridge/history.py`): every page the phone shows is one turn
{source, reply}, kept on THIS computer so a host restart loses nothing and a phone can page back.

Store (`config.json` `history`, default on): `<state>/history/current.jsonl` (dir 0700, file 0600) — one JSON line per change
of a turn, appended (the last line of an id wins), rewritten atomically to the newest KEEP turns once it passes COMPACT_AT
lines — and `<state>/history/meta.json` {epoch, next_id, undo}. `history off` = the newest MEM_KEEP turns in memory only
(epoch = the serve start time, so a phone never mixes two runs).

A turn: {"id", "ts" (ms), "src": Src, "reply": {"text", "part"?: [k, N]}, "end": open|done|stopped|failed, "card"?}.
Src = {"k": phone|host|agent|sys|task|cmd|telegram, "dev"?, "name"?, "text", "quote"?, "att"?, "local"?}. The reply of a turn = every
finished reply text of that Agent turn joined by a blank line — results only, never tool calls; a reply over PART_MAX UTF-16
units continues on the next page (`part`), nothing is truncated.

Reset (`/clear`): current.jsonl → `archive/<epoch>-<UTC stamp>.jsonl` (newest ARCHIVE_KEEP kept), epoch + 1, ids keep growing;
undo (「撤销清空」) moves that archive back as current with epoch + 1 again (what was said after the clear is archived in turn,
never lost). Text on disk is the customer's own, next to where the harness keeps its own transcript (Invariant 23); it
leaves this computer only inside §3 transport messages.
"""
from __future__ import annotations

import contextlib
import json
import os
import time

from . import wire

KEEP = 500
COMPACT_AT = 600
MEM_KEEP = 100
ARCHIVE_KEEP = 10
PART_MAX = 200_000          # UTF-16 units of one page's reply
PAGE_MAX = 50
PAGE_BYTES = 2 * 1024 * 1024
FILE_MAX = 32 * 1024 * 1024  # current.jsonl is compacted once it passes this many bytes (P33-C06), as well as COMPACT_AT lines
KEEP_BYTES = 16 * 1024 * 1024  # what compaction (and memory) keeps: the newest turns up to KEEP and up to this many JSON bytes
SRC_KINDS = ("phone", "host", "agent", "sys", "task", "cmd", "telegram")
ENDS = ("open", "done", "stopped", "failed")
SEP = "\n\n"


def enabled(st) -> bool:
    try:
        return st.config().get("history", "on") != "off"
    except (OSError, ValueError):
        return True


def set_enabled(st, on: bool) -> None:
    with st.config_lock():
        cfg = st.config()
        cfg["history"] = "on" if on else "off"
        st.write_private(st.config_path, json.dumps(cfg, indent=1, ensure_ascii=False).encode())


def _units(s: str) -> int:
    return wire.units(s)


def _cut_units(s: str, n: int) -> int:
    """Index in s where the first n UTF-16 units end (never inside a pair)."""
    u = 0
    for i, ch in enumerate(s):
        u += 2 if ord(ch) > 0xFFFF else 1
        if u > n:
            return i
    return len(s)


class History:
    def __init__(self, st, on: bool | None = None):
        self.st = st
        self.on = enabled(st) if on is None else on
        self.dir = st.root / "history"
        self.turns: dict[int, dict] = {}     # current epoch, oldest first
        self.epoch = 0
        self.next_id = 1
        self.undo: str | None = None         # the archive 「撤销清空」 would restore
        self.lines = 0
        self.parts: dict[int, list[int]] = {}   # first part id → every part id (memory only; for N)
        self.size: dict[int, int] = {}       # id → bytes of its newest JSON line (memory bound, P33-C06)
        self.mem = 0                         # sum(self.size.values())
        self.fbytes = 0                      # bytes in current.jsonl
        if self.on:
            self._load()
        else:
            self.epoch = int(time.time())

    # ------------------------------------------------------------ disk
    @property
    def cur_path(self):
        return self.dir / "current.jsonl"

    @property
    def meta_path(self):
        return self.dir / "meta.json"

    @property
    def arch_dir(self):
        return self.dir / "archive"

    def _mkdir(self, d) -> None:
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(d, 0o700)

    def _load(self) -> None:
        try:
            m = json.loads(self.meta_path.read_text())
            if isinstance(m, dict):
                self.epoch = int(m.get("epoch") or 0)
                self.next_id = max(1, int(m.get("next_id") or 1))
                self.undo = m.get("undo") if isinstance(m.get("undo"), str) else None
        except (OSError, ValueError, TypeError):
            pass
        self.turns, self.lines = self._read(self.cur_path)
        self._resize()
        try:
            self.fbytes = self.cur_path.stat().st_size
        except OSError:
            self.fbytes = 0
        if self.turns:
            self.next_id = max(self.next_id, max(self.turns) + 1)
        if self.lines > COMPACT_AT or self.fbytes > FILE_MAX or self.mem > KEEP_BYTES:
            self._compact()

    @staticmethod
    def _read(path) -> tuple[dict, int]:
        turns, n = {}, 0
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    n += 1
                    try:
                        t = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(t, dict) and type(t.get("id")) is int and isinstance(t.get("src"), dict) \
                            and isinstance(t.get("reply"), dict):
                        turns.pop(t["id"], None)
                        turns[t["id"]] = t
        except (OSError, UnicodeDecodeError):
            return {}, 0
        return dict(sorted(turns.items())), n

    def _save_meta(self) -> None:
        if not self.on:
            return
        self._mkdir(self.dir)
        self.st.write_private(self.meta_path, json.dumps({"epoch": self.epoch, "next_id": self.next_id,
                                                          "undo": self.undo}).encode())

    def _resize(self) -> None:
        self.size = {i: len(json.dumps(t, ensure_ascii=False).encode()) + 1 for i, t in self.turns.items()}
        self.mem = sum(self.size.values())

    def _set_size(self, tid: int, n: int) -> None:
        self.mem += n - self.size.get(tid, 0)
        self.size[tid] = n

    def _trim(self, keep: int) -> None:
        """Oldest turns go until ≤ keep turns and ≤ KEEP_BYTES of JSON (the newest one always stays)."""
        while self.turns and (len(self.turns) > keep or (self.mem > KEEP_BYTES and len(self.turns) > 1)):
            tid = next(iter(self.turns))
            self.turns.pop(tid)
            self.mem -= self.size.pop(tid, 0)

    def _append(self, t: dict) -> None:
        line = json.dumps(t, ensure_ascii=False)
        n = len(line.encode()) + 1
        if t["id"] in self.turns:
            self._set_size(t["id"], n)
        if not self.on:
            self._trim(MEM_KEEP)
            return
        try:
            self._mkdir(self.dir)
            self.st.append_private(self.cur_path, line)
            self.lines += 1
            self.fbytes += n
            # P33-C06: every change appends the whole turn, so a long reply streamed in many pieces grows the file fast —
            # compact on bytes as well as lines; the file stays ≤ FILE_MAX (+ one line), memory ≤ the file
            if self.lines > COMPACT_AT or self.fbytes > FILE_MAX:
                self._compact()
        except OSError:
            self.st.log("history_write_fail")

    def _compact(self) -> None:
        self._trim(KEEP)
        data = "".join(json.dumps(t, ensure_ascii=False) + "\n" for t in self.turns.values()).encode()
        self._mkdir(self.dir)
        self.st.write_private(self.cur_path, data)
        self.lines = len(self.turns)
        self.fbytes = len(data)

    # ------------------------------------------------------------ turns
    def add(self, src: dict, reply: str = "", end: str = "open", card: dict | None = None, ts: int | None = None) -> dict:
        t = {"id": self.next_id, "ts": int(ts if ts is not None else time.time() * 1000), "src": src,
             "reply": {"text": reply}, "end": end}
        if card:
            t["card"] = card
        self.next_id += 1
        self.turns[t["id"]] = t
        self._save_meta()
        self._append(t)
        return t

    def get(self, tid) -> dict | None:
        return self.turns.get(tid) if type(tid) is int else None

    def update(self, tid: int, *, append: str | None = None, end: str | None = None, card: dict | None = None,
               text: str | None = None) -> list[dict]:
        """Change one turn; returns every turn that changed (an append past PART_MAX adds a continuation page and renumbers
        the parts), the last one being where the next append goes. [] when the id is gone. text = replace the reply (a
        command's interim line → its result)."""
        t = self.turns.get(tid)
        if t is None:
            return []
        changed = [t]
        if text is not None:
            t["reply"]["text"] = text[:_cut_units(text, PART_MAX)]
        if append:
            cur = t["reply"]["text"]
            joined = cur + SEP + append if cur else append
            while _units(joined) > PART_MAX:
                cut = _cut_units(joined, PART_MAX)
                t["reply"]["text"] = joined[:cut]
                joined = joined[cut:]
                head = next((h for h, ids in self.parts.items() if tid in ids), tid)
                ids = self.parts.setdefault(head, [tid])
                nt = {"id": self.next_id, "ts": int(time.time() * 1000), "src": dict(t["src"]), "reply": {"text": ""},
                      "end": t["end"]}
                self.next_id += 1
                self.turns[nt["id"]] = nt
                ids.append(nt["id"])
                for k, i in enumerate(ids, 1):
                    self.turns[i]["reply"]["part"] = [k, len(ids)]
                changed = [self.turns[i] for i in ids]
                t, tid = nt, nt["id"]
            t["reply"]["text"] = joined
        if end in ENDS:
            for x in changed:
                x["end"] = end
        if card is not None:
            t["card"] = card
        self._save_meta()
        for x in changed:
            self._append(x)
        return changed

    def meta(self) -> dict:
        ids = list(self.turns)
        return {"epoch": self.epoch, "first": ids[0] if ids else 0, "last": ids[-1] if ids else 0, "count": len(ids)}

    def page(self, before=None, after=None, limit: int = PAGE_MAX) -> tuple[list[dict], bool]:
        """Oldest first. after: the turns just newer than that id (catch-up); before: the turns just older; neither: the
        newest. Fewer than `limit` when PAGE_BYTES would be passed; `more` = there is more in that direction."""
        limit = max(1, min(int(limit), PAGE_MAX))
        ts = list(self.turns.values())
        if type(after) is int:
            pool = [t for t in ts if t["id"] > after]
            out, more = pool[:limit], len(pool) > limit
            size = 0
            for i, t in enumerate(out):
                size += len(wire.dumps(t))
                if size > PAGE_BYTES and i:
                    out, more = out[:i], True
                    break
            return out, more
        pool = [t for t in ts if t["id"] < before] if type(before) is int else ts
        out, more = pool[-limit:], len(pool) > limit
        size = 0
        for i in range(len(out) - 1, -1, -1):
            size += len(wire.dumps(out[i]))
            if size > PAGE_BYTES and i < len(out) - 1:
                out, more = out[i + 1:], True
                break
        return out, more

    # ------------------------------------------------------------ reset (/clear) and undo
    def _archive(self, why: str) -> str | None:
        if not self.turns:
            return None
        if not self.on:
            return "memory"
        self._mkdir(self.arch_dir)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        name = f"{self.epoch}-{stamp}.jsonl"
        n = 1
        while (self.arch_dir / name).exists():
            n += 1
            name = f"{self.epoch}-{stamp}-{n}.jsonl"
        data = "".join(json.dumps(t, ensure_ascii=False) + "\n" for t in self.turns.values()).encode()
        self.st.write_private(self.arch_dir / name, data)
        olds = sorted(self.arch_dir.glob("*.jsonl"), key=lambda p: (p.stat().st_mtime, p.name))
        for p in olds[:-ARCHIVE_KEEP]:
            if p.name != name:
                with contextlib.suppress(OSError):
                    p.unlink()
        return name

    def reset(self, why: str = "clear") -> str | None:
        """A new conversation: archive what is there, start from zero (epoch + 1, ids keep growing). Returns the archive name
        (None: nothing to archive — then nothing changes). The archive stays restorable by undo_reset."""
        try:
            name = self._archive(why)
        except OSError:
            self.st.log("history_archive_fail")
            return None
        if name is None:
            return None
        self._mem_undo = dict(self.turns) if not self.on else None
        self.turns, self.parts, self.lines, self.size, self.mem, self.fbytes = {}, {}, 0, {}, 0, 0
        self.epoch += 1
        self.undo = name
        if self.on:
            self.st.write_private(self.cur_path, b"")
        self._save_meta()
        return name

    def undo_reset(self) -> bool:
        """「撤销清空」: the archive the last reset made becomes current again (what was said since is archived), epoch + 1."""
        name, self.undo = self.undo, None
        if not name:
            return False
        if not self.on:
            back = getattr(self, "_mem_undo", None) or {}
            self.turns, self.parts = dict(back), {}
            self._resize()
            self.epoch += 1
            return True
        path = self.arch_dir / name
        turns, _ = self._read(path)
        if not turns:
            self._save_meta()
            return False
        with contextlib.suppress(OSError):
            self._archive("undo")
        self.turns, self.parts = turns, {}
        self._resize()
        self.next_id = max(self.next_id, max(turns) + 1)
        self.epoch += 1
        with contextlib.suppress(OSError):
            path.unlink()
        self._compact()
        self._save_meta()
        return True

    def on_disk(self) -> tuple[int, int]:
        """(files, bytes) of history on this computer — current.jsonl + every archive — whether history is on or off now
        (`history off` stops writing; it does not delete what is there, P33-C07)."""
        n = b = 0
        for p in [self.cur_path, *(self.arch_dir.glob("*.jsonl") if self.arch_dir.is_dir() else [])]:
            with contextlib.suppress(OSError):
                s = p.stat()
                if s.st_size:
                    n, b = n + 1, b + s.st_size
        return n, b

    def purge(self) -> int:
        """`agentj history clear --all` (P33-C07): delete current.jsonl and every archive — not undoable; the pages in memory
        go too and the epoch moves on (a phone starts from an empty history). Returns how many files were deleted."""
        n = 0
        for p in [self.cur_path, *(self.arch_dir.glob("*.jsonl") if self.arch_dir.is_dir() else [])]:
            with contextlib.suppress(OSError):
                p.unlink()
                n += 1
        self.turns, self.parts, self.lines, self.size, self.mem, self.fbytes = {}, {}, 0, {}, 0, 0
        self._mem_undo = None
        self.undo = None
        self.epoch += 1
        if self.dir.is_dir():                      # meta (epoch / next id / undo) even when history is off now
            with contextlib.suppress(OSError):
                self.st.write_private(self.meta_path, json.dumps({"epoch": self.epoch, "next_id": self.next_id,
                                                                  "undo": None}).encode())
        return n

    def archives(self) -> list[str]:
        if not self.on or not self.arch_dir.is_dir():
            return []
        return [p.name for p in sorted(self.arch_dir.glob("*.jsonl"), key=lambda p: (p.stat().st_mtime, p.name))]
