"""P426 #57: OpenCode sub-agents under the phone's rules (ADR-A168 "Sub-agents", G-A65).

Which rules a sub-agent session runs on (source, checked live — reports/qa/p426/opencode/):
- v2 (2.0.0 – 2.1.1 `core/src/session.ts` create): a child session takes its parent's session rules (`permissions ??
  parent.permissions`; the subagent tool passes none) and evaluates `its agent's rules + those` — ours come last and start
  with `* * ask`, so a sub-agent asks exactly like the main session. Nothing to inject: `task <n> allow` (→ `subagent`) for
  every agent that can run as one; the adapter checks each new child's own rules (session.created) and stops one without ours.
- v1 (1.18 `agent/subagent-permissions.ts`): the child gets only the parent's denies + external_directory rules, on top of
  its agent's own rules (general: `* allow`). So in high-risk mode each agent gets rules equal to the main session's,
  injected through OPENCODE_CONFIG_CONTENT `agent.<n>.permission` (the last layer below managed settings), and `task <n>
  allow` is written only for an agent whose live rules (GET /agent) end with exactly that injection; else `task * deny`.

inj(A) = ours (+ OpenCode's own scratch folders) + lifted(A's own rules) + the primary agent's denies. lifted keeps every
deny of A; an allow / ask of A after a deny becomes "ask, then ours inside it" — a deny stays a deny, a narrower allow under
a wide deny (explore: `* deny`, then grep / read / bash allow) becomes what the main session does there, nothing is looser
than ours. inj starts with `* * ask`, so A's earlier rules are shadowed: verified = A's live rules end with inj.

v1 configs are deep-merged objects, not lists: v1_object() regroups inj per permission (`*` first, then each literal name
holding every rule that matches it) — the same answers as the list; a non-universal wildcard name (or a pattern on an
action-only key) cannot be written → that agent stays denied. The owner's own `agent.<n>.permission` keys interleave with
ours in the merge → the suffix check fails → denied (doctor says why).
"""
from __future__ import annotations

import json
import re

from .agent_opencode import _own_dir_rule, _rule, evaluate, our_rules

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")      # also: no `*` / `?`, so `task <n> allow` names one agent
OC_TITLE = re.compile(r"\s*\(@([^)]{1,64}) subagent\)\s*$")   # v1's child title "<description> (@<agent> subagent)"


def _ok(r) -> bool:
    return isinstance(r, dict) and all(isinstance(r.get(k), str) for k in ("permission", "pattern", "action"))


def _wild(p: str) -> bool:
    return "*" in p or "?" in p


def lifted(human) -> list[dict]:
    """Every deny of `human`; each allow / ask after a deny → "ask, then ours inside it" when that region is a whole
    permission (or everything) or ours has the exact pattern; else it is left out (the deny before it stays: stricter —
    and OpenCode's per-start list of skill folders does not change the result from one start to the next)."""
    ours, out, deny_seen = our_rules(), [], False
    for r in human:
        if not _ok(r):
            continue
        p, x = r["permission"], r["pattern"]
        if r["action"] == "deny":
            deny_seen = True
            out.append(_rule(p, x, "deny"))
        elif deny_seen and not (_wild(p) and p != "*"):   # an allow / ask before any deny is shadowed by ours anyway
            inside = ours if p == "*" == x else [o for o in ours if o["permission"] == p and x in ("*", o["pattern"])]
            if x == "*" or inside:
                out += [_rule(p, x, "ask")] + inside
    return out


def denies(rules) -> list[dict]:
    """The primary agent's denies, as session_rules re-applies them (OpenCode's own question deny that is lifted later: no)."""
    rules = [r for r in rules or [] if _ok(r)]
    q_off = evaluate("question", "*", rules) == "deny"
    return [_rule(r["permission"], r["pattern"], "deny") for r in rules
            if r["action"] == "deny" and (r["permission"] != "question" or q_off)]


def _dedup(rules) -> list[dict]:
    out, seen = [], set()
    for r in reversed(rules):                       # keep the LAST copy (only the last one can decide)
        k = (r["permission"], r["pattern"], r["action"])
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out[::-1]


def agent_rules_for(human, primary_denies) -> list[dict]:
    human = [r for r in human or [] if _ok(r)]
    own = [_rule(r["permission"], r["pattern"], r["action"]) for r in human if _own_dir_rule(r)]
    return _dedup(our_rules() + own + lifted(human) + list(primary_denies))


def v1_object(rules) -> dict | None:
    keys = []
    for r in rules:
        p = r["permission"]
        if p != "*" and _wild(p):
            return None
        if p != "*" and p not in keys:
            keys.append(p)

    def proj(names):
        d = {}
        for r in rules:
            if r["permission"] in names:
                d.pop(r["pattern"], None)
                d[r["pattern"]] = r["action"]
        return d
    obj = {"*": proj({"*"}), **{k: proj({"*", k}) for k in keys}}
    for k in ACTION_ONLY & set(obj):      # v1's config schema takes only "ask" | "allow" | "deny" for these
        if set(obj[k]) != {"*"}:
            return None
        obj[k] = obj[k]["*"]
    return obj


ACTION_ONLY = {"todowrite", "question", "webfetch", "websearch", "doom_loop"}   # v1 core/v1/config/permission.ts


def _key(rules) -> list:
    return [(r.get("permission"), r.get("pattern"), r.get("action")) for r in rules if isinstance(r, dict)]


def expected(inj) -> list:
    """What GET /agent shows at the end of an agent's rules when inj was injected (fromConfig order of v1_object)."""
    obj = v1_object(inj)
    return [(k, p, a) for k, d in obj.items() for p, a in (d.items() if isinstance(d, dict) else [("*", d)])] \
        if obj is not None else []


def split(rules, inj) -> tuple[list, bool]:
    """(the agent's own rules, True when inj is in place). OpenCode may add rules after the config's (v1: its scratch
    folder allow; a plugin's deny): inj counts as in place when only denies and that scratch
    allow follow it — they can only tighten — and they belong to the agent's own rules (the same list on every start)."""
    rules = [r for r in rules or [] if _ok(r)]
    exp, keys, t = expected(inj) if inj else [], _key(rules), 0
    n, m = len(exp), len(rules)
    while exp and t + n <= m:
        if keys[m - n - t:m - t] == exp:
            return rules[:m - n - t] + rules[m - t:], True
        r = rules[m - 1 - t]
        if not (r["action"] == "deny" or (r["action"] == "allow" and _own_dir_rule(r))):
            break
        t += 1
    return rules, False


def capable(a) -> bool:
    return isinstance(a, dict) and isinstance(a.get("name"), str) and NAME.fullmatch(a["name"]) is not None \
        and a.get("mode") != "primary" and isinstance(a.get("permission"), list)


def plan(agents, primary: str | None, injected: dict) -> tuple[dict, list, dict]:
    """→ (inj per agent to start with, agents verified now, why the others are not). `agents` = GET /agent's
    [{name, mode, permission}]. A name not injected yet asks for a restart (want differs); a broken suffix keeps what we injected."""
    by = {a.get("name"): a for a in agents if isinstance(a, dict)}
    prim = by.get(primary) or {}
    p_own, _ = split(prim.get("permission"), injected.get(primary))
    pd = denies(p_own)
    want, ok, why = {}, [], {}
    for a in agents:
        if not capable(a):
            continue
        n = a["name"]
        own, intact = split(a["permission"], injected.get(n))
        if n in injected and not intact:
            want[n], why[n] = injected[n], "conflict"       # the owner's own per-agent keys interleave / a layer after ours
            continue
        w = agent_rules_for(own, pd)
        if v1_object(w) is None:
            why[n] = "wildcard"
            continue
        want[n] = w
        if intact and injected.get(n) == w:
            ok.append(n)
        else:
            why[n] = "pending"
    return want, ok, why


def content_with(content: str, inj: dict) -> str:
    """OPENCODE_CONFIG_CONTENT (already JSON) + our per-agent rules (v1 `agent.<n>.permission`, replacing any there)."""
    try:
        cfg = json.loads(content) if content else {}
    except ValueError:
        cfg = {}
    cfg = cfg if isinstance(cfg, dict) else {}
    ags = cfg.get("agent") if isinstance(cfg.get("agent"), dict) else {}
    for n, rules in inj.items():
        obj = v1_object(rules)
        if obj is not None:
            a = ags.get(n) if isinstance(ags.get(n), dict) else {}
            ags[n] = {**{k: v for k, v in a.items() if k != "permission"}, "permission": obj}
    cfg["agent"] = ags
    return json.dumps(cfg, separators=(",", ":"))


def origin(child: dict | None, lang: str) -> str | None:
    """The card's last line when a sub-agent asks (P172's 「来自子 Agent」 line, as for Claude's sub-agents)."""
    if not isinstance(child, dict):
        return None
    who = " · ".join(x for x in (child.get("agent"), child.get("title")) if isinstance(x, str) and x)[:80] or "?"
    return ("From sub-agent: " if lang == "en" else "来自子 Agent：") + who


def state_text(st: dict | None, lang: str = "zh") -> str:
    st = st or {}
    names = "、".join(st.get("names") or []) if lang != "en" else ", ".join(st.get("names") or [])
    if st.get("mode") == "on":
        return f"OpenCode subagents: on ({names})" if lang == "en" else f"OpenCode 子 Agent：已开启（{names}）"
    if st.get("mode") == "native":
        return ("OpenCode subagents: on (OpenCode's own rules)" if lang == "en"
                else "OpenCode 子 Agent：已开启（OpenCode 自己的权限规则）")
    why = {"research": ("只读定时任务", "read-only scheduled run"), "shared": ("共享会话", "shared session"),
           "none": ("没有可核实的子 Agent", "none could be verified")}.get(st.get("why"), ("规则未核实", "rules not verified"))
    return f"OpenCode subagents: off ({why[1]})" if lang == "en" else f"OpenCode 子 Agent：已关闭（{why[0]}）"
