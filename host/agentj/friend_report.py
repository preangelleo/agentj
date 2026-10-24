"""P497 #199: host-only reports from a peer session to the owner's main thread.

Integration contract: call submit only for an authenticated incoming message AFTER
PeerService's existing inbound limits/guard/history. The reporting decision/reason
comes from the local peer session, never the wire payload. deliver(envelope) records
one main-thread history page and notifies the owner. reply_via_peer(request) runs the
existing friend-specific session + outbound guard/approvals; it must never route via
SameOwner.forward or send raw main-thread context. Both callbacks return True only
when durably accepted. Reply False means definitely not sent; None/exception means uncertain. An ambiguous delivery is retained as uncertain, not replayed.
No socket, CLI, transport or authority is added here.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

from . import diskq, peer_guard, provenance

FILE = "reports.json"
MANDATORY = frozenset(("owner_requested", "decision", "commitment", "blocked", "failed"))
REASONS = MANDATORY | {"update"}
BATCH_SECONDS = 60
PER_FRIEND_MINUTE = 3
GLOBAL_MINUTE = 20
MAX_RECORDS = 500
MAX_SUMMARY = 2000
RETENTION = 7 * 86400


def owner_source(source) -> bool:
    # None normalizes to phone elsewhere; it must NEVER authorize a report reply.
    return isinstance(source, dict) and source.get("kind") == "phone" and source.get("role") == "owner"


class Reporter:
    def __init__(self, store, deliver, reply_via_peer, *, index, clock=time.time):
        self.store, self.deliver, self.reply_via_peer = store, deliver, reply_via_peer
        if not isinstance(index, peer_guard.SecretIndex):
            raise ValueError("secret_index_required")
        self.index, self.clock = index, clock

    def _read(self):
        # Damaged state must not silently erase deduplication / uncertain receipts.
        p = self.store._p(FILE)
        if not p.exists():
            return {"records": {}, "remarks": {}, "sent": []}
        if p.is_symlink() or p.stat().st_size > 4 * 1024 * 1024:
            raise ValueError("report_state_invalid")
        d = json.loads(p.read_text())
        if not isinstance(d, dict) or not all(isinstance(d.get(k), t) for k, t in
                                              (("records", dict), ("remarks", dict), ("sent", list))):
            raise ValueError("report_state_invalid")
        return d

    def _allowed(self, fid):
        return bool(self.store.settings().get("on") and self.store.get(fid) and not self.store.is_blocked(fid))

    def set_remark(self, fid, name, *, source):
        """Owner-local remark, never the remote card/name. A wires this only after signed owner authorization."""
        if not owner_source(source) or not self._allowed(fid):
            return {"ok": False, "why": "owner_required"}
        if not isinstance(name, str) or not name.strip() or len(name) > 80 or any(ord(c) < 32 for c in name):
            return {"ok": False, "why": "shape"}
        if not peer_guard.outbound(name, self.index).ok:
            return {"ok": False, "why": "privacy"}
        with self.store.lock():
            d = self._read()
            d["remarks"][fid] = name.strip()
            self.store._write(FILE, d)
        return {"ok": True}

    def submit(self, fid, mid, summary, *, reason="update", thread=None):
        """Required reasons cannot be demoted/batched by a peer's payload; caller is the local peer policy."""
        if not self._allowed(fid):
            return {"ok": False, "why": "unavailable"}
        if thread is not None and (not isinstance(thread, str) or not re.fullmatch(r"[0-9a-f]{16}", thread)):
            return {"ok": False, "why": "shape"}
        if (not isinstance(mid, str) or not 0 < len(mid) <= 128 or not isinstance(summary, str)
                or not summary.strip() or len(summary) > MAX_SUMMARY or reason not in REASONS):
            return {"ok": False, "why": "shape"}
        if not peer_guard.outbound(summary, self.index, self.store.settings().get("never_tell_extra", ())).ok:
            return {"ok": False, "why": "privacy"}
        rid = hashlib.sha256((fid + "\0" + mid).encode()).hexdigest()
        now = self.clock()
        with self.store.lock():
            d = self._read()
            if rid in d["records"]:
                return {"ok": True, "id": rid, "state": d["records"][rid]["state"]}
            # Retain pending/uncertain forever until an owner resolves them; backpressure rather than lose reports.
            d["records"] = {k: r for k, r in d["records"].items()
                            if r["state"] not in ("replied", "reported") or now - r["at"] < RETENTION}
            if len(d["records"]) >= MAX_RECORDS:
                return {"ok": False, "why": "backpressure"}
            d["records"][rid] = {"friend": fid, "mid": mid, "summary": summary, "reason": reason,
                                  "at": now, "state": "pending", "thread": thread}
            self.store._write(FILE, d)
        return {"ok": True, "id": rid, "state": "pending", "mandatory": reason in MANDATORY}

    async def flush(self):
        """Mandatory reports are eligible immediately; ordinary updates wait 60s and batch per friend.
        Call after submit and on the host tick. Rate-limited reports stay pending (mandatory first).
        """
        now = self.clock()
        with self.store.lock():
            d = self._read()
            d["sent"] = [r for r in d["sent"] if now - r["at"] < 60]
            pending = sorted(((k, r) for k, r in d["records"].items() if r["state"] == "pending"),
                             key=lambda pair: (pair[1]["reason"] not in MANDATORY, pair[1]["at"]))
            groups = {}
            for rid, r in pending:
                fid = r["friend"]
                if not self._allowed(fid):
                    continue
                if r["reason"] not in MANDATORY and now - r["at"] < BATCH_SECONDS:
                    continue
                if fid not in groups:
                    if len(d["sent"]) >= GLOBAL_MINUTE or sum(s["friend"] == fid for s in d["sent"]) >= PER_FRIEND_MINUTE:
                        continue
                    groups[fid] = []
                    d["sent"].append({"friend": fid, "at": now})
                if len(groups[fid]) < 8:
                    groups[fid].append((rid, dict(r)))
                    r["state"] = "uncertain"   # persisted BEFORE any await: no duplicate after crash/restart
            self.store._write(FILE, d)
            remarks = dict(d["remarks"])
        durable = await diskq.aflush()
        results = []
        for fid, rows in groups.items():
            # Re-read revocation immediately before crossing to main-thread history.
            ok = False
            if durable and self._allowed(fid):
                name = remarks.get(fid) or fid  # NEVER a remote-supplied display name
                data = {"friend": fid, "name": name, "reports": [dict(id=rid, **r) for rid, r in rows]}
                envelope = {"kind": "friend_report", "source": {"kind": "unknown", "role": "group"},
                            "untrusted": True, "execute": False, "reply_route": "peer",
                            "data": peer_guard.wrap(fid, name, json.dumps(data, ensure_ascii=False))}
                try:
                    ok = await self.deliver(envelope) is True
                except Exception:  # uncertain: never include callback error text/content in logs
                    pass
            with self.store.lock():
                d = self._read()
                for rid, _ in rows:
                    d["records"][rid]["state"] = "reported" if ok else "uncertain"
                self.store._write(FILE, d)
            results.append({"ids": [rid for rid, _ in rows], "state": "reported" if ok else "uncertain"})
        return results

    async def reply(self, rid, text, *, source, resend=False):
        """Only the owner's explicit reply text leaves; no main conversation is copied to a peer."""
        if not owner_source(source):
            return {"ok": False, "why": "owner_required"}
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_SUMMARY:
            return {"ok": False, "why": "shape"}
        if not peer_guard.outbound(text, self.index, self.store.settings().get("never_tell_extra", ())).ok:
            return {"ok": False, "why": "privacy"}
        with self.store.lock():
            d = self._read()
            r = d["records"].get(rid)
            if not r or r["state"] not in ("reported", "reply_unknown") or not self._allowed(r["friend"]):
                return {"ok": False, "why": "not_replyable"}
            retry_unknown = r["state"] == "reply_unknown"
            if retry_unknown != (resend is True):
                return {"ok": False, "why": "resend_confirmation_required"}
            r["state"] = "reply_uncertain"
            self.store._write(FILE, d)
            request = {"friend": r["friend"], "reply_to": r["mid"], "text": text,
                       "route": "peer", "retry_unknown": retry_unknown, "report_id": rid, "thread": r.get("thread"), "source": provenance.phone()}
        try:
            outcome = await self.reply_via_peer(request) if await diskq.aflush() else False
        except Exception:
            outcome = None
        ok = outcome is True
        state = "replied" if ok else ("reply_unknown" if retry_unknown else "reported") if outcome is False else "reply_uncertain"
        with self.store.lock():
            d = self._read()
            d["records"][rid]["state"] = state
            self.store._write(FILE, d)
        return {"ok": ok, "state": state}
