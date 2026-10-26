"""P547 (PROTOCOL "P547 §A", ADR-P547): a remote's Face ID key set up with ANOTHER remote's approval.

The host decides, never the page. A device D that asks (`pk_peer_req`) gets a direct `pk_offer` only when it is the first one
with a key (no other listed device has a `pk` record and neither has D). Otherwise another listed device with its OWN key, in a
ready p33 session, must approve with an assertion of that key over

    passkey.elevate_challenge(channel, approver device, id, "pk_peer", id, digest)
    digest = hex SHA-256("agentj-pk-peer-v1\\n" + D + "\\n" + D's name + "\\n" + code)

where `code` (6 digits) is derived from the request id, D and D's X25519 key and shown on both screens. D's old key is not
asked for (#300 a borrowed key, #317 a key lost with a reinstall): the approval replaces it. One approval = one device, one
registration (`pk_offer.peer` → the next `pk_reg` of D replaces the record without `replace_fa`; State.set_passkey(peer_ok)).
Limits: PEER_TTL s per request, one per device, MAX_OPEN per host, RATE_N requests per device per RATE_WINDOW s (in memory —
a restart ends every request, which only narrows). Every decision: approvals.log (reason "pk_peer", the assertion as hashes),
host.log, activity, a notice to every remote and a `security` push."""
from __future__ import annotations

import asyncio
import hashlib
import secrets
import time

from . import approvals, passkey, wire
from .text import clean_label, pick

TYPES = ("pk_peer_req", "pk_peer_answer", "pk_peer_cancel")
KIND = "pk_peer"
PEER_TTL = 300
MAX_OPEN = 3
RATE_N, RATE_WINDOW = 5, 600
CODE_LABEL = "agentj/pk-peer/code/v1"
DIGEST_LABEL = "agentj-pk-peer-v1"


def code(rid: str, device: str, x25519_b64u: str) -> str:
    h = hashlib.sha256(f"{CODE_LABEL}\n{rid}\n{device}\n{x25519_b64u}".encode()).digest()
    return f"{int.from_bytes(h[:4], 'big') % 10**6:06d}"


def digest(device: str, name: str, code_: str) -> str:
    return hashlib.sha256(f"{DIGEST_LABEL}\n{device}\n{name}\n{code_}".encode()).hexdigest()


class Req:
    __slots__ = ("id", "device", "name", "code", "digest", "at", "exp", "state", "by", "timer")

    def __init__(self, rid, device, name, code_, now):
        self.id, self.device, self.name, self.code = rid, device, name, code_
        self.digest = digest(device, name, code_)
        self.at, self.exp, self.state, self.by, self.timer = int(now), int(now) + PEER_TTL, "open", "", None


class Desk:
    """Wired into serve.Host as `self.pkpeer`."""

    def __init__(self, host, clock=time.time):
        self.host, self.clock = host, clock
        self.reqs: dict[str, Req] = {}
        self.hits: dict[str, list[float]] = {}

    # ------------------------------------------------------------ helpers
    def _live(self) -> list[Req]:
        now = self.clock()
        for r in [r for r in self.reqs.values() if r.exp <= now]:
            self._drop(r)
        return list(self.reqs.values())

    def _drop(self, r: Req) -> None:
        self.reqs.pop(r.id, None)
        if r.timer is not None and r.timer is not asyncio.current_task():
            r.timer.cancel()

    def _pk_id(self, did: str) -> str | None:
        pk = self.host.st.passkey_of_device(did)
        return pk["id"] if pk else None

    def _ready(self, s) -> bool:
        h = self.host
        return h.sessions.get(s.cid) is s and s.state == "ready" and s.p33 and h.st.is_allowed(s.pub)

    def _eligible(self, did: str) -> bool:
        """Review minor 4: a device approves only with a key IT registered (`dev` == its id; a legacy record without `dev`
        counts). A record moved by a passkey restore keeps the registering id — its key lives on another device, and asking
        it would pop the browser's cross-device QR sheet."""
        pk = self.host.st.passkey_of_device(did)
        return bool(pk) and pk.get("dev", did) == did

    def _approver(self, s, r: Req) -> bool:
        """Another listed device, with its own key (never the credential D holds), registered by itself."""
        mine = self._pk_id(s.device)
        return bool(mine) and s.device != r.device and mine != self._pk_id(r.device) and self._eligible(s.device)

    def keyed_others(self, did: str) -> list[str]:
        """The other listed devices with a key that is not `did`'s own credential."""
        mine = self._pk_id(did)
        return [d for d in self.host.st.devices() if d != did and (k := self._pk_id(d)) and k != mine]

    def direct_ok(self, did: str) -> bool:
        """Model §2 (review M1): a direct (unapproved) pk_offer only while no OTHER listed device holds a key."""
        return not self.keyed_others(did)

    def _sessions(self, did: str | None = None):
        return [x for x in list(self.host.sessions.values()) if self._ready(x) and (did is None or x.device == did)]

    def ask_msg(self, s, r: Req) -> dict:
        h = self.host
        ch = passkey.elevate_challenge(h.channel, s.device, r.id, KIND, r.id, r.digest)
        return {"t": "pk_peer_ask", "id": r.id, "device": r.device, "name": r.name, "at": r.at, "exp": r.exp, "code": r.code,
                "fa": self._pk_id(s.device), "fa_ch": wire.b64u(ch)}

    async def _close(self, r: Req, skip=None) -> None:
        for x in self._sessions():
            if x is not skip and x.device != r.device and self._pk_id(x.device):
                await self.host.send_app(x, {"t": "pk_peer_close", "id": r.id})

    async def _to_d(self, r: Req, msg: dict) -> None:
        for x in self._sessions(r.device):
            await self.host.send_app(x, msg)

    async def _offer(self, r: Req) -> None:
        await self._to_d(r, {"t": "pk_peer_done", "id": r.id, "ok": True, "by": r.by})
        for x in self._sessions(r.device):
            await self.host._pk_offer(x, peer=r.id)

    async def _expire(self, r: Req) -> None:
        await asyncio.sleep(max(0.0, r.exp - self.clock()))
        if self.reqs.get(r.id) is r:
            self._drop(r)
            self.host.st.log("pk_peer", device=r.device, result="expired", id=r.id)
            await self._to_d(r, {"t": "pk_peer_done", "id": r.id, "ok": False, "why": "expired"})
            await self._close(r)

    def _arm(self, r: Req) -> None:
        if r.timer is not None:
            r.timer.cancel()
        try:
            r.timer = self.host._bg(self._expire(r), "pk_peer_expire")
        except RuntimeError:
            r.timer = None

    # ------------------------------------------------------------ serve hooks
    def approved(self, device: str, rid) -> bool:
        """Did THIS host mark request `rid` approved for `device` (and is it still valid, not yet used)?"""
        r = self.reqs.get(rid) if isinstance(rid, str) else None
        return bool(r) and r.device == device and r.state == "approved" and r.exp > self.clock()

    def used(self, rid: str) -> None:
        r = self.reqs.get(rid)
        if r:
            self._drop(r)
            self.host.st.log("pk_peer", device=r.device, result="used", id=r.id)

    async def on_ready(self, s) -> None:
        """A device came back within the TTL: an approver sees the open cards, D its approved offer."""
        if not s.p33:
            return
        for r in self._live():
            if r.state == "open" and self._approver(s, r):
                await self.host.send_app(s, self.ask_msg(s, r))
            elif r.state == "approved" and s.device == r.device:
                await self.host.send_app(s, {"t": "pk_peer_done", "id": r.id, "ok": True, "by": r.by})
                await self.host._pk_offer(s, peer=r.id)

    async def on_phone(self, s, t: str, obj: dict) -> None:
        if t == "pk_peer_req":
            return await self._req(s, obj)
        rid = obj.get("id")
        r = self.reqs.get(rid) if isinstance(rid, str) else None
        if r is None or r.exp <= self.clock():
            if t == "pk_peer_answer" and isinstance(rid, str) and len(rid) <= 32:
                await self.host.send_app(s, {"t": "pk_peer_answer_res", "id": rid, "ok": False, "why": "expired"})
            return
        if t == "pk_peer_cancel":
            if s.device == r.device:
                self._drop(r)
                self.host.st.log("pk_peer", device=r.device, result="cancelled", id=r.id)
                await self._to_d(r, {"t": "pk_peer_done", "id": r.id, "ok": False, "why": "cancelled"})
                await self._close(r)
            return
        await self._answer(s, r, obj)

    # ------------------------------------------------------------ D asks
    async def _req(self, s, obj: dict) -> None:
        h = self.host
        rid = obj.get("r")
        if not wire.is_rid(rid):
            return
        res = {"t": "pk_peer_res", "r": rid}
        mine = self._pk_id(s.device)
        others = self.keyed_others(s.device)
        if not others:
            if mine:
                return await h.send_app(s, {**res, "ok": False, "why": "no_approver"})
            await h.send_app(s, {**res, "ok": True, "mode": "direct"})
            return await h._pk_offer(s)
        if not any(self._eligible(d) for d in others):     # every other key was restored onto its record: nobody can approve
            return await h.send_app(s, {**res, "ok": False, "why": "no_approver"})
        live = self._live()
        r = next((x for x in live if x.device == s.device), None)
        if r is None:
            now = self.clock()
            hits = [t for t in self.hits.get(s.device, []) if now - t < RATE_WINDOW]
            self.hits[s.device] = hits
            if len(hits) >= RATE_N:
                return await h.send_app(s, {**res, "ok": False, "why": "rate"})
            if len(live) >= MAX_OPEN:
                return await h.send_app(s, {**res, "ok": False, "why": "busy"})
            hits.append(now)
            name = clean_label(h._dev_label(s.device)) or s.device
            new = secrets.token_bytes(16)
            r = Req(wire.b64u(new), s.device, name, code(wire.b64u(new), s.device, wire.b64u(s.pub)), now)
            self.reqs[r.id] = r
            self._arm(r)
            h.st.log("pk_peer", device=s.device, result="asked", id=r.id)
            for x in self._sessions():
                if self._approver(x, r):
                    await h.send_app(x, self.ask_msg(x, r))
        online = len({x.device for x in self._sessions() if self._approver(x, r)})
        await h.send_app(s, {**res, "ok": True, "mode": "peer", "id": r.id, "code": r.code, "exp": r.exp, "online": online})
        if r.state == "approved":
            await self._offer(r)

    # ------------------------------------------------------------ an approver answers
    async def _answer(self, s, r: Req, obj: dict) -> None:
        h = self.host
        reply = {"t": "pk_peer_answer_res", "id": r.id}
        if r.state != "open" or not self._approver(s, r):
            return await h.send_app(s, {**reply, "ok": False, "why": "gone" if r.state != "open" else "not_approver"})
        ok = obj.get("ok") is True
        pk = h.st.passkey_of_device(s.device)
        fa = None
        if ok:
            try:
                f = passkey.approval_fields(obj.get("fa"))
                if f["id"] != pk["id"]:
                    raise passkey.PasskeyError("bad", "credential")
                ch = passkey.elevate_challenge(h.channel, s.device, r.id, KIND, r.id, r.digest)
                h.st.passkey_count(s.device, pk["id"], passkey.verify_assertion(pk, f, ch))
            except (passkey.PasskeyError, ValueError, TypeError) as e:
                h.st.log("pk_peer", device=s.device, result="refused",
                         reason=getattr(e, "detail", type(e).__name__), id=r.id)
                return await h.send_app(s, {**reply, "ok": False, "why": "passkey_failed"})
            fa = {k: hashlib.sha256(f[k] if isinstance(f[k], bytes) else f[k].encode()).hexdigest() for k in ("id", "cd", "ad", "sig")}
        by = s.name or h._dname(s.device) or s.device
        try:
            approvals.record(h.st, rid=r.id, agent="phone", tool=KIND, input_sha256=r.digest, shown_sha256=r.digest,
                             decision="allow" if ok else "deny", reason=KIND, device=s.device, fa=fa)
        except Exception:  # noqa: BLE001 — the log line never costs the decision (host.log has it)
            h.st.log("approvals_log", status="failed")
        h.st.log("pk_peer", device=s.device, result="allow" if ok else "deny", id=r.id, by=r.device)
        h.activity("decision", by=by, result="allow" if ok else "deny",
                   tool=pick(h.lang, "另一台批准设置 Face ID", "Face ID set-up approved by another remote"), text=r.name)
        await h.send_app(s, {**reply, "ok": True})
        await self._close(r, skip=s)
        when = time.strftime("%Y-%m-%d %H:%M")
        if ok:
            r.state, r.by, r.exp = "approved", clean_label(by)[:64], int(self.clock()) + PEER_TTL
            self._arm(r)
            await self._offer(r)
            text = pick(h.lang, f"遥控器「{by}」批准「{r.name}」在本机设置 Face ID / 指纹 · {when}",
                        f"Remote “{by}” approved Face ID / fingerprint set-up on “{r.name}” · {when}")
        else:
            self._drop(r)
            await self._to_d(r, {"t": "pk_peer_done", "id": r.id, "ok": False, "why": "denied"})
            text = pick(h.lang, f"遥控器「{by}」拒绝了「{r.name}」设置 Face ID / 指纹 · {when}",
                        f"Remote “{by}” declined Face ID / fingerprint set-up on “{r.name}” · {when}")
        await h.broadcast(text, frm="notice")
        h.push_notify("security")
