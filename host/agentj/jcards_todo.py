"""P394 (#130) the official To-do card, kept by the Agent and the owner together.

Leo 10-19: 「这个待办事项应该是 AgentJ 和用户共同维护的……每个待办事项要多一个 key……责任人是谁……什么时候过期，什么时候开始……
这个开始有可能是某种条件，有可能是某个时间……做完了，你自己把它打条横杠……只看 AgentJ……和责任人是董事长」.

The active data stays in cards-data/todo/todo.json; closed rows move to archive/<id>.json near capacity (0287). Version 2
adds fields to each item; a version-1 file ({"items": [{"id", "text", "done"}]}) reads without loss and is rewritten as
version 2 on the first change, every old id, text and order kept:

    {"schema": 2, "items": [{"id": "t3", "text": "…", "owner": "agent" | "owner",
      "status": "pending" | "active" | "done" | "cancelled", "done": <status == done, for older readers>,
      "start": null | {"at": "<ISO date or date-time>"} | {"after": "<item id>"} | {"when": "<free text>"},
      "end": <same three kinds>, "by": "agent" | "owner", "source": "<which conversation>" | null,
      "note": "<text>" | null, "created": "<ISO>" | null, "finished": "<ISO>" | null}]}

Defaults for an old item: owner = owner, by = owner, status = done ? done : pending, no start / end. A date without a time
starts at 00:00 and ends at 23:59:59 of that day (this computer's time zone); a time without a zone is local time.

`agentj card todo add|edit|done|cancel|reopen|start|toggle|list|due` writes under one lock, read → check → temp file →
rename (an Agent and the phone's buttons may race; a change between the read and the write is refused, never merged).
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import re
import stat
import uuid

from . import jcards as cards
from . import privacy

CID = "todo"
SCHEMA = 2
OWNERS = ("agent", "owner")
STATUSES = ("pending", "active", "done", "cancelled")
CLOSED = ("done", "cancelled")
OPS = ("add", "edit", "done", "cancel", "reopen", "start", "toggle", "delete", "list", "due", "archive")
COND_MAX, NOTE_MAX, SOURCE_MAX = 200, 500, 200
SOON = 24 * 3600                        # s: an end time this close is 「快到期」 / due soon
ID_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
REL_RE = re.compile(r"\+(\d{1,4})([mhdw])")
AFTER_RE = re.compile(r"(?:after|等)[:：\s]\s*([A-Za-z0-9_-]{1,32})")
# the card's display order: overdue → due / end condition met → in progress → may start now → open → waiting → done → cancelled
RANK = {"overdue": 0, "due": 1, "active": 2, "ready": 3, "open": 4, "waiting": 5, "done": 6, "cancelled": 7}


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _local(t: dt.datetime) -> dt.datetime:
    return t if t.tzinfo else t.astimezone()      # naive = this computer's local time


def parse_time(s: str, end: bool = False) -> dt.datetime | None:
    """ISO date (→ 00:00 / 23:59:59 local) or date-time (naive = local); None when it is neither."""
    s = s.strip()
    if DATE_RE.fullmatch(s):
        try:
            d = dt.date.fromisoformat(s)
        except ValueError:
            return None
        return _local(dt.datetime.combine(d, dt.time(23, 59, 59) if end else dt.time(0, 0)))
    try:
        return _local(dt.datetime.fromisoformat(s.replace("Z", "+00:00")))
    except ValueError:
        return None


def _iso(t: dt.datetime) -> str:
    return t.astimezone().isoformat(timespec="minutes")


def condition(raw, now: dt.datetime | None = None, end: bool = False):
    """CLI words → a stored condition: '' / none → None; ISO / +30m +2h +1d +1w → {"at"}; after:<id> → {"after"}; any other
    words → {"when"}. ValueError(why) on a secret-looking or too long text."""
    if raw is None:
        return None
    s = str(raw).strip()
    if s.lower() in ("", "none", "null", "-", "无", "没有"):
        return None
    m = REL_RE.fullmatch(s)
    if m:
        n, u = int(m.group(1)), m.group(2)
        return {"at": _iso((now or now_utc()) + dt.timedelta(minutes=n * {"m": 1, "h": 60, "d": 1440, "w": 10080}[u]))}
    if DATE_RE.fullmatch(s) and parse_time(s, end):
        return {"at": s}
    if re.match(r"\d{4}-\d{2}-\d{2}[T ]\d", s):
        t = parse_time(s)
        if not t:
            raise ValueError("bad_time")
        return {"at": _iso(t)}
    m = AFTER_RE.fullmatch(s)
    if m:
        return {"after": m.group(1)}
    return {"when": _text(s, COND_MAX)}


def _text(s, most: int) -> str:
    s = str(s or "").strip()
    if not s or "\x00" in s:
        raise ValueError("bad_text")
    if privacy.redact(s) != s:
        raise ValueError("looks_like_a_secret")
    s = " ".join(s.split())
    if len(s) > most:
        raise ValueError("too_long")
    return s


def _cond(v):
    """A stored condition as read: exactly one known key with a string, else None (a hand edit never breaks the card)."""
    if not isinstance(v, dict) or len(v) != 1:
        return None
    (k, x), = v.items()
    if k == "at" and isinstance(x, str) and parse_time(x.strip()[:COND_MAX]):
        return {k: x.strip()}
    if k in ("at", "when") and isinstance(x, str) and x.strip():   # an `at` that is not a time is shown (redacted) as words
        return {"when": x.strip()[:COND_MAX]}
    if k == "after" and isinstance(x, str) and ID_RE.fullmatch(x):
        return {k: x}
    return None


def normalize(r, i: int) -> dict | None:
    """One stored row → a full version-2 item (None: not an item). A version-1 row gets the defaults; nothing is lost."""
    if isinstance(r, str):
        r = {"text": r}
    if not isinstance(r, dict) or not isinstance(r.get("text"), str) or not r["text"].strip():
        return None
    st = r.get("status") if r.get("status") in STATUSES else ("done" if r.get("done") is True else "pending")
    s = lambda k, most: r[k].strip()[:most] if isinstance(r.get(k), str) and r[k].strip() else None
    t = lambda k: r[k] if isinstance(r.get(k), str) and len(r[k]) <= 40 and parse_time(r[k]) else None   # a time or nothing
    return {"id": r["id"] if isinstance(r.get("id"), str) and ID_RE.fullmatch(r["id"]) else f"i{i + 1}",
            "text": r["text"], "owner": r.get("owner") if r.get("owner") in OWNERS else "owner",
            "status": st, "done": st == "done", "start": _cond(r.get("start")), "end": _cond(r.get("end")),
            "by": r.get("by") if r.get("by") in OWNERS else "owner", "source": s("source", SOURCE_MAX),
            "note": s("note", NOTE_MAX), "created": t("created"), "finished": t("finished")}


def _parse(raw: bytes | None):
    """→ (doc, rows, problem): the file as stored (rows = every stored row, items or not)."""
    if not raw:
        return {}, [], None
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, [], "bad_data"
    rows = doc.get("items") if isinstance(doc, dict) else doc
    if not isinstance(rows, list):
        return None, [], "bad_data"
    return doc if isinstance(doc, dict) else {}, rows, None


def load(raw: bytes | None) -> tuple[list, str | None]:
    """→ (items, problem). Missing file = an empty list."""
    _, rows, bad = _parse(raw)
    return [n for n in (normalize(r, i) for i, r in enumerate(rows[:cards.ITEMS_MAX])) if n], bad


# ---------------------------------------------------------------- what is due
def _when(c, end: bool) -> dt.datetime | None:
    return parse_time(c["at"], end) if c and "at" in c else None


def assess(it: dict, by_id: dict, now: dt.datetime) -> dict:
    """{state, flags}: state is the card's rank key (RANK); flags are what the phone marks and `due` reports:
    overdue · due_soon · end_met (the end condition item is done) · ready (a start time passed / the item it waits for is
    done) · waiting · check (a free-text condition only the Agent can judge)."""
    if it["status"] in CLOSED:
        return {"state": it["status"], "flags": []}
    flags = []
    s, e = it["start"], it["end"]
    if s and "at" in s:
        t = _when(s, False)
        flags.append("ready" if t and t <= now else "waiting")
    elif s and "after" in s:
        dep = by_id.get(s["after"])
        flags.append("ready" if not dep or dep["status"] in CLOSED else "waiting")
    elif s:
        flags.append("check")
    if e and "at" in e:
        t = _when(e, True)
        if t and t < now:
            flags.append("overdue")
        elif t and (t - now).total_seconds() <= SOON:
            flags.append("due_soon")
    elif e and "after" in e:
        dep = by_id.get(e["after"])
        if dep and dep["status"] == "done":
            flags.append("end_met")
    elif e and "check" not in flags:
        flags.append("check")
    if it["status"] == "active":         # already started: neither 「等着」 nor 「可以开始」
        flags = [f for f in flags if f not in ("waiting", "ready")]
    state = ("overdue" if "overdue" in flags else "due" if {"due_soon", "end_met"} & set(flags) else
             "active" if it["status"] == "active" else "waiting" if "waiting" in flags else
             "ready" if "ready" in flags else "open")
    return {"state": state, "flags": flags}


def ordered(items: list, now: dt.datetime | None = None, references=()) -> list:
    """(item, assessment) in the card's order: rank, then the end time (soonest first), then the file's order."""
    now = now or now_utc()
    by_id = {x["id"]: x for x in [*references, *items]}
    far = dt.datetime.max.replace(tzinfo=dt.timezone.utc)
    rows = [(x, assess(x, by_id, now), n) for n, x in enumerate(items)]
    return [(x, a) for x, a, _ in sorted(rows, key=lambda r: (RANK[r[1]["state"]], _when(r[0]["end"], True) or far, r[2]))]


def due(items: list, now: dt.datetime | None = None, who: str = "agent", references=()) -> list:
    """The open items of `who` to look at now: everything open except what still waits for a time or another item."""
    return [{**_brief(x), "state": a["state"], "flags": a["flags"]} for x, a in ordered(items, now, references)
            if x["owner"] == who and a["state"] not in CLOSED and a["state"] != "waiting"]


def _brief(x: dict) -> dict:
    """An item as the CLI prints it into the Agent's conversation: every word redacted (a hand edit may hold a key)."""
    r = lambda v: privacy.redact(v) if isinstance(v, str) else v
    c = lambda v: {k: r(w) for k, w in v.items()} if isinstance(v, dict) else v
    return {"id": r(x["id"]), "text": r(x["text"]), "owner": x["owner"], "status": x["status"], "start": c(x["start"]), "end": c(x["end"]),
            "by": x["by"], "source": r(x["source"]), "note": r(x["note"]), "created": x["created"], "finished": x["finished"]}


# ---------------------------------------------------------------- the phone
def _show_cond(c, by_id: dict, end: bool, lang: str):
    if not c:
        return None
    if "at" in c:
        t = _when(c, end)
        return {"kind": "at", "at": int(t.timestamp() * 1000) if t else None, "date": bool(DATE_RE.fullmatch(c["at"])),
                "text": cards._clean(privacy.redact(c["at"]), 40)}
    if "after" in c:
        dep, aid = by_id.get(c["after"]), c["after"] if privacy.redact(c["after"]) == c["after"] else "?"
        return {"kind": "after", "id": aid, "met": not dep or dep["status"] == "done",
                "text": cards._clean(privacy.redact(dep["text"]), 60) if dep else aid}
    return {"kind": "when", "text": cards._clean(privacy.redact(c["when"]), COND_MAX)}


def phone_items(raw: bytes | None, lang: str, now: dt.datetime | None = None, root=None) -> tuple[list, str | None]:
    """jcard_get rows for the To-do card: every field the card draws, already redacted, in the card's order."""
    items, bad = load(raw)
    references = dependencies(root, items) if root is not None else []
    by_id = {x['id']: x for x in [*references, *items]}
    out = []
    for x, a in ordered(items, now, references):
        clean = lambda v, most: cards._clean(privacy.redact(v), most) if v else None
        out.append({"id": x["id"], "text": cards._clean(privacy.redact(x["text"]), cards.ITEM_TEXT_MAX),
                    "editable": privacy.redact(x["text"]) == x["text"], "done": x["status"] in CLOSED, "status": x["status"], "owner": x["owner"], "by": x["by"],
                    "start": _show_cond(x["start"], by_id, False, lang), "end": _show_cond(x["end"], by_id, True, lang),
                    "note": clean(x["note"], NOTE_MAX), "source": clean(x["source"], SOURCE_MAX),
                    "created": x["created"], "finished": x["finished"], "state": a["state"], "flags": a["flags"]})
    return out, bad


# ---------------------------------------------------------------- changes
def _fail(why: str, **kw) -> dict:
    return {"ok": False, "id": CID, "why": why, **kw}


def _next_id(items: list, reserved=()) -> str:
    n = max([int(m.group(1)) for x in [y["id"] for y in items] + list(reserved) for m in [re.fullmatch(r"t(\d+)", x)] if m] + [0])
    taken = {x["id"] for x in items} | set(reserved)
    while f"t{n + 1}" in taken:
        n += 1
    return f"t{n + 1}"


def _apply(items: list, op: str, entry, f: dict, now: dt.datetime, reserved=(), known=None) -> tuple[str | None, str | None]:
    """Changes `items` in place → (the item id, why-not)."""
    stamp = _iso(now)
    known = reserved if known is None else known
    if op == "delete":
        x = next((y for y in items if y["id"] == entry), None)
        if not x:
            return None, "not_found"
        items.remove(x)
        return entry, None
    if op == "add":
        x = {"id": _next_id(items, reserved), "text": f["text"], "owner": f.get("owner") or "owner", "status": f.get("status") or "pending",
             "done": False, "start": f.get("start"), "end": f.get("end"), "by": f.get("by") or "agent",
             "source": f.get("source"), "note": f.get("note"), "created": stamp, "finished": None}
        items.append(x)
    else:
        x = next((y for y in items if y["id"] == entry), None)
        if not x:
            return None, "not_found"
        for k in ("text", "owner", "start", "end", "source", "note", "status"):   # done / cancel may carry a note too
            if k in f:
                x[k] = f[k]
        if op != "edit":
            x["status"] = {"done": "done", "cancel": "cancelled", "reopen": "pending", "start": "active",
                           "toggle": "pending" if x["status"] == "done" else "done"}[op]
    for k in ("start", "end"):
        c = x.get(k)
        if c and "after" in c and (c["after"] == x["id"] or c["after"] not in known and not any(y["id"] == c["after"] for y in items)):
            return None, "bad_after"
    x["done"] = x["status"] == "done"
    x["finished"] = (x.get("finished") or stamp) if x["status"] in CLOSED else None
    return x["id"], None


def fields(text=None, owner=None, start=None, end=None, source=None, note=None, status=None, by=None, now=None) -> dict:
    """CLI flags → stored fields (only the given ones). ValueError(why) on a bad value."""
    f = {}
    if text is not None:
        f["text"] = _text(text, cards.ITEM_TEXT_MAX)
    for k, v, allowed in (("owner", owner, OWNERS), ("by", by, OWNERS), ("status", status, STATUSES)):
        if v is not None:
            if v not in allowed:
                raise ValueError("bad_" + k)
            f[k] = v
    if start is not None:
        f["start"] = condition(start, now)
    if end is not None:
        f["end"] = condition(end, now, end=True)
    for k, v, most in (("source", source, SOURCE_MAX), ("note", note, NOTE_MAX)):
        if v is not None:
            f[k] = _text(v, most) if str(v).strip() else None
    return f


def change(root, op: str, entry=None, now: dt.datetime | None = None, revision=None, **kw) -> dict:
    """One atomic change of the To-do file. The card may be off: the item is still kept (the owner sees it when the card is
    switched on) and the answer says `on: false`."""
    now = now or now_utc()
    c = cards.find(root, CID)
    if not c:
        return _fail("not_found")
    if op not in OPS or op in ("list", "due", "archive"):
        return _fail("bad_op")
    try:
        f = fields(now=now, **kw)
    except ValueError as e:
        return _fail(str(e))
    if op == "add" and "text" not in f:
        return _fail("bad_text")
    if op != "add" and not (isinstance(entry, str) and ID_RE.fullmatch(entry)):
        return _fail("not_found")
    try:
        with cards._dirfd(root, ("cards-data", CID), create=True) as d:
            lockfd = os.open(".todo-write.lock", os.O_CREAT | os.O_RDWR | os.O_NONBLOCK | cards._NOFOLLOW, 0o600, dir_fd=d)
            try:
                if not stat.S_ISREG(os.fstat(lockfd).st_mode):
                    return _fail("refused")
                import fcntl
                fcntl.flock(lockfd, fcntl.LOCK_EX)
                raw, problem, cut = cards._read(root, c)
                if problem not in (None, "empty") or cut:
                    return _fail("refused")
                from .jcards_data import _revision
                if revision is not None and revision != _revision(raw or b''):
                    return _fail("conflict")
                doc, rows, bad = _parse(raw)
                if bad:
                    return _fail(bad)          # never overwrite a file we cannot read: the owner's hand edit stays
                if len(rows) > cards.ITEMS_MAX:
                    return _fail("full")       # unknown oversized hand edits are never silently trimmed
                from . import jcards_todo_archive as archive
                reserved = archive.ids(root)
                archived_row = None
                if op != "add" and not any(n and n['id'] == entry for n in (normalize(r, i) for i, r in enumerate(rows))) and entry in reserved:
                    archived_row = archive.get(root, entry)
                    rows.append(archived_row)
                known = set(reserved)
                sequence = doc.get('_nextTodoId', 1)
                if type(sequence) is int and sequence > 1:
                    reserved.add('t' + str(sequence - 1))
                at = [(i, n) for i, n in ((i, normalize(r, i)) for i, r in enumerate(rows)) if n]
                items = [n for _, n in at]
                if revision is not None and op == 'edit' and any(x['id'] == entry and privacy.redact(x['text']) != x['text'] for x in items):
                    return _fail('redacted')
                removed = next((x.copy() for x in items if x["id"] == entry), None)
                iid, why = _apply(items, op, entry, f, now, reserved, known)
                if why:
                    return _fail(why)
                # every stored row stays where it was: an item gets its new fields (unknown keys kept), anything else as is
                out = list(rows)
                if op == "delete":
                    out = [r for i, r in enumerate(rows) if not any(j == i and n['id'] == iid for j, n in at)]
                else:
                    for (i, _), n in zip(at, items):
                        out[i] = {**(rows[i] if isinstance(rows[i], dict) else {}), **n}
                    out += items[len(at):]
                # Archive edits stay in the archive; reopening installs the row in the main file first.
                item = removed if op == 'delete' else next(x for x in items if x['id'] == iid)
                if archived_row is not None and op != 'delete':
                    # A legacy long note/source is display-bounded, not a license to truncate archived source fields.
                    updated = {**archived_row, **{k: item[k] for k in f},
                               'status': item['status'], 'done': item['done'], 'finished': item['finished']}
                    out = [updated if isinstance(r, dict) and r.get('id') == iid else r for r in out]
                if archived_row is not None and (item['status'] in CLOSED or op == 'delete'):
                    if op == 'delete':
                        archive.remove(root, iid)
                    else:
                        archive.store(root, next(r for r in out if isinstance(r, dict) and r.get('id') == iid))
                    return {"ok": True, "revision": _revision(raw or b''), "id": CID, "item": _brief(item),
                            "on": c["on"], "address": cards.address(CID, iid), "archived": True,
                            "capacity": archive.capacity(root, c, raw)}
                doc = {**doc, 'schema': SCHEMA, '_nextTodoId': int(_next_id(items, reserved)[1:])}
                encode = lambda rs: (json.dumps({**doc, 'items': rs}, ensure_ascii=False, indent=1) + '\n').encode('utf-8')
                body = encode(out)
                limit = c['doc']['data'].get('maxBytes', cards.DATA_DEFAULT)
                copies = []
                if len(body) >= limit * .8 or len(out) >= cards.ITEMS_MAX:
                    # Never stage the changed/new target: a failed main rename must not create a ghost operation.
                    for row in list(out):
                        n = normalize(row, 0)
                        if (n and n['status'] in CLOSED and n['id'] != iid and isinstance(row, dict)
                                and row.get('id') == n['id'] and sum(1 for r in out if isinstance(r, dict) and r.get('id') == n['id']) == 1):
                            original = next((r for r in rows if isinstance(r, dict) and r.get('id') == n['id']), row)
                            copies.append(original)  # archive every original field losslessly, not display-normalized values
                            out.remove(row)
                            body = encode(out)
                            if len(body) < limit * .7 and len(out) < cards.ITEMS_MAX * .8:
                                break
                if len(body) > limit or len(out) > cards.ITEMS_MAX:
                    return {**_fail('too_large' if len(body) > limit else 'full'),
                            'capacity': archive.capacity(root, c, raw)}
                current, issue, truncated = cards._read(root, c)
                if issue not in (None, "empty") or truncated or (current or None) != (raw or None):
                    return _fail("conflict")
                for row in copies:
                    archive.store(root, row)  # durable copy before the main-file atomic removal
                if copies:
                    os.fsync(d)  # persist a newly created archive/ directory before removing its source rows
                if op == 'delete':
                    archive.remove(root, iid)  # an old staged copy must not resurrect after deletion
                name = c["doc"]["data"]["file"]
                archive.atomic(d, name, body)
                if archived_row is not None and op != 'delete':
                    # Stale copies are harmless while the main file contains the ID; clean them after commit.
                    with contextlib.suppress(OSError):
                        archive.remove(root, iid)
            finally:
                os.close(lockfd)
    except OSError:
        return _fail("refused")
    item = removed if op == "delete" else next(x for x in items if x["id"] == iid)
    return {"ok": True, "revision": _revision(body), "id": CID, "item": _brief(item), "on": c["on"], "address": cards.address(CID, iid),
            "capacity": archive.capacity(root, c, body)}


def read(root) -> tuple[list, str | None, bool]:
    """(items, problem, on) — the To-do file as the CLI sees it (on or off)."""
    c = cards.find(root, CID)
    if not c:
        return [], "not_found", False
    raw, problem, cut = cards._read(root, c)
    if problem not in (None, "empty"):
        return [], problem, c["on"]
    items, bad = load(raw)
    return items, bad or ("cut" if cut else None), c["on"]


def dependencies(root, items):
    """Read only archived IDs referenced by current conditions, preserving due/start meaning."""
    from . import jcards_todo_archive as archive
    active = {x['id'] for x in items}
    requested = {c['after'] for x in items for k in ('start', 'end') for c in [x.get(k)] if c and 'after' in c} - active
    out = []
    for iid in requested:
        try:
            out.append(normalize(archive.get(root, iid), 0))
        except OSError:
            pass   # missing/unreadable archive is also reported by the capacity snapshot
    return out


def listing(root, op: str, who: str | None = None, now: dt.datetime | None = None, page=0) -> dict:
    """`todo list` (every item, the card's order, with state / flags) · `todo due` (who's items to look at now; default agent)."""
    items, problem, on = read(root)
    if problem in ("not_found", "refused", "unreadable", "bad_data"):
        return _fail(problem)
    if op == 'archive':
        from . import jcards_todo_archive as archive
        try:
            active = {x['id'] for x in items}
            names = sorted(archive.ids(root) - active)
            if type(page) is not int or page < 0:
                return _fail('bad_op')
            selected = names[page * cards.ITEMS_MAX:(page + 1) * cards.ITEMS_MAX]
            history = [normalize(archive.get(root, iid), 0) for iid in selected]
            return {'ok': True, 'id': CID, 'on': on, 'items': [{**_brief(x), 'state': x['status'], 'flags': []}
                    for x in history if not who or x['owner'] == who], 'page': page, 'total': len(names)}
        except OSError:
            return _fail('refused')
    references = dependencies(root, items)
    if op == "due":
        rows = due(items, now, who or "agent", references)
    else:
        rows = [{**_brief(x), "state": a["state"], "flags": a["flags"]} for x, a in ordered(items, now, references)
                if not who or x["owner"] == who]
    from . import jcards_todo_archive as archive
    c = cards.find(root, CID)
    raw, _, _ = cards._read(root, c)
    try:
        capacity = archive.capacity(root, c, raw)
    except OSError:
        capacity = {'near_limit': True, 'archive_unavailable': True}
    return {"ok": True, "id": CID, "on": on, "items": rows, 'capacity': capacity, **({"problem": problem} if problem else {})}


WHY = {"not_found": ("没有这一条（agentj card todo list）", "no such item (agentj card todo list)"),
       "bad_op": ("不认识的操作", "unknown operation"),
       "bad_text": ("标题不能空，一行写完", "the title must not be empty"),
       "too_long": ("太长了：标题 300 字、条件 200 字、备注 500 字以内", "too long: title 300, condition 200, note 500 characters"),
       "looks_like_a_secret": ("像密钥的东西不进待办，用 agentj secret request", "a key never goes into a to-do; use agentj secret request"),
       "bad_time": ("时间写成 2026-10-20 或 2026-10-20T09:00，或 +2h / +1d", "write a time as 2026-10-20, 2026-10-20T09:00, +2h or +1d"),
       "bad_after": ("after:<id> 要指向另一条已有的待办", "after:<id> must name another existing item"),
       "bad_owner": ("责任人只能是 agent 或 owner", "owner is agent or owner"),
       "bad_by": ("创建者只能是 agent 或 owner", "by is agent or owner"),
       "bad_status": ("状态只能是 pending / active / done / cancelled", "status is pending, active, done or cancelled"),
       "full": ("未归档事项已到上限；请先完成部分事项", "active storage is full; close some items first"),
       "too_large": ("未完成事项超出容量，已完成事项会自动归档；可用 agentj card todo archive 查询", "open items exceed storage capacity; closed items archive automatically (agentj card todo archive)"),
       "conflict": ("刚好有别人改了，重读再改", "it changed meanwhile; read it again and retry"),
       "bad_data": ("待办文件不是有效的 JSON，先修好它（不会覆盖）", "the to-do file is not valid JSON; fix it first (it is not overwritten)"),
       "refused": ("这个文件不能读写（链接或非普通文件）", "the file cannot be used (a link or not a regular file)")}


def command(a, root) -> int:
    """`agentj card todo …` (jcards.parser builds the flags)."""
    if a.op in ("list", "due", "archive"):
        r = listing(root, a.op, a.owner if a.op != "due" else (a.owner or "agent"), page=a.page)
    else:
        kw = {k: getattr(a, k) for k in ("text", "owner", "start", "end", "source", "note", "status", "by")}
        if a.op == "add" and kw["by"] is None:
            kw["by"] = "agent"
        r = change(root, a.op, a.entry, **kw)
    if a.json:
        print(json.dumps(r, ensure_ascii=False))
    elif not r["ok"]:
        z, e = WHY.get(r["why"], (r["why"], r["why"]))
        print(f"✗ {r['why']}：{z} / {e}")
    elif "items" in r:
        for x in r["items"]:
            mark = {"done": "✓", "cancelled": "✗"}.get(x["status"], "·")
            print(f"{mark} {x['id']:<6} [{x['owner']}] {x['state']:<9} {x['text']}"
                  + (f"  ({', '.join(x['flags'])})" if x["flags"] else ""))
        if not r["items"]:
            print("（没有）/ (none)")
    else:
        print(f"✓ {r['item']['id']} {r['item']['status']} · {r['address']}"
              + ("" if r["on"] else "  · 待办卡关着，主人在 设置 → Cards 打开就能看到 / the To-do card is off (Settings → Cards)"))
    if not a.json and r.get('capacity', {}).get('near_limit'):
        print('待办容量接近上限；已完成事项会自动归档，可用 agentj card todo archive 查询 / To-do storage is nearly full; closed items archive automatically: agentj card todo archive')
    return 0 if r["ok"] else 1
