
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import pathlib
import re
import secrets
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from . import owner_grant as og
from . import bg, wire, passkey
from .text import pick

TYPES = ("otg_put", "sx_req", "sx_give", "sx_offer", "sx_rk", "sx_put", "sx_ack", "sx_err", "roster", "roster_ack", "otg_revoke_ack", "otg_more", "otg_more_res")
SEAL_INFO = b"agentj-secret-xfer-v1"
WAIT = 120                       # s a transfer waits for the other host
RATE_N, RATE_WINDOW, RATE_DAY = 5, 600, 20
MAX_OPEN = 3
ROSTER_RESEND = 600              # s before an unacknowledged roster is sent again
CODES = ("not_same_owner", "bad_proof", "unknown_cred", "expired", "replay", "not_found", "rate", "estop", "shape",
         "dest", "busy", "old_peer", "no_roster", "secret_policy", "cooldown")
_R = re.compile(r"[0-9a-f]{32}")


# ---------------------------------------------------------------- crypto
def _salt(tid: str, i: int) -> bytes:
    return hashlib.sha256(f"{tid}\n{i}".encode()).digest()


def aad(otg: dict, i: int) -> bytes:
    s = otg["steps"][i]
    return "\n".join((otg["tid"], str(i), s["name"], s["from"], s["to"])).encode()


def _key(priv: X25519PrivateKey, pub: bytes, tid: str, i: int) -> bytes:
    shared = priv.exchange(X25519PublicKey.from_public_bytes(pub))
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=_salt(tid, i), info=SEAL_INFO).derive(shared)


def seal(rk_pub: bytes, value: bytes, otg: dict, i: int) -> tuple[bytes, bytes, bytes]:
    ek = X25519PrivateKey.generate()
    nonce = os.urandom(12)
    ct = AESGCM(_key(ek, rk_pub, otg["tid"], i)).encrypt(nonce, bytes(value), aad(otg, i))
    return ek.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw), nonce, ct


def open_sealed(rk: X25519PrivateKey, epk: bytes, nonce: bytes, ct: bytes, otg: dict, i: int) -> bytearray:
    if not 16 < len(ct) <= og.MAX_SECRET_LEN + 16:
        raise ValueError("ciphertext size")
    return bytearray(AESGCM(_key(rk, epk, otg["tid"], i)).decrypt(nonce, ct, aad(otg, i)))


def raw_pub(k: X25519PrivateKey) -> bytes:
    return k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


# ---------------------------------------------------------------- the value on the holder
def read_value(spec: dict) -> bytearray | None:

    path = pathlib.Path(spec["path"])
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_uid != os.getuid() or path.stat().st_size > 1 << 20:
            return None
        if spec["mode"] != "env":
            data = path.read_bytes()
            return bytearray(data) if data and len(data) <= og.MAX_SECRET_LEN else None
        pat = re.compile(r"^\s*(?:export\s+)?" + re.escape(spec["key"]) + r"\s*=(.*)$")
        val = None
        with open(path, encoding="utf-8") as f:
            for ln in f:
                m = pat.match(ln.rstrip("\r\n"))
                if m:
                    val = m.group(1).strip()
        if not val:
            return None
        if len(val) >= 2 and val[0] == val[-1] == '"':
            val = re.sub(r'\\([\\"$`])', r"\1", val[1:-1])
        elif len(val) >= 2 and val[0] == val[-1] == "'":
            val = val[1:-1]
        return bytearray(val.encode()) if val and len(val) <= og.MAX_SECRET_LEN else None
    except (OSError, UnicodeDecodeError, KeyError):
        return None


def registry_dest(st, name: str) -> dict | None:

    from .elevate import read_results
    rows = [r for r in read_results(st).values() if r.get("name") == name and r.get("result") == "saved"
            and isinstance(r.get("where"), dict) and r["where"].get("path")]
    if not rows:
        return None
    w = max(rows, key=lambda r: r.get("at", 0))["where"]
    return {"mode": w.get("mode"), "path": w.get("path"), "key": w.get("key") or name}


def canonical(spec: dict) -> str:
    return f"env:{spec['path']}#{spec['key']}" if spec["mode"] == "env" else f"file:{spec['path']}"


class Xfer:
    def __init__(self, svc):
        self.svc = svc
        from .owner_broker import Broker
        self.broker = Broker(self)
        self.waiters: dict[str, tuple[str, asyncio.Future]] = {}
        self.pending: dict[str, dict] = {}          # push, receiver side: r → {fid, otg, i, rk, spec, exp}
        self.hits: dict[str, list[float]] = {}
        self.sent: dict[str, tuple[str, float]] = {}    # friend → (roster hash, when) last sent

    # ------------------------------------------------------------ plumbing
    @property
    def st(self):
        return self.svc.st

    @property
    def me(self) -> str:
        return self.svc.keys.id

    def zh(self) -> bool:
        return self.svc.lang == "zh"

    def _rate(self, fid: str) -> bool:
        now = time.time()
        with self.svc.store._edit("xfer-rate.json", {}) as d:
            h = [t for t in d.get(fid, []) if isinstance(t, (int, float)) and 0 <= now - t < 86400]
            allowed = len(h) < RATE_DAY and sum(now - t < RATE_WINDOW for t in h) < RATE_N
            d[fid] = h + ([now] if allowed else [])
        return allowed

    def _audit(self, otg: dict, i: int, direction: str, fid: str, result: str, **kw) -> None:
        from .elevate import audit
        s = otg["steps"][i]
        with contextlib.suppress(Exception):
            audit(self.st, None, kind="secret_xfer", id=f"{otg['tid']}:{i}", dir=direction, peer=fid, name=s["name"],
                  otg_sha256=hashlib.sha256(og.jcs(otg)).hexdigest(), result=result, **kw)
        self.svc.log("secret_xfer", dir=direction, result=result, step=i)

    def _owner_notice(self, text: str, fid=None, tid=None) -> None:
        h = self.svc.host
        if fid and tid:
            with contextlib.suppress(Exception):
                h.peer_broadcast({"t": "owner_grant_notice", "peer": fid, "grant": tid, "text": text})
        with contextlib.suppress(Exception):
            asyncio.get_running_loop()
            bg.spawn(h.broadcast(text, frm="notice"), "owner_grant_notice", self.svc.log)
        with contextlib.suppress(Exception):
            h.push_notify("security")
        with contextlib.suppress(Exception):
            h.activity("notice", text=text[:200])

    def caps_ok(self, fid: str) -> bool:
        return set(og.CAPS) <= set(self.svc.same_owner.row(fid).get("remote_caps") or [])

    # ------------------------------------------------------------ stores
    def _grants_in(self) -> dict:
        now = time.time()
        return {k: v for k, v in self.svc.store._read("grants-in.json", {}).items() if isinstance(v, dict) and v.get("exp", 0) > now}

    def local_live(self, rec: dict) -> bool:
        try:
            tid = rec["otg"]["tid"]
            if rec["otg"]["iat"] <= self.svc.store._read("owner-stop-epoch.json", {}).get("at", 0):
                return False
            if tid in self.svc.store._read("grant-tombstones.json", {}) or tid in self.svc.store._read("grant-revokes.json", {}):
                return False
            proof = rec["proof"]
            device = proof["device"]
            e = self.binding_entry(device)
            return bool(e) and bool(og.verify(rec["otg"], proof, [e]))
        except (og.GrantError, KeyError, TypeError):
            return False

    def _grants_out(self) -> dict:
        now = time.time()
        return {k: v for k, v in self.svc.store._read("grants-out.json", {}).items() if isinstance(v, dict) and v.get("exp", 0) > now and self.local_live(v)}

    def pinned(self, fid: str) -> list | None:
        r = self.svc.store._read("owner-roster.json", {}).get(fid)
        return r.get("list") if isinstance(r, dict) and isinstance(r.get("list"), list) else None

    def forget(self, fid: str, revoke: bool = True) -> None:

        if revoke:
            with self.svc.store._edit("owner-epochs.json", {}) as d:
                d[fid] = time.time() + og.SKEW
        with self.svc.store._edit("owner-roster.json", {}) as d:
            d.pop(fid, None)
        with self.svc.store._edit("grants-in.json", {}) as d:
            for k in [k for k, v in d.items() if isinstance(v, dict) and v.get("fid") == fid]:
                d.pop(k)
        # Only ending this relationship invalidates local signed plans touching it.
        # Other peers' already-delivered tasks keep their own explicit grants.
        with self.svc.store._edit("grants-out.json", {}) as d:
            for tid, rec in list(d.items()):
                if isinstance(rec, dict) and fid in og.targets(rec["otg"]):
                    d.pop(tid)

    def stop(self) -> None:

        with self.svc.store._edit("owner-stop-epoch.json", {}) as d:
            d["at"] = time.time() + og.SKEW
        for tid in list(self.svc.store._read("grants-out.json", {})):
            self._list({"revoke":tid})
        incoming = self.svc.store._read("grants-in.json", {})
        for tid, rec in incoming.items():
            self.tombstone(tid, rec["exp"], rec["fid"])
        with self.svc.store._edit("grants-in.json", {}) as d:
            d.clear()
        self.pending.clear()
        for fid, future in list(self.waiters.values()):
            if not future.done():
                future.set_result({"t":"sx_err", "code":"estop"})

    def accept_grant(self, fid: str, otg, proof) -> dict:

        if not self.svc.same_owner.active(fid):
            raise og.GrantError("not_same_owner")
        if not isinstance(otg, dict) or otg.get("origin") != fid or self.me not in og.targets(og.check(otg)):
            raise og.GrantError("shape", "not for this host")
        after = self.usable_after(fid)
        if otg["iat"] < after:
            raise og.GrantError("cooldown" if time.time() < after else "replay", f"usable_after:{after}")
        if otg["tid"] in self.svc.store._read("grant-tombstones.json", {}) or f"{fid}:{otg['tid']}" in self.svc.store._read("grant-tombstones.json", {}):
            raise og.GrantError("replay", "revoked grant")
        from . import peer_guard
        index = getattr(self.svc, "index", None) or peer_guard.SecretIndex([])
        if not peer_guard.outbound(og.card_text(otg), index, self.svc.store.never_tell_extra).ok:
            raise og.GrantError("secret_policy", "grant metadata contains a credential value")
        known = self._grants_in().get(otg["tid"])
        roster = self.pinned(fid)
        if roster is None:
            raise og.GrantError("no_roster", "this friend's remotes are not known here yet")
        entry = og.verify(otg, proof, roster)
        if known and known.get("fid") == fid:
            return known
        rec = {"otg": otg, "proof": proof, "fid": fid, "cred": hashlib.sha256(entry["id"].encode()).hexdigest()[:16], "by": entry["name"],
               "exp": otg["exp"], "used": {}, "at": int(time.time())}
        with self.svc.store._edit("grants-in.json", {}) as d:
            now = time.time()
            for k in [k for k, v in d.items() if not isinstance(v, dict) or v.get("exp", 0) <= now]:
                d.pop(k)
            if otg["tid"] in d:
                return d[otg["tid"]]
            d[otg["tid"]] = rec
        self.svc.log("owner_grant", result="accepted", steps=len(otg["steps"]))
        return rec

    def live_for(self, fid: str, tids, task: str | None = None) -> list:

        if not isinstance(tids, list) or not self.svc.same_owner.active(fid):
            return []
        have = self._grants_in()
        candidates = [t for t in tids[:4] if isinstance(t, str) and isinstance(have.get(t), dict) and have[t].get("fid") == fid
                and self.valid(have[t]) and any(s["cat"] in og.GRANT_CATS and s["host"] == self.me for s in have[t]["otg"]["steps"])]
        if not task:
            return []
        out = []
        with self.svc.store._edit("grants-in.json", {}) as d:
            for t in candidates:
                rec = d.get(t)
                if rec and rec.get("task", task) == task:
                    rec["task"] = task
                    out.append(t)
        return out

    def grant_tier(self, fid: str, tids: list, tool: str, tool_input, verdict, cwd, classify=None) -> dict | None:

        have = self._grants_in()
        for t in tids:
            rec = have.get(t)
            if isinstance(rec, dict) and not self.valid(rec):
                continue
            if not isinstance(rec, dict) or rec.get("fid") != fid or not self.svc.same_owner.active(fid):
                continue
            picks = og.tier(rec["otg"], self.me, rec.get("used") or {}, tool, tool_input, verdict, cwd, classify)
            if picks is None:
                continue
            steps = rec["otg"]["steps"]
            with self.svc.store._edit("grants-in.json", {}) as d:
                live = d.get(t)
                if not live or not self.valid(live):
                    continue
                used = live.setdefault("used", {})
                counts = {i: picks.count(i) for i in set(picks)}
                if any(int(used.get(str(i), 0)) + n > int(steps[i].get("max", 1)) for i, n in counts.items()):
                    continue
                for i, n in counts.items():
                    used[str(i)] = int(used.get(str(i), 0)) + n
            return {"tid": t, "steps": sorted(set(picks)), "by": rec.get("by"), "origin": rec["otg"]["hosts"].get(fid, fid)}
        return None

    def origin_tier(self, tool: str, tool_input, verdict, cwd, classify=None) -> dict | None:
        scope = self.task_scope()
        if not scope or self.svc.host.stopped():
            return None
        for tid, rec in self._grants_out().items():
            if rec.get("task") != scope:
                continue
            picks = og.tier(rec["otg"], self.me, rec.get("used") or {}, tool, tool_input, verdict, cwd, classify)
            if picks is None:
                continue
            with self.svc.store._edit("grants-out.json", {}) as d:
                live = d.get(tid)
                if not live or self.svc.host.stopped() or not self.local_live(live) or live.get("task") != scope:
                    continue
                used = live.setdefault("used", {})
                counts = {i:picks.count(i) for i in set(picks)}
                if any(int(used.get(str(i), 0)) + n > int(live["otg"]["steps"][i].get("max",1)) for i,n in counts.items()):
                    continue
                for i,n in counts.items():
                    used[str(i)] = int(used.get(str(i),0)) + n
            return {"tid":tid, "steps":sorted(set(picks)), "by":rec["proof"]["device"], "origin":rec["otg"]["hosts"][self.me]}
        return None

    def valid(self, rec: dict) -> bool:
        try:
            if self.svc.host.stopped() or not self.svc.same_owner.active(rec["fid"]):
                return False
            if rec["otg"]["iat"] <= max(self.svc.store._read("owner-epochs.json", {}).get(rec["fid"], 0), self.svc.store._read("owner-stop-epoch.json", {}).get("at", 0)):
                return False
            tombs = self.svc.store._read("grant-tombstones.json", {})
            if rec["otg"]["tid"] in tombs or f"{rec['fid']}:{rec['otg']['tid']}" in tombs:
                return False
            og.verify(rec["otg"], rec["proof"], self.pinned(rec["fid"]) or [])
            return True
        except (og.GrantError, KeyError):
            return False

    def tombstone(self, tid: str, exp: int, fid: str = "") -> None:
        with self.svc.store._edit("grant-tombstones.json", {}) as d:
            now = time.time()
            for k in list(d):
                if d[k] <= now:
                    d.pop(k)
            d[f"{fid}:{tid}" if fid else tid] = max(exp, int(now) + og.TTL)

    def use_step(self, tid: str, i: int, n: int = 1, cap: int = 1) -> bool:
        with self.svc.store._edit("grants-in.json", {}) as d:
            rec = d.get(tid)
            if not isinstance(rec, dict) or not self.valid(rec):
                return False
            used = rec.setdefault("used", {})
            if int(used.get(str(i), 0)) + n > cap:
                return False
            used[str(i)] = int(used.get(str(i), 0)) + n
        return True

    # ------------------------------------------------------------ incoming
    def on_app(self, fid: str, obj: dict) -> None:
        t = obj.get("t")
        if self.svc.store.is_blocked(fid) or not self.svc.same_owner.active(fid):
            if t in ("sx_req", "sx_offer") and isinstance(obj.get("r"), str):
                self.svc._send(fid, {"t": "sx_err", "r": obj["r"], "code": "not_same_owner"})
            self.svc.log("secret_xfer", result="dropped", reason="not_same_owner", kind=str(t)[:12])
            return
        if t == "otg_more":
            jobs = getattr(self, "_jobs", set())
            if len(jobs) >= MAX_OPEN:
                return self._err(fid, str(obj.get("r") or ""), "busy")
            job = asyncio.create_task(self._more(fid, obj))
            self._jobs = jobs
            jobs.add(job)
            self.svc.jobs.add(job)
            job.add_done_callback(jobs.discard)
            job.add_done_callback(self.svc.jobs.discard)
            return
        if t == "roster":
            return self._on_roster(fid, obj)
        if t == "roster_ack":
            if not isinstance(obj.get("h"), str) or not re.fullmatch(r"[0-9a-f]{16}", obj["h"]):
                return
            with self.svc.store._edit("owner-roster.json", {}) as d:
                d.setdefault("_out", {})[fid] = {"acked": obj.get("h") if isinstance(obj.get("h"), str) else None}
            return
        if t == "otg_revoke_ack":
            tid = obj.get("tid")
            if isinstance(tid, str) and _R.fullmatch(tid):
                with self.svc.store._edit("grant-revokes.json", {}) as d:
                    rec = d.get(tid)
                    if rec and fid in rec.get("targets", []):
                        rec["acks"] = list(set([*(rec.get("acks") or []), fid]))
            return
        if t == "otg_put":
            rv = obj.get("revoke")
            if isinstance(rv, str) and _R.fullmatch(rv):
                self.tombstone(rv, int(time.time()) + og.TTL, fid)
                self.svc._send(fid, {"t":"otg_revoke_ack", "tid":rv})
                # The origin withdrew its grant: narrows only.
                with self.svc.store._edit("grants-in.json", {}) as d:
                    if isinstance(d.get(rv), dict) and d[rv].get("fid") == fid:
                        d.pop(rv)
                return
            try:
                self.accept_grant(fid, obj.get("otg"), obj.get("proof"))
            except og.GrantError as e:
                self.svc.log("owner_grant", result="refused", reason=e.why)
            return
        r = obj.get("r")
        if not isinstance(r, str) or not _R.fullmatch(r):
            return
        if t in ("sx_req", "sx_offer", "sx_put"):
            live_jobs = getattr(self, "_jobs", set())
            if len(live_jobs) >= MAX_OPEN:
                return self._err(fid, r, "busy")
            job = asyncio.create_task({"sx_req": self._serve_pull, "sx_offer": self._take_offer, "sx_put": self._take_put}[t](fid, obj))
            self.svc.jobs.add(job)
            self._jobs = live_jobs
            live_jobs.add(job)
            job.add_done_callback(live_jobs.discard)
            job.add_done_callback(self.svc.jobs.discard)
            return
        w = self.waiters.get(r)
        if w and w[0] == fid and not w[1].done() and t in ("sx_give", "sx_rk", "sx_ack", "sx_err", "otg_more_res"):
            w[1].set_result(obj)

    async def _more(self, fid, obj):
        r, tid = obj.get("r"), obj.get("tid")
        if not isinstance(r, str) or not _R.fullmatch(r):
            return
        answer = {"result":"refused", "why":"owner_source"}
        rec = self._grants_out().get(tid)
        h = self.svc.host
        def authorized():
            if not rec or not self.local_live(rec) or h.stopped() or fid not in og.targets(rec["otg"]):
                return False
            scope = rec.get("task", "")
            if scope.startswith("task:"):
                row = self.svc.store._read("owner-ordinary-tasks.json", {}).get(scope[5:], {})
                return (row.get("active") and row.get("device") == rec["proof"]["device"]
                        and row.get("session") == self.task_session() and row.get("exp", 0) > time.time())
            return self.task_scope() == scope
        device = rec["proof"]["device"] if rec else None
        online = any(s.device == device and s.state == "ready" for s in h.sessions.values())
        if authorized() and online:
            with self.svc.store._edit("owner-more-seen.json", {}) as seen:
                for key in list(seen):
                    if seen[key] <= time.time():
                        seen.pop(key)
                replay = r in seen
                seen[r] = rec["exp"]
            if not replay and self._rate(fid):
                req = {k:obj.get(k) for k in ("name", "why", "dest")}
                req.update(op="give" if obj.get("op") == "ask" else "ask", peer=fid, title=rec["otg"]["title"])
                if obj.get("op") in ("ask", "give"):
                    from .owner_call import supplement
                    try:
                        with supplement(rec["task"], device, authorized):
                            answer = await self._one(req["op"], req, None)
                    except og.GrantError as exc:
                        answer = {"result":"refused", "why":exc.why}
        self.svc._send(fid, {"t":"otg_more_res", "r":r, "result":answer})

    async def more(self, req):
        from . import provenance
        source = provenance.owner_grants(self.svc.host.agent)
        if not source:
            return {"result":"refused", "why":"owner_source"}
        fid, tids = source
        peer, why = self._resolve(req.get("peer"))
        if peer != fid:
            return {"result":"refused", "why":why or "owner_source"}
        rec = next((rec for tid in tids if (rec := self._grants_in().get(tid)) and self.valid(rec)), None)
        if not rec:
            return {"result":"refused", "why":"expired"}
        r = secrets.token_hex(16)
        result = await self._wait(fid, r, {"t":"otg_more", "r":r, "tid":rec["otg"]["tid"],
            **{k:req.get(k) for k in ("op", "name", "why", "dest")}}, timeout=WAIT * 5)
        answer = result.get("result")
        if result.get("t") != "otg_more_res" or not isinstance(answer, dict):
            return {"result":"refused", "why":"timeout"}
        if answer.get("result") != "saved":
            return {"result":"refused", "why":answer.get("why") if answer.get("why") in (*CODES, "owner_source", "owner") else "shape"}
        receipt = answer.get("receipt", {})
        grant = self._grants_in().get(receipt.get("grant"), {})
        if (not self.valid(grant) or type(receipt.get("length")) is not int or not 0 <= receipt["length"] <= og.MAX_SECRET_LEN
                or not re.fullmatch(r"sha256:[0-9a-f]{8}", str(receipt.get("fingerprint")))):
            return {"result":"refused", "why":"shape"}
        return {"result":"saved", "receipt": {"name":req["name"], "dest":req.get("dest") or "(default)",
            "grant":receipt["grant"], "length":receipt["length"], "fingerprint":receipt["fingerprint"],
            "from" if req["op"] == "ask" else "to":grant["otg"]["hosts"][fid]}}

    def _err(self, fid: str, r: str, code: str) -> None:
        self.svc._send(fid, {"t": "sx_err", "r": r, "code": code if code in CODES else "shape",
                             **({"usable_after":self.usable_after(fid)} if code == "cooldown" else {})})

    def _step(self, fid: str, obj: dict, want_from: str, want_to: str) -> tuple[dict, int]:
        if self.svc.host.stopped():
            raise og.GrantError("estop")
        rec = self.accept_grant(fid, obj.get("otg"), obj.get("proof"))
        otg, i = rec["otg"], obj.get("i")
        if type(i) is not int or not 0 <= i < len(otg["steps"]):
            raise og.GrantError("shape", "step")
        s = otg["steps"][i]
        if s["cat"] != "secret" or s["from"] != want_from or s["to"] != want_to:
            raise og.GrantError("shape", "step direction")
        if not self._rate(fid):
            raise og.GrantError("rate")
        return otg, i

    async def _serve_pull(self, fid: str, obj: dict) -> None:

        from .elevate import fingerprint, wipe
        r = obj["r"]
        try:
            otg, i = self._step(fid, obj, self.me, fid)
            rk = wire.unb64u(obj.get("rk") or "")
            if len(rk) != 32:
                raise og.GrantError("shape", "rk")
            s = otg["steps"][i]
            spec = await asyncio.to_thread(self._holder_spec, s)
            value = await asyncio.to_thread(read_value, spec) if spec else None
            if value is None:
                raise og.GrantError("not_found")
            if not self.use_step(otg["tid"], i):
                wipe(value)
                raise og.GrantError("replay")
            try:
                epk, n, ct = seal(rk, value, otg, i)
                fp = fingerprint(value)
            finally:
                wipe(value)
            self.svc._send(fid, {"t": "sx_give", "r": r, "tid": otg["tid"], "i": i, "epk": wire.b64u(epk), "n": wire.b64u(n),
                                 "ct": wire.b64u(ct), "fp": fp})
            self._audit(otg, i, "out", fid, "sent", fp=fp)
            name, peer = s["name"], otg["hosts"][fid]
            self._owner_notice(pick(self.svc.lang,
                                    f"「{peer}」按你签的授权单 {otg['tid'][:8]} 取走了密钥 {name}（本机 → {peer}）。不是你发起的？"
                                    f"到好友页解除该好友，并在「设置 → 遥控器」停用发起的那台。",
                                    f"“{peer}” took key {name} (this host → {peer}) under the grant you signed ({otg['tid'][:8]}). "
                                    f"Not you? Remove this friend and disable its signed grants here."), fid, otg["tid"])
        except og.GrantError as e:
            self._err(fid, r, e.why)
            self.svc.log("secret_xfer", dir="out", result="refused", reason=e.why)
        except Exception as e:
            self._err(fid, r, "bad_proof")
            self.svc.log("secret_xfer", dir="out", result="refused", reason=type(e).__name__)

    def _holder_spec(self, s: dict) -> dict | None:

        from .elevate import Refused, parse_dest
        spec = registry_dest(self.st, s["name"])
        if spec is None:
            return None
        if s["src"]:
            try:
                requested = parse_dest(s["src"], s["name"], None, self.st.root)
                if canonical(requested) != canonical(spec):
                    return None
            except Refused:
                return None
        return spec

    async def _take_offer(self, fid: str, obj: dict) -> None:

        r = obj["r"]
        try:
            otg, i = self._step(fid, obj, fid, self.me)
            now = time.monotonic()
            self.pending = {k:v for k,v in self.pending.items() if v["exp"] > now}
            if len(self.pending) >= MAX_OPEN:
                raise og.GrantError("busy")
            s = otg["steps"][i]
            spec = await asyncio.to_thread(self._dest_spec, s)
            if spec is None:
                raise og.GrantError("dest")
            if not self.use_step(otg["tid"], i):
                raise og.GrantError("replay")
            rk = X25519PrivateKey.generate()
            self.pending[r] = {"fid": fid, "otg": otg, "i": i, "rk": rk, "spec": spec, "exp": time.monotonic() + WAIT}
            self.svc._send(fid, {"t": "sx_rk", "r": r, "rk": wire.b64u(raw_pub(rk))})
        except og.GrantError as e:
            self._err(fid, r, e.why)
            self.svc.log("secret_xfer", dir="in", result="refused", reason=e.why)

    def _dest_spec(self, s: dict) -> dict | None:
        from .elevate import Refused, parse_dest
        if s["dest"]:
            try:
                return parse_dest(s["dest"], s["name"], None, self.st.root)
            except Refused:
                return None
        return registry_dest(self.st, s["name"])

    async def _take_put(self, fid: str, obj: dict) -> None:
        r = obj["r"]
        p = self.pending.pop(r, None)
        if p is None or p["fid"] != fid or time.monotonic() > p["exp"] or not self.valid(self._grants_in().get(p["otg"]["tid"], {})):
            return self._err(fid, r, "expired")
        otg, i, s = p["otg"], p["i"], p["otg"]["steps"][p["i"]]
        try:
            value = open_sealed(p["rk"], wire.unb64u(obj.get("epk") or ""), wire.unb64u(obj.get("n") or ""),
                                wire.unb64u(obj.get("ct") or ""), otg, i)
        except Exception:  # noqa: BLE001 — a bad seal is one refusal, never a value
            return self._err(fid, r, "bad_proof")
        try:
            fp = await self._store(value, p["spec"], s["name"], otg, fid)
        except Exception as e:  # noqa: BLE001
            self._audit(otg, i, "in", fid, "failed", reason=type(e).__name__)
            return self._err(fid, r, "dest")
        self.svc._send(fid, {"t": "sx_ack", "r": r, "fp": fp})
        self._audit(otg, i, "in", fid, "stored", fp=fp, dest_sha256=hashlib.sha256(p["spec"]["path"].encode()).hexdigest())
        peer = otg["hosts"][fid]
        self._owner_notice(pick(self.svc.lang,
                                f"「{peer}」按你签的授权单 {otg['tid'][:8]} 往本机存了密钥 {s['name']}。不是你发起的？到好友页解除该好友。",
                                f"“{peer}” saved key {s['name']} on this host under the grant you signed ({otg['tid'][:8]}). "
                                "Not you? Remove this friend and disable its signed grants here."), fid, otg["tid"])

    async def _store(self, value: bytearray, spec: dict, name: str, otg: dict, fid: str) -> str:
        from .elevate import fingerprint, remember, wipe, write_secret
        try:
            def commit():
                if self.svc.host.stopped() or not self.svc.same_owner.active(fid) or time.time() >= otg["exp"]:
                    raise og.GrantError("expired")
                if otg["origin"] == self.me:
                    local = self._grants_out().get(otg["tid"])
                    if not local or not self.task_scope() or local.get("task") != self.task_scope():
                        raise og.GrantError("replay")
                elif not self.valid(self._grants_in().get(otg["tid"], {})):
                    raise og.GrantError("replay")
                write_secret(spec, value)
            await asyncio.to_thread(commit)
            fp = fingerprint(value)
            length = len(value)
        finally:
            wipe(value)
        remember(self.st, secrets.token_hex(16), {"kind": "secret", "name": name, "result": "saved", "until": None,
                                                  "via": "same_owner", "peer": fid, "grant": otg["tid"],
                                                  "receipt": {"name": name, "dest": spec.get("show") or canonical(spec),
                                                              "length": length, "fingerprint": fp, "verify": "skipped"},
                                                  "where": {k: spec.get(k) for k in ("mode", "path", "key")}})
        return fp

    def usable_after(self, fid: str | None = None) -> int:
        cutoff = self.svc.store._read("owner-stop-epoch.json", {}).get("at", 0)
        if fid:
            cutoff = max(cutoff, self.svc.store._read("owner-epochs.json", {}).get(fid, 0))
        return int(cutoff) + 1 if cutoff else 0

    def learn_cooldown(self, fid: str, answer: dict) -> dict:
        after = answer.get("usable_after")
        if answer.get("code") == "cooldown" and type(after) is int and time.time() < after <= time.time() + 2 * og.SKEW + 60:
            with self.svc.store._edit("same-owner.json", {}) as d:
                d.setdefault(fid, {})["remote_grant_after"] = after
            return {"usable_after":after}
        return {}

    # ------------------------------------------------------------ outgoing (origin)
    async def _wait(self, fid: str, r: str, obj: dict, timeout: float = WAIT) -> dict:
        fut = asyncio.get_running_loop().create_future()
        self.waiters[r] = (fid, fut)
        try:
            if not self.svc._send(fid, obj):
                return {"t": "sx_err", "code": "offline"}
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return {"t": "sx_err", "code": "timeout"}
        finally:
            self.waiters.pop(r, None)

    async def pull(self, fid: str, rec: dict, i: int, spec: dict) -> dict:

        otg = rec["otg"]
        scope = self.task_scope()
        rk = X25519PrivateKey.generate()
        r = secrets.token_hex(16)
        ans = await self._wait(fid, r, {"t": "sx_req", "r": r, "otg": otg, "proof": rec["proof"], "i": i,
                                        "rk": wire.b64u(raw_pub(rk))})
        if ans.get("t") != "sx_give" or ans.get("tid") != otg["tid"] or ans.get("i") != i:
            self._audit(otg, i, "in", fid, "refused", reason=str(ans.get("code"))[:24])
            return {"result": "refused", "why": str(ans.get("code") or "shape")[:24], **self.learn_cooldown(fid, ans)}
        try:
            if self.task_scope() != scope or not scope or self.svc.host.stopped() or not self.svc.same_owner.active(fid) or otg["tid"] not in self._grants_out() or time.time() >= otg["exp"]:
                return {"result": "refused", "why": "expired"}
            value = open_sealed(rk, wire.unb64u(ans.get("epk") or ""), wire.unb64u(ans.get("n") or ""),
                                wire.unb64u(ans.get("ct") or ""), otg, i)
        except Exception:  # noqa: BLE001
            self._audit(otg, i, "in", fid, "refused", reason="seal")
            return {"result": "refused", "why": "bad_seal"}
        length = len(value)
        try:
            fp = await self._store(value, spec, otg["steps"][i]["name"], otg, fid)
        except Exception as e:  # noqa: BLE001
            self._audit(otg, i, "in", fid, "failed", reason=type(e).__name__)
            return {"result": "refused", "why": "dest", "detail": str(getattr(e, "detail", "")) or type(e).__name__}
        self.svc._send(fid, {"t": "sx_ack", "r": r, "fp": fp})
        self._audit(otg, i, "in", fid, "stored", fp=fp, dest_sha256=hashlib.sha256(spec["path"].encode()).hexdigest())
        self._mark_out(otg["tid"], i)
        return {"result": "saved", "receipt": {"name": otg["steps"][i]["name"], "dest": spec.get("show") or canonical(spec),
                                               "length": length, "fingerprint": fp, "from": otg["hosts"][fid], "grant": otg["tid"]}}

    async def push(self, fid: str, rec: dict, i: int, spec: dict) -> dict:

        otg = rec["otg"]
        scope = self.task_scope()
        value = await asyncio.to_thread(read_value, spec)
        if value is None:
            return {"result": "refused", "why": "not_found"}
        try:
            r = secrets.token_hex(16)
            ans = await self._wait(fid, r, {"t": "sx_offer", "r": r, "otg": otg, "proof": rec["proof"], "i": i})
            if ans.get("t") != "sx_rk":
                self._audit(otg, i, "out", fid, "refused", reason=str(ans.get("code"))[:24])
                return {"result": "refused", "why": str(ans.get("code") or "shape")[:24], **self.learn_cooldown(fid, ans)}
            if self.task_scope() != scope or not scope or self.svc.host.stopped() or not self.svc.same_owner.active(fid) or otg["tid"] not in self._grants_out() or time.time() >= otg["exp"]:
                return {"result": "refused", "why": "expired"}
            rk = wire.unb64u(ans.get("rk") or "")
            if len(rk) != 32:
                return {"result": "refused", "why": "shape"}
            epk, n, ct = seal(rk, value, otg, i)
            from .elevate import fingerprint
            fp, length = fingerprint(value), len(value)
        finally:
            from .elevate import wipe
            wipe(value)
        ans = await self._wait(fid, r, {"t": "sx_put", "r": r, "epk": wire.b64u(epk), "n": wire.b64u(n), "ct": wire.b64u(ct)})
        if ans.get("t") != "sx_ack" or ans.get("fp") != fp:
            self._audit(otg, i, "out", fid, "refused", reason=str(ans.get("code"))[:24])
            return {"result": "refused", "why": str(ans.get("code") or "unconfirmed")[:24]}
        self._audit(otg, i, "out", fid, "sent", fp=fp)
        self._mark_out(otg["tid"], i)
        return {"result": "saved", "receipt": {"name": otg["steps"][i]["name"], "dest": otg["steps"][i]["dest"] or "(default)",
                                               "length": length, "fingerprint": fp, "to": otg["hosts"][fid], "grant": otg["tid"]}}

    def _mark_out(self, tid: str, i: int) -> None:
        with self.svc.store._edit("grants-out.json", {}) as d:
            if isinstance(d.get(tid), dict):
                d[tid].setdefault("used", {})[str(i)] = 1

    # ------------------------------------------------------------ roster
    def my_roster(self) -> list:
        proofs = self.svc.store._read("roster-via.json", {})
        out = []
        for did, rec in self.st.devices().items():
            e = og.roster_entry(did, rec)
            if e:
                if isinstance(proofs.get(e["id"]), dict):
                    v = proofs[e["id"]]
                    e["via"] = {k: x for k, x in v.items() if k != "bind"}
                    e["bind"] = v.get("bind", "")
                e["sign_pub"] = wire.b64u(self.st.sign_key(did) or b"")
                if og.binding_ok(e):
                    out.append(e)
        return out[:og.ROSTER_MAX]

    def note_via(self, cred_id: str, via: dict) -> None:

        with self.svc.store._edit("roster-via.json", {}) as d:
            d[cred_id] = {**via, **({"bind": d[cred_id]["bind"]} if cred_id in d and "bind" in d[cred_id] and "bind" not in via else {})}
            for k in list(d)[:-og.ROSTER_MAX * 2]:
                d.pop(k)
        self.roster_tick()

    def _on_roster(self, fid: str, obj: dict) -> None:
        sent = obj.get("list")
        if not isinstance(sent, list):
            return
        caps = [c for c in obj.get("caps") or [] if isinstance(c, str) and len(c) <= 16][:8] if isinstance(obj.get("caps"), list) else []
        after = obj.get("grant_after", 0)
        after = after if type(after) is int and 0 <= after <= time.time() + 2 * og.SKEW + 60 else 0
        with self.svc.store._edit("same-owner.json", {}) as d:
            if isinstance(d.get(fid), dict):
                d[fid]["remote_caps"] = caps
                d[fid]["remote_grant_after"] = after
        old = self.pinned(fid)
        previous = self.svc.store._read("owner-roster.json", {}).get(fid, {})
        retired = set(previous.get("retired") or [])
        sent = [e for e in sent if isinstance(e, dict) and e.get("id") not in retired]
        if old is None:
            ceremony = self.svc.same_owner.row(fid)
            if ceremony.get("roster_v") != 3 or ceremony.get("remote_roster_v") != 3:
                return
            pinned = [e for e in ceremony.get("remote_roster", []) if e.get("sign_pub") == ceremony.get("remote_pub") and og.binding_ok(e)]
            if len(pinned) != 1:
                return
            with self.svc.store._edit("owner-roster.json", {}) as d:
                d[fid] = {"list": pinned, "at": int(time.time())}
            old = pinned
        new, added, removed = og.merge_roster(old, sent)
        if old is not None and not added and not removed:
            h = hashlib.sha256(og.jcs({"list":sent,"grant_after":after})).hexdigest()[:16]
            self.svc._send(fid, {"t": "roster_ack", "h": h})
            return
        retired.update(e["id"] for e in old if not any(n["id"] == e["id"] for n in new))
        with self.svc.store._edit("owner-roster.json", {}) as d:
            d[fid] = {"list": new, "at": int(time.time()), "retired":sorted(retired)}
        self.svc._send(fid, {"t": "roster_ack", "h": hashlib.sha256(og.jcs({"list":sent,"grant_after":after})).hexdigest()[:16]})
        name = self.svc.store.name_of(fid)
        self.svc.log("owner_roster", result="pinned" if old is None else "changed", n=len(new), added=len(added), removed=len(removed))
        if old is None and new:
            self._owner_notice(pick(self.svc.lang,
                                    f"已记下「{name}」的 {len(new)} 台遥控器（{('、'.join(e['name'] for e in new))[:120]}）：以后它们签的同主人任务授权本机直接认。不认识其中某台？到好友页解除该好友。",
                                    f"Noted {len(new)} remote(s) of “{name}” ({(', '.join(e['name'] for e in new))[:120]}): grants they sign "
                                    "are honoured here. Don't recognise one? Remove this friend on the friends page."))
        elif added:
            self._owner_notice(pick(self.svc.lang, f"「{name}」新增可签授权的遥控器：{'、'.join(added)[:120]}（由它已认可的遥控器 Face ID 批准）。",
                                    f"“{name}” added a remote that may sign grants: {', '.join(added)[:120]} (approved by Face ID on a known one)."))

    def _send_revoke(self, fid: str, tid: str) -> None:
        net = getattr(self.svc, "net", None)
        if net is not None and any(r.get("peer") == fid and r.get("mid") == tid and r.get("t") == "otg_put"
                                   and r.get("state") == "pending" for r in net.outbox_rows()):
            return
        sent = getattr(self, "revoke_sent", {})
        self.revoke_sent = sent
        key, now = (fid,tid), time.monotonic()
        if now - sent.get(key, -1000) < 30:
            return
        if self.svc._send(fid, {"t":"otg_put", "mid":tid, "otg":None, "proof":None, "revoke":tid}):
            sent[key] = now

    def roster_tick(self) -> None:

        revokes = self.svc.store._read("grant-revokes.json", {})
        for tid, rec in revokes.items():
            if rec.get("exp", 0) <= time.time():
                continue
            for fid in rec.get("targets", []):
                if fid not in rec.get("acks", []) and self.svc.same_owner.active(fid):
                    self._send_revoke(fid, tid)
        mine = self.my_roster()
        out = self.svc.store._read("owner-roster.json", {}).get("_out", {})
        now = time.monotonic()
        for fid in list(self.svc.store._read("same-owner.json", {})):
            after = self.usable_after(fid)
            h = hashlib.sha256(og.jcs({"list":mine,"grant_after":after})).hexdigest()[:16]
            if not self.svc.same_owner.active(fid) or (out.get(fid) or {}).get("acked") == h:
                continue
            last = self.sent.get(fid)
            if last and last[0] == h and now - last[1] < ROSTER_RESEND:
                continue
            if self.svc._send(fid, {"t": "roster", "list": mine, "caps": og.CAPS, "grant_after":after}):
                self.sent[fid] = (h, now)

    def binding_entry(self, device: str) -> dict | None:
        rec = self.st.devices().get(device)
        e = og.roster_entry(device, rec)
        if e:
            e["sign_pub"] = wire.b64u(self.st.sign_key(device) or b"")
        return e

    def bound(self, device: str) -> bool:
        return any(e.get("dev") == device for e in self.my_roster())

    async def bind_phone(self, session, obj: dict) -> None:
        h = self.svc.host
        entry = self.binding_entry(session.device)
        if entry is None:
            return await h.send_app(session, {"t": "roster_bind_res", "ok": False, "why": "no_passkey"})
        if obj.get("t") == "roster_bind_get":
            return await h.send_app(session, {"t": "roster_bind_offer", "entry": entry})
        try:
            f = passkey.approval_fields(obj.get("fa"))
            if f["id"] != entry["id"]:
                raise ValueError()
            passkey.verify_assertion(entry, f, og.bind_challenge(entry))
            entry["bind"] = obj.get("roster_sig")
            if not og.binding_ok(entry):
                raise ValueError()
        except (ValueError, TypeError, passkey.PasskeyError):
            return await h.send_app(session, {"t": "roster_bind_res", "ok": False, "why": "bad_proof"})
        with self.svc.store._edit("roster-via.json", {}) as d:
            d.setdefault(entry["id"], {})["bind"] = entry["bind"]
        # The old ceremony's list did not include this binding. Never silently reuse its consent.
        for fid in list(self.svc.store._read("same-owner.json", {})):
            if self.svc.same_owner.row(fid).get("nonce"):
                self.svc.same_owner.consent(fid, False, end="ended")
        self.roster_tick()
        return await h.send_app(session, {"t": "roster_bind_res", "ok": True})

    # ------------------------------------------------------------ the CLI (elevate.sock {"t": "sx", "op": …})
    def _resolve(self, ref) -> tuple[str | None, str | None]:
        fid, why = self.svc._resolve(ref)
        if not fid:
            return None, why
        if not self.svc.same_owner.active(fid):
            return None, "not_same_owner"
        after = max(self.usable_after(fid), self.svc.same_owner.row(fid).get("remote_grant_after", 0))
        if after > time.time():
            raise og.GrantError("cooldown", f"usable_after:{after}")
        if not self.caps_ok(fid):
            return None, "old_peer"
        if self.pinned(fid) is None:
            return None, "no_roster"
        return fid, None

    def _hosts(self, fids) -> dict:
        out = {self.me: _safe(self.st.agent_name()) or "Agent J"}
        for f in fids:
            out[f] = _safe(self.svc.store.name_of(f)) or f[:12]
        return out

    def task_session(self):
        agent = self.svc.host.agent
        explicit = getattr(agent, "session", None)
        if isinstance(explicit, str):
            return explicit
        if isinstance(explicit, dict) and explicit.get("sessionId"):
            return str(getattr(agent, "kind", "")) + ":" + str(explicit["sessionId"])
        return str(getattr(agent, "kind", "")) + ":" + str(self.st.agent_session(getattr(agent, "kind", "")) or "")

    def admit(self, send, text, quote=None):
        from . import provenance
        if not provenance.is_owner(getattr(send, "source", None)) or send.device not in self.st.devices():
            return
        session = self.task_session()
        now = time.time()
        with self.svc.store._edit("owner-ordinary-tasks.json", {}) as rows:
            for key in list(rows):
                if rows[key].get("exp", 0) <= now:
                    rows.pop(key)
            candidates = {key: row for key, row in rows.items() if row.get("device") == send.device and row.get("session") == session}
            chosen = None
            reply = (quote or {}).get("id")
            if type(reply) is int:
                chosen = next((key for key,row in candidates.items() if reply in row.get("turns", [])), None)
            elif " ".join(text.casefold().strip().rstrip("。.!！").split()) in ("继续同一任务", "continue the same task"):
                signed = {key:row for key,row in candidates.items() if any(rec.get("task") == "task:" + key for rec in self._grants_out().values())}
                if len(signed) == 1:
                    chosen = next(iter(signed))
            chosen = chosen or secrets.token_hex(16)
            row = rows.setdefault(chosen, {"device":send.device, "session":session, "exp":now + og.TTL, "turns":[], "provisional":session.endswith(":")})
            for key, old in rows.items():
                if old.get("session") == session:
                    old["active"] = key == chosen
            row["turns"] = [*row["turns"], send.turn][-64:]
            send.owner_task = chosen

    def begin_execution(self, send):
        from .owner_call import Execution
        old = getattr(self, "execution", None)
        if old:
            old.live = False
        self.execution = Execution(send)

    def end_execution(self):
        execution = getattr(self, "execution", None)
        if execution:
            execution.live = False

    def task_scope(self) -> str | None:
        from . import owner_call
        continuation = owner_call.continuation()
        if continuation:
            return continuation[0]
        from . import provenance
        agent = self.svc.host.agent
        send = getattr(agent, "cur_send", None)
        source = provenance.turn_source(agent)
        if not provenance.owner_task_source(agent):
            return None
        goals = getattr(self.svc.host, "goals", None)
        goal = goals.current() if goals is not None else None
        if goal and provenance.is_owner(source):
            device = (goal.get("grant") or {}).get("device")
            if device and self.st.sign_key(device):
                return "goal:" + goal["id"]
        resume = source.get("otg_resume")
        ctx = self.svc.store._read("owner-task-context.json", {}).get(resume) if isinstance(resume, str) else None
        if (ctx and goal and ctx.get("scope") == "goal:" + goal["id"] and provenance.unattended_turn(agent) == "system"
                and ctx.get("device") in self.st.devices() and ctx.get("session") == str(getattr(agent, "session", ""))
                and any(t in self._grants_out() for t in ctx.get("grants", []))):
            return ctx["scope"]
        if send is None or not provenance.is_owner(source):
            return None
        device = getattr(send, "device", None)
        if not device or device not in self.st.devices():
            return None
        task = getattr(send, "owner_task", None)
        if task:
            row = self.svc.store._read("owner-ordinary-tasks.json", {}).get(task, {})
            current_session = self.task_session()
            if row.get("provisional") and row.get("device") == device and row.get("session", "").split(":",1)[0] == current_session.split(":",1)[0]:
                with self.svc.store._edit("owner-ordinary-tasks.json", {}) as rows:
                    row = rows.get(task, {})
                    row["session"], row["provisional"] = current_session, current_session.endswith(":")
            if row.get("device") != device or row.get("session") != self.task_session() or row.get("exp", 0) <= time.time() or not row.get("active"):
                return None
            return "task:" + task
        return f"{device}:{send.sid}:{send.turn}"

    def recovery_source(self, agent) -> dict:

        from . import provenance
        out = provenance.system()
        goals = getattr(self.svc.host, "goals", None)
        goal = goals.current() if goals is not None else None
        if not goal:
            return out
        rows = self.svc.store._read("owner-task-context.json", {})
        for handle, ctx in rows.items():
            if (ctx.get("scope") == "goal:" + goal["id"] and ctx.get("session") == str(getattr(agent, "session", ""))
                    and ctx.get("device") in self.st.devices() and ctx.get("exp", 0) > time.time()):
                return {**out, "otg_resume": handle}
        return out

    async def _sign(self, otg: dict) -> dict:

        h = self.svc.host
        scope = self.task_scope()
        from . import provenance, owner_call, policy_registry
        continuation = owner_call.continuation()
        if not scope or (not continuation and (not provenance.is_owner(provenance.turn_source(h.agent)) or not getattr(h.agent, "cur_send", None))):
            return {"result": "refused", "why": "owner_source"}
        if self.usable_after() > time.time():
            return {"result":"refused", "why":"cooldown", "usable_after":self.usable_after()}
        device = continuation[1] if continuation else h.agent.cur_send.device
        gate = await self.svc._outbound(og.card_text(otg))
        if not gate.ok:
            return {"result":"refused", "why":"secret_policy"}
        ans = await h.ask(og.TOOL, {"grant": otg["tid"], "card": og.card_text(otg), "summary":og.card_text(otg), "otg":otg}, batch=False, grant_info=True,
                          require_passkey=True, ttl=5 * h.ask_ttl, only=device,
                          _company_proposal=policy_registry.HOST_CARD if continuation else None)
        if self.task_scope() != scope or (not continuation and getattr(getattr(h.agent, "cur_send", None), "device", None) != device):
            return {"result":"refused", "why":"owner_source"}
        if ans.get("behavior") != "allow":
            return {"result": "refused", "why": "owner"}
        try:
            proof = og.proof_of(otg, ans.get("grant_info") or {})
        except og.GrantError as e:
            return {"result": "refused", "why": e.why}
        if scope.startswith("task:"):
            with self.svc.store._edit("owner-ordinary-tasks.json", {}) as rows:
                row = rows.get(scope[5:])
                if row:
                    row["exp"] = max(row.get("exp", 0), otg["exp"])
        with self.svc.store._edit("grants-out.json", {}) as d:
            now = time.time()
            for k in [k for k, v in d.items() if not isinstance(v, dict) or v.get("exp", 0) <= now]:
                d.pop(k)
            d[otg["tid"]] = {"otg": otg, "proof": proof, "exp": otg["exp"], "used": {}, "at": int(now), "task": scope}
        if scope.startswith("goal:"):
            with self.svc.store._edit("owner-task-context.json", {}) as d:
                now = time.time()
                for k in list(d):
                    if d[k].get("exp", 0) <= now:
                        d.pop(k)
                handle = next((k for k,v in d.items() if v.get("scope") == scope and v.get("device") == device
                               and v.get("session") == str(getattr(h.agent, "session", ""))), secrets.token_hex(16))
                old = d.get(handle, {})
                d[handle] = {"scope": scope, "device": device, "session": str(getattr(h.agent, "session", "")),
                             "grants": list(dict.fromkeys([*(old.get("grants") or []), otg["tid"]]))[-16:], "exp": otg["exp"]}
        for fid in og.targets(otg):
            self.svc._send(fid, {"t": "otg_put", "otg": otg, "proof": proof})
        self.svc.log("owner_grant", result="signed", steps=len(otg["steps"]))
        return {"proof": proof}

    def covering(self, name: str, frm: str, to: str, dest: str = "") -> tuple[dict, int] | None:

        for rec in self._grants_out().values():
            if rec.get("task") != self.task_scope() or not self.task_scope():
                continue
            for s in rec["otg"]["steps"]:
                if (s["cat"] == "secret" and s["name"] == name and s["from"] == frm and s["to"] == to
                        and not (rec.get("used") or {}).get(str(s["i"])) and (not dest or s["dest"] == dest)):
                    return rec, s["i"]
        return None

    async def cli(self, req: dict, cwd: str | None) -> dict:
        from .owner_call import live
        if not live():
            return {"result":"refused", "why":"caller"}
        op = req.get("op")
        try:
            if op == "grants":
                return self._list(req)
            if self.svc.host.stopped():
                return {"result": "stopped"}
            if op in ("ask", "give"):
                return await self._one(op, req, cwd)
            if op == "grant":
                return await self._plan(req, cwd)
        except og.GrantError as e:
            return {"result": "refused", "why": e.why, "detail": e.detail,
                    **({"usable_after":int(e.detail.split(":",1)[1])} if e.why == "cooldown" and e.detail.startswith("usable_after:") else {})}
        return {"result": "refused", "why": "shape", "detail": "op"}

    async def _one(self, op: str, req: dict, cwd: str | None) -> dict:
        from .elevate import Refused, parse_dest
        name = req.get("name")
        if not isinstance(name, str) or not og.NAME_RE.fullmatch(name):
            raise og.GrantError("shape", "--name: a variable name")
        from . import provenance
        if provenance.owner_grants(self.svc.host.agent):
            return await self.more(req)
        fid, why = self._resolve(req.get("peer"))
        if not fid:
            raise og.GrantError(why or "not_friend", "--peer: a Same owner friend on Agent J ≥ this version")
        try:
            if op == "ask":
                raw = req.get("dest") or ""
                spec = parse_dest(raw, name, cwd, self.st.root) if raw else registry_dest(self.st, name)
                if spec is None:
                    raise og.GrantError("dest", "--dest env:<file>[#KEY] or file:<path> (this host)")
                step = og.secret_step(name, fid, self.me, canonical(spec), req.get("src") or "")
            else:
                raw = req.get("src") or ""
                spec = parse_dest(raw, name, cwd, self.st.root) if raw else registry_dest(self.st, name)
                if spec is None:
                    raise og.GrantError("not_found", "--src env:<file>[#KEY] or file:<path> on this host")
                dest = req.get("dest") or ""
                if dest and not (dest.startswith(("env:/", "file:/", "env:~", "file:~", "/", "~"))):
                    raise og.GrantError("dest", "--dest on the other host: an absolute path (or ~/…)")
                step = og.secret_step(name, self.me, fid, dest, canonical(spec))
        except Refused as e:
            raise og.GrantError("dest", e.detail) from None
        frm, to = step["from"], step["to"]
        hit = self.covering(name, frm, to, step["dest"])
        if hit is None:
            otg = og.build(self.me, self._hosts([fid]), req.get("title") or pick(self.svc.lang, f"传密钥 {name}", f"Move key {name}"),
                           req.get("why") or "", [step], self.svc.lang)
            signed = await self._sign(otg)
            if "proof" not in signed:
                return signed
            rec = {"otg": otg, "proof": signed["proof"]}
            i = 0
        else:
            rec, i = hit
            s = rec["otg"]["steps"][i]
            if op == "ask":
                spec = parse_dest(s["dest"], name, None, self.st.root)
            else:
                signed_spec = parse_dest(s["src"], name, None, self.st.root) if s["src"] else registry_dest(self.st, name)
                if signed_spec is None:
                    raise og.GrantError("not_found", "the signed default source is not registered")
                if req.get("src") and canonical(spec) != canonical(signed_spec):
                    raise og.GrantError("shape", "--src differs from the signed source")
                spec = signed_spec
        res = await (self.pull(fid, rec, i, spec) if op == "ask" else self.push(fid, rec, i, spec))
        res["signed"] = hit is None
        return res

    async def _plan(self, req: dict, cwd: str | None) -> dict:
        from .elevate import Refused, parse_dest
        plan = req.get("plan")
        if not isinstance(plan, dict) or set(plan) - {"title", "why", "steps"} or not isinstance(plan.get("steps"), list):
            raise og.GrantError("shape", 'plan: {"title", "why", "steps": [...]}')
        steps, fids = [], []

        def host(ref):
            if ref in ("self", "me", self.me):
                return self.me
            fid, why = self._resolve(ref)
            if not fid:
                raise og.GrantError(why or "not_friend", f"{ref}: a Same owner friend")
            if fid not in fids:
                fids.append(fid)
            return fid
        for n, s in enumerate(plan["steps"][:og.MAX_STEPS + 1]):
            if not isinstance(s, dict):
                raise og.GrantError("shape", f"steps[{n}]")
            if "secret" in s:
                if set(s) - {"secret", "from", "to", "dest", "src"}:
                    raise og.GrantError("shape", f"steps[{n}]: secret, from, to, dest, src")
                frm, to = host(s.get("from") or "self"), host(s.get("to") or "self")
                dest, src = s.get("dest") or "", s.get("src") or ""
                try:
                    if to == self.me and dest:
                        dest = canonical(parse_dest(dest, s["secret"], cwd, self.st.root))
                    if frm == self.me and src:
                        src = canonical(parse_dest(src, s["secret"], cwd, self.st.root))
                except Refused as e:
                    raise og.GrantError("dest", f"steps[{n}]: {e.detail}") from None
                steps.append(og.secret_step(s["secret"], frm, to, dest, src))
            else:
                h = host(s.get("host") or "")
                steps.append(og.grant_step(h, {k: v for k, v in s.items() if k != "host"}))
        if not fids:
            raise og.GrantError("shape", "a grant names at least one other Same owner host")
        otg = og.build(self.me, self._hosts(fids), plan.get("title") or "", plan.get("why") or "", steps, self.svc.lang)
        signed = await self._sign(otg)
        if "proof" not in signed:
            return signed
        return {"result": "signed", "grant": otg["tid"], "exp": otg["exp"], "steps": len(steps)}

    def _list(self, req: dict) -> dict:
        tid = req.get("revoke")
        if isinstance(tid, str) and _R.fullmatch(tid):
            gone = self.svc.store._read("grants-out.json", {}).get(tid)
            if gone:
                # Durable retry intent must exist before removing the live plan.
                with self.svc.store._edit("grant-revokes.json", {}) as d:
                    for k in list(d):
                        if d[k].get("exp",0) <= time.time():
                            d.pop(k)
                    d.setdefault(tid, {"targets":og.targets(gone["otg"]), "acks":[], "exp":gone["exp"]})
            pending = self.svc.store._read("grant-revokes.json", {}).get(tid)
            self.tombstone(tid, gone["exp"] if gone else int(time.time()) + og.TTL)
            with self.svc.store._edit("grants-out.json", {}) as d:
                d.pop(tid, None)
            if pending:
                for fid in pending["targets"]:
                    if fid not in pending["acks"]:
                        self._send_revoke(fid, tid)
            return {"result":"revoked" if gone or pending else "unknown", "grant":tid,
                    "pending":len(set(pending["targets"])-set(pending["acks"])) if pending else 0}
        rows = []
        for k, v in self._grants_out().items():
            rows.append({"grant": k, "dir": "signed_here", "exp": v["exp"], "title": v["otg"]["title"],
                         "steps": [og._step_line(v["otg"], s) for s in v["otg"]["steps"]], "used": v.get("used") or {}})
        for k, v in self._grants_in().items():
            rows.append({"grant": k, "dir": "from", "by": v["otg"]["hosts"].get(v["fid"]), "exp": v["exp"],
                         "title": v["otg"]["title"], "steps": [og._step_line(v["otg"], s) for s in v["otg"]["steps"]],
                         "used": v.get("used") or {}})
        pending = [{"grant":t, "pending":[f for f in v.get("targets", []) if f not in v.get("acks", [])], "exp":v["exp"]}
                   for t,v in self.svc.store._read("grant-revokes.json", {}).items() if v.get("exp",0)>time.time()]
        return {"result": "list", "items": rows, "revocations":pending, "usable_after":self.usable_after()}


def _safe(name) -> str:
    from .text import clean_label
    return clean_label(str(name or ""))[:40]
