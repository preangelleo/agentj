"""Owner approval-card delivery: accepted transport, per-device durable retry, no card content in state/logs."""
from __future__ import annotations

import asyncio
import json
import pathlib
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


async def deliver(host, obj_fn, *, rid: str, only=None) -> dict:
    """Send to every eligible online paired remote; queue each unavailable target.

    ``accepted`` means send_app accepted the encrypted frame. It never means that a
    card was merely constructed. The persisted queue contains ids only, never card text.
    """
    targets = {only} if isinstance(only, str) else set(only or host._approvers())
    online = [s for s in list(host.sessions.values())
              if s.state == "ready" and s.device in targets and host.st.is_allowed(s.pub)]
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
    return {"accepted": accepted, "queued": queued | failed, "failed": failed,
            "targets": targets, "complete": bool(targets) and accepted == targets}


async def replay(host, session) -> int:
    """Replay queued open cards only after repeating every live-boundary check."""
    count = 0
    if session.state != "ready" or not host.st.is_allowed(session.pub):
        return 0
    current = host.st.devices().get(session.device) if hasattr(host.st, "devices") else None
    if (not isinstance(current, dict) or current.get("device", session.device) != session.device
            or current.get("pub") != wire.b64u(session.pub)
            or current.get("sk") != wire.b64u(session.sign_pub)
            or host.st.sign_key(session.device) != session.sign_pub):
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
              count += 1
    return count
