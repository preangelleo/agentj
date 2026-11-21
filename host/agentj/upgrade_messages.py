"""Keep undelivered messages and already-completed replies across upgrades.

Only a host-sealed, never-submitted receipt is eligible. The old recv/deliv
states cannot prove that, even if the history page still looks queued.
"""
import asyncio
import json
import time
import weakref

from cryptography.exceptions import InvalidSignature

from . import compose, diskq, history, humane, phone_update, provenance, service_recovery, silent, wire
from .text import drop_replacement

DOMAIN = b"agentj:upgrade-held-phone-say:v1\x00"
MAX_AGE = phone_update.LEASE


def ordinary_phone_page(page, device):
    if not page or page.get("form") or page.get("silent"):
        return False
    source = page["src"]
    text = source.get("text")
    return (source.get("k") == "phone" and source.get("dev") == device
            and source.get("source_kind") == "phone" and source.get("source_role") == "owner"
            and isinstance(text, str) and text.strip() and not text.lstrip().startswith(("/", "／", "!"))
            and not any(key in source for key in ("att", "quote", "term", "form"))
            and not page["reply"].get("part"))


def _page_coordinates(hist, page, device):
    if hist.on and ordinary_phone_page(page, device):
        stamp = page.get("ts")
        if type(stamp) is int and stamp > 0:
            return hist.epoch, stamp
    return None


def bind_reply_page(host, send, page):
    """Pin the original store before queueing, so a live history reload cannot reuse its page number."""
    if host.hist.get(send.turn) is not page:
        return
    proof = _page_coordinates(host.hist, page, send.device)
    if proof is not None:
        send.restart_page = (weakref.ref(host.hist), proof)


def reply_page_coordinates(host, send):
    bound = getattr(send, "restart_page", None)
    if bound and bound[0]() is host.hist:
        current = _page_coordinates(host.hist, host.hist.get(send.turn), send.device)
        if current == bound[1]:
            return current
    return None


def matches_reply_page(hist, page, device, coordinates):
    return coordinates is not None and _page_coordinates(hist, page, device) == coordinates


def answered_page(hist, device, turn, coordinates):
    """Reconcile a stale delivery receipt with its already-final history page."""
    page = hist.get(turn)
    if not matches_reply_page(hist, page, device, coordinates) or page.get("end") != "done":
        return False
    text = page["reply"].get("text")
    return isinstance(text, str) and bool(text.strip()) and not silent.is_silent(text)


def checkpoint_reply(agent, proc, result):
    """Save an exact native terminal reply before the adapter's remaining async work."""
    host, send = agent.host, agent.cur_send
    if (result.get("subtype") != "success" or result.get("is_error") is not False or agent.halting
            or proc is not agent.proc or proc is not agent.turn_proc or not agent.own_turns()
            or type(send) is not compose.Send or send.state != "delivered" or send.source != provenance.phone()
            or getattr(host, "cur_turn", None) != send.turn or host.cur_failed or not host.hist.on
            or host.receipts.state(send.device, send.sid) != "deliv"):
        return
    session = result.get("session_id")
    if not isinstance(session, str) or not session or agent.init.get("session_id") != session:
        return
    phone = getattr(send, "phone_binding", None)
    page = host.hist.get(send.turn)
    if (phone is None or not phone.revision or not phone.matches(host, phone.session())
            or reply_page_coordinates(host, send) is None or page["end"] != "open"):
        return
    raw = result.get("result")
    if not isinstance(raw, str) or silent.is_silent(raw):
        return
    text = wire.well_formed(silent.strip_markers(drop_replacement(raw)))
    prior = page["reply"]["text"]
    if not text.strip() or wire.units(text) > history.PART_MAX or (prior and prior != text):
        return
    if not prior:
        host.agent_text(text)
    host.hist_update(send.turn, end="done")


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
    coordinates = _page_coordinates(host.hist, page, session.device)
    write = host.receipts.mark(session.device, sid, "held", page["id"], seal=seal, reply_page=coordinates)
    if write is None or not await asyncio.wrap_future(write) or not host.hist.last_write_ok or not await diskq.aflush():
        fail(host, session.device, sid, page, coordinates)
        return {"ok": False, "why": "other"}
    kick(host)
    return {"ok": True, "state": "queued", "turn": page["id"]}


def fail(host, device, sid, page, coordinates):
    if placeholder := host.sends.get(device, sid):
        placeholder.state = "failed"
    host.receipts.mark(device, sid, "done")
    text = humane.of(host, "say.restart_lost")
    if (page and host.hist.get(page["id"]) is page
            and matches_reply_page(host.hist, page, device, coordinates)):
        if page["end"] == "open":
            host.hist_update(page["id"], text=text, end="failed", card={})
    else:
        host.hist_add({"k": "sys", "text": ""}, text, "done")


async def resume(host):
    """Use the remaining authenticated session; never create a substitute owner."""
    if not host.agent or pending(host.st):
        return
    for device, sid in list(host.receipts.items):
        row = host.receipts.items.get((device, sid))
        if row is None or row["s"] != "held":
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
        # Legacy rows can acquire page coordinates only from the verified sealed handoff.
        coordinates = _page_coordinates(host.hist, page, device) if verified else row.get("reply_page")
        if not verified or host.stopped() or host.seat_refusal():
            fail(host, device, sid, page, coordinates)
            continue
        # Acquire synchronously before awaiting IO: concurrent reconnects can never submit twice.
        write = host.receipts.mark(device, sid, "recv", page["id"], reply_page=coordinates)
        if write is None or not await asyncio.wrap_future(write):
            fail(host, device, sid, page, coordinates)
            continue
        if binding(host, session, sid, page) != proof or host.stopped() or host.seat_refusal():
            fail(host, device, sid, page, coordinates)
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
            fail(host, device, sid, page, coordinates)


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
