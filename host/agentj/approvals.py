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


def signed_message(channel: str, device: str, rid: str, decision: str, digest_hex: str, scope: str | None = None, *, task_scope: str | None = None) -> bytes:
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
    if task_scope is not None:
        if decision == "allow_batch":
            raise ValueError("task scope cannot batch")
        base += "\ntask:" + scope_digest(task_scope)
    return base.encode()


def allow_add_line(addrs) -> str:
    """P296: an `agentj mail send` card's allow that also puts the ticked recipients on the mail allowlist signs one more
    line — hex SHA-256 of "allow:" + the ticked addresses in the order the card offered them, newline-joined (wire.js
    mailAllowMessage). The host checks the picks against what IT offered, so a phone cannot add anyone else."""
    return hashlib.sha256(("allow:" + "\n".join(addrs)).encode()).hexdigest()


def verify(sign_pub: bytes, sig: bytes, channel: str, device: str, rid: str, decision: str, digest_hex: str,
           scope: str | None = None, allow_add: list | None = None, *, task_scope: str | None = None) -> bool:
    if len(sign_pub) != 32 or len(sig) != 64:
        return False
    try:
        msg = signed_message(channel, device, rid, decision, digest_hex, scope, task_scope=task_scope)
        if allow_add:
            if decision != "allow":
                return False
            msg += ("\n" + allow_add_line(allow_add)).encode()
        Ed25519PublicKey.from_public_bytes(sign_pub).verify(sig, msg)
        return True
    except (InvalidSignature, ValueError):
        return False


def input_digest(tool_input) -> str:
    return hashlib.sha256(json.dumps(tool_input, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def record(st, *, rid: str, agent: str, tool: str, input_sha256: str, shown_sha256: str, decision: str, reason: str,
           device: str | None = None, sign_pub: bytes | None = None, sig: bytes | None = None, cats: list | None = None,
           scope: str | None = None, grant: str | None = None, auto: str = "batch", control: dict | None = None,
           scope_sha256: str | None = None, fa: dict | None = None, task_scope: str | None = None, task_action: dict | None = None, task_id: str | None = None, peer_proof: dict | None = None, task_uv: bool | None = None) -> dict:
    """Append metadata only (0600): signed device/control decisions and their exact-scope automatic children.
    task_uv records the actual local requirement; task_scope signs the authorization ceiling. Never log tool input.
    fa contains assertion hashes only. The cloud cannot read this log.
    """
    rec = {"ts": int(time.time()), "id": rid, "channel": st.config()["channel"], "agent": agent, "tool": tool,
           "input_sha256": input_sha256, "shown_sha256": shown_sha256, "decision": decision, "reason": reason,
           "device": device, "sk": wire.b64u(sign_pub) if sign_pub else None, "sig": wire.b64u(sig) if sig else None}
    if task_uv is not None:
        rec["task_uv"] = task_uv
    if peer_proof is not None:
        rec["peer_proof"] = peer_proof
    if task_id is not None:
        rec["task_id"] = task_id
    if task_scope is not None:
        rec["task_scope"] = task_scope
    if task_action is not None:
        rec["task_action"] = task_action
    if cats is not None:
        rec["cats"] = list(cats)
    if scope is not None:
        rec["scope_sha256"] = scope_digest(scope)
    elif scope_sha256 is not None:          # P296: the signed allowlist line (allow_add_line), checked like the group line
        rec["scope_sha256"] = scope_sha256
    if grant is not None:
        rec["auto"], rec["grant"] = auto, grant   # P158: auto "goal" = under the signed goal card (reason goal | goal_auto)
    if fa is not None:
        rec["fa"] = {k: str(fa[k])[:64] for k in ("id", "cd", "ad", "sig") if k in fa}
    if control is not None:
        rec["kind"] = "control"
        rec.update({k: control[k] for k in ("n", "sig_ts", "object_sha256")})
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
    return "-" if picks is None else ";".join(("t:" + _h(p) if isinstance(p, str) else ",".join(str(n) for n in p)) for p in picks)


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
        if isinstance(p, str):
            if not p.strip() or wire.text_problem(p, 2000):
                return False
            continue
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


# ---------------------------------------------------------------- P242: signed phone controls in approvals.log
CONTROL_TOOLS = ("dev_confirm", "dev_unbind")   # remotes.py writes these two to approvals.log too (adds / unbinds)
LEGACY_WINDOW_S = 5                             # a pre-0.20.3 line and its controls.log line are written in the same moment


def _key_verdict(st, device: str, sk: bytes) -> str:
    cur = st.sign_key(device)
    return "ok_removed" if cur is None else ("ok" if cur == sk else "bad")


def _check_control(st, r: dict) -> str:
    from . import controls
    if r.get("kind") == "control":
        try:
            sk, sig = wire.unb64u(r.get("sk") or ""), wire.unb64u(r.get("sig") or "")
        except ValueError:
            return "bad"
        ts = r.get("sig_ts")
        if not isinstance(ts, int) or isinstance(ts, bool) or r.get("tool") not in controls.ACTIONS or \
                not controls.verify(sk, sig, str(r.get("channel")), str(r.get("device")), str(r.get("tool")), str(r.get("n")),
                                    ts, str(r.get("object_sha256"))):
            return "bad"
        return _key_verdict(st, str(r.get("device")), sk)
    # before 0.20.3 (P242): the line kept no signature — the control's own line in controls.log has it
    dev, tool, at = str(r.get("device")), str(r.get("tool")), r.get("ts")
    try:
        lines = st.controls_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    found = None
    for ln in lines:
        try:
            c = json.loads(ln)
        except ValueError:
            continue
        if not isinstance(c, dict) or c.get("action") != tool or c.get("device") != dev or c.get("result") != "ok" \
                or not isinstance(at, int) or not isinstance(c.get("ts"), int) or abs(c["ts"] - at) > LEGACY_WINDOW_S:
            continue
        found = "bad"
        try:
            sig = wire.unb64u(str(c.get("sig") or ""))
        except ValueError:
            continue
        cur = st.sign_key(dev)
        ts = c.get("sig_ts")
        if cur is None:
            return "legacy"    # signer unpaired since: no key left to check the old line against
        if isinstance(ts, int) and controls.verify(cur, sig, str(r.get("channel")), dev, tool, str(c.get("n")), ts,
                                                   str(c.get("object_sha256"))):
            return "ok"
    return found or "legacy"


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
    allow_batch line it names (index: id → record) verifies and covers the same tool, else 'bad'.
    P242: a signed phone control (kind "control": dev_confirm / dev_unbind) is checked as the control it was; a line written
    by a host before 0.20.3 for one carries no signature itself and is checked against its controls.log line ('legacy' when
    that line is gone — never 'bad' for a signature that was simply not copied)."""
    if r.get("kind") == "control" or (r.get("tool") in CONTROL_TOOLS and r.get("agent") == "phone"
                                      and r.get("reason") == "device" and not r.get("sig") and not r.get("sk")):
        return _check_control(st, r)
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
    if r.get("reason") in ("goal", "goal_auto", "goal_nocard"):
        # P158: allowed under a goal — the signed `agentj goal` allow it names must verify (P336: or its goal_preauth line)
        g = (index or {}).get(str(r.get("grant")))
        if not g or g.get("decision") != "allow" or g.get("tool") != "agentj goal" or g.get("reason") not in ("device", "goal_preauth"):
            return "bad"
        return "auto" if check_record(st, g, index) in ("ok", "ok_removed", "auto") else "bad"
    if r.get("reason") == "goal_grant_add":
        # P339 「批准，并加进目标授权」: the owner's signed allow it names carries the offer line (allow_add_line) it added
        g = (index or {}).get(str(r.get("grant")))
        if not g or g.get("decision") != "allow" or g.get("reason") != "device" or not g.get("scope_sha256") \
                or g.get("device") != r.get("device") or r.get("scope_sha256") != g.get("scope_sha256"):
            return "bad"                      # review #7: the offer text it added is the one the owner signed
        return "auto" if check_record(st, g) in ("ok", "ok_removed") else "bad"
    if r.get("reason") == "goal_preauth":
        # P336: a goal started under the owner's signed 「设了目标就不弹窗」 — that approval_set control line must verify
        c = (index or {}).get(str(r.get("grant")))
        want = hashlib.sha256(b"approval.goal_nocard\ntrue").hexdigest()
        if not c or c.get("kind") != "control" or c.get("tool") != "approval_set" or c.get("object_sha256") != want \
                or c.get("device") != r.get("device"):
            return "bad"
        return "auto" if check_record(st, c) in ("ok", "ok_removed") else "bad"
    if r.get("reason") in ("level_full", "level_harness") and r.get("grant"):
        # P336: allowed under the full level — the signed switch to `full` it names must verify (P551: or to `harness`)
        c = (index or {}).get(str(r.get("grant")))
        # P552: a released signed `full` is in force as `harness` — its switch is the parent of a level_harness line too
        want = {hashlib.sha256(b"approval.level\n" + w.encode()).hexdigest()
                for w in ((r["reason"][6:],) if r["reason"] == "level_full" else ("harness", "full"))}
        if not c or c.get("kind") != "control" or c.get("tool") != "approval_set" or c.get("object_sha256") not in want:
            return "bad"
        return "auto" if check_record(st, c) in ("ok", "ok_removed") else "bad"
    if r.get("reason") == "parked":
        # P336 (#36): the identical retry of a step the owner allowed on its parked card — that allow must verify
        g = (index or {}).get(str(r.get("grant")))
        if not g or g.get("decision") not in ("allow", "allow_batch") or g.get("reason") != "device" \
                or g.get("tool") != r.get("tool") or g.get("input_sha256") != r.get("input_sha256"):
            return "bad"
        return "auto" if check_record(st, g) in ("ok", "ok_removed") else "bad"
    if r.get("reason") == "same_owner" and r.get("peer_proof"):
        from .friends import FriendStore
        try:
            proof = r["peer_proof"]
            scope = json.loads(proof["task_scope"])
            target = scope["target"]
            row = FriendStore(st)._read("same-owner.json", {}).get(target["sender"], {})
            action = r["task_action"]
            valid = (type(scope.get("uv")) is bool and type(r.get("task_uv")) is bool
                     and (not r["task_uv"] or scope["uv"]) and r.get("task_scope") == proof["task_scope"] and proof["sk"] == row.get("remote_pub")
                     and target["relationship"] == row.get("verified")
                     and r.get("task_id") == str(row.get("action_claims", {}).get(proof["id"]))
                     and action in scope["actions"] and action["tool"] == r.get("tool")
                     and action["input_sha256"] == r.get("input_sha256")
                     and action["class"][1] == sorted(r.get("cats", [])) and r["ts"] <= scope["expires"])
            return "auto" if valid and verify(wire.unb64u(proof["sk"]), wire.unb64u(proof["sig"]), proof["channel"],
                        proof["device"], proof["id"], "allow", proof["shown_sha256"], task_scope=proof["task_scope"]) else "bad"
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            return "bad"
    if r.get("reason") == "task_grant":
        g = (index or {}).get(str(r.get("grant")))
        try:
            scope = json.loads(g["task_scope"])
            action = r["task_action"]
            valid = (type(scope.get("uv")) is bool and type(r.get("task_uv")) is bool
                     and g.get("task_uv") is scope["uv"] and (not r["task_uv"] or scope["uv"])
                     and g.get("decision") == "allow" and g.get("reason") == "device"
                     and g.get("device") == r.get("device") and r.get("task_scope") == g["task_scope"]
                     and r.get("task_id") == g.get("task_id") == scope["task"]
                     and g["ts"] <= r["ts"] <= scope["expires"]
                     and action in scope["actions"] and action["tool"] == r.get("tool")
                     and action["input_sha256"] == r.get("input_sha256")
                     and action["class"][1] == sorted(r.get("cats", [])))
        except (KeyError, TypeError, ValueError, IndexError, AttributeError):
            return "bad"
        return "auto" if valid and check_record(st, g) in ("ok", "ok_removed") else "bad"
    if r.get("reason") == "batch":
        g = (index or {}).get(str(r.get("grant")))
        if not g or g.get("decision") != "allow_batch" or g.get("tool") != r.get("tool") or g.get("device") != r.get("device"):
            return "bad"
        return "auto" if check_record(st, g) in ("ok", "ok_removed") else "bad"
    if r.get("reason") != "device":
        return "unsigned"
    if r.get("task_scope") is not None:
        try:
            uv = json.loads(r["task_scope"])["uv"]
            if type(uv) is not bool or (r.get("decision") == "allow" and r.get("task_uv") is not uv):
                return "bad"
        except (ValueError, TypeError, KeyError):
            return "bad"

    try:
        sk, sig = wire.unb64u(r.get("sk") or ""), wire.unb64u(r.get("sig") or "")
    except ValueError:
        return "bad"
    try:
        msg = signed_message(str(r.get("channel")), str(r.get("device")), str(r.get("id")), str(r.get("decision")),
                             str(r.get("shown_sha256")), task_scope=r.get("task_scope"))
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
