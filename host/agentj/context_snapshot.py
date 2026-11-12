"""One conservative context-window percentage for phone, heartbeat and compaction.

Never substitute model aliases or cumulative spend for a missing native window.
"""
import math
import time

MAX_AGE = 2 * 3600


def number(v):
    return type(v) in (int, float) and math.isfinite(v) and v >= 0


def display_percent(ctx):
    """Return a native phone percentage, even when used/max is unavailable."""
    if not isinstance(ctx, dict):
        return None
    value = ctx.get('pct')
    return value if number(value) and value <= 100 else None


def percent(ctx):
    """Return a threshold-safe percentage; pct-only readings are unknown."""
    if not isinstance(ctx, dict):
        return None
    used, maximum = ctx.get('used'), ctx.get('max')
    native = display_percent(ctx)
    if used is None or maximum is None or not number(used) or not number(maximum) or maximum <= 0 or used > maximum:
        return None
    actual = 100 * used / maximum
    # Claude Code reports a rounded percentage; allow one percentage point for rounding.
    if native is not None and abs(native - actual) > 1:
        return None
    return actual


def current(host):
    ctx = (getattr(host, 'meter_state', None) or {}).get('ctx')
    # A native pct without a token window is display-only; notices still require percent().
    if percent(ctx) is None and display_percent(ctx) is None:
        return None
    # An explicitly inconsistent token window is not a trustworthy phone reading either.
    if display_percent(ctx) is not None and ctx.get('used') is not None and ctx.get('max') is not None \
            and percent(ctx) is None:
        return None
    agent = getattr(host, 'agent', None)
    if not agent:
        return None
    from .heartbeat import session_key
    if ctx.get('session_key') is not None and ctx['session_key'] != session_key(agent):
        return None
    kind = getattr(agent, 'kind', None)
    if kind == 'codex':
        sid = getattr(agent, 'tid', None)
    elif kind in ('acp', 'kimi', 'qwen', 'dsh', 'opencode'):
        sid = getattr(agent, 'sid', None)
    else:
        sid = (getattr(agent, 'session', None) or {}).get('sessionId')
    if ctx.get('session_id') is not None and ctx['session_id'] != sid:
        return None
    at = ctx.get('at')
    if at is not None and (not number(at) or at > time.time() + 60 or time.time() - at > MAX_AGE):
        return None
    model = ctx.get('model_id')
    if model is not None:
        selected = (getattr(agent, 'cfg', None) or {}).get('model')
        if selected and (getattr(agent, 'cfg', None) or {}).get('session_mode') != 'shared' and model != selected:
            return None
        if (getattr(agent, 'cfg', None) or {}).get('session_mode') == 'shared':
            native = getattr(agent, '_native_last', None)
            if isinstance(native, dict) and native.get('source_at') != ctx.get('at'):
                return None
    return ctx
