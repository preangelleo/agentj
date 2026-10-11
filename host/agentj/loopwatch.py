"""P136: name what blocked the event loop. A watcher thread notices the loop has not ticked for `threshold` seconds and,
while it is still stuck, samples the loop thread: the innermost frames (file basename:line:function only — no paths, no
values), the asyncio task that was running, and the kernel thread state (D = waiting on the disk / swap-in, R = CPU)
plus major page faults taken during the stall. `serve.loop_watch` puts the sample on its `loop_stall` line."""
from __future__ import annotations

import asyncio
import os
import sys
import threading
import time

FRAMES = 3          # innermost frames named in `where`


def _thread_stat(native: int) -> tuple[str | None, int | None]:
    try:
        with open(f"/proc/self/task/{native}/stat", "rb") as f:
            rest = f.read().rsplit(b")", 1)[1].split()
        return rest[0].decode(), int(rest[9])    # field 3 state, field 12 majflt
    except (OSError, IndexError, ValueError):
        return None, None


def _where(frame) -> str:
    out = []
    while frame is not None and len(out) < FRAMES:
        co = frame.f_code
        out.append(f"{os.path.basename(co.co_filename)}:{frame.f_lineno}:{co.co_name}")
        frame = frame.f_back
    return " < ".join(out)


class StallProbe:
    def __init__(self, loop: asyncio.AbstractEventLoop, threshold: float, period: float = 1.0):
        self.loop, self.threshold, self.period = loop, threshold, period
        self.ident, self.native = threading.get_ident(), threading.get_native_id()
        self.tick = time.monotonic()
        self.sample: dict | None = None
        self.stop = threading.Event()

    def beat(self) -> None:                          # on the loop, once per `period`
        self.tick = time.monotonic()

    def start(self) -> None:
        threading.Thread(target=self._run, name="agentj-loopwatch", daemon=True).start()

    def _run(self) -> None:
        while not self.stop.wait(0.25):
            if self.sample is None and time.monotonic() - self.tick - self.period >= self.threshold:
                s = self.capture()
                if time.monotonic() - self.tick - self.period >= self.threshold:   # still stuck: the sample is the culprit
                    self.sample = s

    def capture(self) -> dict:
        frame = sys._current_frames().get(self.ident)
        try:
            task = asyncio.current_task(self.loop)
        except Exception:  # noqa: BLE001 — diagnostics only
            task = None
        name = None
        if task is not None:
            coro = task.get_coro()
            name = getattr(coro, "__qualname__", None) or task.get_name()
        pstate, majflt = _thread_stat(self.native)
        return {"where": _where(frame) if frame is not None else None, "task": name, "pstate": pstate, "_majflt": majflt}

    def take(self) -> dict:
        """The sample of the stall that just ended (empty if none), with major faults counted since the sample."""
        s, self.sample = self.sample, None
        if not s:
            return {}
        before = s.pop("_majflt")
        after = _thread_stat(self.native)[1]
        if before is not None and after is not None:
            s["majflt"] = after - before
        return {k: v for k, v in s.items() if v is not None}
