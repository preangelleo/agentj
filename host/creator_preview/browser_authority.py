"""Short-lived browser creator grants. Only the Host can mint or use one."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import re
import secrets
import time

from agentj import approval_material, browser, browser_ego, cloud, controls, instance, preferences
from agentj.browser_cdp import CDPError
from . import browser_creator

SITES = browser_creator.SITES
from agentj.browser_login import RequestPhone

PHONE_TYPES = ("browser_grants", "browser_grant_prepare", "browser_grant", "browser_grant_revoke")
# Add a site only after the read journey passes on an actual Mac with Ego Lite.
FIELD_VERIFIED_SITES: frozenset[str] = frozenset()
DURATIONS = (15, 60, 480)
HEX_ID = re.compile(r"[0-9a-f]{32}")


def _stamp(path):
    try:
        s = path.stat()
    except FileNotFoundError:
        return None
    return (s.st_dev, s.st_ino, s.st_mtime_ns)


@dataclass(frozen=True)
class Context:
    state: object
    host_key: bytes
    channel: str
    agent: object
    history: str
    stop_at: int | None
    seat: tuple
    profile_revision: tuple
    preference_revision: tuple | None


@dataclass(frozen=True)
class Record:
    target: dict
    owner: RequestPhone
    context: Context
    runtime: tuple
    until: float
    lease_until: float


class CreatorGrants:
    def __init__(self, host):
        self.host = host
        self.st = host.st
        self.offers: dict[str, Record] = {}
        self.grants: dict[str, Record] = {}
        self.revision = dict.fromkeys(SITES, 0)
        self.worker = asyncio.Lock()

    def _context(self):
        """Rebind after a profile/settings replacement, seat move, or conversation change."""
        h = self.host
        if (browser.system() != "macos" or instance.current() != 1 or h.stopped()
                or h.stopping.is_set() or not browser.settings(self.st)["enabled"]):
            return None
        profile = _stamp(browser_ego._profile_file(self.st))
        if profile is None:
            return None
        seat = cloud.read_cloud(self.st) or {}
        seat_binding = (seat.get("host_id"), (seat.get("tenant") or {}).get("slug"), seat.get("linked_at"))
        return Context(h.st, h.kp.pub, h.channel, h.agent, str(h.hist.epoch), h.estop.get("at"),
                       seat_binding, profile, _stamp(preferences.path()))

    def _available(self):
        try:
            return self._context()
        except (OSError, ValueError):
            return None

    def _live(self, record: Record, *, conn=False) -> bool:
        return (record.target["site"] in FIELD_VERIFIED_SITES
                and time.monotonic() < record.until and time.time() < record.target["expires"]
                and self._available() == record.context and record.owner.still_paired(self.host)
                and (not conn or record.owner.matches(self.host, record.owner.session())))

    def _audit(self, target: dict, result: str, action=""):
        self.st.log("browser_grant", id=target.get("id"), site=target.get("site"), operation=action, result=result)

    def _prune(self):
        for book in (self.offers, self.grants):
            for key, record in list(book.items()):
                if not self._live(record):
                    self._audit(record.target, "expired" if time.monotonic() >= record.until else "authority_changed")
                    del book[key]

    def clear(self):
        for record in (*self.offers.values(), *self.grants.values()):
            self._audit(record.target, "estop")
        self.offers.clear()
        self.grants.clear()
        for site in SITES:
            self.revision[site] += 1

    async def _runtime(self):
        try:
            return await asyncio.to_thread(browser.runtime, self.st)
        except (OSError, CDPError):
            return None

    def _same_runtime(self, record, rt):
        return (rt and rt.get("backend") == browser_ego.BACKEND
                and rt.get("runtime_id") == record.runtime and self._live(record))

    async def _read(self, rt, site, account, action, live, until=None):
        try:
            return await asyncio.to_thread(browser_creator.read, rt, site, account, action, live, until)
        except (OSError, CDPError):
            return None

    async def _prepare(self, s, obj):
        site, account, minutes = obj.get("site"), obj.get("account"), obj.get("minutes")
        rule = SITES.get(site) if isinstance(site, str) else None
        if (rule is None or not isinstance(account, str) or not rule.account.fullmatch(account)
                or type(minutes) is not int or minutes not in DURATIONS):
            return "shape", None
        if site not in FIELD_VERIFIED_SITES:
            return "site_unavailable", None
        owner = RequestPhone.from_session(self.host, s)
        context = self._available()
        if owner is None or context is None:
            return "browser_unavailable", None
        if self.worker.locked():
            return "busy", None

        revision = self.revision[site]

        def live():
            return (self.revision[site] == revision and owner.matches(self.host, s)
                    and self._available() == context)

        async with self.worker:
            rt = await self._runtime()
            if not live() or not rt or rt.get("backend") != browser_ego.BACKEND:
                return "browser_unavailable", None
            verified = await self._read(rt, site, account, "verify", live)
            if not live() or not verified:
                return "account_unverified", None
            current = await self._runtime()
            if not live() or not current or rt["runtime_id"] != current.get("runtime_id"):
                return "browser_unavailable", None
            issued = time.monotonic()
            target = {
                "v": 1, "id": secrets.token_hex(16), "site": site, "origin": rule.origin,
                "account": account, "actions": list(rule.actions), "backend": browser_ego.BACKEND,
                "profile": "Agent J", "expires": int(time.time()) + minutes * 60,
                "host_binding": hashlib.sha256(controls.canonical_json(
                    [context.host_key.hex(), context.seat, rt["runtime_id"], tuple(map(str, context.profile_revision))]
                ).encode()).hexdigest(),
            }
            # Any refresh supersedes this remote's prior offer for this site.
            for key, offer in list(self.offers.items()):
                if offer.owner.device == owner.device and offer.target["site"] == site:
                    del self.offers[key]
            if len(self.offers) >= 16:
                return "busy", None
            self.offers[target["id"]] = Record(target, owner, context, rt["runtime_id"], issued + 120, issued + minutes * 60)
            self._audit(target, "offered")
            return None, target

    async def on_phone(self, s, obj):
        h = self.host
        action, r = obj.get("t"), obj.get("r")
        if (action not in PHONE_TYPES or not isinstance(r, str) or not re.fullmatch(r"[0-9a-f]{8,64}", r)
                or not getattr(s, "p33", False) or not h._paired_live(s)):
            return
        self._prune()

        async def respond(why=None, **extra):
            if h._paired_live(s):
                await h._ctl_res(s, r, action, not why, **({"why": why} if why else extra))

        if action == "browser_grants":
            rt = await self._runtime() if self.grants else None
            for site, grant in list(self.grants.items()):
                if not self._same_runtime(grant, rt):
                    self._audit(grant.target, "browser_changed")
                    del self.grants[site]
            return await respond(available=bool(FIELD_VERIFIED_SITES) and self._available() is not None,
                                 grants=[record.target for record in self.grants.values()])
        if action == "browser_grant_prepare":
            why, target = await self._prepare(s, obj)
            return await respond(why, target=target)
        if action == "browser_grant_revoke":
            ident = obj.get("id")
            if not isinstance(ident, str) or not HEX_ID.fullmatch(ident):
                return await respond("shape")
            target = {"id": ident}
            why = controls.check(self.st, h.nonces, h.channel, s.device, obj, action, target)
            if not why:
                for site, record in list(self.grants.items()):
                    if record.target["id"] == ident:
                        self._audit(record.target, "revoked")
                        del self.grants[site]
                        self.revision[site] += 1
                        # A signed revocation also closes any previously offered replacement.
                        for key, offer in list(self.offers.items()):
                            if offer.target["site"] == site:
                                del self.offers[key]
                        break
            controls.log(self.st, action=action, device=s.device, result=why or "ok", obj=target)
            return await respond(why)

        supplied = obj.get("target")
        ident = supplied.get("id") if isinstance(supplied, dict) else None
        offer = self.offers.get(ident) if isinstance(ident, str) and HEX_ID.fullmatch(ident) else None
        try:
            unchanged = offer is not None and controls.canonical_json(supplied) == controls.canonical_json(offer.target)
        except (TypeError, ValueError, RecursionError):
            unchanged = False
        if not unchanged or not offer.owner.matches(h, s):
            return await respond("changed")
        target = offer.target
        uv = await h.owner_uv_offer(s, r, action, target, obj)
        if uv is None:
            return
        why = controls.check(self.st, h.nonces, h.channel, s.device, obj, action, target)
        if not why:
            why = h.owner_uv_check(s, uv, obj)
        if why:
            controls.log(self.st, action=action, device=s.device, result="refused:" + why, obj=target)
            return await respond(why)
        # Consume before awaiting. Revoke or replace in the gap wins.
        if self.worker.locked():
            return await respond("busy")
        async with self.worker:
            rt = await self._runtime()
            def live():
                return self.offers.get(ident) is offer and self._live(offer, conn=True)
            result = None
            if live() and self._same_runtime(offer, rt):
                result = await self._read(rt, target["site"], target["account"], "verify", live,
                                          min(target["expires"], time.time() + offer.until - time.monotonic()))
            if not result or not live():
                return await respond("changed")
            del self.offers[ident]
            self.grants[target["site"]] = Record(target, offer.owner, offer.context, offer.runtime,
                                                 offer.lease_until, offer.lease_until)
            controls.log(self.st, action=action, device=s.device, result="ok", obj=target,
                         n=obj.get("n"), ts=obj.get("ts"), sig=obj.get("sig"))
            self._audit(target, "granted")
        await respond(grant=target)

    async def from_agent(self, req, peer):
        from agentj import provenance
        site, operation = req.get("site"), req.get("action")
        rule = SITES.get(site) if isinstance(site, str) else None
        if (set(req) != {"t", "site", "action"} or not rule or operation not in rule.actions):
            return {"ok": False, "reason": "unsupported_creator_action",
                    "hint": "此入口只读。请在主机浏览器亲自完成写入。 / This entry is read-only. Complete writes in the host browser."}
        if site not in FIELD_VERIFIED_SITES:
            return {"ok": False, "reason": "site_unavailable"}
        send = getattr(self.host.agent, "cur_send", None)
        if type(peer) is not int or send is None:
            return {"ok": False, "reason": "owner_phone_required"}
        proof = await asyncio.to_thread(approval_material.verified_phone_chat, self.host, peer)
        if not proof:
            return {"ok": False, "reason": "owner_phone_required"}
        self._prune()
        grant = self.grants.get(site)
        if not grant:
            return {"ok": False, "reason": "site_grant_required"}
        agent, source = proof
        caller = getattr(send, "phone_binding", None)
        agent_proc = getattr(agent, "proc", None)

        def live():
            return (self.grants.get(site) is grant and self._live(grant)
                    and agent is self.host.agent and getattr(agent, "cur_send", None) is send
                    and getattr(agent, "proc", None) is agent_proc
                    and isinstance(caller, RequestPhone) and caller.same_pairing(grant.owner)
                    and caller.matches(self.host, caller.session()) and not provenance.untrusted_turn(agent)
                    and approval_material.phone_main_live(agent))

        if not live():
            return {"ok": False, "reason": "owner_phone_required"}
        if self.worker.locked():
            return {"ok": False, "reason": "busy"}
        async with self.worker:
            rt = await self._runtime()
            if not live() or not self._same_runtime(grant, rt):
                return {"ok": False, "reason": "browser_or_grant_changed"}
            data = await self._read(rt, site, grant.target["account"], operation, live,
                                    min(grant.target["expires"], time.time() + grant.until - time.monotonic()))
            rt = await self._runtime()
            fresh = await asyncio.to_thread(approval_material.verified_phone_chat, self.host, peer)
            if not data or not live() or not self._same_runtime(grant, rt) or fresh != (agent, source):
                if self.grants.get(site) is grant and not data:
                    del self.grants[site]
                self._audit(grant.target, "unverified", operation)
                return {"ok": False, "reason": "browser_or_grant_changed"}
            self._audit(grant.target, "read", operation)
            return {"ok": True, "site": site, "action": operation, **data}
