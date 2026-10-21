"""P428 (CALENDAR-SYNC-SPEC §2–§3): the undo journal, the offline outbox and conflict bookkeeping of the local calendar.

`jcards_cal` keeps storage / validation / the locked revisioned write and calls `record()` for every local change: one
`history` entry (≤ 20, newest last; undo applies `before` through the same path, so undo twice = redo) and, for an event
linked to a two-way remote, one coalesced `outbox` op. Remote-pulled changes (jcards_cal_remote) are never journaled.
Listing helpers expose names, counts and flags only — never a key name, server, remote id, href or etag.
"""
from __future__ import annotations

import copy
import re

from . import jcards_cal as cal

TWO_WAY = ("google", "caldav")
HISTORY, OUTBOX, CONFLICTS = 20, 200, 50
META = ("rid", "etag", "base", "basef", "lossy", "conflict", "rdefault")   # sync bookkeeping kept from the current row on undo
WORDS = ("auth", "offline", "conflict", "gone", "failed")


def _iso() -> str:
    return cal.now().replace(microsecond=0).isoformat()


def two_way(doc: dict, name) -> dict | None:
    s = doc.get("subscriptions", {}).get(name) if isinstance(name, str) else None
    return s if isinstance(s, dict) and s.get("kind") in TWO_WAY else None


def is_two_way(root, name, op) -> bool:
    """CLI routing: connect / disconnect always; sync / unsubscribe of a two-way name (ICS keeps the P413 path)."""
    if op in ("connect", "disconnect"):
        return True
    try:
        return two_way(cal.load(root)[0], name) is not None
    except (OSError, ValueError):
        return False


def unknown(root, name) -> bool:
    """D6: `sync --name N` for a name with no subscription at all → `not_found` (unreadable data: the P413 path decides)."""
    try:
        return isinstance(name, str) and bool(re.fullmatch(r"[a-z][a-z0-9-]{1,31}", name)) \
            and name not in cal.load(root)[0].get("subscriptions", {})
    except (OSError, ValueError, AttributeError):
        return False


def _find(doc: dict, ident):
    return next((e for e in doc["events"] if isinstance(e, dict) and e.get("id") == ident), None)


def _pending(doc: dict, ident, name):
    return next((o for o in doc.get("outbox", []) if o.get("id") == ident and o.get("remote") == name), None)


def target(doc: dict, to=None) -> str | None:
    """`add --to NAME`, else the first enabled two-way remote with push_new."""
    if to is not None:
        s = two_way(doc, to)
        if not s or not s.get("enabled"):
            raise ValueError("bad_remote")
        return to
    return next((n for n in sorted(doc.get("subscriptions", {})) if (two_way(doc, n) or {}).get("push_new")
                 and doc["subscriptions"][n].get("enabled")), None)


def queue(doc: dict, op: str, ident: str, name: str, rid=None, etag=None) -> None:
    """Outbox coalescing: create+update → create; create+delete → both dropped; update+update → one update;
    update+delete → delete; delete then a restore (undo) → the delete dropped and an update queued."""
    box = doc.setdefault("outbox", [])
    old = _pending(doc, ident, name)
    if old is not None:
        if old["op"] == "create":
            if op == "delete":
                box.remove(old)
            return
        if old["op"] == "update":
            if op == "delete":
                old.update(op="delete", rid=rid, etag=etag, at=_iso())
            return
        box.remove(old)
        op = "update" if op != "delete" else op
    if len(box) >= OUTBOX:
        raise ValueError("full")
    box.append({"op": op, "id": ident, "remote": name, "at": _iso(), "tries": 0,
                **({"rid": rid, "etag": etag} if op == "delete" else {})})


def record(doc: dict, before, after, journal: bool = True) -> None:
    """Every local change: one history entry (≤ 20, newest last) and, for an event of a two-way remote, an outbox op."""
    row = after or before
    if after is not None and after.get("lossy"):         # F5: a field the owner changed is theirs now, no longer lossy
        keep = [f for f in after["lossy"] if before is not None and before.get(f) == after.get(f)]
        if keep:
            after["lossy"] = keep
        else:
            after.pop("lossy")
    if journal:
        h = doc.setdefault("history", [])
        h.append({"at": _iso(), "id": row["id"], "before": copy.deepcopy(before), "after": copy.deepcopy(after)})
        del h[:-HISTORY]
    name = row.get("remote")
    if not two_way(doc, name) or row.get("readonly"):
        return
    pend = _pending(doc, row["id"], name)
    if after is None:
        if before.get("rid"):
            queue(doc, "delete", row["id"], name, before["rid"], before.get("etag"))
        elif pend:
            queue(doc, "delete", row["id"], name)
        return
    if after.get("rid") and before is None and not (pend and pend["op"] == "delete"):
        for k in ("rid", "etag", "base"):             # its delete already reached the remote: create it again
            after.pop(k, None)
    queue(doc, "update" if after.get("rid") else "create", row["id"], name)


def _fields(e: dict) -> dict:
    return {k: e[k] for k in cal.FIELDS if k in e}


def undo(root, entry=None, revision=None):
    """Reverts the newest history entry (or that event's newest) by applying `before` through `record`; journaled itself."""
    def update(doc):
        rows = [x for x in doc.get("history", []) if isinstance(x, dict) and (entry is None or x.get("id") == entry)]
        if not rows:
            raise ValueError("nothing_to_undo")
        last = rows[-1]
        cur, want = _find(doc, last["id"]), copy.deepcopy(last.get("before"))
        if cur is not None and (cur.get("subscription") or cur.get("readonly")):
            raise ValueError("readonly")
        before = copy.deepcopy(cur)
        if want is None:
            if cur is None:
                raise ValueError("nothing_to_undo")
            doc["events"].remove(cur)
        else:
            new = {**cal.event(want), "id": last["id"], **{k: v for k, v in want.items() if k == "remote"}}
            keep = cur if cur is not None else want
            new.update({k: keep[k] for k in META if k in keep and k != "conflict"})
            if cur is None:
                doc["events"].append(new)
            else:
                doc["events"][doc["events"].index(cur)] = new
            want = new
        record(doc, before, want)
    return cal.transaction(root, update, revision)


def resolve(root, entry, keep, revision=None):
    """keep local → restore the saved local version and queue update / delete (create when the remote copy is gone);
    keep remote → drop the saved copy. Both clear the conflict."""
    if keep not in ("local", "remote"):
        return {"ok": False, "why": "bad_keep"}
    def update(doc):
        c = next((x for x in doc.get("conflicts", []) if x.get("id") == entry), None)
        if c is None:
            raise ValueError("not_found")
        doc["conflicts"].remove(c)
        cur = _find(doc, entry)
        before = copy.deepcopy(cur)
        if cur is not None:
            cur.pop("conflict", None)
        gone = c.get("remote_state") == "deleted"
        if keep == "remote":
            if gone and cur is not None:
                doc["events"].remove(cur)
                record(doc, before, None)
            return
        local = c.get("local")
        if local is None:
            if cur is not None:
                doc["events"].remove(cur)
                record(doc, before, None)
            return
        row = {**cal.event(local), "id": entry, "remote": c.get("remote")}
        lossy = [f for f in c.get("lossy") or () if f in ("title", "note")]
        if cur is None:
            doc["events"].append(row)
        else:
            row.update({k: cur[k] for k in ("rid", "etag", "base", "basef", "rdefault") if k in cur})
            doc["events"][doc["events"].index(cur)] = row
        record(doc, before, row)
        if lossy and cur is not None:
            row["lossy"] = lossy                         # F5: old remote text the owner never touched is not written back
    return cal.transaction(root, update, revision)


def flags(doc: dict, rows: list) -> list:
    """Per-event `remote` (the remote's name), `pending` (queued) and `conflict` — never a remote id / href / etag."""
    raw = {e.get("id"): e for e in doc["events"] if isinstance(e, dict)}
    queued = {o.get("id") for o in doc.get("outbox", []) if isinstance(o, dict)}
    clash = {c.get("id") for c in doc.get("conflicts", []) if isinstance(c, dict)}
    for r in rows:
        e = raw.get(r["id"], {})
        name = e.get("remote") if two_way(doc, e.get("remote")) else None
        r.update(remote=name, pending=r["id"] in queued, conflict=bool(e.get("conflict")) or r["id"] in clash)
    return rows


def sub_rows(doc: dict) -> list:
    out = []
    for n, s in doc.get("subscriptions", {}).items():
        if not (re.fullmatch(r"[a-z][a-z0-9-]{1,31}", n) and isinstance(s, dict)):
            continue
        kind = s.get("kind") if s.get("kind") in TWO_WAY else "ics"
        out.append({"name": n, "kind": kind, "enabled": bool(s.get("enabled")),
                    "last_sync": s.get("last_sync") if isinstance(s.get("last_sync"), str) else None,
                    "last_error": s.get("last_error") if s.get("last_error") in WORDS + ("full",) else None,
                    "queued": sum(1 for o in doc.get("outbox", []) if o.get("remote") == n),
                    "conflicts": sum(1 for c in doc.get("conflicts", []) if c.get("remote") == n),
                    "push_new": bool(s.get("push_new")), "auto": bool(s.get("auto"))})
    return out


def conflict_rows(doc: dict) -> list:
    out = []
    for c in doc.get("conflicts", []):
        try:
            local = c.get("local")
            out.append({"id": c["id"], "remote": c["remote"], "at": c.get("at"), "remote_state": c["remote_state"],
                        "local": None if local is None else cal.event(local)})
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    return out


def queue_view(root) -> dict:
    """`agentj card cal queue`: the outbox and conflicts without ids of the remote side."""
    try:
        doc, _ = cal.load(root)
    except (OSError, ValueError) as e:
        return {"ok": False, "why": str(e) if isinstance(e, ValueError) else "refused"}
    box = [{k: o[k] for k in ("op", "id", "remote", "at", "tries", "error") if k in o} for o in doc.get("outbox", [])]
    return {"ok": True, "queued": len(box), "outbox": box, "conflict_count": len(doc.get("conflicts", [])),
            "conflicts": [{k: c.get(k) for k in ("id", "remote", "at", "remote_state")} for c in doc.get("conflicts", [])]}
