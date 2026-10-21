"""P428 (CALENDAR-SYNC-SPEC §4): two-way calendar sync — one engine for every two-way kind, Google through our pinned gog.

`sync(root, name, adapter)` pulls outside the calendar lock, merges under `transaction(revision)` (three-way by `base` hash +
etag; remote wins with the local version saved as a conflict; duplicates linked), then flushes the outbox in order. An adapter
has four calls (`list(lo, hi)`, `create(ev)`, `update(rid, etag, ev)`, `delete(rid, etag)`) and raises `RemoteError(word)`;
CalDAV (jcards_cal_caldav) is ADAPTERS' second entry. Remote content is data, never instructions; only counts and fixed words
leave `sync()`.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import re
import time

from . import jcards_cal as cal, privacy
from .jcards_cal_undo import (CONFLICTS, TWO_WAY, WORDS, _fields, _find, _iso, queue, resolve, sub_rows, two_way, undo)

MAX_SUBS = 8
RID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.@-]{0,1023}")
CALID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.@+-]{0,199}")
DAV = ("server", "user_key", "pass_key")              # caldav connect fields: a URL and two key-card NAMES
PAST, AHEAD = dt.timedelta(days=30), dt.timedelta(days=365)
SYNCED = ("title", "start", "end", "all_day", "note", "reminder")     # F5: the fields with a per-field base (`basef`)
# F10: every word that may leave host_sync / connect / the elevate log; anything else (an exception's text) → "failed".
FIXED = WORDS + ("full", "too_soon", "not_found", "disabled", "bad_data", "refused", "exists", "bad_name", "bad_kind",
                 "bad_server", "server_not_allowed", "bad_key_name", "key_missing", "bad_calendar", "no_calendar",
                 "not_supported", "google_not_connected", "sync_failed")


def fixed(word) -> str:
    return word if isinstance(word, str) and word in FIXED else "failed"


ROWRID = re.compile(r"[^\x00-\x20\x7f-\x9f\u2028\u2029\\]{1,1024}")  # N1: a remote id kept in calendar.json (Google id / CalDAV path)
ROWTAG = re.compile(r"[\x20-\x7e]{0,200}")                     # N1: an etag kept there: printable ASCII, never a line break


def tag(v) -> str:
    """A remote etag as stored: ≤ 200 printable ASCII characters, else "" (the next pull rebases it)."""
    v = str(v or "")[:200]
    return v if ROWTAG.fullmatch(v) else ""


def outgoing(ev: dict) -> dict:
    """N1: calendar.json lives under agentperm/ (the Agent can write it), so before ANY adapter create / update the event is
    rebuilt from its seven card fields through `cal.event()` — never a raw row — keeping only id / remote / rid / etag /
    rdefault, with the rid / etag shapes checked. → the event to send; ValueError("bad_data") when the row is not valid."""
    try:
        out = {**cal.event({k: ev[k] for k in cal.FIELDS if k in ev}), "id": ev.get("id"), "remote": ev.get("remote")}
    except (ValueError, TypeError, AttributeError):
        raise ValueError("bad_data") from None
    for k, shape in (("rid", ROWRID), ("etag", ROWTAG)):
        if ev.get(k) is not None:
            if not (isinstance(ev[k], str) and shape.fullmatch(ev[k])):
                raise ValueError("bad_data")
            out[k] = ev[k]
    if ev.get("rdefault"):
        out["rdefault"] = True
    return out


def sendable(ev: dict) -> dict:
    """Adapter side of N1 (create / update): the same rebuild; a bad row is a `failed` remote call, nothing is sent."""
    try:
        return outgoing(ev)
    except ValueError:
        raise RemoteError("failed") from None


class RemoteError(Exception):
    def __init__(self, word: str):
        super().__init__(word if word in WORDS else "failed")
        self.word = str(self)


def _norm(v, day: bool) -> str:
    return v if day else cal.instant(v).astimezone(dt.timezone.utc).isoformat()


def canon(e: dict) -> list:
    day = bool(e.get("all_day"))
    return [e.get("title"), day, _norm(e["start"], day), _norm(e["end"], day), e.get("reminder"), e.get("note") or ""]


def digest(e: dict) -> str:
    return hashlib.sha256(json.dumps(canon(e), ensure_ascii=False).encode()).hexdigest()


def fhash(e: dict, f: str) -> str:
    day = bool(e.get("all_day"))
    v = _norm(e[f], day) if f in ("start", "end") else (e.get("note") or "") if f == "note" else e.get(f)
    return hashlib.sha256(json.dumps([f, v], ensure_ascii=False).encode()).hexdigest()[:16]


def basef(e: dict) -> dict:
    return {f: fhash(e, f) for f in SYNCED}


def changed_fields(ev: dict):
    """F5: the fields the owner changed since the last sync (never a lossy one they did not touch); None = no per-field
    base (a row linked before P428 F5): every field goes, as before."""
    bf = ev.get("basef")
    if not isinstance(bf, dict):
        return None
    lossy = set(ev.get("lossy") or ())
    return [f for f in SYNCED if fhash(ev, f) != bf.get(f) and f not in lossy]


# ------------------------------------------------------------------ the sync engine (§4)
def _in_window(e: dict, lo, hi) -> bool:
    if e.get("all_day"):
        return e["end"] > lo.date().isoformat() and e["start"] < hi.date().isoformat()
    return cal.instant(e["end"]) > lo and cal.instant(e["start"]) < hi


def _dup_key(e: dict) -> tuple:
    day = bool(e.get("all_day"))
    return (str(e.get("title", "")).strip().casefold(), day, _norm(e["start"], day), _norm(e["end"], day))


def _linked(r: dict, name: str, ident: str, old: dict | None = None) -> dict:
    """A row of this remote from its pulled version; a replaced local row keeps its own `source` and `conflict`."""
    ev = {**_fields(r), "id": ident, "remote": name, "rid": r["rid"], "etag": r["etag"], "base": digest(r), "basef": basef(r)}
    for k in ("readonly", "rdefault"):
        if r.get(k):
            ev[k] = True
    lossy = [f for f in r.get("lossy") or () if f in SYNCED]
    if lossy:
        ev["lossy"] = lossy                                     # redacted / truncated remote text: never written back as is
    for k in ("source", "conflict"):
        if (old or {}).get(k):
            ev[k] = old[k]
    return ev


def _save_conflict(doc: dict, ident, name, local, state: str, lossy=None) -> None:
    rows = [c for c in doc.setdefault("conflicts", []) if c.get("id") != ident]
    rows.append({"id": ident, "remote": name, "at": _iso(), "local": local, "remote_state": state,
                 **({"lossy": list(lossy)} if lossy else {})})
    doc["conflicts"] = rows[-CONFLICTS:]


def _drop(doc: dict, ident, name) -> None:
    doc["outbox"] = [o for o in doc.get("outbox", []) if not (o.get("id") == ident and o.get("remote") == name)]


def _merge(doc: dict, name: str, items: list, seen: set, lo, hi, res: dict) -> None:
    """Step 2 + 3 under the calendar lock: three-way compare by `base` + etag, remote deletions, pending deletes, duplicates."""
    remote = {r["rid"]: r for r in items}
    for o in [o for o in doc.get("outbox", []) if o.get("remote") == name and o.get("op") == "delete"]:
        r = remote.pop(o.get("rid"), None)
        if o.get("rid") not in seen:
            _drop(doc, o["id"], name)                          # already gone remotely: nothing left to push
        elif r is not None and r["etag"] != o.get("etag") and not r.get("readonly"):
            _drop(doc, o["id"], name)                          # remote changed what we deleted: remote version back
            doc["events"].append({**_linked(r, name, o["id"]), "conflict": True})
            _save_conflict(doc, o["id"], name, None, "changed")
    for L in [e for e in doc["events"] if isinstance(e, dict) and e.get("remote") == name and e.get("rid")]:
        r = remote.pop(L["rid"], None)
        try:
            changed = digest(L) != L.get("base")
        except (ValueError, KeyError, TypeError, AttributeError):
            continue                                           # N1: not a valid event (hand / Agent edited): left alone
        if r is None:
            if L["rid"] in seen or not _in_window(L, lo, hi):
                continue                                       # unreadable remote row / outside the window: untouched
            if not changed or L.get("readonly"):
                doc["events"].remove(L)
                _drop(doc, L["id"], name)
            else:
                _drop(doc, L["id"], name)
                for k in ("rid", "etag", "base", "basef"):
                    L.pop(k, None)
                L["conflict"] = True
                _save_conflict(doc, L["id"], name, _fields(L), "deleted")
            continue
        if r["etag"] == L.get("etag") and not L.get("readonly"):
            continue                                           # only local changed (the outbox pushes it) or nothing
        at = doc["events"].index(L)
        if not changed or L.get("readonly") or canon(L) == canon(r):
            doc["events"][at] = _linked(r, name, L["id"], L)   # only remote changed, or both and equal → rebase
            _drop(doc, L["id"], name)
            continue
        _save_conflict(doc, L["id"], name, _fields(L), "changed", L.get("lossy"))   # both, different: remote wins, local kept
        _drop(doc, L["id"], name)
        doc["events"][at] = {**_linked(r, name, L["id"], L), "conflict": True}
    free = {}
    for e in doc["events"]:
        if isinstance(e, dict) and not e.get("remote") and not e.get("subscription") and not e.get("readonly"):
            try:
                free.setdefault(_dup_key(e), e)
            except (ValueError, KeyError, TypeError):
                continue
    for r in remote.values():
        e = free.pop(_dup_key(r), None)
        if e is not None:                                       # the same event on both sides: link, no second copy
            at = doc["events"].index(e)
            doc["events"][at] = _linked(r, name, e["id"], e)    # F5: the card takes the remote values, nothing is pushed
            res["linked_duplicates"] += 1
            if canon(e)[1:] != canon(r)[1:]:                    # note / reminder differ: the owner's version kept aside
                doc["events"][at]["conflict"] = True
                _save_conflict(doc, e["id"], name, _fields(e), "changed")
            continue
        doc["events"].append(_linked(r, name, "r-" + hashlib.sha256((name + "\n" + r["rid"]).encode()).hexdigest()[:32]))


def _bump(root, name, word: str, ident=None) -> None:
    def update(doc):
        for o in doc.get("outbox", []):
            if o.get("remote") == name and (ident is None or o.get("id") == ident):
                o["tries"] = int(o.get("tries") or 0) + 1
                o["error"] = word
        if two_way(doc, name) is not None:
            doc["subscriptions"][name]["last_error"] = word
    cal.transaction(root, update)


def _push(root, name: str, adapter, o: dict) -> str | None:
    """One outbox op outside the lock, then its bookkeeping under it. Returns a stop word or None."""
    doc, _ = cal.load(root)
    ev = _find(doc, o["id"])
    key = (o["id"], o["op"], o["at"])
    def mine(d):
        return next((x for x in d.get("outbox", []) if (x.get("id"), x.get("op"), x.get("at")) == key), None)
    def done(d):
        x = mine(d)
        if x is not None:
            d["outbox"].remove(x)
    def refuse(d):                                     # N1: a row that is not a valid event: its op dropped, row flagged
        done(d)
        cur = _find(d, o["id"])
        if cur is not None:
            cur["conflict"] = True
    try:
        if o["op"] == "delete":
            if not o.get("rid"):
                cal.transaction(root, done)
                return "skip"
            if not (isinstance(o["rid"], str) and ROWRID.fullmatch(o["rid"])) \
                    or not (o.get("etag") is None or isinstance(o["etag"], str) and ROWTAG.fullmatch(o["etag"])):
                cal.transaction(root, done)                    # N1: a tampered delete op: nothing sent
                return "skip"
            try:
                adapter.delete(o["rid"], o.get("etag"))
            except RemoteError as e:
                if e.word != "gone":
                    raise
            cal.transaction(root, done)
            return None
        if ev is None or ev.get("readonly"):
            cal.transaction(root, done)                        # nothing to send any more
            return "skip"
        try:
            send = outgoing(ev)                                # N1: never a raw row to an adapter
        except ValueError:
            cal.transaction(root, refuse)
            return "skip"
        if o["op"] == "update" and (not ev.get("rid") or digest(ev) == ev.get("base")):
            cal.transaction(root, done)
            return "skip"
        sent = digest(ev)
        fields = changed_fields(ev) if o["op"] == "update" else None
        if fields == []:                                       # only untouched lossy text differs: settle, send nothing
            def settle(d):
                cur = _find(d, o["id"])
                if cur is not None and digest(cur) == sent:
                    cur["base"] = sent
                    done(d)
            cal.transaction(root, settle)
            return "skip"
        if o["op"] == "create":
            got = adapter.create(send)
            def stored(d):
                done(d)
                cur = _find(d, o["id"])
                if cur is None:                                # deleted while we were creating it: delete it too
                    queue(d, "delete", o["id"], name, got["rid"], got.get("etag"))
                    return
                cur.update(rid=got["rid"], etag=got.get("etag", ""), base=sent, basef=basef(ev))
                if digest(cur) != sent:
                    queue(d, "update", o["id"], name)
        else:
            try:
                got = adapter.update(send["rid"], send.get("etag"), send, fields)
            except RemoteError as e:
                if e.word != "gone":
                    raise
                def lost(d):                                   # the remote copy is gone: keep ours, ask the owner
                    done(d)
                    cur = _find(d, o["id"])
                    if cur is not None:
                        for k in ("rid", "etag", "base", "basef"):
                            cur.pop(k, None)
                        cur["conflict"] = True
                        _save_conflict(d, o["id"], name, _fields(cur), "deleted")
                cal.transaction(root, lost)
                return "skip"
            bf = {**(ev.get("basef") or {}), **{f: fhash(ev, f) for f in (SYNCED if fields is None else fields)}}
            def stored(d):
                cur = _find(d, o["id"])
                if cur is not None:
                    cur.update(etag=got.get("etag", cur.get("etag")), base=sent, basef=bf)
                    if digest(cur) == sent:
                        done(d)
        cal.transaction(root, stored)
        return None
    except RemoteError as e:
        if e.word == "conflict":                               # 412: the next pull settles it (step 2)
            def mark(d):
                x = mine(d)
                if x is not None:
                    x["error"] = "conflict"
            cal.transaction(root, mark)
            return "skip"
        _bump(root, name, e.word, o["id"])
        return e.word if e.word in ("offline", "auth") else "failed"


def sync(root, name, adapter, clock=None) -> dict:
    """§4: pull outside the lock → merge under transaction(revision) (one retry) → flush the outbox in order → counts."""
    res = {"ok": True, "name": name, "pulled": 0, "pushed": 0, "queued": 0, "conflicts": 0, "linked_duplicates": 0}
    try:
        doc, rev = cal.load(root)
    except (OSError, ValueError):
        return {**res, "ok": False, "why": "bad_data"}
    sub = two_way(doc, name)
    if sub is None or not sub.get("enabled"):
        return {**res, "ok": False, "why": "not_found" if sub is None else "disabled"}
    t = clock or cal.now()
    lo, hi = t - PAST, t + AHEAD
    word = None
    try:
        raw = adapter.list(lo, hi)
    except RemoteError as e:
        word = e.word
    if word is None:
        seen, items = set(), []
        for r in raw:
            if isinstance(r, dict) and isinstance(r.get("rid"), str):
                if r["rid"] not in seen and r.get("ok", True):
                    items.append(r)
                seen.add(r["rid"])
        res["pulled"] = len(items)
        for attempt in (0, 1):
            res["linked_duplicates"] = 0
            out = cal.transaction(root, lambda d: _merge(d, name, items, seen, lo, hi, res), rev)
            if out.get("ok") or out.get("why") != "conflict" or attempt:
                break
            rev = cal.load(root)[1]
        if not out.get("ok"):
            word = out.get("why") if out.get("why") == "full" else "failed"
            res["linked_duplicates"] = 0
        else:
            tried, failed = set(), False
            while word is None:
                doc, _ = cal.load(root)
                o = next((x for x in doc.get("outbox", []) if x.get("remote") == name
                          and (x.get("id"), x.get("op"), x.get("at")) not in tried), None)
                if o is None:
                    break
                tried.add((o.get("id"), o.get("op"), o.get("at")))
                got = _push(root, name, adapter, o)
                if got is None:
                    res["pushed"] += 1
                elif got == "failed":
                    failed = True                              # this op stays queued (tries+1); the others still go
                elif got != "skip":
                    word = got
            word = word or ("failed" if failed else None)
    elif word in ("offline", "auth"):
        _bump(root, name, word)
    if word == "auth" and hasattr(adapter, "auth_failed"):
        adapter.auth_failed()
    def note(d):
        s = two_way(d, name)
        if s is not None:
            if word is None:
                s["last_sync"] = _iso()
                s.pop("last_error", None)
            else:
                s["last_error"] = word if word in WORDS + ("full",) else "failed"
    cal.transaction(root, note)
    doc, _ = cal.load(root)
    res["queued"] = sum(1 for o in doc.get("outbox", []) if o.get("remote") == name)
    res["conflicts"] = sum(1 for c in doc.get("conflicts", []) if c.get("remote") == name)
    if word is not None:
        res.update(ok=False, why=word, **({"offline": True} if word == "offline" else {}))
    return res


# ------------------------------------------------------------------ Google through our pinned gog only
def classify(code: int, body) -> str:
    err = body.get("error") if isinstance(body, dict) else None
    txt = json.dumps(err if err is not None else body or "").lower()
    if any(w in txt for w in ("invalid_grant", "401", "unauthenticated")):
        return "auth"
    if code in (-2, 7, 8) or any(w in txt for w in ("timeout", "timed out", "dial tcp", "no such host", "connection refused",
                                                    "network is unreachable", "connection reset", "backenderror", " 500",
                                                    " 502", " 503", " 504", "unavailable")):
        return "offline"
    if code in (4, 10) or "missing --account" in txt:
        return "auth"
    if code == 5 or any(w in txt for w in ("404", "410", "notfound", "not found", "deleted")):
        return "gone"
    if "412" in txt or "precondition" in txt:
        return "conflict"
    return "failed"


def google_item(it) -> dict | None:
    if not isinstance(it, dict) or not isinstance(it.get("id"), str) or not RID.fullmatch(it["id"]):
        return None
    if it.get("status") == "cancelled":
        return None
    def txt(v, n):                                     # → (card text, lossy: redaction / truncation / clean-up changed it)
        raw = v if isinstance(v, str) else ""
        out = privacy.redact(privacy.redact(cal.clean(raw), owner=True)[:n], owner=True)[:n].strip()
        return out, out != raw.strip()
    s, e = it.get("start") if isinstance(it.get("start"), dict) else {}, it.get("end") if isinstance(it.get("end"), dict) else {}
    day = isinstance(s.get("date"), str)
    rem = it.get("reminders") if isinstance(it.get("reminders"), dict) else {}
    mins = [o.get("minutes") for o in rem.get("overrides") or [] if isinstance(o, dict) and o.get("method") == "popup"
            and type(o.get("minutes")) is int and 0 <= o["minutes"] <= 40320]
    out = {"rid": it["id"], "etag": tag(it.get("etag")), "ok": False}
    (title, lt), (note, ln) = txt(it.get("summary"), 300), txt(it.get("description"), 1000)
    try:
        out.update(cal.event({"title": title or "(no title)", "note": note,
                              "all_day": day, "start": s.get("date" if day else "dateTime"),
                              "end": e.get("date" if day else "dateTime"), "reminder": min(mins) if mins else None}), ok=True)
    except ValueError:
        return out                                     # seen, not shown: a linked row is never deleted for it
    lossy = [f for f, bad in (("title", lt or not title), ("note", ln)) if bad]
    if lossy:
        out["lossy"] = lossy
    if it.get("recurringEventId") or it.get("recurrence"):
        out["readonly"] = True
    if rem.get("useDefault", "reminders" not in it):
        out["rdefault"] = True
    return out


class Google:
    """Every call is `google.gog_calendar` (exact command list, -a auto, --no-input); user values always `--flag=VALUE`."""
    kind = "google"

    def __init__(self, st, sub: dict, run=None):
        from . import google
        self.st, self.cal = st, sub.get("calendar") or "primary"
        if not CALID.fullmatch(self.cal):
            raise ValueError("bad_calendar")
        self.run = run or (lambda argv, timeout: google.gog_calendar(st, argv, timeout))

    def _call(self, argv: list, timeout: float = 60):
        code, body = self.run(argv, timeout)
        if code == 0 and not (isinstance(body, dict) and "error" in body):
            return body
        raise RemoteError(classify(code, body))

    @staticmethod
    def _rid(rid) -> str:
        if not isinstance(rid, str) or not RID.fullmatch(rid):
            raise RemoteError("failed")
        return rid

    @staticmethod
    def _event(body) -> dict:
        ev = body.get("event") if isinstance(body, dict) and isinstance(body.get("event"), dict) else body
        if not isinstance(ev, dict) or not isinstance(ev.get("id"), str) or not RID.fullmatch(ev["id"]):
            raise RemoteError("failed")
        return {"rid": ev["id"], "etag": tag(ev.get("etag"))}

    @staticmethod
    def _flags(ev: dict, new: bool, fields=None) -> list:
        """F5: only the changed fields' flags (None = all); an unchanged reminder is never touched (e-mail reminders stay)."""
        f = set(SYNCED if fields is None else fields)
        t = (lambda v: dt.date.fromisoformat(v).isoformat()) if ev.get("all_day") \
            else (lambda v: cal.instant(v).isoformat())        # gog wants full RFC 3339; N1: parsed, never the raw text
        a = ["--summary=" + ev["title"]] if "title" in f else []
        if f & {"start", "end", "all_day"}:
            a += ["--from=" + t(ev["start"]), "--to=" + t(ev["end"])] + (["--all-day"] if ev.get("all_day") else [])
        if "note" in f:
            a.append("--description=" + (ev.get("note") or ""))
        if "reminder" in f:
            if ev.get("reminder") is not None:
                a.append("--reminder=popup:%dm" % ev["reminder"])
            elif new or not ev.get("rdefault"):
                a.append("--no-reminders")
        return a

    def list(self, lo, hi) -> list:
        body = self._call(["calendar", "events", self.cal, "--from=" + lo.isoformat(), "--to=" + hi.isoformat(),
                           "--all-pages", "--max", "250"], 180)
        rows = body if isinstance(body, list) else next((body[k] for k in ("items", "events") if isinstance(body, dict)
                                                         and isinstance(body.get(k), list)), None)
        if rows is None:
            raise RemoteError("failed")
        return [r for r in map(google_item, rows) if r is not None]

    def create(self, ev: dict) -> dict:
        return self._event(self._call(["calendar", "create", self.cal, *self._flags(sendable(ev), True)]))

    def update(self, rid, etag, ev: dict, fields=None) -> dict:
        return self._event(self._call(["calendar", "update", self.cal, self._rid(rid),
                                       *self._flags(sendable(ev), False, fields), "--send-updates=none"]))

    def delete(self, rid, etag) -> None:
        self._call(["calendar", "delete", self.cal, self._rid(rid), "-y", "--send-updates=none"])

    def auth_failed(self) -> None:
        from . import google_connect
        google_connect._set(self.st, "calendar-write", "oauth_consent_required")


def _caldav(st, sub: dict):
    from .jcards_cal_caldav import CalDAV
    return CalDAV(st, sub)


ADAPTERS = {"google": Google, "caldav": _caldav}


def adapter_for(st, sub: dict):
    make = ADAPTERS.get(sub.get("kind"))
    if make is None:
        raise ValueError("not_supported")
    return make(st, sub)


# ------------------------------------------------------------------ connect / disconnect / the host side
def connect(st, kind, name, calendar=None, push_new=False, auto=False, server=None, user_key=None, pass_key=None) -> dict:
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]{1,31}", name):
        return {"ok": False, "why": "bad_name"}
    if kind not in TWO_WAY:
        return {"ok": False, "why": "bad_kind"}
    if kind == "caldav":                                       # names checked, values read, discovery run (§5)
        from . import jcards_cal_caldav as dav
        try:
            extra = dav.setup(st, server, user_key, pass_key, calendar)
        except RemoteError as e:
            return {"ok": False, "name": name, "why": e.word}
        except ValueError as e:                                # F1 / F10: bad_key_name, server_not_allowed, … — fixed words
            return {"ok": False, "name": name, "why": fixed(e.args[0] if e.args else None)}
    elif calendar is not None and not (isinstance(calendar, str) and CALID.fullmatch(calendar)):
        return {"ok": False, "why": "bad_calendar"}
    else:
        from . import google
        conn = (google.connect_status(st, "calendar-write").get("connections") or {}).get("calendar-write") or {}
        if conn.get("step") != "ready":
            return {"ok": False, "name": name, "why": "google_not_connected",
                    "hint": "agentj google connect --purpose calendar-write"}
    def update(doc):
        subs = doc.setdefault("subscriptions", {})
        if name in subs and subs[name].get("kind") != kind:
            raise ValueError("exists")
        if name not in subs and len(subs) >= MAX_SUBS:
            raise ValueError("full")
        old = subs.get(name) or {}
        where = extra if kind == "caldav" else {"calendar": calendar or old.get("calendar") or "primary"}
        subs[name] = {"kind": kind, "enabled": True, **where, "push_new": bool(push_new), "auto": bool(auto),
                      **{k: old[k] for k in ("last_sync", "last_error") if k in old}}
    out = cal.transaction(st.root, update)
    return {"ok": True, "name": name, "connected": True} if out.get("ok") else {"ok": False, "name": name, "why": fixed(out["why"])}


def disconnect(root, name) -> dict:
    """Stops syncing: queued ops and conflicts dropped; pushed / pulled events stay as plain local events (read-only
    recurring instances go, they belong to the remote)."""
    def update(doc):
        if two_way(doc, name) is None:
            raise ValueError("not_found")
        doc["subscriptions"].pop(name)
        keep = []
        for e in doc["events"]:
            if isinstance(e, dict) and e.get("remote") == name:
                if e.get("readonly"):
                    continue
                for k in ("remote", "rid", "etag", "base", "basef", "lossy", "conflict", "rdefault"):
                    e.pop(k, None)
            keep.append(e)
        doc["events"] = keep
        doc["outbox"] = [o for o in doc.get("outbox", []) if o.get("remote") != name]
        doc["conflicts"] = [c for c in doc.get("conflicts", []) if c.get("remote") != name]
    out = cal.transaction(root, update)
    return {"ok": True, "name": name, "disconnected": True} if out.get("ok") else {"ok": False, "name": name, "why": fixed(out["why"])}


def host_sync(st, name) -> dict:
    try:
        doc, _ = cal.load(st.root)
        sub = two_way(doc, name)
        if sub is None:
            return {"ok": False, "name": name, "why": "not_found"}
        return sync(st.root, name, adapter_for(st, sub))
    except ValueError as e:                                    # F10: our own words only, never an exception's text
        return {"ok": False, "name": name, "why": fixed(e.args[0] if e.args else None)}
    except Exception:
        return {"ok": False, "name": name, "why": "failed"}


def enabled(root, name) -> str | None:
    """None when `name` is an enabled two-way remote, else the refusal word (F8: kick / sync only for those)."""
    try:
        sub = two_way(cal.load(root)[0], name)
    except (OSError, ValueError):
        return "bad_data"
    return None if sub is not None and sub.get("enabled") else "not_found" if sub is None else "disabled"


def request_shape(req: dict) -> str | None:
    """elevate.sock `{"t":"calsync","op":"sync"|"kick"|"disconnect","name"}` ·
    `{"t":"calsync","op":"connect","name","kind","calendar"?,"push_new"?,"auto"?}` (+ `server`, `user_key`, `pass_key` for
    caldav: a URL and two key-card NAMES, never a value)."""
    op = req.get("op")
    allowed = {"t", "op", "name"} | ({"kind", "calendar", "push_new", "auto"} if op == "connect" else set()) \
        | (set(DAV) if op == "connect" and req.get("kind") == "caldav" else set())
    if op not in ("sync", "kick", "connect", "disconnect"):
        return "op: sync | kick | connect | disconnect"
    if set(req) - allowed or not isinstance(req.get("name"), str) or not re.fullmatch(r"[a-z][a-z0-9-]{1,31}", req["name"]):
        return "name: a remote name; fixed fields"
    if op == "connect" and (req.get("kind") not in TWO_WAY or any(k in req and type(req[k]) is not bool for k in ("push_new", "auto"))
                            or not (req.get("calendar") is None or isinstance(req["calendar"], str))
                            or any(k in req and not isinstance(req[k], str) for k in DAV)):
        return "kind: google | caldav; push_new / auto: booleans; calendar: a string"
    return None


def host_op(st, req: dict) -> dict:
    """Synchronous host side (in-process CLI fallback and the body of the elevate request)."""
    if req["op"] == "connect":
        res = connect(st, req["kind"], req["name"], req.get("calendar"), req.get("push_new", False), req.get("auto", False),
                      *(req.get(k) for k in DAV))
        if res.get("ok"):
            res["sync"] = host_sync(st, req["name"])
        return res
    if req["op"] == "disconnect":
        return disconnect(st.root, req["name"])
    return host_sync(st, req["name"])


class Runner:
    """serve's offline-queue runner: a queued change → sync that remote after 5 s (debounced, one task per remote, but at
    most 30 s after the first waiting kick); `auto` remotes every 15 min; failures back off ×2 up to 1 h. A sync asked for
    by the phone / elevate.sock (`gap=True`) runs at most once per 30 s per remote — inside that window the last result
    comes back with why `too_soon`. State of removed remotes is dropped. Syncs run in a thread, never on the event loop."""
    DEBOUNCE, AUTO, BASE, CAP, GAP, HOLD = 5.0, 900.0, 60.0, 3600.0, 30.0, 30.0

    def __init__(self, st, run=None, sleep=None, clock=None):
        self.st, self.run = st, run or (lambda name: host_sync(st, name))
        self.sleep = sleep or asyncio.sleep
        self.clock = clock or time.monotonic
        self.timers: dict = {}
        self.fails: dict = {}
        self.locks: dict = {}
        self.first: dict = {}                          # name → when the first still-waiting debounce kick came
        self.last: dict = {}                           # name → (when, result) of the last sync
        self.connects: dict = {}                       # N4: name → when connect was last asked for (pruned after GAP)

    def kick(self, name: str, delay: float | None = None) -> None:
        old = self.timers.get(name)
        if old is not None and not old.done():
            old.cancel()
        if delay is None:                              # debounce, capped: a stream of kicks cannot postpone it forever
            t = self.clock()
            delay = max(0.0, min(self.DEBOUNCE, self.first.setdefault(name, t) + self.HOLD - t))
        else:
            self.first.pop(name, None)
        self.timers[name] = asyncio.get_running_loop().create_task(self._later(name, delay))

    def _forget(self, name: str) -> None:
        lock = self.locks.get(name)
        if lock is not None and not lock.locked():
            del self.locks[name]
        for d in (self.fails, self.first, self.last):
            d.pop(name, None)
        t = self.timers.pop(name, None)
        if t is not None and t is not asyncio.current_task() and not t.done():
            t.cancel()

    async def _later(self, name: str, delay: float) -> None:
        await self.sleep(delay)
        if self.timers.get(name) is asyncio.current_task():
            del self.timers[name]                      # a kick from now on starts a new timer, never cancels this sync
            self.first.pop(name, None)
        res, auto = await self.now(name, with_auto=True)
        if not res.get("ok") and res.get("why") in WORDS + ("sync_failed",):
            n = self.fails[name] = self.fails.get(name, 0) + 1
            if name not in self.timers:
                self.kick(name, min(self.BASE * 2 ** (n - 1), self.CAP))
            return
        self.fails.pop(name, None)
        if auto and name not in self.timers:
            self.kick(name, self.AUTO)

    async def now(self, name: str, with_auto: bool = False, gap: bool = False):
        lock = self.locks.setdefault(name, asyncio.Lock())
        async with lock:
            seen = self.last.get(name)
            if gap and seen and self.clock() - seen[0] < self.GAP:
                res = {**{k: v for k, v in seen[1].items() if k != "offline"}, "ok": False, "why": "too_soon"}
                return (res, False) if with_auto else res
            def work():
                res = self.run(name)
                try:
                    sub = two_way(cal.load(self.st.root)[0], name)
                except (OSError, ValueError):
                    sub = {}
                return res, bool((sub or {}).get("auto")), sub is not None
            res, auto, alive = await asyncio.to_thread(work)
            self.last[name] = (self.clock(), res)
        if not alive:
            self._forget(name)                         # the remote is gone: no lock / timer / result kept for it
        return (res, auto) if with_auto else res

    def connect_soon(self, name: str) -> bool:
        """N4: connect (reads two key cards, logs in) at most once per 30 s per name; True = refuse with `too_soon`."""
        t = self.clock()
        self.connects = {n: w for n, w in self.connects.items() if t - w < self.GAP}
        if name in self.connects:
            return True
        self.connects[name] = t
        return False

    def after(self, listing: dict) -> None:
        for s in listing.get("subscriptions") or []:
            if s.get("kind") in TWO_WAY and s.get("enabled") and s.get("queued"):
                self.kick(s["name"])


def runner(host, create: bool = True):
    r = getattr(host, "_calrun", None)
    if r is None and create:
        r = host._calrun = Runner(host.st)
    return r


async def start(host) -> None:
    """At serve start: nothing at all unless a two-way remote exists; queued or `auto` remotes get their first sync."""
    try:
        doc, _ = await asyncio.to_thread(cal.load, host.st.root)
    except (OSError, ValueError):
        return
    names = [s["name"] for s in sub_rows(doc) if s["kind"] in TWO_WAY and s["enabled"] and (s["auto"] or s["queued"])]
    for n in names:
        runner(host).kick(n)


def after_change(host, res: dict) -> None:
    if res.get("ok") and any(s.get("kind") in TWO_WAY and s.get("queued") for s in res.get("subscriptions") or []):
        runner(host).after(res)


async def elevate_request(host, st, req: dict) -> dict:
    bad = request_shape(req) if isinstance(req, dict) else "shape"
    if bad:
        return {"ok": False, "why": "shape", "detail": bad}
    if req["op"] in ("kick", "sync"):
        why = await asyncio.to_thread(enabled, st.root, req["name"])
        if why:                                                # F8: never a timer / lock for a name that is not a remote
            res = {"ok": False, "name": req["name"], "why": why}
        elif req["op"] == "kick":
            runner(host).kick(req["name"])
            return {"ok": True, "name": req["name"], "scheduled": True}
        else:
            res = await runner(host).now(req["name"], gap=True)
    elif req["op"] == "connect" and runner(host).connect_soon(req["name"]):
        res = {"ok": False, "name": req["name"], "why": "too_soon"}
    else:
        res = await asyncio.to_thread(host_op, st, req)
    st.log("calsync", result=req["op"], status="ok" if res.get("ok") else fixed(res.get("why")))
    return res


PHONE_OPS = ("add", "edit", "delete", "undo", "resolve", "sync")


async def phone(host, obj: dict) -> dict:
    """The paired `jcard_cal` frame: add|edit|delete|undo|resolve need the card's revision; sync takes a remote name."""
    from . import jcards
    root, op = host.st.root, obj.get("op")
    values = obj.get("event", {})
    if obj.get("id") != "calendar" or op not in PHONE_OPS or not isinstance(values, dict) \
            or (op != "sync" and not isinstance(obj.get("revision"), str)) or not (jcards.find(root, "calendar") or {}).get("on"):
        return {"ok": False, "why": "bad_op"}
    if op == "sync":
        name = obj.get("name")
        doc, _ = await asyncio.to_thread(cal.load, root)
        if not two_way(doc, name):
            return {"ok": False, "why": "not_two_way"}
        got = await runner(host).now(name, gap=True)
        return {**await asyncio.to_thread(cal.listing, root), "sync": got}
    if op == "undo":
        res = await asyncio.to_thread(undo, root, obj.get("entry"), obj["revision"])
    elif op == "resolve":
        res = await asyncio.to_thread(resolve, root, obj.get("entry"), obj.get("keep"), obj["revision"])
    else:
        res = await asyncio.to_thread(cal.change, root, op, obj.get("entry"), obj["revision"], **values)
    after_change(host, res)
    return res


def cli(a, root) -> dict:
    """`agentj card cal connect|disconnect|sync|unsubscribe --name N` for a two-way remote: serve over elevate.sock (gog and
    Google's state are outside the Agent fence); serve not running → this process does it itself."""
    from . import elevate
    from .state import State
    st = State(root)
    req = {"t": "calsync", "op": "disconnect" if a.op == "unsubscribe" else a.op, "name": a.name}
    if a.op == "connect":
        req.update(kind=a.kind, push_new=bool(a.push_new), auto=bool(a.auto), **({"calendar": a.calendar} if a.calendar else {}),
                   **{k: getattr(a, k) for k in DAV if getattr(a, k, None) is not None})
    bad = request_shape(req)
    if bad:
        return {"ok": False, "why": "shape", "detail": bad}
    res = elevate.client_request(st, req, timeout=600)
    if res.get("result") == "unavailable":
        try:
            res = host_op(st, req)
        except OSError:
            res = {"ok": False, "name": a.name, "why": "fenced",
                   "detail": "Agent J is not running; ask the owner to start it (agentj service status)"}
    return res


def cli_kick(root, res: dict) -> None:
    """After a CLI change that queued an op: ask serve to sync that remote in 5 s (best effort, never waits for it)."""
    names = [s["name"] for s in res.get("subscriptions") or [] if s.get("kind") in TWO_WAY and s.get("queued")]
    if not names or not res.get("ok"):
        return
    from . import elevate
    from .state import State
    for n in names:
        elevate.client_request(State(root), {"t": "calsync", "op": "kick", "name": n}, timeout=3)
