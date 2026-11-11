"""Bounded format recovery for model responses; never retries a transport failure."""
from __future__ import annotations


def retry_format(call, parse, reminder, *, on_fail):
    """Parse once, retry an invalid format with a reminder, then use the caller's safe fallback.

    `call` receives None for the first prompt and `reminder` on the retry.
    Only parser errors trigger another paid model call. `on_fail` receives the last
    parse error and must return a conservative result or raise a caller error.
    """
    for hint in (None, reminder):
        result = call(hint)
        try:
            return parse(result)
        except (ValueError, TypeError, KeyError) as exc:
            last = exc
    return on_fail(last)
