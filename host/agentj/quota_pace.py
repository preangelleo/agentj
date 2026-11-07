"""Pure subscription percentage pace. No prices, plan volumes or account access.

Mirrors web/public/js/quotapace.js: speed = used share / elapsed share.
Zero elapsed and unknown/stale observations have unknown speed; 80/90 wins.
"""
import math


def number(x):
    return type(x) in (int, float) and math.isfinite(x)


def pace(used, reset, now, period=604800, *, stale=False):
    valid = number(used) and 0 <= used <= 100
    out = {'speed': None, 'fast': None, 'tier': '', 'elapsed': None}
    if valid:
        out['tier'] = 'empty' if used >= 90 else 'tight' if used >= 80 else ''
    if (stale or not valid or not all(number(x) and x > 0 for x in (reset, now, period))
            or not 0 < reset-now <= period):
        return out
    elapsed = period-(reset-now)
    out['elapsed'] = elapsed
    if elapsed > 0:
        out['speed'] = used / (elapsed / period * 100)
        out['fast'] = used > elapsed / period * 100 + 1e-9
        if not out['tier'] and out['fast']:
            out['tier'] = 'fast'
    return out
