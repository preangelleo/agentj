"""P142 (0.18.3, after P136): no fsync on the event-loop thread. On a thrashing disk one fsync'ed state write parks the loop
in kernel state D for seconds — every phone heartbeat and the relay's auth answer wait behind it.

- `write`: atomic 0600 write (tmp → rename). Off the loop the tmp is fsync'ed before the rename, as always. On the loop the
  tmp is written and renamed at once — every reader (this process, the CLI, a lock holder after us) sees the new bytes
  exactly as before — and only the fsync of the file and its directory goes to one ordered writer thread. The window this
  opens is a power cut between the rename and that fsync; ext4 (auto_da_alloc) and btrfs flush a file renamed over an
  existing one at the rename's commit, so the old-or-new guarantee holds there too.
- `fsync` / `settle`: the same split for writers with their own tmp/rename logic.
- `append`: host.log lines — written at once while the disk is quick; after one slow append the loop's lines are buffered
  and appended by the same thread, in order, for a while (no fsync either way).
- `flush` / `aflush`: the barrier — serve's shutdown and every loop's end wait for it. The control socket does NOT: readers
  already see every renamed file, and on a thrashing disk a CLI answer must not wait for the fsync queue."""
from __future__ import annotations

import asyncio
import os
import pathlib
import queue
import threading
import time

_lk = threading.Lock()
_idle = threading.Condition(_lk)
_q: "queue.Queue | None" = None
_thread: threading.Thread | None = None
_outstanding = 0                                # jobs queued and not yet finished
_logbuf: dict[str, list[bytes]] = {}            # path → lines appended on the loop, not yet on disk
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


SLOW_APPEND = 0.05     # s: an append on the loop slower than this = the disk is struggling → buffer for a while
BUFFER_FOR = 30.0      # s: how long the loop then hands its log lines to the writer thread
_buffer_until = 0.0


def append(path: pathlib.Path, line: bytes) -> None:
    """O_APPEND a line (host.log). While the disk answers quickly a line on the loop is written at once (readers see it
    immediately, as before). One slow append (> SLOW_APPEND) switches the loop to buffered for BUFFER_FOR seconds: lines
    are then appended by the writer thread, in order, and the loop never waits on that file again meanwhile."""
    global _buffer_until
    key = str(path)
    loop = on_loop()
    with _lk:
        behind = key in _logbuf
        buffered = loop and (behind or time.monotonic() < _buffer_until)
        if buffered:
            if behind:
                _logbuf[key].append(line)
                return
            _logbuf[key] = [line]
    if buffered:
        def job():
            with _lk:
                lines = _logbuf.pop(key, [])
            if lines:
                _append(key, lines)
        _submit(job)
        return
    if behind:
        flush()                                       # off the loop: never overtake lines still queued
    t = time.monotonic()
    _append(key, [line])
    if loop and time.monotonic() - t > SLOW_APPEND:
        with _lk:
            _buffer_until = time.monotonic() + BUFFER_FOR


def _append(path: str, lines: list[bytes]) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, b"".join(lines))
    finally:
        os.close(fd)


def _worker() -> None:
    global _outstanding
    while True:
        job = _q.get()
        try:
            job()
        except Exception:  # noqa: BLE001 — a failed fsync / log append must never kill the writer
            pass
        finally:
            with _lk:
                _outstanding -= 1
                if not _outstanding:
                    _idle.notify_all()


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
            await aflush()
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
    """Wait until every queued fsync and log line is done. Never on the loop (use `aflush`)."""
    with _lk:
        return _idle.wait_for(lambda: not _outstanding, timeout)


async def aflush(timeout: float = 30.0) -> bool:
    with _lk:
        if not _outstanding:
            return True
    return await asyncio.to_thread(flush, timeout)


def pending() -> int:
    with _lk:
        return _outstanding
