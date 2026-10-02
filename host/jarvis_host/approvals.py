"""Approvals on the phone (PROTOCOL §8): what a device signs, how the host checks it, and the local approvals log.

The Noise session already proves which paired device an answer came from; the Ed25519 signature on top makes every
decision a record that can be re-checked later (`jarvis approvals --verify`) and binds it to the exact request id and
the exact text the phone showed. The log holds metadata only: never the tool input, only its SHA-256.
"""
from __future__ import annotations

import hashlib
import json
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import wire

CONTEXT = "agentjarvis-approve-v1"
DECISIONS = ("allow", "deny")


def shown_digest(tool: str, summary: str) -> str:
    """hex SHA-256 of what the phone displays for a request: tool name, newline, summary (UTF-8)."""
    return hashlib.sha256(f"{tool}\n{summary}".encode()).hexdigest()


def signed_message(channel: str, device: str, rid: str, decision: str, digest_hex: str) -> bytes:
    if decision not in DECISIONS:
        raise ValueError("bad decision")
    return f"{CONTEXT}\n{channel}\n{device}\n{rid}\n{decision}\n{digest_hex}".encode()


def verify(sign_pub: bytes, sig: bytes, channel: str, device: str, rid: str, decision: str, digest_hex: str) -> bool:
    if len(sign_pub) != 32 or len(sig) != 64:
        return False
    try:
        Ed25519PublicKey.from_public_bytes(sign_pub).verify(sig, signed_message(channel, device, rid, decision, digest_hex))
        return True
    except (InvalidSignature, ValueError):
        return False


def input_digest(tool_input) -> str:
    return hashlib.sha256(json.dumps(tool_input, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def record(st, *, rid: str, agent: str, tool: str, input_sha256: str, shown_sha256: str, decision: str, reason: str,
           device: str | None = None, sign_pub: bytes | None = None, sig: bytes | None = None) -> dict:
    """Append one decision to approvals.log (0600). reason: device | timeout | no_device | serve_stop | agent_gone."""
    rec = {"ts": int(time.time()), "id": rid, "channel": st.config()["channel"], "agent": agent, "tool": tool,
           "input_sha256": input_sha256, "shown_sha256": shown_sha256, "decision": decision, "reason": reason,
           "device": device, "sk": wire.b64u(sign_pub) if sign_pub else None, "sig": wire.b64u(sig) if sig else None}
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


def check_record(st, r: dict) -> str:
    """'ok' (signature valid and the key is the device's current one) · 'ok_removed' (valid; device no longer paired) ·
    'bad' (signature does not verify / key differs from the paired device's) · 'unsigned' (a host-side decision:
    timeout, no device, serve stopped — these never carry a signature)."""
    if r.get("reason") != "device":
        return "unsigned"
    try:
        sk, sig = wire.unb64u(r.get("sk") or ""), wire.unb64u(r.get("sig") or "")
    except ValueError:
        return "bad"
    if not verify(sk, sig, str(r.get("channel")), str(r.get("device")), str(r.get("id")), str(r.get("decision")),
                  str(r.get("shown_sha256"))):
        return "bad"
    cur = st.sign_key(str(r.get("device")))
    if cur is None:
        return "ok_removed"
    return "ok" if cur == sk else "bad"
