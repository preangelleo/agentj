"""P497: signed phone workflow resume, with a task/run/contract-bound one-use challenge.

A integration: on a READY paired Noise session call challenge(host, channel, device,
run_id, source=provenance.phone()), show its exact object, sign signing_bytes(obj),
then execute(host, obj, sig, channel=..., device=..., source=...). Never derive source
from wire fields or friend text. D/B must share this canonical JSON signing contract.
This independent domain avoids changing A-owned controls.py/protocol until integration.
Non-idempotent repeats still go through wfrun.resume's normal owner confirmation card.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import diskq, tasks, wfrun, wire
from .friend_report import owner_source

FILE = "wf-resume.json"
DOMAIN = b"agentjarvis-wf-resume-v1\n"
TTL = 120
MAX_PENDING = 100
KEYS = {"action", "channel", "device", "id", "run_id", "tsha", "run_sha", "nonce", "issued", "expires"}
HEX64 = re.compile(r"[0-9a-f]{64}")


class Refused(ValueError):
    pass


def signing_bytes(obj):
    if not isinstance(obj, dict) or set(obj) != KEYS:
        raise Refused("shape")
    if (obj["action"] != "wf_resume" or not all(isinstance(obj[k], str) and 0 < len(obj[k]) <= 256
                                               for k in ("channel", "device", "id", "run_id", "tsha", "run_sha", "nonce"))
            or not tasks.taskspec.ID_RE.fullmatch(obj["id"]) or not re.fullmatch(r"[0-9a-f]{32}", obj["run_id"])
            or not HEX64.fullmatch(obj["tsha"]) or not HEX64.fullmatch(obj["run_sha"])
            or not re.fullmatch(r"[0-9a-f]{32}", obj["nonce"])
            or type(obj["issued"]) is not int or type(obj["expires"]) is not int
            or obj["expires"] - obj["issued"] != TTL):
        raise Refused("shape")
    return DOMAIN + json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def _load(st):
    path = st.root / FILE
    if not path.exists():
        return {}
    if path.is_symlink() or path.stat().st_size > 128 * 1024:
        raise Refused("corrupt")
    try:
        d = json.loads(path.read_text())
    except (OSError, ValueError):
        raise Refused("corrupt") from None
    if not isinstance(d, dict):
        raise Refused("corrupt")
    return d


def _save(st, d):
    st.write_private(st.root / FILE, json.dumps(d, separators=(",", ":")).encode())


def _key(host, channel, device, source):
    if not owner_source(source):
        raise Refused("owner_required")
    # The ingress additionally checks READY and channel ownership; a signing key
    # is read afresh here so unpair/key rotation invalidates old challenges.
    if not isinstance(channel, str) or not channel or not isinstance(device, str) or not device:
        raise Refused("unpaired")
    key = host.st.sign_key(device)
    if not key:
        raise Refused("unpaired")
    return key


def _entry(host, tid, tsha):
    e = tasks.find(host.agent_cfg.get("dir"), tid)
    if e["problems"] or e.get("tsha") != tsha:
        raise Refused("contract_changed")
    return e


def _run_sha(rec):
    # Claim changes are managed by automation resume itself, not the phone.
    return hashlib.sha256(json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def challenge(host, channel, device, run_id, *, source, now=None):
    now = int(time.time() if now is None else now)
    _key(host, channel, device, source)
    if host.stopped() or not host.agent_cfg:
        raise Refused("unavailable")
    rec = wfrun.load(host.st, run_id)  # exact run only; no mutable 'latest run' reference
    e = tasks.find(host.agent_cfg.get("dir"), rec["id"])
    if e["problems"] or not HEX64.fullmatch(e.get("tsha") or ""):
        raise Refused("invalid_task")
    wfrun.plan(host.st, run_id, e, getattr(host, "lang", "zh"), host.scheduler.instance_id)
    obj = {"action": "wf_resume", "channel": channel, "device": device, "id": rec["id"], "run_id": run_id,
           "tsha": e["tsha"], "run_sha": _run_sha(rec), "nonce": secrets.token_hex(16), "issued": now, "expires": now + TTL}
    signing_bytes(obj)
    with host.st.file_lock("wf-resume"):
        d = _load(host.st)
        d = {n: r for n, r in d.items() if isinstance(r, dict) and r.get("expires", 0) > now}
        if len(d) >= MAX_PENDING:
            raise Refused("busy")
        d[obj["nonce"]] = dict(obj)
        _save(host.st, d)
    return obj


class _Scheduler:
    """Recheck the exact signed contract at the final synchronous enqueue boundary."""
    def __init__(self, host, obj, key):
        self.host, self.obj, self.key = host, obj, key

    def __getattr__(self, name):
        return getattr(self.host.scheduler, name)

    def request(self, tid, trigger="manual", minute=None):
        try:
            if tid != self.obj["id"] or trigger != "resume":
                raise Refused("target_changed")
            if self.host.st.sign_key(self.obj["device"]) != self.key:
                raise Refused("unpaired")
            _entry(self.host, tid, self.obj["tsha"])
        except Refused:
            raise tasks.TaskError("changed") from None
        return self.host.scheduler.request(tid, trigger, minute)


class _Host:
    def __init__(self, host, obj, key):
        self.host = host
        self.scheduler = _Scheduler(host, obj, key)

    def __getattr__(self, name):
        return getattr(self.host, name)


async def execute(host, obj, signature, *, channel, device, source, now=None):
    """Consumes the challenge durably before awaiting, even if resume is refused/cancelled.
    A fresh challenge is needed after restart/error; a used challenge can never run twice.
    """
    why = "error"
    target_hash = None
    try:
        key = _key(host, channel, device, source)
        signed = signing_bytes(obj)
        target_hash = hashlib.sha256(signed).hexdigest()
        now = time.time() if now is None else now
        if obj["channel"] != channel or obj["device"] != device:
            raise Refused("target_changed")
        if not obj["issued"] <= now < obj["expires"]:
            raise Refused("expired")
        try:
            if not isinstance(signature, str) or len(signature) > 100:
                raise ValueError("signature_shape")
            sig = wire.unb64u(signature) if isinstance(signature, str) else b""
            Ed25519PublicKey.from_public_bytes(key).verify(sig, signed)
        except (InvalidSignature, ValueError):
            raise Refused("bad_signature") from None
        with host.st.file_lock("wf-resume"):
            d = _load(host.st)
            if d.get(obj["nonce"]) != obj:
                raise Refused("replay")
            _entry(host, obj["id"], obj["tsha"])
            rec = wfrun.load(host.st, obj["run_id"])
            if rec["id"] != obj["id"] or _run_sha(rec) != obj["run_sha"]:
                raise Refused("run_changed")
            del d[obj["nonce"]]
            _save(host.st, d)
        if not await diskq.aflush():
            raise Refused("io")
        # No --yes / trusted-terminal shortcut. The ordinary non-idempotent card stays.
        result = await wfrun.resume(_Host(host, obj, key), obj["run_id"], yes=False, trusted=False)
        why = result.get("result", "refused")
        return result
    except (Refused, wfrun.RunError, tasks.TaskError) as e:
        why = getattr(e, "why", getattr(e, "reason", str(e)))
        return {"result": "refused", "why": why}
    finally:
        # Fixed outcome only, no text, signature, nonce or contract material in logs.
        host.st.log("wf_resume", result=why if why in {"queued", "refused", "declined", "owner_required", "unpaired",
                    "shape", "target_changed", "expired", "bad_signature", "replay", "contract_changed", "run_changed"} else "error",
                    **({"object_sha256": target_hash} if target_hash else {}))
