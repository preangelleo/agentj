"""Report scheduler for `agentj serve` (PROTOCOL.md §7). Best effort and never blocking:

- triggers (start, approve, revoke, pairing pending/end, device online/gone) are coalesced by a 2 s debounce;
- a heartbeat every 60 s (45 s for 10 minutes after an urgent notice);
- the blocking HTTPS call runs on a daemon thread with a hard timeout, at most one in flight;
- ≤ RATE_PER_HOUR reports per sliding hour (Dashboard allows 120; the rest is headroom for `agentj report`);
- 403 not_bound → `report_unbound` once, then silent for that binding until restart (cloud.json is kept).
Logs are metadata only: seq, status class, trigger.
"""
from __future__ import annotations

import asyncio
import collections
import contextlib
import os
import threading
import time
from typing import Callable

from . import cloud
from .envcompat import getenv


def _ttl(env: str, default: float) -> float:
    """Tests may only shorten, never lengthen."""
    try:
        return max(0.0, min(default, float(getenv(env, default))))
    except ValueError:
        return default


DEBOUNCE = _ttl("AGENTJ_TEST_REPORT_DEBOUNCE", 2)
HEARTBEAT = _ttl("AGENTJ_TEST_HEARTBEAT", 60)
HARD_TIMEOUT = 4 * cloud.HTTP_TIMEOUT + 5   # F19 + language fallback + a replay retry may make four requests
RATE_PER_HOUR = 100


def in_daemon_thread(fn: Callable, *args) -> asyncio.Future:
    """Like asyncio.to_thread, but on a daemon thread: a hung HTTPS call can never hold up `serve` shutdown."""
    loop = asyncio.get_running_loop()
    fut = loop.create_future()

    def work():
        try:
            res, err = fn(*args), None
        except BaseException as e:  # noqa: BLE001 — surfaced to the loop as the future's exception
            res, err = None, e

        def settle():
            if not fut.done():
                fut.set_exception(err) if err else fut.set_result(res)
        with contextlib.suppress(RuntimeError):  # loop already closed
            loop.call_soon_threadsafe(settle)

    threading.Thread(target=work, daemon=True, name="agentj-report").start()
    return fut


class Reporter:
    def __init__(self, st, view: Callable[[], tuple], *, send: Callable = cloud.send_report, debounce: float | None = None,
                 heartbeat: float | None = None, hard_timeout: float = HARD_TIMEOUT, rate_per_hour: int = RATE_PER_HOUR,
                 on_account: Callable[[dict], None] | None = None, on_notices: Callable[[list], None] | None = None):
        self.st, self.view, self.send = st, view, send
        self.on_notices = on_notices
        self.urgent_until = 0.0
        self.on_account = on_account     # A1: the 200's account language (serve applies a newer one, last write wins)
        self.debounce = DEBOUNCE if debounce is None else debounce
        self.heartbeat = HEARTBEAT if heartbeat is None else heartbeat
        self.hard_timeout, self.rate_per_hour = hard_timeout, rate_per_hour
        self.wake = asyncio.Event()
        self.reason: str | None = None
        self.inflight: asyncio.Future | None = None
        self.unbound_host: str | None = None
        self.sent: collections.deque = collections.deque()
        self.last_attempt: float | None = None
        self.results: list[cloud.ReportResult] = []   # for tests / status

    def trigger(self, why: str) -> None:
        if self.reason is None:
            self.reason = why
        self.wake.set()

    async def run(self) -> None:
        self.trigger("start")
        while True:
            interval = min(self.heartbeat, 45) if time.monotonic() < self.urgent_until else self.heartbeat
            wait = interval if self.last_attempt is None else max(0.0, self.last_attempt + interval - time.monotonic())
            try:
                await asyncio.wait_for(self.wake.wait(), wait)
            except TimeoutError:
                self.reason = self.reason or "heartbeat"
            if self.reason is None:
                self.wake.clear()
                continue
            await asyncio.sleep(self.debounce)       # coalesce a burst of triggers into one report
            await self._rate_wait()
            why, self.reason = self.reason, None
            self.wake.clear()
            await self.send_once(why)

    async def _rate_wait(self) -> None:
        while True:
            now = time.monotonic()
            while self.sent and now - self.sent[0] >= 3600:
                self.sent.popleft()
            if len(self.sent) < self.rate_per_hour:
                return
            await asyncio.sleep(self.sent[0] + 3600 - now)

    async def send_once(self, why: str) -> cloud.ReportResult | None:
        self.last_attempt = time.monotonic()
        link = cloud.read_cloud(self.st)
        if not link or link["host_id"] == self.unbound_host:
            return None
        if self.inflight is not None and not self.inflight.done():
            self.st.log("report_fail", status="busy", trigger=why)
            return None
        online, pending = self.view()            # computed on the loop: a live snapshot
        self.sent.append(time.monotonic())
        fut = self.inflight = in_daemon_thread(self.send, self.st, set(online), dict(pending))
        done, _ = await asyncio.wait({fut}, timeout=self.hard_timeout)
        if not done:
            self.st.log("report_fail", status="timeout", trigger=why)
            res = cloud.ReportResult("fail", "timeout")
        else:
            try:
                res = fut.result()
            except Exception:  # noqa: BLE001 — a reporting bug must never reach serve
                res = cloud.ReportResult("fail", "error")
            if res.kind == "ok":
                self.st.log("report_ok", seq=res.seq, trigger=why)
                items = getattr(res, "notices", None)
                if items is not None and self.on_notices:
                    if any(n['priority'] == 'urgent' for n in items): self.urgent_until = time.monotonic() + 600
                    try: self.on_notices(items)
                    except Exception: self.st.log("notice_delivery_failed")
                acct = getattr(res, "account", None)
                if acct and self.on_account:
                    try:
                        self.on_account(acct)
                    except Exception:  # noqa: BLE001 — a sync bug must never stop reporting
                        self.st.log("language_sync_failed")
            elif res.kind == "unbound":
                self.unbound_host = link["host_id"]
                self.st.log("report_unbound", trigger=why)
            elif res.kind == "fail":
                self.st.log("report_fail", status=res.status, trigger=why)
        self.results.append(res)
        return res
