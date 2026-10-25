"""P171 (PROTOCOL §24, ADR-P171): the phone's 「遥控器管理」 page — list, rename, unbind and add remotes from a paired remote.

Every write is a signed phone control (controls.py): only a ready, allowlisted session of a device with an approval key, with
a valid Ed25519 signature over the target, each nonce once. Reads (`dev_list`) and `dev_cancel` (narrows only) are unsigned.

Adding keeps the safety-code comparison of §4: `dev_add` makes a pairing exactly like `agentj pair` (5 min, one-time) and
hands its link + QR to the asking remote only; the new device shows its 6-digit code; the human types that code on the
asking remote (`dev_confirm`, signed over device id + code). One try: a wrong code ends the pairing. The approval
passphrase (L2) is not asked here — like the account path (§19, P78) the proof is a paired remote's own signature plus the
code; `agentj remote-pair off` switches both off.

P190 (§27, reverse pairing): `dev_add` may carry `rv` = the rendezvous channel of a code the NEW device shows; the asking
remote scanned it and hands the link to that device itself, sealed to the code's key. Then `dev_pending` carries the safety
code too (`sas`): both screens show the same 6 digits and the human confirms they match on the remote (`dev_confirm`, same
signed check). Everything else — one pairing at a time, the 10-remote cap and its eviction, the logs — is the §24 path. Every add / unbind / rename is a controls.log + approvals.log line (adds /
unbinds) + activity line, and adds / unbinds are a notice to every remote plus a `security` push.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
import time

from . import activity, approvals, controls, wire
from .devnames import device_names   # P179
from .state import MAX_DEVICES
from .text import clean_label, pick

ACTIONS = ("dev_name", "dev_unbind", "dev_add", "dev_confirm")
TYPES = ("dev_list", "dev_cancel") + ACTIONS
NAME_MAX = 64
_CODE_LEN = 6
_RV = re.compile(r"[A-Za-z0-9_-]{22}")


def name_problem(name) -> str | None:
    if not isinstance(name, str):
        return "shape"
    s = name.strip()
    if not 1 <= len(s) <= NAME_MAX or any(ord(c) < 32 or ord(c) == 127 for c in s):
        return "bad_name"
    return None


def _num(v) -> int:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


def rows(st, online: set, me: str | None = None) -> list[dict]:
    """The allowlist as the page shows it: oldest pairing first; metadata only (never keys)."""
    out = []
    devs = st.devices()
    for did, v in sorted(devs.items(), key=lambda kv: _num(kv[1].get("paired_at")) if isinstance(kv[1], dict) else 0):
        if not isinstance(did, str) or not isinstance(v, dict):
            continue
        src = str(v.get("source", "local"))
        n = device_names(v)              # P179: who named it — the device itself at pairing, or the owner (+ the device's own label)
        out.append({"id": did, "name": clean_label(n["name"] or str(v.get("name", ""))), "paired_at": _num(v.get("paired_at")),
                    "seen": _num(v.get("seen")), "online": did in online, "me": did == me,
                    "source": "phone" if src.startswith("phone") else src if src in ("local", "account") else "local",
                    "named_by": n["named_by"], "self_name": clean_label(n["self_name"] or "")})
    return out


class Remotes:
    """Wired into serve.Host as `self.remotes`; `host` is the running serve."""

    def __init__(self, host):
        self.host = host

    # ------------------------------------------------------------ reads
    def snapshot(self, s) -> dict:
        h = self.host
        from . import remote_pair
        p = h.pairing
        mine = bool(p and getattr(p, "phone", None) == s.device)
        from .security_rotation import view
        return {"security_rotation": True, "security_stage": view(h.st)["stage"], "limit": MAX_DEVICES, "items": rows(h.st, h._online(), s.device),
                "can_add": remote_pair.enabled(h.st), "busy": bool(p) and not mine}

    async def push_list(self) -> None:
        """Every ready p33 remote gets the fresh list (after any change, from any path)."""
        h = self.host
        for s in list(h.sessions.values()):
            if s.state == "ready" and s.p33 and h.st.is_allowed(s.pub):
                await self.send_state(s)

    async def send_state(self, s) -> None:
        """Same current state on change and resume; suggest the required remote kind still missing."""
        from . import onboarding
        h = self.host
        snap = self.snapshot(s)
        kinds = {onboarding.kind_of(row['name']) for row in snap['items']}
        kind = 'computer' if 'computer' in kinds and 'phone' not in kinds else (
            'phone' if 'phone' in kinds and 'computer' not in kinds else None)
        await h.send_app(s, {"t": "devs", **snap})
        await h.send_app(s, {"t": "pair_guide", "kind": kind if kind in ('phone', 'computer') else None})

    async def changed(self) -> None:
        """All successful allowlist mutations converge here, regardless of their approval route."""
        from . import first_run
        h = self.host
        await self.push_list()
        await h._send_ready(lambda x: h.preferences_msg(x))
        try:
            snap = await asyncio.to_thread(first_run.check, h.st, ['desktop', 'phone', 'passkey', 'admin', 'exit'], h.setup_serve())
        except (first_run.SetupError, OSError):
            h.st.log('setup_refresh', status='error')  # a damaged checklist cannot cost a successful pairing
            return
        if snap.get('revision'):
            h.push_setup(snap)

    # ------------------------------------------------------------ entry
    async def on_phone(self, s, t: str, obj: dict) -> None:
        h = self.host
        r = obj.get("r")
        if not isinstance(r, str) or not 1 <= len(r) <= 32:
            return
        if t == "dev_list":
            return await h.send_app(s, {"t": "devs", "r": r, **self.snapshot(s)})
        if t == "dev_cancel":
            p = h.pairing
            if p and getattr(p, "phone", None) == s.device:
                cid = p.cid
                h._pair_finish(p, {"ev": "denied", "reason": "cancelled"})
                if cid is not None and cid in h.sessions:
                    await h.close_cid(cid, "pair_cancelled", {"ev": "denied", "reason": "cancelled"})
                h.st.log("phone_pair_cancel", device=s.device)
            return await h._ctl_res(s, r, "dev_cancel", True)
        target = self._target(t, obj)
        if target is None:
            return await h._ctl_res(s, r, t, False, why="shape")
        why = controls.check(h.st, h.nonces, h.channel, s.device, obj, t, target)
        if why:
            h.st.log("control_refused", device=s.device, action=t, reason=why)
            controls.log(h.st, action=t, device=s.device, result="refused:" + why)
            h.activity("control_refused", action=t, by=s.name or s.device, why=why)
            return await h._ctl_res(s, r, t, False, why=why)
        if t == "dev_unbind" and target["device"] == s.device and target["device"] in h.st.devices():
            await h._ctl_res(s, r, t, True, device=s.device)    # this remote unbinds itself: answer before it is detached
        try:
            signed = {"n": obj["n"], "ts": obj["ts"], "sig": obj["sig"]}   # controls.check passed: all three are there
            result, extra = await getattr(self, "_" + t)(s, target, signed)
        except OSError:
            result, extra = "io", {}
        except ValueError:
            result, extra = "bad_name", {}
        h.st.log("control", device=s.device, action=t, result=result)
        controls.log(h.st, action=t, device=s.device, result=result, obj=target, sig=obj.get("sig"), n=obj.get("n"),
                     ts=obj.get("ts"))
        if self.host.sessions.get(s.cid) is s:     # an unbind of this very remote detached it: nothing more to say
            await h._ctl_res(s, r, t, result == "ok", **({} if result == "ok" else {"why": result}), **extra)
        if result == "ok" and t == "dev_name":
            await self.changed()

    @staticmethod
    def _target(t: str, obj: dict) -> dict | None:
        dev = obj.get("device")
        if t == "dev_add":
            rv = obj.get("rv")
            if rv is None:
                return {}
            return {"rv": rv} if isinstance(rv, str) and _RV.fullmatch(rv) else None
        if not isinstance(dev, str) or not 1 <= len(dev) <= 64:
            return None
        if t == "dev_unbind":
            return {"device": dev}
        if t == "dev_name":
            name = obj.get("name")
            return {"device": dev, "name": name} if isinstance(name, str) and len(name) <= 200 else None
        code = obj.get("code")
        if not isinstance(code, str) or len(code) != _CODE_LEN or not code.isdigit() or not code.isascii():
            return None
        return {"device": dev, "code": code}

    # ------------------------------------------------------------ writes
    async def _dev_name(self, s, target: dict, signed: dict):
        h = self.host
        if name_problem(target["name"]):
            return "bad_name", {}
        name = clean_label(target["name"].strip())[:NAME_MAX]
        if not await asyncio.to_thread(h.st.rename_device, target["device"], name, "phone"):   # P179: owner_name via a remote
            return "gone", {}
        for x in h.sessions.values():
            if x.device == target["device"]:
                x.name = name
        h.reporter.trigger("online")
        h.activity("decision", by=s.name or s.device, result="allow", tool=pick(h.lang, "遥控器改名", "Remote renamed"), text=name)
        return "ok", {}

    async def _dev_unbind(self, s, target: dict, signed: dict):
        h = self.host
        did = target["device"]
        devs = h.st.devices()
        if did not in devs:
            return "gone", {}
        name = clean_label(str((devs.get(did) or {}).get("name", ""))) or did
        by = s.name or s.device
        self._record(s, "dev_unbind", did, did, target, signed)
        h.activity("decision", by=by, result="allow", tool=pick(h.lang, "解绑遥控器", "Remote unbound"), text=name)
        removed, _ = await h.revoke(did)
        if not removed:
            return "gone", {}
        when = time.strftime("%Y-%m-%d %H:%M")
        await h.broadcast(pick(h.lang, f"遥控器「{by}」解绑了遥控器：{name} · {when}",
                               f"Remote “{by}” unbound a remote: {name} · {when}"), frm="notice")
        h.push_notify("security")
        return "ok", {"device": did}

    async def _dev_add(self, s, target: dict, signed: dict):
        h = self.host
        from . import remote_pair
        from .serve import PAIR_TTL, Pairing
        if not remote_pair.enabled(h.st):
            return "disabled", {}
        if not h.relay_up:
            return "offline", {}
        old = h.pairing
        if old and getattr(old, "phone", None) != s.device:
            return "busy", {}
        if old:                                   # this remote asked again: the previous link is void
            cid = old.cid
            h._pair_finish(old, {"ev": "replaced"})
            if cid is not None and cid in h.sessions:
                await h.close_cid(cid, "pair_replaced")
        p = Pairing(secrets.token_bytes(16), secrets.token_bytes(32), time.time() + PAIR_TTL, time.monotonic() + PAIR_TTL, None)
        p.phone = s.device
        p.rv = target.get("rv")
        h.pairing = p
        p.timer = asyncio.create_task(h._expire_pairing(p))
        link = wire.pairing_link(h.cfg["web"], h.cfg["relay"], h.channel, h.kp.pub, p.pid, p.psk, int(p.expires))
        full = len(h.st.devices()) >= MAX_DEVICES
        if p.rv:                                  # the remote seals the link to the scanned code: no QR to draw
            h.st.log("phone_pair_begin", device=s.device, kind="rv", id=hashlib.sha256(p.rv.encode()).hexdigest()[:16])
            return "ok", {"link": link, "expires": int(p.expires), "ttl": PAIR_TTL, "full": full, "rv": True}
        qr = wire.pairing_qr(link).png_data_uri(scale=6, border=2, dark="#141414", light="#faf9f5")
        h.st.log("phone_pair_begin", device=s.device)
        return "ok", {"link": link, "qr": qr, "expires": int(p.expires), "ttl": PAIR_TTL, "full": full}

    async def _dev_confirm(self, s, target: dict, signed: dict):
        import hmac
        h = self.host
        p = h.pairing
        if not p or getattr(p, "phone", None) != s.device:
            return "gone", {}
        pend = h.sessions.get(p.cid) if p.cid is not None else None
        if not pend or pend.state != "pending" or pend.device != target["device"]:
            return "gone", {}
        if time.monotonic() > pend.deadline:
            res = {"ev": "denied", "reason": "timeout"}
            await h.close_cid(pend.cid, "approve_timeout", res)
            h._pair_finish(p, res)
            return "timeout", {}
        ok = hmac.compare_digest(target["code"].encode(), wire.safety_code(pend.h).encode())
        if not ok:                                 # one try, as at the terminal
            res = {"ev": "denied", "reason": "code_mismatch"}
            h._set_timer(pend, None)
            h.st.log("pair_denied", cid=pend.cid, device=pend.device, code_ok=False, source="phone")
            await h.close_cid(pend.cid, "code_mismatch", res)
            h._pair_finish(p, res)
            h.emit("denied", device=pend.device, name=pend.name)
            return "code_mismatch", {}
        res = await h._grant_pair(pend, source="phone:" + s.device)
        h._pair_finish(p, res)
        if res.get("ev") != "approved":
            return res.get("reason", "denied"), {}
        by = s.name or s.device
        self._record(s, "dev_confirm", pend.device, pend.device + "\n" + hashlib.sha256(pend.h).hexdigest()[:32], target, signed)
        rv = bool(getattr(p, "rv", None))
        h.activity("decision", by=by, result="allow", tool=pick(h.lang, "扫码添加遥控器" if rv else "添加遥控器",
                                                                "Remote added by scan" if rv else "Remote added"), text=pend.name)
        when = time.strftime("%Y-%m-%d %H:%M")
        how = pick(h.lang, "扫码", "by scan") if rv else ""
        await h.broadcast(pick(h.lang, f"经遥控器「{by}」{how}添加遥控器：{pend.name} · {when} · {pend.device}",
                               f"Remote added {how + ' ' if how else ''}from “{by}”: {pend.name} · {when} · {pend.device}"),
                          frm="notice")
        h.push_notify("security")
        extra = {"device": pend.device, "name": pend.name}
        for k in ("evicted", "replaced"):
            if res.get(k):
                extra[k] = {"id": res[k]["id"], "name": clean_label(str(res[k].get("name", "")))}
        return "ok", extra

    def _record(self, s, tool: str, device: str, text: str, target: dict, signed: dict) -> None:
        """approvals.log line for an add / unbind. P242: it carries the control's own signature (+ nonce, ts, object hash)
        and the signer's key, so `agentj approvals --verify` re-checks it (before, the line had none and verified as ✗)."""
        digest = hashlib.sha256(text.encode()).hexdigest()
        st = self.host.st
        try:
            approvals.record(st, rid=secrets.token_hex(8), agent="phone", tool=tool, input_sha256=digest,
                             shown_sha256=digest, decision="allow", reason="device", device=s.device,
                             sign_pub=st.sign_key(s.device), sig=wire.unb64u(signed["sig"]),
                             control={"n": signed["n"], "sig_ts": signed["ts"], "object_sha256": controls.object_digest(tool, target)})
        except Exception:  # noqa: BLE001 — the log line never costs the action (controls.log already has it)
            self.host.st.log("approvals_log", status="failed")

    # ------------------------------------------------------------ pairing events for the asking remote
    async def pending(self, p, s) -> None:
        """serve: the new device reached `hello` on a pairing this module made → the asking remote shows the code field."""
        did = getattr(p, "phone", None)
        if not did:
            return
        ev = self.host._pending_event(s)
        msg = {"t": "dev_pending", "device": s.device, "name": s.name, "deadline_in": ev.get("deadline_in", 0),
               "limit": MAX_DEVICES}
        if getattr(p, "rv", None) and getattr(s, "h", None):   # §27: the remote shows the same code as the new device
            msg["rv"] = p.rv                                    # … for THIS code (a late event of an earlier one is ignored)
            msg["sas"] = wire.safety_code(s.h)
        for k in ("evict", "replaces"):
            if ev.get(k):
                msg[k] = {"id": ev[k]["id"], "name": clean_label(str(ev[k].get("name", "")))}
        await self._to(did, msg)

    def finished(self, p, result: dict) -> None:
        """serve._pair_finish: tell the asking remote how its pairing ended (approved / denied / expired / replaced)."""
        did = getattr(p, "phone", None)
        if not did or getattr(p, "told", False):    # _pair_finish may run twice for one pairing (close + finish): tell once
            return
        p.told = True
        msg = {"t": "dev_pair_end", "ev": str(result.get("ev", "denied"))[:16]}
        if result.get("reason"):
            msg["reason"] = str(result["reason"])[:32]
        if getattr(p, "rv", None):
            msg["rv"] = p.rv
        self.host._bg(self._to(did, msg), "dev_pair_end")

    async def _to(self, did: str, msg: dict) -> None:
        h = self.host
        for x in list(h.sessions.values()):
            if x.device == did and x.state == "ready" and x.p33 and h.st.is_allowed(x.pub):
                await h.send_app(x, msg)
