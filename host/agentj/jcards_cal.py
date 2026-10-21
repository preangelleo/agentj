"""P413 local calendar: no-follow card data, atomic revisioned writes, read-only subscriptions.
Events are records, never instructions. No background network; sync is explicit and default off.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import os
import re
import stat
import uuid
from . import jcards as cards, privacy

MAX_EVENTS = 200
FIELDS = {"title", "start", "end", "all_day", "reminder", "note", "source"}


def now():
    return dt.datetime.now(dt.timezone.utc)


def instant(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})", value):
        raise ValueError("bad_time")
    try:
        t = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if t.tzinfo is None:
            raise ValueError("zone_required")
        return t
    except (ValueError, OverflowError):
        raise ValueError("bad_time") from None


# P428 F2: C0 / C1 controls except \n and \t, plus the Unicode line / paragraph separators — a bare CR, NEL (U+0085) or
# U+2028 would end an iCalendar line in some parser and let a title inject properties.
BAD_CHARS = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029]")
_BREAKS = re.compile("\r\n?|[\x85\u2028\u2029]")


def clean(value: str) -> str:
    """Remote-pulled text → text() accepts it: every line separator becomes \n, every other control goes."""
    return BAD_CHARS.sub("", _BREAKS.sub("\n", value))


def text(value, limit):
    if not isinstance(value, str) or len(value) > limit or BAD_CHARS.search(value) or privacy.redact(value, owner=True) != value:
        raise ValueError("bad_text")
    return value.strip()


def event(value):
    out = {"title": text(value.get("title"), 300), "note": text(value.get("note", ""), 1000),
           "source": text(value.get("source", ""), 200), "all_day": value.get("all_day", False),
           "reminder": value.get("reminder")}
    if not out["title"] or type(out["all_day"]) is not bool:
        raise ValueError("bad_event")
    reminder = out["reminder"]
    if reminder is not None and (type(reminder) is not int or not 0 <= reminder <= 40320):
        raise ValueError("bad_reminder")
    start, end = value.get("start"), value.get("end")
    if out["all_day"]:
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start):
                raise ValueError()
            a = dt.date.fromisoformat(start)
            end = end or (a + dt.timedelta(days=1)).isoformat()
            b = dt.date.fromisoformat(end)
            if len(end) != 10 or b <= a:
                raise ValueError()
        except (TypeError, ValueError, OverflowError):
            raise ValueError("bad_time") from None
    else:
        a = instant(start)
        end = end or (a + dt.timedelta(hours=1)).isoformat()
        if instant(end) <= a:
            raise ValueError("bad_time")
    out.update(start=start, end=end)
    return out


def load(root):
    c = cards.find(root, "calendar")
    if c is None:
        raise ValueError("not_found")
    raw, issue, cut = cards._read(root, c)
    if issue not in (None, "empty") or cut:
        raise ValueError("refused")
    raw = raw or b''
    try:
        doc = json.loads(raw) if raw else {"schema": 1, "events": [], "subscriptions": {}}
        if not isinstance(doc, dict) or doc.get("schema") != 1 or not isinstance(doc.get("events"), list) or len(doc["events"]) > MAX_EVENTS or not isinstance(doc.get("subscriptions", {}), dict):
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("bad_data") from None
    return doc, hashlib.sha256(raw).hexdigest()


def safe_rows(doc):
    rows = []
    seen = set()
    for raw in doc["events"]:
        try:
            ident = raw.get("id")
            if not isinstance(ident, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", ident) or ident in seen or privacy.redact(ident, owner=True) != ident:
                raise ValueError()
            clean = event(raw)
            rows.append({**clean, "id": ident, "readonly": bool(raw.get("subscription") or raw.get("readonly"))})
            seen.add(ident)
        except (ValueError, AttributeError, TypeError):
            # Never silently drop malformed records on write. Read shows only validated records.
            continue
    return rows


def todo_rows(root):
    from . import jcards_todo as todo
    rows = []
    for item in todo.listing(root, "list").get("items", []):
        if item.get("status") in todo.CLOSED:
            continue
        at = next((c["at"] for c in (item.get("start"), item.get("end")) if isinstance(c, dict) and c.get("at")), None)
        if not at:
            continue
        try:
            clean = event({"title": item["text"], "start": at, "all_day": len(at) == 10, "source": "card/todo#" + item["id"]})
            rows.append({**clean, "id": "todo-" + item["id"], "readonly": True})
        except (ValueError, KeyError):
            continue
    return rows


def listing(root, op="list", clock=None):
    try:
        from . import jcards_cal_undo as rm
        doc, revision = load(root)
        rows = rm.flags(doc, safe_rows(doc)) + [{**r, "remote": None, "pending": False, "conflict": False} for r in todo_rows(root)]
        clock = clock or now()
        if op == "due":
            day = clock.astimezone().date().isoformat()
            def due(e):
                if e["all_day"]:
                    start = dt.datetime.combine(dt.date.fromisoformat(e["start"]), dt.time()).astimezone()
                    return e["start"] <= day < e["end"] or start - dt.timedelta(minutes=e["reminder"] or 0) <= clock < start
                a, b = instant(e["start"]), instant(e["end"])
                return (a.astimezone().date() == clock.astimezone().date() or a - dt.timedelta(minutes=e["reminder"] or 0) <= clock < b) and b > clock
            rows = [e for e in rows if due(e)]
        rows.sort(key=lambda e: (e["start"][:10], "" if e["all_day"] else instant(e["start"]).astimezone(dt.timezone.utc).isoformat(), e["id"]))
        return {"ok": True, "id": "calendar", "revision": revision, "events": rows, "subscriptions": rm.sub_rows(doc),
                "conflicts": rm.conflict_rows(doc), "undo": len(doc.get("history", []))}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {"ok": False, "why": "bad_data", "events": []}


def transaction(root, update, revision=None):
    try:
        with cards._dirfd(root, ("cards-data", "calendar"), create=True) as d:
            fd = os.open(".calendar.lock", os.O_CREAT | os.O_RDWR | os.O_NONBLOCK | cards._NOFOLLOW, 0o600, dir_fd=d)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise ValueError("refused")
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX)
                doc, rev = load(root)
                if revision is not None and revision != rev:
                    raise ValueError("conflict")
                update(doc)
                if len(doc["events"]) > MAX_EVENTS:
                    raise ValueError("full")
                body = json.dumps(doc, ensure_ascii=False).encode()
                if len(body) > cards.DATA_MAX:
                    raise ValueError("full")
                if load(root)[1] != rev:
                    raise ValueError("conflict")
                cards._write_json(root, ("cards-data", "calendar"), "calendar.json", doc)
            finally:
                os.close(fd)
        return listing(root)
    except (OSError, ValueError) as exc:
        return {"ok": False, "why": str(exc) if isinstance(exc, ValueError) else "refused"}
    except (KeyError, TypeError, AttributeError):       # a hand-edited history / outbox row: refuse, write nothing
        return {"ok": False, "why": "bad_data"}


def change(root, op, entry=None, revision=None, to=None, **values):
    """P428: every change is journaled (undo) and, for an event of a two-way remote, queued (jcards_cal_remote.record)."""
    if op not in ("add", "edit", "delete") or set(values) - FIELDS:
        return {"ok": False, "why": "bad_op"}
    def update(doc):
        from . import jcards_cal_undo as rm
        if op == "add":
            new = {**event(values), "id": "e-" + uuid.uuid4().hex}
            name = rm.target(doc, to)
            if name:
                new["remote"] = name
            doc["events"].append(new)
            return rm.record(doc, None, new)
        old = next((e for e in doc["events"] if isinstance(e, dict) and e.get("id") == entry), None)
        if old is None:
            raise ValueError("not_found")
        if old.get("subscription") or old.get("readonly"):
            raise ValueError("readonly")
        before = json.loads(json.dumps(old))
        if op == "delete":
            doc["events"].remove(old)
            return rm.record(doc, before, None)
        old.update(event({**old, **values}))
        rm.record(doc, before, old)
    return transaction(root, update, revision)


def add_parser(ps):
    x = ps.add_parser("cal", help="Local calendar / 本地日历")
    x.add_argument("op", choices=("add", "edit", "delete", "list", "due", "subscribe", "unsubscribe", "sync", "undo", "resolve",
                                  "queue", "connect", "disconnect"))
    for field in ("entry", "revision", "title", "start", "end", "note", "source", "name", "key"):
        x.add_argument("--" + field)
    x.add_argument("--to", metavar="NAME", help="add：同时写到这个双向日历 / add: also put it on this two-way calendar")
    x.add_argument("--all-day", action="store_true", default=None)
    x.add_argument("--timed", action="store_false", dest="all_day", default=None)
    x.add_argument("--reminder", type=int)
    x.add_argument("--clear-reminder", action="store_true")
    x.add_argument("--keep", choices=("local", "remote"), help="resolve：local 留我的版本，remote 留 Google/iCloud 那边的 / resolve: local keeps mine, remote keeps theirs")
    x.add_argument("--kind", choices=("google", "caldav"), help="connect：双向日历的种类 / connect: the two-way calendar kind")
    x.add_argument("--calendar", help="connect：哪个日历，Google 默认 primary，CalDAV 填日历名 / connect: which calendar (Google id, default primary; CalDAV display name)")
    x.add_argument("--server", help="connect --kind caldav：https 服务器地址 / connect --kind caldav: the https server URL")
    x.add_argument("--user-key", metavar="NAME", help="connect --kind caldav：存账号的密钥卡名 / connect --kind caldav: key card name holding the user")
    x.add_argument("--pass-key", metavar="NAME", help="connect --kind caldav：存 App 专用密码的密钥卡名 / connect --kind caldav: key card name holding the app-specific password")
    x.add_argument("--push-new", action="store_true", help="connect：新加的事件也写过去 / connect: new events go there too")
    x.add_argument("--auto", action="store_true", help="connect：每 15 分钟自动同步 / connect: sync every 15 minutes")
    x.add_argument("--json", action="store_true")


def command(a, root):
    from . import jcards_cal_undo as rm
    if a.op in ("list", "due"):
        result = listing(root, a.op)
    elif a.op == "undo":
        result = rm.undo(root, a.entry, a.revision)
    elif a.op == "resolve":
        result = rm.resolve(root, a.entry, a.keep, a.revision)
    elif a.op == "queue":
        result = rm.queue_view(root)
    elif a.op in ("connect", "disconnect", "unsubscribe", "sync") and rm.is_two_way(root, a.name, a.op):
        from . import jcards_cal_remote
        result = jcards_cal_remote.cli(a, root)
    elif a.op == "sync" and rm.unknown(root, a.name):
        result = {"ok": False, "why": "not_found"}             # D6: no such calendar (an existing ICS one that is off: disabled)
    elif a.op in ("subscribe", "unsubscribe", "sync"):
        from . import jcards_cal_sync
        result = jcards_cal_sync.command(root, a.op, a.name, a.key)
    else:
        values = {k: getattr(a, k) for k in FIELDS if getattr(a, k) is not None}
        if a.clear_reminder:
            values["reminder"] = None
        result = change(root, a.op, a.entry, a.revision, a.to, **values)
    if a.op in ("add", "edit", "delete", "undo", "resolve"):
        from . import jcards_cal_remote
        jcards_cal_remote.cli_kick(root, result)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1
