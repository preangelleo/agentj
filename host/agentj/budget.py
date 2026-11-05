"""0.26.9 #473 (E3): the owner's day / month spending budget. `<state>/budget.json` (0600).

What counts as spend: a request whose danger class is `spend` / `price` (danger.py) or whose registry action is `payment`
(policy_registry). In budget = the host knows the EXACT amount (from a host-held source, never the model's words or an
estimate) and it fits under both limits. No such source exists yet, so every spend is `unknown` → Face ID (the
constitution: never estimate). `spent_day` / `spent_month` are reported only when exactly known — today never.

Raising (a higher limit, a first limit — from none counts as from 0, dropping one limit while the other stays) is a widening: only the phone's signed `budget_set` with this
remote's Face ID; the record keeps that signature and is re-verified with the device's current key on every read, so a
hand-edited file widens nothing. Lowering / clearing is a narrowing: a signed tap, or `agentj budget set` on the terminal
(stored as `cap`, which can only lower the signed limits).
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

CURRENCIES = ("USD", "CNY")
MAX_AMOUNT = 1_000_000


def _path(st) -> Path:
    return Path(st.root) / "budget.json"


def _hwm_path(st) -> Path:
    return Path(st.root) / "budget.hwm"


def hwm(st) -> list:
    """Security r4 F7: the host's high-water mark [ts, nonce] of the newest stored signed record — a budget.json rolled
    back to an older (higher, still validly signed) record no longer matches it → no budget."""
    try:
        v = json.loads(_hwm_path(st).read_text())
        return v if isinstance(v, list) and len(v) == 2 else []
    except (OSError, ValueError):
        return []


def amount_ok(v) -> bool:
    return v is None or (type(v) in (int, float) and math.isfinite(v) and 0 <= v <= MAX_AMOUNT and round(v, 2) == v)


def read_raw(st) -> dict:
    try:
        d = json.loads(_path(st).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(st, d: dict) -> None:
    from .state import _write_private
    _write_private(_path(st), json.dumps(d, ensure_ascii=False, sort_keys=True).encode())


def target(day, month, currency) -> dict:
    return {"day": day, "month": month, "currency": currency}


def fields(obj: dict) -> dict:
    """The signed object of `budget_set` (PROTOCOL §42.2, web wire.js): only the fields the phone sent, among day / month."""
    return {k: obj[k] for k in ("day", "month") if k in obj}


def canonical(obj: dict) -> str:
    """wire.js canonicalJson of the sent fields: sorted keys, no spaces; an integral number without `.0` (JSON.stringify)."""
    def num(v):
        return int(v) if isinstance(v, float) and v.is_integer() else v
    return json.dumps({k: num(v) for k, v in fields(obj).items()}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _verified(st, rec) -> bool:
    from . import controls, wire
    try:
        f = rec["fields"]
        sk = st.sign_key(str(rec["device"]))
        return bool(sk) and isinstance(f, dict) and f and set(f) <= {"day", "month"} and all(amount_ok(v) for v in f.values()) \
            and isinstance(rec.get("ts"), int) and controls.verify(sk, wire.unb64u(rec["sig"]), st.config()["channel"],
                                                                   str(rec["device"]), "budget_set", str(rec.get("n")), rec["ts"],
                                                                   controls.object_digest("budget_set", f))
    except (ValueError, KeyError, TypeError, OSError):
        return False


def state(st) -> dict:
    """The budget in force: {day, month, currency}. The signed phone records (each re-verified with the device's current key)
    applied in order; any record that no longer verifies → no budget (every spend asks with Face ID). A terminal `cap` only
    lowers; clearing either axis there clears the budget."""
    d = read_raw(st)
    cur = d.get("currency") if d.get("currency") in CURRENCIES else "USD"
    recs = d.get("signed") if isinstance(d.get("signed"), list) else []
    if not recs or not all(isinstance(r, dict) and _verified(st, r) for r in recs):
        return {"day": None, "month": None, "currency": cur}
    newest = max(recs, key=lambda r: r["ts"])
    if hwm(st) != [newest["ts"], newest.get("n")]:
        return {"day": None, "month": None, "currency": cur}      # rolled back (or the mark is missing): fail closed
    out = {"day": None, "month": None, "currency": cur}
    for r in sorted(recs, key=lambda r: r["ts"]):
        out.update(r["fields"])
    cap = d.get("cap") if isinstance(d.get("cap"), dict) else {}
    if any(cap.get(k, "absent") is None for k in ("day", "month")):
        return {"day": None, "month": None, "currency": cur}
    for k in ("day", "month"):
        c = cap.get(k)
        if amount_ok(c) and c is not None and out[k] is not None and c < out[k]:
            out[k] = c
    return out


def phone_msg(st, r=None) -> dict:
    """`budget_state` (PROTOCOL §42): spent_* only when exactly known — never today."""
    s = state(st)
    return {"t": "budget_state", **({"r": r} if r else {}), "day": s["day"], "month": s["month"], "currency": s["currency"]}


def raises(old: dict, new: dict) -> bool:
    """Is `new` wider than `old` anywhere (a first limit, a higher one, another currency)?"""
    if old.get("currency") != new.get("currency") and (new.get("day") is not None or new.get("month") is not None):
        return True
    some = new.get("day") is not None or new.get("month") is not None
    for k in ("day", "month"):
        o, n = old.get(k), new.get(k)
        if n is not None and (o is None or n > o):
            return True
        if o is not None and n is None and some:
            return True         # dropping one limit while the other stays = no limit on that axis
    return False


def apply(old: dict, sent: dict) -> dict:
    """The budget after a `budget_set` carrying only `sent` (day? / month?)."""
    return {**old, **fields(sent)}


def store_signed(st, sent: dict, device: str, signed: dict) -> None:
    """Keep the newest record per axis (a record that set both axes covers both); drop the terminal cap (re-signed now)."""
    d = read_raw(st)
    recs = [r for r in (d.get("signed") if isinstance(d.get("signed"), list) else []) if isinstance(r, dict)]
    if recs and not all(_verified(st, r) for r in recs):
        recs = []
    new = {"fields": fields(sent), "device": device, "n": signed.get("n"), "ts": signed.get("ts"), "sig": signed.get("sig"),
           "at": int(time.time())}
    keep = [r for r in recs if set(r["fields"]) - set(new["fields"])]
    d["signed"] = (keep + [new])[-4:]
    d.pop("cap", None)
    _write(st, d)
    from .state import _write_private
    _write_private(_hwm_path(st), json.dumps([new["ts"], new["n"]]).encode())


def set_cap(st, day="keep", month="keep") -> dict:
    """The terminal's narrowing: never above what is in force. → the new state."""
    cur = state(st)
    d = read_raw(st)
    cap = d.get("cap") if isinstance(d.get("cap"), dict) else {}
    for k, v in (("day", day), ("month", month)):
        if v == "keep":
            continue
        if not amount_ok(v):
            raise ValueError(k)
        if v is not None and (cur[k] is None or v > cur[k]):
            raise PermissionError(k)
        cap[k] = v
    d["cap"] = cap
    _write(st, d)
    return state(st)


def verdict(budget_state, amount) -> str:
    """in | over | unset | unknown. `amount` = (value, currency) only from a host-held exact source."""
    if not isinstance(budget_state, dict) or (budget_state.get("day") is None and budget_state.get("month") is None):
        return "unset"
    if not (isinstance(amount, tuple) and len(amount) == 2 and amount_ok(amount[0]) and amount[0] is not None):
        return "unknown"
    v, cur = amount
    if cur != budget_state.get("currency"):
        return "unknown"
    left = [budget_state[k] for k in ("day", "month") if budget_state.get(k) is not None]
    return "in" if all(v <= x for x in left) else "over"


# ------------------------------------------------------------------ CLI (`agentj budget show|set`)
def _fmt(v) -> str:
    return "未设 / not set" if v is None else f"{v:g}"


def cmd(a) -> None:
    import sys
    from .state import State
    st = State()
    if a.op == "set":
        def num(x):
            if x is None:
                return "keep"
            if str(x).lower() in ("none", "off", "clear"):
                return None
            return round(float(x), 2)
        try:
            s = set_cap(st, num(a.day), num(a.month))
        except PermissionError:
            sys.exit("提高或新设预算只能在已配对手机上用 Face ID（设置 → 预算）；终端只能调低或清除。 / Raising or adding a budget "
                     "needs Face ID on a paired phone (Settings → Budget); the terminal can only lower or clear it.")
        except ValueError:
            sys.exit(f"金额要在 0–{MAX_AMOUNT}，最多两位小数 / amount 0–{MAX_AMOUNT}, at most 2 decimals")
    else:
        s = state(st)
    if getattr(a, "json", False):
        print(json.dumps(s, ensure_ascii=False))
        return
    print(f"预算 / budget ({s['currency']}): 每天 day {_fmt(s['day'])} · 每月 month {_fmt(s['month'])}")
    print("预算内的花钱仍需主机能核实确切金额；核实不了一律 Face ID。 / In-budget spending still needs an amount the host "
          "can verify; otherwise Face ID.")
