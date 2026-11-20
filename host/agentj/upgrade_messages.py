"""Keep the first dispatch of a paired phone message on the far side of an upgrade.

Only a host-sealed, never-submitted receipt is eligible. The old recv/deliv
states cannot prove that, even if the history page still looks queued.
"""
import asyncio
import json
import time

from cryptography.exceptions import InvalidSignature

from . import compose, diskq, humane, phone_update, provenance, service_recovery, wire

DOMAIN = b"agentj:upgrade-held-phone-say:v1\x00"
MAX_AGE = phone_update.LEASE


def pending(st):
    if phone_update.busy(st):
        return True
    record = service_recovery.read(st) or {}
    return (record.get("status") in ("pending", "running", "waiting")
            and 0 <= time.time() - record["at"] <= service_recovery.READY_LIMIT + 300)


def binding(host, session, sid, page):
    phone = getattr(session, "login_phone", None)
    if phone is None or not phone.revision or not phone.matches(host, session):
        return None
    # Controls writes the stop state by rename. Two stop/resume operations can share a wall-clock second.
    try:
        stamp = host.st.estop_path.stat()
        stop_generation = (stamp.st_dev, stamp.st_ino, stamp.st_mtime_ns)
    except FileNotFoundError:
        stop_generation = None
    except OSError:
        return None
    return DOMAIN + json.dumps([host.channel, wire.b64u(host.kp.pub), session.device, wire.b64u(phone.sign), stop_generation,
                                phone.revision, host.hist.epoch, page["id"], page["ts"], sid, page["src"]],
                               ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


async def hold(host, session, sid, text):
    """No model, peer workflow, approval or native route has seen this message."""
    phone = getattr(session, "login_phone", None)
    if (provenance._paired_source() != provenance.phone() or not host.hist.on or phone is None
            or not pending(host.st) or not phone.revision or not phone.matches(host, session)):
        return None
    src = {"k": "phone", "dev": session.device, "name": (session.name or session.device)[:64],
           "text": text, "source_kind": "phone", "source_role": "owner"}
    page = host.hist_add(src, "", "open", card={"queued": True})
    payload = binding(host, session, sid, page)
    if payload is None:
        host.hist_update(page["id"], end="failed", text=humane.of(host, "say.restart_lost"))
        return {"ok": False, "why": "not_paired"}
    try:
        seal = wire.b64u(host.st.signing_key().sign(payload))
    except (OSError, ValueError):
        host.hist_update(page["id"], end="failed", text=humane.of(host, "say.restart_lost"))
        return {"ok": False, "why": "other"}
    send = compose.Send(session.device, sid, page["id"])
    host.sends.add(send)
    write = host.receipts.mark(session.device, sid, "held", page["id"], seal=seal)
    if write is None or not await asyncio.wrap_future(write) or not host.hist.last_write_ok or not await diskq.aflush():
        send.state = "failed"
        host.receipts.mark(session.device, sid, "done", page["id"])
        host.hist_update(page["id"], end="failed", text=humane.of(host, "say.restart_lost"))
        return {"ok": False, "why": "other"}
    kick(host)
    return {"ok": True, "state": "queued", "turn": page["id"]}


def fail(host, device, sid, page):
    if placeholder := host.sends.get(device, sid):
        placeholder.state = "failed"
    host.receipts.mark(device, sid, "done")
    if page and page["end"] == "open":
        host.hist_update(page["id"], text=humane.of(host, "say.restart_lost"), end="failed", card={})


async def resume(host):
    """Use the remaining authenticated session; never create a substitute owner."""
    if not host.agent or pending(host.st):
        return
    for (device, sid), row in list(host.receipts.items.items()):
        if row["s"] != "held":
            continue
        page = host.hist.get(row["n"])
        session = next((s for s in host.sessions.values() if s.device == device and host._paired_live(s)), None)
        # A disconnected device may return. A revoked one cannot.
        if session is None and device in host.st.devices() and time.time() - row["t"] <= MAX_AGE:
            continue
        proof = binding(host, session, sid, page) if session and page else None
        current = (page and page["end"] == "open" and not page["reply"].get("text") and host.hist.on
                   and 0 <= time.time() - page["ts"] / 1000 <= MAX_AGE)
        verified = False
        if current and proof:
            try:
                host.st.signing_key().public_key().verify(wire.unb64u(row.get("seal", "")), proof)
                verified = True
            except (OSError, ValueError, TypeError, InvalidSignature):
                pass
        if not verified or host.stopped() or host.seat_refusal():
            fail(host, device, sid, page)
            continue
        # Acquire synchronously before awaiting IO: concurrent reconnects can never submit twice.
        write = host.receipts.mark(device, sid, "recv", page["id"])
        if write is None or not await asyncio.wrap_future(write):
            fail(host, device, sid, page)
            continue
        if binding(host, session, sid, page) != proof or host.stopped() or host.seat_refusal():
            fail(host, device, sid, page)
            continue
        # The platform holds the original authenticated words, never an approval.
        placeholder = host.sends.get(device, sid)

        def allowed(session=session, placeholder=placeholder, page=page, sid=sid, proof=proof):
            return (binding(host, session, sid, page) == proof and not host.stopped() and not host.seat_refusal()
                    and host.hist.get(page["id"]) is page and page["end"] == "open"
                    and (placeholder is None or placeholder.state != "cancelled"))

        from . import main_thread
        main_thread.owner_queued(host)
        if await host._accept(session, page["src"]["text"], sid, held_page=page, held_guard=allowed) is None:
            fail(host, device, sid, page)


def kick(host):
    if getattr(host, "_upgrade_messages_task", None) and not host._upgrade_messages_task.done():
        return
    if not any(r["s"] == "held" for r in host.receipts.items.values()):
        return
    async def drain():
        while not host.stopping.is_set() and any(r["s"] == "held" for r in host.receipts.items.values()):
            await resume(host)
            await asyncio.sleep(2)
    host._upgrade_messages_task = host._bg(drain(), "upgrade_held_messages")
