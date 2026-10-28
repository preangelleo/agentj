"""P414 (ADR-P414-bang #135, PROTOCOL §37.11): `vault_share` — a remote without a wrap asks to use the stored computer
password; another remote that holds a `prf` wrap approves once with Face ID.

Mixed into terminal.Desk. Two steps like `term_open` (host nonce, signed control-v1 action `vault_share` object {} = "share",
passkey assertion, the requester's PRF over a fresh salt sealed kind `vault_share`), then ONE §11 card of kind `vshare` to every
OTHER remote holding a `prf` wrap (elevate `only` = that list). M4: the card shows — and its digest covers — the requester's id,
its passkey credential id, its pairing date and the flags `web` (P122 web version) / `account` (added on the account page,
P78); refused for a temporary (friend) entry and a web remote whose terminal entry lacks `web`; only a `prf` wrap approves.
The approving remote's PRF (over ITS wrap's salt, M3-rotated with `vn`) unwraps K; serve wraps K for the requester; both
PRFs and K are wiped. Deny / 120 s / e-stop → nothing stored. controls.log `vshare` with both ids; a notice on every remote.
"""
from __future__ import annotations

import asyncio
import contextlib
import re
import secrets

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from . import controls, passkey, term_store as store, term_vault as vault, wire

SHARE_PENDING_MS = 150000
_HEX32 = re.compile(r"[0-9a-f]{32}")
TARGET = {}


class VshareDesk:
    def _vshare_init(self) -> None:
        self.shares: dict[str, dict] = {}   # host nonce → step-1 record
        self.sharing: str | None = None     # the requesting device whose card is out (one at a time)

    def _vshare_refusal(self, s, busy: bool = True) -> str | None:
        if self.h.stopped():
            return "estop"
        if not self.st.passkey_of_device(s.device):
            return "no_passkey"
        e = store.entry(store.read_config(self.st), s.device)
        if e and "until" in e:
            return "temporary"
        if store.is_web(self.st, s.device) and not (e and e["web"]):
            return "web_not_allowed"
        if vault.has_prf(self.st, s.device):
            return "has"
        if not vault.any_vault(self.st):
            return "no_vault"
        if not vault.prf_peers(self.st, s.device):
            return "no_peer"
        if busy and self.sharing:
            return "busy"
        return None

    async def on_vshare(self, s, obj: dict) -> None:
        r = obj.get("r")
        if not wire.is_rid(r):
            return
        why = await asyncio.to_thread(self._vshare_refusal, s)
        if why:
            self._log("vault_share", s.device, "refused:" + why)
            return await self._res(s, r, "vault_share", False, why=why)
        pk = self.st.passkey_of_device(s.device)
        digest = controls.object_digest("vault_share", TARGET)
        if "n" not in obj and "fa" not in obj:                       # step 1: the host issues the nonce
            now = self.clock()
            self.shares = {k: v for k, v in self.shares.items() if now - v["at"] < 120}
            if len(self.shares) > 16:
                return await self._res(s, r, "vault_share", False, why="busy")
            n, salt, priv = secrets.token_hex(16), secrets.token_bytes(32), X25519PrivateKey.generate()
            from .elevate import _raw_pub
            self.shares[n] = {"device": s.device, "r": r, "at": now, "salt": salt, "priv": priv}
            ch = passkey.elevate_challenge(self.h.channel, s.device, n, "vault_share", n, digest)
            return await self._res(s, r, "vault_share", False, why="passkey_required", n=n, fa=pk["id"], fa_s=passkey.pk_state(pk), fa_ch=wire.b64u(ch),
                                   salt=wire.b64u(salt), epk=wire.b64u(_raw_pub(priv)))
        n = obj.get("n")
        pend = self.shares.pop(n, None) if isinstance(n, str) else None
        if pend is None or pend["device"] != s.device:
            why = "replay" if isinstance(n, str) and _HEX32.fullmatch(n) else "shape"
        elif self.clock() - pend["at"] > 120:
            why = "stale"
        else:
            why = controls.check(self.st, self.h.nonces, self.h.channel, s.device, obj, "vault_share", TARGET)
        if not why:
            try:
                f = passkey.approval_fields(obj.get("fa"))
                if f["id"] != pk["id"]:
                    raise passkey.PasskeyError("bad", "credential")
                ch = passkey.elevate_challenge(self.h.channel, s.device, n, "vault_share", n, digest)
                self.st.passkey_count(s.device, pk["id"], passkey.verify_assertion(pk, f, ch))
            except (passkey.PasskeyError, ValueError):
                why = "passkey_failed"
        prf = None
        if not why:
            from .elevate import open_sealed, seal_aad
            try:
                prf = open_sealed(pend["priv"], wire.unb64u(obj.get("pepk")), wire.unb64u(obj.get("pct")),
                                  seal_aad(self.h.channel, s.device, n, "vault_share", n, digest))
                if len(prf) < 16:
                    raise ValueError("short")
            except (ValueError, TypeError):
                why = "shape"
        if not why:
            why = await asyncio.to_thread(self._vshare_refusal, s)
        if not why and self.sharing:                  # P414 (R2-N7): re-checked and claimed with no await in between —
            why = "busy"                               # two step-2s racing through the threaded check cannot both start
        if not why:
            self.sharing = s.device
        if why:
            from .elevate import wipe
            wipe(prf)
            self.st.log("control_refused", device=s.device, action="vault_share", reason=why)
            self._log("vault_share", s.device, "refused:" + why, TARGET, sig=obj.get("sig"), n=n, ts=obj.get("ts"))
            return await self._res(s, r, "vault_share", False, why=why)
        self.h._bg(self._vshare(s, r, pend, prf, pk), "vault_share")   # its finally releases `sharing`
        self._log("vault_share", s.device, "start", TARGET, sig=obj.get("sig"), n=n, ts=obj.get("ts"))
        with contextlib.suppress(Exception):
            await self.h.send_app(s, {"t": "ctl_pending", "r": r, "action": "vault_share", "timeout_ms": SHARE_PENDING_MS})

    def _vshare_flags(self, device: str) -> list[str]:
        rec = self.st.devices().get(device) or {}
        flags = []
        if store.is_web(self.st, device):
            flags.append("web")
        if isinstance(rec, dict) and str(rec.get("source", "")) == "account":
            flags.append("account")
        return flags

    async def _vshare(self, s, r, pend, prf, pk) -> None:
        why, by = "failed", {}
        st, channel, me, salt = self.st, self.h.channel, s.device, pend["salt"]
        try:
            await asyncio.sleep(0)
            rec = st.devices().get(me) or {}
            pa = rec.get("paired_at") if isinstance(rec, dict) else 0
            name = self.h._dname(me) or me

            def sink(value: bytearray, approver: str, vn: bytes | None) -> dict:
                late = self._vshare_refusal(s, busy=False)    # P414 (R2-N7): the requester re-checked at approval time
                if late:                                      # (made temporary / web-disallowed / e-stop since the ask)
                    return {"result": "failed", "why": late}
                apk = st.passkey_of_device(approver)
                if not apk or not st.passkey_of_device(me) or st.passkey_of_device(me)["id"] != pk["id"]:
                    return {"result": "failed", "why": "passkey"}
                ok = vault.share(st, channel, approver, apk["id"], value, vn, me, pk["id"], salt, prf)
                if ok:
                    by["device"] = approver
                return {"result": "saved"} if ok else {"result": "failed", "why": "vault"}

            peers = await asyncio.to_thread(vault.prf_peers, st, me)
            if not peers:
                why = "failed"
            else:
                card = {"kind": "vshare", "dev": me, "name": name, "cred": pk["id"],
                        "paired": int(pa) if isinstance(pa, (int, float)) and not isinstance(pa, bool) else 0,
                        "flags": self._vshare_flags(me), "only": peers, "sink": sink}
                res = await self.h.elevate.request(card)
                rs = res.get("result")
                why = None if rs == "saved" else {"denied": "denied", "timeout": "timeout", "stopped": "estop"}.get(
                    rs, "estop" if self.h.stopped() else "failed")
        except Exception as ex:  # noqa: BLE001 — never a detail on the wire
            from . import bg
            bg.swallowed(self.st.log, "vault_share_failed", ex)
            why = why or "failed"
        finally:
            self.sharing = None
            from .elevate import wipe
            wipe(prf)
        self._log("vshare", me, "ok" if not why else "refused:" + why, extra={"by": by.get("device", "")})
        if not why:
            self._notice(f"已允许 {self.h._dname(me) or me} 使用记住的电脑密码",
                         f"{self.h._dname(me) or me} may now use the remembered computer password")
            self._prefs()
        m = {"t": "ctl_res", "r": r, "action": "vault_share", "ok": not why, **({"why": why} if why else {})}
        if not await self.h.send_app(s, m):
            await self.h._send_device(s.device, m)
