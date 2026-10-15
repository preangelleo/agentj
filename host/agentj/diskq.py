"""P142 (0.18.3, after P136): no fsync on the event-loop thread. On a thrashing disk one fsync'ed state write parks the loop
in kernel state D for seconds — every phone heartbeat and the relay's auth answer wait behind it.

- `write`: atomic 0600 write (tmp → rename). Off the loop the tmp is fsync'ed before the rename, as always. On the loop the
  tmp is written and renamed at once — every reader (this process, the CLI, a lock holder after us) sees the new bytes
  exactly as before — and only the fsync of the file and its directory goes to one ordered writer thread. The window this
  opens is a power cut between the rename and that fsync; ext4 (auto_da_alloc) and btrfs flush a file renamed over an
  existing one at the rename's commit, so the old-or-new guarantee holds there too.
- `fsync` / `settle`: the same split for writers with their own tmp/rename logic.
- `append` (P292): host.log lines. The loop never writes one itself: its line goes to a bounded per-file queue that the log
  thread ("agentj-log", separate from the fsync thread so a slow fsync never holds log lines) appends in order. While the
  disk is healthy the loop waits up to LOOP_WAIT for its line to land (readers see it at once, as before); once a wait runs
  out it stops waiting for BUFFER_FOR s. A full queue drops its oldest lines and the thread writes one `log_dropped`
  {count} line in their place. Off the loop a line is written by the caller after everything queued for that file.
  (2026-10-14: the P142 rule "write at once until one append was slow" put the first slow append on the loop — 58.8 s.)
- `flush` / `aflush`: the barrier — serve's shutdown and every loop's end wait for it, bounded. The control socket does
  NOT: readers already see every renamed file, and on a thrashing disk a CLI answer must not wait for the fsync queue."""
from __future__ import annotations

import asyncio
import collections
import json
import os
import pathlib
import queue
import threading
import time

_lk = threading.Lock()
_idle = threading.Condition(_lk)
_q: "queue.Queue | None" = None
_thread: threading.Thread | None = None
_outstanding = 0                                # fsync jobs queued and not yet finished
_guards: dict[int, object] = {}                 # loop id → the async generator that flushes when that loop shuts down


def on_loop() -> bool:
    """True on a thread that is running an asyncio event loop right now (the place nothing may block)."""
    return asyncio._get_running_loop() is not None


def _tmp(path: pathlib.Path) -> pathlib.Path:
    return path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")   # serve and the CLI may both write


def write(path: pathlib.Path, data: bytes, flags: int = 0) -> None:
    path = pathlib.Path(path)
    tmp = _tmp(path)
    loop = on_loop()
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | flags, 0o600)
    try:
        os.write(fd, data)
        if not loop:
            os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    if loop:
        _submit(lambda: _sync(path))


def fsync(fd: int) -> None:
    """For writers with their own tmp/rename logic: fsync now, except on the loop (pair it with `settle` after the rename)."""
    if not on_loop():
        os.fsync(fd)


def settle(path) -> None:
    """After a rename whose fsync was skipped on the loop: the ordered writer fsyncs the file and its directory."""
    if on_loop():
        p = pathlib.Path(path)
        _submit(lambda: _sync(p))


def _sync(path: pathlib.Path) -> None:
    for p, mode in ((path, os.O_RDONLY), (path.parent, os.O_RDONLY | os.O_DIRECTORY)):
        try:
            fd = os.open(p, mode)
        except OSError:
            return                                   # replaced / removed meanwhile: whoever did that owns its durability
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)


# ------------------------------------------------------------ host.log lines (P292)
LOOP_WAIT = 0.02       # s: the loop waits at most this long for its line to land while the disk is healthy
BUFFER_FOR = 30.0      # s: after one wait ran out the loop hands its lines over without waiting, for this long
LOG_MAX_LINES = 4096   # per file: ~2 min of the busiest host.log seen (a reconnect storm) — far more than one stall
LOG_MAX_BYTES = 1 << 20   # per file: 1 MiB of queued lines (a line is ~150 B), bounded memory on a disk that never answers
_buffer_until = 0.0

_llk = threading.Lock()
_lcv = threading.Condition(_llk)
_pending: dict[str, collections.deque] = {}     # path → [(seq, line)] not yet on disk
_pbytes: dict[str, int] = {}
_dropped: dict[str, int] = {}                   # path → oldest lines dropped since the last write of that file
_seq: dict[str, int] = {}                       # path → last line number queued
_done: dict[str, int] = {}                      # path → last line number settled (on disk, dropped or failed)
_writing: set[str] = set()                      # paths someone (log thread / an off-loop caller) is appending to now
_lthread: threading.Thread | None = None


def append(path: pathlib.Path, line: bytes) -> None:
    """O_APPEND a line (host.log). On the loop: queued for the log thread (never written here); off the loop: written by the
    caller after every line still queued for that file — so lines never overtake each other inside this process."""
    global _buffer_until
    key = str(path)
    if not on_loop():
        return _append_now(key, line)
    _guard_loop()
    with _llk:
        q = _pending.setdefault(key, collections.deque())
        n = _seq[key] = _seq.get(key, 0) + 1
        q.append((n, line))
        size = _pbytes[key] = _pbytes.get(key, 0) + len(line)
        while q and (len(q) > LOG_MAX_LINES or size > LOG_MAX_BYTES):
            m, old = q.popleft()                     # keep the newest: the stall's own diagnosis is in them
            size -= len(old)
            _dropped[key] = _dropped.get(key, 0) + 1
            _done[key] = max(_done.get(key, 0), m)
        _pbytes[key] = size
        _start_log_thread()
        _lcv.notify_all()
        if time.monotonic() < _buffer_until:
            return
        if _lcv.wait_for(lambda: _done.get(key, 0) >= n, LOOP_WAIT):
            return
        _buffer_until = time.monotonic() + BUFFER_FOR


def _take(key: str) -> tuple[list[bytes], int]:
    """Under _llk: every queued line of `key` (oldest first, a `log_dropped` line ahead of them) and the last line number."""
    q = _pending.pop(key, None) or ()
    _pbytes.pop(key, None)
    lines = [ln for _, ln in q]
    last = q[-1][0] if q else _done.get(key, 0)
    dropped = _dropped.pop(key, 0)
    if dropped:
        lines.insert(0, (json.dumps({"ts": int(time.time()), "ev": "log_dropped", "count": dropped}) + "\n").encode())
    return lines, last


def _settled(key: str, last: int) -> None:
    with _llk:
        _writing.discard(key)
        _done[key] = max(_done.get(key, 0), last)
        _lcv.notify_all()


def _append_now(key: str, line: bytes) -> None:
    with _llk:
        _lcv.wait_for(lambda: key not in _writing)   # off the loop: the thread may be mid-write on this file
        lines, last = _take(key)
        _writing.add(key)
    try:
        _append(key, lines + [line])
    finally:
        _settled(key, last)


def _append(path: str, lines: list[bytes]) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, b"".join(lines))
    finally:
        os.close(fd)


def _log_worker() -> None:
    while True:
        with _llk:
            key = None
            while key is None:
                key = next((k for k, q in _pending.items() if (q or _dropped.get(k)) and k not in _writing), None)
                if key is None:
                    _lcv.wait()
            lines, last = _take(key)
            _writing.add(key)
        try:
            if lines:
                _append(key, lines)
        except Exception:  # noqa: BLE001 — a failed log append must never kill the writer
            pass
        finally:
            _settled(key, last)


def _start_log_thread() -> None:
    """Under _llk."""
    global _lthread
    if _lthread is None or not _lthread.is_alive():
        _lthread = threading.Thread(target=_log_worker, name="agentj-log", daemon=True)
        _lthread.start()


def _log_busy() -> bool:
    """Under _llk."""
    return bool(_writing) or any(q for q in _pending.values()) or any(_dropped.values())


def log_pending() -> int:
    with _llk:
        return sum(len(q) for q in _pending.values())


# ------------------------------------------------------------ the fsync thread
def _worker() -> None:
    global _outstanding
    while True:
        job = _q.get()
        try:
            job()
        except Exception:  # noqa: BLE001 — a failed fsync must never kill the writer
            pass
        finally:
            with _lk:
                _outstanding -= 1
                if not _outstanding:
                    _idle.notify_all()


LOOP_END_FLUSH = 5.0   # s: a loop that ends waits at most this long for its queued work (a stuck disk never hangs the exit)


def _guard_loop() -> None:
    """A loop that queued work waits for it when it ends: asyncio.run → shutdown_asyncgens closes this generator."""
    loop = asyncio._get_running_loop()
    if loop is None or id(loop) in _guards:
        return

    async def guard():
        try:
            yield
        finally:
            _guards.pop(id(loop), None)
            await aflush(LOOP_END_FLUSH)
    g = guard()
    _guards[id(loop)] = g
    from . import bg
    bg.spawn(g.__anext__(), "diskq_guard")          # P192 (B13): held (the loop keeps only a weak reference)


def _submit(job) -> None:
    global _q, _thread, _outstanding
    _guard_loop()
    with _lk:
        if _thread is None or not _thread.is_alive():
            _q = queue.Queue()
            _thread = threading.Thread(target=_worker, name="agentj-disk", daemon=True)
            _thread.start()
        _outstanding += 1
        _q.put(job)


def flush(timeout: float | None = 30.0) -> bool:
    """Wait until every queued fsync and log line is done (False = `timeout` ran out first). Never on the loop (use `aflush`)."""
    end = None if timeout is None else time.monotonic() + timeout
    left = lambda: None if end is None else max(0.0, end - time.monotonic())   # noqa: E731
    with _lk:
        if not _idle.wait_for(lambda: not _outstanding, left()):
            return False
    with _llk:
        return _lcv.wait_for(lambda: not _log_busy(), left())


async def aflush(timeout: float = 30.0) -> bool:
    with _lk, _llk:
        if not _outstanding and not _log_busy():
            return True
    return await asyncio.to_thread(flush, timeout)


def pending() -> int:
    with _lk:
        n = _outstanding
    return n + log_pending()
