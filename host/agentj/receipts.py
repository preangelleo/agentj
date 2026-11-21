"""P313: the phone's `say` receipts on disk — so a host restart never runs the same phone message twice (PROTOCOL §14).

compose.Sends remembers every sid a device used, but only for the life of the process. This file keeps the same key
(device, sid) in `<state>/say_receipts.jsonl` (0600), written the moment a `say` is accepted — before the Agent gets it
and before the phone hears `say_res ok` — with its state:

  held   host-sealed upgrade handoff, never submitted; page coordinates are for failure notices
  recv   ordinary acceptance, optionally bound to its history page; it may already have reached the harness
  deliv  handed to the harness (or the write failed half-way: maybe handed);
         a confirmed ordinary phone dispatch can bind its history epoch and page timestamp for completion;
         a maybe-handed dispatch can bind only its interruption notice
  done   finished: the turn ended, the host answered it itself, it was withdrawn or refused by the harness
  lost   an earlier serve died while it was `recv` / `deliv` without a final page (set at load, once)

After a restart every remembered sid still answers `dup` (no replay — the host never stores the words here, only the
key), and unresolved messages that were `recv` / `deliv` when the old process died are handed back once (`interrupted`) so
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
STATES = ("held", "recv", "deliv", "done", "lost")
RANK = {s: i for i, s in enumerate(STATES)}   # a state never goes back (replay is order-insensitive)
OPEN = ("recv", "deliv")
PAGE_STATES = ("held", *OPEN)

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


def _reply_page(value):
    if (isinstance(value, (list, tuple)) and len(value) == 2
            and all(type(item) is int for item in value) and value[0] >= 0 and value[1] > 0):
        return tuple(value)
    return None


class Receipts:
    def __init__(self, root: pathlib.Path, log=None, now=time.time, *, answered=None):
        self.path = pathlib.Path(root) / FILE
        self.log = log or (lambda *a, **k: None)
        self.now = now
        self.items: dict[tuple, dict] = {}          # (device, sid) → {"s", "t", "n"}; insertion order = first seen
        self.lines = 0
        self.broken = False
        self.interrupted: list[dict] = []           # [{"d", "i", "s" (recv|deliv), "n" (turn id)}] — from the last process
        self._lk = threading.Lock()
        self._load(answered)

    # ---------------------------------------------------------------- load
    def _load(self, answered) -> None:
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
            if rec["s"] == "held" and isinstance(r.get("seal"), str):
                rec["seal"] = r["seal"]
            if rec["s"] in PAGE_STATES and rec["n"] is not None:
                rec["notice_only"] = rec["s"] == "deliv" and "notice_page" in r
                rec["reply_page"] = _reply_page(r.get("notice_page" if rec["notice_only"] else "page"))
            if old is None:
                self.items[k] = rec
            elif RANK[rec["s"]] >= RANK[old["s"]]:
                if "seal" in rec:
                    old["seal"] = rec["seal"]
                # A snapshot may precede an older unbound line. Keep matching stronger evidence.
                if (rec["s"] != old["s"] or rec["n"] not in (None, old["n"])
                        or (rec.get("reply_page") is not None
                            and (rec["reply_page"] != old.get("reply_page") or not rec["notice_only"]))):
                    old["reply_page"] = rec.get("reply_page")
                    old["notice_only"] = rec.get("notice_only", False)
                old["s"] = rec["s"]
                old["n"] = rec["n"] if rec["n"] is not None else old["n"]
        self._trim()
        recovered = 0
        for (d, i), r in self.items.items():
            page = r.get("reply_page")
            if (r["s"] == "deliv" and page is not None and not r.get("notice_only")
                    and answered is not None and answered(d, r["n"], page)):
                r["s"] = "done"
                recovered += 1
                continue
            if r["s"] in OPEN:
                self.interrupted.append({"d": d, "i": i, "s": r["s"], "n": r["n"], "page": page})
                r["s"] = "lost"
        if recovered or self.interrupted or self.lines > 2 * len(self.items) + 64:
            self._rewrite()
        if recovered:
            self.log("say_receipts", result="completed_reply", count=recovered)
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
        page_key = "notice_page" if r["s"] == "deliv" and r.get("notice_only") else "page"
        return json.dumps({"d": k[0], "i": k[1], "s": r["s"], "t": int(r["t"]), "n": r["n"],
                           **({"seal": r["seal"]} if r["s"] == "held" and "seal" in r else {}),
                           **({page_key: r["reply_page"]} if r["s"] in PAGE_STATES and r.get("reply_page") else {})},
                          separators=(",", ":"))

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

    def _append(self, line: str) -> bool:
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
                return False
            if self.lines > 2 * len(self.items) + 64:
                self._rewrite()
            return not self.broken

    # ---------------------------------------------------------------- API
    def used(self, device: str, sid: str) -> bool:
        return (device, sid) in self.items

    def state(self, device: str, sid: str) -> str | None:
        r = self.items.get((device, sid))
        return r["s"] if r else None

    def mark(self, device: str, sid: str, state: str, turn: int | None = None, *, seal: str | None = None,
             reply_page: tuple[int, int] | None = None, notice_only: bool = False) -> concurrent.futures.Future | None:
        """Record now (memory, so a second `say` with this sid is `dup` at once) and queue the line for the disk. Returns
        the write's future (None when nothing changed). A state never goes back."""
        if state not in RANK or not _ok_key(device) or not _ok_key(sid):
            return None
        k = (device, sid)
        r = self.items.get(k)
        reply_page = _reply_page(reply_page) if state in PAGE_STATES and type(turn) is int else None
        if r is not None and (RANK[state] < RANK[r["s"]] or RANK[state] == RANK[r["s"]]
                              and (turn is None or turn == r["n"])
                              and (reply_page is None or (reply_page == r.get("reply_page")
                                   and (notice_only or not r.get("notice_only"))))):
            return None                             # no new page or stronger confirmation
        new = {"s": state, "t": r["t"] if r else self.now(),
               "n": r["n"] if r and turn is None else turn}
        if state == "held":
            new["seal"] = seal
        if reply_page is not None:
            new["reply_page"] = reply_page
            new["notice_only"] = notice_only
        # Publish the complete row in one operation. Compaction runs off-loop and must
        # never combine a new delivery state with an older, more permissive page field.
        self.items[k] = new
        if r is None and (len(self.items) > TOTAL_MAX + 256
                          or sum(1 for d, _ in self.items if d == device) > PER_DEVICE + 64):
            self._trim()
        return _executor().submit(self._append, self._line(k, new))

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
