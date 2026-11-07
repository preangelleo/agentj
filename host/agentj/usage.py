"""Spend and speed of every turn and background task (P204, 0.21; PROTOCOL §29).

What one turn cost and how fast it went, from the numbers the harness itself reports: duration, tokens (input / output /
cache read / cache write), the harness's own dollar figure when it gives one (Claude Code `total_cost_usd`, OpenCode
`cost`) or unknown, and — on a subscription login, where dollars are not what the owner
pays — the share of the 5-hour quota the turn used (the harness's own utilisation number before and after). Nothing here
is guessed: a number the harness did not give stays out (the phone shows "—"); an estimate is marked `est`.

The phone gets each turn's figures on its history page (`use`, inside the end-to-end session); `agentj usage` sums them by
day / week. Everything stays on this computer: `<state>/usage/YYYY-MM-DD.jsonl` (dir 0700, files 0600), kept 400 days, no
upload of any kind — not to us, not to anyone.

Price table: USD per million tokens, `{"in", "out", "cr" (cache read), "cw" (cache write)}`. Built-in rows below (checked on
PRICES_CHECKED); the owner's rows in `<state>/prices.json` win (`agentj usage price <model> --in … --out …`). A model with no
row gets no dollar estimate.
"""
from __future__ import annotations

import json
import os
import re
import time

PRICES_CHECKED = "2026-10-13"
# Anthropic first-party list prices (claude-api reference, 2026-10). cw = 5-minute cache write (1.25 × input).
PRICES = {
    "claude-fable-5-1": {"in": 10.0, "out": 50.0, "cr": 0.25, "cw": 12.5},
    "claude-fable-5": {"in": 10.0, "out": 50.0, "cr": 1.0, "cw": 12.5},
    "claude-opus-5-5": {"in": 4.0, "out": 20.0, "cr": 0.2, "cw": 5.0},
    "claude-opus-5": {"in": 5.0, "out": 25.0, "cr": 0.5, "cw": 6.25},
    "claude-opus-4-8": {"in": 5.0, "out": 25.0, "cr": 0.5, "cw": 6.25},
    "claude-opus-4-7": {"in": 5.0, "out": 25.0, "cr": 0.5, "cw": 6.25},
    "claude-opus-4-6": {"in": 5.0, "out": 25.0, "cr": 0.5, "cw": 6.25},
    "claude-sonnet-5-5": {"in": 2.0, "out": 10.0, "cr": 0.2, "cw": 2.5},
    "claude-sonnet-5": {"in": 2.0, "out": 10.0, "cr": 0.2, "cw": 2.5},
    "claude-sonnet-4-6": {"in": 3.0, "out": 15.0, "cr": 0.3, "cw": 3.75},
    "claude-haiku-5-5": {"in": 0.1, "out": 0.5, "cr": 0.01, "cw": 0.125},
    "claude-haiku-4-5": {"in": 1.0, "out": 5.0, "cr": 0.1, "cw": 1.25},
}
PRICE_KEYS = ("in", "out", "cr", "cw")
TOKEN_KEYS = ("i", "o", "cr", "cw")
KEEP_DAYS = 400
MAX_DAY = 4 * 1024 * 1024
_DAY = re.compile(r"(\d{4}-\d{2}-\d{2})\.jsonl")
_MODEL = re.compile(r"[A-Za-z0-9._:/@\[\]-]{1,100}")


# ------------------------------------------------------------------ prices
def norm_model(m) -> str | None:
    """`anthropic/claude-opus-5-5[1m]`, `claude-opus-5-5-20260401`, `us.anthropic.claude-opus-5-5` → `claude-opus-5-5`."""
    if not isinstance(m, str) or not _MODEL.fullmatch(m.strip()):
        return None
    m = m.strip().lower()
    m = re.sub(r"\[[^\]]*\]$", "", m)
    m = m.rsplit("/", 1)[-1]
    m = re.sub(r"^(?:[a-z]{2}\.)?anthropic\.", "", m)
    m = re.sub(r"[-@]\d{8}(?:-v\d+(?::\d+)?)?$", "", m)
    return m or None


def _path(st):
    return st.root / "prices.json"


def owner_prices(st) -> dict:
    try:
        d = json.loads(_path(st).read_text())
    except (OSError, ValueError):
        return {}
    out = {}
    for k, v in (d.items() if isinstance(d, dict) else ()):
        row = _row(v)
        if norm_model(k) and row:
            out[norm_model(k)] = row
    return out


def _row(v) -> dict | None:
    if not isinstance(v, dict):
        return None
    row = {}
    for k in PRICE_KEYS:
        x = v.get(k)
        if x is None:
            continue
        if isinstance(x, bool) or not isinstance(x, (int, float)) or not 0 <= x <= 10000:
            return None
        row[k] = float(x)
    return row if "in" in row and "out" in row else None


def table(st=None) -> dict:
    t = {k: dict(v, src="built-in") for k, v in PRICES.items()}
    if st is not None:
        for k, v in owner_prices(st).items():
            t[k] = dict(v, src="owner")
    return t


def price_of(model, st=None, tab: dict | None = None) -> dict | None:
    """Exact id first, then the longest table key the id starts with (`claude-opus-5-5-fast` → no; a dated id is cut
    first by norm_model)."""
    m = norm_model(model)
    if not m:
        return None
    tab = tab if tab is not None else table(st)
    if m in tab:
        return tab[m]
    return None


def set_price(st, model: str, row: dict | None) -> None:
    m = norm_model(model)
    if not m:
        raise ValueError("model")
    d = owner_prices(st)
    if row is None:
        d.pop(m, None)
    else:
        r = _row(row)
        if not r:
            raise ValueError("price")
        d[m] = r
    st.write_private(_path(st), json.dumps(d, indent=1, sort_keys=True).encode())


def estimate(toks: dict, price: dict | None) -> float | None:
    if not price:
        return None
    i, o, cr, cw = (toks.get(k) or 0 for k in TOKEN_KEYS)
    if not any((i, o, cr, cw)):
        return None
    usd = (i * price["in"] + o * price["out"] + cr * price.get("cr", price["in"]) + cw * price.get("cw", price["in"])) / 1e6
    return round(usd, 6)


# ------------------------------------------------------------------ one turn
def _n(x) -> int:
    return x if type(x) is int and x >= 0 else 0


def tokens_claude(u) -> dict:
    """Claude Code `result.usage` (Anthropic shape)."""
    u = u if isinstance(u, dict) else {}
    return {"i": _n(u.get("input_tokens")), "o": _n(u.get("output_tokens")),
            "cr": _n(u.get("cache_read_input_tokens")), "cw": _n(u.get("cache_creation_input_tokens"))}


def tokens_codex(t) -> dict:
    """Codex app-server TokenUsageBreakdown: inputTokens includes the cached part."""
    t = t if isinstance(t, dict) else {}
    cached = _n(t.get("cachedInputTokens"))
    return {"i": max(0, _n(t.get("inputTokens")) - cached), "o": _n(t.get("outputTokens")) + _n(t.get("reasoningOutputTokens")),
            "cr": cached, "cw": 0}


def tokens_opencode(t) -> dict:
    t = t if isinstance(t, dict) else {}
    c = t.get("cache") if isinstance(t.get("cache"), dict) else {}
    return {"i": _n(t.get("input")), "o": _n(t.get("output")) + _n(t.get("reasoning")), "cr": _n(c.get("read")),
            "cw": _n(c.get("write"))}


def sub_tokens(a: dict, b: dict) -> dict:
    return {k: max(0, _n(a.get(k)) - _n(b.get(k))) for k in TOKEN_KEYS}


def usd_of(x) -> float | None:
    return round(float(x), 6) if isinstance(x, (int, float)) and not isinstance(x, bool) and 0 <= x < 1e6 else None


class Turn:
    """Collects one turn: parts by key (each part replaces its previous value, so a harness may report cumulative
    numbers), the time spent with a tool running, the first-output moment."""

    def __init__(self, harness: str | None, q0: float | None = None):
        self.t0 = time.monotonic()
        self.harness = harness
        self.parts: dict[str, dict] = {}
        self.model: str | None = None
        self.q0 = q0
        self.phase: str | None = None
        self.phase_t = self.t0
        self.tool_s = 0.0
        self.first: float | None = None

    def put(self, key: str, toks: dict | None = None, usd=None, model=None) -> None:
        p = {k: v for k in TOKEN_KEYS if type(v := (toks or {}).get(k)) is int and v >= 0}
        u = usd_of(usd)
        if u is not None:
            p["usd"] = u
        self.parts[key] = p
        if norm_model(model):
            self.model = model.strip()

    def set_phase(self, ph: str | None) -> None:
        now = time.monotonic()
        if self.phase == "tool":
            self.tool_s += now - self.phase_t
        if ph == "gen" and self.first is None:
            self.first = now - self.t0
        self.phase, self.phase_t = ph, now

    def finish(self, *, billing: str = "api", q1: float | None = None, st=None) -> dict:
        self.set_phase(None)
        secs = round(time.monotonic() - self.t0, 1)
        toks = {k: sum(p[k] for p in self.parts.values()) for k in TOKEN_KEYS
                if self.parts and all(k in p for p in self.parts.values())}
        reported = [p["usd"] for p in self.parts.values() if "usd" in p]
        use = {"s": secs, "h": self.harness, "b": billing if billing in ("api", "sub") else "api"}
        if self.model:
            use["m"] = self.model[:100]
        use.update(toks)
        if reported and len(reported) == len(self.parts):
            use["usd"] = round(sum(reported), 6)
        if self.tool_s >= 0.1:
            use["tool_s"] = round(self.tool_s, 1)
        gen = secs - self.tool_s
        if toks.get("o") and gen >= 0.5:
            use["tps"] = round(toks["o"] / gen, 1)
        if billing == "sub" and isinstance(self.q0, (int, float)) and isinstance(q1, (int, float)) and q1 >= self.q0:
            use["q"] = round(q1 - self.q0, 1)       # percentage points of the 5-hour window this turn used
        return use


def from_claude_result(ev: dict, harness: str = "claude") -> dict:
    """A finished `claude -p` run (a background task): its single `result` event → a `use` dict, billing unknown."""
    t = Turn(harness)
    model = None
    mu = ev.get("modelUsage") if isinstance(ev.get("modelUsage"), dict) else {}
    if mu:
        model = max(mu, key=lambda k: _n((mu[k] or {}).get("outputTokens")) if isinstance(mu[k], dict) else 0)
    t.put("result", tokens_claude(ev.get("usage")), ev.get("total_cost_usd"), model)
    use = t.finish()
    d = ev.get("duration_ms")
    if type(d) is int and d >= 0:
        use["s"] = round(d / 1000, 1)
    return use


# ------------------------------------------------------------------ the local log
def _dir(st):
    return st.root / "usage"


def record(st, kind: str, use: dict, **extra) -> None:
    """Append one line (`kind` turn | task). Never raises — a full disk must not stop serve."""
    try:
        d = _dir(st)
        d.mkdir(mode=0o700, exist_ok=True)
        day = time.strftime("%Y-%m-%d")
        p = d / f"{day}.jsonl"
        if p.exists() and p.stat().st_size > MAX_DAY:
            return
        row = {"ts": int(time.time() * 1000), "k": kind, **{k: v for k, v in use.items()},
               **{k: (v[:80] if isinstance(v, str) else v) for k, v in extra.items() if v is not None}}
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        _purge(st)
    except (OSError, ValueError, TypeError):
        pass


def _purge(st) -> None:
    cut = time.strftime("%Y-%m-%d", time.localtime(time.time() - KEEP_DAYS * 86400))
    for f in _dir(st).iterdir():
        m = _DAY.fullmatch(f.name)
        if m and m.group(1) < cut:
            try:
                f.unlink()
            except OSError:
                pass


def rows(st, days: int) -> list[dict]:
    cut = time.strftime("%Y-%m-%d", time.localtime(time.time() - (days - 1) * 86400))
    out = []
    d = _dir(st)
    if not d.is_dir():
        return out
    for f in sorted(d.iterdir()):
        m = _DAY.fullmatch(f.name)
        if not m or m.group(1) < cut:
            continue
        try:
            for ln in f.read_text().splitlines():
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(r, dict):
                    r["day"] = m.group(1)
                    out.append(r)
        except OSError:
            continue
    return out


def _empty() -> dict:
    return {"n": 0, "s": 0.0, "i": 0, "o": 0, "cr": 0, "cw": 0, "usd": 0.0, "est": False, "no_usd": 0, "q": 0.0, "sub": 0}


def _add(a: dict, r: dict) -> None:
    a["n"] += 1
    a["s"] = round(a["s"] + (r.get("s") or 0), 1)
    for k in TOKEN_KEYS:
        a[k] += _n(r.get(k))
    if r.get("b") == "sub":
        a["sub"] += 1
        if isinstance(r.get("q"), (int, float)):
            a["q"] = round(a["q"] + r["q"], 1)
    elif isinstance(r.get("usd"), (int, float)):
        a["usd"] = round(a["usd"] + r["usd"], 6)
        a["est"] = a["est"] or bool(r.get("est"))
    else:
        a["no_usd"] += 1


def summary(st, days: int = 1) -> dict:
    """{"days": n, "total": {...}, "by_day": {day: {...}}, "by_kind": {turn|task: {...}}}. Subscription turns add to `q`
    (quota points), never to `usd`."""
    out = {"days": days, "total": _empty(), "by_day": {}, "by_kind": {}}
    for r in rows(st, days):
        _add(out["total"], r)
        _add(out["by_day"].setdefault(r["day"], _empty()), r)
        _add(out["by_kind"].setdefault(r.get("k") or "turn", _empty()), r)
    return out


def today(st) -> dict:
    """The load card's one line: today's totals (the same numbers `agentj usage` prints)."""
    t = summary(st, 1)["total"]
    return {k: t[k] for k in ("n", "s", "i", "o", "cr", "cw", "usd", "est", "q", "sub", "no_usd")}


# ------------------------------------------------------------------ text
def ktok(n) -> str:
    n = _n(n)
    if n >= 1_000_000:
        return f"{n / 1e6:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def money(x, est=False) -> str:
    if not isinstance(x, (int, float)):
        return "—"
    s = f"${x:.4f}" if x < 0.01 else f"${x:.2f}"
    return ("≈" if est else "") + s


def line(a: dict, zh: bool = True) -> str:
    """One row of `agentj usage`."""
    parts = [f"{a['n']} {'次' if zh else 'runs'}", f"{a['s']:.0f}s",
             f"{'输入' if zh else 'in'} {ktok(a['i'])}", f"{'输出' if zh else 'out'} {ktok(a['o'])}",
             f"{'缓存' if zh else 'cache'} {ktok(a['cr'] + a['cw'])}"]
    if a["n"] - a["sub"] > 0:
        parts.append(money(a["usd"], a["est"]) if a["n"] - a["sub"] > a["no_usd"] else "—")
    if a["sub"]:
        parts.append((f"订阅额度 {a['q']:.1f}%" if zh else f"subscription quota {a['q']:.1f}%"))
    return " · ".join(parts)


def cmd_usage(args) -> None:
    import sys
    from .state import State
    st = State()
    if not st.exists():
        sys.exit(f"还没初始化：先运行 `agentj init`（状态目录 {st.root}）")
    rc = _usage(st, args)
    if rc:
        sys.exit(rc)


def _usage(st, args) -> int:
    lang = getattr(args, "lang", None)
    if not lang:
        try:
            from . import preferences
            lang = preferences.get(preferences.effective(st), "appearance.language", "zh")
        except Exception:  # noqa: BLE001 — a broken settings file must not hide the numbers
            lang = "zh"
    zh = lang != "en"
    if getattr(args, "action", None) == "prices":
        tab = table(st)
        if args.json:
            print(json.dumps({"checked_on": PRICES_CHECKED, "prices": tab}, ensure_ascii=False, indent=1))
        else:
            print(("价目表（美元 / 百万 token；内置核对于 " if zh else "Price table (USD per million tokens; built-in checked ")
                  + PRICES_CHECKED + ("）" if zh else ")"))
            for k in sorted(tab):
                r = tab[k]
                print(f"  {k}: in {r['in']} · out {r['out']} · cache read {r.get('cr', '—')} · cache write {r.get('cw', '—')}"
                      f" ({r['src']})")
        return 0
    if getattr(args, "action", None) == "price":
        try:
            if args.remove:
                set_price(st, args.model, None)
            else:
                set_price(st, args.model, {"in": args.input, "out": args.output, "cr": args.cache_read, "cw": args.cache_write})
        except ValueError as e:
            print(("价目无效：" if zh else "invalid price: ") + str(e))
            return 2
        print(("已保存 " if zh else "saved ") + str(norm_model(args.model)))
        return 0
    days = 7 if args.week else max(1, min(400, args.days or 1))
    s = summary(st, days)
    if args.json:
        print(json.dumps(s, ensure_ascii=False, indent=1))
        return 0
    head = ("今天" if days == 1 else f"最近 {days} 天") if zh else ("Today" if days == 1 else f"Last {days} days")
    print(f"{head}: " + (line(s["total"], zh) if s["total"]["n"] else ("还没有记录" if zh else "nothing recorded yet")))
    for k, a in sorted(s["by_kind"].items()):
        print(f"  {({'turn': '对话回合', 'task': '后台任务'} if zh else {'turn': 'chat turns', 'task': 'background tasks'}).get(k, k)}: "
              + line(a, zh))
    if days > 1:
        for d, a in sorted(s["by_day"].items()):
            print(f"  {d}: " + line(a, zh))
    print("（只在本机统计，不外发。订阅登录按额度占比计，不折算美元；≈ 为按价目表估算。）" if zh else
          "(Counted on this computer only, never sent anywhere. Subscription logins count quota share, not dollars; "
          "≈ = estimated from the price table.)")
    return 0


def add_parser(sub) -> None:
    x = sub.add_parser("usage", help="spend and speed of turns and background tasks (local only) / 花费与速度")
    x.add_argument("action", nargs="?", choices=["prices", "price"], help="prices = show the price table; price = set one row")
    x.add_argument("model", nargs="?", help="model id (for price)")
    x.add_argument("--days", type=int, default=1)
    x.add_argument("--week", action="store_true", help="the last 7 days")
    x.add_argument("--json", action="store_true")
    x.add_argument("--lang", choices=("zh", "en"))
    x.add_argument("--in", dest="input", type=float)
    x.add_argument("--out", dest="output", type=float)
    x.add_argument("--cache-read", type=float)
    x.add_argument("--cache-write", type=float)
    x.add_argument("--remove", action="store_true", help="drop the owner's row for this model")
    x.set_defaults(fn=cmd_usage)
