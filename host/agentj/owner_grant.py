
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time

from . import passkey, wire

V = 1
TOOL = "agentj secret grant"        # the card's tool (approvals.log, policy_registry.HOST_TOOLS)
TTL = 24 * 3600                     # #392: one signature, 24 h
SKEW = 600                          # s a peer's clock may run ahead
MAX_STEPS = 12
MAX_HOSTS = 6
MAX_SECRET_LEN = 16384
TID_LABEL = b"agentj-owner-grant-v1\n"
CAPS = ["otg1", "sx1"]
GRANT_CATS = ("spend", "delete", "send", "price")
_TID = re.compile(r"[0-9a-f]{32}")
_FID = re.compile(r"[A-Za-z0-9_-]{1,64}")
NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
ROSTER_MAX = 16


class GrantError(Exception):
    def __init__(self, why: str, detail: str = ""):
        super().__init__(f"{why}: {detail}" if detail else why)
        self.why, self.detail = why, detail


def jcs(o) -> bytes:
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def tid_of(core: dict) -> str:
    return hashlib.sha256(TID_LABEL + jcs({k: v for k, v in core.items() if k != "tid"})).hexdigest()[:32]


def _label(v, n: int = 40) -> str:
    from .text import clean_label
    return clean_label(str(v or ""))[:n] or "?"


def _text(v, n: int, what: str, required: bool = True) -> str:
    if v is None:
        v = ""
    if not isinstance(v, str) or len(v) > n or wire.text_problem(v, n) or "\n" in v or "\r" in v:
        raise GrantError("shape", what)
    v = v.strip()
    if required and not v:
        raise GrantError("shape", what + " is required")
    return v


# ---------------------------------------------------------------- steps
def secret_step(name, frm: str, to: str, dest="", src="") -> dict:
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise GrantError("shape", "secret name: a variable name like STRIPE_KEY")
    if frm == to:
        raise GrantError("shape", "a key moves between two different hosts")
    return {"cat": "secret", "name": name, "from": frm, "to": to,
            "dest": _text(dest, 300, "dest", required=False), "src": _text(src, 300, "src", required=False)}


def grant_step(host: str, g: dict) -> dict:
    from .goal import GoalError, check_grants
    try:
        raw = {k:v for k,v in g.items() if k != "exact"}
        if g.get("cat") in ("spend", "price") and isinstance(g.get("exact"), str) and not any(raw.get(k) for k in ("match", "paths", "upload", "prefix")):
            raw["match"] = [g["exact"][:120]]
        item = check_grants([raw], 1)[0]
        if item["cat"] in ("spend", "price"):
            if "usd" in item or not isinstance(g.get("exact"), str) or not g["exact"].strip() or len(g["exact"]) > 1200:
                raise GrantError("shape", "spend/price require exact full action text; usd soft budgets are not supported")
            item["exact"] = g["exact"]
        elif "exact" in g:
            raise GrantError("shape", "exact is only for spend/price")
    except GoalError as e:
        raise GrantError("shape", e.detail or e.why) from None
    return {"cat": item.pop("cat"), "host": host, **item}


def build(origin: str, hosts: dict, title: str, why: str, steps: list, lang: str, now: float | None = None) -> dict:

    now = int(time.time() if now is None else now)
    if len(hosts) > MAX_HOSTS:
        raise GrantError("shape", f"at most {MAX_HOSTS} hosts including the origin")
    if not steps or len(steps) > MAX_STEPS:
        raise GrantError("shape", f"1–{MAX_STEPS} steps")
    out = []
    for i, s in enumerate(steps):
        out.append({"i": i, **s})
    core = {"v": V, "origin": origin, "hosts": {k: _label(v) for k, v in hosts.items()}, "lang": "en" if lang == "en" else "zh",
            "title": _text(title, 120, "title"), "why": _text(why, 300, "why"), "iat": now, "exp": now + TTL,
            "nonce": secrets.token_hex(16), "steps": out}
    otg = {**core, "tid": tid_of(core)}
    check(otg, now)
    return otg


def targets(otg: dict) -> list[str]:

    t = []
    for s in otg["steps"]:
        for h in ((s["from"], s["to"]) if s["cat"] == "secret" else (s["host"],)):
            if h != otg["origin"] and h not in t:
                t.append(h)
    return t


def check(otg, now: float | None = None, *, live: bool = True) -> dict:

    now = time.time() if now is None else now
    if not isinstance(otg, dict) or set(otg) != {"v", "origin", "hosts", "lang", "title", "why", "iat", "exp", "nonce", "steps", "tid"}:
        raise GrantError("shape", "fields")
    if otg["v"] != V or otg["lang"] not in ("zh", "en") or not isinstance(otg["tid"], str) or not _TID.fullmatch(otg["tid"]):
        raise GrantError("shape", "version")
    if otg["tid"] != tid_of(otg):
        raise GrantError("bad_proof", "tid")
    hosts = otg["hosts"]
    if not isinstance(hosts, dict) or not 2 <= len(hosts) <= MAX_HOSTS or not all(
            isinstance(k, str) and _FID.fullmatch(k) and isinstance(v, str) and 0 < len(v) <= 40 for k, v in hosts.items()):
        raise GrantError("shape", "hosts")
    if otg["origin"] not in hosts:
        raise GrantError("shape", "origin")
    for k in ("title", "why"):
        _text(otg[k], 120 if k == "title" else 300, k)
    if not isinstance(otg["nonce"], str) or not re.fullmatch(r"[0-9a-f]{32}", otg["nonce"]):
        raise GrantError("shape", "nonce")
    iat, exp = otg["iat"], otg["exp"]
    if type(iat) is not int or type(exp) is not int or not 0 < exp - iat <= TTL:
        raise GrantError("shape", "lifetime")
    if live and (iat > now + SKEW or now >= exp):
        raise GrantError("expired", "outside its 24 h")
    steps = otg["steps"]
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise GrantError("shape", "steps")
    for i, s in enumerate(steps):
        if not isinstance(s, dict) or s.get("i") != i:
            raise GrantError("shape", f"steps[{i}]")
        if s.get("cat") == "secret":
            if set(s) != {"i", "cat", "name", "from", "to", "dest", "src"}:
                raise GrantError("shape", f"steps[{i}] fields")
            if s["from"] not in hosts or s["to"] not in hosts or otg["origin"] not in (s["from"], s["to"]):
                raise GrantError("shape", f"steps[{i}]: route a remote-to-remote key through the origin as two signed steps")
            secret_step(s["name"], s["from"], s["to"], s["dest"], s["src"])
        elif s.get("cat") in GRANT_CATS:
            if s.get("host") not in hosts:
                raise GrantError("shape", f"steps[{i}].host")
            g = {k: v for k, v in s.items() if k not in ("i", "host")}
            if grant_step(s["host"], g) != {k: v for k, v in s.items() if k != "i"}:
                raise GrantError("shape", f"steps[{i}] not normalized")
        else:
            raise GrantError("shape", f"steps[{i}].cat")
    if not targets(otg):
        raise GrantError("shape", "no other host")
    return otg


# ---------------------------------------------------------------- the card text (frozen for v1: hosts render the same bytes)
CAT = {"zh": {"spend": "花钱", "delete": "删除", "send": "对外发送", "price": "改价"},
       "en": {"spend": "Spend", "delete": "Delete", "send": "Send out", "price": "Change prices"}}


def _step_line(otg: dict, s: dict) -> str:
    zh, h = otg["lang"] == "zh", otg["hosts"]
    if s["cat"] == "secret":
        where = s["dest"] or ("对方默认位置" if zh else "its default place")
        src = (f"（取自 {s['src']}）" if zh else f" (from {s['src']})") if s["src"] else ""
        return (f"密钥 {s['name']}：从「{h[s['from']]}」{src}→「{h[s['to']]}」，存到 {where}" if zh else
                f"Key {s['name']}: from “{h[s['from']]}”{src} → “{h[s['to']]}”, saved to {where}")
    limits = [*([s["exact"]] if s.get("exact") else []), *s.get("match", []), *s.get("paths", []), *([" ".join(s["prefix"])] if s.get("prefix") else []),
              *([f"{s['upload']['via']} → {s['upload']['host']}"] if s.get("upload") else [])]
    lim = (" · 限：" if zh else " · only: ") + "，".join(limits) if limits else ""
    usd = f"，≤ ${s['usd']:g}" if zh and s.get("usd") else f", ≤ ${s['usd']:g}" if s.get("usd") else ""
    return (f"在「{h[s['host']]}」上{CAT['zh'][s['cat']]}：{s['what']}{lim}（最多 {s['max']} 次{usd}）" if zh else
            f"On “{h[s['host']]}” — {CAT['en'][s['cat']]}: {s['what']}{lim} (at most {s['max']}{usd})")


def card_text(otg: dict) -> str:
    zh = otg["lang"] == "zh"
    lines = [("同主人任务授权：" if zh else "Same-owner task grant: ") + otg["title"],
             ("理由：" if zh else "Why: ") + otg["why"],
             ("发起：「" + otg["hosts"][otg["origin"]] + "」· 签一次，24 小时内有效，其它主机不再弹卡" if zh else
              "Started on “" + otg["hosts"][otg["origin"]] + "” · one signature, valid 24 h; the other hosts ask no more")]
    lines += [f"{s['i'] + 1}. {_step_line(otg, s)}" for s in otg["steps"]]
    lines.append(("编号 " if zh else "Grant ") + otg["tid"])
    lines.append("不是你发起的？点拒绝，并到好友页删除该好友。" if zh else
                 "Not you? Deny, then remove this friend on the friends page.")
    return "\n".join(lines)


# ---------------------------------------------------------------- proof (the card's Face ID assertion), checked by any host
def proof_of(otg: dict, info: dict) -> dict:

    fa = info.get("fa")
    if not isinstance(fa, dict) or not info.get("summary") or not info.get("channel") or not isinstance(info.get("sig"), str):
        raise GrantError("no_passkey", "the allow carried no Face ID assertion")
    return {"tool": TOOL, "summary": info["summary"], "channel": info["channel"], "device": info["device"],
            "rid": info["rid"], "kind": info["kind"], "sig":info["sig"], "fa": {k: fa[k] for k in ("id", "cd", "ad", "sig")}}


def shown_ok(otg: dict, summary) -> bool:
    text = card_text(otg)
    return isinstance(summary, str) and len(summary.encode()) <= 24000 and (summary == text or summary.startswith(text + "\n"))


def verify(otg: dict, proof, roster: list, now: float | None = None) -> dict:

    from .approvals import shown_digest, signed_message
    check(otg, now)
    if not isinstance(proof, dict) or set(proof) != {"tool", "summary", "channel", "device", "rid", "kind", "sig", "fa"}:
        raise GrantError("bad_proof", "shape")
    if proof["tool"] != TOOL or proof.get("kind") not in ("sandbox", "perm") or not shown_ok(otg, proof["summary"]):
        raise GrantError("bad_proof", "shown text")
    if not all(isinstance(proof[k], str) and 0 < len(proof[k]) <= 64 for k in ("channel", "device", "rid", "kind")):
        raise GrantError("bad_proof", "parts")
    try:
        f = passkey.approval_fields(proof["fa"])
    except passkey.PasskeyError:
        raise GrantError("bad_proof", "assertion") from None
    entry = next((e for e in roster if isinstance(e, dict) and e.get("id") == f["id"]), None)
    if entry is None:
        raise GrantError("unknown_cred", "the signing remote is not on this friend's pinned list")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        if entry.get("dev") != proof["device"]:
            raise ValueError("device")
        Ed25519PublicKey.from_public_bytes(wire.unb64u(entry["sign_pub"])).verify(wire.unb64u(proof["sig"]),
            signed_message(proof["channel"], proof["device"], proof["rid"], "allow", shown_digest(TOOL,proof["summary"])))
        ch = passkey.elevate_challenge(proof["channel"], proof["device"], proof["rid"], proof["kind"], proof["rid"],
                                       shown_digest(TOOL, proof["summary"]))
        passkey.verify_assertion({k: entry.get(k) for k in ("alg", "spki", "rp")}, f, ch)
    except Exception:
        raise GrantError("bad_proof", "assertion") from None
    return entry


# ---------------------------------------------------------------- roster
def roster_entry(did: str, rec: dict) -> dict | None:
    pk = rec.get("pk") if isinstance(rec, dict) else None
    if not isinstance(pk, dict) or not all(isinstance(pk.get(k), str) for k in ("id", "spki", "rp")) or pk.get("alg") not in passkey.ALGS:
        return None
    return {"id": pk["id"], "alg": pk["alg"], "spki": pk["spki"], "rp": pk["rp"], "dev": did,
            "name": _label(rec.get("name") or did)}


def bind_challenge(e) -> bytes:
    core = {k: e[k] for k in ("id", "alg", "spki", "rp", "dev")}
    return hashlib.sha256(b"agentj-owner-roster-bind-v1\n" + jcs(core)).digest()


def binding_ok(e) -> bool:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        core = {k: e[k] for k in ("id", "alg", "spki", "rp", "dev")}
        Ed25519PublicKey.from_public_bytes(wire.unb64u(e["sign_pub"])).verify(wire.unb64u(e["bind"]), b"agentj-owner-roster-v1\n" + jcs(core))
        return True
    except Exception:
        return False


def entry_ok(e) -> bool:
    if not isinstance(e, dict) or set(e) - {"id", "alg", "spki", "rp", "dev", "name", "via", "bind", "sign_pub"}:
        return False
    try:
        passkey.load_spki(wire.unb64u(e.get("spki") or ""), e.get("alg"))
    except (passkey.PasskeyError, ValueError, TypeError):
        return False
    return (isinstance(e.get("id"), str) and 0 < len(e["id"]) <= 1400 and e.get("rp") in [passkey.rp_of(o) for o in passkey.WEB_ORIGINS] + _test_rps()
            and isinstance(e.get("dev"), str) and len(e["dev"]) <= 64 and isinstance(e.get("name"), str) and len(e["name"]) <= 40)


def _test_rps() -> list:
    from .envcompat import getenv
    t = getenv(passkey.TEST_ENV)
    return ["127.0.0.1", "localhost"] if t == "loopback" else [passkey.rp_of(t)] if t else []


def via_ok(via, entry: dict, pinned: list) -> bool:

    from . import pkpeer
    if not isinstance(via, dict) or set(via) - {"roster"} != {"cred", "dev", "channel", "rid", "device", "name", "x", "sign_pub", "cd", "ad", "sig"}:
        return False
    if not all(isinstance(via[k], str) and 0 < len(via[k]) <= 4096 for k in via if k != "roster") or type(via.get("roster", False)) is not bool:
        return False
    try:
        if via["sign_pub"] != entry.get("sign_pub") or via["device"] != entry["dev"] or wire.device_id(wire.unb64u(via["x"])) != via["device"]:
            return False
        approver = next((e for e in pinned if e.get("id") == via["cred"]), None)
        if approver is None:
            return False
        f = passkey.approval_fields({"id": via["cred"], "cd": via["cd"], "ad": via["ad"], "sig": via["sig"]})
        dg = pkpeer.digest(via["device"], via["name"], pkpeer.code(via["rid"], via["device"], via["x"]), via["sign_pub"], via.get("roster", False))
        ch = passkey.elevate_challenge(via["channel"], via["dev"], via["rid"], pkpeer.KIND, via["rid"], dg)
        passkey.verify_assertion({k: approver.get(k) for k in ("alg", "spki", "rp")}, f, ch)
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        binding = {k: entry[k] for k in ("id", "alg", "spki", "rp", "dev")}
        Ed25519PublicKey.from_public_bytes(wire.unb64u(via["sign_pub"])).verify(wire.unb64u(entry.get("bind") or ""), b"agentj-owner-roster-v1\n" + jcs(binding))
        return True
    except Exception:
        return False


def merge_roster(pinned: list | None, sent: list) -> tuple[list, list, list]:

    sent = [e for e in sent if entry_ok(e)][:ROSTER_MAX] if isinstance(sent, list) else []
    clean = lambda e: {k: e[k] for k in ("id", "alg", "spki", "rp", "dev", "name", "sign_pub", "bind") if k in e}   # noqa: E731
    if pinned is None:
        return [], [], []
    ids = {e["id"] for e in sent}
    kept = [e for e in pinned if e["id"] in ids]
    removed = [e["name"] for e in pinned if e["id"] not in ids]
    added = []
    for e in sent:
        if any(k["id"] == e["id"] for k in kept):
            continue
        if via_ok(e.get("via"), e, kept):
            kept.append(clean(e))
            added.append(e["name"])
    return kept[:ROSTER_MAX], added, removed


# ---------------------------------------------------------------- grants a forwarded task may use (target side)
def pseudo_goal(otg: dict, me: str, used: dict) -> dict:

    grants = []
    for s in otg["steps"]:
        if s["cat"] in GRANT_CATS and s["host"] == me:
            grants.append({k: v for k, v in s.items() if k not in ("i", "host")})
        else:
            grants.append({"cat": "-", "max": 0})       # keeps index = step number; never matches
    return {"grants": grants, "used": {str(k): v for k, v in (used or {}).items()}}


def tier(otg: dict, me: str, used: dict, tool: str, tool_input, verdict, cwd, classify=None) -> list[int] | None:

    from . import goal
    if goal.suspect(tool, tool_input, verdict):
        return None
    def scoped(text, previous):
        d = pseudo_goal(otg, me, used)
        for i, g in enumerate(d["grants"]):
            if g.get("cat") in ("spend", "price") and g.get("exact") != text:
                g["cat"] = "-"
            if i in previous:
                d["used"][str(i)] = int(d["used"].get(str(i), 0)) + previous.count(i)
        return d
    text = goal.action_text(tool, tool_input)
    if tool != "Bash" or classify is None:
        return goal.match_grant(scoped(text, []), tool, tool_input, verdict, cwd, classify)
    chunks = goal.pieces(text)
    if chunks is None:
        return None
    lead, picks = cwd, []
    for chunk in chunks:
        v = classify(tool, {"command": chunk})
        m = re.fullmatch(r"cd\s+(\S+)", chunk)
        if m and not v.cats:
            import os
            lead = os.path.realpath(os.path.join(lead or "/", os.path.expanduser(m[1].strip("'\""))))
            continue
        if not v.cats:
            continue
        if goal._SUBST.search(chunk):
            return None
        hit = goal.match_grant(scoped(chunk, picks), tool, {"command": chunk}, v, lead)
        if hit is None:
            return None
        picks += hit
    return picks or None


# Task authority lives with grants; provenance retains compatibility entry points.
def owner_task_source(agent) -> bool:

    from . import provenance as p
    from . import owner_call
    if not owner_call.live():
        return False
    if p._paired_source() is not p._UNSET:
        return False
    src = p.turn_source(agent)
    req = p._REQUEST.get()
    return req is p._UNSET or p.normalize(req) == src


def owner_grant_required(agent) -> bool:
    from . import provenance as p
    sources = [p.turn_source(agent)]
    request = p._REQUEST.get()
    if request is not p._UNSET:
        sources.append(p.normalize(request))
    return any(s.get("kind") == "same_owner" and (s.get("otg") or s.get("otg_required")) for s in sources)


def owner_grants(agent) -> tuple[str, list] | None:

    from . import provenance as p
    if p._paired_source() is not p._UNSET:
        return None
    o = p._REQUEST.get()
    if o is not p._UNSET and o.get("kind") != "same_owner":
        return None
    src = p.turn_source(agent)
    if src.get("kind") != "same_owner" or not (src.get("otg") or src.get("otg_required")):
        return None
    if o is not p._UNSET and (o.get("fid") != src.get("fid") or o.get("otg") != src.get("otg")):
        return None
    return src["fid"], list(src["otg"])


def task_source(src):

    out = {"kind": src["kind"], "role": "task"}
    otg, fid = src.get("otg"), src.get("fid")
    if (src.get("kind") == "same_owner" and isinstance(otg, list) and otg and len(otg) <= 4 and isinstance(fid, str)
            and _FID.fullmatch(fid) and all(isinstance(t, str) and _TID.fullmatch(t) for t in otg)):
        out.update(otg=list(otg), fid=fid)    # P574: grant ids the host verified on arrival (never the Agent's words)
    if src.get("kind") == "same_owner" and src.get("otg_required") is True and isinstance(fid, str) and _FID.fullmatch(fid):
        out.update(fid=fid, otg_required=True, otg=list(out.get("otg") or []))
    return out


def resume_source(src):

    from . import provenance
    out = provenance.system()
    if isinstance(src.get("otg_resume"), str) and _TID.fullmatch(src["otg_resume"]):
        out["otg_resume"] = src["otg_resume"]
    return out
