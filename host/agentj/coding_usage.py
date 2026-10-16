"""P340 D (FEEDBACK #42, 2026-10-16): the Coding Plans' own usage windows on the phone's WEEKLY / 5H rules.

「如果本身有这个数据还是要接进来的。如果是有这个数据咱们没接进来，是咱们的 bug。」 — so each OpenCode provider whose plan
publishes a usage read that works with the very API key OpenCode already uses is read here, after every turn, through the
same `provider_runtime.probe` path as P67's compatible `/v1/usage` (key by NAME from the service env file, never logged,
never sent anywhere but the provider's own host, no redirects, 8 s, ≤ 64 KiB).

| plan (base URL host)                       | read                                                     | windows            |
|--------------------------------------------|----------------------------------------------------------|--------------------|
| Kimi Code (api.kimi.com / api.kimi.ai …/coding) | GET <base>/usages, Bearer — what the official kimi-cli `/usage` calls | 5h, weekly, monthly |
| Zhipu GLM / Z.ai Coding Plan (open.bigmodel.cn / api.z.ai) | GET /api/monitor/usage/quota/limit — Zhipu's own usage plugin | 5h (+ weekly)  |
| MiniMax Coding Plan (api.minimaxi.com / .cn / api.minimax.io) | GET /v1/api/openplatform/coding_plan/remains        | 5h, weekly         |

Not here, on purpose (evidence in reports/qa/p340/d/providers.md): Volcano Ark Coding Plan — its usage
(`GetCodingPlanUsage`, `arkcli usage plan`) is a control-plane OpenAPI signed with AK/SK or SSO, the plan's API key is
data-plane only; Alibaba Bailian Coding Plan — usage is on the console page only (the console call refuses an API key with
ConsoleNeedLogin). Those return [] and the phone keeps both rules with no text.

Each window: {window: '5h'|'weekly'|'monthly'|'daily', pct: 0…100, used?, limit?, reset?: unix s}; ordered longest first is
not required (the phone puts 5h on the bottom rule).
"""
from __future__ import annotations

import datetime as _dt
import math
import time
import urllib.parse

KIMI = {"api.kimi.com", "api.kimi.ai"}
ZHIPU = {"open.bigmodel.cn", "api.z.ai"}
MINIMAX = {"api.minimaxi.com": "api.minimaxi.com", "api.minimax.cn": "api.minimaxi.com", "api.minimax.io": "api.minimax.io"}
# OpenCode's bundled models.dev plan providers (id → its base URL, the key variable it reads), so a built-in provider with
# no block in opencode.json is read too. Ids / bases / env names from OpenCode's models.dev cache (2026-10-16).
BUILTIN = {
    "kimi-code-plan-cn": ("https://api.kimi.com/coding/v1", "KIMI_API_KEY"),
    "kimi-code-plan-global": ("https://api.kimi.ai/coding/v1", "KIMI_API_KEY"),
    "kimi-for-coding": ("https://api.kimi.com/coding/v1", "KIMI_API_KEY"),
    "zhipuai-coding-plan": ("https://open.bigmodel.cn/api/coding/paas/v4", "ZHIPU_API_KEY"),
    "zai-coding-plan": ("https://api.z.ai/api/coding/paas/v4", "ZHIPU_API_KEY"),
    "minimax-coding-plan": ("https://api.minimax.io/anthropic/v1", "MINIMAX_API_KEY"),
    "minimax-cn-coding-plan": ("https://api.minimax.cn/anthropic/v1", "MINIMAX_API_KEY"),
}


def endpoint(base) -> tuple[str, str] | None:
    """(kind, url) for a provider base URL whose plan has a usage read; None otherwise. https only, no credentials in URL."""
    if not isinstance(base, str):
        return None
    try:
        u = urllib.parse.urlsplit(base)
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    if u.scheme != "https" or u.username or u.password or u.port not in (None, 443):
        return None
    path = u.path.rstrip("/")
    if host in KIMI:
        if not path.startswith("/coding"):
            return None                      # the Moonshot open platform (pay as you go) has no plan windows
        return "kimi", f"https://{host}/coding/v1/usages"
    if host in ZHIPU:
        return "zhipu", f"https://{host}/api/monitor/usage/quota/limit"
    if host in MINIMAX:
        return "minimax", f"https://{MINIMAX[host]}/v1/api/openplatform/coding_plan/remains"
    return None


def headers(kind: str, key: str) -> dict:
    # Zhipu's own plugin sends the raw key; its gateway accepts "Bearer <key>" as well (CodexBar, verified 2026-08).
    return {"Authorization": "Bearer " + key}


# ------------------------------------------------------------------ helpers
def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else None
    if isinstance(v, str) and v.strip():
        try:
            f = float(v.strip())
        except ValueError:
            return None
        return f if math.isfinite(f) else None
    return None


def _ts(v, now: float) -> int | None:
    """ISO 8601 / unix s / unix ms → unix s, only if in the future and within 40 days."""
    t = None
    if isinstance(v, str) and v.strip() and not v.strip().lstrip("-").replace(".", "", 1).isdigit():
        s = v.strip().replace("Z", "+00:00")
        if "." in s:                          # Kimi sends nanoseconds; fromisoformat takes ≤ 6 digits
            head, _, rest = s.partition(".")
            frac = "".join(ch for ch in rest if ch.isdigit())
            tz = rest[len(frac):]
            s = head + "." + frac[:6] + tz
        try:
            d = _dt.datetime.fromisoformat(s)
        except ValueError:
            return None
        if d.tzinfo is None:
            d = d.replace(tzinfo=_dt.timezone.utc)
        t = d.timestamp()
    else:
        n = _num(v)
        if n is None:
            return None
        t = n / 1000 if n > 1e12 else n
    return int(t) if now < t <= now + 40 * 86400 else None


def _window(minutes) -> str | None:
    m = _num(minutes)
    if m is None:
        return None
    return {300: "5h", 1440: "daily", 10080: "weekly"}.get(int(m)) or ("monthly" if 28 * 1440 <= m <= 31 * 1440 else None)


def _row(window, pct=None, used=None, limit=None, reset=None) -> dict | None:
    used, limit, pct = _num(used), _num(limit), _num(pct)
    if pct is None and used is not None and limit and limit > 0 and used >= 0:
        pct = used / limit * 100
    if window is None or pct is None or pct < 0:
        return None
    out = {"window": window, "pct": min(100.0, pct)}
    if used is not None and limit and limit > 0 and used >= 0:
        out.update(used=used, limit=limit)
    if reset:
        out["reset"] = reset
    return out


def _dedupe(rows) -> list:
    out, seen = [], set()
    for r in rows:
        if r and r["window"] not in seen:
            seen.add(r["window"])
            out.append(r)
    return out[:4]


# ------------------------------------------------------------------ parsers (pure; tests feed them recorded shapes)
def _kimi_detail(d: dict):
    if not isinstance(d, dict):
        return None, None, None
    limit = _num(d.get("limit"))
    used = _num(d.get("used"))
    if used is None and limit is not None and _num(d.get("remaining")) is not None:
        used = limit - _num(d.get("remaining"))
    reset = next((d.get(k) for k in ("resetTime", "reset_time", "resetAt", "reset_at") if d.get(k)), None)
    return used, limit, reset


def kimi(data, now: float | None = None) -> list:
    """Two shapes on the same endpoint: ratio pools `usages.limit_5h / limit_7d / limit_month_total {used_ratio, reset_time}`
    and the older counts `usage` (the weekly pool) + `limits[] {window {duration, timeUnit}, detail}`."""
    now = time.time() if now is None else now
    if not isinstance(data, dict):
        return []
    rows = []
    pools = data.get("usages")
    if isinstance(pools, dict):
        for key, w in (("limit_5h", "5h"), ("limit_7d", "weekly"), ("limit_month_total", "monthly")):
            p = pools.get(key)
            r = _num(p.get("used_ratio")) if isinstance(p, dict) else None
            if r is not None and r >= 0:
                rows.append(_row(w, pct=min(1.0, r) * 100, reset=_ts(p.get("reset_time") or p.get("resetTime"), now)))
    for lim in data.get("limits") if isinstance(data.get("limits"), list) else []:
        if not isinstance(lim, dict):
            continue
        win = lim.get("window") if isinstance(lim.get("window"), dict) else {}
        unit = {"TIME_UNIT_MINUTE": 1, "TIME_UNIT_HOUR": 60, "TIME_UNIT_DAY": 1440}.get(win.get("timeUnit"))
        dur = _num(win.get("duration"))
        used, limit, reset = _kimi_detail(lim.get("detail"))
        rows.append(_row(_window(dur * unit) if unit and dur else None, used=used, limit=limit, reset=_ts(reset, now)))
    if isinstance(data.get("usage"), dict):
        used, limit, reset = _kimi_detail(data["usage"])
        rows.append(_row("weekly", used=used, limit=limit, reset=_ts(reset, now)))
    return _dedupe(rows)


def zhipu(data, now: float | None = None) -> list:
    """`data.limits[] {type, unit, number, usage, currentValue, remaining, percentage, nextResetTime(ms)}`; token / credit
    windows only (TIME_LIMIT counts MCP tool calls, not the plan). unit 3 = hours, 1 = days, 6 = weeks, 5 = minutes."""
    now = time.time() if now is None else now
    body = data.get("data") if isinstance(data, dict) else None
    lims = body.get("limits") if isinstance(body, dict) else None
    rows = []
    for lim in lims if isinstance(lims, list) else []:
        if not isinstance(lim, dict) or lim.get("type") not in ("TOKENS_LIMIT", "CREDIT_LIMIT"):
            continue
        mult = {1: 1440, 3: 60, 5: 1, 6: 10080}.get(lim.get("unit")) if isinstance(lim.get("unit"), int) else None
        number = _num(lim.get("number"))
        w = _window(number * mult) if mult and number else None
        total, cur, rem = _num(lim.get("usage")), _num(lim.get("currentValue")), _num(lim.get("remaining"))
        used = cur if cur is not None else (total - rem if total is not None and rem is not None else None)
        pct = None if used is not None and total else _num(lim.get("percentage"))
        rows.append(_row(w, pct=pct, used=used, limit=total, reset=_ts(lim.get("nextResetTime"), now)))
    return _dedupe(sorted([r for r in rows if r], key=lambda r: r["window"] != "5h"))


def minimax(data, now: float | None = None) -> list:
    """`model_remains[]`: only the published remaining percentages (`current_interval_remaining_percent` = the 5 h
    interval, `current_weekly_remaining_percent`), the busiest model row; the *_usage_count fields are left alone because
    they are the remaining count despite the name (CodexBar)."""
    now = time.time() if now is None else now
    rows = data.get("model_remains") if isinstance(data, dict) else None
    best = {}
    for m in rows if isinstance(rows, list) else []:
        if not isinstance(m, dict):
            continue
        for w, pk, rk in (("5h", "current_interval_remaining_percent", "end_time"),
                          ("weekly", "current_weekly_remaining_percent", "weekly_end_time")):
            rem = _num(m.get(pk))
            if rem is None or not 0 <= rem <= 100:
                continue
            r = _row(w, pct=100 - rem, reset=_ts(m.get(rk), now))
            if r and (w not in best or r["pct"] > best[w]["pct"]):
                best[w] = r
    return [best[w] for w in ("5h", "weekly") if w in best]


PARSE = {"kimi": kimi, "zhipu": zhipu, "minimax": minimax}


def parse(kind: str, data, now: float | None = None) -> list:
    fn = PARSE.get(kind)
    try:
        return fn(data, now) if fn else []
    except (TypeError, ValueError, AttributeError, OverflowError):
        return []
