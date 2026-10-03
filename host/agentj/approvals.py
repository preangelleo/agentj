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
