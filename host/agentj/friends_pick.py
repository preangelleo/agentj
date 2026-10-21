"""P426 #117: `agentj friends pick` — which of the owner's own devices should take the next job.

Only friends whose Same owner relationship is active on BOTH sides take part (same_owner.SameOwner.active); every other
friend is invisible here, not even listed. Their numbers come from the existing private status request
(`same_owner.status`, the one `agentj friends status` uses) — aggregates only, never conversations, paths or credentials.
All devices are asked at once; one that does not answer in time is shown as unreachable and never picked. This host can be a
candidate too ("self"): its numbers come from the same aggregate() without a network round-trip.

The owner's rules live in the friends settings file (`peer/settings.json` key `pick_rules`, written by FriendStore.set_setting
like `relay-policy`; no new store):

    {"order": ["<Agent ID>" | "self", ...],          # priority, first eligible wins
     "pace": 14.29,                                    # weekly quota "on pace" = pace % per elapsed day (default 100/7)
     "machines": {"<Agent ID>" | "self": {
         "week": 1.0,          # weekly use ≤ week × on-pace progress   (null = do not check)
         "load": 0.7,          # load average per CPU core ≤ this        (null = do not check)
         "mem": 4,             # available memory ≥ this many GiB        (null = do not check)
         "in_use": false,      # may somebody be using that device right now
         "sizes": ["small", "medium"]}}}               # task sizes that device takes (missing = all)

Without rules: same-owner devices that nobody is using and whose load is below 1 per core, lowest load first, then the most
weekly headroom; this host is the fallback. A threshold whose number is unknown does not pass (an unknown is never "low").
This host is never ruled out for "in use": the Agent asking is itself a turn on it.

It only recommends. Nothing is sent: the Agent still hands the job over with `agentj friends tell`.
"""
from __future__ import annotations

import asyncio
import copy
import math

SIZES = ("small", "medium", "large")
SELF = "self"
SELF_WORDS = ("self", "本机", "me", "this")
DEFAULT_PACE = round(100 / 7, 2)
DEFAULT_MAX_LOAD = 1.0          # default mode: below one runnable job per core
MAX_ORDER = 32
RULE_KEYS = ("week", "load", "mem", "in_use", "sizes")
LIMITS = {"week": (0.05, 10.0), "load": (0.0, 64.0), "mem": (0.0, 4096.0), "pace": (1.0, 100.0)}
STATUS_TIMEOUT = 10             # same_owner.status already gives up after this many seconds


# ------------------------------------------------------------------ rules (validation + editing; pure functions)
def _num(v, key):
    lo, hi = LIMITS[key]
    return type(v) in (int, float) and math.isfinite(v) and lo <= v <= hi


def valid(rules) -> bool:
    """The exact shape set_setting accepts for `pick_rules` ({} = no rules)."""
    if not isinstance(rules, dict) or set(rules) - {"order", "pace", "machines"}:
        return False
    order = rules.get("order", [])
    if not (isinstance(order, list) and len(order) <= MAX_ORDER and len(set(order)) == len(order)
            and all(isinstance(x, str) and 0 < len(x) <= 64 for x in order)):
        return False
    if "pace" in rules and not _num(rules["pace"], "pace"):
        return False
    machines = rules.get("machines", {})
    if not isinstance(machines, dict) or len(machines) > MAX_ORDER:
        return False
    for mid, r in machines.items():
        if not (isinstance(mid, str) and 0 < len(mid) <= 64 and isinstance(r, dict)) or set(r) - set(RULE_KEYS):
            return False
        for k in ("week", "load", "mem"):
            if r.get(k) is not None and not _num(r[k], k):
                return False
        if "in_use" in r and type(r["in_use"]) is not bool:
            return False
        if "sizes" in r and not (isinstance(r["sizes"], list) and r["sizes"] and len(set(r["sizes"])) == len(r["sizes"])
                                 and all(s in SIZES for s in r["sizes"])):
            return False
    return True


def rules_of(store) -> dict:
    r = store.settings().get("pick_rules")
    return copy.deepcopy(r) if isinstance(r, dict) and valid(r) else {}


def _machine_ref(store, ref):
    """A name / ID / self word → (stored key, None) or (None, why)."""
    if not isinstance(ref, str) or not ref.strip():
        return None, "shape"
    if ref.strip().casefold() in SELF_WORDS:
        return SELF, None
    fid = store.find(ref)
    if fid:
        return fid, None
    return None, "ambiguous" if len(store.matches(ref)) > 1 else "not_friend"


def _parse_value(key, text):
    t = text.strip().lower()
    if key == "in_use":
        if t in ("allow", "yes", "true", "on", "允许"):
            return True
        if t in ("deny", "no", "false", "off", "不允许"):
            return False
        raise ValueError
    if key == "sizes":
        sizes = [s.strip() for s in t.split(",") if s.strip()]
        if not sizes or any(s not in SIZES for s in sizes) or len(set(sizes)) != len(sizes):
            raise ValueError
        return sizes
    if t in ("", "none", "off", "-"):
        return None
    v = float(t.rstrip("%gib").strip()) if key == "mem" else float(t)
    if not _num(v, key):
        raise ValueError
    return v


def edit(store, action: str, args: list) -> dict:
    """`agentj friends pick-rules <action> …` → the new rules (written) or {"ok": False, "why": …}."""
    rules = rules_of(store)
    args = [a for a in (args or []) if isinstance(a, str)]
    if action in (None, "", "show"):
        return {"ok": True, "rules": view(store, rules)}
    if action == "reset":
        rules = {}
    elif action == "order":
        keys = []
        for ref in args:
            key, why = _machine_ref(store, ref)
            if why:
                return {"ok": False, "why": why, "ref": ref[:64]}
            if key in keys:
                return {"ok": False, "why": "duplicate", "ref": ref[:64]}
            keys.append(key)
        if len(keys) > MAX_ORDER:
            return {"ok": False, "why": "too_many"}
        rules["order"] = keys
    elif action == "pace":
        try:
            v = float(args[0]) if len(args) == 1 else None
        except ValueError:
            v = None
        if v is None or not _num(v, "pace"):
            return {"ok": False, "why": "bad_value"}
        rules["pace"] = v
    elif action in ("set", "unset"):
        if not args:
            return {"ok": False, "why": "shape"}
        key, why = _machine_ref(store, args[0])
        if why:
            return {"ok": False, "why": why, "ref": args[0][:64]}
        machines = rules.setdefault("machines", {})
        if action == "unset":
            machines.pop(key, None)
        else:
            r = machines.setdefault(key, {})
            if len(args) < 2:
                return {"ok": False, "why": "shape"}
            for pair in args[1:]:
                k, sep, v = pair.partition("=")
                k = k.strip().replace("-", "_")
                if not sep or k not in RULE_KEYS:
                    return {"ok": False, "why": "bad_key", "ref": pair[:64]}
                try:
                    r[k] = _parse_value(k, v)
                except (ValueError, IndexError):
                    return {"ok": False, "why": "bad_value", "ref": pair[:64]}
        if len(machines) > MAX_ORDER:
            return {"ok": False, "why": "too_many"}
    else:
        return {"ok": False, "why": "shape"}
    rules = {k: v for k, v in rules.items() if v not in ([], {}, None)}
    why = store.set_setting("pick_rules", rules)
    if why:
        return {"ok": False, "why": why}
    return {"ok": True, "rules": view(store, rules)}


def view(store, rules=None) -> dict:
    """The rules as shown (`--rules`, `pick-rules show`): names next to IDs; whether they are the owner's or the default."""
    rules = rules_of(store) if rules is None else rules
    name = lambda k: "本机 / this computer" if k == SELF else store.name_of(k)  # noqa: E731
    machines = rules.get("machines", {})
    return {"source": "owner" if rules else "default", "pace": rules.get("pace", DEFAULT_PACE),
            "order": [{"id": k, "name": name(k)} for k in rules.get("order", [])],
            "machines": [{"id": k, "name": name(k), **{x: r.get(x) for x in ("week", "load", "mem")},
                          "in_use": r.get("in_use", False), "sizes": r.get("sizes", list(SIZES))}
                         for k, r in machines.items()],
            "default": {"load": DEFAULT_MAX_LOAD, "in_use": False, "fallback": SELF}}


# ------------------------------------------------------------------ facts and judgement (pure; unit-tested directly)
def facts(status: dict | None, pace: float, now: float) -> dict:
    """The aggregate numbers the rules look at. None = unknown (never treated as good)."""
    s = status or {}
    load, cores, mem = s.get("load"), s.get("cores"), s.get("memory_available")
    week = (s.get("quota") or {}).get("week") or {}
    used, reset = week.get("used_pct"), week.get("reset_at")
    expected = None
    if used is not None and reset is not None:
        at = s.get("at") if s.get("at") is not None else now
        elapsed_days = 7 - (reset - at) / 86400
        if 0 <= elapsed_days <= 7:
            expected = round(min(100.0, max(0.0, elapsed_days * pace)), 1)
    return {"week_used_pct": None if used is None else round(used, 1), "week_expected_pct": expected,
            "load_per_core": round(load / cores, 2) if load is not None and cores else None,
            "memory_available_gib": round(mem / 2 ** 30, 1) if mem is not None else None,
            "in_use": s.get("in_use"), "agents": s.get("agents"), "background": s.get("background"),
            "harness": s.get("harness"), "model": s.get("model")}


def judge(f: dict, rule: dict, size: str | None, is_self: bool) -> list[str]:
    """Why a device does NOT qualify ([] = it does). Codes, rendered by why()."""
    out = []
    if size and size not in rule.get("sizes", SIZES):
        out.append("size")
    if not is_self and f["in_use"] is not False and not rule.get("in_use", False):
        out.append("in_use" if f["in_use"] else "in_use_unknown")
    load = rule.get("load")
    if load is not None:
        if f["load_per_core"] is None:
            out.append("load_unknown")
        elif f["load_per_core"] > load:
            out.append("load")
    mem = rule.get("mem")
    if mem is not None:
        if f["memory_available_gib"] is None:
            out.append("mem_unknown")
        elif f["memory_available_gib"] < mem:
            out.append("mem")
    week = rule.get("week")
    if week is not None:
        if f["week_used_pct"] is None or f["week_expected_pct"] is None:
            out.append("week_unknown")
        elif f["week_used_pct"] > f["week_expected_pct"] * week:
            out.append("week")
    return out


def score(f: dict):
    """Default ranking: lowest load first, then most weekly headroom (unknowns sort last)."""
    load = f["load_per_core"] if f["load_per_core"] is not None else 99.0
    slack = (f["week_expected_pct"] - f["week_used_pct"]) if None not in (f["week_expected_pct"], f["week_used_pct"]) else -999.0
    return (round(load, 1), -slack)


def rank(machines: list, rules: dict, size: str | None, pace: float, now: float) -> list:
    """machines: [{"id", "name", "self", "status" | None (unreachable)}] → the same dicts with facts / reasons / rank, best
    first. The first eligible one is the recommendation."""
    owner = bool(rules)
    order = rules.get("order", []) if owner else []
    per = rules.get("machines", {}) if owner else {}
    for m in machines:
        m["reachable"] = m.get("status") is not None
        m["facts"] = facts(m.get("status"), pace, now)
        key = SELF if m["self"] else m["id"]
        rule = per.get(key) or ({} if m["self"] else {"load": DEFAULT_MAX_LOAD})
        m["rule"] = key in per
        m["reasons"] = ["offline"] if not m["reachable"] else judge(m["facts"], rule, size, m["self"])
        m["eligible"] = not m["reasons"]
        m["listed"] = key in order

    def place(m):
        key = SELF if m["self"] else m["id"]
        if key in order:
            return (0, order.index(key), ())
        # not in the owner's order (or no rules): default ranking, this host after every other device
        return (1, int(m["self"]), score(m["facts"]))
    ordered = sorted(machines, key=place)
    n = 0
    for m in ordered:
        m["rank"] = (n := n + 1) if m["eligible"] else None
        m["recommended"] = m["rank"] == 1
    return ordered


# ------------------------------------------------------------------ plain words
def _fmt(v):
    return "—" if v is None else (f"{v:.1f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v))


REASONS = {
    "offline": ("连不上（没回状态）", "unreachable (no status reply)"),
    "size": ("不接这种大小的活", "does not take this task size"),
    "in_use": ("有人在用", "someone is using it"),
    "in_use_unknown": ("不知道有没有人在用", "unknown whether someone is using it"),
    "load": ("负载超过上限", "load above the limit"),
    "load_unknown": ("读不到负载", "load unknown"),
    "mem": ("可用内存不够", "not enough free memory"),
    "mem_unknown": ("读不到可用内存", "free memory unknown"),
    "week": ("本周用量超过均速进度", "weekly use ahead of even pace"),
    "week_unknown": ("读不到本周用量", "weekly use unknown"),
}


def why(m: dict, lang: str) -> str:
    """「Mac M1：本周用量 31%，低于均速进度 42%，负载 0.8，没人在用 → 推荐」"""
    zh = lang != "en"
    f = m["facts"]
    name = (("本机 " if zh else "this computer ") + m["name"]) if m["self"] else m["name"]
    if not m["reachable"]:
        return f"{name}：{REASONS['offline'][0]} → 不选" if zh else f"{name}: {REASONS['offline'][1]} → skipped"
    bits = []
    if f["week_used_pct"] is not None:
        if f["week_expected_pct"] is not None:
            below = f["week_used_pct"] <= f["week_expected_pct"]
            bits.append(f"本周用量 {_fmt(f['week_used_pct'])}%，{'低于' if below else '高于'}均速进度 {_fmt(f['week_expected_pct'])}%" if zh
                        else f"weekly use {_fmt(f['week_used_pct'])}%, {'below' if below else 'above'} even pace {_fmt(f['week_expected_pct'])}%")
        else:
            bits.append(f"本周用量 {_fmt(f['week_used_pct'])}%" if zh else f"weekly use {_fmt(f['week_used_pct'])}%")
    if f["load_per_core"] is not None:
        bits.append(f"负载 {_fmt(f['load_per_core'])}/核" if zh else f"load {_fmt(f['load_per_core'])}/core")
    if f["memory_available_gib"] is not None:
        bits.append(f"可用内存 {_fmt(f['memory_available_gib'])} GB" if zh else f"{_fmt(f['memory_available_gib'])} GB free")
    if f["in_use"] is not None and not m["self"]:
        bits.append(("有人在用" if f["in_use"] else "没人在用") if zh else ("in use" if f["in_use"] else "nobody using it"))
    head = name + ("：" if zh else ": ") + ("，" if zh else ", ").join(bits or (["没有可用的数字"] if zh else ["no numbers available"]))
    if m["recommended"]:
        tail = "推荐" if zh else "recommended"
    elif m["eligible"]:
        tail = f"备选（第 {m['rank']} 位）" if zh else f"alternative (#{m['rank']})"
    else:
        r = "、".join(REASONS[c][0] for c in m["reasons"]) if zh else ", ".join(REASONS[c][1] for c in m["reasons"])
        tail = ("不选：" if zh else "skipped: ") + r
    return head + " → " + tail


def public(m: dict) -> dict:
    """One machine as it goes out (--json): a stable key set, never the raw status object."""
    return {"id": m["id"], "name": m["name"], "self": m["self"], "reachable": m["reachable"], "eligible": m["eligible"],
            "recommended": m["recommended"], "rank": m["rank"], "listed": m["listed"], "rule": m["rule"],
            "reasons": list(m["reasons"]), "facts": m["facts"], "why": {"zh": why(m, "zh"), "en": why(m, "en")}}


# ------------------------------------------------------------------ the request (inside serve)
async def _self_status(svc):
    from .busy import _mem_available_mib
    from .same_owner import aggregate
    try:
        memory = await asyncio.to_thread(_mem_available_mib)
    except Exception:  # noqa: BLE001 — unknown memory, never a failed pick
        memory = None
    return aggregate(svc.host, memory)


async def _friend_status(svc, fid):
    try:
        res = await asyncio.wait_for(svc.same_owner.status(fid), STATUS_TIMEOUT + 2)
    except (asyncio.TimeoutError, OSError):
        return None
    return res.get("status") if isinstance(res, dict) and res.get("ok") else None


async def run(svc, req: dict) -> dict:
    """friends.sock `{"t": "pick", "size": …, "self": bool}` → the recommendation."""
    size = req.get("size")
    if size is not None and size not in SIZES:
        return {"ok": False, "why": "bad_size"}
    store = svc.store
    if svc.host.stopped():
        return {"ok": False, "why": "stopped"}
    rules = rules_of(store)
    pace = rules.get("pace", DEFAULT_PACE)
    fids = [fid for fid in store.friends() if svc.same_owner.active(fid)]
    stats = await asyncio.gather(*(_friend_status(svc, fid) for fid in fids), _self_status(svc))
    machines = [{"id": fid, "name": store.name_of(fid), "self": False, "status": s} for fid, s in zip(fids, stats)]
    if req.get("self", True):
        me = svc.keys.id if svc.keys else SELF
        machines.append({"id": me, "name": svc.st.agent_name() or "Agent J", "self": True, "status": stats[-1]})
    ordered = rank(machines, rules, size, pace, svc.clock())
    best = next((m for m in ordered if m["recommended"]), None)
    out = [public(m) for m in ordered]
    zh_none, en_none = "没有合适的设备：先等一等，或改一下规则（agentj friends pick-rules）。", \
        "No device qualifies: wait a little, or change the rules (agentj friends pick-rules)."
    summary = {"zh": (why(best, "zh") if best else zh_none), "en": (why(best, "en") if best else en_none)}
    svc.log("friends_pick", result="picked" if best else "none", n=len(out))
    return {"ok": True, "task_size": size, "rules": "owner" if rules else "default", "pace": pace,
            "pick": ({"id": best["id"], "name": best["name"], "self": best["self"]} if best else None),
            "machines": out, "summary": summary,
            "next": "agentj friends tell <friend> <task>"}
