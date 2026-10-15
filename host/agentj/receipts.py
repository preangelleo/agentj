"""P313: the phone's `say` receipts on disk — so a host restart never runs the same phone message twice (PROTOCOL §14).

compose.Sends remembers every sid a device used, but only for the life of the process. This file keeps the same key
(device, sid) in `<state>/say_receipts.jsonl` (0600), written the moment a `say` is accepted — before the Agent gets it
and before the phone hears `say_res ok` — with its state:

  recv   accepted, not yet handed to the Agent
  deliv  handed to the harness (or the write failed half-way: maybe handed)
  done   finished: the turn ended, the host answered it itself, it was withdrawn or refused by the harness
  lost   an earlier serve died while it was `recv` / `deliv` (set at load, once)

After a restart every remembered sid still answers `dup` (no replay — the host never stores the words here, only the
key), and the messages that were `recv` / `deliv` when the old process died are handed back once (`interrupted`) so
serve can tell the phone in one line that they may need a manual resend. Bounded: entries older than KEEP_S go, and at
most PER_DEVICE (= compose.SEEN_MAX) per device, TOTAL_MAX in all; the file is rewritten when it holds more than twice
the live lines.

Writes go through one ordered writer thread (the loop never fsyncs, P142); `recv` is awaited (`await persist()`), the
later states are fire-and-forget (losing one only makes the restart line more careful, never a second delivery).
A missing / broken / unwritable file never stops a message: it degrades to the old memory-only dedupe and is logged."""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import pathlib
import threading
import time

from .compose import SEEN_MAX

FILE = "say_receipts.jsonl"
KEEP_S = 7 * 86400
PER_DEVICE = SEEN_MAX
TOTAL_MAX = 8192
STATES = ("recv", "deliv", "done", "lost")
RANK = {s: i for i, s in enumerate(STATES)}   # a state never goes back (replay is order-insensitive)
OPEN = ("recv", "deliv")

_ex: concurrent.futures.ThreadPoolExecutor | None = None
_ex_lk = threading.Lock()


def _executor() -> concurrent.futures.ThreadPoolExecutor:
    global _ex
    with _ex_lk:
        if _ex is None:
            _ex = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="agentj-receipts")
        return _ex


def _ok_key(x) -> bool:
    return isinstance(x, str) and 0 < len(x) <= 128


class Receipts:
    def __init__(self, root: pathlib.Path, log=None, now=time.time):
        self.path = pathlib.Path(root) / FILE
        self.log = log or (lambda *a, **k: None)
        self.now = now
        self.items: dict[tuple, dict] = {}          # (device, sid) → {"s", "t", "n"}; insertion order = first seen
        self.lines = 0
        self.broken = False
        self.interrupted: list[dict] = []           # [{"d", "i", "s" (recv|deliv), "n" (turn id)}] — from the last process
        self._lk = threading.Lock()
        self._load()

    # ---------------------------------------------------------------- load
    def _load(self) -> None:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return
        except OSError:
            self.broken = True
            self.log("say_receipts", result="unreadable")
            return
        for line in raw.splitlines():
            self.lines += 1
            try:
                r = json.loads(line)
            except ValueError:
                continue                            # a torn last line (power cut mid-append) costs that line only
            if not isinstance(r, dict) or not _ok_key(r.get("d")) or not _ok_key(r.get("i")) or r.get("s") not in RANK:
                continue
            t, n = r.get("t"), r.get("n")
            k = (r["d"], r["i"])
            old = self.items.get(k)
            rec = {"s": r["s"], "t": t if isinstance(t, (int, float)) and not isinstance(t, bool) else 0,
                   "n": n if type(n) is int else None}
            if old is None:
                self.items[k] = rec
            elif RANK[rec["s"]] >= RANK[old["s"]]:
                old["s"] = rec["s"]
                old["n"] = rec["n"] if rec["n"] is not None else old["n"]
        self._trim()
        for (d, i), r in self.items.items():
            if r["s"] in OPEN:
                self.interrupted.append({"d": d, "i": i, "s": r["s"], "n": r["n"]})
                r["s"] = "lost"
        if self.interrupted or self.lines > 2 * len(self.items) + 64:
            self._rewrite()
        if self.interrupted:
            self.log("say_receipts", result="interrupted", count=len(self.interrupted))

    def _trim(self) -> None:
        cut = self.now() - KEEP_S
        per: dict[str, int] = {}
        keep = []
        for k, r in reversed(list(self.items.items())):          # newest first
            if r["t"] < cut and r["s"] not in OPEN:
                continue
            per[k[0]] = per.get(k[0], 0) + 1
            if per[k[0]] > PER_DEVICE or len(keep) >= TOTAL_MAX:
                continue
            keep.append((k, r))
        self.items = dict(reversed(keep))

    # ---------------------------------------------------------------- disk (one ordered writer thread)
    def _line(self, k: tuple, r: dict) -> str:
        return json.dumps({"d": k[0], "i": k[1], "s": r["s"], "t": int(r["t"]), "n": r["n"]}, separators=(",", ":"))

    def _rewrite(self) -> None:
        snap = list(self.items.items())             # the loop may add one meanwhile (atomic copy under the GIL)
        data = "".join(self._line(k, r) + "\n" for k, r in snap).encode()
        tmp = self.path.with_name(f"{FILE}.{os.getpid()}.tmp")
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, data)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp, self.path)
            self.lines = len(snap)
        except OSError:
            self.broken = True
            self.log("say_receipts", result="write_failed")

    def _append(self, line: str) -> None:
        with self._lk:
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                try:
                    os.write(fd, (line + "\n").encode())
                    os.fsync(fd)
                finally:
                    os.close(fd)
                self.lines += 1
                self.broken = False
            except OSError:
                self.broken = True
                self.log("say_receipts", result="write_failed")
                return
            if self.lines > 2 * len(self.items) + 64:
                self._rewrite()

    # ---------------------------------------------------------------- API
    def used(self, device: str, sid: str) -> bool:
        return (device, sid) in self.items

    def state(self, device: str, sid: str) -> str | None:
        r = self.items.get((device, sid))
        return r["s"] if r else None

    def mark(self, device: str, sid: str, state: str, turn: int | None = None) -> concurrent.futures.Future | None:
        """Record now (memory, so a second `say` with this sid is `dup` at once) and queue the line for the disk. Returns
        the write's future (None when nothing changed). A state never goes back."""
        if state not in RANK or not _ok_key(device) or not _ok_key(sid):
            return None
        k = (device, sid)
        r = self.items.get(k)
        if r is not None and (RANK[state] < RANK[r["s"]] or RANK[state] == RANK[r["s"]] and (turn is None or turn == r["n"])):
            return None                             # nothing new (the same state may only add its page id)
        if r is None:
            r = self.items[k] = {"s": state, "t": self.now(), "n": turn}
            if len(self.items) > TOTAL_MAX + 256 or sum(1 for d, _ in self.items if d == device) > PER_DEVICE + 64:
                self._trim()
        else:
            r["s"] = state
            if turn is not None:
                r["n"] = turn
        return _executor().submit(self._append, self._line(k, r))

    async def persist(self, device: str, sid: str, state: str, turn: int | None = None) -> None:
        """mark() and wait until the line is on disk (fsync'ed) — off the loop."""
        fut = self.mark(device, sid, state, turn)
        if fut is not None:
            await asyncio.wrap_future(fut)

    def done_turn(self, turn: int | None) -> None:
        """The page `turn` ended: every receipt still open for it is done."""
        if turn is None:
            return
        for (d, i), r in list(self.items.items()):
            if r["n"] == turn and r["s"] in OPEN:
                self.mark(d, i, "done")

    def flush(self) -> None:
        """Tests / shutdown: wait for every queued line."""
        _executor().submit(lambda: None).result()
