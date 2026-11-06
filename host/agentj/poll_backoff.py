"""Per-endpoint background retry scheduling; never grants freshness or retries a signed body."""
from __future__ import annotations

import math
import random
import time
from email.utils import parsedate_to_datetime

MAX_RETRY_AFTER = 300


def retry_after(value, *, now=None):
    """Accept bounded seconds or HTTP dates, never booleans, negative/huge/nonfinite values."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError, OverflowError):
        if not isinstance(value, str) or len(value) > 128:
            return None
        try:
            seconds = parsedate_to_datetime(value).timestamp() - (time.time() if now is None else now)
        except (TypeError, ValueError, OverflowError):
            return None
    return seconds if math.isfinite(seconds) and 0 <= seconds <= MAX_RETRY_AFTER else None


class Response(dict):
    """JSON object with transport metadata outside the untrusted JSON namespace."""
    retry_after = None


def response_delay(status, obj):
    if status != 429 and not 500 <= status <= 599:
        return None
    header = retry_after(getattr(obj, 'retry_after', None))
    body = retry_after(obj.get('retry_after')) if isinstance(obj, dict) else None
    return max(header or 0, body or 0)


class Backoff:
    def __init__(self, base=4, cap=60, *, clock=time.monotonic, jitter=random.random):
        self.base, self.cap, self.clock, self.jitter = base, cap, clock, jitter
        self.failures = 0
        self.until = 0.0

    def remaining(self):
        return max(0.0, self.until - self.clock())

    def reset(self):
        self.failures = 0
        self.until = 0.0

    def failed(self, status='network', retry=None):
        if retry is None and status not in ('network', 'timeout', 'http_5xx'):
            self.reset()
            return 0.0
        self.failures = min(self.failures + 1, 16)
        delay = min(self.cap, self.base * 2 ** (self.failures - 1))
        delay = min(self.cap, delay * (1 + .25 * self.jitter()))
        delay = max(delay, retry_after(retry) or 0)
        self.until = self.clock() + delay
        return delay
