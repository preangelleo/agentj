"""P192 (B13 step 1 beyond serve.py, B18): fire-and-forget tasks outside `serve.Host._bg`, and swallowed exceptions that
should at least leave a line in host.log.

- `spawn(coro, what, log)`: the loop keeps only a weak reference to a task — an unreferenced one can be collected mid-flight.
  Held here until it ends; a failure is logged as `bg_task_failed` (what + exception type, never the message).
- `swallowed(log, ev, exc, ...)`: a broad `except` on a connection / upgrade / approval path logs `ev` with the exception type
  (never the message: it may carry a URL, a path or a key). The same (event, why, type) is logged at most once per minute,
  so a loop that keeps failing does not flood host.log; the line carries how many were folded into it (`count`)."""
from __future__ import annotations

import asyncio
import time

_held: set = set()
_last: dict = {}                    # (ev, why, type) → [monotonic of the last line, failures since]
EVERY = 60.0


def spawn(coro, what: str, log=None) -> asyncio.Task:
    """Raises RuntimeError outside a running loop (like create_task); callers that tolerate that keep their own guard."""
    try:
        task = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()                # never "coroutine was never awaited"
        raise
    _held.add(task)

    def done(t: asyncio.Task) -> None:
        _held.discard(t)
        if not t.cancelled() and t.exception() is not None and log is not None:
            try:
                log("bg_task_failed", why=what, exception_type=type(t.exception()).__name__)
            except Exception:  # noqa: BLE001 — logging a failure must not raise in a done-callback
                pass
    task.add_done_callback(done)
    return task


def host_log(host):
    """`host.st.log`, or None for a test double without a state (a log call must never be the thing that raises)."""
    return getattr(getattr(host, "st", None), "log", None)


def swallowed(log, ev: str, exc: BaseException, why: str | None = None, every: float = EVERY) -> bool:
    """Log a swallowed exception (rate-limited). True when a line was written; `log=None` is a no-op."""
    if log is None:
        return False
    key = (ev, why, type(exc).__name__)
    now = time.monotonic()
    seen = _last.get(key)
    if seen is not None and now - seen[0] < every:
        seen[1] += 1
        return False
    folded = seen[1] if seen is not None else 0
    _last[key] = [now, 0]
    kw = {"exception_type": type(exc).__name__}
    if why:
        kw["why"] = why
    if folded:
        kw["count"] = folded + 1
    try:
        log(ev, **kw)
    except Exception:  # noqa: BLE001 — never turn a swallowed error into a raised one
        return False
    return True
