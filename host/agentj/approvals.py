"""Approvals on the phone (PROTOCOL §8): what a device signs, how the host checks it, and the local approvals log.

The Noise session already proves which paired device an answer came from; the Ed25519 signature on top makes every
decision a record that can be re-checked later (`agentj approvals --verify`) and binds it to the exact request id and
the exact text the phone showed. The log holds metadata only: never the tool input, only its SHA-256.

Batch approval (ADR-A48): `allow_batch` = "allow this one and, for the rest of this turn, the same kind of low-risk action";
the signed text then also carries the SHA-256 of the exact scope text the phone showed, so a decision cannot be widened after
the fact; the log keeps that hash (`scope_sha256`), never the scope text (it names commands / folders).
Every request approved under it is its own log line (reason `batch`, `auto: batch`, `grant` = the signed request's id).
"""
from __future__ import annotations

import hashlib
import json
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import wire

CONTEXT = "agentjarvis-approve-v1"
DECISIONS = ("allow", "deny", "allow_batch")


def shown_digest(tool: str, summary: str) -> str:
    """hex SHA-256 of what the phone displays for a request: tool name, newline, summary (UTF-8)."""
    return hashlib.sha256(f"{tool}\n{summary}".encode()).hexdigest()


def scope_digest(scope: str) -> str:
    """hex SHA-256 of the batch scope text the phone displayed (UTF-8). The log keeps only this, like shown_sha256."""
    return hashlib.sha256(scope.encode()).hexdigest()


def signed_message(channel: str, device: str, rid: str, decision: str, digest_hex: str, scope: str | None = None) -> bytes:
    """allow / deny: unchanged since L1. allow_batch: one more line, hex SHA-256 of the scope text exactly as shown (§8)."""
    if decision not in DECISIONS:
        raise ValueError("bad decision")
    base = f"{CONTEXT}\n{channel}\n{device}\n{rid}\n{decision}\n{digest_hex}"
    if decision == "allow_batch":
        if not isinstance(scope, str) or not scope:
            raise ValueError("allow_batch needs a scope")
        base += "\n" + scope_digest(scope)
    elif scope is not None:
        raise ValueError("only allow_batch carries a scope")
    return base.encode()


def verify(sign_pub: bytes, sig: bytes, channel: str, device: str, rid: str, decision: str, digest_hex: str,
           scope: str | None = None) -> bool:
    if len(sign_pub) != 32 or len(sig) != 64:
        return False
    try:
        Ed25519PublicKey.from_public_bytes(sign_pub).verify(sig, signed_message(channel, device, rid, decision, digest_hex,
                                                                                 scope))
        return True
    except (InvalidSignature, ValueError):
        return False


def input_digest(tool_input) -> str:
    return hashlib.sha256(json.dumps(tool_input, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def record(st, *, rid: str, agent: str, tool: str, input_sha256: str, shown_sha256: str, decision: str, reason: str,
           device: str | None = None, sign_pub: bytes | None = None, sig: bytes | None = None, cats: list | None = None,
           scope: str | None = None, grant: str | None = None) -> dict:
    """Append one decision to approvals.log (0600). reason: device | batch | timeout | no_device | serve_stop | agent_gone |
    too_many | estop | policy (beyond the harness's own sandbox, never asked: ADR-A73). cats = danger categories ([] = low risk); scope = the signed batch scope (allow_batch); grant = for an
    automatic approval, the id of the signed allow_batch it relied on."""
    rec = {"ts": int(time.time()), "id": rid, "channel": st.config()["channel"], "agent": agent, "tool": tool,
           "input_sha256": input_sha256, "shown_sha256": shown_sha256, "decision": decision, "reason": reason,
           "device": device, "sk": wire.b64u(sign_pub) if sign_pub else None, "sig": wire.b64u(sig) if sig else None}
    if cats is not None:
        rec["cats"] = list(cats)
    if scope is not None:
        rec["scope_sha256"] = scope_digest(scope)
    if grant is not None:
        rec["auto"], rec["grant"] = "batch", grant
    st.append_private(st.approvals_path, json.dumps(rec, ensure_ascii=False))
    return rec


# ---------------------------------------------------------------- questions (PROTOCOL §10.7): an answer is an approval
Q_CONTEXT = "agentjarvis-question-v1"
Q_ACTIONS = ("answer", "cancel")


Q_MAX_QS, Q_MAX_OPTS = 8, 16


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def norm_questions(raw) -> list | None:
    """Claude Code's AskUserQuestion `questions` (or an adapter's equivalent in the same shape) → the card's
    [{"q", "h", "m", "o": [{"l", "d"}]}]; None when the card cannot show it EXACTLY (over a bound, a duplicate question or
    label, empty, wrong types, a control character) — then there is no card and the harness is told nobody answered, so
    the model asks in text. Never truncated: the signature and the answer both bind the exact text."""
    if not isinstance(raw, list) or not 1 <= len(raw) <= Q_MAX_QS:
        return None
    seen, out = set(), []
    for q in raw:
        if not isinstance(q, dict):
            return None
        text, head, opts = q.get("question"), q.get("header") or "", q.get("options")
        if not isinstance(text, str) or not text.strip() or wire.text_problem(text, 1000) or text in seen:
            return None
        if not isinstance(head, str) or wire.text_problem(head, 64):
            return None
        if not isinstance(opts, list) or not 1 <= len(opts) <= Q_MAX_OPTS:
            return None
        labels, o = set(), []
        for x in opts:
            if not isinstance(x, dict):
                return None
            lab, d = x.get("label"), x.get("description") or ""
            if not isinstance(lab, str) or not lab.strip() or wire.text_problem(lab, 200) or lab in labels:
                return None
            if not isinstance(d, str) or wire.text_problem(d, 500):
                return None
            labels.add(lab)
            o.append({"l": lab, "d": d})
        seen.add(text)
        out.append({"q": text, "h": head, "m": q.get("multiSelect") is True, "o": o})
    return out


def question_text(qs: list) -> str:
    """Q: one line per question `q <sha(h)> <sha(q)> m|s`, then one line per option `o <sha(l)> <sha(d)>` (d absent = "")."""
    lines = []
    for q in qs:
        lines.append(f"q {_h(q.get('h') or '')} {_h(q['q'])} {'m' if q.get('m') else 's'}")
        for o in q["o"]:
            lines.append(f"o {_h(o['l'])} {_h(o.get('d') or '')}")
    return "\n".join(lines)


def question_digest(qs: list) -> str:
    """hex SHA-256 of Q — what approvals.log keeps instead of the questions."""
    return _h(question_text(qs))


def picks_text(picks) -> str:
    """P: `1,3;2` (questions by `;`, 1-based option numbers by `,`); `-` = cancel."""
    return "-" if picks is None else ";".join(",".join(str(n) for n in p) for p in picks)


def question_message(channel: str, device: str, qid: str, action: str, qs: list, picks) -> bytes:
    if action not in Q_ACTIONS:
        raise ValueError("bad action")
    if (action == "cancel") != (picks is None):
        raise ValueError("picks only with answer")
    return (f"{Q_CONTEXT}\n{channel}\n{device}\n{qid}\n{action}\n{question_digest(qs)}\n{picks_text(picks)}").encode()


def check_picks(qs: list, picks) -> bool:
    """Exactly one option number for a single-select question, ≥ 1 for a multi-select one; 1-based, ascending, in range."""
    if not isinstance(picks, list) or len(picks) != len(qs):
        return False
    for q, p in zip(qs, picks):
        if not isinstance(p, list) or not p or not all(type(n) is int for n in p):
            return False
        if p != sorted(set(p)) or p[0] < 1 or p[-1] > len(q["o"]):
            return False
        if not q.get("m") and len(p) != 1:
            return False
    return True


def verify_question(sign_pub: bytes, sig: bytes, channel: str, device: str, qid: str, action: str, qs: list, picks) -> bool:
    if len(sign_pub) != 32 or len(sig) != 64:
        return False
    try:
        Ed25519PublicKey.from_public_bytes(sign_pub).verify(sig, question_message(channel, device, qid, action, qs, picks))
        return True
    except (InvalidSignature, ValueError):
        return False


def record_question(st, *, qid: str, agent: str, q_sha256: str, decision: str, reason: str, picks: str | None = None,
                    device: str | None = None, sign_pub: bytes | None = None, sig: bytes | None = None) -> dict:
    """One approvals.log line per question outcome (kind "question"): the hash of what was shown, the picks as numbers,
    the device, its key and signature — never the question or option text. decision: answer | cancel | timeout | gone |
    stopped | no_device; reason: device | timeout | agent_gone | serve_stop | estop | no_device."""
    rec = {"ts": int(time.time()), "kind": "question", "id": qid, "channel": st.config()["channel"], "agent": agent,
           "q_sha256": q_sha256, "decision": decision, "reason": reason, "picks": picks, "device": device,
           "sk": wire.b64u(sign_pub) if sign_pub else None, "sig": wire.b64u(sig) if sig else None}
    st.append_private(st.approvals_path, json.dumps(rec, ensure_ascii=False))
    return rec


def read_log(st) -> list[dict]:
    try:
        lines = st.approvals_path.read_text().splitlines()
    except FileNotFoundError:
        return []
    out = []
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


def check_record(st, r: dict, index: dict | None = None) -> str:
    """'ok' (signature valid and the key is the device's current one) · 'ok_removed' (valid; device no longer paired) ·
    'bad' (signature does not verify / key differs from the paired device's) · 'unsigned' (a host-side decision:
    timeout, no device, serve stopped — these never carry a signature) · for an automatic batch approval: 'auto' when the
    allow_batch line it names (index: id → record) verifies and covers the same tool, else 'bad'."""
    if r.get("kind") == "question":
        if r.get("reason") != "device":
            return "unsigned"
        try:
            sk, sig = wire.unb64u(r.get("sk") or ""), wire.unb64u(r.get("sig") or "")
        except ValueError:
            return "bad"
        msg = (f"{Q_CONTEXT}\n{r.get('channel')}\n{r.get('device')}\n{r.get('id')}\n{r.get('decision')}\n"
               f"{r.get('q_sha256')}\n{r.get('picks')}").encode()
        try:
            if len(sk) != 32 or len(sig) != 64 or r.get("decision") not in Q_ACTIONS:
                return "bad"
            Ed25519PublicKey.from_public_bytes(sk).verify(sig, msg)
        except (InvalidSignature, ValueError):
            return "bad"
        cur = st.sign_key(str(r.get("device")))
        return "ok_removed" if cur is None else ("ok" if cur == sk else "bad")
    if r.get("reason") == "batch":
        g = (index or {}).get(str(r.get("grant")))
        if not g or g.get("decision") != "allow_batch" or g.get("tool") != r.get("tool") or g.get("device") != r.get("device"):
            return "bad"
        return "auto" if check_record(st, g) in ("ok", "ok_removed") else "bad"
    if r.get("reason") != "device":
        return "unsigned"
    try:
        sk, sig = wire.unb64u(r.get("sk") or ""), wire.unb64u(r.get("sig") or "")
    except ValueError:
        return "bad"
    try:
        msg = signed_message(str(r.get("channel")), str(r.get("device")), str(r.get("id")), str(r.get("decision")),
                             str(r.get("shown_sha256")))
        if r.get("decision") == "allow" and isinstance(r.get("scope_sha256"), str):
            msg += ("\n" + r["scope_sha256"]).encode()     # P71 friend_request: the group line (§17.7)
    except ValueError:                                  # allow_batch: the scope line is its hash, kept in the log
        msg = (f"{CONTEXT}\n{r.get('channel')}\n{r.get('device')}\n{r.get('id')}\nallow_batch\n{r.get('shown_sha256')}\n"
               f"{r.get('scope_sha256')}").encode() if r.get("decision") == "allow_batch" else b""
    try:
        if len(sk) != 32 or len(sig) != 64 or not msg:
            return "bad"
        Ed25519PublicKey.from_public_bytes(sk).verify(sig, msg)
    except (InvalidSignature, ValueError):
        return "bad"
    cur = st.sign_key(str(r.get("device")))
    if cur is None:
        return "ok_removed"
    return "ok" if cur == sk else "bad"
