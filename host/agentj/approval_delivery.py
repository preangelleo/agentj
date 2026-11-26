"""Owner approval-card delivery: accepted transport, per-device durable retry, no card content in state/logs."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import pathlib
import secrets
import time

from . import diskq
from . import wire

_FILENAME = "approval-card-delivery.json"


def _path(host):
    return pathlib.Path(host.st.root) / _FILENAME


def _read(host):
    try:
        value = json.loads(_path(host).read_text())
        return value if isinstance(value, list) else []
    except (OSError, ValueError):
        return []


def _write(host, rows):
    diskq.write(_path(host), json.dumps(rows, separators=(",", ":")).encode())


def _put(host, rid, device):
    rows = _read(host)
    if not any(r.get("id") == rid and r.get("device") == device for r in rows):
        rows.append({"id": rid, "device": device})
        _write(host, rows)


def _drop(host, rid, device):
    rows = [r for r in _read(host) if not (r.get("id") == rid and r.get("device") == device)]
    _write(host, rows)


def drop_device(host, device):
    """Remove every queued card for a device when its session is unbound."""
    rows = [r for r in _read(host) if r.get("device") != device]
    _write(host, rows)


def _bound(host, session):
    current = host.st.devices().get(session.device)
    return (session.state == "ready" and host.st.is_allowed(session.pub)
            and isinstance(current, dict) and current.get("device", session.device) == session.device
            and current.get("pub") == wire.b64u(session.pub)
            and current.get("sk") == wire.b64u(session.sign_pub)
            and host.st.sign_key(session.device) == session.sign_pub)


async def deliver(host, obj_fn, *, rid: str, only=None) -> dict:
    """Send to every eligible online paired remote; queue each unavailable target.

    ``accepted`` means send_app accepted the encrypted frame. It never means that a
    card was merely constructed. The persisted queue contains ids only, never card text.
    """
    targets = {only} if isinstance(only, str) else set(only or host._approvers())
    online = [s for s in list(host.sessions.values())
              if s.state == "ready" and s.device in targets and host.st.is_allowed(s.pub)
              and (not s.sign_pub or _bound(host, s))]
    online_devices = {s.device for s in online}
    queued = targets - online_devices
    for device in queued:
        _put(host, rid, device)
    accepted, failed = set(), set()
    for session in online:
        try:
            ok = await host.send_app(session, obj_fn(session))
        except (OSError, ConnectionError, asyncio.TimeoutError):
            ok = False
        if ok:
            accepted.add(session.device)
            _drop(host, rid, session.device)
        else:
            failed.add(session.device)
            _put(host, rid, session.device)
    # A second tab may have disconnected after another tab accepted the frame.
    failed -= accepted
    for device in accepted:
        _drop(host, rid, device)
    return {"accepted": accepted, "queued": queued | failed, "failed": failed,
            "targets": targets, "complete": bool(targets) and accepted == targets}


async def replay(host, session) -> int:
    """Replay queued open cards only after repeating every live-boundary check."""
    count = 0
    if not _bound(host, session):
        return 0
    # The replay check and the encrypted frame write must share the transport
    # lock.  This closes the revoke/unbind window for single-frame cards too.
    lock = getattr(host, "send_lock", host.__dict__.setdefault("_approval_delivery_replay_lock", asyncio.Lock()))
    async with lock:
        for row in list(_read(host)):
          if row.get("device") != session.device:
              continue
          rid = row.get("id")
          ask = host.asks.get(rid)
          deadline = getattr(ask, "deadline", float("inf")) if ask is not None else float("inf")
          obj = (None if ask is not None and getattr(ask, "deadline", float("inf")) <= time.monotonic() else host._ask_msg(ask, session)) if ask is not None and not ask.fut.done() else None
          if obj is None:
              card = getattr(getattr(host, "elevate", None), "cards", {}).get(rid)
              if card is not None and not card["fut"].done():
                  deadline = card.get("deadline", float("inf"))
                  if card.get("deadline", float("inf")) <= time.monotonic():
                      _drop(host, rid, session.device)
                      continue
                  obj = host.elevate.card_msg(card, session)
          if obj is None:
              _drop(host, rid, session.device)
              continue
          # Recheck all mutable bindings while holding the same lock used by
          # send_app.  send_app receives _already_locked so the check and write
          # remain one critical section.
          current = host.st.devices().get(session.device) if hasattr(host.st, "devices") else None
          if (getattr(session, "cid", None) is not None
                  and host.sessions.get(session.cid) is not session
                  or session.state != "ready" or not host.st.is_allowed(session.pub)
                  or not isinstance(current, dict)
                  or current.get("pub") != wire.b64u(session.pub)
                  or current.get("sk") != wire.b64u(session.sign_pub)
                  or host.st.sign_key(session.device) != session.sign_pub
                  or deadline <= time.monotonic()):
              continue
          try:
              ok = await host.send_app(session, obj, _already_locked=True)
          except (OSError, ConnectionError, asyncio.TimeoutError):
              ok = False
          if ok:
              _drop(host, rid, session.device)
              if obj.get("t") == "elev" and obj.get("kind") == "secret":
                  host.st.log("elev_delivery", id=rid, kind="secret", result="replayed")
              count += 1
    return count


# A rendered-frame observation stays within the original in-memory approval authority.
WAIT = 10
MAX_TARGETS = 64


@dataclass
class Probe:
    ask: object
    session: object
    pair_revision: str
    review_revision: int
    review_digest: str
    deadline: float
    nonce: str
    phase: str = ''


def probes(host):
    return host.__dict__.setdefault('_approval_delivery_probes', {})


def _pair_revision(host, session):
    record = host.st.devices().get(session.device) or {}
    if session.sign_pub and host.st.sign_key(session.device) == session.sign_pub:
        return record.get('pair_revision')
    return None


def _card_live(host, probe):
    ask, session = probe.ask, probe.session
    return (time.monotonic() < probe.deadline and host.asks.get(ask.rid) is ask
            and not ask.fut.done() and ask.review_state == 'pending'
            and ask.review_revision == probe.review_revision and ask.envelope_digest == probe.review_digest
            and (not ask.only or session.device == ask.only)
            and _pair_revision(host, session) == probe.pair_revision
            and ask.delivery_subject_live())


def _live(host, probe):
    return _card_live(host, probe) and host._paired_live(probe.session)


def ready_to_write(host, session, message):
    probe = probes(host).get((message['id'], session.cid))
    return (probe is not None and probe.session is session and message['delivery']['n'] == probe.nonce
            and _live(host, probe))


def receive(host, session, message):
    if set(message) != {'t', 'kind', 'id', 'n', 'phase'}:
        return
    rid, nonce, phase = message['id'], message['n'], message['phase']
    if not isinstance(rid, str) or not isinstance(nonce, str):
        return
    probe = probes(host).get((rid, session.cid))
    if (probe is None or probe.session is not session or probe.nonce != nonce or not _live(host, probe)
            or (probe.phase, phase) not in (('', 'received'), ('received', 'visible'))
            or (phase == 'visible' and not session.fg)):
        return
    probe.phase = phase
    host.st.log('approval_delivery', id=rid, device=session.device, phase=phase)


async def _write_observed(host, probe, phase):
    session = probe.session
    message = host._ask_msg(probe.ask, session)
    ttl = min(message['ttl'], int(probe.deadline - time.monotonic()))
    if ttl > 0 and await host.send_app(session, {**message, 'delivery': {'n': probe.nonce, 'ttl': ttl}}):
        host.st.log('approval_delivery', id=probe.ask.rid, device=session.device, phase=phase)


async def _follow_up(host, targets, remind):
    try:
        await asyncio.sleep(WAIT)
        if any(probe.phase == 'visible' and _card_live(host, probe) for probe in targets):
            return
        for probe in targets:
            if _live(host, probe):
                await _write_observed(host, probe, 'frame_rewritten')
        if remind:
            await asyncio.sleep(WAIT)

            def pending():
                live = [probe for probe in targets if _card_live(host, probe)]
                return bool(live) and all(probe.phase != 'visible' for probe in live)

            if pending():
                from . import card_reminder
                await card_reminder.deliver(host, targets[0].ask.rid, pending)
    finally:
        for probe in targets:
            probes(host).pop((probe.ask.rid, probe.session.cid), None)


async def deliver_observed(host, ask, remind):
    targets = []
    try:
        for session in list(host.sessions.values()):
            if (session.state != 'ready' or not host.st.is_allowed(session.pub)
                    or (ask.only and session.device != ask.only)):
                continue
            # Never replace the existing compatibility path for clients without observation support.
            negotiated = getattr(session, 'ask_receipt', False) and host._paired_live(session)
            revision = _pair_revision(host, session) if negotiated else None
            if (not revision or len(probes(host)) >= MAX_TARGETS or ask.deadline - time.monotonic() < 1):
                await host.send_app(session, host._ask_msg(ask, session))
                continue
            probe = Probe(ask, session, revision, ask.review_revision, ask.envelope_digest,
                          min(time.monotonic() + 60, ask.deadline), secrets.token_hex(16))
            probes(host)[(ask.rid, session.cid)] = probe
            targets.append(probe)
            await _write_observed(host, probe, 'frame_written')
    finally:
        if targets:
            host._bg(_follow_up(host, targets, remind), 'approval_delivery')
