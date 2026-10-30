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
host.log, activity, a notice to every remote and a `security` push.

P555 (PASSKEY-SEAT-MODEL §2 revised): a device whose record already holds a key is never sent to another remote first.
- A key saved before P539 under the device's own id counts as this device's own (State.passkey_of_device): zero taps.
- A record whose key is truly `unknown` (a restore moved it here) gets a self-check with its `pk_state`: `fa` (its own
  credential id) + `fa_ch` over elevate_challenge(channel, device, n, "pk_check", n, CHECK_DIGEST). One Face ID tap →
  `pk_check {n, fa, aa}`; verified → `local` is noted (State.passkey_local) and no pk_peer request is made. Only a failed /
  cancelled check (or no key) goes on to a pk_peer request.
- Nobody can approve (the only keyed remote lost its key, or no other remote holds one): the request still opens; the owner
  approves it at the Agent J host's terminal with the 对照码 (`agentj faceid approve <code>`, ctl `pk_peer_host`: a
  claim like `resume` — never an Agent — plus ALWAYS the remote pairing password, the bar of `agentj pair`; a terminal alone
  is no proof against a same-uid Agent, security review B1). Only while no eligible remote could approve instead."""
from __future__ import annotations

import asyncio
import hashlib
import secrets
import time

from . import approvals, passkey, wire
from .text import clean_label, pick

TYPES = ("pk_peer_req", "pk_peer_answer", "pk_peer_cancel", "pk_check")
KIND = "pk_peer"
PEER_TTL = 300
MAX_OPEN = 3
RATE_N, RATE_WINDOW = 5, 600
CODE_LABEL = "agentj/pk-peer/code/v1"
DIGEST_LABEL = "agentj-pk-peer-v1"
CHECK_KIND = "pk_check"
CHECK_DIGEST = hashlib.sha256(b"agentj-pk-check-v1").hexdigest()
CHECK_TTL = 600
CHECKS = 4            # P566: open self-check challenges kept per device
HOST_BY = "host"                                   # r.by of a terminal approval (the page words it)
HOST_MISS_N, HOST_MISS_WINDOW = 5, 600             # wrong 对照码 at the terminal before it stops listening for a while


def code(rid: str, device: str, x25519_b64u: str) -> str:
    h = hashlib.sha256(f"{CODE_LABEL}\n{rid}\n{device}\n{x25519_b64u}".encode()).digest()
    return f"{int.from_bytes(h[:4], 'big') % 10**6:06d}"


def digest(device: str, name: str, code_: str, sign_pub: str = "", roster: bool = False) -> str:
    return hashlib.sha256((f"{DIGEST_LABEL}\n{device}\n{name}\n{code_}" + ("\n" + sign_pub if sign_pub else "") + ("\nroster" if roster else "")).encode()).hexdigest()


class Req:
    __slots__ = ("id", "device", "name", "code", "digest", "at", "exp", "state", "by", "timer", "x", "via", "sign_pub", "intro")

    def __init__(self, rid, device, name, code_, now, x="", sign_pub="", intro=False):
        self.intro = intro
        self.id, self.device, self.name, self.code = rid, device, name, code_
        self.x, self.via, self.sign_pub = x, None, sign_pub       # P574: D's X25519 key; the approver's assertion (owner_grant.via_ok checks it)
        self.digest = digest(device, name, code_, sign_pub, intro)
        self.at, self.exp, self.state, self.by, self.timer = int(now), int(now) + PEER_TTL, "open", "", None


class Desk:
    """Wired into serve.Host as `self.pkpeer`."""

    def __init__(self, host, clock=time.time):
        self.host, self.clock = host, clock
        self.reqs: dict[str, Req] = {}
        self.hits: dict[str, list[float]] = {}
        # P555: open self-checks. P566 (#364): kept per DEVICE (≤ CHECKS each, CHECK_TTL), not per socket — the Face ID sheet's drop
        # gave the answer to a new socket, whose own pk_state already carried a fresh challenge; each n still answers once
        self.checks: dict[str, list[tuple[str, float]]] = {}
        self.host_misses: list[float] = []

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
                **({"roster": True} if r.intro else {}), "sign_pub": r.sign_pub, "fa": self._pk_id(s.device), "fa_ch": wire.b64u(ch)}

    async def _close(self, r: Req, skip=None) -> None:
        for x in self._sessions():
            if x is not skip and x.device != r.device and self._pk_id(x.device):
                await self.host.send_app(x, {"t": "pk_peer_close", "id": r.id})

    async def _to_d(self, r: Req, msg: dict) -> None:
        for x in self._sessions(r.device):
            await self.host.send_app(x, msg)

    async def _offer(self, r: Req) -> None:
        if r.intro:
            pk = self.host.st.passkey_of_device(r.device)
            if pk and r.via and self.host.peers is not None and self.host.peers.xfer.bound(r.device):
                self.host.peers.xfer.note_via(pk["id"], r.via)
                await self._to_d(r, {"t":"pk_peer_done", "id":r.id, "ok":True, "by":r.by, "roster":True})
                self._drop(r)
            return
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

    def via_of(self, rid: str) -> dict | None:
        """P574: the approving remote's assertion for request `rid` (None: approved at the terminal, or gone)."""
        r = self.reqs.get(rid)
        return r.via if r is not None and r.state == "approved" and r.by != HOST_BY else None

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

    # ------------------------------------------------------------ P555 self-check of a key the host cannot place
    def check_fields(self, s) -> dict:
        """With `pk_state`: a fresh self-check challenge when this device's key is `unknown` — P566 (#346): or `local` (Settings'
        「测试一下」 on 「已设好」; a pass changes nothing, the answer only says the key works here) — else nothing."""
        pk = self.host.st.passkey_of_device(s.device)
        if not pk or passkey.pk_state(pk) not in ("unknown", "local"):
            return {}
        n = wire.b64u(secrets.token_bytes(16))
        now = self.clock()
        live = [c for c in self.checks.get(s.device, []) if c[1] > now]
        self.checks[s.device] = (live + [(n, now + CHECK_TTL)])[-CHECKS:]
        ch = passkey.elevate_challenge(self.host.channel, s.device, n, CHECK_KIND, n, CHECK_DIGEST)
        return {"fa": pk["id"], "fa_ch": wire.b64u(ch), "n": n}

    async def _check(self, s, obj: dict) -> None:
        h = self.host
        mine, n = self.checks.get(s.device, []), obj.get("n")
        c = next((x for x in mine if isinstance(n, str) and x[0] == n), None)
        if c is not None:                         # single use, whatever the outcome
            rest = [x for x in mine if x is not c]
            if rest:
                self.checks[s.device] = rest
            else:
                self.checks.pop(s.device, None)
        pk = h.st.passkey_of_device(s.device)
        was = h.st.passkey_state(s.device)        # P566 review S2: before passkey_count notes a working `unknown` key
        res = {"t": "pk_check_res", **({"r": obj["r"]} if wire.is_rid(obj.get("r")) else {})}   # the page's ask1 waits on `r`
        try:
            if not c or c[1] <= self.clock() or not pk:
                raise passkey.PasskeyError("expired", "no_check")
            f = passkey.approval_fields(obj.get("fa"))
            if f["id"] != pk["id"]:
                raise passkey.PasskeyError("bad", "credential")
            ch = passkey.elevate_challenge(h.channel, s.device, n, CHECK_KIND, n, CHECK_DIGEST)
            h.st.passkey_count(s.device, pk["id"], passkey.verify_assertion(pk, f, ch))
        except (passkey.PasskeyError, ValueError, TypeError) as e:
            h.st.log("pk_check", device=s.device, result="fail", reason=getattr(e, "detail", type(e).__name__))
            await h.send_app(s, {**res, "ok": False})
            # P566 (#346): a fresh challenge for the next 「测试一下」 / self-check (each one answers once)
            return await h.send_app(s, {"t": "pk_state", "s": h.st.passkey_state(s.device), **self.check_fields(s)})
        local = passkey.attachment(obj.get("aa")) != "cross-platform"
        # P566 review S2: 「测试一下」 on a key already known as this device's own changes nothing (`aa` is the browser's unsigned
        # word); only an `unknown` record is placed by its self-check, as in P555
        if was == "unknown":
            h.st.passkey_local(s.device, pk["id"], local)
        h.st.log("pk_check", device=s.device, result="ok", local=local)
        await h.send_app(s, {**res, "ok": True, "s": h.st.passkey_state(s.device)})
        await h.remotes.changed()                 # the remotes list and this device's own row repaint

    # ------------------------------------------------------------ P555 the owner approves at the Agent J host's terminal
    def nobody_else(self, did: str) -> bool:
        """No other listed remote could approve `did` with its own key (the only case the terminal may)."""
        return not any(self._eligible(d) for d in self.keyed_others(did))

    async def host_approve(self, code_: str) -> dict:
        """ctl `pk_peer_host` (serve already took the claim + the remote pairing password): the open request with this 对照码,
        and only while no other remote could approve it (security review B1)."""
        h = self.host
        now = self.clock()
        self.host_misses = [t for t in self.host_misses if now - t < HOST_MISS_WINDOW]
        if len(self.host_misses) >= HOST_MISS_N:
            h.st.log("pk_peer", device="host", result="refused", reason="rate")
            return {"ok": False, "error": "rate"}
        hit = [r for r in self._live() if r.state == "open" and r.code == code_] if isinstance(code_, str) else []
        if len(hit) != 1:
            self.host_misses.append(now)
            h.st.log("pk_peer", device="host", result="refused", reason="no_request")
            if len(self.host_misses) == HOST_MISS_N:
                await h.broadcast(pick(h.lang, "Agent J 主机终端上连续输错了 Face ID 设置的对照码，已暂停 10 分钟。不是你本人在操作，请检查这台 Agent J 主机。",
                                       "Wrong Face ID set-up codes were typed at the Agent J host terminal; paused for 10 minutes. If that was not you, check this computer."), frm="notice")
                h.push_notify("security")
            return {"ok": False, "error": "no_request"}
        r = hit[0]
        if not self.nobody_else(r.device):
            h.st.log("pk_peer", device="host", result="refused", reason="approver_exists", id=r.id)
            return {"ok": False, "error": "approver_exists"}
        try:
            approvals.record(h.st, rid=r.id, agent="terminal", tool=KIND, input_sha256=r.digest, shown_sha256=r.digest,
                             decision="allow", reason=KIND, device="host-terminal")
        except Exception:  # noqa: BLE001
            h.st.log("approvals_log", status="failed")
        h.st.log("pk_peer", device="host", result="allow", id=r.id, by=r.device)
        h.activity("decision", by=pick(h.lang, "Agent J 主机终端", "Agent J host terminal"), result="allow",
                   tool=pick(h.lang, "在 Agent J 主机上批准设置 Face ID", "Face ID set-up approved on the Agent J host"), text=r.name)
        await self._close(r)
        r.state, r.by, r.exp = "approved", HOST_BY, int(self.clock()) + PEER_TTL
        self._arm(r)
        await self._offer(r)
        when = time.strftime("%Y-%m-%d %H:%M")
        await h.broadcast(pick(h.lang, f"在 Agent J 主机终端批准「{r.name}」在本机设置 Face ID / 指纹 · {when}",
                               f"“{r.name}” Face ID / fingerprint set-up approved at the Agent J host terminal · {when}"), frm="notice")
        h.push_notify("security")
        return {"ok": True, "name": r.name}

    async def on_phone(self, s, t: str, obj: dict) -> None:
        if t == "pk_check":
            return await self._check(s, obj)
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
        intro = obj.get("roster") is True
        if intro and (h.peers is None or not h.peers.xfer.bound(s.device)):
            return await h.send_app(s, {**res, "ok":False, "why":"roster_binding_required"})
        mine = self._pk_id(s.device)
        others = self.keyed_others(s.device)
        if not others and not mine:
            await h.send_app(s, {**res, "ok": True, "mode": "direct"})
            return await h._pk_offer(s)
        # P555: nobody can approve (D holds the only key but it failed here, or every other key was restored onto its
        # record) → the request still opens; the owner approves it at the Agent J host's terminal (host_approve)
        live = self._live()
        r = next((x for x in live if x.device == s.device and x.intro == intro), None)
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
            r = Req(wire.b64u(new), s.device, name, code(wire.b64u(new), s.device, wire.b64u(s.pub)), now, wire.b64u(s.pub), wire.b64u(h.st.sign_key(s.device) or b""), intro)
            self.reqs[r.id] = r
            self._arm(r)
            h.st.log("pk_peer", device=s.device, result="asked", id=r.id)
            for x in self._sessions():
                if self._approver(x, r):
                    await h.send_app(x, self.ask_msg(x, r))
        online = len({x.device for x in self._sessions() if self._approver(x, r)})
        await h.send_app(s, {**res, "ok": True, "mode": "peer", "id": r.id, "code": r.code, "exp": r.exp, "online": online,
                             **({"host": 1} if self.nobody_else(s.device) else {})})   # P555: only then the terminal way
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
            # P574 (§17.13): kept whole (public data: an assertion over this request) so a same-owner friend can check that
            # an already trusted remote's Face ID approved this device before it trusts the device's new key
            r.via = {"cred": f["id"], "dev": s.device, "channel": h.channel, "rid": r.id, "device": r.device, "name": r.name,
                     "x": r.x, "sign_pub": r.sign_pub, "roster": r.intro, "cd": wire.b64u(f["cd"]), "ad": wire.b64u(f["ad"]), "sig": wire.b64u(f["sig"])}
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
